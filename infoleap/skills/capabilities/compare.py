"""
Comparison Engine — Pure-Python Post-Processing (0 API Calls)
==============================================================
Analyses a query result DataFrame and generates structured comparison
narratives using template-based text generation.

Used by views/chat.py AFTER SQL execution, BEFORE the summary LLM call.
The comparison summary is injected into the summary prompt as context,
giving the LLM richer material without an extra API call.

Key public functions:
  should_compare(df, question) → bool
  build_comparison(df, question) → ComparisonResult | None
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


# ── entity column names (order = priority for detection) ───────────────────────
ENTITY_COLS = [
    "brand_name", "city_name", "zone_name", "appliance_name",
    "age_band", "gender", "nps_category", "stage",
]

# Columns to skip when hunting for the "metric" column
SKIP_COLS = {
    "respondent_id", "brand_id", "appliance_id", "date_id",
    "city_id", "zone_id", "id", "purchase_rank",
}


# ── comparison result dataclass ────────────────────────────────────────────────

@dataclass
class ComparisonResult:
    """Structured comparison output — consumed by the enriched summary prompt."""
    leader: str
    leader_value: float
    runner_up: str
    runner_up_value: float
    delta: float
    metric_name: str
    all_entities: list[tuple[str, float]] = field(default_factory=list)
    template_key: str = "leader"
    template: str = ""

    def to_context_string(self) -> str:
        """One-liner for injection into the summary prompt."""
        return self.template if self.template else (
            f"{self.leader} ({self.leader_value:.1f}) leads "
            f"{self.runner_up} ({self.runner_up_value:.1f}) "
            f"by {self.delta:.1f} on {self.metric_name}."
        )


# ── template library ──────────────────────────────────────────────────────────

COMPARE_TEMPLATES = {
    "leader": (
        "{leader} leads with {metric}={leader_val:.1f}%, "
        "ahead of {runner_up} ({runner_up_val:.1f}%) by {delta:.1f} pp."
    ),
    "close_race": (
        "{leader} ({leader_val:.1f}%) and {runner_up} ({runner_up_val:.1f}%) "
        "are closely matched on {metric} (gap: {delta:.1f} pp)."
    ),
    "dominant": (
        "{leader} dominates {metric} at {leader_val:.1f}%, more than double "
        "the nearest competitor {runner_up} ({runner_up_val:.1f}%)."
    ),
    "nps_rank": (
        "NPS ranking: {ranking_str}. {leader} has the strongest brand loyalty."
    ),
    "multi_entity": (
        "{leader} ({leader_val:.1f}) leads across {n_entities} entries. "
        "Runner-up: {runner_up} ({runner_up_val:.1f}). Gap: {delta:.1f}."
    ),
}


def _pick_template(delta: float, leader_val: float, runner_up_val: float,
                    metric_name: str) -> str:
    """Choose the best narrative template based on the comparison dynamics."""
    metric_lower = metric_name.lower()

    # NPS uses absolute scores, not percentages
    if "nps" in metric_lower:
        return "nps_rank"

    # Thresholds for "close" vs "dominant"
    if delta < 3.0:
        return "close_race"
    if runner_up_val > 0 and leader_val / runner_up_val >= 2.0:
        return "dominant"
    return "leader"


# ── detection ──────────────────────────────────────────────────────────────────

_COMPARE_WORDS = frozenset([
    "compare", "vs", "versus", "better", "worse", "difference",
    "lead", "ahead", "behind", "winner", "rank", "top",
    "highest", "lowest", "best", "most", "least",
])


def _find_entity_col(df: pd.DataFrame) -> str | None:
    """Return the first entity column found in the DataFrame."""
    for col in ENTITY_COLS:
        if col in df.columns:
            return col
    return None


def _find_metric_col(df: pd.DataFrame) -> str | None:
    """Return the best numeric metric column for ranking."""
    # Priority order: pct → nps → count → first numeric
    for frag in ["pct", "percent", "share", "nps", "count", "total", "n"]:
        for col in df.columns:
            if (frag in col.lower()
                    and pd.api.types.is_numeric_dtype(df[col])
                    and col.lower() not in SKIP_COLS):
                return col
    # Fallback: first numeric that isn't in SKIP_COLS
    for col in df.columns:
        if (pd.api.types.is_numeric_dtype(df[col])
                and col.lower() not in SKIP_COLS
                and not col.lower().endswith("_id")):
            return col
    return None


def should_compare(df: pd.DataFrame, question: str) -> bool:
    """
    Decide whether this result warrants a comparison analysis.

    Trigger conditions (all checked in Python, no LLM needed):
      - Explicit comparison intent words in the question, OR
      - Multiple entity rows (2–20) with a clear entity column
      - At least one numeric metric column
    """
    if df is None or df.empty:
        return False

    q = question.lower()
    has_compare_intent = any(w in q for w in _COMPARE_WORDS)
    has_multi_entities = 2 <= len(df) <= 20
    has_entity_col = _find_entity_col(df) is not None
    has_metric = _find_metric_col(df) is not None

    return (has_compare_intent or has_multi_entities) and has_entity_col and has_metric


# ── comparison builder ─────────────────────────────────────────────────────────

def build_comparison(df: pd.DataFrame, question: str) -> ComparisonResult | None:
    """
    Build a structured ComparisonResult from the DataFrame.

    Returns None if the data doesn't support meaningful comparison.
    """
    if not should_compare(df, question):
        return None

    entity_col = _find_entity_col(df)
    metric_col = _find_metric_col(df)
    if not entity_col or not metric_col:
        return None

    # Sort descending by the metric column
    df_sorted = df.sort_values(metric_col, ascending=False).reset_index(drop=True)

    # Extract leader and runner-up
    leader = str(df_sorted.iloc[0][entity_col])
    leader_val = float(df_sorted.iloc[0][metric_col])

    if len(df_sorted) >= 2:
        runner_up = str(df_sorted.iloc[1][entity_col])
        runner_up_val = float(df_sorted.iloc[1][metric_col])
    else:
        runner_up = ""
        runner_up_val = 0.0

    delta = round(abs(leader_val - runner_up_val), 1)
    metric_name = metric_col.replace("_", " ").title()

    # Build the full entity ranking list
    all_entities = [
        (str(row[entity_col]), float(row[metric_col]))
        for _, row in df_sorted.iterrows()
    ]

    # Pick the right template
    template_key = _pick_template(delta, leader_val, runner_up_val, metric_col)

    # Format the template
    ranking_str = ", ".join(
        f"{name} ({val:.1f})" for name, val in all_entities
    )

    try:
        template = COMPARE_TEMPLATES[template_key].format(
            leader=leader,
            leader_val=leader_val,
            runner_up=runner_up,
            runner_up_val=runner_up_val,
            delta=delta,
            metric=metric_name,
            ranking_str=ranking_str,
            n_entities=len(all_entities),
        )
    except KeyError:
        # Fallback template
        template = (
            f"{leader} ({leader_val:.1f}) leads {runner_up} ({runner_up_val:.1f}) "
            f"by {delta:.1f} on {metric_name}."
        )

    return ComparisonResult(
        leader=leader,
        leader_value=leader_val,
        runner_up=runner_up,
        runner_up_value=runner_up_val,
        delta=delta,
        metric_name=metric_name,
        all_entities=all_entities,
        template_key=template_key,
        template=template,
    )
