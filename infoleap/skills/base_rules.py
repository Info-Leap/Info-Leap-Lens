"""
Layer 1 — Universal Rules
=========================
These rules are prepended to EVERY skill prompt regardless of project or capability.
They never change unless the SQL generation approach itself changes.

Design principle: Rules that are true for ALL projects, ALL capabilities.
If a rule only applies to one project or one view, it belongs in Layer 3 (config/project_N.py).
"""

_TEMPLATE = """\
RULES:
1. SQL only. No markdown fences.
2. Use VIEWS only.
3. Penetration: ROUND(count*100.0/(SELECT COUNT(*) FROM {respondent_table}), 1)
4. Use exact column names/values from DICT.
5. Case-sensitive matching.
6. After SQL, on new line: CHART: {{"type": "...", "x": "col", "y": "col"}}
"""


def get_rules(respondent_table: str = "fact_respondents") -> str:
    """
    Return the universal rules block with the correct respondent table name.

    Args:
        respondent_table: The table used as denominator for penetration percentages.
                          Always 'fact_respondents' for Project 1, but could differ
                          in future projects (e.g., 'fact_patients' for a healthcare study).
    """
    return _TEMPLATE.format(respondent_table=respondent_table)
