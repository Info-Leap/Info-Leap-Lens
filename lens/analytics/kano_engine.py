"""
kano_engine.py — Derived Kano Analysis for InfoLeap Pulse brand health suite.

IMPORTANT — Method note:
  Classic Kano analysis requires paired functional/dysfunctional survey questions
  (e.g. "How do you feel if feature X IS present?" vs "if it IS NOT present?").
  This dataset does NOT have those paired questions.

  Instead, this module implements **Penalty-Reward Contrast Analysis (PRCA)**,
  also known as the Three-Factor Theory approach (Matzler & Hinterhuber, 1998;
  Brandt 1987). PRCA derives Kano-equivalent categories from:
    - Binary attribute presence (does respondent associate their brand with X?)
    - Customer satisfaction scores (CSAT 0-10)
  by comparing mean CSAT when an attribute is present vs. absent.

  This is statistically legitimate and widely used in academic and commercial
  market research when paired Kano questions are unavailable.

  Reference:
    Matzler, K. & Hinterhuber, H.H. (1998). How to make product development
    projects more successful by integrating Kano's model of customer
    satisfaction into quality function deployment. Technovation, 18(1), 25-38.

Dependencies: numpy, pandas; statsmodels optional (numpy OLS fallback used).
No Streamlit. No external API calls. Read-only SQLite access.
"""

from __future__ import annotations

import sqlite3
import sys
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ── project path bootstrap ────────────────────────────────────────────────────
_LENS_DIR = Path(__file__).resolve().parent.parent
_PROJ_ROOT = _LENS_DIR.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

# ── statsmodels optional import ───────────────────────────────────────────────
try:
    import statsmodels.api as _sm
    _SM_OK = True
except ImportError:
    _SM_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# Product-category code mapping (mirrors can_map_engine.PRODUCT_CODES and
# analytics_tools._PRODUCT_CODES).  Keys are lowercase for normalisation.
# ─────────────────────────────────────────────────────────────────────────────
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
# Kano classification thresholds (tunable)
# ─────────────────────────────────────────────────────────────────────────────
# Indices below this magnitude are "negligible" → indifferent class
_INDIFFERENT_THRESHOLD: float = 0.05   # CSAT points (0-10 scale)

# Asymmetry ratio threshold for must-be / attractive split
# ratio = penalty / reward  ≥ _ASYMMETRY_RATIO  → must_be
# ratio = reward / penalty  ≥ _ASYMMETRY_RATIO  → attractive
_ASYMMETRY_RATIO: float = 1.5

# Minimum base size for each split to be considered "meaningful"
_MIN_N_DEFAULT: int = 30


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ro_conn(project_id: str | None = None) -> sqlite3.Connection:
    """Open the project DB in read-only mode."""
    try:
        from oxdata.db_loader import get_db_path
        db_path = get_db_path(required_table="fact_satisfaction", project_id=project_id)
        if db_path is None:
            raise FileNotFoundError("get_db_path() returned None")
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception as exc:
        raise RuntimeError(f"Cannot open DB: {exc}") from exc


def _cat_filter_clause(conn: sqlite3.Connection, category: str | None, table_alias: str = "bi") -> tuple[str, list]:
    """Return (WHERE-fragment, params) for category_code filtering on fact_brand_imagery."""
    if not category:
        return "", []
    key = str(category).strip().lower()
    codes = _PRODUCT_CODES.get(key)
    if not codes or key == "all":
        return "", []
    try:
        has_cat = conn.execute(
            "SELECT 1 FROM fact_brand_imagery WHERE category_code IS NOT NULL LIMIT 1"
        ).fetchone()
        if not has_cat:
            return "", []
    except Exception:
        return "", []
    placeholders = ",".join("?" * len(codes))
    return f"AND {table_alias}.category_code IN ({placeholders})", list(codes)


def _classify_kano(reward: float, penalty: float) -> str:
    """
    Classify a single attribute into a derived Kano category.

    Uses Penalty-Reward Contrast asymmetry logic:
      - indifferent : both reward and penalty below _INDIFFERENT_THRESHOLD
      - must_be     : penalty >> reward  (penalty/reward ≥ _ASYMMETRY_RATIO)
      - attractive  : reward >> penalty  (reward/penalty ≥ _ASYMMETRY_RATIO)
      - performance : roughly symmetric (both meaningful, ratio within bounds)
    """
    if reward < -_INDIFFERENT_THRESHOLD and penalty < -_INDIFFERENT_THRESHOLD:
        return "reverse"

    abs_r = abs(reward)
    abs_p = abs(penalty)

    if abs_r < _INDIFFERENT_THRESHOLD and abs_p < _INDIFFERENT_THRESHOLD:
        return "indifferent"

    # Guard division by zero with a small epsilon
    eps = 1e-9
    if abs_p >= _ASYMMETRY_RATIO * (abs_r + eps):
        return "must_be"
    if abs_r >= _ASYMMETRY_RATIO * (abs_p + eps):
        return "attractive"
    return "performance"


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_base_data(
    conn: sqlite3.Connection,
    category: str | None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Load the three tables needed for PRCA.

    Returns
    -------
    df_csat : respondent_id, score          (CSAT respondents only)
    df_img  : respondent_id, attr_id        (presence rows, filtered to cohort)
    df_imp  : attr_id, mean_importance      (aggregated per attribute)
    """
    # 1. CSAT table
    df_csat = pd.read_sql_query(
        "SELECT respondent_id, score FROM fact_satisfaction",
        conn,
    )

    # 2. Last-purchased brand per CSAT respondent (to filter imagery correctly)
    #    We only want imagery where the brand = the brand they last purchased
    #    and the respondent is in the CSAT cohort.
    csat_ids_str = ",".join(str(int(r)) for r in df_csat["respondent_id"].tolist())

    cat_clause, cat_params = _cat_filter_clause(conn, category, table_alias="bi")

    # Join: get (respondent_id, attr_id) for their own last-purchased brand only
    imagery_sql = f"""
        SELECT
            bi.respondent_id,
            bi.attr_id
        FROM fact_brand_imagery bi
        JOIN fact_brand_awareness fba
            ON bi.respondent_id = fba.respondent_id
           AND bi.brand_id      = fba.brand_id
           AND fba.stage        = 'LAST_PURCHASED'
        WHERE bi.respondent_id IN ({csat_ids_str})
        {cat_clause}
    """
    df_img = pd.read_sql_query(imagery_sql, conn, params=cat_params)

    # 3. Mean importance per attribute (bq3a, all respondents)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fact_need_importance)")}
    score_col = "importance_score" if "importance_score" in cols else "score"
    df_imp = pd.read_sql_query(
        f"""
        SELECT attr_id, AVG({score_col}) AS mean_importance
        FROM fact_need_importance
        GROUP BY attr_id
        """,
        conn,
    )

    return df_csat, df_img, df_imp


def _load_attr_meta(conn: sqlite3.Connection) -> pd.DataFrame:
    """Load attribute dimension table."""
    return pd.read_sql_query(
        "SELECT attr_id, attr_label, broad_feature FROM dim_bq3_attribute",
        conn,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PRCA computation
# ─────────────────────────────────────────────────────────────────────────────

def _compute_prca(
    df_csat: pd.DataFrame,
    df_img: pd.DataFrame,
    min_n: int,
) -> pd.DataFrame:
    """
    Core Penalty-Reward Contrast Analysis.

    Parameters
    ----------
    df_csat : DataFrame with columns [respondent_id, score]
    df_img  : DataFrame with columns [respondent_id, attr_id]
              — rows where respondent associates attr with their brand
    min_n   : minimum n_present AND n_absent to include an attribute

    Returns
    -------
    DataFrame with one row per attribute:
      attr_id, reward_index, penalty_index, mean_csat_present,
      mean_csat_absent, n_present, n_absent, impact_coef
    """
    # Build presence pivot: respondents (rows) × attributes (cols), binary
    n_respondents = df_csat["respondent_id"].nunique()
    all_resp = df_csat["respondent_id"].unique()
    all_attrs = df_img["attr_id"].unique()

    # Sparse approach: use merge rather than full pivot for memory efficiency
    # Mark present respondent-attribute pairs
    df_presence = df_img[["respondent_id", "attr_id"]].drop_duplicates()
    df_presence = df_presence[df_presence["respondent_id"].isin(all_resp)]
    df_presence["present"] = 1

    # Mean CSAT overall
    mean_csat_overall = float(df_csat["score"].mean())

    records = []
    for attr_id in all_attrs:
        attr_subset = df_presence[df_presence["attr_id"] == attr_id][["respondent_id"]].copy()
        present_ids = set(attr_subset["respondent_id"].tolist())
        absent_ids  = set(all_resp) - present_ids

        n_present = len(present_ids)
        n_absent  = len(absent_ids)

        if n_present < min_n or n_absent < min_n:
            continue

        csat_present_mask = df_csat["respondent_id"].isin(present_ids)
        mean_csat_present = float(df_csat.loc[csat_present_mask, "score"].mean())
        mean_csat_absent  = float(df_csat.loc[~csat_present_mask, "score"].mean())

        reward_index  = mean_csat_present - mean_csat_overall
        penalty_index = mean_csat_overall - mean_csat_absent

        records.append({
            "attr_id":           attr_id,
            "reward_index":      reward_index,
            "penalty_index":     penalty_index,
            "mean_csat_present": mean_csat_present,
            "mean_csat_absent":  mean_csat_absent,
            "n_present":         n_present,
            "n_absent":          n_absent,
        })

    df_prca = pd.DataFrame(records)

    # ── OLS impact coefficients ──────────────────────────────────────────────
    # Fit CSAT ~ Σ(presence dummies) for all qualifying attributes at once.
    # Standardised coefficient = relative impact direction + magnitude.
    df_prca["impact_coef"] = np.nan
    if len(df_prca) > 0:
        qualifying_attrs = df_prca["attr_id"].tolist()

        # Build design matrix (respondents × qualifying_attrs), binary
        # Only keep respondents in df_csat; attrs already filtered above
        piv = (
            df_presence[df_presence["attr_id"].isin(qualifying_attrs)]
            .pivot_table(index="respondent_id", columns="attr_id", values="present", fill_value=0)
        )
        # Align to all csat respondents (those absent from pivot = 0 for all attrs)
        piv = piv.reindex(df_csat["respondent_id"].values, fill_value=0)
        X = piv.values.astype(float)         # (n_resp, n_attrs)
        y = df_csat["score"].values.astype(float)

        # Standardise X columns (each binary) and y for comparability
        # Using sklearn-style manual standardisation for zero-dep path
        x_std = X.std(axis=0)
        y_std = y.std()
        # Drop near-zero variance columns to avoid rank deficiency
        valid_mask = x_std > 1e-6
        X_valid = X[:, valid_mask]
        valid_attr_ids = np.array(qualifying_attrs)[valid_mask]

        if X_valid.shape[1] > 0 and y_std > 1e-6:
            X_std_cols = (X_valid - X_valid.mean(axis=0)) / x_std[valid_mask]
            y_std_vals = (y - y.mean()) / y_std

            # OLS via statsmodels (with intercept) or numpy lstsq fallback
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    if _SM_OK:
                        Xc = _sm.add_constant(X_std_cols, has_constant="add")
                        res = _sm.OLS(y_std_vals, Xc).fit()
                        coefs = res.params[1:]   # drop intercept
                    else:
                        # numpy lstsq with bias column
                        ones = np.ones((X_std_cols.shape[0], 1))
                        Xc = np.hstack([ones, X_std_cols])
                        coefs, *_ = np.linalg.lstsq(Xc, y_std_vals, rcond=None)
                        coefs = coefs[1:]         # drop intercept

                    # Map coefs back to attr_id positions
                    coef_map = dict(zip(valid_attr_ids.tolist(), coefs.tolist()))
                    df_prca["impact_coef"] = df_prca["attr_id"].map(coef_map)
                except Exception:
                    pass  # leave impact_coef as NaN — not fatal

    return df_prca


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute_kano(
    category: str | None = None,
    min_n: int = _MIN_N_DEFAULT,
    project_id: str | None = None,
) -> dict:
    """
    Compute derived Kano analysis via Penalty-Reward Contrast Analysis (PRCA).

    DERIVED Kano — not classic dual-question Kano. See module docstring.

    Parameters
    ----------
    category : str | None
        Product category filter. Accepted keys (case-insensitive):
        'all', 'ceiling fans', 'air cooler', 'mixer grinder',
        'led batten', 'water heater', 'water pumps'.
        None or 'all' → no category filter on imagery rows.
    min_n : int
        Minimum respondents with attribute present AND absent to include an
        attribute in the analysis (default 30). Lower values give more
        attributes but less stable estimates.
    project_id : str | None
        Optional project ID to load database for.

    Returns
    -------
    dict with keys:
      attributes       : list of attribute dicts (see below)
      n_respondents    : int   — CSAT respondents used
      mean_csat_overall: float — mean CSAT across cohort
      category         : echoed from input
      method           : description string
      error            : None | str

    Each attribute dict contains:
      attr_id, attr_label, broad_feature, kano_category,
      reward_index, penalty_index, impact_coef,
      mean_importance, mean_csat_present, mean_csat_absent,
      n_present, n_absent
    """
    _empty = {
        "attributes": [],
        "n_respondents": 0,
        "mean_csat_overall": float("nan"),
        "category": category,
        "method": "Penalty-Reward Contrast Analysis (derived Kano)",
        "error": None,
    }

    try:
        conn = _ro_conn(project_id=project_id)
    except Exception as exc:
        _empty["error"] = f"DB connection failed: {exc}"
        return _empty

    try:
        df_csat, df_img, df_imp = _load_base_data(conn, category)
        df_attr = _load_attr_meta(conn)
    except Exception as exc:
        conn.close()
        _empty["error"] = f"Data load failed: {exc}"
        return _empty
    finally:
        conn.close()

    if df_csat.empty:
        _empty["error"] = "fact_satisfaction is empty — no CSAT data available."
        return _empty

    if df_img.empty:
        _empty["error"] = (
            "No imagery rows found for CSAT respondents with LAST_PURCHASED brand. "
            "Check fact_brand_imagery and fact_brand_awareness data."
        )
        return _empty

    mean_csat_overall = float(df_csat["score"].mean())
    n_respondents = int(df_csat["respondent_id"].nunique())

    # ── Core PRCA computation ────────────────────────────────────────────────
    try:
        df_prca = _compute_prca(df_csat, df_img, min_n)
    except Exception as exc:
        _empty["error"] = f"PRCA computation failed: {exc}"
        _empty["n_respondents"] = n_respondents
        _empty["mean_csat_overall"] = mean_csat_overall
        return _empty

    if df_prca.empty:
        _empty["error"] = (
            f"No attributes survived the min_n={min_n} filter. "
            "Try lowering min_n or check that imagery and CSAT respondents overlap."
        )
        _empty["n_respondents"] = n_respondents
        _empty["mean_csat_overall"] = mean_csat_overall
        return _empty

    # ── Kano classification ──────────────────────────────────────────────────
    df_prca["kano_category"] = df_prca.apply(
        lambda r: _classify_kano(r["reward_index"], r["penalty_index"]),
        axis=1,
    )

    # ── Merge attribute labels and importance ────────────────────────────────
    df_out = df_prca.merge(df_attr, on="attr_id", how="left")
    df_out = df_out.merge(df_imp, on="attr_id", how="left")

    # ── Assemble output list ─────────────────────────────────────────────────
    attributes = []
    for _, row in df_out.iterrows():
        attributes.append({
            "attr_id":           int(row["attr_id"]),
            "attr_label":        str(row.get("attr_label") or ""),
            "broad_feature":     str(row.get("broad_feature") or ""),
            "kano_category":     str(row["kano_category"]),
            "reward_index":      float(row["reward_index"]),
            "penalty_index":     float(row["penalty_index"]),
            "impact_coef":       float(row["impact_coef"]) if pd.notna(row.get("impact_coef")) else None,
            "mean_importance":   float(row["mean_importance"]) if pd.notna(row.get("mean_importance")) else None,
            "mean_csat_present": float(row["mean_csat_present"]),
            "mean_csat_absent":  float(row["mean_csat_absent"]),
            "n_present":         int(row["n_present"]),
            "n_absent":          int(row["n_absent"]),
        })

    # Sort: must_be first, then attractive, then performance, then indifferent;
    # within each group sort by abs(penalty_index) or abs(reward_index) desc
    _order = {"must_be": 0, "attractive": 1, "performance": 2, "indifferent": 3}
    attributes.sort(
        key=lambda a: (
            _order.get(a["kano_category"], 9),
            -max(abs(a["reward_index"]), abs(a["penalty_index"])),
        )
    )

    return {
        "attributes":        attributes,
        "n_respondents":     n_respondents,
        "mean_csat_overall": mean_csat_overall,
        "category":          category,
        "method":            "Penalty-Reward Contrast Analysis (derived Kano)",
        "error":             None,
    }


def kano_summary_text(result: dict) -> str:
    """
    Return a concise, agent-readable text summary of a compute_kano() result.

    Includes counts per category, top-3 must-be (highest penalty), and
    top-3 attractive (highest reward). Suitable for LLM consumption.
    """
    if result.get("error"):
        return f"[Kano analysis failed] {result['error']}"

    attrs = result.get("attributes", [])
    if not attrs:
        return "[Kano analysis] No attributes classified."

    n_total          = result.get("n_respondents", 0)
    mean_csat        = result.get("mean_csat_overall", float("nan"))
    category_label   = result.get("category") or "all categories"
    method           = result.get("method", "")

    # Counts per Kano type
    from collections import Counter
    counts = Counter(a["kano_category"] for a in attrs)
    count_str = ", ".join(
        f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda x: _order_key(x[0]))
    )

    # Top-3 must-be (highest absolute penalty)
    must_be_attrs = sorted(
        [a for a in attrs if a["kano_category"] == "must_be"],
        key=lambda a: -abs(a["penalty_index"]),
    )[:3]
    must_be_str = (
        "; ".join(f"{a['attr_label']} (penalty={a['penalty_index']:+.2f})" for a in must_be_attrs)
        or "none"
    )

    # Top-3 attractive (highest absolute reward)
    attractive_attrs = sorted(
        [a for a in attrs if a["kano_category"] == "attractive"],
        key=lambda a: -abs(a["reward_index"]),
    )[:3]
    attractive_str = (
        "; ".join(f"{a['attr_label']} (reward={a['reward_index']:+.2f})" for a in attractive_attrs)
        or "none"
    )

    lines = [
        f"Kano PRCA ({method}) — {category_label}",
        f"CSAT respondents: {n_total} | Mean CSAT: {mean_csat:.2f}/10",
        f"Attributes classified: {len(attrs)} | {count_str}",
        f"Top must-be (basics): {must_be_str}",
        f"Top attractive (delighters): {attractive_str}",
    ]
    return "\n".join(lines)


def _order_key(cat: str) -> int:
    return {"must_be": 0, "attractive": 1, "performance": 2, "indifferent": 3}.get(cat, 9)


# ─────────────────────────────────────────────────────────────────────────────
# CLI verification  (py -3.12 kano_engine.py)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import time
    from collections import Counter

    print("Running compute_kano() verification...")
    t0 = time.perf_counter()
    result = compute_kano()
    elapsed = time.perf_counter() - t0

    if result["error"]:
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    attrs = result["attributes"]
    counts = Counter(a["kano_category"] for a in attrs)

    print(f"\n=== Kano PRCA Verification ===")
    print(f"  n_respondents    : {result['n_respondents']}")
    print(f"  mean_csat_overall: {result['mean_csat_overall']:.4f}")
    print(f"  attrs classified : {len(attrs)}")
    print(f"  elapsed          : {elapsed:.2f}s")
    print(f"\n  Counts per kano_category:")
    for cat in ["must_be", "attractive", "performance", "indifferent"]:
        print(f"    {cat:12s}: {counts.get(cat, 0)}")

    print(f"\n  Top 3 must-be (highest |penalty|):")
    for a in sorted([x for x in attrs if x["kano_category"] == "must_be"],
                    key=lambda x: -abs(x["penalty_index"]))[:3]:
        print(f"    {a['attr_label']:40s}  penalty={a['penalty_index']:+.3f}  reward={a['reward_index']:+.3f}")

    print(f"\n  Top 3 attractive (highest |reward|):")
    for a in sorted([x for x in attrs if x["kano_category"] == "attractive"],
                    key=lambda x: -abs(x["reward_index"]))[:3]:
        print(f"    {a['attr_label']:40s}  reward={a['reward_index']:+.3f}  penalty={a['penalty_index']:+.3f}")

    print(f"\n  Top 3 performance (closest reward~=penalty):")
    for a in sorted([x for x in attrs if x["kano_category"] == "performance"],
                    key=lambda x: abs(x["reward_index"] - x["penalty_index"]))[:3]:
        print(f"    {a['attr_label']:40s}  reward={a['reward_index']:+.3f}  penalty={a['penalty_index']:+.3f}")

    print(f"\n  Sample attribute record:")
    if attrs:
        import json
        print(f"    {json.dumps(attrs[0], indent=4, default=str)}")

    # Category filter smoke test
    print(f"\n  Smoke test — ceiling fans filter:")
    r2 = compute_kano(category="ceiling fans")
    if r2["error"]:
        print(f"    ERROR: {r2['error']}")
    else:
        c2 = Counter(a["kano_category"] for a in r2["attributes"])
        print(f"    attrs: {len(r2['attributes'])} | {dict(c2)}")

    print(f"\n  kano_summary_text:")
    print(kano_summary_text(result))

    # Assertion checks
    assert len(attrs) >= 20, f"Expected ≥20 attrs, got {len(attrs)}"
    assert len(counts) >= 3, f"Expected ≥3 Kano categories, got {len(counts)}: {dict(counts)}"
    assert not any(
        np.isnan(a["reward_index"]) or np.isnan(a["penalty_index"])
        for a in attrs
    ), "NaN found in reward/penalty indices"
    assert elapsed < 15.0, f"Took {elapsed:.1f}s — exceeds 15s budget"

    print(f"\nAll assertions passed.")
