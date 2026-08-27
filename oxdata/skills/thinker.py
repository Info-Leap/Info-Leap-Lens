"""
Thinker — Complexity Classifier + LLM Planner + Multi-Query Executor
=====================================================================
The "brain" of OxData v2. Sits between the user question and the
existing skill routing, adding an intelligence layer that:

  1. **Classifies** question complexity (Python, 0 tokens)
  2. **Plans** multi-step queries for complex questions (LLM, ~300 tokens)
  3. **Executes** sub-queries and merges results (Python + SQL)

Design principles:
  - Simple questions (80%) bypass the thinker entirely — zero overhead.
  - Only "complex" questions trigger the LLM planner (+1 API call).
  - The planner output is structured JSON, validated against the skill registry.
  - Sub-query results are merged in Python, then passed to the enriched summary.

Public API:
  classify_complexity(question, session_history)  → "simple" | "contextual" | "complex"
  plan_query(question, session_context, llm_fn)   → QueryPlan
  execute_plan(plan, get_sql_fn, run_sql_fn)      → MergedResult
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Any

import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class QueryStep:
    """A single sub-query planned by the thinker."""
    step_id: int
    skill: str
    sub_question: str
    depends_on: int | None = None
    sql: str | None = None
    df: pd.DataFrame | None = None
    error: str | None = None


@dataclass
class QueryPlan:
    """The full decomposition plan for a complex question."""
    original_question: str
    complexity: str                     # "simple" | "contextual" | "complex"
    reasoning: str = ""
    steps: list[QueryStep] = field(default_factory=list)
    merge_strategy: str = "concat"      # "concat" | "join_on_brand_name" | "join_on_city_name" | ...
    chart_hint: str = "auto"


@dataclass
class MergedResult:
    """The output of executing a multi-step plan."""
    df: pd.DataFrame | None = None
    all_dfs: list[pd.DataFrame] = field(default_factory=list)
    all_sqls: list[str] = field(default_factory=list)
    skill_keys: list[str] = field(default_factory=list)
    chart_hint: str = "auto"
    merge_description: str = ""
    error: str | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# 1. COMPLEXITY CLASSIFIER (Python — 0 tokens)
# ═══════════════════════════════════════════════════════════════════════════════

# Keyword sets for each domain — comprehensive coverage to avoid mis-routing
_DOMAIN_KEYWORDS = {
    "nps": {
        "nps", "net promoter", "promoter", "detractor", "passive",
        "recommend", "loyalty", "satisfaction", "nps score", "nps_score",
        "nps_category", "promoters", "detractors", "passives",
        "brand rating", "brand score", "how likely to recommend",
    },
    "awareness": {
        "awareness", "tom", "top of mind", "first recall", "first brand",
        "spontaneous", "spont", "unaided", "unaided awareness",
        "aided", "aided awareness", "prompted", "recognition",
        "recall", "brand recall", "brand awareness",
        "funnel", "brand funnel", "awareness funnel",
        "mind share", "mindshare",
        "conversion", "convert", "conversion rate", "funnel conversion",
        "awareness conversion", "awareness funnel",
    },
    "ownership": {
        # Kitchen-specific terms only — "ownership" alone is too generic
        # and causes false "complex" routing for room appliance queries
        "kitchen", "kitchen appliance",
        "appliance owned", "appliances you own", "do you own",
        "household appliance",
        # Specific kitchen appliances (match user language)
        "mixer", "mixie", "mixer grinder", "grinder",
        "juicer", "microwave", "microwave oven",
        "kettle", "electric kettle",
        "water purifier", "purifier",
        "induction stove",
        "sandwich maker",
        "otg", "otg oven",
        "food processor",
        "coffee maker",
        "air fryer",
        "electric chimney",
        "dishwasher",
        # penetration and saturation remain kitchen-context words
        "penetration", "saturation",
        # Phrases for "most owned" queries
        "top product", "top appliance", "most popular", "best selling",
        "leading product", "most owned", "#1 product", "number one product",
        "highest ownership", "most common",
    },
    "purchase": {
        "purchase", "purchased", "bought", "recently bought",
        "buying", "last bought", "recent purchase",
        "purchase rank", "first purchase", "latest purchase",
        "shopping", "buy",
    },
    "room": {
        # General room terms
        "room appliance", "room electrical", "electrical appliance",
        # Specific room appliances — these take priority over generic "ownership"
        "ceiling fan", "fans",
        "air conditioner", "a/c",
        "geyser", "water heater",
        "room heater",
        "led bulb", "led", "tube light", "batten",
        "led ceiling", "panel light", "downlight",
        "exhaust fan",
        "pedestal fan", "table fan", "wall fan",
        "air cooler",
        "air purifier",
        "cfl", "incandescent",
        "chandelier", "jhoomer",
    },
    "qualitative": {
        "why", "prefer", "reasons", "feedback", "sentiment", "opinion",
        "think", "feel", "say", "interviews", "verbatim", "consumer voice",
        "better than", "worse than", "choose", "dislike", "like",
    },
    "demographic": {
        # Only trigger demographic domain for truly demographic questions
        # Geographic grouping words ("by zone", "by city") are NOT a separate domain —
        # they're just GROUP BY dimensions that apply to any skill.
        "respondent", "respondents", "sample", "sample size",
        "how many people", "total respondents",
        "gender breakdown", "male female", "age distribution",
        "fieldwork", "data collection",
        "respondent profile", "demographic profile",
        # City-level questions (pure count/profile, not filter on another skill)
        "how many in delhi", "how many in mumbai", "how many in kolkata",
    },
}

# Cross-domain signal words
_CROSS_REF_WORDS = frozenset([
    "side by side", "correlat", "relationship between", "along with",
    "combined with", "together with", "cross-tab", "cross tab",
    "map it to", "overlay",
])

_CONDITIONAL_WORDS = frozenset([
    "for the city with", "for the brand with", "for those who",
    "among those", "given that", "where the", "in the same",
    # Removed "for the top", "for the highest", "for the lowest" —
    # those are ranking filters within a single domain, not cross-domain signals
])

_MULTI_QUERY_WORDS = frozenset([
    "and also", "additionally show", "plus show", "as well as",
    "include their", "with their", "competitors", "competitor",
    "vs", "versus", "compare with", "compared to", "over",
])

_SUMMARY_WORDS = frozenset([
    "summarize", "summarise", "summary", "key findings", "wrap up",
    "overview of our", "recap", "what did we discuss",
    "what have we covered",
])


def classify_complexity(
    question: str,
    session_history: list[dict] | None = None,
) -> str:
    """
    Classify a question's complexity level.

    Args:
        question:        The user's raw question string.
        session_history: List of prior messages (for context detection).

    Returns:
        - "simple":     Single domain, single query needed.
        - "contextual": Needs prior-context resolution, but single query.
        - "complex":    Multi-domain or multi-step, needs LLM planner.
        - "summary":    Session summary request, no SQL needed.
    """
    q = question.lower()

    # Check for session summary request first
    if any(w in q for w in _SUMMARY_WORDS):
        return "summary"

    # Count how many domains the question touches
    domains_hit = []
    matched_keywords_per_domain = {}  # track what matched for filtering
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        matches = [kw for kw in keywords if kw in q]
        if matches:
            domains_hit.append(domain)
            matched_keywords_per_domain[domain] = matches

    # ═══════════════════════════════════════════════════════════════════════════════
    # DEMOGRAPHIC FILTER: generic mentions of "respondents", "sample", etc.
    # should NOT count as a separate domain when the question is primarily about
    # another domain (e.g., "count of respondents for top product").
    # ═══════════════════════════════════════════════════════════════════════════════
    GENERIC_DEMO_TERMS = {
        "respondent", "respondents", "sample", "samples",
        "sample size", "total respondents", "how many people",
        "how many",  # generic count phrase
    }
    if len(domains_hit) > 1 and "demographic" in domains_hit:
        demo_matches = matched_keywords_per_domain.get("demographic", [])
        # If all matched demo keywords are generic, demote demographic
        if all(kw in GENERIC_DEMO_TERMS for kw in demo_matches):
            domains_hit = [d for d in domains_hit if d != "demographic"]

    # Multi-domain = complex
    if len(domains_hit) >= 2:
        return "complex"

    # Cross-reference signals
    if any(w in q for w in _CROSS_REF_WORDS):
        return "complex"

    # Conditional cross-domain (e.g., "for the city with highest NPS")
    if any(w in q for w in _CONDITIONAL_WORDS):
        # Check if the condition references a different domain than the query
        return "complex"

    # Multi-query signals
    if any(w in q for w in _MULTI_QUERY_WORDS):
        return "complex"

    # Context-dependent follow-ups
    _CONTEXT_WORDS = {"that city", "those brands", "that brand", "the same",
                      "those results", "that table", "same brands"}
    has_context_ref = any(w in q for w in _CONTEXT_WORDS)
    has_new_domain = len(domains_hit) == 1 and session_history

    if has_context_ref and has_new_domain:
        # Referencing prior context but switching domain
        # Check if prior messages used a different domain
        prior_skills = set()
        for msg in (session_history or []):
            if msg.get("skill"):
                prior_skills.add(msg["skill"])
        if prior_skills and domains_hit[0] not in prior_skills:
            return "complex"
        return "contextual"

    if has_context_ref:
        return "contextual"

    # If the user just types a short phrase like "by state", "in north", "for males"
    # without explicitly naming a domain, it's almost certainly a contextual follow-up.
    if not domains_hit and len(q.split()) <= 4 and session_history:
        return "contextual"

    return "simple"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LLM PLANNER (~300 tokens — only called for "complex" questions)
# ═══════════════════════════════════════════════════════════════════════════════

_PLANNER_SYSTEM_PROMPT = """\
You are a research query planner for a consumer survey database.

### COGNITIVE DECONSTRUCTION (MANDATORY)
Before generating JSON, you MUST use `<thought>` tags to:
1.  **Deconstruct Intent**: What is the core research question?
2.  **Map Domains**: Which skills (nps, awareness, etc.) are needed?
3.  **Check Dependencies**: Does Step 2 need Brand names from Step 1?
4.  **Fallback Planning**: If Step 1 returns 0 rows, how should the plan adapt?

### 1. Available skills (each queries one view):
- awareness: Brand awareness (TOM, spontaneous, aided) from v_brand_awareness
- nps: Net Promoter Score from v_brand_nps
- ownership: Kitchen appliance ownership from v_kitchen_ownership
- purchase: Recent appliance purchases from v_recent_purchase
- room: Room appliances (fans, ACs, LEDs) from v_room_appliances
- demographic: Respondent details from v_respondents
- qualitative: Consumer sentiments and "Why" reasons from interviews (RAG)

### 2. Rules:
1. Decompose the question into 1-3 sub-queries (MAX 3).
2. Each sub-query must use exactly ONE skill.
3. If step 2 depends on step 1's result, set depends_on = 1.
4. merge_strategy: "join_on_brand_name" | "join_on_city_name" | "join_on_zone_name" | "concat"
5. Return ONLY valid JSON after your thought block.

Return this JSON format:
{"reasoning": "brief explanation",
 "steps": [
   {"step_id": 1, "skill": "skill_name", "sub_question": "specific question"},
   {"step_id": 2, "skill": "skill_name", "sub_question": "specific question", "depends_on": 1}
 ],
 "merge_strategy": "join_on_brand_name",
 "chart_hint": "grouped_bar"}
"""

# Valid skills for validation
_VALID_SKILLS = {"awareness", "nps", "ownership", "purchase", "room", "demographic", "qualitative", "general"}


def _build_planner_prompt(question: str, session_context: str = "") -> str:
    """Build the user prompt for the LLM planner."""
    parts = [f"Question: {question}"]
    if session_context:
        parts.append(f"\nSession context:\n{session_context}")
    return "\n".join(parts)


def _parse_plan_output(raw: str, question: str) -> QueryPlan:
    """Parse the LLM planner's JSON output into a QueryPlan."""
    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw.strip())

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return QueryPlan(
                    original_question=question,
                    complexity="complex",
                    reasoning="Failed to parse planner output — falling back",
                )
        else:
            return QueryPlan(
                original_question=question,
                complexity="complex",
                reasoning="No JSON found in planner output — falling back",
            )

    # Build steps
    steps = []
    for s in data.get("steps", []):
        skill = s.get("skill", "general")
        if skill not in _VALID_SKILLS:
            skill = "general"
        steps.append(QueryStep(
            step_id=s.get("step_id", len(steps) + 1),
            skill=skill,
            sub_question=s.get("sub_question", question),
            depends_on=s.get("depends_on"),
        ))

    # Cap at 3 steps max
    steps = steps[:3]

    return QueryPlan(
        original_question=question,
        complexity="complex",
        reasoning=data.get("reasoning", ""),
        steps=steps,
        merge_strategy=data.get("merge_strategy", "concat"),
        chart_hint=data.get("chart_hint", "auto"),
    )


async def plan_query(question: str, session_context: str, llm_fn) -> QueryPlan:
    """
    Asks the LLM to decompose a complex question into sub-steps.

    Args:
        question:        The user's complex question.
        session_context: Summary of recent session history.
        llm_fn:          Async function(system_prompt, user_prompt) → LLM response string.

    Returns:
        A QueryPlan with decomposed steps.
    """
    user_prompt = _build_planner_prompt(question, session_context)

    try:
        raw_response = await llm_fn(_PLANNER_SYSTEM_PROMPT, user_prompt)
        plan = _parse_plan_output(raw_response, question)

    except Exception as e:
        plan = QueryPlan(
            original_question=question,
            complexity="complex",
            reasoning=f"Planner failed: {e}. Falling back to single query.",
        )

    # If planner returned empty steps, create a single fallback step
    if not plan.steps:
        plan.steps = [QueryStep(
            step_id=1,
            skill="general",
            sub_question=question,
        )]

    return plan


# ═══════════════════════════════════════════════════════════════════════════════
# 3. MULTI-QUERY EXECUTOR + MERGER
# ═══════════════════════════════════════════════════════════════════════════════

# Columns suitable for joining DataFrames across domains
_JOIN_COLS = {
    "join_on_brand_name":     "brand_name",
    "join_on_city_name":      "city_name",
    "join_on_zone_name":      "zone_name",
    "join_on_appliance_name": "appliance_name",
}


def _merge_dataframes(dfs: list[pd.DataFrame], strategy: str) -> pd.DataFrame:
    """
    Merge multiple DataFrames using the specified strategy.

    Args:
        dfs:      List of DataFrames to merge.
        strategy: One of "concat", "join_on_brand_name", etc.

    Returns:
        Merged DataFrame.
    """
    if not dfs:
        return pd.DataFrame()
    if len(dfs) == 1:
        return dfs[0]

    join_col = _JOIN_COLS.get(strategy)

    if join_col:
        # Outer join on the specified column
        result = dfs[0]
        for df in dfs[1:]:
            if join_col in result.columns and join_col in df.columns:
                # Avoid duplicate columns by suffixing
                overlap = set(result.columns) & set(df.columns) - {join_col}
                suffixes = ("", f"_{df.columns.tolist()[0][:3]}")
                result = pd.merge(
                    result, df,
                    on=join_col,
                    how="outer",
                    suffixes=suffixes,
                )
            else:
                # If join column missing, just concat
                result = pd.concat([result, df], ignore_index=True)
        return result
    else:
        # Simple concatenation
        return pd.concat(dfs, ignore_index=True)


def execute_plan(
    plan: QueryPlan,
    get_sql_fn: Callable[[str, str, list], str],
    run_sql_fn: Callable[[str, str], tuple[pd.DataFrame | None, str | None, list, str]],
    history: list[dict] | None = None,
) -> MergedResult:
    """
    Execute all steps in a QueryPlan and merge results.

    Args:
        plan:       The decomposed QueryPlan.
        get_sql_fn: Function(question, skill_key, history) → SQL string.
        run_sql_fn: Function(sql, skill_key) → (DataFrame or None, error or None, retry_log, final_sql).
        history:    Conversation history for context.

    Returns:
        MergedResult with merged DataFrame and metadata.
    """
    all_dfs: list[pd.DataFrame] = []
    all_sqls: list[str] = []
    skill_keys: list[str] = []
    errors: list[str] = []

    for step in plan.steps:
        sub_q = step.sub_question
        if step.depends_on is not None:
            dep_step = next(
                (s for s in plan.steps if s.step_id == step.depends_on), None
            )
            if dep_step and dep_step.df is not None and not dep_step.df.empty:
                entity_col = None
                for ec in ["brand_name", "city_name", "zone_name", "appliance_name"]:
                    if ec in dep_step.df.columns:
                        entity_col = ec
                        break
                if entity_col:
                    entities = dep_step.df[entity_col].tolist()
                    entities_str = ", ".join(str(e) for e in entities[:10])
                    sub_q = f"{step.sub_question} (specifically for: {entities_str})"

        try:
            original_sql = get_sql_fn(sub_q, step.skill, history or [])
            step.sql = original_sql

            df, error, retry_log, final_sql = run_sql_fn(original_sql, step.skill)
            step.sql = final_sql
            step.error = error
            step.retry_log = retry_log  # store for detailed logging

            # Always record the SQL used for this step (for UI display)
            all_sqls.append(final_sql)

            if df is not None:
                step.df = df
                all_dfs.append(df)
            else:
                errors.append(f"Step {step.step_id}: {error}")

            skill_keys.append(step.skill)

        except Exception as e:
            step.error = str(e)
            errors.append(f"Step {step.step_id}: {e}")
            skill_keys.append(step.skill)

    # Merge results
    merged_df = _merge_dataframes(all_dfs, plan.merge_strategy) if all_dfs else None
    merge_desc = (
        f"Merged {len(all_dfs)} query results using {plan.merge_strategy}"
        if len(all_dfs) > 1 else ""
    )

    return MergedResult(
        df=merged_df,
        all_dfs=all_dfs,
        all_sqls=all_sqls,
        skill_keys=skill_keys,
        chart_hint=plan.chart_hint,
        merge_description=merge_desc,
        error="; ".join(errors) if errors else None,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION CONTEXT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_session_context(history: list[dict], max_turns: int = 8) -> str:
    """
    Build session context string from conversation history.
    Passes full user questions and rich assistant summaries so the agent
    can reference prior findings, maintain filter state, and resolve follow-ups.
    """
    turns: list[str] = []
    for msg in history[-(max_turns * 2):]:
        if msg.get("role") == "user":
            turns.append(f"Q: {msg.get('content', '')[:500]}")
        elif msg.get("role") == "assistant":
            data = msg.get("data")
            summary = getattr(data, "summary", "")[:800] if data is not None else ""
            sql = msg.get("sql", "")[:300]
            if summary:
                turns.append(f"A: {summary}")
            if sql:
                turns.append(f"SQL used: {sql}")
    return "\n".join(turns) if turns else ""
