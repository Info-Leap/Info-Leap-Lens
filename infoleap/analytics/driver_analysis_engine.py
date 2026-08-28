"""
Brand Driver Analysis Engine
=============================
Runs BIP normalization + Correspondence Analysis on a user-selected subset
of brand attributes (drivers), optionally split by comparison direction
(Overall / By Zone / By Category).

Feeds structured output to an LLM (OpenRouter) to generate highlighted
insight narratives explaining the competitive implications.

Usage
-----
    from infoleap.analytics.driver_analysis_engine import DriverAnalysisEngine

    engine = DriverAnalysisEngine(category_codes=[1, 7])   # Ceiling Fans
    result = engine.run(
        driver_ids=[78, 79, 80, 85, 87],        # selected attribute IDs
        brands=["Bajaj", "Crompton", "Havells"],
        compare_by="overall",                    # or "zone" / "category"
        top_brands=8,
        percentile_threshold=65,
    )
    # result: {status, bip, ca, ai_insight, summary_table}
"""

from __future__ import annotations

import os
import sys
import json
import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from infoleap.analytics.bip_engine import BIPNormalizationEngine
from infoleap.analytics.can_map_engine import run_ca_pipeline, PRODUCT_CODES

_DEFAULT_DB = _ROOT / "oxdata" / "data" / "project_1" / "oxdata.db"


# â”€â”€ helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _resolve_db(db_path=None, project_id: Optional[str] = None) -> str:
    if db_path:
        return str(db_path)
    try:
        from infoleap.db_loader import get_db_path
        found = get_db_path(required_table="fact_brand_imagery", project_id=project_id)
        if found:
            return str(found)
    except Exception:
        pass
    return str(_DEFAULT_DB)


def _get_attr_labels(db_path: str, attr_ids: list[int]) -> dict[int, str]:
    """Return {attr_id: attr_label} for given IDs."""
    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(attr_ids))
    rows = conn.execute(
        f"SELECT attr_id, attr_label FROM dim_bq3_attribute WHERE attr_id IN ({placeholders})",
        attr_ids,
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def _get_all_attrs(db_path: str) -> pd.DataFrame:
    """All attributes with id, label, broad_feature."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT attr_id, attr_label, broad_feature FROM dim_bq3_attribute ORDER BY attr_id",
        conn,
    )
    conn.close()
    return df


# â”€â”€ AI narrative â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

INSIGHT_SYSTEM_PROMPT = """You are a senior market research analyst specialising in brand equity and consumer perception.
Interpret the Brand Driver Analysis output and generate concise, actionable insights.
Respond in structured markdown with these exact sections:
## Key Findings
## Brand Ownership (who owns what)
## Strategic Gaps (white-space opportunities no brand owns)
## Competitive Risks
## Recommended Actions
Be specific: name brands, name drivers, give numbers. No vague generalisations."""

INSIGHT_USER_TEMPLATE = """Brand Driver Analysis Results â€” {product} | Drivers: {n_drivers} | Method: {compare_by}

### Drivers selected
{driver_list}

### BIP Significance (YES = brand significantly over-associated with driver)
{bip_table}

### Perceptual Map Summary
F1 explains {f1_pct:.1f}% variance, F2 {f2_pct:.1f}%.
Brand positions on F1/F2 (principal coordinates):
{brand_positions}

### Raw association % (top brands Ã— selected drivers)
{raw_table}

### Column averages (market norm per driver)
{col_avgs}

Please analyse these results and generate the structured insight report."""


def _build_ai_context(
    bip_result: dict,
    ca_result: dict,
    product: str,
    compare_by: str,
    driver_labels: list[str],
) -> str:
    """Construct the user message for the LLM."""
    t14 = bip_result["tables"].get("table14_significance", pd.DataFrame())
    t1  = bip_result["tables"].get("table1_raw", pd.DataFrame())
    col_avgs = bip_result["tables"].get("column_averages", pd.Series(dtype=float))
    n_drivers = len(driver_labels)

    # BIP YES/NO table as markdown
    bip_md = t14.to_markdown() if not t14.empty else "No data"

    # CA positions
    ca_brands = []
    if ca_result and ca_result.get("status") == "ok":
        map_data = ca_result.get("map_data", {})
        for b in map_data.get("brands", []):
            ca_brands.append(f"  {b['name']}: F1={b.get('F1',0):.4f}, F2={b.get('F2',0):.4f}")
        eig = ca_result["ca_results"]["eigenvalues"]
        f1_pct = float(eig["Inertia_%"].iloc[0]) if len(eig) > 0 else 0
        f2_pct = float(eig["Inertia_%"].iloc[1]) if len(eig) > 1 else 0
    else:
        f1_pct, f2_pct = 0, 0

    raw_md   = t1.round(1).to_markdown() if not t1.empty else "No data"
    avgs_md  = col_avgs.round(1).to_markdown() if not col_avgs.empty else "No data"

    return INSIGHT_USER_TEMPLATE.format(
        product=product,
        n_drivers=n_drivers,
        compare_by=compare_by,
        driver_list="\n".join(f"- {d}" for d in driver_labels),
        bip_table=bip_md,
        f1_pct=f1_pct,
        f2_pct=f2_pct,
        brand_positions="\n".join(ca_brands) or "Insufficient data",
        raw_table=raw_md,
        col_avgs=avgs_md,
    )


def _call_llm(user_message: str) -> str:
    """Call OpenRouter LLM for insight narrative."""
    try:
        from dotenv import load_dotenv
        load_dotenv(str(_ROOT / "oxdata" / ".env"))
    except Exception:
        pass

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    model   = os.environ.get("OPENROUTER_MODEL_PRO", "meta-llama/llama-3.3-70b-instruct")
    if not api_key:
        return "_AI insight unavailable: OPENROUTER_API_KEY not set._"

    try:
        import httpx
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://infoleap.pulse",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": INSIGHT_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
                "max_tokens": 1200,
                "temperature": 0.3,
            },
            timeout=45.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"_AI insight error: {e}_"


# â”€â”€ comparison split helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

ZONE_CODES = {
    "North": [1, 2, 3, 4],       # Delhi, Lucknow, Bikaner, Patiala
    "South": [5, 6, 7, 8, 17, 18],
    "West":  [9, 10, 11, 12],
    "East":  [13, 14, 15, 16],
}


def _run_bip_for_attrs(
    attr_ids: list[int],
    category_codes: list[int],
    brands: Optional[list[str]],
    percentile_threshold: float,
    top_brands: int,
    db_path: str,
    zone: Optional[str] = None,
    gender: Optional[str] = None,
    age_band: Optional[str] = None,
    city: Optional[str] = None,
) -> dict:
    """Run BIP for a specific set of attr_ids."""
    eng = BIPNormalizationEngine(
        db_path=db_path,
        category_codes=category_codes,
        percentile_threshold=percentile_threshold,
        top_brands=top_brands,
        zone=zone,
        gender=gender,
        age_band=age_band,
        city=city,
    )
    matrix = eng.get_brand_attr_matrix(attr_ids=attr_ids)
    if not matrix.empty and brands:
        keep = [b for b in brands if b in matrix.index]
        if keep:
            matrix = matrix.loc[keep]
    if matrix.empty:
        return {"status": "no_data"}
    tables = eng.compute_normalization(matrix)
    sig = eng.get_significance_summary()
    charts = eng.get_chart_specs()
    return {"status": "ok", "tables": tables, "significance_summary": sig,
            "chart_specs": charts, "matrix": matrix}


# â”€â”€ main engine â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class DriverAnalysisEngine:
    """
    Brand Driver Analysis: run BIP + CA on a selected set of attributes,
    optionally split by comparison direction.
    """

    def __init__(
        self,
        db_path=None,
        category_codes: list[int] = None,
        zone: Optional[str] = None,
        gender: Optional[str] = None,
        age_band: Optional[str] = None,
        city: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        self.db_path        = _resolve_db(db_path, project_id=project_id)
        self.project_id     = project_id
        self.category_codes = category_codes or PRODUCT_CODES["All"]
        self.zone           = zone
        self.gender         = gender
        self.age_band       = age_band
        self.city           = city

    def get_all_attributes(self) -> pd.DataFrame:
        """Return all 93 attributes with id, label, broad_feature."""
        return _get_all_attrs(self.db_path)

    def _compute_nps_impacts(self, driver_ids: list[int], category_codes: list[int], brands: list[str], pooled: bool = False, dv_stage: str = "NPS") -> tuple[pd.Series, dict]:
        """
        Derive driver importance by regressing NPS scores against brand-attribute associations.

        Returns
        -------
        (coef_series, stats)
            coef_series : pd.Series of std_coef indexed by attribute label (unchanged shape,
                          for backward compat with existing callers/charts).
            stats       : dict with:
                "fit"     -> model fit stats (r_squared, adj_r_squared, f_statistic, f_p_value,
                             aic, bic, n, n_attrs, n_before_na_omit, pct_dropped_na, shapiro_wilk, vif)
                "drivers" -> {attr_label: {p_value, significant, std_error, t_stat, ci_low, ci_high}}
            stats is {} when the R path wasn't used (no data / R unavailable / Python fallback).
        """
        try:
            from infoleap.skills.r_bridge import run_r_stat as _run_r_stat, r_available as _r_available
            _use_r = _r_available()
        except Exception:
            _use_r = False
        # Keep BayesianRidge as Python-only fallback when R unavailable
        if not _use_r:
            from sklearn.linear_model import BayesianRidge
        
        conn = sqlite3.connect(self.db_path)

        # 2026-07-30: see can_map_engine.py's get_brand_attr_matrix for the full rationale â€”
        # projects onboarded via the generic ingestion pipeline never populate category_code
        # (most brand-health clients survey ONE category), so an unconditional category filter
        # would zero out every row for them even though real imagery data exists.
        _has_category_dim = conn.execute(
            "SELECT 1 FROM fact_brand_imagery WHERE category_code IS NOT NULL LIMIT 1"
        ).fetchone()
        effective_category_codes = category_codes if _has_category_dim else []

        # 1. Get associations (long format â€” respondent Ã— brand rows)
        attr_placeholders = ",".join("?" * len(driver_ids))

        # Demographic filters for regression data
        demo_where = []
        if self.zone: demo_where.append("vr.zone_name = ?")
        if self.gender: demo_where.append("vr.gender = ?")
        if self.age_band: demo_where.append("vr.age_band = ?")
        if self.city: demo_where.append("vr.city_name = ?")
        demo_sql = (" AND " + " AND ".join(demo_where)) if demo_where else ""
        demo_vals = []
        if self.zone: demo_vals.append(self.zone)
        if self.gender: demo_vals.append(self.gender)
        if self.age_band: demo_vals.append(self.age_band)
        if self.city: demo_vals.append(self.city)

        cat_sql = ""
        if effective_category_codes:
            cat_placeholders = ",".join("?" * len(effective_category_codes))
            cat_sql = f"AND fi.category_code IN ({cat_placeholders})"

        if pooled:
            # Pooled: base = all AIDED-aware respondentÃ—brand pairs (XLSTAT approach).
            # CROSS JOIN selected attrs so every aware pair gets a row per attr.
            # LEFT JOIN imagery: non-associated attrs â†’ 0 (not missing).
            # This matches XLSTAT's n exactly â€” every aware pair included even if all IVs=0.
            params = driver_ids + demo_vals
            query = f"""
                SELECT aw.respondent_id, b.brand_name, a.attr_label,
                       COALESCE(fi.value, 0) AS value
                FROM fact_brand_awareness aw
                JOIN dim_brand b ON b.brand_id = aw.brand_id
                CROSS JOIN (
                    SELECT attr_id, attr_label FROM dim_bq3_attribute
                    WHERE attr_id IN ({attr_placeholders})
                ) a
                LEFT JOIN fact_brand_imagery fi
                  ON fi.respondent_id = aw.respondent_id
                  AND fi.brand_id = aw.brand_id
                  AND fi.attr_id = a.attr_id
                JOIN v_respondents vr ON vr.respondent_id = aw.respondent_id
                WHERE aw.stage = 'AIDED'
                  {demo_sql}
            """
        else:
            brand_placeholders = ",".join("?" * len(brands))
            params = driver_ids + effective_category_codes + brands + demo_vals
            brand_filter_sql = f"AND b.brand_name IN ({brand_placeholders})"
            query = f"""
                SELECT fi.respondent_id, b.brand_name, a.attr_label, fi.value
                FROM fact_brand_imagery fi
                JOIN dim_bq3_attribute a ON fi.attr_id = a.attr_id
                JOIN dim_brand b ON fi.brand_id = b.brand_id
                JOIN v_respondents vr ON vr.respondent_id = fi.respondent_id
                WHERE fi.attr_id IN ({attr_placeholders})
                  {cat_sql}
                  {brand_filter_sql}
                  {demo_sql}
                  AND EXISTS (
                      SELECT 1 FROM fact_brand_awareness aw
                      WHERE aw.respondent_id = fi.respondent_id
                        AND aw.brand_id = fi.brand_id
                        AND aw.stage IN ('TOM','SPONT','AIDED','EVER_USED','CONSIDERATION','CURRENT_USER')
                  )
            """
        df_assoc = pd.read_sql(query, conn, params=params)

        # 2. Get DV (EVER_USED binary or NPS score depending on dv_stage)
        nps_demo_clauses = [w.replace("fi.", "vr.") for w in demo_where]
        _FUNNEL_STAGES = {"TOM", "SPONT", "AIDED", "CONSIDERATION",
                          "EVER_USED", "CURRENT_USER", "PREFERRED"}
        use_funnel_dv = dv_stage in _FUNNEL_STAGES

        if pooled and use_funnel_dv:
            # DV = 1 if respondent hit funnel stage for that brand, 0 otherwise.
            # Base for 0s comes from df_assoc (all AIDED pairs) â€” merge fills missing = 0.
            if nps_demo_clauses:
                dv_where = "WHERE fa.stage = ? AND " + " AND ".join(nps_demo_clauses)
            else:
                dv_where = "WHERE fa.stage = ?"
            dv_query = f"""
                SELECT fa.respondent_id, b.brand_name, 1 AS nps_score
                FROM fact_brand_awareness fa
                JOIN dim_brand b ON b.brand_id = fa.brand_id
                JOIN v_respondents vr ON vr.respondent_id = fa.respondent_id
                {dv_where}
            """
            nps_params = [dv_stage] + demo_vals
            df_nps = pd.read_sql(dv_query, conn, params=nps_params)
        else:
            if pooled:
                if nps_demo_clauses:
                    nps_where = "WHERE " + " AND ".join(nps_demo_clauses)
                else:
                    nps_where = ""
                nps_params = demo_vals[:]
            else:
                brand_placeholders2 = ",".join("?" * len(brands))
                clauses = [f"n.brand_name IN ({brand_placeholders2})"] + nps_demo_clauses
                nps_where = "WHERE " + " AND ".join(clauses)
                nps_params = list(brands) + demo_vals
            nps_query = f"""
                SELECT n.respondent_id, n.brand_name, n.nps_score
                FROM v_brand_nps n
                JOIN v_respondents vr ON n.respondent_id = vr.respondent_id
                {nps_where}
            """
            df_nps = pd.read_sql(nps_query, conn, params=nps_params)
        conn.close()

        # â”€â”€ Data layer override: rebuild df_assoc + df_nps from raw Excel when available â”€â”€
        # SQLite fact_brand_imagery may be sparse/buggy for raw-layer projects; raw data is authoritative.
        if self.project_id:
            try:
                from infoleap.data_layer import project_has_raw_layer, get_project_layer
                if project_has_raw_layer(self.project_id):
                    _layer = get_project_layer(self.project_id)
                    if _layer is not None:
                        # Resolve driver_ids â†’ attr_labels from SQLite dim_bq3_attribute
                        _c = sqlite3.connect(self.db_path)
                        _id_to_label = dict(_c.execute(
                            "SELECT attr_id, attr_label FROM dim_bq3_attribute"
                        ).fetchall())
                        _c.close()
                        _target_labels = {_id_to_label[i] for i in driver_ids if i in _id_to_label}

                        _img = _layer.imagery.copy()
                        _demo = _layer.demographics
                        _df = _demo.copy()
                        if self.zone     and self.zone     != "all": _df = _df[_df["zone"]     == self.zone]
                        if self.gender   and self.gender   != "all": _df = _df[_df["gender"]   == self.gender]
                        if self.age_band and self.age_band != "all": _df = _df[_df["age_band"] == self.age_band]
                        if self.city     and self.city     != "all": _df = _df[_df["city"]     == self.city]
                        _img = _img[_img["respondent_id"].isin(_df["respondent_id"])]
                        if _target_labels:
                            _img = _img[_img["attr_label"].isin(_target_labels)]
                        if brands and not pooled:
                            _img = _img[_img["brand_name"].isin(brands)]
                        _img = _img.copy()
                        _img["value"] = 1

                        _nps = _layer.nps.copy()
                        _nps = _nps.rename(columns={"score": "nps_score"})
                        _nps = _nps[_nps["respondent_id"].isin(_df["respondent_id"])]
                        if brands and not pooled:
                            _nps = _nps[_nps["brand_name"].isin(brands)]

                        if not _img.empty and not _nps.empty:
                            df_assoc = _img[["respondent_id", "brand_name", "attr_label", "value"]]
                            df_nps   = _nps[["respondent_id", "brand_name", "nps_score"]]
            except Exception:
                pass
        # â”€â”€ End data layer override â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

        if df_assoc.empty or df_nps.empty:
            return pd.Series(dtype=float), {}

        # 3. Pivot associations to wide
        df_wide = df_assoc.pivot_table(index=['respondent_id', 'brand_name'], columns='attr_label', values='value', aggfunc='max', fill_value=0)

        # 4. Join with DV; for funnel DV fill missing pairs with 0 (didn't hit stage)
        df_dv = df_nps.set_index(['respondent_id', 'brand_name'])
        if pooled and use_funnel_dv:
            df_final = df_wide.join(df_dv, how='left')
            df_final['nps_score'] = df_final['nps_score'].fillna(0).astype(int)
        else:
            df_final = df_dv.join(df_wide, how='inner').dropna(subset=['nps_score'])

        # Minimum sample for regression: 20
        if len(df_final) < 20:
            return pd.Series(0.0, index=df_wide.columns), {}

        # 5. Regression â€” use R OLS bridge (XLSTAT-correct std-beta importance)
        X = df_final[df_wide.columns].fillna(0)
        y_series = df_final['nps_score']
        if _use_r:
            try:
                # Imagery attrs are binary association flags â€” a respondent not asked about
                # (or not endorsing) an attribute is "not associated" = 0, not missing data.
                # R's na.omit() would otherwise listwise-delete any row with an unfilled cell,
                # collapsing n and producing a degenerate model (see .regression_reference/AUDIT.md Â§3).
                rdf = df_final[list(df_wide.columns) + ['nps_score']].reset_index(drop=True)
                rdf[list(df_wide.columns)] = rdf[list(df_wide.columns)].fillna(0)
                rdf.columns = [c.replace(".", "_").replace(" ", "_") for c in rdf.columns]
                # Auto-detect binary DV â†’ logistic regression (fixes GAP 4: was always OLS)
                _dv_vals = rdf["nps_score"].dropna()
                _dv_is_binary = _dv_vals.isin([0, 1]).all() and _dv_vals.nunique() <= 2
                _r_script = "logistic_regression" if _dv_is_binary else "driver_regression"
                res = _run_r_stat(_r_script, rdf)
                if "error" not in res and res.get("significant_drivers"):
                    coef_map = {d["attribute"].replace(".", "_").replace(" ", "_"): d.get("std_coef", 0)
                                for d in res["significant_drivers"]}
                    mapped = {orig: coef_map.get(orig.replace(".", "_").replace(" ", "_"), 0.0)
                              for orig in df_wide.columns}
                    driver_detail = {
                        d["attribute"].replace(".", "_").replace(" ", "_"): {
                            "p_value":    d.get("p_value"),
                            "significant": d.get("significant"),
                            "std_error":  d.get("std_error"),
                            "t_stat":     d.get("t_stat"),
                            "ci_low":     d.get("ci_low"),
                            "ci_high":    d.get("ci_high"),
                        }
                        for d in res["significant_drivers"]
                    }
                    drivers_by_label = {
                        orig: driver_detail.get(orig.replace(".", "_").replace(" ", "_"), {})
                        for orig in df_wide.columns
                    }
                    fit_stats = {k: v for k, v in res.items() if k != "significant_drivers"}
                    return pd.Series(mapped), {"fit": fit_stats, "drivers": drivers_by_label}
            except Exception:
                pass
        # Python fallback (BayesianRidge) when R unavailable
        try:
            from sklearn.linear_model import BayesianRidge
            model = BayesianRidge().fit(X, y_series)
            return pd.Series(model.coef_, index=df_wide.columns), {}
        except Exception:
            return pd.Series(0.0, index=df_wide.columns), {}

    def run(
        self,
        driver_ids:           list[int],
        brands:               Optional[list[str]] = None,
        compare_by:           str = "overall",    # "overall"|"zone"|"category"
        top_brands:           int = 10,
        percentile_threshold: float = 65,
        product_label:        str = "All",
        generate_ai_insight:  bool = True,
        pooled:               bool = False,       # True = all brands stacked (XLSTAT category model)
    ) -> dict:
        """
        Run full driver analysis.

        Parameters
        ----------
        driver_ids          : list of attribute IDs selected by user
        brands              : optional brand name filter
        compare_by          : "overall" | "zone" | "category"
        top_brands          : max brands if no explicit brand filter
        percentile_threshold: BIP significance gate (default 65)
        product_label       : human-readable product label (for AI context)
        generate_ai_insight : whether to call the LLM

        Returns
        -------
        dict with keys:
            status          : "ok" | "no_data" | "error"
            bip_overall     : BIP result dict
            ca_overall      : CA result dict
            bip_by_split    : dict[split_label -> BIP result] (only for zone/category)
            driver_labels   : list[str] â€” attribute labels for selected IDs
            ai_insight      : str markdown â€” LLM narrative
            summary_table   : pd.DataFrame â€” per-brand YES count + top drivers
            nps_impacts     : pd.Series â€” derived importance scores
            nps_impact_stats: dict â€” model fit stats for the nps_impacts regression
                              (r_squared, adj_r_squared, f_statistic, f_p_value, aic, bic,
                              n, n_attrs, n_before_na_omit, pct_dropped_na, shapiro_wilk, vif).
                              {} when the R path wasn't used.
            nps_impact_drivers: dict[attr_label -> {p_value, significant, std_error, t_stat,
                              ci_low, ci_high}] â€” per-driver detail for nps_impacts. {} when
                              the R path wasn't used.
        """
        if not driver_ids:
            return {"status": "no_data", "message": "No drivers selected."}

        # Resolve attr labels
        label_map = _get_attr_labels(self.db_path, driver_ids)
        driver_labels = [label_map.get(i, f"Attr {i}") for i in driver_ids]

        # â”€â”€ Overall BIP â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        bip_overall = _run_bip_for_attrs(
            attr_ids=driver_ids,
            category_codes=self.category_codes,
            brands=brands,
            percentile_threshold=percentile_threshold,
            top_brands=top_brands,
            db_path=self.db_path,
            zone=self.zone,
            gender=self.gender,
            age_band=self.age_band,
            city=self.city,
        )

        if bip_overall.get("status") == "no_data":
            return {"status": "no_data", "message": "No data for selected filters."}

        # Actual brands included in analysis
        final_brands = list(bip_overall["matrix"].index) if "matrix" in bip_overall else (brands or [])

        # â”€â”€ Overall CA â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        brand_ids = None
        if final_brands:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute("SELECT brand_id, brand_name FROM dim_brand").fetchall()
            conn.close()
            name_to_id = {r[1]: r[0] for r in rows}
            brand_ids = [name_to_id[b] for b in final_brands if b in name_to_id]

        ca_overall = run_ca_pipeline(
            category_codes=self.category_codes,
            attr_ids=driver_ids,
            brand_ids=brand_ids,
            top_brands=top_brands,
            top_attrs=len(driver_ids),
            zone=self.zone,
            gender=self.gender,
            age_band=self.age_band,
            city=self.city,
        )

        # â”€â”€ NPS Impact Analysis â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        _dv_stage = "EVER_USED" if pooled else "NPS"
        nps_impacts, nps_impact_meta = self._compute_nps_impacts(driver_ids, self.category_codes, final_brands, pooled=pooled, dv_stage=_dv_stage)
        nps_impact_stats   = nps_impact_meta.get("fit", {})
        nps_impact_drivers = nps_impact_meta.get("drivers", {})

        # â”€â”€ Split comparison â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        bip_by_split: dict = {}
        if compare_by == "zone":
            for zone_name in ZONE_CODES.keys():
                bip_by_split[zone_name] = _run_bip_for_attrs(
                    attr_ids=driver_ids,
                    category_codes=self.category_codes,
                    brands=final_brands,
                    percentile_threshold=percentile_threshold,
                    top_brands=top_brands,
                    db_path=self.db_path,
                    zone=zone_name, # Overrides self.zone for split view
                    gender=self.gender,
                    age_band=self.age_band,
                    city=self.city,
                )
        elif compare_by == "category":
            for cat_name, cat_codes in PRODUCT_CODES.items():
                if cat_name == "All":
                    continue
                r = _run_bip_for_attrs(
                    attr_ids=driver_ids,
                    category_codes=cat_codes,
                    brands=final_brands,
                    percentile_threshold=percentile_threshold,
                    top_brands=top_brands,
                    db_path=self.db_path,
                    zone=self.zone,
                    gender=self.gender,
                    age_band=self.age_band,
                    city=self.city,
                )
                if r.get("status") == "ok":
                    bip_by_split[cat_name] = r

        # â”€â”€ Summary table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        summary_table = pd.DataFrame()
        if bip_overall.get("status") == "ok":
            t14 = bip_overall["tables"].get("table14_significance", pd.DataFrame())
            if not t14.empty:
                rows = []
                for brand in t14.index:
                    yes_attrs = [a for a in t14.columns if t14.loc[brand, a] == "YES"]
                    t1 = bip_overall["tables"].get("table1_raw", pd.DataFrame())
                    mean_pct = float(t1.loc[brand].mean()) if not t1.empty and brand in t1.index else 0
                    rows.append({
                        "Brand": brand,
                        "YES count": len(yes_attrs),
                        "% of drivers owned": round(len(yes_attrs) / len(driver_ids) * 100, 1),
                        "Mean assoc %": round(mean_pct, 1),
                        "Top drivers owned": ", ".join(yes_attrs[:3]) + ("..." if len(yes_attrs) > 3 else ""),
                    })
                summary_table = pd.DataFrame(rows).sort_values("YES count", ascending=False)

        # â”€â”€ AI insight â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        ai_insight = ""
        if generate_ai_insight and bip_overall.get("status") == "ok":
            user_msg = _build_ai_context(
                bip_result=bip_overall,
                ca_result=ca_overall,
                product=product_label,
                compare_by=compare_by,
                driver_labels=driver_labels,
            )
            ai_insight = _call_llm(user_msg)

        return {
            "status":        "ok",
            "bip_overall":   bip_overall,
            "ca_overall":    ca_overall,
            "bip_by_split":  bip_by_split,
            "driver_labels": driver_labels,
            "driver_ids":    driver_ids,
            "ai_insight":    ai_insight,
            "summary_table": summary_table,
            "compare_by":    compare_by,
            "product_label": product_label,
            "nps_impacts":   nps_impacts,
            "nps_impact_stats":   nps_impact_stats,
            "nps_impact_drivers": nps_impact_drivers,
        }

    def cross_category_driver_reach(
        self,
        attr_ids: list[int],
        percentile_threshold: float = 65,
        top_brands: int = 10,
    ) -> dict:
        """
        For each product category, run BIP on attr_ids and collect market-norm %
        and per-driver brand ownership counts.

        Returns
        -------
        {
          "reach_df":     DataFrame(rows=category_name, cols=driver_label, values=market_norm_pct),
          "ownership_df": DataFrame(rows=category_name, cols=driver_label, values=yes_brand_count),
          "driver_labels": list[str],
        }
        """
        label_map = _get_attr_labels(self.db_path, attr_ids)
        driver_labels = [label_map.get(i, f"Attr {i}") for i in attr_ids]

        reach_rows: dict[str, dict] = {}
        ownership_rows: dict[str, dict] = {}

        for cat_name, cat_codes in PRODUCT_CODES.items():
            if cat_name == "All":
                continue
            result = _run_bip_for_attrs(
                attr_ids=attr_ids,
                category_codes=cat_codes,
                brands=None,
                percentile_threshold=percentile_threshold,
                top_brands=top_brands,
                db_path=self.db_path,
            )
            if result.get("status") != "ok":
                reach_rows[cat_name] = {lbl: 0.0 for lbl in driver_labels}
                ownership_rows[cat_name] = {lbl: 0 for lbl in driver_labels}
                continue

            tables = result["tables"]
            col_avgs: pd.Series = tables.get("column_averages", pd.Series(dtype=float))
            t14: pd.DataFrame   = tables.get("table14_significance", pd.DataFrame())

            reach_row: dict[str, float] = {}
            own_row: dict[str, int] = {}
            for attr_id, lbl in zip(attr_ids, driver_labels):
                reach_row[lbl] = float(col_avgs.get(lbl, col_avgs.get(attr_id, 0.0)))
                if not t14.empty and lbl in t14.columns:
                    own_row[lbl] = int((t14[lbl] == "YES").sum())
                else:
                    own_row[lbl] = 0

            reach_rows[cat_name] = reach_row
            ownership_rows[cat_name] = own_row

        reach_df     = pd.DataFrame(reach_rows).T.fillna(0.0)
        ownership_df = pd.DataFrame(ownership_rows).T.fillna(0).astype(int)

        for lbl in driver_labels:
            if lbl not in reach_df.columns:
                reach_df[lbl]     = 0.0
                ownership_df[lbl] = 0
        reach_df     = reach_df[driver_labels]
        ownership_df = ownership_df[driver_labels]

        return {
            "reach_df":      reach_df,
            "ownership_df":  ownership_df,
            "driver_labels": driver_labels,
        }


# â”€â”€ CLI test â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

if __name__ == "__main__":
    print("Testing DriverAnalysisEngine...")
    eng = DriverAnalysisEngine(category_codes=PRODUCT_CODES["Ceiling Fans"])

    # Get attribute listing
    attrs = eng.get_all_attributes()
    print(f"Attributes available: {len(attrs)}")
    brand_price_ids = attrs[attrs["broad_feature"] == "Brand and Price"]["attr_id"].tolist()[:8]
    print(f"Using Brand & Price drivers: {brand_price_ids}")

    result = eng.run(
        driver_ids=brand_price_ids,
        top_brands=8,
        compare_by="overall",
        generate_ai_insight=False,  # skip LLM in test
        product_label="Ceiling Fans",
    )

    print(f"Status: {result['status']}")
    if result["status"] == "ok":
        print(f"Drivers: {result['driver_labels']}")
        print(f"Summary table:\n{result['summary_table'].to_string(index=False)}")
        bip = result["bip_overall"]
        print(f"BIP tables: {list(bip['tables'].keys())}")
        print(f"Charts: {len(bip.get('chart_specs', []))}")
        ca = result["ca_overall"]
        print(f"CA status: {ca['status']}")
        if ca["status"] == "ok":
            eig = ca["ca_results"]["eigenvalues"]
            print(f"F1={eig['Inertia_%'].iloc[0]:.1f}% F2={eig['Inertia_%'].iloc[1]:.1f}%")
