"""
Text-to-SQL retriever.
"""

import os
import re
import json
from google import genai
from sqlalchemy import text
from database.connection import get_engine
def rprint(*args, **kwargs):
    import re
    text = " ".join(str(a) for a in args)
    text = re.sub(r"\[/?[a-zA-Z_ ]*\]", "", text)
    print(text)

def _get_client():
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception:
        return None

SCHEMA_SUMMARY = """
SQLite schema for LENS survey data (long format):

TABLE projects:
  project_id (PK), project_name, client_name, wave
  
TABLE respondents:
  respondent_id (PK), project_id (FK), masked_name, city, zone, gender,
  age_band, sec_class, category, respondent_type, is_affluent

TABLE variables:
  variable_code (PK), project_id (FK), category, section, question_type,
  label_en, label_hi, scale_min, scale_max, feature_bucket

TABLE responses:
  response_id (PK), respondent_id (FK), variable_code (FK),
  value_numeric, value_text, value_boolean

TABLE need_attributes:
  attribute_id (PK), variable_code (FK), category, label_en,
  label_hi, feature_bucket

Key variable patterns:
- bq3a_1_N: importance rating for attribute N (1=not important, 7=very important)
- bq3b_N: brand association for attribute N
- bq5: overall satisfaction score (0-10)
"""

SQL_WRITER_PROMPT = f"""You write SQLite SELECT queries for a market research database.

{SCHEMA_SUMMARY}

Rules:
- Only write SELECT queries. Never INSERT, UPDATE, DELETE, DROP.
- Always use table aliases
- Limit results to 100 rows unless aggregating
- Use LIKE for text matching (case-insensitive in SQLite)
- Return ONLY the SQL query. No explanation. No markdown backticks.
"""

def validate_sql(sql: str) -> tuple[bool, str]:
    """Basic SQL safety validation."""
    sql_upper = sql.upper().strip()
    forbidden = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER',
                 'CREATE', 'TRUNCATE', 'GRANT', 'REVOKE']
    for keyword in forbidden:
        if re.search(rf'\b{keyword}\b', sql_upper):
            return False, f"Forbidden keyword: {keyword}"
    if not sql_upper.startswith('SELECT'):
        return False, "Query must start with SELECT"
    return True, "OK"

def retrieve_from_sql(sql_intent: str,
                      category_filter: str = "Mixer Grinder") -> dict:
    """
    Generates and executes a SQL query from plain English intent.
    """
    rprint(f"[blue]SQL intent:[/blue] {sql_intent}")

    prompt = f"""{SQL_WRITER_PROMPT}

Category filter: {category_filter}
Intent: {sql_intent}
Write the SQLite SELECT query:"""

    try:
        client = _get_client()
        if not client:
            raise Exception("GenAI client initialization failed. Check API keys.")
            
        response = client.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=prompt
        )
        generated_sql = response.text.strip()
    except Exception as e:
        rprint(f"[red]SQL generation failed:[/red] {e}")
        return {"error": str(e), "sql": "", "rows": [], "columns": []}

    # Remove any accidental markdown
    generated_sql = re.sub(r'```sql|```', '', generated_sql).strip()

    rprint(f"  [dim]Generated SQL:[/dim] {generated_sql[:200]}...")

    # Validate
    is_safe, reason = validate_sql(generated_sql)
    if not is_safe:
        rprint(f"[red]SQL validation failed:[/red] {reason}")
        return {"error": reason, "sql": generated_sql, "rows": [], "columns": []}

    # Execute
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text(generated_sql))
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]

        rprint(f"  [green]SQL returned {len(rows)} rows.[/green]")
        return {
            "sql": generated_sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows)
        }
    except Exception as e:
        rprint(f"[red]SQL execution error:[/red] {e}")
        return {"error": str(e), "sql": generated_sql, "rows": [], "columns": []}

