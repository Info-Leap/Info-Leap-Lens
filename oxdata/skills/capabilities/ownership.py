"""
Capability: ownership
=====================
Binary ownership / penetration analysis.
Covers appliances, products, or any category where respondents tick all that apply.

The source data is binary flag columns expanded into one row per respondent × entity.
Penetration % = distinct owners / total respondents.

Required binding keys (from config/project_N.CAPABILITIES["ownership"]):
  view         — pre-joined ownership view
  view_rows    — approximate row count
  entity_col   — column for entity name (e.g., appliance_name)
  entity_list  — comma-separated list of valid entity names (for reference)
"""

CAPABILITY_ID = "ownership"
DESCRIPTION   = "Ownership / penetration analysis (binary — owns or doesn't own)"

KEY_COLUMNS_SUMMARY = "respondent_id, {entity_col}"


def format_prompt(binding: dict, shared_cols: str, respondent_table: str) -> str:
    b = binding
    views = b.get("views", [{"name": b.get("view", ""), "desc": ""}])
    views_str = "\n".join([f"VIEW: {v['name']} ({v.get('desc','')})" for v in views])
    
    return f"""
{views_str}
Columns: respondent_id, {b['entity_col']}
{shared_cols}

- Use COUNT(DISTINCT respondent_id) for headcount.
- Penetration % = owners / total_respondents_IN_SCOPE * 100.
- Use CTE for per-zone base if grouping by zone.

EXAMPLES:
-- Penetration (Overall)
SELECT {b['entity_col']}, COUNT(DISTINCT respondent_id) owners,
  ROUND(COUNT(DISTINCT respondent_id) * 100.0 / (SELECT COUNT(*) FROM {respondent_table}), 1) pct
FROM {views[0]['name']} GROUP BY {b['entity_col']} ORDER BY owners DESC;

-- specific entity by zone
WITH zb AS (SELECT zone_name, COUNT(*) total FROM {respondent_table} GROUP BY zone_name)
SELECT v.zone_name, COUNT(DISTINCT v.respondent_id) owners, ROUND(COUNT(DISTINCT v.respondent_id)*100.0/zb.total, 1) pct
FROM {views[1]['name'] if len(views)>1 else views[0]['name']} v JOIN zb ON v.zone_name=zb.zone_name 
WHERE v.{b['entity_col']}='Ceiling Fans' GROUP BY v.zone_name;
"""
