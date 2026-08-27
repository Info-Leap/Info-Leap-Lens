"""
Capability: room
================
Room / home appliance ownership.
Structurally identical to 'ownership' but covers a different product category
(room/electrical appliances vs. kitchen appliances).

Kept as a separate capability so:
  1. Its keyword list can be tuned independently.
  2. Its entity_list (fans, ACs, bulbs, etc.) is distinct.
  3. Future projects can opt into room OR ownership independently.

Required binding keys (from config/project_N.CAPABILITIES["room"]):
  view         — pre-joined room appliances view
  view_rows    — approximate row count
  entity_col   — column for appliance name
  entity_list  — comma-separated list of valid appliance names
"""

CAPABILITY_ID = "room"
DESCRIPTION   = "Room / home appliance ownership (fans, AC, bulbs, geysers, etc.)"

KEY_COLUMNS_SUMMARY = "respondent_id, {entity_col}"


def format_prompt(binding: dict, shared_cols: str, respondent_table: str) -> str:
    b = binding
    return f"""
=== VIEW: {b['view']}  ({b['view_rows']} rows) ===
  respondent_id
  {b['entity_col']}
{shared_cols}

IMPORTANT: Output ONLY a single SQL query. Do NOT provide multiple queries or explanations.

VALID APPLIANCE NAMES: {b['entity_list']}

NOTE: Code 10 is absent from the source codebook — this is a known gap in the data,
not a data load error.

=== EXAMPLES ===
-- Penetration for all room appliances
SELECT {b['entity_col']},
  COUNT(DISTINCT respondent_id) owners,
  ROUND(COUNT(DISTINCT respondent_id) * 100.0
    / (SELECT COUNT(*) FROM {respondent_table}), 1) pct
FROM {b['view']}
GROUP BY {b['entity_col']} ORDER BY owners DESC;

-- AC penetration by zone
SELECT zone_name,
  COUNT(DISTINCT respondent_id) ac_owners,
  ROUND(COUNT(DISTINCT respondent_id) * 100.0
    / (SELECT COUNT(*) FROM {respondent_table}), 1) pct
FROM {b['view']}
WHERE {b['entity_col']} LIKE '%AC%' OR {b['entity_col']} LIKE '%Conditioner%'
GROUP BY zone_name ORDER BY ac_owners DESC;
"""
