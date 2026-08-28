"""
BIPNormalizationEngine — Brand Image Profiling Normalization Engine
====================================================================
Replicates the Normalisation.xlsx (sheet "65%") methodology used for
2-wheeler BIP studies, adapted for electrical appliance brands.

Data sources:
  fact_brand_imagery  — respondent × attribute × brand association rows
  dim_bq3_attribute   — attribute metadata (label, broad_feature, applies_* cols)
  dim_brand           — brand id / name
  v_brand_imagery     — view joining all of the above + demographics

14-table pipeline mirrors the Excel model exactly:
  TABLE 1   raw % association score (brand × attribute)
  TABLE 2   absolute deviation from column (attribute) mean
  TABLE 3   % normalized deviation  (diff / mean × 100)
  TABLE 4   threshold-filtered normalized deviation (zeroed if not significant)
  TABLE 5   positive-only filtered
  TABLE 6   negative-only filtered
  TABLE 7   rank table (all deviations)
  TABLE 8   rank table (positive deviations)
  TABLE 9   rank table (negative deviations)
  TABLE 13  combined rank summary
  TABLE 14  YES / NO significance flags

Percentile thresholds (calibrated from Excel row 36-38):
  p75 ≈ 15.48   p65 ≈ 12.40 (default gate)   p25 ≈ 5.98
These are recomputed from live data; the defaults above are fallback references.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# ── path bootstrap ─────────────────────────────────────────────────────────────
_LENS_DIR   = Path(__file__).resolve().parent.parent          # lens/
_PROJ_ROOT  = _LENS_DIR.parent                                # project root
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

try:
    from infoleap.db_loader import get_db_path as _get_db_path
    _HAS_DB_LOADER = True
except ImportError:
    _HAS_DB_LOADER = False

# Canonical DB location (fallback if db_loader unavailable)
_DEFAULT_DB = _PROJ_ROOT / "infoleap" / "data" / "project_1" / "oxdata.db"


# ── helpers ────────────────────────────────────────────────────────────────────

def _resolve_db(db_path: Optional[str | Path], project_id: Optional[str] = None) -> str:
    """Return a usable string path to the SQLite database.

    project_id, when supplied, is forwarded to get_db_path() so the engine
    reads the correct project's oxdata.db when called from a multi-project
    Streamlit session (2026-08-04: H3 multi-project fix).
    """
    if db_path is not None:
        return str(db_path)
    if _HAS_DB_LOADER:
        found = _get_db_path(required_table="fact_brand_imagery", project_id=project_id)
        if found:
            return str(found)
        # fallback: any DB for this project
        found = _get_db_path(required_table="fact_respondents", project_id=project_id)
        if found:
            return str(found)
    return str(_DEFAULT_DB)


def _rank_series(s: pd.Series, non_sig_rank: int) -> pd.Series:
    """
    Rank non-zero values descending (1 = best / largest absolute).
    Zero / NaN entries get non_sig_rank.
    """
    mask = s != 0
    ranked = s.copy().astype(float)
    ranked[~mask] = np.nan
    ranked[mask] = ranked[mask].rank(method="min", ascending=False)
    ranked = ranked.fillna(non_sig_rank)
    return ranked.astype(int)


# ── main engine ───────────────────────────────────────────────────────────────

class BIPNormalizationEngine:
    """
    Brand Image Profiling Normalization Engine.

    Parameters
    ----------
    db_path : str | Path | None
        Path to oxdata.db.  If None, resolved via db_loader / defaults.
    category_codes : list[int] | None
        Filter to specific category codes (1–12 in OX schema).
        None = all categories.
    percentile_threshold : float
        Primary significance gate (default 65 → p65 of all normalized deviations).
    """

    # Mapping broad_feature code → display label for radar / bar sections
    SECTION_LABELS: dict[str, str] = {
        "product":  "Product",
        "design":   "Design",
        "service":  "Service",
        "features": "Features",
        "brand":    "Brand",
    }

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        category_codes: Optional[list[int]] = None,
        percentile_threshold: float = 65,
        top_brands: int = 12,
        zone: Optional[str] = None,
        gender: Optional[str] = None,
        age_band: Optional[str] = None,
        city: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        self.db_path              = _resolve_db(db_path, project_id=project_id)
        self.project_id           = project_id

        self.category_codes       = category_codes
        self.percentile_threshold = float(percentile_threshold)
        self.top_brands           = top_brands
        self.zone     = None if (not zone     or zone     == "all") else zone
        self.gender   = None if (not gender   or gender   == "all") else gender
        self.age_band = None if (not age_band or age_band == "all") else age_band
        self.city     = None if (not city     or city     == "all") else city

        # lazy cache
        self._matrix:   Optional[pd.DataFrame] = None
        self._tables:   Optional[dict]         = None
        self._attrs_meta: Optional[pd.DataFrame] = None

    # ── connection helper ──────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(os.path.abspath(self.db_path))

    # ── schema probe ──────────────────────────────────────────────────────────

    def _tables_exist(self, conn: sqlite3.Connection, *names: str) -> bool:
        existing = {
            r[0] for r in
            conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()
        }
        return all(n in existing for n in names)

    # ── attribute metadata ────────────────────────────────────────────────────

    def get_attribute_meta(self) -> pd.DataFrame:
        """
        Returns dim_bq3_attribute rows as a DataFrame.
        Columns: attr_id, attr_label, broad_feature, [applies_* columns].
        Returns empty DataFrame if table absent.
        """
        if self._attrs_meta is not None:
            return self._attrs_meta

        conn = self._conn()
        if not self._tables_exist(conn, "dim_bq3_attribute"):
            conn.close()
            warnings.warn(
                "dim_bq3_attribute not found in DB — BIP data not yet ingested.",
                RuntimeWarning, stacklevel=2
            )
            self._attrs_meta = pd.DataFrame(
                columns=["attr_id", "attr_label", "broad_feature"]
            )
            return self._attrs_meta

        self._attrs_meta = pd.read_sql_query(
            "SELECT * FROM dim_bq3_attribute ORDER BY attr_id", conn
        )
        conn.close()
        return self._attrs_meta

    # ── TABLE 1: raw % association matrix ─────────────────────────────────────

    def get_brand_attr_matrix(
        self,
        category_codes: Optional[list[int]] = None,
        brand_ids:      Optional[list[int]] = None,
        attr_section:   Optional[str]       = None,
        attr_ids:       Optional[list[int]] = None,
    ) -> pd.DataFrame:
        """
        Build brand × attribute raw-% association matrix (TABLE 1).

        Value = respondents who chose brand B for attribute A
                ─────────────────────────────────────────────── × 100
                total respondents who evaluated attribute A

        Parameters
        ----------
        category_codes : list[int] | None
            Override instance-level category filter for this call.
        brand_ids : list[int] | None
            Restrict to specific brand IDs.
        attr_section : str | None
            Restrict to attributes whose broad_feature matches this string.

        Returns
        -------
        DataFrame  — index = brand_name, columns = attr_label, values = float (0–100).
        Empty DataFrame if imagery tables absent.
        """
        codes = category_codes if category_codes is not None else self.category_codes

        # Data layer path: skip SQLite for projects with raw_data.xlsx
        if self.project_id:
            try:
                from infoleap.data_layer import project_has_raw_layer, get_project_layer
                if project_has_raw_layer(self.project_id):
                    layer = get_project_layer(self.project_id)
                    if layer is not None:
                        img = layer.imagery.copy()
                        if any([self.zone, self.gender, self.age_band, self.city]):
                            demo = layer.demographics
                            filt = demo.copy()
                            if self.zone:      filt = filt[filt["zone"]     == self.zone]
                            if self.gender:    filt = filt[filt["gender"]   == self.gender]
                            if self.age_band:  filt = filt[filt["age_band"] == self.age_band]
                            if self.city:      filt = filt[filt["city"]     == self.city]
                            img = img[img["respondent_id"].isin(filt["respondent_id"])]
                        if not img.empty:
                            total_n = img["respondent_id"].nunique()
                            assoc = (
                                img.groupby(["brand_name", "attr_label"])["respondent_id"]
                                .nunique().reset_index()
                            )
                            assoc.columns = ["brand_name", "attr_label", "assoc_n"]
                            attr_base = img.groupby("attr_label")["respondent_id"].nunique().rename("attr_total")
                            assoc = assoc.merge(attr_base, on="attr_label")
                            assoc["pct"] = assoc["assoc_n"] / assoc["attr_total"] * 100.0
                            matrix = assoc.pivot_table(
                                index="brand_name", columns="attr_label",
                                values="pct", fill_value=0.0
                            )
                            brand_totals = img.groupby("brand_name")["respondent_id"].nunique()
                            row_sums = matrix.sum(axis=1).sort_values(ascending=False)
                            top_brands = row_sums.head(self.top_brands).index.tolist()
                            matrix = matrix.loc[
                                [b for b in top_brands if b in matrix.index]
                            ]
                            return matrix
            except Exception:
                pass

        conn  = self._conn()

        # 2026-07-30: see can_map_engine.py's get_brand_attr_matrix for the full rationale —
        # projects onboarded via the generic ingestion pipeline never populate category_code
        # (most brand-health clients survey ONE category), so applying project_1's hardcoded
        # category filter would zero out every row for them even though real data exists.
        if codes and self._tables_exist(conn, "fact_brand_imagery"):
            _has_category_dim = conn.execute(
                "SELECT 1 FROM fact_brand_imagery WHERE category_code IS NOT NULL LIMIT 1"
            ).fetchone()
            if not _has_category_dim:
                codes = None

        # Guard: tables may not exist
        needs = ["fact_brand_imagery", "dim_bq3_attribute", "dim_brand"]
        view_available = self._tables_exist(conn, "v_brand_imagery")
        if not self._tables_exist(conn, *needs):
            conn.close()
            warnings.warn(
                "fact_brand_imagery / dim_bq3_attribute not found — returning empty matrix.",
                RuntimeWarning, stacklevel=2
            )
            return pd.DataFrame()

        # Demographic join if any filter active
        _demo = [
            ("vr.zone_name",  self.zone),
            ("vr.gender",     self.gender),
            ("vr.age_band",   self.age_band),
            ("vr.city_name",  self.city),
        ]
        _has_demo  = any(v for _, v in _demo)
        _demo_join = (
            "JOIN v_respondents vr ON vr.respondent_id = fi.respondent_id"
            if _has_demo else ""
        )

        # Build WHERE clauses.
        # denom_where_parts excludes brand_ids — denominator is total respondents
        # per attribute regardless of which brand they chose, so brand filtering
        # must NOT apply (it would reference b.brand_id which has no join in denom).
        where_parts: list[str] = []
        denom_where_parts: list[str] = []
        params: list = []
        denom_params: list = []

        if codes:
            placeholders = ",".join("?" * len(codes))
            where_parts.append(f"fi.category_code IN ({placeholders})")
            denom_where_parts.append(f"fi.category_code IN ({placeholders})")
            params.extend(codes)
            denom_params.extend(codes)

        if brand_ids:
            placeholders = ",".join("?" * len(brand_ids))
            where_parts.append(f"b.brand_id IN ({placeholders})")
            params.extend(brand_ids)
            # NOT added to denom_where_parts — denom has no dim_brand join

        if attr_section:
            where_parts.append("LOWER(a.broad_feature) = LOWER(?)")
            denom_where_parts.append("LOWER(a.broad_feature) = LOWER(?)")
            params.append(attr_section)
            denom_params.append(attr_section)

        if attr_ids:
            placeholders = ",".join("?" * len(attr_ids))
            where_parts.append(f"fi.attr_id IN ({placeholders})")
            denom_where_parts.append(f"fi.attr_id IN ({placeholders})")
            params.extend(attr_ids)
            denom_params.extend(attr_ids)

        for col, val in _demo:
            if val:
                where_parts.append(f"{col} = ?")
                denom_where_parts.append(f"{col} = ?")
                params.append(val)
                denom_params.append(val)

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        denom_where_sql = ("WHERE " + " AND ".join(denom_where_parts)) if denom_where_parts else ""

        # Per-attribute total respondents (denominator)
        # Uses denom_where_sql / denom_params — no brand_ids filter.
        denom_sql = f"""
            SELECT fi.attr_id, COUNT(DISTINCT fi.respondent_id) AS total_resp
            FROM fact_brand_imagery fi
            JOIN dim_bq3_attribute a ON fi.attr_id = a.attr_id
            {_demo_join}
            {denom_where_sql}
            GROUP BY fi.attr_id
        """
        denom_df = pd.read_sql_query(denom_sql, conn, params=denom_params)

        if denom_df.empty:
            conn.close()
            return pd.DataFrame()

        # Per brand × attribute respondent count (numerator)
        if where_parts:
            numer_where = "WHERE " + " AND ".join(where_parts) + " AND fi.value = 1"
        else:
            numer_where = "WHERE fi.value = 1"

        numer_sql = f"""
            SELECT b.brand_name, a.attr_label,
                   a.broad_feature,
                   fi.attr_id,
                   COUNT(DISTINCT fi.respondent_id) AS n_chose
            FROM fact_brand_imagery fi
            JOIN dim_bq3_attribute a ON fi.attr_id = a.attr_id
            JOIN dim_brand         b ON fi.brand_id = b.brand_id
            {_demo_join}
            {numer_where}
            GROUP BY b.brand_name, a.attr_label, a.broad_feature, fi.attr_id
        """
        numer_df = pd.read_sql_query(numer_sql, conn, params=params)
        conn.close()

        if numer_df.empty:
            return pd.DataFrame()

        # Merge to compute %
        merged = numer_df.merge(denom_df, on="attr_id", how="left")
        # Left-join can produce NaN total_resp for attrs missing from denom_df;
        # treat those as zero-respondent rows and drop them to avoid NaN propagation
        # into T3 normalization and percentile calculations.
        merged["total_resp"] = merged["total_resp"].fillna(0)
        merged = merged[merged["total_resp"] > 0]
        merged["raw_pct"] = merged["n_chose"] / merged["total_resp"] * 100.0

        # Pivot: rows = brand_name, cols = attr_label
        matrix = merged.pivot_table(
            index="brand_name",
            columns="attr_label",
            values="raw_pct",
            aggfunc="mean",
            fill_value=0.0,
        )
        matrix.columns.name = None
        matrix.index.name   = "brand_name"

        # Filter to top_brands by mean % across attrs (removes obscure brands
        # that distort column averages and inflate normalized deviations).
        if self.top_brands and len(matrix) > self.top_brands:
            brand_totals = matrix.mean(axis=1).sort_values(ascending=False)
            matrix = matrix.loc[brand_totals.head(self.top_brands).index]

        # Drop brands with ALL-zero associations in the selected attribute set.
        # In "All" category, cross-category brands (e.g. water pump brands in a
        # ceiling-fan-dominant top-N) get phantom zeros for every attribute.
        # These corrupt col_avgs (deflated) and T3 normalization (inflated signals).
        matrix = matrix.loc[matrix.sum(axis=1) > 0]

        # Attach section metadata as a separate attribute on the frame
        section_map = (
            merged[["attr_label", "broad_feature"]]
            .drop_duplicates()
            .set_index("attr_label")["broad_feature"]
            .to_dict()
        )
        matrix.attrs["section_map"] = section_map

        # Attach per-attribute respondent counts for significance testing
        n_resp = denom_df.set_index("attr_id")["total_resp"]
        # Map attr_id to attr_label to match matrix columns
        id_to_label = merged[["attr_id", "attr_label"]].drop_duplicates().set_index("attr_id")["attr_label"].to_dict()
        n_resp.index = n_resp.index.map(id_to_label)
        matrix.attrs["n_resp"] = n_resp

        self._matrix = matrix
        return matrix

    # ── full normalization pipeline ───────────────────────────────────────────

    def compute_cell_p_values(self, t1: pd.DataFrame, expected: np.ndarray, n_resp: pd.Series) -> pd.DataFrame:
        """
        Calculate Z-test p-values for each association cell.
        Z = (Observed - Expected) / sqrt(Expected * (100 - Expected) / n)
        """
        import scipy.stats as stats
        p_obs = t1.values
        # Avoid division by zero
        expected_safe = np.clip(expected, 0.1, 99.9)
        # Reindex n_resp to match t1 columns to ensure alignment
        n_resp_aligned = n_resp.reindex(t1.columns).fillna(1).values
        n_val = n_resp_aligned[None, :] # broadcast across brands
        
        se = np.sqrt(expected_safe * (100 - expected_safe) / n_val)
        # Avoid division by zero in SE
        se = np.clip(se, 0.001, None)
        z_scores = (p_obs - expected_safe) / se
        p_values = 2 * (1 - stats.norm.cdf(np.abs(z_scores)))
        
        return pd.DataFrame(p_values, index=t1.index, columns=t1.columns)

    def compute_normalization(
        self,
        matrix: Optional[pd.DataFrame] = None,
    ) -> dict:
        """
        Run all 14 (now 15) normalization tables on the brand × attribute raw-% matrix.

        Parameters
        ----------
        matrix : pd.DataFrame | None
            Output of get_brand_attr_matrix().  If None, calls it automatically
            using instance-level filters.

        Returns
        -------
        dict with keys:
            table1_raw, table2_abs_diff, table3_norm_pct,
            table4_filtered, table5_positive, table6_negative,
            table7_ranks, table8_ranks_pos, table9_ranks_neg,
            table13_combined, table14_significance, table15_p_values,
            percentiles, column_averages
        """
        if matrix is None:
            matrix = self.get_brand_attr_matrix()

        if matrix.empty:
            warnings.warn(
                "BIP matrix is empty — normalization cannot proceed.",
                RuntimeWarning, stacklevel=2
            )
            empty = pd.DataFrame()
            return {
                "table1_raw":       empty,
                "table2_abs_diff":  empty,
                "table3_norm_pct":  empty,
                "table4_filtered":  empty,
                "table5_positive":  empty,
                "table6_negative":  empty,
                "table7_ranks":     empty,
                "table8_ranks_pos": empty,
                "table9_ranks_neg": empty,
                "table13_combined": empty,
                "table14_significance": empty,
                "table15_p_values": empty,
                "percentiles":      {"p25": 0.0, "p65": 0.0, "p75": 0.0},
                "column_averages":  pd.Series(dtype=float),
            }

        # ── TABLE 1: raw % ────────────────────────────────────────────────────
        t1 = matrix.copy().astype(float)
        col_avgs = t1.mean(axis=0)    # attribute-level mean across brands
        row_avgs = t1.mean(axis=1)    # brand-level mean across attributes
        grand_mean = t1.values.mean() # category-level grand mean

        # ── TABLE 2: absolute deviation from column mean (internal only) ────────
        # NOTE: Excel Normalisation.xlsx does not have this as a named table.
        # It is an intermediate step not shown in the Excel workbook.
        t2 = t1.subtract(col_avgs, axis=1)

        # ── TABLE 3: doubly-normalized deviation ──────────────────────────────
        # EXCEL MAPPING: this is Excel's "TABLE 2" (Normalisation.xlsx sheet "65%").
        # Code numbering is offset by 1 vs Excel because TABLE 2 above is internal.
        # Formula: (raw × grand_mean) / (brand_avg × col_avg) - 1) × 100
        # Replicates Excel: =(C8*$J$36)*100/($J8*C$36)-100
        # where $J$36 = grand mean, $J8 = col avg (brand row average), C$36 = attribute col avg.
        col_avgs_safe = col_avgs.clip(lower=0.1)
        row_avgs_safe = row_avgs.clip(lower=0.1)
        grand_mean_safe = max(grand_mean, 0.1)

        expected = np.outer(row_avgs_safe.values, col_avgs_safe.values) / grand_mean_safe
        t3 = pd.DataFrame(
            (t1.values / expected - 1) * 100.0,
            index=t1.index, columns=t1.columns
        ).fillna(0.0).clip(-500, 500)

        # ── Brand-normalized score (Excel T3): (raw/brand_avg - 1) × 100 ─────
        # Used as secondary gate: brand must over-perform vs its OWN average.
        t_brand = (t1.divide(row_avgs_safe, axis=0) - 1) * 100.0

        # ── TABLE 15: p-values ────────────────────────────────────────────────
        n_resp = matrix.attrs.get("n_resp", pd.Series(dtype=int))
        expected_for_pval = np.tile(col_avgs_safe.values, (len(t1), 1))
        t15 = self.compute_cell_p_values(t1, expected_for_pval, n_resp)

        # ── Percentile thresholds: signed p65 of doubly-normalized T3 ────────
        # Matches Excel $O$37 = PERCENTILE(all T2 values, 0.65)
        all_t3_vals = t3.values.flatten()
        if len(all_t3_vals) > 0:
            p25       = float(np.percentile(all_t3_vals, 25))
            p65_true  = float(np.percentile(all_t3_vals, 65))
            p75       = float(np.percentile(all_t3_vals, 75))
            p_thresh  = float(np.percentile(all_t3_vals, self.percentile_threshold))
        else:
            p25, p65_true, p75, p_thresh = 0.0, 4.0, 8.0, 4.0
        p65 = p_thresh

        # ── TABLE 4: threshold-filtered ───────────────────────────────────────
        # Significance rule (XLSTAT-correct):
        #   YES = brand-normalized T3 > 0 AND cell-level Z-test p < 0.05 (T15)
        # T15 already computed above via compute_cell_p_values(); wire it in here.
        # Fallback to percentile gate if T15 shape mismatch (zero-row guard).
        if not t15.empty and t15.shape == t3.shape:
            sig_mask = (t3.values > 0) & (t_brand.values > 0) & (t15.values < 0.05)
        else:
            sig_mask = (t3.values > p65) & (t_brand.values > 0)
        t4 = t3.where(sig_mask, other=0.0)

        # ── TABLE 5: positive significant deviations only ─────────────────────
        t5 = t4.where(t4 > 0, other=0.0)

        # ── TABLE 6: negative significant deviations only ─────────────────────
        t6 = t4.where(t4 < 0, other=0.0)

        # ── TABLE 7: ranks — all significant (1 = largest absolute deviation) ─
        n_brands = len(t4)
        non_sig_rank = n_brands + 1

        t7 = t4.copy().astype(float)
        for col in t4.columns:
            series = t4[col].abs()   # rank by magnitude
            t7[col] = _rank_series(series, non_sig_rank)

        # ── TABLE 8: ranks — positive only ────────────────────────────────────
        t8 = t5.copy().astype(float)
        for col in t5.columns:
            t8[col] = _rank_series(t5[col], non_sig_rank)

        # ── TABLE 9: ranks — negative only (rank by most negative) ───────────
        t9 = t6.copy().astype(float)
        for col in t6.columns:
            neg_abs = t6[col].abs()
            t9[col] = _rank_series(neg_abs, non_sig_rank)

        # ── TABLE 13: combined rank summary ───────────────────────────────────
        # Merge rank info: for each cell, show overall rank (t7) alongside sign
        # Presented as "Rank (sign)" or just the rank integer.
        t13 = t7.copy()

        # ── TABLE 14: YES / NO significance flags ─────────────────────────────
        t14 = pd.DataFrame(sig_mask, index=t1.index, columns=t1.columns).map(lambda x: "YES" if x else "NO")

        # Cache and return
        pct_key = f"p{int(self.percentile_threshold)}"
        result = {
            "table1_raw":          t1.round(4),
            "table2_abs_diff":     t2.round(4),
            "table3_norm_pct":     t3.round(4),
            "table4_filtered":     t4.round(4),
            "table5_positive":     t5.round(4),
            "table6_negative":     t6.round(4),
            "table7_ranks":        t7,
            "table8_ranks_pos":    t8,
            "table9_ranks_neg":    t9,
            "table13_combined":    t13,
            "table14_significance": t14,
            "table15_p_values":    t15.round(4),
            "percentiles":         {
                "p25":   round(p25, 4),
                "p65":   round(p65_true, 4),  # always the true 65th percentile
                "p75":   round(p75, 4),
                pct_key: round(p_thresh, 4),  # selected threshold (may differ from p65)
            },
            "column_averages":     col_avgs.round(4),
        }
        self._tables = result
        return result

    # Alias for compute_normalization to match specific requirements
    normalize_imagery_matrix = compute_normalization

    # ── significance summary ──────────────────────────────────────────────────

    def get_significance_summary(self) -> pd.DataFrame:
        """
        Per-brand: list of attributes where significance = YES.

        Returns
        -------
        DataFrame with columns:
            brand_name, significant_attributes (list), count_significant
        """
        tables = self._tables or self.compute_normalization()
        t14 = tables["table14_significance"]
        if t14.empty:
            return pd.DataFrame(columns=["brand_name", "significant_attributes", "count_significant"])

        rows = []
        for brand in t14.index:
            yes_attrs = t14.columns[t14.loc[brand] == "YES"].tolist()
            rows.append({
                "brand_name":             brand,
                "significant_attributes": yes_attrs,
                "count_significant":      len(yes_attrs),
            })

        return (
            pd.DataFrame(rows)
            .sort_values("count_significant", ascending=False)
            .reset_index(drop=True)
        )

    # ── brand strength map ────────────────────────────────────────────────────

    def get_brand_strength_map(self) -> dict:
        """
        Per brand: categorize attributes into positive / negative / neutral.

        Returns
        -------
        {
          brand_name: {
            "positive_attrs": [attr, ...],
            "negative_attrs": [attr, ...],
            "neutral_attrs":  [attr, ...],
          }
        }
        """
        tables = self._tables or self.compute_normalization()
        t4 = tables["table4_filtered"]
        t14 = tables["table14_significance"]

        if t4.empty:
            return {}

        strength_map: dict = {}
        for brand in t4.index:
            pos_attrs  = t4.columns[(t4.loc[brand] > 0)].tolist()
            neg_attrs  = t4.columns[(t4.loc[brand] < 0)].tolist()
            # neutral = all attributes minus significant ones
            sig_attrs  = set(pos_attrs) | set(neg_attrs)
            neut_attrs = [c for c in t4.columns if c not in sig_attrs]

            strength_map[brand] = {
                "positive_attrs": pos_attrs,
                "negative_attrs": neg_attrs,
                "neutral_attrs":  neut_attrs,
            }

        return strength_map

    # ── section (broad_feature) aggregation ──────────────────────────────────

    def _section_scores(self, tables: dict) -> pd.DataFrame:
        """
        Mean of TABLE 4 filtered deviations, grouped by broad_feature section.
        Returns DataFrame: index=brand_name, columns=section labels.
        """
        t4 = tables["table4_filtered"]
        if t4.empty:
            return pd.DataFrame()

        section_map = self._matrix.attrs.get("section_map", {}) if self._matrix is not None else {}
        if not section_map:
            # Try to load from DB
            meta = self.get_attribute_meta()
            if not meta.empty:
                section_map = (
                    meta.set_index("attr_label")["broad_feature"]
                    .to_dict()
                )

        if not section_map:
            return pd.DataFrame()

        section_df = t4.copy()
        section_df.columns = [
            section_map.get(c, "other") for c in section_df.columns
        ]
        # Replace zeros (non-significant cells) with NaN before groupby mean so
        # insignificant attributes don't dilute strong signals in the radar chart.
        # fillna(0) restores zeros on sections where brand has NO significant attrs.
        grouped = (
            section_df.replace(0, float("nan"))
            .T.groupby(level=0).mean().T
            .fillna(0.0)
        )
        return grouped

    # ── chart specs ───────────────────────────────────────────────────────────

    def get_chart_specs(self) -> list:
        """
        Produce all 6 Plotly chart specifications as JSON-serializable dicts.

        Returns
        -------
        list of dicts, each:
            { "chart_id": str, "title": str, "figure": dict (Plotly JSON) }

        Charts produced:
          1. bip_heatmap          — brands × attributes heatmap
          2. brand_strength_bar   — count of YES attributes by section per brand
          3. attr_competitiveness — deviation range per attribute with brand labels
          4. top_bottom_table     — top/bottom performers table
          5. section_radar        — normalized deviation radar by section
          6. significance_hist    — distribution of all normalized deviations
        """
        tables = self._tables or self.compute_normalization()
        specs  = []

        if tables["table1_raw"].empty:
            # Return placeholder specs with empty figures
            for cid, title in [
                ("bip_heatmap",          "BIP Heatmap (no data)"),
                ("brand_strength_bar",   "Brand Strength (no data)"),
                ("attr_competitiveness", "Attribute Competitiveness (no data)"),
                ("top_bottom_table",     "Top/Bottom Performers (no data)"),
                ("section_radar",        "Section Radar (no data)"),
                ("significance_hist",    "Significance Distribution (no data)"),
            ]:
                specs.append({
                    "chart_id": cid,
                    "title":    title,
                    "figure":   go.Figure().to_dict(),
                })
            return specs

        t3   = tables["table3_norm_pct"]
        t4   = tables["table4_filtered"]
        t14  = tables["table14_significance"]
        t1   = tables["table1_raw"]
        t2   = tables["table2_abs_diff"]
        pcts = tables["percentiles"]

        # ── 1. BIP Heatmap ─────────────────────────────────────────────────────
        # Diverging heatmap: green = over-indexed, red = under-indexed.
        # Significance is shown as a subtle dot ONLY on significant cells (not
        # YES/NO on every cell) so the colour signal reads cleanly — the full
        # YES/NO flag stays in the hover tooltip. Executive-readable.
        _sig_raw = t14.values.tolist()
        dot_text = [["•" if str(v).upper() == "YES" else "" for v in row]
                    for row in _sig_raw]
        fig_hm = go.Figure(
            data=go.Heatmap(
                z=t3.values.tolist(),
                x=t3.columns.tolist(),
                y=t3.index.tolist(),
                customdata=_sig_raw,
                colorscale="RdYlGn",
                zmid=0,
                text=dot_text,
                texttemplate="%{text}",
                textfont={"size": 13, "color": "rgba(15,23,42,0.55)"},
                colorbar=dict(title=dict(text="Norm.<br>Dev. %", side="right"),
                              thickness=14, len=0.7, outlinewidth=0),
                xgap=1, ygap=1,
                hoverongaps=False,
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "%{x}<br>"
                    "Norm Dev: <b>%{z:.1f}%</b><br>"
                    "Significant: %{customdata}<extra></extra>"
                ),
            )
        )
        fig_hm.update_layout(
            title=dict(
                text=("<b>Brand Image Profiling — Normalized Deviation</b>"
                      "<br><sup>Green = brand over-indexes on the attribute · Red = under-indexes"
                      " · • marks statistically significant cells (hover for detail)</sup>"),
                font=dict(size=15, color="#0f172a"), x=0.01, xanchor="left",
            ),
            paper_bgcolor="white", plot_bgcolor="white",
            font=dict(family="Inter, sans-serif", size=11, color="#334155"),
            xaxis=dict(tickangle=-45, title="", tickfont=dict(size=9, color="#64748b")),
            yaxis=dict(title="", tickfont=dict(size=11, color="#0f172a"),
                       autorange="reversed"),
            height=max(400, len(t3.index) * 38 + 170),
            margin=dict(l=150, b=210, t=70, r=20),
        )
        specs.append({
            "chart_id": "bip_heatmap",
            "title":    "BIP Normalized Deviation Heatmap",
            "figure":   fig_hm.to_dict(),
        })

        # ── 2. Brand Strength Bar ──────────────────────────────────────────────
        # Count of YES attributes per brand, stacked by section
        section_df = self._section_scores(tables)
        strength_map = self.get_brand_strength_map()

        # Build counts per brand per section
        meta = self.get_attribute_meta()
        section_map: dict[str, str] = {}
        if not meta.empty and "attr_label" in meta.columns and "broad_feature" in meta.columns:
            section_map = (
                meta.set_index("attr_label")["broad_feature"]
                .to_dict()
            )
        elif self._matrix is not None:
            section_map = self._matrix.attrs.get("section_map", {})

        # Use t14 (YES/NO) with positive t3 filter to count brand-owned (over-indexed) attrs
        t3 = tables.get("table3_norm_pct", tables["table4_filtered"].where(
            tables["table4_filtered"] != 0, other=1.0))
        t14_sig = tables["table14_significance"]

        yes_counts: dict[str, dict[str, int]] = {}
        for brand in strength_map:
            section_counts: dict[str, int] = {}
            if brand in t14_sig.index:
                for attr in t14_sig.columns:
                    # Count only: YES in t14 AND positive in t3 (brand owns this attribute)
                    if t14_sig.loc[brand, attr] == "YES":
                        try:
                            t3_val = float(t3.loc[brand, attr]) if brand in t3.index and attr in t3.columns else 0
                        except Exception:
                            t3_val = 0
                        if t3_val > 0:
                            # Use DB section name directly (no SECTION_LABELS mapping needed)
                            sec_label = str(section_map.get(attr, "Other"))
                            # Normalise for display consistency
                            if sec_label.lower() in ("other", ""):
                                sec_label = "Other"
                            section_counts[sec_label] = section_counts.get(sec_label, 0) + 1
            yes_counts[brand] = section_counts

        all_sections = sorted(
            {sec for counts in yes_counts.values() for sec in counts}
        )
        brands_ordered = sorted(
            yes_counts, key=lambda b: sum(yes_counts[b].values()), reverse=True
        )

        fig_bar = go.Figure()
        colors  = px.colors.qualitative.Set2
        for i, sec in enumerate(all_sections):
            fig_bar.add_trace(go.Bar(
                name=sec,
                x=brands_ordered,
                y=[yes_counts[b].get(sec, 0) for b in brands_ordered],
                marker_color=colors[i % len(colors)],
                text=[yes_counts[b].get(sec, 0) or "" for b in brands_ordered],
                textposition="inside",
            ))

        total_by_brand = {b: sum(yes_counts[b].values()) for b in brands_ordered}
        fig_bar.update_layout(
            barmode="stack",
            title="Brand Strength — Count of Positively-Significant Attributes by Section<br><sup>Only YES flags where brand is over-indexed (t3 &gt; 0)</sup>",
            xaxis=dict(title="Brand", tickangle=-35),
            yaxis=dict(title="# Attributes Owned"),
            legend=dict(title="Section"),
            height=520,
            margin=dict(t=80, b=80, l=60, r=40),
        )
        specs.append({
            "chart_id": "brand_strength_bar",
            "title":    "Brand Strength Bar Chart",
            "figure":   fig_bar.to_dict(),
        })

        # ── 3. Attribute Competitiveness Chart ─────────────────────────────────
        # Per attribute: range bar showing min/max normalized deviation,
        # with brand labels at each end.
        attrs = t3.columns.tolist()
        attr_min  = t3.min(axis=0)
        attr_max  = t3.max(axis=0)
        attr_range = attr_max - attr_min

        brand_at_min = t3.idxmin(axis=0)
        brand_at_max = t3.idxmax(axis=0)

        sorted_attrs = attr_range.sort_values(ascending=True).index.tolist()

        fig_comp = go.Figure()
        fig_comp.add_trace(go.Bar(
            name="Range",
            x=[attr_range[a] for a in sorted_attrs],
            y=sorted_attrs,
            orientation="h",
            base=[attr_min[a] for a in sorted_attrs],
            marker_color="steelblue",
            opacity=0.6,
            customdata=[[brand_at_min[a], brand_at_max[a]] for a in sorted_attrs],
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Min: %{base:.1f}% (%{customdata[0]})<br>"
                "Max: %{x:.1f}% (%{customdata[1]})<extra></extra>"
            ),
        ))
        fig_comp.add_trace(go.Scatter(
            name="Min brand",
            x=[attr_min[a] for a in sorted_attrs],
            y=sorted_attrs,
            mode="markers+text",
            text=[brand_at_min[a][:6] for a in sorted_attrs],
            textposition="middle left",
            marker=dict(color="tomato", size=8),
            hoverinfo="skip",
        ))
        fig_comp.add_trace(go.Scatter(
            name="Max brand",
            x=[attr_max[a] for a in sorted_attrs],
            y=sorted_attrs,
            mode="markers+text",
            text=[brand_at_max[a][:6] for a in sorted_attrs],
            textposition="middle right",
            marker=dict(color="seagreen", size=8),
            hoverinfo="skip",
        ))
        fig_comp.add_vline(x=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig_comp.update_layout(
            title="Attribute Competitiveness — Deviation Range across Brands",
            xaxis=dict(title="Normalized Deviation (%)"),
            yaxis=dict(title="Attribute"),
            height=max(500, len(attrs) * 22 + 150),
            margin=dict(l=180, t=60),
        )
        specs.append({
            "chart_id": "attr_competitiveness",
            "title":    "Attribute Competitiveness Chart",
            "figure":   fig_comp.to_dict(),
        })

        # ── 4. Top/Bottom Performers Table ─────────────────────────────────────
        # Long-form table: attribute × brand, with raw%, diff, norm_dev, significance
        rows = []
        for attr in t1.columns:
            for brand in t1.index:
                rows.append({
                    "Attribute":     attr,
                    "Brand":         brand,
                    "Raw %":         round(t1.at[brand, attr], 2),
                    "Diff":          round(t2.at[brand, attr], 2),
                    "Norm Dev %":    round(t3.at[brand, attr], 2),
                    "Significant":   t14.at[brand, attr],
                })
        perf_df = pd.DataFrame(rows)
        perf_df_sorted = perf_df.sort_values("Norm Dev %", ascending=False)

        header_vals = list(perf_df_sorted.columns)
        cell_vals   = [perf_df_sorted[c].tolist() for c in header_vals]

        # Color-code significance
        sig_col_idx = header_vals.index("Significant")
        cell_colors = [
            (
                ["lightgreen" if v == "YES" else "lightyellow" for v in cell_vals[sig_col_idx]]
                if i == sig_col_idx else
                ["white"] * len(cell_vals[0])
            )
            for i, _ in enumerate(header_vals)
        ]

        fig_tbl = go.Figure(
            data=go.Table(
                header=dict(
                    values=[f"<b>{h}</b>" for h in header_vals],
                    fill_color="steelblue",
                    font_color="white",
                    align="left",
                ),
                cells=dict(
                    values=cell_vals,
                    fill_color=cell_colors,
                    align=["left", "left", "right", "right", "right", "center"],
                ),
            )
        )
        fig_tbl.update_layout(
            title="Top/Bottom Performers — Full BIP Detail Table",
            height=600,
        )
        specs.append({
            "chart_id": "top_bottom_table",
            "title":    "Top/Bottom Performers Table",
            "figure":   fig_tbl.to_dict(),
        })

        # ── 5. Section-wise Radar ──────────────────────────────────────────────
        # Radar chart: for each brand, mean normalized deviation per broad_feature section
        if section_df.empty:
            fig_radar = go.Figure()
            fig_radar.update_layout(title="Section Radar (section data unavailable)")
        else:
            section_labels = section_df.columns.tolist()
            fig_radar = go.Figure()
            colors_r = px.colors.qualitative.Plotly
            for i, brand in enumerate(section_df.index):
                vals = section_df.loc[brand].tolist()
                fig_radar.add_trace(go.Scatterpolar(
                    r=vals + [vals[0]],
                    theta=section_labels + [section_labels[0]],
                    name=brand,
                    mode="lines+markers",
                    line_color=colors_r[i % len(colors_r)],
                    opacity=0.8,
                ))
            fig_radar.update_layout(
                title="Section-wise Radar — Normalized Deviation by Broad Feature",
                polar=dict(
                    radialaxis=dict(
                        visible=True,
                        title="Mean Norm. Dev. %",
                    )
                ),
                showlegend=True,
                height=550,
            )

        specs.append({
            "chart_id": "section_radar",
            "title":    "Section-wise Radar",
            "figure":   fig_radar.to_dict(),
        })

        # ── 6. Significance Distribution Histogram ────────────────────────────
        all_devs = t3.values.flatten()

        fig_hist = go.Figure()
        fig_hist.add_trace(go.Histogram(
            x=all_devs,
            nbinsx=50,
            name="Norm. Dev. distribution",
            marker_color="steelblue",
            opacity=0.75,
        ))
        # Percentile lines
        for pname, pval, color in [
            ("p25",  pcts["p25"],  "gold"),
            ("p65",  pcts["p65"],  "darkorange"),
            ("p75",  pcts["p75"],  "tomato"),
            ("-p65", -pcts["p65"], "darkorange"),
            ("-p75", -pcts["p75"], "tomato"),
        ]:
            fig_hist.add_vline(
                x=pval,
                line_dash="dash",
                line_color=color,
                annotation_text=f"{pname}={pval:.1f}",
                annotation_position="top right" if pval >= 0 else "top left",
            )
        fig_hist.update_layout(
            title="Significance Distribution — Normalized Deviations with Percentile Thresholds",
            xaxis=dict(title="Normalized Deviation (%)"),
            yaxis=dict(title="# Cells"),
            height=420,
        )
        specs.append({
            "chart_id": "significance_hist",
            "title":    "Significance Distribution Histogram",
            "figure":   fig_hist.to_dict(),
        })

        return specs

    # ── convenience: full run ─────────────────────────────────────────────────

    def run(
        self,
        category_codes: Optional[list[int]] = None,
        brand_ids:      Optional[list[int]] = None,
        attr_section:   Optional[str]       = None,
        matrix:         Optional[pd.DataFrame] = None,
    ) -> dict:
        """
        End-to-end convenience method.

        Loads raw matrix (or accepts one directly), computes all tables,
        generates chart specs.

        Parameters
        ----------
        matrix : pd.DataFrame | None
            If provided, skip DB load and use this brands × attributes matrix directly.
            Useful for validation against external reference data (e.g. Excel files).

        Returns
        -------
        {
          "matrix":           pd.DataFrame,
          "tables":           dict (all 14 tables + percentiles),
          "significance":     pd.DataFrame,
          "strength_map":     dict,
          "chart_specs":      list,
          "status":           "ok" | "empty",
          "message":          str,
        }
        """
        if matrix is None:
            matrix = self.get_brand_attr_matrix(
                category_codes=category_codes,
                brand_ids=brand_ids,
                attr_section=attr_section,
            )

        if matrix is None or matrix.empty:
            return {
                "status":  "empty",
                "message": "No data available for selected filters.",
                "matrix":  pd.DataFrame(),
                "tables":  {},
                "significance": pd.DataFrame(),
                "strength_map": {},
                "chart_specs":  [],
            }

        tables       = self.compute_normalization(matrix)
        significance = self.get_significance_summary()
        strength_map = self.get_brand_strength_map()
        chart_specs  = self.get_chart_specs()

        return {
            "status":       "ok",
            "message":      "ok",
            "matrix":       matrix,
            "tables":       tables,
            "significance": significance,
            "strength_map": strength_map,
            "chart_specs":  chart_specs,
        }


# ── module self-test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    engine = BIPNormalizationEngine()
    print(f"DB path resolved: {engine.db_path}")

    print("Loading brand-attribute matrix…")
    matrix = engine.get_brand_attr_matrix()

    if matrix.empty:
        print("Matrix empty — fact_brand_imagery data not yet ingested.")
        print("Engine initialised correctly; will produce results once BQ3 data is loaded.")
    else:
        print(f"Matrix shape: {matrix.shape}  ({len(matrix)} brands × {len(matrix.columns)} attributes)")
        tables = engine.compute_normalization(matrix)
        pcts = tables["percentiles"]
        print(f"Percentile thresholds — p25: {pcts['p25']:.2f}  p65: {pcts['p65']:.2f}  p75: {pcts['p75']:.2f}")

        sig = engine.get_significance_summary()
        print("\nSignificance summary (top 5 brands):")
        print(sig.head())

        charts = engine.get_chart_specs()
        print(f"\nGenerated {len(charts)} chart specs:")
        for c in charts:
            print(f"  {c['chart_id']:30s}  keys={list(c['figure'].keys())}")

    print("\nDone.")
