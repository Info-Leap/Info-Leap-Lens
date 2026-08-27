"""
CorrespondenceAnalysis (CA) engine for InfoLeap Pulse brand health dashboard.

Replicates full XLSTAT CA output from 'Data for CAN MAP_BLUSH Project.xlsm':
  - Contingency table (brand × attribute % matrix)
  - Chi-square test of independence
  - Eigenvalues / inertia decomposition
  - Row results (brands): weights, distances, profiles, chi-sq distances,
    principal coords, standard coords, contributions, squared cosines
  - Column results (attributes): same set as rows
  - Symmetric perceptual map (F1 × F2)
  - Asymmetric perceptual map (brands principal + attributes standard)
  - Scree plot, contribution bars, cos² heatmap, chi-sq distance heatmap,
    row profiles radar, brand trajectory plot

CA algorithm (from scratch — no sklearn):
  1. Input N: brands × attrs, values = % associations
  2. P = N / N.sum()           correspondence matrix
  3. r = P.sum(axis=1)         row masses
  4. c = P.sum(axis=0)         column masses
  5. S = Dr^{-1/2} (P - r c') Dc^{-1/2}   standardised residuals
  6. SVD: U, d, Vt = svd(S, full_matrices=False)
  7. Principal row coords:    F = Dr^{-1/2} U diag(d)
  8. Principal col coords:    G = Dc^{-1/2} Vt' diag(d)
  9. Standard row coords:     A = Dr^{-1/2} U
  10. Standard col coords:    B = Dc^{-1/2} Vt'
  11. Eigenvalues = d²
  12. Total inertia = sum(d²)

Dependencies: numpy, scipy, pandas, plotly
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

# ── project path bootstrap ────────────────────────────────────────────────────
_LENS_DIR = Path(__file__).resolve().parent.parent
_PROJ_ROOT = _LENS_DIR.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

# Default DB path (same pattern as BrandImageryEngine)
_DEFAULT_DB = _PROJ_ROOT / "oxdata" / "data" / "project_1" / "oxdata.db"


# ─────────────────────────────────────────────────────────────────────────────
# Plotly import (optional — engine degrades gracefully if unavailable)
# ─────────────────────────────────────────────────────────────────────────────
try:
    import plotly.graph_objects as go
    import plotly.colors as pc

    _PLOTLY_OK = True
except ImportError:  # pragma: no cover
    _PLOTLY_OK = False


# ─────────────────────────────────────────────────────────────────────────────
# Colour palette (consistent with InfoLeap Pulse design)
# ─────────────────────────────────────────────────────────────────────────────
_BRAND_COLOURS = [
    "#4F81BD", "#C0504D", "#9BBB59", "#8064A2",
    "#4BACC6", "#F79646", "#2C4770", "#E05C5C",
    "#70AD47", "#ED7D31", "#5B9BD5", "#FFC000",
    "#7030A0", "#00B0F0", "#FF0000", "#92D050",
    "#00B050", "#FF7F00", "#7F7F7F", "#595959",
]
_ATTR_COLOUR  = "#A9A9A9"   # uniform grey for attribute points
_GRID_COLOUR  = "#E5E5E5"
_BG_COLOUR    = "#FAFAFA"

_PLOTLY_LAYOUT_BASE = dict(
    paper_bgcolor=_BG_COLOUR,
    plot_bgcolor=_BG_COLOUR,
    font=dict(family="Inter, sans-serif", size=12, color="#333333"),
    margin=dict(l=60, r=40, t=60, b=60),
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: safe division
# ─────────────────────────────────────────────────────────────────────────────
def _safe_div(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b == 0, 0.0, a / b)


# ── Category Mapping (mq6: 1-6=recent owners, 7-12=intending buyers) ─────────
PRODUCT_CODES = {
    "All":           list(range(1, 13)),
    "Ceiling Fans":  [1, 7],
    "Air Cooler":    [2, 8],
    "Mixer Grinder": [3, 9],
    "LED Batten":    [4, 10],
    "Water Heater":  [5, 11],
    "Water Pumps":   [6, 12],
}


class CorrespondenceAnalysis:
    """
    Full Correspondence Analysis engine matching XLSTAT output.

    Parameters
    ----------
    db_path : str | Path | None
        Path to oxdata.db. Resolved via get_db_path() if None.
    category_codes : list[str] | None
        Filter fact_brand_imagery rows to these category codes.
    min_respondents : int
        Minimum respondents for a brand to be included (default 50).
    top_brands : int
        Maximum brands to include, sorted by total associations (default 20).
    top_attrs : int
        Maximum attributes to include, sorted by total associations (default 30).
    """

    def __init__(
        self,
        db_path=None,
        category_codes: Optional[list] = None,
        min_respondents: int = 50,
        top_brands: int = 20,
        top_attrs: int = 30,
        zone: Optional[str] = None,
        gender: Optional[str] = None,
        age_band: Optional[str] = None,
        city: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        if db_path is None:
            try:
                from oxdata.db_loader import get_db_path
                found = get_db_path(required_table="fact_brand_imagery", project_id=project_id)
                self.db_path = str(found) if found else str(_DEFAULT_DB)
            except Exception:
                self.db_path = str(_DEFAULT_DB)
        else:
            self.db_path = str(db_path)

        self.category_codes   = category_codes
        self.min_respondents  = min_respondents
        self.top_brands       = top_brands
        self.top_attrs        = top_attrs
        # Demographic filters (None or "all" = no filter)
        self.zone     = None if (not zone     or zone     == "all") else zone
        self.gender   = None if (not gender   or gender   == "all") else gender
        self.age_band = None if (not age_band or age_band == "all") else age_band
        self.city     = None if (not city     or city     == "all") else city
        self.project_id = project_id

        # Set after fit()
        self._matrix: Optional[pd.DataFrame] = None
        self._results: Optional[dict]        = None

    # ─────────────────────────────────────────────────────────────────────
    # DB helpers
    # ─────────────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(os.path.abspath(self.db_path))

    def _get_brand_attr_matrix_from_layer(self) -> pd.DataFrame:
        """Build brand×attr % matrix from raw data layer (for projects with raw_data.xlsx)."""
        try:
            from lens.data_layer import get_project_layer
            layer = get_project_layer(self.project_id)
            if layer is None:
                return pd.DataFrame()
            img = layer.imagery.copy()
            if any([self.zone, self.gender, self.age_band, self.city]):
                demo = layer.demographics
                filt = demo.copy()
                if self.zone:      filt = filt[filt["zone"]     == self.zone]
                if self.gender:    filt = filt[filt["gender"]   == self.gender]
                if self.age_band:  filt = filt[filt["age_band"] == self.age_band]
                if self.city:      filt = filt[filt["city"]     == self.city]
                img = img[img["respondent_id"].isin(filt["respondent_id"])]
            if img.empty:
                return pd.DataFrame()
            total_n = img["respondent_id"].nunique()
            assoc = (
                img.groupby(["brand_name", "attr_label"])["respondent_id"]
                .nunique().reset_index()
            )
            assoc.columns = ["brand_name", "attr_label", "assoc_n"]
            assoc["pct"] = assoc["assoc_n"] / total_n * 100.0
            matrix = assoc.pivot_table(
                index="brand_name", columns="attr_label", values="pct", fill_value=0.0
            )
            brand_totals = img.groupby("brand_name")["respondent_id"].nunique()
            eligible = brand_totals[brand_totals >= self.min_respondents].index
            row_sums = matrix.sum(axis=1).sort_values(ascending=False)
            top_brand_names = row_sums[row_sums.index.isin(eligible)].head(self.top_brands).index.tolist()
            attr_sums = matrix.sum(axis=0).sort_values(ascending=False)
            top_attr_names = attr_sums.head(self.top_attrs).index.tolist()
            matrix = matrix.loc[
                [b for b in top_brand_names if b in matrix.index],
                [a for a in top_attr_names  if a in matrix.columns],
            ]
            if matrix.empty:
                return matrix
            return matrix.loc[:, matrix.std(axis=0) > 0]
        except Exception:
            return pd.DataFrame()

    # ─────────────────────────────────────────────────────────────────────
    # Public: data retrieval
    # ─────────────────────────────────────────────────────────────────────

    def get_brand_attr_matrix(
        self,
        category_codes: Optional[list] = None,
        brand_ids: Optional[list] = None,
        attr_ids: Optional[list] = None,
        attr_section: Optional[str] = None,
        awareness_stages: Optional[list] = None,
    ) -> pd.DataFrame:
        """
        Build brand × attribute % association matrix from fact_brand_imagery.

        Each cell = (respondents associating brand B with attribute A) /
                    (total respondents who answered that attribute question)
                    × 100.

        The denominator is per-attribute, not global, which is the correct
        survey convention when not all attributes are shown to all respondents.

        Parameters
        ----------
        category_codes : list[str] | None
            Override instance-level category_codes filter.
        brand_ids : list[int] | None
            Restrict to these brand IDs.
        attr_ids : list[int] | None
            Restrict to these attribute IDs.

        Returns
        -------
        pd.DataFrame
            Index = brand names, columns = attribute labels, values = %.
            Brands with fewer than `min_respondents` total associations are
            dropped. Attributes with zero variance across brands are dropped.
        """
        cats = category_codes if category_codes is not None else self.category_codes

        # Data layer path: skip SQLite for projects with raw_data.xlsx
        if self.project_id:
            try:
                from lens.data_layer import project_has_raw_layer
                if project_has_raw_layer(self.project_id):
                    return self._get_brand_attr_matrix_from_layer()
            except Exception:
                pass

        conn = self._conn()

        # 2026-07-30: project_1 has a real category_code dimension (6 appliance categories x2
        # awareness/aided rows = 12 codes) — but projects onboarded via the generic ingestion
        # pipeline (lens/ingestion/generic_loader.py) never populate category_code at all (most
        # brand-health clients survey ONE category, not project_1's multi-category structure).
        # Applying project_1's hardcoded category filter to such a project's fact_brand_imagery
        # zeroes out every row (category_code IS NULL everywhere) even though real imagery data
        # exists. Detect that case and treat the category filter as not applicable, rather than
        # silently reporting "no data" for a perfectly good single-category project.
        if cats:
            _has_category_dim = conn.execute(
                "SELECT 1 FROM fact_brand_imagery WHERE category_code IS NOT NULL LIMIT 1"
            ).fetchone()
            if not _has_category_dim:
                cats = None

        # Demographic join needed if any filter active
        _demo_filters = [
            ("fr.zone",     self.zone),
            ("fr.gender",   self.gender),
            ("fr.age_band", self.age_band),
            ("fr.city",     self.city),
        ]
        _has_demo = any(v for _, v in _demo_filters)
        _demo_join = (
            "JOIN fact_respondents fr ON fr.respondent_id = fbi.respondent_id"
            if _has_demo else ""
        )

        # ── base query: join imagery + dim tables ─────────────────────────
        parts  = ["WHERE fbi.value = 1"]
        params: list = []

        if cats:
            placeholders = ",".join("?" * len(cats))
            parts.append(f"AND fbi.category_code IN ({placeholders})")
            params.extend(cats)
        if brand_ids:
            placeholders = ",".join("?" * len(brand_ids))
            parts.append(f"AND fbi.brand_id IN ({placeholders})")
            params.extend(brand_ids)
        if attr_ids:
            placeholders = ",".join("?" * len(attr_ids))
            parts.append(f"AND fbi.attr_id IN ({placeholders})")
            params.extend(attr_ids)
        if attr_section:
            parts.append("AND da.broad_feature = ?")
            params.append(attr_section)
        for col, val in _demo_filters:
            if val:
                parts.append(f"AND {col} = ?")
                params.append(val)

        # Awareness gate: restrict to respondents who were asked about each brand (fixes GAP 3)
        _awareness_stage_params: list = []
        if awareness_stages:
            stage_ph = ",".join("?" * len(awareness_stages))
            parts.append(
                f"AND EXISTS (SELECT 1 FROM fact_brand_awareness aw "
                f"WHERE aw.respondent_id=fbi.respondent_id AND aw.brand_id=fbi.brand_id "
                f"AND aw.stage IN ({stage_ph}))"
            )
            _awareness_stage_params = list(awareness_stages)
            params.extend(_awareness_stage_params)

        where = " ".join(parts)

        # Respondents who gave a positive association for each (brand, attr)
        assoc_sql = f"""
            SELECT
                db.brand_name,
                da.attr_label,
                fbi.brand_id,
                fbi.attr_id,
                COUNT(DISTINCT fbi.respondent_id) AS assoc_n
            FROM fact_brand_imagery fbi
            JOIN dim_brand         db  ON db.brand_id  = fbi.brand_id
            JOIN dim_bq3_attribute da  ON da.attr_id   = fbi.attr_id
            {_demo_join}
            {where}
            GROUP BY fbi.brand_id, fbi.attr_id, db.brand_name, da.attr_label
        """

        # Total respondents who answered each attribute question
        # (value can be 0 or 1 — both rows count as "answered")
        # Denominator: total unique respondents who SAW each attribute
        # (independent of brand choice) — group by attr_id ONLY.
        denom_parts  = ["WHERE 1=1"]
        denom_params: list = []
        if cats:
            placeholders = ",".join("?" * len(cats))
            denom_parts.append(f"AND fbi.category_code IN ({placeholders})")
            denom_params.extend(cats)
        if attr_ids:
            placeholders = ",".join("?" * len(attr_ids))
            denom_parts.append(f"AND fbi.attr_id IN ({placeholders})")
            denom_params.extend(attr_ids)
        if attr_section:
            denom_parts.append("AND da.broad_feature = ?")
            denom_params.append(attr_section)
        for col, val in _demo_filters:
            if val:
                denom_parts.append(f"AND {col} = ?")
                denom_params.append(val)

        # Mirror the awareness gate in the denominator so base rates stay consistent
        if awareness_stages:
            stage_ph = ",".join("?" * len(awareness_stages))
            denom_parts.append(
                f"AND EXISTS (SELECT 1 FROM fact_brand_awareness aw "
                f"WHERE aw.respondent_id=fbi.respondent_id "
                f"AND aw.stage IN ({stage_ph}))"
            )
            denom_params.extend(list(awareness_stages))

        denom_where  = " ".join(denom_parts)
        denom_has_da = attr_section is not None
        denom_join   = "JOIN dim_bq3_attribute da ON da.attr_id = fbi.attr_id" if denom_has_da else ""
        # Add demo join to denom if needed
        denom_demo_join = (
            "JOIN fact_respondents fr ON fr.respondent_id = fbi.respondent_id"
            if _has_demo else ""
        )

        denom_sql = f"""
            SELECT
                fbi.attr_id,
                COUNT(DISTINCT fbi.respondent_id) AS total_n
            FROM fact_brand_imagery fbi
            {denom_join}
            {denom_demo_join}
            {denom_where}
            GROUP BY fbi.attr_id
        """

        # Brand-level denominators (also scoped to same demographics)
        brand_denom_parts  = ["WHERE 1=1"]
        brand_denom_params: list = []
        if cats:
            placeholders = ",".join("?" * len(cats))
            brand_denom_parts.append(f"AND fbi.category_code IN ({placeholders})")
            brand_denom_params.extend(cats)
        for col, val in _demo_filters:
            if val:
                brand_denom_parts.append(f"AND {col} = ?")
                brand_denom_params.append(val)
        brand_denom_where = " ".join(brand_denom_parts)
        brand_denom_sql = f"""
            SELECT fbi.brand_id, COUNT(DISTINCT fbi.respondent_id) AS total_brand_n
            FROM fact_brand_imagery fbi
            {denom_demo_join}
            {brand_denom_where}
            GROUP BY fbi.brand_id
        """

        assoc_df = pd.read_sql_query(assoc_sql, conn, params=params)
        denom_df = pd.read_sql_query(denom_sql, conn, params=denom_params)
        brand_denom_df = pd.read_sql_query(brand_denom_sql, conn, params=brand_denom_params)
        conn.close()

        if assoc_df.empty:
            return pd.DataFrame()

        # Merge on attr_id ONLY (denom is per-attribute, not per brand×attr)
        merged = assoc_df.merge(
            denom_df, on="attr_id", how="left"
        )
        merged["pct"] = _safe_div(
            merged["assoc_n"].values.astype(float),
            merged["total_n"].values.astype(float),
        ) * 100.0

        # Pivot to brand × attr
        matrix = merged.pivot_table(
            index="brand_name", columns="attr_label",
            values="pct", aggfunc="mean", fill_value=0.0
        )

        # ── apply top_brands / top_attrs filters ──────────────────────────
        # Sort brands by total association weight, keep top_brands
        brand_totals = matrix.sum(axis=1).sort_values(ascending=False)

        # Also enforce min_respondents: use brand_denom_df
        brand_resp = brand_denom_df.set_index("brand_id")["total_brand_n"]
        # Map brand_id → brand_name
        brand_id_map = merged[["brand_name", "brand_id"]].drop_duplicates()
        id_to_name   = dict(zip(brand_id_map["brand_id"], brand_id_map["brand_name"]))
        brand_resp.index = brand_resp.index.map(id_to_name)

        eligible_brands = brand_resp[brand_resp >= self.min_respondents].index
        brand_totals    = brand_totals[brand_totals.index.isin(eligible_brands)]
        top_brand_names = brand_totals.head(self.top_brands).index.tolist()

        # Sort attrs by total association weight, keep top_attrs
        attr_totals     = matrix.sum(axis=0).sort_values(ascending=False)
        top_attr_names  = attr_totals.head(self.top_attrs).index.tolist()

        matrix = matrix.loc[
            [b for b in top_brand_names if b in matrix.index],
            [a for a in top_attr_names  if a in matrix.columns],
        ]

        # Drop zero-variance attributes (all brands identical → no info)
        matrix = matrix.loc[:, matrix.std(axis=0) > 0]

        # Drop brands with ALL-zero associations in the selected attribute set.
        # This happens in "All" category where cross-category brands (e.g. water
        # pump brands) have no positive associations with ceiling-fan-dominant
        # attributes. All-zero rows corrupt the CA SVD decomposition.
        matrix = matrix.loc[matrix.sum(axis=1) > 0]

        return matrix

    # ─────────────────────────────────────────────────────────────────────
    # Public: CA decomposition
    # ─────────────────────────────────────────────────────────────────────

    def fit(self, matrix: pd.DataFrame) -> dict:
        """
        Run Correspondence Analysis on a brand × attribute % matrix.

        Stores results internally and returns the full results dict
        matching XLSTAT output structure.

        Parameters
        ----------
        matrix : pd.DataFrame
            Brand × attribute % matrix (from get_brand_attr_matrix or custom).
            All values must be ≥ 0.

        Returns
        -------
        dict with keys:
            contingency_table   pd.DataFrame  — input % matrix
            chi2_test           dict          — chi2, df, p_value, critical, alpha
            total_inertia       float
            eigenvalues         pd.DataFrame  — F1..Fk: eigenvalue, %inertia, cum%
            row_results         dict          — all brand CA statistics
            col_results         dict          — all attribute CA statistics
        """
        if matrix.empty:
            raise ValueError("Input matrix is empty — no data to analyse.")

        N = matrix.values.astype(float)
        brand_names = list(matrix.index)
        attr_names  = list(matrix.columns)
        n_rows, n_cols = N.shape

        # ── 1. Correspondence matrix ──────────────────────────────────────
        total = N.sum()
        if total == 0:
            raise ValueError("Matrix sums to zero — check data extraction.")
        P = N / total

        r = P.sum(axis=1)   # row masses  (n_rows,)
        c = P.sum(axis=0)   # col masses  (n_cols,)

        # ── 3. Standardised residuals matrix ─────────────────────────────
        Dr_sqrt_inv = np.diag(1.0 / np.sqrt(np.where(r > 0, r, 1e-12)))
        Dc_sqrt_inv = np.diag(1.0 / np.sqrt(np.where(c > 0, c, 1e-12)))

        S = Dr_sqrt_inv @ (P - np.outer(r, c)) @ Dc_sqrt_inv

        # ── 4. SVD ────────────────────────────────────────────────────────
        # Full SVD; drop last trivial singular value (rank = min-1)
        U, d, Vt = np.linalg.svd(S, full_matrices=False)

        # Truncate to min(n_rows, n_cols) - 1 dimensions
        k = min(n_rows, n_cols) - 1
        U  = U[:, :k]
        d  = d[:k]
        Vt = Vt[:k, :]

        # Sign normalisation — match XLSTAT convention:
        # for each factor, flip so the element with largest absolute value
        # in that column of U is POSITIVE (deterministic, reproducible).
        for j in range(k):
            max_idx = np.argmax(np.abs(U[:, j]))
            if U[max_idx, j] < 0:
                U[:, j]  *= -1
                Vt[j, :] *= -1

        eigenvalues   = d ** 2
        total_inertia = float(eigenvalues.sum())

        # ── 2. Chi-square test ───────────────────────────────────────────
        grand_total = N.sum()
        chi2_stat   = grand_total * total_inertia
        dof         = (n_rows - 1) * (n_cols - 1)
        p_val       = float(1.0 - stats.chi2.cdf(chi2_stat, dof)) if dof > 0 else 1.0
        chi2_crit   = float(stats.chi2.ppf(0.95, dof)) if dof > 0 else 0.0
        alpha       = 0.05

        chi2_test = {
            "chi2":       round(float(chi2_stat), 4),
            "df":         int(dof),
            "p_value":    round(float(p_val), 6),
            "critical":   round(float(chi2_crit), 4),
            "alpha":      alpha,
            "significant": bool(p_val < alpha),
        }

        # ── 4. Continuation (inertia percentages) ───────────────────────
        pct_inertia   = eigenvalues / total_inertia * 100 if total_inertia > 0 else eigenvalues * 0
        cum_pct       = np.cumsum(pct_inertia)

        eig_df = pd.DataFrame({
            "Factor":       [f"F{i+1}" for i in range(k)],
            "Eigenvalue":   np.round(eigenvalues, 10),
            "Inertia_%":    np.round(pct_inertia, 4),
            "Cum_Inertia_%": np.round(cum_pct, 4),
        }).set_index("Factor")

        # ── 5. Row (brand) coordinates ────────────────────────────────────
        Dr_sqrt_inv_vec = 1.0 / np.sqrt(np.where(r > 0, r, 1e-12))
        Dc_sqrt_inv_vec = 1.0 / np.sqrt(np.where(c > 0, c, 1e-12))

        # Principal coords:  F = Dr^{-1/2} U diag(d)
        F_rows = (Dr_sqrt_inv_vec[:, None] * U) * d[None, :]
        # Standard coords:   A = Dr^{-1/2} U
        A_rows = Dr_sqrt_inv_vec[:, None] * U

        # ── 6. Column (attribute) coordinates ────────────────────────────
        # Principal coords:  G = Dc^{-1/2} Vt' diag(d)
        F_cols = (Dc_sqrt_inv_vec[:, None] * Vt.T) * d[None, :]
        # Standard coords:   B = Dc^{-1/2} Vt'
        A_cols = Dc_sqrt_inv_vec[:, None] * Vt.T

        factor_labels = [f"F{i+1}" for i in range(k)]

        # ── 7. Row profiles (P / r) ───────────────────────────────────────
        row_profiles = _safe_div(P, r[:, None])   # shape (n_rows, n_cols)

        # ── 8. Column profiles (P / c) ────────────────────────────────────
        col_profiles = _safe_div(P, c[None, :])   # shape (n_rows, n_cols)
        # col profiles: each column of P divided by its mass
        col_profiles_T = _safe_div(P.T, c[:, None])  # shape (n_cols, n_rows)

        # ── 9. Chi-square distances between rows ─────────────────────────
        row_chisq = self._chisq_distances(row_profiles, c)
        col_chisq = self._chisq_distances(col_profiles_T, r)

        # ── 10. Distances from centroid ───────────────────────────────────
        row_sq_dist = np.array([
            np.sum(_safe_div((row_profiles[i] - c) ** 2, np.where(c > 0, c, 1e-12)))
            for i in range(n_rows)
        ])
        col_sq_dist = np.array([
            np.sum(_safe_div((col_profiles_T[j] - r) ** 2, np.where(r > 0, r, 1e-12)))
            for j in range(n_cols)
        ])

        row_dist = np.sqrt(np.clip(row_sq_dist, 0, None))
        col_dist = np.sqrt(np.clip(col_sq_dist, 0, None))

        # ── 11. Inertia per point ─────────────────────────────────────────
        row_inertia = r * row_sq_dist
        col_inertia = c * col_sq_dist

        row_rel_inertia = _safe_div(row_inertia, total_inertia) if total_inertia else row_inertia
        col_rel_inertia = _safe_div(col_inertia, total_inertia) if total_inertia else col_inertia

        # ── 12. Contributions (absolute) ─────────────────────────────────
        # CTR_i_k = r_i * F_ik^2 / lambda_k
        row_ctrs = np.zeros((n_rows, k))
        col_ctrs = np.zeros((n_cols, k))
        for ki in range(k):
            lam = eigenvalues[ki] if eigenvalues[ki] > 0 else 1e-12
            row_ctrs[:, ki] = r * F_rows[:, ki] ** 2 / lam
            col_ctrs[:, ki] = c * F_cols[:, ki] ** 2 / lam

        # ── 13. Squared cosines (quality of representation) ───────────────
        # COS2_ik = F_ik^2 / sq_dist_i
        row_cos2 = np.zeros((n_rows, k))
        col_cos2 = np.zeros((n_cols, k))
        for i in range(n_rows):
            denom = row_sq_dist[i] if row_sq_dist[i] > 0 else 1e-12
            row_cos2[i] = F_rows[i] ** 2 / denom
        for j in range(n_cols):
            denom = col_sq_dist[j] if col_sq_dist[j] > 0 else 1e-12
            col_cos2[j] = F_cols[j] ** 2 / denom

        # ── 14. Package results ───────────────────────────────────────────
        def _df_coords(arr, names, index_name):
            df = pd.DataFrame(
                np.round(arr, 6),
                index=names,
                columns=factor_labels,
            )
            df.index.name = index_name
            return df

        row_results = {
            "weights":          pd.Series(np.round(r, 6),             index=brand_names, name="weight"),
            "sq_distances":     pd.Series(np.round(row_sq_dist, 6),   index=brand_names, name="sq_dist"),
            "distances":        pd.Series(np.round(row_dist, 6),      index=brand_names, name="dist"),
            "inertia":          pd.Series(np.round(row_inertia, 6),   index=brand_names, name="inertia"),
            "rel_inertia":      pd.Series(np.round(row_rel_inertia,6),index=brand_names, name="rel_inertia"),
            "profiles":         pd.DataFrame(np.round(row_profiles, 6),index=brand_names, columns=attr_names),
            "chisq_distances":  pd.DataFrame(np.round(row_chisq, 6),  index=brand_names, columns=brand_names),
            "principal_coords": _df_coords(F_rows, brand_names, "brand"),
            "standard_coords":  _df_coords(A_rows, brand_names, "brand"),
            "contributions":    _df_coords(row_ctrs, brand_names, "brand"),
            "cos2":             _df_coords(row_cos2, brand_names, "brand"),
        }

        col_results = {
            "weights":          pd.Series(np.round(c, 6),             index=attr_names, name="weight"),
            "sq_distances":     pd.Series(np.round(col_sq_dist, 6),   index=attr_names, name="sq_dist"),
            "distances":        pd.Series(np.round(col_dist, 6),      index=attr_names, name="dist"),
            "inertia":          pd.Series(np.round(col_inertia, 6),   index=attr_names, name="inertia"),
            "rel_inertia":      pd.Series(np.round(col_rel_inertia,6),index=attr_names, name="rel_inertia"),
            "profiles":         pd.DataFrame(np.round(col_profiles_T, 6), index=attr_names, columns=brand_names),
            "chisq_distances":  pd.DataFrame(np.round(col_chisq, 6),  index=attr_names, columns=attr_names),
            "principal_coords": _df_coords(F_cols, attr_names, "attribute"),
            "standard_coords":  _df_coords(A_cols, attr_names, "attribute"),
            "contributions":    _df_coords(col_ctrs, attr_names, "attribute"),
            "cos2":             _df_coords(col_cos2, attr_names, "attribute"),
        }

        results = {
            "contingency_table": matrix.round(2),
            "chi2_test":         chi2_test,
            "total_inertia":     round(total_inertia, 6),
            "eigenvalues":       eig_df,
            "row_results":       row_results,
            "col_results":       col_results,
            # raw arrays for chart generation
            "_brand_names":      brand_names,
            "_attr_names":       attr_names,
            "_F_rows":           F_rows,
            "_F_cols":           F_cols,
            "_A_rows":           A_rows,
            "_A_cols":           A_cols,
            "_r":                r,
            "_c":                c,
            "_row_rel_inertia":  row_rel_inertia,
            "_col_rel_inertia":  col_rel_inertia,
            "_row_ctrs":         row_ctrs,
            "_col_ctrs":         col_ctrs,
            "_row_cos2":         row_cos2,
            "_col_cos2":         col_cos2,
            "_row_chisq":        row_chisq,
            "_eigenvalues":      eigenvalues,
            "_pct_inertia":      pct_inertia,
            "_cum_pct":          cum_pct,
            "_factor_labels":    factor_labels,
        }

        self._matrix  = matrix
        self._results = results
        return results

    # ─────────────────────────────────────────────────────────────────────
    # Public: convenience accessors
    # ─────────────────────────────────────────────────────────────────────

    def get_perceptual_map_data(self, n_dims: int = 2) -> dict:
        """
        Return brand + attribute coordinates for perceptual map rendering.

        Parameters
        ----------
        n_dims : int
            Number of CA dimensions to return (default 2, i.e. F1 + F2).

        Returns
        -------
        dict with keys:
            brands : list[dict]  — [{name, F1, F2, ..., weight, rel_inertia}]
            attrs  : list[dict]  — [{name, F1, F2, ..., weight, rel_inertia}]
            explained_variance : list[float]  — % inertia per factor
            total_inertia : float
        """
        self._require_fit()
        res      = self._results
        F_rows   = res["_F_rows"]
        F_cols   = res["_F_cols"]
        labels   = res["_factor_labels"]
        n_dims   = min(n_dims, F_rows.shape[1])

        brands = []
        for i, name in enumerate(res["_brand_names"]):
            entry = {"name": name,
                     "weight":      float(res["_r"][i]),
                     "rel_inertia": float(res["_row_rel_inertia"][i])}
            for d in range(n_dims):
                entry[labels[d]] = float(F_rows[i, d])
            brands.append(entry)

        attrs = []
        for j, name in enumerate(res["_attr_names"]):
            entry = {"name": name,
                     "weight":      float(res["_c"][j]),
                     "rel_inertia": float(res["_col_rel_inertia"][j])}
            for d in range(n_dims):
                entry[labels[d]] = float(F_cols[j, d])
            attrs.append(entry)

        return {
            "brands":             brands,
            "attrs":              attrs,
            "explained_variance": [float(v) for v in res["_pct_inertia"][:n_dims]],
            "total_inertia":      res["total_inertia"],
        }

    def get_full_ca_tables(self) -> dict:
        """
        Return all CA tables as serialisable dicts (DataFrames converted to records).

        Returns
        -------
        dict with keys:
            contingency_table, chi2_test, total_inertia, eigenvalues,
            row_results, col_results, row_chisq
        Each DataFrame is converted to a dict-of-records for JSON portability.
        """
        self._require_fit()
        res = self._results

        def _df_to_dict(df: pd.DataFrame) -> dict:
            return df.round(4).reset_index().to_dict(orient="records")

        def _series_to_dict(s: pd.Series) -> dict:
            return s.round(4).to_dict()

        def _row_res_to_serial(rr: dict) -> dict:
            out = {}
            for k, v in rr.items():
                if isinstance(v, pd.DataFrame):
                    out[k] = _df_to_dict(v)
                elif isinstance(v, pd.Series):
                    out[k] = _series_to_dict(v)
                else:
                    out[k] = v
            return out

        return {
            "contingency_table": _df_to_dict(res["contingency_table"]),
            "chi2_test":         res["chi2_test"],
            "total_inertia":     round(res["total_inertia"], 4),
            "eigenvalues":       _df_to_dict(res["eigenvalues"]),
            "row_results":       _row_res_to_serial(res["row_results"]),
            "col_results":       _row_res_to_serial(res["col_results"]),
            "row_chisq":         _df_to_dict(res["row_results"]["chisq_distances"]),
        }

    # ─────────────────────────────────────────────────────────────────────
    # Public: chart specs
    # ─────────────────────────────────────────────────────────────────────

    def get_chart_specs(self) -> list:
        """
        Generate all Plotly chart specs as JSON-serialisable dicts.

        Charts produced:
          1. Symmetric perceptual map  (F1 × F2, brands squares + attrs circles)
          2. Asymmetric perceptual map (brands principal + attrs standard coords)
          3. Scree plot                (% inertia bar + cumulative line)
          4. Brand contributions F1   (horizontal bar)
          5. Brand contributions F2   (horizontal bar)
          6. Squared cosines heatmap  (brand × factor)
          7. Chi-sq distance heatmap  (brand × brand)
          8. Row profiles radar       (each brand vs mean)
          9. Brand trajectory plot    (F1 coords across categories if multi-cat)
         10. Biplot with confidence ellipses (brands principal + 95% CI)

        Returns
        -------
        list[dict]
            Each element has "type", "title", "figure" (Plotly fig dict).
        """
        self._require_fit()
        if not _PLOTLY_OK:
            return [{"error": "plotly not installed"}]

        specs = []
        specs.append(self._chart_symmetric_map())
        specs.append(self._chart_asymmetric_map())
        specs.append(self._chart_scree())
        specs.append(self._chart_contribution_bars("F1"))
        specs.append(self._chart_contribution_bars("F2"))
        specs.append(self._chart_contribution_cols("F1"))
        specs.append(self._chart_cos2_heatmap())
        specs.append(self._chart_chisq_heatmap())
        specs.append(self._chart_row_profiles_radar())
        specs.append(self._chart_brand_trajectory())
        specs.append(self._chart_biplot_ellipses())
        return specs

    # ─────────────────────────────────────────────────────────────────────
    # Private: chi-square distance computation
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _chisq_distances(profiles: np.ndarray, masses: np.ndarray) -> np.ndarray:
        """
        Compute chi-square distance matrix between rows of `profiles`.

        d²(i,i') = Σ_j  (p_ij - p_i'j)² / c_j

        Parameters
        ----------
        profiles : np.ndarray  shape (n, m)
        masses   : np.ndarray  shape (m,)  — column masses (denominator)

        Returns
        -------
        np.ndarray shape (n, n) — symmetric distance matrix
        """
        n = profiles.shape[0]
        D = np.zeros((n, n))
        inv_masses = _safe_div(np.ones_like(masses), masses)
        for i in range(n):
            diff = profiles[i] - profiles  # (n, m)
            D[i] = np.sum(diff ** 2 * inv_masses[None, :], axis=1)
        return np.sqrt(np.clip(D, 0, None))

    # ─────────────────────────────────────────────────────────────────────
    # Private: guard
    # ─────────────────────────────────────────────────────────────────────

    def _require_fit(self):
        if self._results is None:
            raise RuntimeError("Call fit() before accessing results or charts.")

    # ─────────────────────────────────────────────────────────────────────
    # Private: chart builders
    # ─────────────────────────────────────────────────────────────────────

    def _axis_poles(self, factor_idx: int, n: int = 2):
        """Return (neg_attrs, pos_attrs) — the attribute names defining each pole of
        a CA factor. These translate an abstract F1/F2 axis into plain meaning for
        an executive reader (left side = X, right side = Y)."""
        try:
            res = self._results
            attr_names = res["_attr_names"]
            F_cols = res["_F_cols"]
            coords = [(attr_names[j], float(F_cols[j, factor_idx]))
                      for j in range(len(attr_names))]
            coords.sort(key=lambda x: x[1])
            def _short(s, m=18):
                s = str(s)
                return s[:m] + "…" if len(s) > m else s
            neg = [_short(c[0]) for c in coords[:n] if c[1] < 0]
            pos = [_short(c[0]) for c in reversed(coords[-n:]) if c[1] > 0]
            return neg, pos
        except Exception:
            return [], []

    def _axis_label(self, factor_idx: int) -> str:
        """Executive-readable axis label, e.g.
        'F1 · 42.3% inertia   ◀ Affordable, Basic   |   Premium, Durable ▶'"""
        pct = self._results["_pct_inertia"]
        lbl = self._results["_factor_labels"][factor_idx]
        base = f"{lbl} · {pct[factor_idx]:.1f}% inertia"
        neg, pos = self._axis_poles(factor_idx)
        if neg or pos:
            neg_s = ", ".join(neg) if neg else "—"
            pos_s = ", ".join(pos) if pos else "—"
            return f"{base}    ◀ {neg_s}   |   {pos_s} ▶"
        return base

    @staticmethod
    def _label_position(f1: float, f2: float) -> str:
        """Place label in opposite quadrant from origin relative to point."""
        ang = np.degrees(np.arctan2(f2, f1))
        # Divide into 8 sectors for more variety
        if -22.5 <= ang < 22.5:   return "middle right"
        elif 22.5 <= ang < 67.5:  return "top right"
        elif 67.5 <= ang < 112.5: return "top center"
        elif 112.5 <= ang < 157.5:return "top left"
        elif ang >= 157.5 or ang < -157.5: return "middle left"
        elif -157.5 <= ang < -112.5: return "bottom left"
        elif -112.5 <= ang < -67.5:  return "bottom center"
        else:                         return "bottom right"

    def _chart_symmetric_map(self) -> dict:
        """
        Symmetric biplot: brands (squares) + attributes (circles) on F1 × F2.
        Top-8 attributes labeled with arrows connecting label to point.
        All attribute dots hoverable. Brand labels with colored badges.
        """
        res          = self._results
        brand_names  = res["_brand_names"]
        attr_names   = res["_attr_names"]
        F_rows       = res["_F_rows"]
        F_cols       = res["_F_cols"]
        row_ri       = res["_row_rel_inertia"]
        col_ri       = res["_col_rel_inertia"]

        max_row_ri = float(row_ri.max()) if row_ri.max() > 0 else 1.0
        max_col_ri = float(col_ri.max()) if col_ri.max() > 0 else 1.0

        N_LABELED = min(8, len(attr_names))
        top_attr_idx = set(np.argsort(col_ri)[::-1][:N_LABELED].tolist())

        def _trunc(s, n=22):
            return s[:n] + "…" if len(s) > n else s

        traces = []

        # ── All attribute dots: single trace, coloured by top/non-top ─────────
        all_attr_cd = [[attr_names[j], float(F_cols[j, 0]), float(F_cols[j, 1]),
                        float(col_ri[j]) * 100]
                       for j in range(len(attr_names))]
        attr_sizes  = [max(7, min(15, 5 + 10 * float(col_ri[j]) / max_col_ri))
                       for j in range(len(attr_names))]
        attr_opac   = [1.0 if j in top_attr_idx else 0.50 for j in range(len(attr_names))]
        attr_colors = ["#3b82f6" if j in top_attr_idx else "#94a3b8"
                       for j in range(len(attr_names))]

        traces.append(go.Scatter(
            x=[float(F_cols[j, 0]) for j in range(len(attr_names))],
            y=[float(F_cols[j, 1]) for j in range(len(attr_names))],
            mode="markers",
            name="Attributes (hover for all)",
            marker=dict(symbol="circle-open-dot", size=attr_sizes, color=attr_colors,
                        opacity=attr_opac, line=dict(width=1.5, color=attr_colors)),
            customdata=all_attr_cd,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
            ),
            showlegend=True,
        ))

        # ── Brand traces: coloured squares, marker-only (labels via annotations) ──
        for i, name in enumerate(brand_names):
            colour  = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
            f1, f2  = float(F_rows[i, 0]), float(F_rows[i, 1])
            ri_norm = float(row_ri[i]) / max_row_ri
            msize   = max(12, 10 + 14 * ri_norm)

            traces.append(go.Scatter(
                x=[f1], y=[f2],
                mode="markers",
                name=name,
                marker=dict(symbol="square", size=msize, color=colour,
                            opacity=0.92,
                            line=dict(width=2.5, color="white")),
                customdata=[[name, f1, f2, float(row_ri[i]) * 100]],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                    "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
                ),
                showlegend=True,
            ))

        # Axis range with generous padding for labels
        all_x = np.concatenate([F_rows[:, 0], F_cols[:, 0]])
        all_y = np.concatenate([F_rows[:, 1], F_cols[:, 1]])
        xspan = float(all_x.max() - all_x.min())
        yspan = float(all_y.max() - all_y.min())
        pad_x = max(0.25 * xspan, 0.08)
        pad_y = max(0.28 * yspan, 0.08)
        x_range = [float(all_x.min()) - pad_x, float(all_x.max()) + pad_x]
        y_range = [float(all_y.min()) - pad_y, float(all_y.max()) + pad_y]

        # Axis cross-lines (subtle)
        traces.append(go.Scatter(x=x_range, y=[0, 0], mode="lines",
            line=dict(color="#dde1e9", width=1.2),
            showlegend=False, hoverinfo="skip"))
        traces.append(go.Scatter(x=[0, 0], y=y_range, mode="lines",
            line=dict(color="#dde1e9", width=1.2),
            showlegend=False, hoverinfo="skip"))

        fig = go.Figure(data=traces)

        # ── Faint 4-quadrant tints — partition the perceptual space for the eye ──
        _q_tints = [
            (0, x_range[1], 0, y_range[1], "rgba(16,185,129,0.045)"),   # top-right
            (x_range[0], 0, 0, y_range[1], "rgba(56,189,248,0.045)"),   # top-left
            (x_range[0], 0, y_range[0], 0, "rgba(245,158,11,0.045)"),   # bottom-left
            (0, x_range[1], y_range[0], 0, "rgba(167,139,250,0.045)"),  # bottom-right
        ]
        for _x0, _x1, _y0, _y1, _fill in _q_tints:
            fig.add_shape(type="rect", x0=_x0, x1=_x1, y0=_y0, y1=_y1,
                          fillcolor=_fill, line=dict(width=0), layer="below")

        # ── Brand labels: colored pill with white bg, no arrow (brands are big squares) ──
        for i, name in enumerate(brand_names):
            colour = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
            f1, f2 = float(F_rows[i, 0]), float(F_rows[i, 1])
            anc_h = "left"  if f1 >= 0 else "right"
            anc_v = "bottom" if f2 >= 0 else "top"
            ox = 14 if f1 >= 0 else -14
            oy = 12 if f2 >= 0 else -12
            fig.add_annotation(
                x=f1, y=f2, text=f"<b>{name}</b>",
                showarrow=False,
                xanchor=anc_h, yanchor=anc_v,
                xshift=ox, yshift=oy,
                font=dict(size=11, color=colour),
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor=colour, borderwidth=1.5,
                borderpad=3,
            )

        # ── Top-N attribute labels with small arrows pointing to dots ─────────
        for j in sorted(top_attr_idx):
            af1, af2 = float(F_cols[j, 0]), float(F_cols[j, 1])
            # Place label text away from the point
            sign_x = 1 if af1 >= 0 else -1
            sign_y = 1 if af2 >= 0 else -1
            ax_off = sign_x * max(0.04 * (x_range[1] - x_range[0]), 0.06)
            ay_off = sign_y * max(0.04 * (y_range[1] - y_range[0]), 0.06)
            fig.add_annotation(
                x=af1, y=af2,
                ax=af1 + ax_off, ay=af2 + ay_off,
                text=f"<i>{_trunc(attr_names[j])}</i>",
                xanchor="center", yanchor="middle",
                axref="x", ayref="y",
                showarrow=True,
                arrowhead=2, arrowsize=0.9, arrowwidth=1.4,
                arrowcolor="#3b82f6",
                font=dict(size=9, color="#1e3a8a"),
                bgcolor="rgba(219,234,254,0.93)",
                bordercolor="#93c5fd", borderwidth=1,
                borderpad=3,
            )

        fig.update_layout(
            paper_bgcolor=_BG_COLOUR,
            plot_bgcolor="#fafcff",
            font=dict(family="Inter, sans-serif", size=12, color="#1e293b"),
            margin=dict(l=80, r=60, t=80, b=100),
            height=870,
            title=dict(
                text=(
                    f"<b>Symmetric Perceptual Map</b>"
                    f"<br><sup>Principal coordinates · Top {N_LABELED} attributes labeled · hover all dots</sup>"
                ),
                font=dict(size=14, color="#0f172a"),
                x=0.5, xanchor="center",
            ),
            xaxis=dict(
                title=dict(text=self._axis_label(0), font=dict(size=11, color="#64748b")),
                zeroline=False,
                gridcolor="#eef2f7", gridwidth=1,
                tickfont=dict(size=10, color="#94a3b8"),
                linecolor="#e2e8f0", showline=True, mirror=True,
                range=x_range,
            ),
            yaxis=dict(
                title=dict(text=self._axis_label(1), font=dict(size=11, color="#64748b")),
                zeroline=False,
                gridcolor="#eef2f7", gridwidth=1,
                tickfont=dict(size=10, color="#94a3b8"),
                linecolor="#e2e8f0", showline=True, mirror=True,
                range=y_range,
            ),
            legend=dict(
                font=dict(size=9), itemsizing="constant",
                orientation="v", x=1.02, y=0.5, xanchor="left", yanchor="middle",
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor="#e2e8f0", borderwidth=1,
            ),
            hoverlabel=dict(bgcolor="white", font_size=11,
                            bordercolor="#e2e8f0"),
        )
        return {"type": "symmetric_map", "title": "Symmetric Perceptual Map",
                "figure": fig.to_dict()}

    def _chart_asymmetric_map(self) -> dict:
        """
        Asymmetric biplot: brands on principal coords (F), attributes on scaled standard coords (A).
        Attributes scaled to same display range as brands for visual balance.
        Top-8 attributes labeled with arrow annotations. Horizontal legend.
        """
        res         = self._results
        brand_names = res["_brand_names"]
        attr_names  = res["_attr_names"]
        F_rows      = res["_F_rows"]
        A_cols      = res["_A_cols"]
        row_ri      = res["_row_rel_inertia"]
        col_ri      = res["_col_rel_inertia"]

        max_row_ri = float(row_ri.max()) if row_ri.max() > 0 else 1.0
        max_col_ri = float(col_ri.max()) if col_ri.max() > 0 else 1.0

        N_LABELED = min(8, len(attr_names))
        top_attr_idx = set(np.argsort(col_ri)[::-1][:N_LABELED].tolist())

        def _trunc(s, n=22):
            return s[:n] + "…" if len(s) > n else s

        # Scale A_cols to match display range of F_rows
        f_scale = float(np.abs(F_rows).max()) if np.abs(F_rows).max() > 0 else 1.0
        a_scale = float(np.abs(A_cols).max()) if np.abs(A_cols).max() > 0 else 1.0
        scale_factor = min(f_scale / a_scale if a_scale > 0 else 1.0, 2.5)
        A_vis = A_cols * scale_factor  # visual-only; hover shows actual std coords

        traces = []

        # ── Attribute dots: two groups (top/other) for legend clarity ─────────
        all_attr_cd = [[attr_names[j],
                        float(A_cols[j, 0]), float(A_cols[j, 1]),
                        float(col_ri[j]) * 100]
                       for j in range(len(attr_names))]
        attr_sizes  = [max(8, min(14, 5 + 9 * float(col_ri[j]) / max_col_ri))
                       if j in top_attr_idx else 6
                       for j in range(len(attr_names))]
        attr_opac   = [1.0 if j in top_attr_idx else 0.40 for j in range(len(attr_names))]
        attr_colors = ["#7c3aed" if j in top_attr_idx else "#c4b5fd"
                       for j in range(len(attr_names))]

        traces.append(go.Scatter(
            x=[float(A_vis[j, 0]) for j in range(len(attr_names))],
            y=[float(A_vis[j, 1]) for j in range(len(attr_names))],
            mode="markers",
            name="Attributes (std coord, scaled)",
            marker=dict(symbol="diamond-open-dot", size=attr_sizes, color=attr_colors,
                        opacity=attr_opac, line=dict(width=1.5, color=attr_colors)),
            customdata=all_attr_cd,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Std F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
            ),
            showlegend=True,
        ))

        # ── Brand traces: principal coords, coloured squares ──────────────────
        for i, name in enumerate(brand_names):
            colour  = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
            f1, f2  = float(F_rows[i, 0]), float(F_rows[i, 1])
            ri_norm = float(row_ri[i]) / max_row_ri
            msize   = max(12, 10 + 14 * ri_norm)

            traces.append(go.Scatter(
                x=[f1], y=[f2],
                mode="markers",
                name=name,
                marker=dict(symbol="square", size=msize, color=colour,
                            opacity=0.92, line=dict(width=2.5, color="white")),
                customdata=[[name, f1, f2, float(row_ri[i]) * 100]],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                    "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
                ),
                showlegend=True,
            ))

        # Axis range with generous padding
        all_x = np.concatenate([F_rows[:, 0], A_vis[:, 0]])
        all_y = np.concatenate([F_rows[:, 1], A_vis[:, 1]])
        xspan = float(all_x.max() - all_x.min())
        yspan = float(all_y.max() - all_y.min())
        pad_x = max(0.25 * xspan, 0.08)
        pad_y = max(0.28 * yspan, 0.08)
        xr = [float(all_x.min()) - pad_x, float(all_x.max()) + pad_x]
        yr = [float(all_y.min()) - pad_y, float(all_y.max()) + pad_y]

        traces.append(go.Scatter(x=xr, y=[0, 0], mode="lines",
            line=dict(color="#dde1e9", width=1.2),
            showlegend=False, hoverinfo="skip"))
        traces.append(go.Scatter(x=[0, 0], y=yr, mode="lines",
            line=dict(color="#dde1e9", width=1.2),
            showlegend=False, hoverinfo="skip"))

        fig = go.Figure(data=traces)

        # ── Brand labels: colored pills ────────────────────────────────────────
        for i, name in enumerate(brand_names):
            colour = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
            f1, f2 = float(F_rows[i, 0]), float(F_rows[i, 1])
            anc_h = "left"  if f1 >= 0 else "right"
            anc_v = "bottom" if f2 >= 0 else "top"
            fig.add_annotation(
                x=f1, y=f2, text=f"<b>{name}</b>",
                showarrow=False,
                xanchor=anc_h, yanchor=anc_v,
                xshift=14 if f1 >= 0 else -14,
                yshift=12 if f2 >= 0 else -12,
                font=dict(size=11, color=colour),
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor=colour, borderwidth=1.5,
                borderpad=3,
            )

        # ── Top-N attribute labels with arrows ────────────────────────────────
        for j in sorted(top_attr_idx):
            av1, av2 = float(A_vis[j, 0]), float(A_vis[j, 1])
            sign_x = 1 if av1 >= 0 else -1
            sign_y = 1 if av2 >= 0 else -1
            ax_off = sign_x * max(0.04 * (xr[1] - xr[0]), 0.06)
            ay_off = sign_y * max(0.04 * (yr[1] - yr[0]), 0.06)
            fig.add_annotation(
                x=av1, y=av2,
                ax=av1 + ax_off, ay=av2 + ay_off,
                text=f"<i>{_trunc(attr_names[j])}</i>",
                xanchor="center", yanchor="middle",
                axref="x", ayref="y",
                showarrow=True,
                arrowhead=2, arrowsize=0.9, arrowwidth=1.4,
                arrowcolor="#7c3aed",
                font=dict(size=9, color="#4c1d95"),
                bgcolor="rgba(237,233,254,0.93)",
                bordercolor="#a78bfa", borderwidth=1,
                borderpad=3,
            )

        fig.update_layout(
            paper_bgcolor=_BG_COLOUR,
            plot_bgcolor="#fafcff",
            font=dict(family="Inter, sans-serif", size=12, color="#1e293b"),
            margin=dict(l=80, r=60, t=90, b=100),
            height=870,
            title=dict(
                text=(
                    f"<b>Asymmetric Perceptual Map</b>"
                    f"<br><sup>Brands on principal coords · Attributes on scaled std coords · Top {N_LABELED} labeled</sup>"
                ),
                font=dict(size=14, color="#0f172a"),
                x=0.5, xanchor="center",
            ),
            xaxis=dict(
                title=dict(text=self._axis_label(0), font=dict(size=11, color="#64748b")),
                zeroline=False,
                gridcolor="#eef2f7", gridwidth=1,
                tickfont=dict(size=10, color="#94a3b8"),
                linecolor="#e2e8f0", showline=True, mirror=True,
                range=xr,
            ),
            yaxis=dict(
                title=dict(text=self._axis_label(1), font=dict(size=11, color="#64748b")),
                zeroline=False,
                gridcolor="#eef2f7", gridwidth=1,
                tickfont=dict(size=10, color="#94a3b8"),
                linecolor="#e2e8f0", showline=True, mirror=True,
                range=yr,
            ),
            legend=dict(
                font=dict(size=9), itemsizing="constant",
                orientation="v", x=1.02, y=0.5, xanchor="left", yanchor="middle",
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor="#e2e8f0", borderwidth=1,
            ),
            hoverlabel=dict(bgcolor="white", font_size=11, bordercolor="#e2e8f0"),
        )
        return {"type": "asymmetric_map", "title": "Asymmetric Perceptual Map",
                "figure": fig.to_dict()}

    def _chart_scree(self) -> dict:
        """Scree plot: % inertia per factor (bars) + cumulative % (line)."""
        res     = self._results
        factors = res["_factor_labels"]
        pct     = [float(v) for v in res["_pct_inertia"]]
        cum     = [float(v) for v in res["_cum_pct"]]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=factors, y=pct,
            name="% Inertia",
            marker_color="#4F81BD",
            text=[f"{v:.1f}%" for v in pct],
            textposition="outside",
        ))
        fig.add_trace(go.Scatter(
            x=factors, y=cum,
            name="Cumulative %",
            mode="lines+markers+text",
            line=dict(color="#C0504D", width=2),
            marker=dict(size=7),
            text=[f"{v:.1f}%" for v in cum],
            textposition="top center",
            textfont=dict(size=10, color="#C0504D"),
            yaxis="y2",
        ))
        fig.update_layout(
            **_PLOTLY_LAYOUT_BASE,
            title=dict(text="Scree Plot — Inertia by Factor", font=dict(size=16)),
            xaxis=dict(title="Factor"),
            yaxis=dict(title="% Inertia", range=[0, max(pct) * 1.3], gridcolor=_GRID_COLOUR),
            yaxis2=dict(
                title="Cumulative %", range=[0, 115],
                overlaying="y", side="right",
                showgrid=False,
            ),
            legend=dict(x=0.7, y=0.1),
        )
        return {"type": "scree", "title": "Scree Plot", "figure": fig.to_dict()}

    def _chart_contribution_bars(self, factor: str = "F1") -> dict:
        """Horizontal bar chart of brand contributions to a given factor."""
        res     = self._results
        names   = res["_brand_names"]
        labels  = res["_factor_labels"]

        if factor not in labels:
            factor = labels[0]
        fi      = labels.index(factor)
        ctrs    = res["_row_ctrs"][:, fi]

        # Sort descending for readability
        order   = np.argsort(ctrs)[::-1]
        sorted_names = [names[i] for i in order]
        sorted_vals  = [float(ctrs[i] * 100) for i in order]   # display as %
        colours = [_BRAND_COLOURS[i % len(_BRAND_COLOURS)] for i in order]

        fig = go.Figure(go.Bar(
            x=sorted_vals, y=sorted_names,
            orientation="h",
            marker_color=colours,
            text=[f"{v:.1f}%" for v in sorted_vals],
            textposition="outside",
            textfont=dict(size=12),
            hovertemplate="<b>%{y}</b><br>Contribution: %{x:.2f}%<extra></extra>",
        ))
        fig.update_layout(
            **_PLOTLY_LAYOUT_BASE,
            title=dict(text=f"Brand Contributions to {factor}", font=dict(size=16)),
            xaxis=dict(title=f"Contribution to {factor} (%)", gridcolor=_GRID_COLOUR,
                       range=[0, max(sorted_vals) * 1.25]),
            yaxis=dict(autorange="reversed", tickfont=dict(size=12)),
            height=max(350, 32 * len(names)),
        )
        return {"type": f"contribution_{factor.lower()}", "title": f"Contributions {factor}",
                "figure": fig.to_dict()}

    def _chart_contribution_cols(self, factor: str = "F1") -> dict:
        """Horizontal bar chart of attribute contributions to a given factor."""
        res     = self._results
        names   = res["_attr_names"]
        labels  = res["_factor_labels"]

        if factor not in labels:
            factor = labels[0]
        fi      = labels.index(factor)
        ctrs    = res["_col_ctrs"][:, fi]

        order        = np.argsort(ctrs)[::-1]
        sorted_names = [names[i] for i in order]
        sorted_vals  = [float(ctrs[i] * 100) for i in order]

        fig = go.Figure(go.Bar(
            x=sorted_vals, y=sorted_names,
            orientation="h",
            marker_color=_ATTR_COLOUR,
            text=[f"{v:.1f}%" for v in sorted_vals],
            textposition="outside",
            textfont=dict(size=11),
            hovertemplate="<b>%{y}</b><br>Contribution: %{x:.2f}%<extra></extra>",
        ))
        fig.update_layout(
            **_PLOTLY_LAYOUT_BASE,
            title=dict(text=f"Attribute Contributions to {factor}", font=dict(size=16)),
            xaxis=dict(title=f"Contribution to {factor} (%)", gridcolor=_GRID_COLOUR,
                       range=[0, max(sorted_vals) * 1.25]),
            yaxis=dict(autorange="reversed", tickfont=dict(size=11)),
            height=max(350, 28 * len(names)),
        )
        return {"type": f"contribution_cols_{factor.lower()}", "title": f"Attr Contributions {factor}",
                "figure": fig.to_dict()}

    def _chart_cos2_heatmap(self) -> dict:
        """Heatmap of squared cosines: brand × factor quality of representation."""
        res     = self._results
        names   = res["_brand_names"]
        labels  = res["_factor_labels"]
        cos2    = res["_row_cos2"]

        fig = go.Figure(go.Heatmap(
            z=cos2.tolist(),
            x=labels,
            y=names,
            colorscale="Blues",
            text=[[f"{v:.3f}" for v in row] for row in cos2],
            texttemplate="%{text}",
            hovertemplate="Brand: %{y}<br>Factor: %{x}<br>cos²: %{z:.4f}<extra></extra>",
            colorbar=dict(title="cos²"),
            zmin=0, zmax=1,
        ))
        fig.update_layout(
            **_PLOTLY_LAYOUT_BASE,
            title=dict(text="Squared Cosines — Brand Representation Quality", font=dict(size=16)),
            xaxis=dict(title="Factor"),
            yaxis=dict(title="Brand"),
            height=max(300, 35 * len(names)),
        )
        return {"type": "cos2_heatmap", "title": "Squared Cosines Heatmap",
                "figure": fig.to_dict()}

    def _chart_chisq_heatmap(self) -> dict:
        """Heatmap of chi-square distances between brands."""
        res     = self._results
        names   = res["_brand_names"]
        dists   = res["_row_chisq"]

        fig = go.Figure(go.Heatmap(
            z=dists.tolist(),
            x=names,
            y=names,
            colorscale="RdYlGn_r",
            text=[[f"{v:.2f}" for v in row] for row in dists],
            texttemplate="%{text}",
            hovertemplate="%{y} ↔ %{x}: %{z:.3f}<extra></extra>",
            colorbar=dict(title="χ² dist"),
        ))
        fig.update_layout(
            **_PLOTLY_LAYOUT_BASE,
            title=dict(text="Chi-Square Distance Matrix (Brands)", font=dict(size=16)),
            xaxis=dict(title="Brand", tickangle=-45),
            yaxis=dict(title="Brand"),
            height=max(400, 40 * len(names)),
            width=max(500, 50 * len(names)),
        )
        return {"type": "chisq_distance_heatmap", "title": "Chi-Square Distance Heatmap",
                "figure": fig.to_dict()}

    def _chart_row_profiles_radar(self) -> dict:
        """
        Overlapping radar chart: each brand's attribute profile (row profile)
        vs the mean profile (centroid).
        Only the first 12 attributes are shown for readability.
        """
        res         = self._results
        brand_names = res["_brand_names"]
        attr_names  = res["_attr_names"]
        profiles    = res["row_results"]["profiles"].values   # (n_brands, n_attrs)
        mean_prof   = profiles.mean(axis=0)

        # Keep ALL attributes — no filtering (enhance visuals only)
        theta = attr_names + [attr_names[0]]   # close the polygon
        r_max = float(profiles.max()) * 1.1

        # Gridline ring values at 25/50/75% of max
        grid_vals = [round(r_max * p, 4) for p in (0.25, 0.5, 0.75)]

        traces = []
        # Mean profile
        traces.append(go.Scatterpolar(
            r=mean_prof.tolist() + [float(mean_prof[0])],
            theta=theta,
            name="Market Mean",
            line=dict(color="#444444", dash="dash", width=2.5),
            fill="none",
            hovertemplate="<b>Market Mean</b><br>%{theta}: %{r:.4f}<extra></extra>",
        ))
        for i, name in enumerate(brand_names):
            colour = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
            vals   = profiles[i].tolist()
            traces.append(go.Scatterpolar(
                r=vals + [vals[0]],
                theta=theta,
                name=name,
                line=dict(color=colour, width=2.5),
                fill="toself",
                fillcolor=colour,
                opacity=0.75,
                hovertemplate=f"<b>{name}</b><br>%{{theta}}: %{{r:.4f}}<extra></extra>",
            ))

        fig = go.Figure(data=traces)
        fig.update_layout(
            **_PLOTLY_LAYOUT_BASE,
            height=650,
            title=dict(text="Row Profiles — Brand Attribute Radar", font=dict(size=16)),
            polar=dict(
                radialaxis=dict(
                    visible=True,
                    range=[0, r_max],
                    tickvals=grid_vals,
                    ticktext=[f"{v:.3f}" for v in grid_vals],
                    gridcolor="#e5e7eb",
                    gridwidth=1,
                    linecolor="#d1d5db",
                    tickfont=dict(size=9),
                ),
                angularaxis=dict(
                    tickfont=dict(size=10),
                    gridcolor="#e5e7eb",
                ),
                bgcolor=_BG_COLOUR,
            ),
            legend=dict(font=dict(size=10), itemsizing="constant"),
        )
        return {"type": "row_profiles_radar", "title": "Row Profiles Radar",
                "figure": fig.to_dict()}

    def _chart_brand_trajectory(self) -> dict:
        """
        Brand trajectory on F1 axis across categories (if multi-category data).
        Falls back to a F1 × brand bar chart when single category.
        """
        res         = self._results
        brand_names = res["_brand_names"]
        F_rows      = res["_F_rows"]

        # If category_codes has multiple values, caller should call fit() per
        # category and merge externally. Here we show F1 coords per brand.
        f1_vals = F_rows[:, 0].tolist()
        order   = np.argsort(f1_vals)
        colours = [_BRAND_COLOURS[i % len(_BRAND_COLOURS)] for i in order]

        fig = go.Figure(go.Bar(
            x=[f1_vals[i] for i in order],
            y=[brand_names[i] for i in order],
            orientation="h",
            marker_color=colours,
            text=[f"{f1_vals[i]:.3f}" for i in order],
            textposition="outside",
        ))
        fig.add_vline(x=0, line_dash="dash", line_color=_GRID_COLOUR)
        fig.update_layout(
            **_PLOTLY_LAYOUT_BASE,
            title=dict(text="Brand Positions on F1 (Trajectory / Ranking)", font=dict(size=16)),
            xaxis=dict(title=self._axis_label(0), gridcolor=_GRID_COLOUR),
            yaxis=dict(autorange="reversed"),
            height=max(300, 40 * len(brand_names)),
        )
        return {"type": "brand_trajectory", "title": "Brand Trajectory (F1)",
                "figure": fig.to_dict()}

    def _chart_biplot_ellipses(self) -> dict:
        """
        Symmetric biplot with approx. 95% confidence ellipses (filled semi-transparent).
        Brands labeled via annotations. Top-8 attributes labeled with arrows.
        Ellipse size ∝ sampling uncertainty (larger mass → smaller ellipse).
        """
        res         = self._results
        brand_names = res["_brand_names"]
        attr_names  = res["_attr_names"]
        F_rows      = res["_F_rows"]
        F_cols      = res["_F_cols"]
        row_ri      = res["_row_rel_inertia"]
        col_ri      = res["_col_rel_inertia"]
        eigenvalues = res["_eigenvalues"]
        r_masses    = res["_r"]

        max_row_ri  = float(row_ri.max()) if row_ri.max() > 0 else 1.0
        max_col_ri  = float(col_ri.max()) if col_ri.max() > 0 else 1.0
        N_LABELED   = min(8, len(attr_names))
        top_attr_idx = set(np.argsort(col_ri)[::-1][:N_LABELED].tolist())
        ci_scale    = 1.96

        def _trunc(s, n=22):
            return s[:n] + "…" if len(s) > n else s

        def _hex_to_rgba(hex_col: str, alpha: float) -> str:
            h = hex_col.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{alpha})"

        traces = []

        # ── Filled confidence ellipses (drawn first → behind points) ──────────
        for i, name in enumerate(brand_names):
            colour = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
            cx = float(F_rows[i, 0])
            cy = float(F_rows[i, 1])
            if len(eigenvalues) >= 2 and r_masses[i] > 0:
                ax_r = ci_scale * np.sqrt(float(eigenvalues[0]) / (r_masses[i] * 100 + 1e-12))
                ay_r = ci_scale * np.sqrt(float(eigenvalues[1]) / (r_masses[i] * 100 + 1e-12))
                # Cap ellipses to reasonable size
                ax_r = min(ax_r, 0.35)
                ay_r = min(ay_r, 0.35)
            else:
                ax_r = ay_r = 0.04

            theta_arr = np.linspace(0, 2 * np.pi, 72)
            ex = (cx + ax_r * np.cos(theta_arr)).tolist()
            ey = (cy + ay_r * np.sin(theta_arr)).tolist()
            fill_rgba = _hex_to_rgba(colour, 0.12)
            line_rgba = _hex_to_rgba(colour, 0.55)

            # Filled ellipse
            traces.append(go.Scatter(
                x=ex + [ex[0]], y=ey + [ey[0]],
                mode="lines",
                fill="toself",
                fillcolor=fill_rgba,
                line=dict(color=line_rgba, width=1.2, dash="dot"),
                showlegend=False,
                hoverinfo="skip",
            ))

        # ── Attribute dots ──────────────────────────────────────────────────────
        attr_sizes  = [max(7, min(14, 5 + 9 * float(col_ri[j]) / max_col_ri))
                       if j in top_attr_idx else 6
                       for j in range(len(attr_names))]
        attr_opac   = [0.95 if j in top_attr_idx else 0.40 for j in range(len(attr_names))]
        attr_colors = ["#0ea5e9" if j in top_attr_idx else "#94a3b8"
                       for j in range(len(attr_names))]

        traces.append(go.Scatter(
            x=F_cols[:, 0].tolist(),
            y=F_cols[:, 1].tolist(),
            mode="markers",
            name="Attributes",
            marker=dict(symbol="circle-open-dot", size=attr_sizes, color=attr_colors,
                        opacity=attr_opac, line=dict(width=1.5, color=attr_colors)),
            customdata=[[attr_names[j], float(F_cols[j, 0]), float(F_cols[j, 1]),
                         float(col_ri[j]) * 100]
                        for j in range(len(attr_names))],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
            ),
            showlegend=True,
        ))

        # ── Brand centre-points ────────────────────────────────────────────────
        for i, name in enumerate(brand_names):
            colour  = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
            f1, f2  = float(F_rows[i, 0]), float(F_rows[i, 1])
            ri_norm = float(row_ri[i]) / max_row_ri
            msize   = max(12, 10 + 14 * ri_norm)
            traces.append(go.Scatter(
                x=[f1], y=[f2],
                mode="markers",
                name=name,
                marker=dict(symbol="square", size=msize, color=colour,
                            opacity=0.95, line=dict(width=2.5, color="white")),
                customdata=[[name, f1, f2, float(row_ri[i]) * 100]],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                    "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
                ),
                showlegend=True,
            ))

        # Axis range
        all_x = np.concatenate([F_rows[:, 0], F_cols[:, 0]])
        all_y = np.concatenate([F_rows[:, 1], F_cols[:, 1]])
        xspan = float(all_x.max() - all_x.min())
        yspan = float(all_y.max() - all_y.min())
        pad_x = max(0.28 * xspan, 0.08)
        pad_y = max(0.30 * yspan, 0.08)
        xr = [float(all_x.min()) - pad_x, float(all_x.max()) + pad_x]
        yr = [float(all_y.min()) - pad_y, float(all_y.max()) + pad_y]

        traces.append(go.Scatter(x=xr, y=[0, 0], mode="lines",
            line=dict(color="#dde1e9", width=1.2),
            showlegend=False, hoverinfo="skip"))
        traces.append(go.Scatter(x=[0, 0], y=yr, mode="lines",
            line=dict(color="#dde1e9", width=1.2),
            showlegend=False, hoverinfo="skip"))

        fig = go.Figure(data=traces)

        # ── Brand labels with colored borders ─────────────────────────────────
        for i, name in enumerate(brand_names):
            colour = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
            f1, f2 = float(F_rows[i, 0]), float(F_rows[i, 1])
            anc_h = "left"  if f1 >= 0 else "right"
            anc_v = "bottom" if f2 >= 0 else "top"
            fig.add_annotation(
                x=f1, y=f2, text=f"<b>{name}</b>",
                showarrow=False,
                xanchor=anc_h, yanchor=anc_v,
                xshift=14 if f1 >= 0 else -14,
                yshift=12 if f2 >= 0 else -12,
                font=dict(size=11, color=colour),
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor=colour, borderwidth=1.5,
                borderpad=3,
            )

        # ── Top-N attribute labels with arrows ────────────────────────────────
        for j in sorted(top_attr_idx):
            af1, af2 = float(F_cols[j, 0]), float(F_cols[j, 1])
            sign_x = 1 if af1 >= 0 else -1
            sign_y = 1 if af2 >= 0 else -1
            ax_off = sign_x * max(0.04 * (xr[1] - xr[0]), 0.06)
            ay_off = sign_y * max(0.04 * (yr[1] - yr[0]), 0.06)
            fig.add_annotation(
                x=af1, y=af2,
                ax=af1 + ax_off, ay=af2 + ay_off,
                text=f"<i>{_trunc(attr_names[j])}</i>",
                xanchor="center", yanchor="middle",
                axref="x", ayref="y",
                showarrow=True,
                arrowhead=2, arrowsize=0.9, arrowwidth=1.4,
                arrowcolor="#0ea5e9",
                font=dict(size=9, color="#0c4a6e"),
                bgcolor="rgba(224,242,254,0.93)",
                bordercolor="#7dd3fc", borderwidth=1,
                borderpad=3,
            )

        fig.update_layout(
            paper_bgcolor=_BG_COLOUR,
            plot_bgcolor="#fafcff",
            font=dict(family="Inter, sans-serif", size=12, color="#1e293b"),
            margin=dict(l=80, r=60, t=90, b=100),
            height=870,
            title=dict(
                text=(
                    "<b>Biplot with Approx. 95% Confidence Ellipses</b>"
                    f"<br><sup>Shaded zone = sampling uncertainty · Top {N_LABELED} attributes labeled · larger ellipse = less stable position</sup>"
                ),
                font=dict(size=14, color="#0f172a"),
                x=0.5, xanchor="center",
            ),
            xaxis=dict(
                title=dict(text=self._axis_label(0), font=dict(size=11, color="#64748b")),
                zeroline=False,
                gridcolor="#eef2f7", gridwidth=1,
                tickfont=dict(size=10, color="#94a3b8"),
                linecolor="#e2e8f0", showline=True, mirror=True,
                range=xr,
            ),
            yaxis=dict(
                title=dict(text=self._axis_label(1), font=dict(size=11, color="#64748b")),
                zeroline=False,
                gridcolor="#eef2f7", gridwidth=1,
                tickfont=dict(size=10, color="#94a3b8"),
                linecolor="#e2e8f0", showline=True, mirror=True,
                range=yr,
            ),
            legend=dict(
                font=dict(size=9), itemsizing="constant",
                orientation="v", x=1.02, y=0.5, xanchor="left", yanchor="middle",
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor="#e2e8f0", borderwidth=1,
            ),
            hoverlabel=dict(bgcolor="white", font_size=11, bordercolor="#e2e8f0"),
        )
        return {"type": "biplot_ellipses", "title": "Biplot with Confidence Ellipses",
                "figure": fig.to_dict()}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level: build a perceptual map from a pre-computed CA results dict
# with custom attribute label selection and anti-overlap label placement.
# Called directly from the UI layer (no caching) so the user can interactively
# choose which attributes to label without rerunning the whole CA pipeline.
# ─────────────────────────────────────────────────────────────────────────────

def _push_apart_labels(
    positions: list,          # list of [x, y] label-box centres
    min_dist: float = 0.06,   # minimum gap between label centres (data units)
    max_iter: int = 200,
) -> list:
    """
    Simple iterative repulsion: push overlapping label centres apart along the
    vector that connects them, keeping them at least min_dist apart.
    Returns a new list of [x, y] positions.
    """
    import copy
    pts = [list(p) for p in positions]
    for _ in range(max_iter):
        moved = False
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx = pts[j][0] - pts[i][0]
                dy = pts[j][1] - pts[i][1]
                dist = (dx ** 2 + dy ** 2) ** 0.5
                if dist < min_dist and dist > 1e-9:
                    push = (min_dist - dist) / 2.0
                    nx, ny = dx / dist, dy / dist
                    pts[i][0] -= nx * push
                    pts[i][1] -= ny * push
                    pts[j][0] += nx * push
                    pts[j][1] += ny * push
                    moved = True
                elif dist < 1e-9:   # exact same position — nudge randomly
                    pts[j][0] += min_dist * 0.5
                    pts[j][1] += min_dist * 0.3
                    moved = True
        if not moved:
            break
    return pts


def build_perceptual_map(
    ca_results: dict,
    map_type: str = "symmetric",
    label_attrs: "list | None" = None,
    show_all_dots: bool = True,
    axis_labels: "tuple | None" = None,
) -> dict:
    """
    Build a perceptual map (symmetric or asymmetric) from a pre-computed
    CA results dict, with custom attribute label selection and anti-overlap
    label placement.

    Parameters
    ----------
    ca_results : dict
        Output of CorrespondenceAnalysis.fit() (i.e. ca_res["ca_results"]).
    map_type : "symmetric" | "asymmetric"
    label_attrs : list[str] | None
        Attribute names to label.  None → top-8 by column relative inertia.
    show_all_dots : bool
        If False, only show dots for labeled attributes (hides the clutter).
    axis_labels : (str, str) | None
        (x_label, y_label).  None → derived from eigenvalue percentages.

    Returns
    -------
    go.Figure
    """
    import math

    res         = ca_results
    brand_names = list(res["_brand_names"])
    attr_names  = list(res["_attr_names"])
    F_rows      = res["_F_rows"]
    F_cols      = res["_F_cols"] if map_type == "symmetric" else res["_F_cols"]
    A_cols      = res.get("_A_cols", F_cols)   # for asymmetric
    row_ri      = res["_row_rel_inertia"]
    col_ri      = res["_col_rel_inertia"]
    pct         = res.get("_pct_inertia", [0, 0])

    is_asym     = (map_type == "asymmetric")
    # For asymmetric, scale A_cols to match F_rows display range
    if is_asym:
        f_scale = float(np.abs(F_rows).max()) if np.abs(F_rows).max() > 0 else 1.0
        a_scale = float(np.abs(A_cols).max()) if np.abs(A_cols).max() > 0 else 1.0
        scale_factor = min(f_scale / a_scale if a_scale > 0 else 1.0, 2.5)
        vis_cols = A_cols * scale_factor
    else:
        vis_cols = F_cols

    max_row_ri = float(row_ri.max()) if row_ri.max() > 0 else 1.0
    max_col_ri = float(col_ri.max()) if col_ri.max() > 0 else 1.0

    # ── Determine which attributes to label ─────────────────────────────────
    if label_attrs is not None and len(label_attrs) > 0:
        label_idx = [i for i, n in enumerate(attr_names) if n in set(label_attrs)]
    else:
        n_auto = min(8, len(attr_names))
        label_idx = np.argsort(col_ri)[::-1][:n_auto].tolist()

    label_idx_set = set(label_idx)

    def _trunc(s, n=22):
        return s[:n] + "…" if len(s) > n else s

    # ── Axis range ──────────────────────────────────────────────────────────
    all_x = np.concatenate([F_rows[:, 0], vis_cols[:, 0]])
    all_y = np.concatenate([F_rows[:, 1], vis_cols[:, 1]])
    xspan = float(all_x.max() - all_x.min()) or 1.0
    yspan = float(all_y.max() - all_y.min()) or 1.0
    pad_x = max(0.28 * xspan, 0.08)
    pad_y = max(0.30 * yspan, 0.08)
    xr = [float(all_x.min()) - pad_x, float(all_x.max()) + pad_x]
    yr = [float(all_y.min()) - pad_y, float(all_y.max()) + pad_y]

    # Axis labels
    if axis_labels:
        xl, yl = axis_labels
    else:
        xl = f"F1 ({pct[0]:.1f}% inertia)" if len(pct) > 0 else "F1"
        yl = f"F2 ({pct[1]:.1f}% inertia)" if len(pct) > 1 else "F2"

    # Colour for attributes
    attr_colour = "#7c3aed" if is_asym else "#3b82f6"
    attr_light  = "#c4b5fd" if is_asym else "#94a3b8"
    attr_sym    = "diamond-open-dot" if is_asym else "circle-open-dot"
    lbl_bg      = "rgba(237,233,254,0.93)" if is_asym else "rgba(219,234,254,0.93)"
    lbl_border  = "#a78bfa" if is_asym else "#93c5fd"
    lbl_arrow   = "#7c3aed" if is_asym else "#3b82f6"
    lbl_font_c  = "#4c1d95" if is_asym else "#1e3a8a"

    traces = []

    # ── Attribute dots ───────────────────────────────────────────────────────
    if show_all_dots:
        dot_x = [float(vis_cols[j, 0]) for j in range(len(attr_names))]
        dot_y = [float(vis_cols[j, 1]) for j in range(len(attr_names))]
        dot_sz = [max(7, min(14, 5 + 9 * float(col_ri[j]) / max_col_ri))
                  if j in label_idx_set else 6
                  for j in range(len(attr_names))]
        dot_op = [1.0 if j in label_idx_set else 0.35 for j in range(len(attr_names))]
        dot_c  = [attr_colour if j in label_idx_set else attr_light
                  for j in range(len(attr_names))]
        dot_cd = [[attr_names[j], float(vis_cols[j, 0]), float(vis_cols[j, 1]),
                   float(col_ri[j]) * 100] for j in range(len(attr_names))]
        traces.append(go.Scatter(
            x=dot_x, y=dot_y, mode="markers",
            name="Attributes (hover all)",
            marker=dict(symbol=attr_sym, size=dot_sz, color=dot_c,
                        opacity=dot_op, line=dict(width=1.5, color=dot_c)),
            customdata=dot_cd,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
            ),
            showlegend=True,
        ))
    else:
        # Only labeled attr dots
        lx = [float(vis_cols[j, 0]) for j in label_idx]
        ly = [float(vis_cols[j, 1]) for j in label_idx]
        lsz = [max(8, min(14, 5 + 9 * float(col_ri[j]) / max_col_ri)) for j in label_idx]
        lcd = [[attr_names[j], float(vis_cols[j, 0]), float(vis_cols[j, 1]),
                float(col_ri[j]) * 100] for j in label_idx]
        traces.append(go.Scatter(
            x=lx, y=ly, mode="markers",
            name="Attributes (selected)",
            marker=dict(symbol=attr_sym, size=lsz, color=attr_colour,
                        opacity=0.95, line=dict(width=1.5, color=attr_colour)),
            customdata=lcd,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
            ),
            showlegend=True,
        ))

    # ── Brand traces ─────────────────────────────────────────────────────────
    for i, name in enumerate(brand_names):
        colour  = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
        f1, f2  = float(F_rows[i, 0]), float(F_rows[i, 1])
        ri_norm = float(row_ri[i]) / max_row_ri
        msize   = max(12, 10 + 14 * ri_norm)
        traces.append(go.Scatter(
            x=[f1], y=[f2], mode="markers", name=name,
            marker=dict(symbol="square", size=msize, color=colour,
                        opacity=0.92, line=dict(width=2.5, color="white")),
            customdata=[[name, f1, f2, float(row_ri[i]) * 100]],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
            ),
            showlegend=True,
        ))

    # Axis cross-lines
    traces.append(go.Scatter(x=xr, y=[0, 0], mode="lines",
        line=dict(color="#dde1e9", width=1.2), showlegend=False, hoverinfo="skip"))
    traces.append(go.Scatter(x=[0, 0], y=yr, mode="lines",
        line=dict(color="#dde1e9", width=1.2), showlegend=False, hoverinfo="skip"))

    fig = go.Figure(data=traces)

    # ── Brand labels (colored pills) ─────────────────────────────────────────
    for i, name in enumerate(brand_names):
        colour = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
        f1, f2 = float(F_rows[i, 0]), float(F_rows[i, 1])
        anc_h = "left"   if f1 >= 0 else "right"
        anc_v = "bottom" if f2 >= 0 else "top"
        fig.add_annotation(
            x=f1, y=f2, text=f"<b>{name}</b>",
            showarrow=False,
            xanchor=anc_h, yanchor=anc_v,
            xshift=14 if f1 >= 0 else -14,
            yshift=12 if f2 >= 0 else -12,
            font=dict(size=11, color=colour),
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor=colour, borderwidth=1.5,
            borderpad=3,
        )

    # ── Attribute labels with anti-overlap placement ──────────────────────────
    # Step 1: compute desired arrow-tip positions (label box centres) radiating
    # outward from each attribute point.  Arrow tip is placed at a fixed radial
    # distance scaled by the axis span.
    tip_dist_x = 0.13 * (xr[1] - xr[0])
    tip_dist_y = 0.13 * (yr[1] - yr[0])

    raw_tips   = []
    raw_points = []
    for j in label_idx:
        px, py = float(vis_cols[j, 0]), float(vis_cols[j, 1])
        # Angle from origin → push label in that direction
        angle   = math.atan2(py, px)
        tip_x   = px + tip_dist_x * math.cos(angle)
        tip_y   = py + tip_dist_y * math.sin(angle)
        raw_tips.append([tip_x, tip_y])
        raw_points.append([px, py])

    # Step 2: run iterative repulsion to push overlapping label centres apart
    min_sep = 0.14 * max(xr[1] - xr[0], yr[1] - yr[0])
    relaxed = _push_apart_labels(raw_tips, min_dist=min_sep, max_iter=200)

    # Step 3: add annotations with arrows from relaxed tip → actual attr point
    for k, j in enumerate(label_idx):
        px, py   = raw_points[k]
        tip_x, tip_y = relaxed[k]
        fig.add_annotation(
            x=px, y=py,           # arrowhead → actual attribute dot
            ax=tip_x, ay=tip_y,   # arrow tail → label box
            text=f"<i>{_trunc(attr_names[j])}</i>",
            xanchor="center", yanchor="middle",
            axref="x", ayref="y",
            showarrow=True,
            arrowhead=2, arrowsize=0.9, arrowwidth=1.4,
            arrowcolor=lbl_arrow,
            font=dict(size=9, color=lbl_font_c),
            bgcolor=lbl_bg,
            bordercolor=lbl_border, borderwidth=1,
            borderpad=3,
        )

    map_label = "Asymmetric" if is_asym else "Symmetric"
    coord_note = ("Brands: principal · Attributes: scaled std coords"
                  if is_asym else "Principal coordinates")
    n_labeled  = len(label_idx)

    fig.update_layout(
        paper_bgcolor=_BG_COLOUR,
        plot_bgcolor="#fafcff",
        font=dict(family="Inter, sans-serif", size=12, color="#1e293b"),
        margin=dict(l=80, r=60, t=90, b=100),
        height=870,
        title=dict(
            text=(
                f"<b>{map_label} Perceptual Map</b>"
                f"<br><sup>{coord_note} · {n_labeled} attribute(s) labeled · hover dots for all</sup>"
            ),
            font=dict(size=14, color="#0f172a"),
            x=0.5, xanchor="center",
        ),
        xaxis=dict(
            title=dict(text=xl, font=dict(size=11, color="#64748b")),
            zeroline=False,
            gridcolor="#eef2f7", gridwidth=1,
            tickfont=dict(size=10, color="#94a3b8"),
            linecolor="#e2e8f0", showline=True, mirror=True,
            range=xr,
        ),
        yaxis=dict(
            title=dict(text=yl, font=dict(size=11, color="#64748b")),
            zeroline=False,
            gridcolor="#eef2f7", gridwidth=1,
            tickfont=dict(size=10, color="#94a3b8"),
            linecolor="#e2e8f0", showline=True, mirror=True,
            range=yr,
        ),
        legend=dict(
            font=dict(size=9), itemsizing="constant",
            orientation="h", x=0.5, y=-0.10, xanchor="center",
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#e2e8f0", borderwidth=1,
        ),
        hoverlabel=dict(bgcolor="white", font_size=11, bordercolor="#e2e8f0"),
    )
    return fig


def build_biplot_map(
    ca_results: dict,
    label_attrs: "list | None" = None,
    show_all_dots: bool = True,
    axis_labels: "tuple | None" = None,
) -> "go.Figure":
    """
    Confidence biplot with custom attribute label selection and anti-overlap
    label placement.  Mirrors build_perceptual_map() for the biplot variant.

    Parameters
    ----------
    ca_results : dict
        Output of CorrespondenceAnalysis.fit() (i.e. ca_res["ca_results"]).
    label_attrs : list[str] | None
        Attribute names to label.  None → top-8 by column relative inertia.
    show_all_dots : bool
        If False, only labeled attribute dots are drawn.
    axis_labels : (str, str) | None
        (x_label, y_label).  None → derived from eigenvalue percentages.
    """
    import math

    res         = ca_results
    brand_names = list(res["_brand_names"])
    attr_names  = list(res["_attr_names"])
    F_rows      = res["_F_rows"]
    F_cols      = res["_F_cols"]
    row_ri      = res["_row_rel_inertia"]
    col_ri      = res["_col_rel_inertia"]
    eigenvalues = res.get("_eigenvalues", [0.01, 0.01])
    r_masses    = res.get("_r", np.ones(len(brand_names)) * 0.1)
    pct         = res.get("_pct_inertia", [0, 0])

    max_row_ri = float(row_ri.max()) if row_ri.max() > 0 else 1.0
    max_col_ri = float(col_ri.max()) if col_ri.max() > 0 else 1.0

    # Resolve which attrs to label
    if label_attrs is None:
        N_LAB = min(8, len(attr_names))
        label_idx = list(np.argsort(col_ri)[::-1][:N_LAB])
    else:
        label_idx = [i for i, n in enumerate(attr_names) if n in set(label_attrs)]

    def _trunc(s, n=22):
        return s[:n] + "…" if len(s) > n else s

    def _hex_to_rgba(hex_col: str, alpha: float) -> str:
        h = hex_col.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"

    traces = []

    # ── Confidence ellipses ──────────────────────────────────────────────────
    ci_scale = 1.96
    for i, name in enumerate(brand_names):
        colour = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
        cx, cy = float(F_rows[i, 0]), float(F_rows[i, 1])
        if len(eigenvalues) >= 2 and r_masses[i] > 0:
            ax_r = ci_scale * np.sqrt(float(eigenvalues[0]) / (r_masses[i] * 100 + 1e-12))
            ay_r = ci_scale * np.sqrt(float(eigenvalues[1]) / (r_masses[i] * 100 + 1e-12))
            ax_r, ay_r = min(ax_r, 0.35), min(ay_r, 0.35)
        else:
            ax_r = ay_r = 0.04
        theta_arr = np.linspace(0, 2 * np.pi, 72)
        ex = (cx + ax_r * np.cos(theta_arr)).tolist()
        ey = (cy + ay_r * np.sin(theta_arr)).tolist()
        traces.append(go.Scatter(
            x=ex + [ex[0]], y=ey + [ey[0]],
            mode="lines", fill="toself",
            fillcolor=_hex_to_rgba(colour, 0.12),
            line=dict(color=_hex_to_rgba(colour, 0.55), width=1.2, dash="dot"),
            showlegend=False, hoverinfo="skip",
        ))

    # ── Attribute dots ───────────────────────────────────────────────────────
    labeled_set = set(label_idx)
    if show_all_dots:
        visible_idx = list(range(len(attr_names)))
    else:
        visible_idx = label_idx
    attr_x = [float(F_cols[j, 0]) for j in visible_idx]
    attr_y = [float(F_cols[j, 1]) for j in visible_idx]
    attr_sz   = [max(7, min(14, 5 + 9 * float(col_ri[j]) / max_col_ri))
                 if j in labeled_set else 6 for j in visible_idx]
    attr_opac = [0.95 if j in labeled_set else 0.40 for j in visible_idx]
    attr_col  = ["#0ea5e9" if j in labeled_set else "#94a3b8" for j in visible_idx]
    traces.append(go.Scatter(
        x=attr_x, y=attr_y,
        mode="markers", name="Attributes",
        marker=dict(symbol="circle-open-dot", size=attr_sz, color=attr_col,
                    opacity=attr_opac, line=dict(width=1.5, color=attr_col)),
        customdata=[[attr_names[j], float(F_cols[j, 0]), float(F_cols[j, 1]),
                     float(col_ri[j]) * 100] for j in visible_idx],
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
            "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
        ),
        showlegend=True,
    ))

    # ── Brand centre-points ──────────────────────────────────────────────────
    for i, name in enumerate(brand_names):
        colour = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
        f1, f2 = float(F_rows[i, 0]), float(F_rows[i, 1])
        ri_norm = float(row_ri[i]) / max_row_ri
        msize = max(12, 10 + 14 * ri_norm)
        traces.append(go.Scatter(
            x=[f1], y=[f2], mode="markers", name=name,
            marker=dict(symbol="square", size=msize, color=colour,
                        opacity=0.95, line=dict(width=2.5, color="white")),
            customdata=[[name, f1, f2, float(row_ri[i]) * 100]],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "F1: %{customdata[1]:.3f} | F2: %{customdata[2]:.3f}<br>"
                "Rel. inertia: %{customdata[3]:.2f}%<extra></extra>"
            ),
            showlegend=True,
        ))

    # Axis ranges
    all_x = np.concatenate([F_rows[:, 0], F_cols[:, 0]])
    all_y = np.concatenate([F_rows[:, 1], F_cols[:, 1]])
    xspan = float(all_x.max() - all_x.min())
    yspan = float(all_y.max() - all_y.min())
    pad_x = max(0.28 * xspan, 0.08)
    pad_y = max(0.30 * yspan, 0.08)
    xr = [float(all_x.min()) - pad_x, float(all_x.max()) + pad_x]
    yr = [float(all_y.min()) - pad_y, float(all_y.max()) + pad_y]

    traces.append(go.Scatter(x=xr, y=[0, 0], mode="lines",
        line=dict(color="#dde1e9", width=1.2), showlegend=False, hoverinfo="skip"))
    traces.append(go.Scatter(x=[0, 0], y=yr, mode="lines",
        line=dict(color="#dde1e9", width=1.2), showlegend=False, hoverinfo="skip"))

    fig = go.Figure(data=traces)

    # ── Brand labels ──────────────────────────────────────────────────────────
    for i, name in enumerate(brand_names):
        colour = _BRAND_COLOURS[i % len(_BRAND_COLOURS)]
        f1, f2 = float(F_rows[i, 0]), float(F_rows[i, 1])
        fig.add_annotation(
            x=f1, y=f2, text=f"<b>{name}</b>",
            showarrow=False,
            xanchor="left" if f1 >= 0 else "right",
            yanchor="bottom" if f2 >= 0 else "top",
            xshift=14 if f1 >= 0 else -14,
            yshift=12 if f2 >= 0 else -12,
            font=dict(size=11, color=colour),
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor=colour, borderwidth=1.5, borderpad=3,
        )

    # ── Attribute labels with anti-overlap arrows ─────────────────────────────
    if label_idx:
        tip_dist_x = 0.13 * (xr[1] - xr[0])
        tip_dist_y = 0.13 * (yr[1] - yr[0])
        raw_tips, raw_points = [], []
        for j in label_idx:
            px, py = float(F_cols[j, 0]), float(F_cols[j, 1])
            angle = math.atan2(py, px)
            raw_tips.append([px + tip_dist_x * math.cos(angle),
                              py + tip_dist_y * math.sin(angle)])
            raw_points.append([px, py])
        min_sep = 0.14 * max(xr[1] - xr[0], yr[1] - yr[0])
        relaxed = _push_apart_labels(raw_tips, min_dist=min_sep, max_iter=200)
        for k, j in enumerate(label_idx):
            px, py = raw_points[k]
            tip_x, tip_y = relaxed[k]
            fig.add_annotation(
                x=px, y=py, ax=tip_x, ay=tip_y,
                text=f"<i>{_trunc(attr_names[j])}</i>",
                xanchor="center", yanchor="middle",
                axref="x", ayref="y",
                showarrow=True, arrowhead=2, arrowsize=0.9, arrowwidth=1.4,
                arrowcolor="#0ea5e9",
                font=dict(size=9, color="#0c4a6e"),
                bgcolor="rgba(224,242,254,0.93)",
                bordercolor="#7dd3fc", borderwidth=1, borderpad=3,
            )

    pct0 = float(pct[0]) if len(pct) > 0 else 0.0
    pct1 = float(pct[1]) if len(pct) > 1 else 0.0
    if axis_labels:
        xl, yl = axis_labels
    else:
        xl = f"F1 ({pct0:.1f}% inertia)"
        yl = f"F2 ({pct1:.1f}% inertia)"

    fig.update_layout(
        paper_bgcolor=_BG_COLOUR,
        plot_bgcolor="#fafcff",
        font=dict(family="Inter, sans-serif", size=12, color="#1e293b"),
        margin=dict(l=80, r=60, t=90, b=100),
        height=870,
        title=dict(
            text=(
                "<b>Biplot with Approx. 95% Confidence Ellipses</b>"
                f"<br><sup>Shaded zone = sampling uncertainty · {len(label_idx)} attributes labeled · larger ellipse = less stable</sup>"
            ),
            font=dict(size=14, color="#0f172a"),
            x=0.5, xanchor="center",
        ),
        xaxis=dict(
            title=dict(text=xl, font=dict(size=11, color="#64748b")),
            zeroline=False, gridcolor="#eef2f7", gridwidth=1,
            tickfont=dict(size=10, color="#94a3b8"),
            linecolor="#e2e8f0", showline=True, mirror=True, range=xr,
        ),
        yaxis=dict(
            title=dict(text=yl, font=dict(size=11, color="#64748b")),
            zeroline=False, gridcolor="#eef2f7", gridwidth=1,
            tickfont=dict(size=10, color="#94a3b8"),
            linecolor="#e2e8f0", showline=True, mirror=True, range=yr,
        ),
        legend=dict(
            font=dict(size=9), itemsizing="constant",
            orientation="h", x=0.5, y=-0.10, xanchor="center",
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#e2e8f0", borderwidth=1,
        ),
        hoverlabel=dict(bgcolor="white", font_size=11, bordercolor="#e2e8f0"),
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Convenience top-level function — run full CA pipeline from DB
# ─────────────────────────────────────────────────────────────────────────────

def run_ca_pipeline(
    category_codes: Optional[list] = None,
    brand_ids: Optional[list] = None,
    attr_ids: Optional[list] = None,
    attr_section: Optional[str] = None,
    db_path=None,
    min_respondents: int = 50,
    top_brands: int = 20,
    top_attrs: int = 50,
    zone: Optional[str] = None,
    gender: Optional[str] = None,
    age_band: Optional[str] = None,
    city: Optional[str] = None,
    project_id: Optional[str] = None,
    awareness_stages: Optional[list] = None,
) -> dict:
    """
    End-to-end CA pipeline: fetch data → fit CA → return full results + charts.

    Parameters
    ----------
    category_codes : list[str] | None
    brand_ids      : list[int] | None
    attr_ids       : list[int] | None
    db_path        : str | Path | None
    min_respondents, top_brands, top_attrs : see CorrespondenceAnalysis

    Returns
    -------
    dict with keys:
        matrix        pd.DataFrame      — brand × attr % matrix
        ca_results    dict              — full CA output from fit()
        tables        dict              — JSON-serialisable tables
        map_data      dict              — {brands, attrs, explained_variance}
        chart_specs   list[dict]        — Plotly fig dicts
        status        str               — 'ok' | 'insufficient_data' | 'error'
        message       str               — human-readable status
    """
    try:
        engine = CorrespondenceAnalysis(
            db_path=db_path,
            category_codes=category_codes,
            min_respondents=min_respondents,
            top_brands=top_brands,
            top_attrs=top_attrs,
            zone=zone, gender=gender, age_band=age_band, city=city,
            project_id=project_id,
        )
        matrix = engine.get_brand_attr_matrix(
            category_codes=category_codes,
            brand_ids=brand_ids,
            attr_ids=attr_ids,
            attr_section=attr_section,
            awareness_stages=awareness_stages,
        )

        if matrix.empty:
            return {
                "status":  "insufficient_data",
                "message": "No imagery data found for the given filters.",
                "matrix":  pd.DataFrame(),
                "ca_results": {}, "tables": {}, "map_data": {}, "chart_specs": [],
            }

        if matrix.shape[0] < 2 or matrix.shape[1] < 2:
            return {
                "status":  "insufficient_data",
                "message": f"CA requires ≥2 brands and ≥2 attributes (got {matrix.shape}).",
                "matrix":  matrix,
                "ca_results": {}, "tables": {}, "map_data": {}, "chart_specs": [],
            }

        ca_results = engine.fit(matrix)
        tables     = engine.get_full_ca_tables()
        map_data   = engine.get_perceptual_map_data(n_dims=2)
        charts     = engine.get_chart_specs()

        return {
            "status":      "ok",
            "message":     (
                f"CA complete — {matrix.shape[0]} brands × {matrix.shape[1]} attributes. "
                f"F1+F2 explain "
                f"{ca_results['_pct_inertia'][0]+ca_results['_pct_inertia'][1]:.1f}% inertia."
            ),
            "matrix":      matrix,
            "ca_results":  ca_results,
            "tables":      tables,
            "map_data":    map_data,
            "chart_specs": charts,
        }

    except Exception as exc:
        return {
            "status":  "error",
            "message": str(exc),
            "matrix":  pd.DataFrame(),
            "ca_results": {}, "tables": {}, "map_data": {}, "chart_specs": [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# CLI smoke-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("Running CA pipeline smoke-test …")
    result = run_ca_pipeline(top_brands=10, top_attrs=15)
    print(f"Status : {result['status']}")
    print(f"Message: {result['message']}")

    if result["status"] == "ok":
        ca = result["ca_results"]
        print(f"\nMatrix shape  : {result['matrix'].shape}")
        print(f"Total inertia : {ca['total_inertia']:.6f}")
        print(f"\nEigenvalues (top 4):")
        print(ca["eigenvalues"].head(4).to_string())
        print(f"\nChi-square test: chi2={ca['chi2_test']['chi2']}, "
              f"p={ca['chi2_test']['p_value']}, significant={ca['chi2_test']['significant']}")
        print(f"\nBrand principal coords (F1, F2):")
        print(ca["row_results"]["principal_coords"].iloc[:, :2].to_string())
        print(f"\nCharts generated: {len(result['chart_specs'])}")
        for spec in result["chart_specs"]:
            print(f"  - {spec.get('type','?')}: {spec.get('title','')}")

        # Verify JSON serialisability
        tables_json = json.dumps(result["tables"], default=str)
        print(f"\nJSON serialisation: OK ({len(tables_json):,} chars)")
    else:
        print("No imagery data in DB or error — pipeline exited cleanly.")
