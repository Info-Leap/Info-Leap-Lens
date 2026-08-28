"""
Capability: purchase
====================
Ranked / recency purchase behaviour.
Covers scenarios where respondents select multiple items in order of recency or preference.

Key distinction from ownership: RANK matters here.
Rank 1 = most recently purchased / primary choice.

Required binding keys (from config/project_N.CAPABILITIES["purchase"]):
  view         — pre-joined purchase view
  view_rows    — approximate row count
  entity_col   — column for entity name
  rank_col     — column for purchase rank (integer, 1 = most recent)
  max_rank     — maximum rank value (e.g., 3 = respondents pick up to 3 items)
  rank_desc    — plain-English description of the rank meaning
  entity_list  — comma-separated list of valid entity names
"""

CAPABILITY_ID = "purchase"
DESCRIPTION   = "Ranked purchase behaviour — recency and primary/secondary choices"

KEY_COLUMNS_SUMMARY = "respondent_id, {rank_col} (1-{max_rank}), {entity_col}"


def format_prompt(binding: dict, shared_cols: str, respondent_table: str) -> str:
    b = binding
    return f"""
VIEW: {b['view']}
Columns: respondent_id, {b['rank_col']} (1-{b['max_rank']}), {b['entity_col']}
{shared_cols}

- Rank 1 = primary/most recent selection.
- WHERE {b['rank_col']} = 1 for "last bought" analysis.

EXAMPLES:
-- primary purchase share
SELECT {b['entity_col']}, COUNT(*) n, ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM {respondent_table}), 1) pct
FROM {b['view']} WHERE {b['rank_col']} = 1 GROUP BY {b['entity_col']} ORDER BY n DESC;

-- total mentions (all ranks)
SELECT {b['entity_col']}, COUNT(*) n FROM {b['view']} GROUP BY {b['entity_col']} ORDER BY n DESC;
"""
