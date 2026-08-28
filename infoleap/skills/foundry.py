"""
Skill Foundry — Assembler + Router
===================================
The foundry is the only file that knows about both the capability modules (Layer 2)
and the project config (Layer 3). It assembles prompts and routes queries.

NEVER call the Groq API from this file.
NEVER import any Streamlit or UI code from this file.
This file must stay pure Python with no external dependencies.

Key functions:
  assemble_prompt(capability_id, project_config)  → str  (assembled system prompt)
  route_query(question, project_config, prior)    → str  (capability_id to use)
  build_prompt_cache(project_config)              → dict (pre-assembled all prompts)

See docs/SKILLS.md for full architecture documentation.
"""

from skills import base_rules
from skills.capabilities import REGISTRY


# ── prompt assembly ────────────────────────────────────────────────────────────

def assemble_prompt(capability_id: str, project_config) -> str:
    """
    Assemble a complete LLM system prompt from 3 layers:
      Layer 1: Universal rules      (skills/base_rules.py)
      Layer 2: Capability logic     (skills/capabilities/<name>.py)
      Layer 3: Project schema       (config/project_N.py → CAPABILITIES dict)

    Args:
        capability_id:  One of the registered capability IDs or "general".
        project_config: A project config module (e.g., config.project_1).

    Returns:
        Complete system prompt string ready to send to the LLM.

    Raises:
        ValueError: If capability_id is not in REGISTRY and is not "general".
        KeyError:   If a required binding key is missing from the project config.
    """
    layer1 = base_rules.get_rules(project_config.RESPONDENT_TABLE)

    # Build relationships section (if defined)
    relationships = getattr(project_config, "RELATIONSHIPS", None)
    if relationships:
        rel_lines = ["\n=== FOREIGN KEY RELATIONSHIPS ==="]
        for rel in relationships:
            rel_lines.append(f"- {rel['from']} → {rel['to']} ({rel['type']}): {rel['semantics']}")
        relationships_section = "\n".join(rel_lines) + "\n"
    else:
        relationships_section = ""

    if capability_id == "general":
        return layer1 + _build_general_section(project_config) + relationships_section

    if capability_id not in REGISTRY:
        raise ValueError(
            f"Unknown capability '{capability_id}'. "
            f"Registered: {list(REGISTRY.keys())}. "
            f"Did you forget to add it to skills/capabilities/__init__.py?"
        )

    if capability_id not in project_config.CAPABILITIES:
        raise ValueError(
            f"Capability '{capability_id}' not configured in "
            f"project '{project_config.PROJECT_ID}'. "
            f"Add it to CAPABILITIES in config/{project_config.PROJECT_ID}.py."
        )

    capability = REGISTRY[capability_id]
    binding    = project_config.CAPABILITIES[capability_id]

    try:
        layer2_and_3 = capability.format_prompt(
            binding,
            project_config.SHARED_VIEW_COLS,
            project_config.RESPONDENT_TABLE,
        )
    except KeyError as e:
        raise KeyError(
            f"Missing binding key {e} for capability '{capability_id}' "
            f"in project '{project_config.PROJECT_ID}'. "
            f"Add it to CAPABILITIES['{capability_id}'] in "
            f"config/{project_config.PROJECT_ID}.py."
        ) from e

    # Inject contextual data dictionary from project
    data_dict = getattr(project_config, "DATA_DICTIONARY", "")
    if data_dict:
        data_dict = f"\n=== DATA DICTIONARY ===\nIMPORTANT: Use ONLY these exact column names and values. Never guess or invent terms.\n{data_dict}\n"
    
    term_mappings = getattr(project_config, "TERM_MAPPINGS", "")
    if term_mappings:
        term_mappings = f"\n{term_mappings}\n"
    
    # 4. Inject Golden Template for Complex Subqueries (Elite Pattern)
    golden_templates = """
=== GOLDEN RESEARCH PATTERNS (CRITICAL: FOLLOW EXACTLY) ===
Pattern: "Zonal NPS / Brand NPS Comparison"
Logic: Join v_respondents (for zones) with v_brand_nps (for scores).
SQL: 
SELECT 
  r.zone_name, 
  b.brand_name,
  ROUND((SUM(CASE WHEN b.nps_score >= 9 THEN 1.0 ELSE 0 END) - SUM(CASE WHEN b.nps_score <= 6 THEN 1.0 ELSE 0 END)) * 100.0 / COUNT(*), 1) as nps
FROM v_respondents r
JOIN v_brand_nps b ON r.respondent_id = b.respondent_id
WHERE b.brand_name IN ('[BRAND1]', '[BRAND2]')
GROUP BY 1, 2
HAVING COUNT(*) >= 30;

Pattern: "Metric by Demographic (Occupation/Age/Gender)"
Logic: Join Fact table with v_respondents on respondent_id.
SQL:
SELECT 
  r.[DEMOGRAPHIC_COL], 
  ROUND((SUM(CASE WHEN b.nps_score >= 9 THEN 1.0 ELSE 0 END) - SUM(CASE WHEN b.nps_score <= 6 THEN 1.0 ELSE 0 END)) * 100.0 / COUNT(*), 1) as nps
FROM v_respondents r
JOIN v_brand_nps b ON r.respondent_id = b.respondent_id
WHERE b.brand_name = '[BRAND_NAME]' AND r.zone_name = '[ZONE_NAME]'
GROUP BY 1
ORDER BY nps DESC;
"""
    
    return layer1 + data_dict + term_mappings + relationships_section + layer2_and_3 + golden_templates


def _build_general_section(project_config) -> str:
    """
    Build the VIEWS section for the GENERAL fallback skill.
    Uses KEY_COLUMNS_SUMMARY from each registered capability to produce a compact
    overview of all views — so the LLM has a schema map without full detail.
    """
    lines = ["\n=== VIEWS (all available — general fallback) ==="]

    for cap_id, binding in project_config.CAPABILITIES.items():
        if "view" not in binding:
            continue

        view = binding["view"]
        rows = binding.get("view_rows", "?")

        # Try to get the key columns summary from the registered capability module
        if cap_id in REGISTRY:
            try:
                summary = REGISTRY[cap_id].KEY_COLUMNS_SUMMARY.format(**binding)
            except KeyError:
                summary = ""
        else:
            summary = ""

        lines.append(f"\n{view}  ({rows} rows)")
        if summary:
            lines.append(f"  {summary}")

    lines.append(f"\n{project_config.SHARED_VIEW_COLS}")

    # Append NPS formula if the project has the NPS capability
    if "nps" in project_config.CAPABILITIES:
        b = project_config.CAPABILITIES["nps"]
        lines.append(f"""
NPS FORMULA — use HAVING COUNT(*) >= {b['min_raters']}:
  ROUND(
    (SUM(CASE WHEN {b['score_col']} >= {b['promoter_min']} THEN 1.0 ELSE 0 END)
   - SUM(CASE WHEN {b['score_col']} <= {b['detractor_max']} THEN 1.0 ELSE 0 END))
    * 100.0 / COUNT(*), 1) AS nps""")

    # Append awareness formula if the project has the awareness capability
    if "awareness" in project_config.CAPABILITIES:
        b = project_config.CAPABILITIES["awareness"]
        lines.append(f"""
AWARENESS — filter: {b['exclude_filter']}
  Spontaneous = stage IN ('{b['tom_value']}', '{b['spont_value']}'), COUNT(DISTINCT respondent_id)
  Total       = all stages, COUNT(DISTINCT respondent_id)""")

    return "\n".join(lines)


# ── prompt cache ───────────────────────────────────────────────────────────────

_PROMPT_CACHE: dict[str, str] = {}
_CACHE_BUILT = False


def build_prompt_cache(project_config) -> dict[str, str]:
    """Legacy - builds all prompts. Use get_skill_prompt() for lazy loading."""
    global _PROMPT_CACHE, _CACHE_BUILT
    if not _CACHE_BUILT:
        supported = list(project_config.CAPABILITIES.keys()) + ["general"]
        for cap_id in supported:
            try:
                _PROMPT_CACHE[cap_id] = assemble_prompt(cap_id, project_config)
            except Exception as e:
                print(f"[Foundry] WARNING: Could not assemble skill '{cap_id}': {e}")
        _CACHE_BUILT = True
    return _PROMPT_CACHE


def get_skill_prompt(skill_key: str, project_config) -> str:
    """
    Lazy-load a skill prompt on-demand.
    Only assembles the specific skill being used, not all of them.
    """
    global _PROMPT_CACHE, _CACHE_BUILT
    if not _CACHE_BUILT:
        _CACHE_BUILT = True
        _PROMPT_CACHE["general"] = assemble_prompt("general", project_config)
    
    if skill_key in _PROMPT_CACHE:
        return _PROMPT_CACHE[skill_key]
    
    try:
        prompt = assemble_prompt(skill_key, project_config)
        _PROMPT_CACHE[skill_key] = prompt
        return prompt
    except Exception as e:
        print(f"[Foundry] Lazy load failed for '{skill_key}': {e}")
        return _PROMPT_CACHE.get("general", "")


# ── router ─────────────────────────────────────────────────────────────────────

def route_query(
    question: str,
    project_config,
    prior_skill: str | None = None,
) -> str:
    """
    Classify a question into a capability_id using the project's keyword lists.
    ZERO API tokens — pure local keyword matching.

    Priority order (defined per project in SKILL_PRIORITY):
      1. Check each capability's keyword list in priority order.
      2. Check entity keywords (e.g., brand names → awareness).
      3. If follow-up pronoun detected and prior_skill known → reuse prior skill.
      4. Fall back to "general".

    Args:
        question:       The user's raw question string.
        project_config: The active project config module.
        prior_skill:    The skill used for the previous question (for follow-up resolution).

    Returns:
        A capability_id string (always valid; worst case "general").
    """
    q = question.lower()

    # Step 1: priority-ordered keyword match
    for cap_id in project_config.SKILL_PRIORITY:
        keywords = project_config.KEYWORDS.get(cap_id, [])
        if any(kw in q for kw in keywords):
            # Map 'room' back to 'ownership' if 'room' isn't in REGISTRY anymore
            if cap_id == "room" and "room" not in REGISTRY and "ownership" in REGISTRY:
                return "ownership"
            return cap_id

    # Step 2: entity keywords (e.g., brand names)
    if hasattr(project_config, "ENTITY_KEYWORDS") and hasattr(project_config, "ENTITY_SKILL"):
        if any(ent in q for ent in project_config.ENTITY_KEYWORDS):
            return project_config.ENTITY_SKILL

    # Step 3: follow-up pronouns — carry forward the last skill
    _FOLLOW_UP = {"their", "those", "that brand", "that product", "same", "it", "them", "these"}
    if prior_skill and any(w in q for w in _FOLLOW_UP):
        return prior_skill

    # Step 4: safe fallback
    return "general"
