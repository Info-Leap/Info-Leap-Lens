"""
RegressionPipeline — config-driven data-prep layer for brand driver regression.

Implements the exact Excel pipeline from the Akshayakalpa regression workbooks:
  1. Awareness filter: only (respondent × brand) pairs where brand was asked about
  2. DV pull: NPS / CSAT / EVER_USED / any awareness stage
  3. IV pull: imagery attrs for those pairs only (no contamination from unseen brands)
  4. Auto-select regression type: binary DV → logistic_regression.R, continuous → driver_regression.R
  5. Output: same dict shape as existing callers expect + pipeline_config metadata

Usage
-----
    from lens.analytics.analysis_spec import AnalysisSpec
    from lens.analytics.regression_pipeline import RegressionPipeline

    spec = AnalysisSpec.nps_drivers(brands=["Bajaj", "Crompton"])
    pipe = RegressionPipeline(db_path, spec)
    result = pipe.run_regression()
    # result["regression_type"], result["pipeline_config"], result["significant_drivers"] etc.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from lens.analytics.analysis_spec import AnalysisSpec, RegressionConfig

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class RegressionPipeline:
    def __init__(self, db_path: str, spec: AnalysisSpec):
        self.db_path = str(db_path)
        self.spec = spec
        self._aware_pairs: Optional[pd.DataFrame] = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_aware_pairs(self, conn) -> pd.DataFrame:
        """Return valid (respondent_id, brand_id) pairs passing the awareness gate."""
        stages = self.spec.awareness_gate_stages
        if not stages:
            # No filter — return all respondent×brand combos from imagery (current behavior)
            return pd.read_sql(
                "SELECT DISTINCT respondent_id, brand_id FROM fact_brand_imagery", conn
            )
        ph = ",".join("?" * len(stages))
        params: list = list(stages)
        brand_filter = ""
        if self.spec.exclude_brand_ids:
            ep = ",".join("?" * len(self.spec.exclude_brand_ids))
            brand_filter = f"AND brand_id NOT IN ({ep})"
            params += list(self.spec.exclude_brand_ids)
        brand_id_filter = ""
        if self.spec.brand_ids:
            bp = ",".join("?" * len(self.spec.brand_ids))
            brand_id_filter = f"AND brand_id IN ({bp})"
            params += list(self.spec.brand_ids)
        sql = (
            f"SELECT DISTINCT respondent_id, brand_id FROM fact_brand_awareness "
            f"WHERE stage IN ({ph}) {brand_filter} {brand_id_filter}"
        )
        return pd.read_sql(sql, conn, params=params)

    def _get_dv(self, conn, aware_pairs: pd.DataFrame, cfg: RegressionConfig) -> pd.DataFrame:
        """Pull DV for aware pairs only."""
        if cfg.dv_source == "nps":
            dv = pd.read_sql(
                "SELECT respondent_id, brand_id, nps_score AS dv FROM fact_brand_nps", conn
            )
            dv = aware_pairs.merge(dv, on=["respondent_id", "brand_id"], how="inner")
        elif cfg.dv_source == "csat":
            dv = pd.read_sql(
                "SELECT ba.respondent_id, ba.brand_id, s.score AS dv "
                "FROM fact_satisfaction s "
                "JOIN fact_brand_awareness ba ON s.respondent_id=ba.respondent_id "
                "AND ba.stage='LAST_PURCHASED'",
                conn,
            )
            dv = aware_pairs.merge(dv, on=["respondent_id", "brand_id"], how="inner")
        elif cfg.dv_source in ("ever_tried", "awareness_stage"):
            stage = cfg.dv_stage or "EVER_USED"
            tried = pd.read_sql(
                "SELECT DISTINCT respondent_id, brand_id FROM fact_brand_awareness WHERE stage=?",
                conn,
                params=[stage],
            )
            tried["dv"] = 1
            # Aware but not tried → dv=0 (left join gives NaN → fill 0)
            dv = aware_pairs.merge(tried, on=["respondent_id", "brand_id"], how="left")
            dv["dv"] = dv["dv"].fillna(0).astype(int)
            return dv[["respondent_id", "brand_id", "dv"]]
        else:
            raise ValueError(f"Unknown dv_source: {cfg.dv_source!r}. Use: nps, csat, ever_tried, awareness_stage")

        if cfg.topbox_threshold is not None:
            dv["dv"] = (pd.to_numeric(dv["dv"], errors="coerce") >= cfg.topbox_threshold).astype(int)
        return dv[["respondent_id", "brand_id", "dv"]].dropna(subset=["dv"])

    def _get_ivs(self, conn, aware_pairs: pd.DataFrame, cfg: RegressionConfig) -> pd.DataFrame:
        """Pull IV imagery for aware pairs only, pivot wide."""
        attr_filter = ""
        params: list = []
        if cfg.attr_ids:
            ph = ",".join("?" * len(cfg.attr_ids))
            attr_filter = f"AND fi.attr_id IN ({ph})"
            params = list(cfg.attr_ids)
        iv = pd.read_sql(
            f"SELECT fi.respondent_id, fi.brand_id, a.attr_label, fi.value "
            f"FROM fact_brand_imagery fi "
            f"JOIN dim_bq3_attribute a ON fi.attr_id=a.attr_id "
            f"WHERE fi.value=1 {attr_filter}",
            conn,
            params=params,
        )
        # Apply awareness filter — only pairs in aware_pairs
        iv = aware_pairs.merge(iv, on=["respondent_id", "brand_id"], how="left")
        return iv.pivot_table(
            index=["respondent_id", "brand_id"],
            columns="attr_label",
            values="value",
            aggfunc="max",
            fill_value=0,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def build_regression_df(self) -> pd.DataFrame:
        """Full pipeline: awareness filter → DV → IV pivot → join. Ready for R."""
        cfg = self.spec.regression
        conn = sqlite3.connect(self.db_path)
        try:
            aware = self._get_aware_pairs(conn)
            self._aware_pairs = aware
            dv = self._get_dv(conn, aware, cfg)
            iv = self._get_ivs(conn, aware[["respondent_id", "brand_id"]], cfg)
        finally:
            conn.close()

        rdf = dv.set_index(["respondent_id", "brand_id"]).join(iv, how="inner")
        rdf = rdf.reset_index().rename(columns={"dv": "nps_score"})
        iv_cols = [c for c in rdf.columns if c not in ("respondent_id", "brand_id", "nps_score")]
        rdf[iv_cols] = rdf[iv_cols].fillna(0)
        return rdf

    def run_regression(self) -> dict:
        """Run full pipeline + regression. Returns same dict shape as existing callers."""
        from oxdata.skills.r_bridge import run_r_stat
        rdf = self.build_regression_df()
        rdf.columns = [str(c).replace(".", "_").replace(" ", "_") for c in rdf.columns]

        if len(rdf) < 20:
            return {
                "error": f"Insufficient data: {len(rdf)} rows after awareness filter + DV join (need ≥20).",
                "pipeline_config": self._pipeline_config(len(rdf)),
            }

        result = run_r_stat(self.spec.regression.regression_type, rdf)
        result["pipeline_config"] = self._pipeline_config(len(rdf))
        return result

    def _pipeline_config(self, n_pairs: int) -> dict:
        cfg = self.spec.regression
        return {
            "dv_source": cfg.dv_source,
            "dv_stage": cfg.dv_stage,
            "topbox_threshold": cfg.topbox_threshold,
            "awareness_gate": self.spec.awareness_gate_stages,
            "regression_type": cfg.regression_type,
            "n_pairs": n_pairs,
        }

    def run(self) -> dict:
        """Run regression if configured. Returns dict with 'regression' key."""
        result = {}
        if self.spec.regression:
            result["regression"] = self.run_regression()
        return result
