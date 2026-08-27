"""
MaxDiff-Equivalent Preference Share Engine
==========================================
Derives attribute preference shares from bq3a importance ratings (1–7 scale)
using softmax / multinomial-logit probability scaling.

IMPORTANT CAVEAT:
    True MaxDiff (Best-Worst Scaling) requires an experimental design where
    respondents repeatedly choose the best and worst items from small subsets.
    This dataset collected standard Likert importance ratings (bq3a, 1–7 scale)
    — NOT best-worst choice tasks. The preference shares produced here are an
    importance-derived approximation that mimics the XLSTAT MaxDiff output
    format (utility score + preference share % summing to 100 + rank).

    Do NOT present this output as true MaxDiff / Best-Worst Scaling results.
    Use the "caveat" field in all output dicts when surfacing to end users.

Method
------
1.  Compute mean_importance, std, n per attribute from fact_need_importance.
2.  Apply softmax scaling:
        pref_share_i = exp(mean_i / tau) / sum_j(exp(mean_j / tau)) × 100
    where tau is a temperature parameter (default 1.0).
    Shares sum exactly to 100 (within floating-point precision).
3.  Also expose simple normalized share:
        norm_share_i = mean_i / sum_j(mean_j) × 100
4.  Rescaled utility (0–100 min-max of mean_importance) — XLSTAT-style.
5.  95 % CI on mean: ±1.96 × std / sqrt(n).
6.  Category filtering: fact_need_importance has no category_code column;
    category is applied by restricting respondents to those who appear in
    fact_brand_imagery for the requested category codes.

Dependencies: numpy, pandas, sqlite3 (stdlib). No streamlit, no external APIs.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ── project path bootstrap ────────────────────────────────────────────────────
_LENS_DIR  = Path(__file__).resolve().parent.parent   # lens/
_PROJ_ROOT = _LENS_DIR.parent                          # project root
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

_DEFAULT_DB = _PROJ_ROOT / "oxdata" / "data" / "project_1" / "oxdata.db"

# ── temperature parameter (softmax) ──────────────────────────────────────────
_DEFAULT_TAU: float = 1.0

# ── min respondents default ───────────────────────────────────────────────────
_DEFAULT_MIN_N: int = 30

# ── caveat string (surfaced in all public outputs) ────────────────────────────
_CAVEAT = (
    "Not true MaxDiff — derived from bq3a importance ratings "
    "(no best-worst choice data collected)."
)

# ── product code map (mirrors can_map_engine.PRODUCT_CODES) ──────────────────
_PRODUCT_CODES: dict[str, list[int]] = {
    "all":           list(range(1, 13)),
    "ceiling fans":  [1, 7],
    "air cooler":    [2, 8],
    "mixer grinder": [3, 9],
    "led batten":    [4, 10],
    "water heater":  [5, 11],
    "water pumps":   [6, 12],
}


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_db() -> str:
    """Return string path to oxdata.db via db_loader, falling back to _DEFAULT_DB."""
    try:
        from oxdata.db_loader import get_db_path
        found = get_db_path(required_table="fact_need_importance")
        if found:
            return str(found)
        found = get_db_path()
        if found:
            return str(found)
    except Exception:
        pass
    return str(_DEFAULT_DB)


def _ro_conn(db_path: str) -> sqlite3.Connection:
    """Open a read-only URI connection."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _cat_codes(category: Optional[str]) -> Optional[list[int]]:
    """Map category name → list of category_code ints, or None for all."""
    if not category or str(category).strip().lower() in ("all", ""):
        return None
    key = str(category).strip().lower()
    return _PRODUCT_CODES.get(key)


def _check_schema(conn: sqlite3.Connection) -> dict[str, bool]:
    """Return flags indicating which optional columns / tables exist."""
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    # Check whether fact_need_importance has a category_code column
    ni_cols: set[str] = set()
    if "fact_need_importance" in tables:
        ni_cols = {
            r[1] for r in conn.execute(
                "PRAGMA table_info(fact_need_importance)"
            ).fetchall()
        }
    return {
        "has_need_importance":      "fact_need_importance" in tables,
        "has_brand_imagery":        "fact_brand_imagery"   in tables,
        "ni_has_category_code":     "category_code"        in ni_cols,
        "has_dim_attr":             "dim_bq3_attribute"    in tables,
    }


def _get_respondent_filter_sql(
    cat_codes: Optional[list[int]],
    schema: dict[str, bool],
) -> tuple[str, list]:
    """
    Build a SQL snippet (and params) that restricts importance rows to
    respondents belonging to the requested category.

    Strategy:
    - If fact_need_importance has category_code → filter directly.
    - Otherwise (no category column) → use a subquery against fact_brand_imagery
      to get the set of respondent_ids who appear in the requested category.
    - If cat_codes is None → no filter, return ('', []).

    Returns
    -------
    (where_clause_fragment, params)
        where_clause_fragment: e.g. "AND ni.respondent_id IN (...)" or ""
        params: list of bind values
    """
    if cat_codes is None:
        return "", []

    if schema["ni_has_category_code"]:
        placeholders = ",".join("?" * len(cat_codes))
        return f"AND ni.category_code IN ({placeholders})", list(cat_codes)

    if schema["has_brand_imagery"]:
        placeholders = ",".join("?" * len(cat_codes))
        subq = (
            f"AND ni.respondent_id IN ("
            f"SELECT DISTINCT respondent_id FROM fact_brand_imagery "
            f"WHERE category_code IN ({placeholders})"
            f")"
        )
        return subq, list(cat_codes)

    # No way to filter — degrade gracefully, return all respondents
    return "", []


# ─────────────────────────────────────────────────────────────────────────────
# Preference share computation (pure numpy)
# ─────────────────────────────────────────────────────────────────────────────

def _softmax_shares(means: np.ndarray, tau: float) -> np.ndarray:
    """
    Softmax preference shares from mean importance scores.

    pref_i = exp(mean_i / tau) / sum_j exp(mean_j / tau) × 100

    Numerically stable via subtract-max before exponentiation.
    Returns array summing to 100.0.
    """
    if tau <= 0:
        raise ValueError(f"tau must be > 0; got {tau}")
    shifted = means / tau - (means / tau).max()
    exp_vals = np.exp(shifted)
    return exp_vals / exp_vals.sum() * 100.0


def _norm_shares(means: np.ndarray) -> np.ndarray:
    """Simple normalized share: mean_i / sum × 100. Sums to 100."""
    total = means.sum()
    if total == 0:
        return np.zeros_like(means)
    return means / total * 100.0


def _rescaled_utility(means: np.ndarray) -> np.ndarray:
    """
    0–100 min-max rescale of mean_importance.
    Mimics XLSTAT 'rescaled score' column.
    """
    mn, mx = means.min(), means.max()
    if mx == mn:
        return np.full_like(means, 50.0)
    return (means - mn) / (mx - mn) * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute_maxdiff(
    category: Optional[str] = None,
    min_n: int = _DEFAULT_MIN_N,
    tau: float = _DEFAULT_TAU,
) -> dict:
    """
    Compute importance-derived MaxDiff-equivalent preference shares.

    Parameters
    ----------
    category : str | None
        Product category name (case-insensitive). Valid values:
        "all", "ceiling fans", "air cooler", "mixer grinder",
        "led batten", "water heater", "water pumps".
        None / "all" → no category filter (all respondents).
    min_n : int
        Minimum number of respondents rating an attribute for it to be
        included in the output. Default 30.
    tau : float
        Softmax temperature. Higher values compress differences between
        attributes (flatter distribution); lower values amplify them.
        Default 1.0.

    Returns
    -------
    dict with keys:
        "items"         list[dict]  — sorted by pref_share desc
            Each item: {
                "attr_id"          int,
                "attr_label"       str,
                "broad_feature"    str,
                "mean_importance"  float,
                "std"              float,
                "n"                int,
                "pref_share"       float,   # softmax, sums to 100
                "norm_share"       float,   # simple normalized, sums to 100
                "rescaled_utility" float,   # 0-100 min-max rescale
                "ci_low"           float,   # 95% CI lower bound
                "ci_high"          float,   # 95% CI upper bound
                "rank"             int,     # 1 = most preferred
            }
        "n_respondents" int     — unique respondents in the analysis set
        "category"      str | None
        "tau"           float
        "method"        str
        "caveat"        str
        "error"         None | str
    """
    _base = dict(
        items=[],
        n_respondents=0,
        category=category,
        tau=tau,
        method="Importance-derived preference share (softmax scaling)",
        caveat=_CAVEAT,
        error=None,
    )

    try:
        db_path = _resolve_db()
        conn    = _ro_conn(db_path)

        schema = _check_schema(conn)
        if not schema["has_need_importance"]:
            conn.close()
            return {**_base, "error": "fact_need_importance table not found in DB."}
        if not schema["has_dim_attr"]:
            conn.close()
            return {**_base, "error": "dim_bq3_attribute table not found in DB."}

        cat_codes = _cat_codes(category)
        resp_filter_sql, resp_params = _get_respondent_filter_sql(cat_codes, schema)

        # ── 1. Aggregate importance per attribute ─────────────────────────
        sql = f"""
            SELECT
                ni.attr_id,
                da.attr_label,
                da.broad_feature,
                AVG(CAST(ni.score AS REAL))              AS mean_importance,
                -- population std via variance formula
                -- SQLite has no STDDEV; compute manually
                AVG(CAST(ni.score AS REAL) * CAST(ni.score AS REAL)) AS mean_sq,
                COUNT(ni.respondent_id)                  AS n
            FROM  fact_need_importance ni
            JOIN  dim_bq3_attribute    da ON da.attr_id = ni.attr_id
            WHERE ni.score IS NOT NULL
              AND ni.score BETWEEN 1 AND 7
              {resp_filter_sql}
            GROUP BY ni.attr_id, da.attr_label, da.broad_feature
            HAVING COUNT(ni.respondent_id) >= ?
            ORDER BY mean_importance DESC
        """
        params = resp_params + [min_n]
        agg_df = pd.read_sql_query(sql, conn, params=params)

        # ── 2. Count unique respondents in analysis set ───────────────────
        resp_sql = f"""
            SELECT COUNT(DISTINCT ni.respondent_id)
            FROM  fact_need_importance ni
            WHERE ni.score IS NOT NULL
              {resp_filter_sql}
        """
        n_resp = conn.execute(resp_sql, resp_params).fetchone()[0] or 0

        conn.close()

        if agg_df.empty:
            return {**_base, "n_respondents": n_resp,
                    "error": "No attributes passed the min_n filter."}

        # ── 3. Std = sqrt(E[X²] − (E[X])²) ──────────────────────────────
        agg_df["std"] = np.sqrt(
            np.clip(agg_df["mean_sq"] - agg_df["mean_importance"] ** 2, 0, None)
        )

        means = agg_df["mean_importance"].values.astype(float)
        stds  = agg_df["std"].values.astype(float)
        ns    = agg_df["n"].values.astype(float)

        # ── 4. Preference shares ──────────────────────────────────────────
        pref_shares    = _softmax_shares(means, tau)
        norm_shares    = _norm_shares(means)
        rescaled_utils = _rescaled_utility(means)

        # ── 5. 95% CI ─────────────────────────────────────────────────────
        se     = np.where(ns > 0, stds / np.sqrt(np.clip(ns, 1, None)), 0.0)
        ci_low = means - 1.96 * se
        ci_hi  = means + 1.96 * se

        # ── 6. Rank (1 = highest pref_share) ─────────────────────────────
        # argsort descending → rank 1..N
        order = np.argsort(-pref_shares)
        ranks = np.empty(len(pref_shares), dtype=int)
        ranks[order] = np.arange(1, len(pref_shares) + 1)

        # ── 7. Build items list ───────────────────────────────────────────
        items = []
        for idx in range(len(agg_df)):
            row = agg_df.iloc[idx]
            items.append({
                "attr_id":          int(row["attr_id"]),
                "attr_label":       str(row["attr_label"]),
                "broad_feature":    str(row["broad_feature"]) if pd.notna(row["broad_feature"]) else "",
                "mean_importance":  round(float(means[idx]), 4),
                "std":              round(float(stds[idx]),  4),
                "n":                int(ns[idx]),
                "pref_share":       round(float(pref_shares[idx]),    4),
                "norm_share":       round(float(norm_shares[idx]),    4),
                "rescaled_utility": round(float(rescaled_utils[idx]), 4),
                "ci_low":           round(float(ci_low[idx]),  4),
                "ci_high":          round(float(ci_hi[idx]),   4),
                "rank":             int(ranks[idx]),
            })

        # Sort by pref_share descending (rank 1 first)
        items.sort(key=lambda x: x["pref_share"], reverse=True)

        return {
            **_base,
            "items":        items,
            "n_respondents": int(n_resp),
        }

    except Exception as exc:
        return {**_base, "error": f"{type(exc).__name__}: {exc}"}


# ─────────────────────────────────────────────────────────────────────────────
# Agent-readable summary
# ─────────────────────────────────────────────────────────────────────────────

def maxdiff_summary_text(result: dict) -> str:
    """
    Return a concise agent-readable text summary of compute_maxdiff() output.

    Includes: top 5 by preference share, bottom 3, total attributes, caveat.
    """
    if result.get("error"):
        return f"MaxDiff compute error: {result['error']}"

    items  = result.get("items", [])
    n_attr = len(items)
    n_resp = result.get("n_respondents", 0)
    cat    = result.get("category") or "All categories"
    tau    = result.get("tau", _DEFAULT_TAU)

    if n_attr == 0:
        return f"No attributes returned for category='{cat}'."

    lines = [
        f"Importance-derived preference shares — {cat} (n={n_resp:,} respondents, "
        f"{n_attr} attributes, tau={tau})",
        "",
        "TOP 5 attributes (by preference share):",
    ]
    for item in items[:5]:
        lines.append(
            f"  #{item['rank']:>2}  {item['attr_label']:<40}  "
            f"pref_share={item['pref_share']:.2f}%  "
            f"mean_imp={item['mean_importance']:.2f}  "
            f"n={item['n']:,}"
        )

    lines.append("")
    lines.append("BOTTOM 3 attributes:")
    for item in items[-3:]:
        lines.append(
            f"  #{item['rank']:>2}  {item['attr_label']:<40}  "
            f"pref_share={item['pref_share']:.2f}%  "
            f"mean_imp={item['mean_importance']:.2f}  "
            f"n={item['n']:,}"
        )

    # Validate share sum
    share_sum = sum(x["pref_share"] for x in items)
    lines.append("")
    lines.append(f"Sum of pref_shares: {share_sum:.2f}% (should be ~100)")
    lines.append("")
    lines.append(f"NOTE: {result['caveat']}")

    return "\n".join(lines)
