"""
Result Validator — Post-execution sanity checks for SQL query results.
======================================================================
Detects bad outputs before they reach the user:
  - NPS out of [-100, 100] range
  - Percentages > 100 or < 0 (wrong denominator — the most common bug)
  - Empty results when data should exist
  - Implausible counts (more owners than total respondents)
  - TOM % summing > 100% (respondents counted multiple times)

Public API:
  validate_result(df, skill_key, question) → ValidationResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
import pandas as pd


@dataclass
class ValidationResult:
    ok: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = []
        for e in self.errors:
            lines.append(f"ERROR: {e}")
        for w in self.warnings:
            lines.append(f"WARN: {w}")
        return "\n".join(lines) if lines else "OK"


def _pct_cols(df: pd.DataFrame) -> list[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c])
        and any(x in c.lower() for x in ["pct", "percent", "share", "nps"])
        and c.lower() != "nps"  # handle NPS separately
    ]


def _nps_cols(df: pd.DataFrame) -> list[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c])
        and "nps" in c.lower()
    ]


def _count_cols(df: pd.DataFrame) -> list[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c])
        and any(x in c.lower() for x in ["count", "owners", "aware", "total", "mentions", "raters"])
        and not any(x in c.lower() for x in ["pct", "percent"])
    ]


def validate_result(
    df: pd.DataFrame | None,
    skill_key: str,
    question: str,
    total_respondents: int = 6631,
) -> ValidationResult:
    """
    Sanity-check a query result DataFrame.

    Args:
        df:                 The query result (may be None/empty).
        skill_key:          Which skill produced this (awareness/nps/ownership/etc).
        question:           Original user question (for context in warnings).
        total_respondents:  Total survey respondents (for cross-check).

    Returns:
        ValidationResult with ok flag, warnings, and errors.
    """
    result = ValidationResult(ok=True)

    # Empty result
    if df is None or df.empty:
        result.ok = False
        result.errors.append("Query returned no rows — likely wrong filter or missing data.")
        return result

    q = question.lower()

    # ── NPS range check ──────────────────────────────────────────────────────
    for col in _nps_cols(df):
        bad = df[(df[col] < -100) | (df[col] > 100)][col]
        if not bad.empty:
            result.ok = False
            result.errors.append(
                f"NPS column '{col}' has values outside [-100, 100]: {bad.tolist()[:5]}. "
                "Likely wrong denominator (used total respondents instead of raters)."
            )

    # ── Percentage range check ────────────────────────────────────────────────
    for col in _pct_cols(df):
        over = df[df[col] > 100][col]
        under = df[df[col] < 0][col]
        if not over.empty:
            result.ok = False
            result.errors.append(
                f"Column '{col}' has values > 100%: {over.tolist()[:5]}. "
                "Wrong denominator — check if zone/city filter scope is applied to base."
            )
        if not under.empty:
            result.warnings.append(
                f"Column '{col}' has negative values: {under.tolist()[:5]}."
            )

    # ── TOM % sum check (should not exceed 100% for same group) ───────────────
    tom_pct_col = next((c for c in df.columns if "tom_pct" in c.lower()), None)
    if tom_pct_col and "zone_name" not in df.columns and "city_name" not in df.columns:
        total_tom = df[tom_pct_col].sum()
        if total_tom > 105:
            result.warnings.append(
                f"TOM% sum = {total_tom:.1f}% (>100%). "
                "Each respondent can only have ONE top-of-mind brand. "
                "Check for duplicate respondent_ids or wrong COUNT logic."
            )

    # ── Count vs respondent cap check ────────────────────────────────────────
    for col in _count_cols(df):
        over = df[df[col] > total_respondents][col]
        if not over.empty:
            result.warnings.append(
                f"Count column '{col}' has values > total respondents ({total_respondents}): "
                f"{over.tolist()[:3]}. Possible double-count — use COUNT(DISTINCT respondent_id)."
            )

    # ── Awareness-specific checks ─────────────────────────────────────────────
    if skill_key == "awareness":
        spont_col = next((c for c in df.columns if "spont_pct" in c.lower()), None)
        tom_col = next((c for c in df.columns if "tom_pct" in c.lower()), None)
        total_col = next((c for c in df.columns if "total_pct" in c.lower()), None)

        if tom_col and spont_col:
            violations = df[df[tom_col] > df[spont_col]]
            if not violations.empty:
                result.warnings.append(
                    f"TOM% > Spont% for some rows — impossible (TOM is subset of Spont). "
                    f"Rows: {violations.index.tolist()[:3]}. "
                    "Spont must include TOM respondents."
                )

        if spont_col and total_col:
            violations = df[df[spont_col] > df[total_col]]
            if not violations.empty:
                result.warnings.append(
                    f"Spont% > Total% for some rows — impossible (Spont ⊆ Total aware). "
                    f"Rows: {violations.index.tolist()[:3]}."
                )

    # ── Penetration > 100% check ──────────────────────────────────────────────
    if skill_key in ("ownership", "room", "purchase"):
        pct_col = next((c for c in df.columns if c.lower() == "pct"), None)
        if pct_col:
            over = df[df[pct_col] > 100]
            if not over.empty:
                result.ok = False
                result.errors.append(
                    f"Penetration > 100% detected: {over[pct_col].tolist()[:5]}. "
                    "Wrong denominator — when filtering by zone/city, use zone/city-scoped respondent count."
                )

    return result


def format_validation_warning(vr: ValidationResult) -> str | None:
    """Return a human-readable warning string if validation found issues, else None."""
    if vr.ok and not vr.warnings:
        return None
    parts = []
    for e in vr.errors:
        parts.append(f"⚠️ **Data issue:** {e}")
    for w in vr.warnings:
        parts.append(f"ℹ️ **Note:** {w}")
    return "\n\n".join(parts) if parts else None
