"""
BrandImageryEngine — Fixed for oxdata star schema.

Correct tables: fact_respondents, fact_brand_awareness, fact_brand_nps, dim_brand, dim_zone, dim_city
Correct views:  v_respondents, v_brand_awareness, v_brand_nps

Previous version queried 'respondents', 'responses', 'choices', 'variables' —
none of which exist in the current schema. Also queried bq3 imagery data
that is not yet ingested. This version uses what is actually in the DB.
"""

import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from oxdata.db_loader import get_db_path


class BrandImageryEngine:
    def __init__(self, db_path=None, project_id=None):
        self.project_id = project_id or "project_1"
        if db_path is None:
            found = get_db_path(project_id=self.project_id, required_table="fact_respondents")
            self.db_path = str(found) if found else "oxdata/data/project_1/oxdata.db"
        else:
            self.db_path = db_path

    def _get_conn(self):
        return sqlite3.connect(os.path.abspath(self.db_path))

    def _meta(self) -> dict:
        """Per-project metadata (description, NPS benchmark, categories, …)."""
        from oxdata.db_loader import get_project_meta
        return get_project_meta(self.project_id)

    def get_categories(self):
        """Return list of product categories from project_meta.json (dynamic, not project_1-only)."""
        cats = self._meta().get("category_names") or []
        if not cats:
            # Fallback: project_1 hardcoded config (only reached when meta file missing AND project_1)
            try:
                from oxdata.config.project_1 import BRAND_CATEGORIES
                cats = list(BRAND_CATEGORIES.keys())
            except ImportError:
                pass
        return ["All"] + cats

    def get_brand_health(self, category="all", zone="all", city="all", months=None,
                         gender="all", age_band="all"):
        """
        Compute brand health metrics from raw Excel (data_layer) when available,
        else from the star-schema SQLite views.
        """
        # data_layer path — no SQL, computes from raw Excel + mapping
        try:
            from lens.data_layer import get_project_layer, project_has_raw_layer
            if project_has_raw_layer(self.project_id):
                return self._get_brand_health_from_layer(zone=zone, city=city,
                                                         gender=gender, age_band=age_band)
        except Exception:
            pass

        conn = self._get_conn()

        # ── 1. Respondent filter (geo + demographic + months; category is NULL in this DB)
        resp_parts, resp_params = [], []
        if zone.lower() not in ("all", ""):
            resp_parts.append("zone_name = ?")
            resp_params.append(zone)
        if city.lower() not in ("all", ""):
            resp_parts.append("LOWER(city_name) = LOWER(?)")
            resp_params.append(city)
        if months:
            cutoff = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
            resp_parts.append("interview_date >= ?")
            resp_params.append(cutoff)
        if gender and gender.lower() not in ("all", ""):
            resp_parts.append("gender = ?")
            resp_params.append(gender)
        if age_band and age_band.lower() not in ("all", ""):
            resp_parts.append("age_band = ?")
            resp_params.append(age_band)

        resp_where = ("WHERE " + " AND ".join(resp_parts)) if resp_parts else ""

        base_n = int(
            pd.read_sql_query(
                f"SELECT COUNT(*) as n FROM v_respondents {resp_where}",
                conn, params=resp_params
            ).iloc[0]["n"]
        )

        if base_n < 10:
            conn.close()
            return {"status": "insufficient_data", "base_n": base_n}

        # ── 2. Brand filter removed (requested by user) ───────────────────────
        brand_filter_awa, brand_filter_nps = "", ""
        brand_params = []

        # ── 3. Awareness data ─────────────────────────────────────────────────
        awa_df = pd.read_sql_query(
            f"""
            WITH base AS (SELECT respondent_id FROM v_respondents {resp_where})
            SELECT ba.brand_id, ba.brand_name, ba.stage, ba.respondent_id
            FROM v_brand_awareness ba
            WHERE ba.respondent_id IN (SELECT respondent_id FROM base)
              AND ba.brand_name NOT IN ('Others (Specify 1)', 'Others (Specify 2)',
                                        'Don''t Know / None')
            {brand_filter_awa}
            """,
            conn, params=resp_params + brand_params
        )

        # ── 4. NPS data ───────────────────────────────────────────────────────
        nps_df = pd.read_sql_query(
            f"""
            WITH base AS (SELECT respondent_id FROM v_respondents {resp_where})
            SELECT bn.brand_id, bn.brand_name, bn.nps_score
            FROM v_brand_nps bn
            WHERE bn.respondent_id IN (SELECT respondent_id FROM base)
              AND bn.brand_name NOT IN ('Others (Specify 1)', 'Others (Specify 2)',
                                        'Don''t Know / None')
            {brand_filter_nps}
            """,
            conn, params=resp_params + brand_params
        )
        conn.close()

        if awa_df.empty:
            return {"status": "insufficient_data", "base_n": base_n}

        # ── 5. Per-brand metrics ──────────────────────────────────────────────
        brands_list = []
        for (brand_id, brand_name), grp in awa_df.groupby(["brand_id", "brand_name"]):
            # Awareness stages only: TOM, SPONT (additive spontaneous), AIDED (incremental).
            # EVER_USED/CONSIDERATION/etc. are independent usage questions — NOT awareness subsets.
            # Must filter to awareness stages only; otherwise aided_n inflates to ~100%.
            _awa_grp  = grp[grp["stage"].isin(["TOM", "SPONT", "AIDED"])]
            total_awa_n = _awa_grp["respondent_id"].nunique()  # total unique respondents aware (55.0%)
            spont_n     = _awa_grp[_awa_grp["stage"].isin(["SPONT", "TOM"])]["respondent_id"].nunique()
            tom_n       = _awa_grp[_awa_grp["stage"] == "TOM"]["respondent_id"].nunique()
            aided_only_n= grp[grp["stage"] == "AIDED"]["respondent_id"].nunique()  # Q17 AIDED recall stage

            tom_pct             = round(tom_n       / base_n * 100, 1)
            spont_pct           = round(spont_n     / base_n * 100, 1)
            aided_pct           = round(aided_only_n/ base_n * 100, 1)  # = oxdata stage='AIDED' % (55.0%)
            total_awareness_pct = round(total_awa_n / base_n * 100, 1)  # = total awareness % (55.0%)

            # NPS metrics
            bnps = nps_df[nps_df["brand_id"] == brand_id]
            raters = len(bnps)
            nps_val = nps_promoters_pct = nps_passives_pct = nps_detractors_pct = None
            if raters >= 30:
                promoters  = int((bnps["nps_score"] >= 9).sum())
                passives   = int(((bnps["nps_score"] >= 7) & (bnps["nps_score"] <= 8)).sum())
                detractors = int((bnps["nps_score"] <= 6).sum())
                nps_val            = round((promoters - detractors) / raters * 100, 1)
                nps_promoters_pct  = round(promoters  / raters * 100, 1)
                nps_passives_pct   = round(passives   / raters * 100, 1)
                nps_detractors_pct = round(detractors / raters * 100, 1)

            # Strategic score: composite of TOM (salience), total awareness (reach), NPS (loyalty).
            # NPS is normalized from [-100,+100] → [0,100] before weighting.
            if nps_val is not None:
                nps_norm = (nps_val + 100) / 2.0
                strat_score = round(tom_pct * 0.40 + total_awareness_pct * 0.10 + nps_norm * 0.50, 1)
            else:
                strat_score = round(tom_pct * 0.60 + total_awareness_pct * 0.20 + spont_pct * 0.20, 1)

            brands_list.append({
                "brand_id":   int(brand_id),
                "brand_name": str(brand_name),
                # Awareness funnel (counts + percentages)
                "tom":                 tom_n,
                "spont":               spont_n,
                "aided":               aided_only_n,
                "total_awareness":     total_awa_n,
                "tom_pct":             tom_pct,
                "spont_pct":           spont_pct,
                "aided_pct":           aided_pct,
                "total_awareness_pct": total_awareness_pct,
                # Incremental awareness segments (for stacked bar chart)
                "tom_only_pct":   tom_pct,
                "spont_incr_pct": round(spont_pct - tom_pct, 1),
                "aided_incr_pct": aided_pct,
                # Usage funnel (bq1d-h ingested via ingest_missing_vars.py)
                **(_fu := {
                    "ever_used":      grp[grp["stage"] == "EVER_USED"]["respondent_id"].nunique(),
                    "current_use":    grp[grp["stage"] == "CURRENT_USER"]["respondent_id"].nunique(),
                    "consideration":  grp[grp["stage"] == "CONSIDERATION"]["respondent_id"].nunique(),
                    "preferred":      grp[grp["stage"] == "PREFERRED"]["respondent_id"].nunique(),
                    "last_purchased": grp[grp["stage"] == "LAST_PURCHASED"]["respondent_id"].nunique(),
                }),
                "ever_used_pct":      round(_fu["ever_used"]      / base_n * 100, 1),
                "current_pct":        round(_fu["current_use"]    / base_n * 100, 1),
                "consideration_pct":  round(_fu["consideration"]  / base_n * 100, 1),
                "preferred_pct":      round(_fu["preferred"]       / base_n * 100, 1),
                "last_purchased_pct": round(_fu["last_purchased"]  / base_n * 100, 1),
                "aided_to_used_pct":  round(_fu["ever_used"] / max(aided_only_n, 1) * 100, 1),
                "used_to_current_pct":round(_fu["current_use"] / max(_fu["ever_used"], 1) * 100, 1),
                # NPS
                "nps":                nps_val,
                "nps_base":           raters,
                "nps_promoters_pct":  nps_promoters_pct,
                "nps_passives_pct":   nps_passives_pct,
                "nps_detractors_pct": nps_detractors_pct,
                # Imagery (bq3 not ingested — placeholders)
                "scores":  [0, 0, 0, 0, 0],
                "imagery": [],
                # Composite
                "strat_score": strat_score,
            })

        brands_list.sort(key=lambda x: x["aided"], reverse=True)

        return {
            "status":    "success",
            "base_n":    base_n,
            "brands":    brands_list,
            "strategic": [],   # bq3 imagery data not yet ingested
        }

    def _get_brand_health_from_layer(self, zone="all", city="all",
                                      gender="all", age_band="all") -> dict:
        """
        data_layer variant of get_brand_health(). Returns identical dict shape.
        Demographic filtering applied via demographics DataFrame join.
        """
        from lens.data_layer import get_project_layer
        layer = get_project_layer(self.project_id)
        if layer is None:
            return {"status": "insufficient_data", "base_n": 0}

        raw_df = layer.raw_df
        demo_df = layer.demographics

        # Apply filters via demographics join
        resp_ids = set(demo_df["respondent_id"].tolist())
        if zone.lower() not in ("all", "") and "zone_name" in demo_df.columns:
            resp_ids &= set(demo_df[demo_df["zone_name"].str.lower() == zone.lower()]["respondent_id"])
        if city.lower() not in ("all", "") and "city_name" in demo_df.columns:
            resp_ids &= set(demo_df[demo_df["city_name"].str.lower() == city.lower()]["respondent_id"])
        if gender.lower() not in ("all", "") and "gender" in demo_df.columns:
            resp_ids &= set(demo_df[demo_df["gender"].str.lower() == gender.lower()]["respondent_id"])
        if age_band.lower() not in ("all", "") and "age_band" in demo_df.columns:
            resp_ids &= set(demo_df[demo_df["age_band"].str.lower() == age_band.lower()]["respondent_id"])

        base_n = len(resp_ids)
        if base_n < 10:
            return {"status": "insufficient_data", "base_n": base_n}

        aw = layer.awareness
        if resp_ids != set(demo_df["respondent_id"]):
            aw = aw[aw["respondent_id"].isin(resp_ids)]
        nps_df = layer.nps
        if resp_ids != set(demo_df["respondent_id"]):
            nps_df = nps_df[nps_df["respondent_id"].isin(resp_ids)]

        STAGES = ["TOM", "SPONT", "AIDED", "EVER_USED", "CURRENT_USER",
                  "CONSIDERATION", "PREFERRED", "LAST_PURCHASED"]
        brands_list = []

        for brand_name, grp in aw.groupby("brand_name"):
            _awa = grp[grp["stage"].isin(["TOM", "SPONT", "AIDED"])]
            total_awa_n  = _awa["respondent_id"].nunique()
            spont_n      = _awa[_awa["stage"].isin(["SPONT", "TOM"])]["respondent_id"].nunique()
            tom_n        = _awa[_awa["stage"] == "TOM"]["respondent_id"].nunique()
            aided_only_n = grp[grp["stage"] == "AIDED"]["respondent_id"].nunique()

            tom_pct             = round(tom_n       / base_n * 100, 1)
            spont_pct           = round(spont_n     / base_n * 100, 1)
            aided_pct           = round(aided_only_n/ base_n * 100, 1)
            total_awareness_pct = round(total_awa_n / base_n * 100, 1)

            bnps = nps_df[nps_df["brand_name"] == brand_name]["nps_score"].dropna()
            raters = len(bnps)
            nps_val = nps_promoters_pct = nps_passives_pct = nps_detractors_pct = None
            if raters >= 30:
                promoters  = int((bnps >= 9).sum())
                passives   = int(((bnps >= 7) & (bnps <= 8)).sum())
                detractors = int((bnps <= 6).sum())
                nps_val            = round((promoters - detractors) / raters * 100, 1)
                nps_promoters_pct  = round(promoters  / raters * 100, 1)
                nps_passives_pct   = round(passives   / raters * 100, 1)
                nps_detractors_pct = round(detractors / raters * 100, 1)

            if nps_val is not None:
                nps_norm = (nps_val + 100) / 2.0
                strat_score = round(tom_pct * 0.40 + total_awareness_pct * 0.10 + nps_norm * 0.50, 1)
            else:
                strat_score = round(tom_pct * 0.60 + total_awareness_pct * 0.20 + spont_pct * 0.20, 1)

            def _stage_n(s): return grp[grp["stage"] == s]["respondent_id"].nunique()
            _fu = {
                "ever_used":      _stage_n("EVER_USED"),
                "current_use":    _stage_n("CURRENT_USER"),
                "consideration":  _stage_n("CONSIDERATION"),
                "preferred":      _stage_n("PREFERRED"),
                "last_purchased": _stage_n("LAST_PURCHASED"),
            }

            brands_list.append({
                "brand_id":   hash(brand_name) % 10000,  # synthetic id (no dim_brand table)
                "brand_name": str(brand_name),
                "tom": tom_n, "spont": spont_n, "aided": aided_only_n,
                "total_awareness": total_awa_n,
                "tom_pct": tom_pct, "spont_pct": spont_pct,
                "aided_pct": aided_pct, "total_awareness_pct": total_awareness_pct,
                "tom_only_pct": tom_pct,
                "spont_incr_pct": round(spont_pct - tom_pct, 1),
                # incremental aided = total_aware - spont (pandas AIDED stage overlaps with SPONT)
                "aided_incr_pct": round(max(total_awareness_pct - spont_pct, 0), 1),
                **{k: _fu[k] for k in _fu},
                "ever_used_pct":      round(_fu["ever_used"]      / base_n * 100, 1),
                "current_pct":        round(_fu["current_use"]    / base_n * 100, 1),
                "consideration_pct":  round(_fu["consideration"]  / base_n * 100, 1),
                "preferred_pct":      round(_fu["preferred"]       / base_n * 100, 1),
                "last_purchased_pct": round(_fu["last_purchased"]  / base_n * 100, 1),
                "aided_to_used_pct":  round(_fu["ever_used"] / max(aided_only_n, 1) * 100, 1),
                "used_to_current_pct":round(_fu["current_use"] / max(_fu["ever_used"], 1) * 100, 1),
                "nps": nps_val, "nps_base": raters,
                "nps_promoters_pct": nps_promoters_pct,
                "nps_passives_pct": nps_passives_pct,
                "nps_detractors_pct": nps_detractors_pct,
                "scores": [0, 0, 0, 0, 0],
                "imagery": [],
                "strat_score": strat_score,
            })

        brands_list.sort(key=lambda x: x["total_awareness"], reverse=True)
        return {"status": "success", "base_n": base_n, "brands_list": brands_list, "strategic": []}

    def get_zone_breakdown(self, brand_name: str, base_n: int) -> dict:
        """
        Returns TOM/SPONT/AIDED percentages and NPS per zone for a given brand.
        Keys: zone_name -> {tom_pct, spont_pct, aided_pct, nps, nps_base, zone_base_n}
        """
        conn = self._get_conn()
        zones = ["North", "South", "East", "West"]
        result = {}

        for zone in zones:
            # Awareness in this zone
            awa = pd.read_sql_query("""
                SELECT stage, COUNT(DISTINCT respondent_id) n
                FROM v_brand_awareness
                WHERE brand_name = ? AND zone_name = ?
                GROUP BY stage
            """, conn, params=[brand_name, zone])

            stage_counts = dict(zip(awa["stage"], awa["n"]))
            tom_n   = stage_counts.get("TOM", 0)
            spont_n = stage_counts.get("SPONT", 0) + tom_n
            aided_n = stage_counts.get("AIDED", 0) + spont_n

            # Zone respondent base
            zone_base = pd.read_sql_query(
                "SELECT COUNT(*) n FROM v_respondents WHERE zone_name = ?",
                conn, params=[zone]
            ).iloc[0]["n"]

            # NPS in this zone
            nps_row = pd.read_sql_query("""
                SELECT COUNT(nps_score) n,
                       SUM(CASE WHEN nps_score>=9 THEN 1 ELSE 0 END) p,
                       SUM(CASE WHEN nps_score<=6 THEN 1 ELSE 0 END) d
                FROM v_brand_nps
                WHERE brand_name = ? AND zone_name = ? AND nps_score IS NOT NULL
            """, conn, params=[brand_name, zone]).iloc[0]

            nps_base = int(nps_row["n"])
            nps_val  = (
                round((nps_row["p"] - nps_row["d"]) / nps_base * 100, 1)
                if nps_base >= 15 else None
            )

            result[zone] = {
                "tom_pct":   round(tom_n   / zone_base * 100, 1) if zone_base > 0 else 0,
                "spont_pct": round(spont_n / zone_base * 100, 1) if zone_base > 0 else 0,
                "aided_pct": round(aided_n / zone_base * 100, 1) if zone_base > 0 else 0,
                "zone_base": int(zone_base),
                "nps":       nps_val,
                "nps_base":  nps_base,
            }

        conn.close()
        return result

    def get_city_nps(self, brand_name: str, min_raters: int = 15) -> list:
        """
        Returns NPS per city for a given brand, sorted descending.
        Each entry: {city_name, nps, raters, zone_name}
        """
        conn = self._get_conn()
        df = pd.read_sql_query("""
            SELECT city_name, zone_name,
                   COUNT(nps_score) raters,
                   ROUND((SUM(CASE WHEN nps_score>=9 THEN 1.0 ELSE 0 END) -
                          SUM(CASE WHEN nps_score<=6 THEN 1.0 ELSE 0 END))
                         * 100.0 / COUNT(nps_score), 1) nps
            FROM v_brand_nps
            WHERE brand_name = ? AND nps_score IS NOT NULL
            GROUP BY city_name, zone_name
            HAVING COUNT(nps_score) >= ?
            ORDER BY nps DESC
        """, conn, params=[brand_name, min_raters])
        conn.close()
        return df.to_dict("records")

    def get_rival_metrics(self, brand_name: str, base_n: int) -> list:
        """
        Returns awareness + NPS for rival brands defined in COMPETITORS config.
        Returns list of dicts: {brand_name, tom_pct, spont_pct, aided_pct, nps}
        """
        # Competitors are project_1-specific config; non-project_1 projects have no predefined
        # competitor list — skip gracefully rather than returning project_1's appliance rivals.
        rivals = []
        if self.project_id == "project_1":
            try:
                from oxdata.config.project_1 import COMPETITORS
                rivals = COMPETITORS.get(brand_name, [])[:3]
            except ImportError:
                pass

        if not rivals:
            return []

        conn = self._get_conn()
        result = []
        for rival in rivals:
            # Awareness
            awa = pd.read_sql_query("""
                SELECT stage, COUNT(DISTINCT respondent_id) n
                FROM v_brand_awareness
                WHERE brand_name = ?
                GROUP BY stage
            """, conn, params=[rival])
            sc = dict(zip(awa["stage"], awa["n"]))
            tom_n   = sc.get("TOM", 0)
            spont_n = sc.get("SPONT", 0) + tom_n
            aided_n = sc.get("AIDED", 0) + spont_n

            # NPS
            nps_row = pd.read_sql_query("""
                SELECT COUNT(nps_score) n,
                       SUM(CASE WHEN nps_score>=9 THEN 1 ELSE 0 END) p,
                       SUM(CASE WHEN nps_score<=6 THEN 1 ELSE 0 END) d
                FROM v_brand_nps WHERE brand_name = ? AND nps_score IS NOT NULL
            """, conn, params=[rival]).iloc[0]
            nps_base = int(nps_row["n"])
            nps_val = (
                round((nps_row["p"] - nps_row["d"]) / nps_base * 100, 1)
                if nps_base >= 30 else None
            )

            result.append({
                "brand_name": rival,
                "tom_pct":    round(tom_n   / base_n * 100, 1),
                "spont_pct":  round(spont_n / base_n * 100, 1),
                "aided_pct":  round(aided_n / base_n * 100, 1),
                "nps":        nps_val,
                "nps_base":   nps_base,
            })

        conn.close()
        return result

    def get_funnel_comparison(
        self,
        brands: list,
        segment_type: str = "overall",
        segment_values: list = None,
    ) -> dict:
        """
        Awareness-depth funnel (TOM/SPONT/AIDED/CONSIDERATION %) for each brand × segment.

        segment_type: 'overall' | 'zone' | 'gender' | 'age_band' | 'city'
        segment_values: list of values (e.g. ['North', 'South']).
            When None or empty, uses a single 'Overall' segment.

        Returns:
            {
              brand_name: {
                segment_value: {tom_pct, spont_pct, aided_pct, consideration_pct, base_n}
              }
            }
        """
        # Route to pandas layer when raw_data.xlsx is available (avoids SQLite AIDED gaps)
        try:
            from lens.data_layer import get_project_layer, project_has_raw_layer
            if project_has_raw_layer(self.project_id):
                return self._get_funnel_comparison_from_layer(
                    brands=brands,
                    segment_type=segment_type,
                    segment_values=segment_values,
                )
        except Exception:
            pass

        segment_col_map = {
            "zone":     "zone_name",
            "gender":   "gender",
            "age_band": "age_band",
            "city":     "city_name",
        }

        conn = self._get_conn()

        # Resolve segments
        if segment_type == "overall" or not segment_values:
            segments = [("Overall", None, None)]
        else:
            col = segment_col_map.get(segment_type, "zone_name")
            segments = [(v, col, v) for v in segment_values]

        result: dict = {}
        for brand in brands:
            result[brand] = {}
            for seg_label, seg_col, seg_val in segments:
                if seg_col and seg_val:
                    base_row = pd.read_sql_query(
                        f"SELECT COUNT(*) n FROM v_respondents WHERE {seg_col} = ?",
                        conn, params=[seg_val]
                    ).iloc[0]
                else:
                    base_row = pd.read_sql_query(
                        "SELECT COUNT(*) n FROM v_respondents", conn
                    ).iloc[0]
                base_n = int(base_row["n"])
                if base_n < 10:
                    result[brand][seg_label] = {
                        "tom_pct": 0, "spont_pct": 0, "aided_pct": 0,
                        "consideration_pct": 0, "base_n": base_n
                    }
                    continue

                if seg_col and seg_val:
                    awa = pd.read_sql_query(
                        f"""
                        SELECT
                            COUNT(DISTINCT CASE WHEN ba.stage = 'TOM' THEN ba.respondent_id END) AS tom_n,
                            COUNT(DISTINCT CASE WHEN ba.stage IN ('TOM', 'SPONT') THEN ba.respondent_id END) AS spont_n,
                            COUNT(DISTINCT CASE WHEN ba.stage = 'AIDED' THEN ba.respondent_id END) AS aided_only_n,
                            COUNT(DISTINCT CASE WHEN ba.stage IN ('TOM', 'SPONT', 'AIDED') THEN ba.respondent_id END) AS total_awa_n,
                            COUNT(DISTINCT CASE WHEN ba.stage = 'CONSIDERATION' THEN ba.respondent_id END) AS consid_n,
                            COUNT(DISTINCT CASE WHEN ba.stage = 'EVER_USED' THEN ba.respondent_id END) AS ever_used_n,
                            COUNT(DISTINCT CASE WHEN ba.stage IN ('CURRENT_USER', 'CURRENT_USE') THEN ba.respondent_id END) AS current_n,
                            COUNT(DISTINCT CASE WHEN ba.stage = 'PREFERRED' THEN ba.respondent_id END) AS preferred_n
                        FROM v_brand_awareness ba
                        JOIN v_respondents r ON ba.respondent_id = r.respondent_id
                        WHERE ba.brand_name = ? AND r.{seg_col} = ?
                        """,
                        conn, params=[brand, seg_val]
                    )
                else:
                    awa = pd.read_sql_query(
                        """
                        SELECT
                            COUNT(DISTINCT CASE WHEN stage = 'TOM' THEN respondent_id END) AS tom_n,
                            COUNT(DISTINCT CASE WHEN stage IN ('TOM', 'SPONT') THEN respondent_id END) AS spont_n,
                            COUNT(DISTINCT CASE WHEN stage = 'AIDED' THEN respondent_id END) AS aided_only_n,
                            COUNT(DISTINCT CASE WHEN stage IN ('TOM', 'SPONT', 'AIDED') THEN respondent_id END) AS total_awa_n,
                            COUNT(DISTINCT CASE WHEN stage = 'CONSIDERATION' THEN respondent_id END) AS consid_n,
                            COUNT(DISTINCT CASE WHEN stage = 'EVER_USED' THEN respondent_id END) AS ever_used_n,
                            COUNT(DISTINCT CASE WHEN stage IN ('CURRENT_USER', 'CURRENT_USE') THEN respondent_id END) AS current_n,
                            COUNT(DISTINCT CASE WHEN stage = 'PREFERRED' THEN respondent_id END) AS preferred_n
                        FROM v_brand_awareness
                        WHERE brand_name = ?
                        """,
                        conn, params=[brand]
                    )

                row = awa.iloc[0]
                tom_n       = int(row["tom_n"] or 0)
                spont_n     = int(row["spont_n"] or 0)
                aided_only_n= int(row["aided_only_n"] or 0)
                total_awa_n = int(row["total_awa_n"] or 0)
                consid_n    = int(row["consid_n"] or 0)
                ever_used_n = int(row["ever_used_n"] or 0)
                current_n   = int(row["current_n"] or 0)
                preferred_n = int(row["preferred_n"] or 0)

                result[brand][seg_label] = {
                    "tom_pct":             round(tom_n       / base_n * 100, 1),
                    "spont_pct":           round(spont_n     / base_n * 100, 1),
                    "aided_pct":           round(aided_only_n/ base_n * 100, 1),
                    "total_awareness_pct": round(total_awa_n / base_n * 100, 1),
                    "consideration_pct":   round(consid_n    / base_n * 100, 1),
                    "ever_used_pct":       round(ever_used_n / base_n * 100, 1),
                    "current_pct":         round(current_n   / base_n * 100, 1),
                    "preferred_pct":       round(preferred_n / base_n * 100, 1),
                    "base_n":              base_n,
                }

        conn.close()
        return result

    def _get_funnel_comparison_from_layer(
        self,
        brands: list,
        segment_type: str = "overall",
        segment_values: list = None,
    ) -> dict:
        """Pandas-layer variant of get_funnel_comparison()."""
        from lens.data_layer import get_project_layer
        layer = get_project_layer(self.project_id)
        if layer is None:
            return {}

        demo_df = layer.demographics
        aw = layer.awareness

        # Determine segments to compute
        seg_col_map = {
            "zone": "zone_name", "gender": "gender",
            "age_band": "age_band", "city": "city_name",
        }
        if segment_type == "overall" or not segment_values:
            segments = [("Overall", None)]
        else:
            col = seg_col_map.get(segment_type, "zone_name")
            segments = [(v, col) for v in segment_values]

        result: dict = {}
        for brand in brands:
            result[brand] = {}
            brand_aw = aw[aw["brand_name"] == brand]

            for seg_label, seg_col in segments:
                if seg_col:
                    seg_val = seg_label
                    seg_resp = set(demo_df[demo_df[seg_col].str.lower() == seg_val.lower()]["respondent_id"]) if seg_col in demo_df.columns else set(demo_df["respondent_id"])
                    base_n = len(seg_resp)
                    b_aw = brand_aw[brand_aw["respondent_id"].isin(seg_resp)]
                else:
                    base_n = len(demo_df)
                    b_aw = brand_aw

                if base_n < 10:
                    result[brand][seg_label] = {"tom_pct": 0, "spont_pct": 0, "aided_pct": 0,
                                                "total_awareness_pct": 0, "consideration_pct": 0,
                                                "ever_used_pct": 0, "current_pct": 0,
                                                "preferred_pct": 0, "base_n": base_n}
                    continue

                def _n(stage):
                    return b_aw[b_aw["stage"] == stage]["respondent_id"].nunique()

                tom_n        = _n("TOM")
                spont_n      = b_aw[b_aw["stage"].isin(["TOM", "SPONT"])]["respondent_id"].nunique()
                aided_only_n = _n("AIDED")
                # unique across all three — pandas AIDED may overlap with SPONT
                total_awa_n  = b_aw[b_aw["stage"].isin(["TOM", "SPONT", "AIDED"])]["respondent_id"].nunique()
                consid_n     = _n("CONSIDERATION")
                ever_used_n  = _n("EVER_USED")
                current_n    = _n("CURRENT_USER")
                preferred_n  = _n("PREFERRED")

                result[brand][seg_label] = {
                    "tom_pct":             round(tom_n        / base_n * 100, 1),
                    "spont_pct":           round(spont_n      / base_n * 100, 1),
                    "aided_pct":           round(aided_only_n / base_n * 100, 1),
                    "total_awareness_pct": round(total_awa_n  / base_n * 100, 1),
                    "consideration_pct":   round(consid_n     / base_n * 100, 1),
                    "ever_used_pct":       round(ever_used_n  / base_n * 100, 1),
                    "current_pct":         round(current_n    / base_n * 100, 1),
                    "preferred_pct":       round(preferred_n  / base_n * 100, 1),
                    "base_n":              base_n,
                }

        return result

    def get_brand_correlation_matrix(self, top_n: int = 15) -> dict:
        """
        Co-awareness matrix: % of respondents aware of both brand A and brand B.
        Returns {brands: [name, ...], matrix: [[val, ...], ...], base_n: int}
        Diagonal = self-awareness (aided%) relative to total base.
        """
        conn = self._get_conn()

        top_brands_df = pd.read_sql_query(
            """
            SELECT brand_name, COUNT(DISTINCT respondent_id) n
            FROM v_brand_awareness
            WHERE brand_name NOT IN ('Others (Specify 1)', 'Others (Specify 2)', 'Don''t Know / None')
            GROUP BY brand_name
            ORDER BY n DESC
            LIMIT ?
            """,
            conn, params=[top_n]
        )
        brands = top_brands_df["brand_name"].tolist()

        base_n = int(
            pd.read_sql_query("SELECT COUNT(*) n FROM v_respondents", conn).iloc[0]["n"]
        )

        brand_resp: dict = {}
        for brand in brands:
            rows = pd.read_sql_query(
                "SELECT DISTINCT respondent_id FROM v_brand_awareness WHERE brand_name = ?",
                conn, params=[brand]
            )
            brand_resp[brand] = set(rows["respondent_id"].tolist())

        conn.close()

        matrix = []
        for a in brands:
            row = []
            for b in brands:
                intersection = len(brand_resp[a] & brand_resp[b])
                row.append(round(intersection / base_n * 100, 1))
            matrix.append(row)

        return {"brands": brands, "matrix": matrix, "base_n": base_n}

    def get_brand_zone_matrix(self, top_n: int = 20) -> dict:
        """
        Brand × zone awareness + NPS matrix for PCA / correspondence map.
        Returns: {brands, zones, tom_matrix, nps_matrix}
        """
        conn = self._get_conn()
        zones = ["North", "South", "East", "West"]

        top_brands_df = pd.read_sql_query(
            """
            SELECT brand_name, COUNT(DISTINCT respondent_id) n
            FROM v_brand_awareness
            WHERE brand_name NOT IN ('Others (Specify 1)', 'Others (Specify 2)', 'Don''t Know / None')
            GROUP BY brand_name
            ORDER BY n DESC
            LIMIT ?
            """,
            conn, params=[top_n]
        )
        brands = top_brands_df["brand_name"].tolist()

        zone_bases: dict = {}
        for z in zones:
            zone_bases[z] = int(
                pd.read_sql_query(
                    "SELECT COUNT(*) n FROM v_respondents WHERE zone_name = ?",
                    conn, params=[z]
                ).iloc[0]["n"]
            )

        tom_matrix, nps_matrix = [], []
        for brand in brands:
            tom_row, nps_row = [], []
            for zone in zones:
                awa = pd.read_sql_query(
                    """
                    SELECT stage, COUNT(DISTINCT respondent_id) n
                    FROM v_brand_awareness
                    WHERE brand_name = ? AND zone_name = ?
                    GROUP BY stage
                    """,
                    conn, params=[brand, zone]
                )
                sc = dict(zip(awa["stage"], awa["n"]))
                tom_n = sc.get("TOM", 0)
                zb = zone_bases[zone]
                tom_row.append(round(tom_n / zb * 100, 1) if zb > 0 else 0)

                nps_row_data = pd.read_sql_query(
                    """
                    SELECT COUNT(*) n,
                           SUM(CASE WHEN nps_score>=9 THEN 1 ELSE 0 END) p,
                           SUM(CASE WHEN nps_score<=6 THEN 1 ELSE 0 END) d
                    FROM v_brand_nps WHERE brand_name = ? AND zone_name = ?
                    """,
                    conn, params=[brand, zone]
                ).iloc[0]
                nb = int(nps_row_data["n"])
                if nb >= 15:
                    nps_val = round((nps_row_data["p"] - nps_row_data["d"]) / nb * 100, 1)
                    nps_norm = round((nps_val + 100) / 2.0, 1)
                else:
                    nps_norm = 50.0
                nps_row.append(nps_norm)

            tom_matrix.append(tom_row)
            nps_matrix.append(nps_row)

        conn.close()
        return {
            "brands":     brands,
            "zones":      zones,
            "tom_matrix": tom_matrix,
            "nps_matrix": nps_matrix,
        }
