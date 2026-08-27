"""
LENS Market Analytics Toolkit
==============================
Pure-Python statistical tools. No LLM involvement in calculation.
Every number is computed directly from the database.

The LLM's job is ONLY to narrate/interpret pre-computed results.
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional
import sys, os
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "oxdata"))
try:
    from db_loader import get_db_path as _get_db_path
    DB_PATH = _get_db_path()
except Exception:
    # fallback: search common locations
    _here = Path(__file__).resolve().parent.parent.parent
    DB_PATH = next(
        (p for p in [
            _here / "data" / "project_1" / "oxdata.db",
            _here / "oxdata" / "data" / "project_1" / "oxdata.db",
        ] if p.exists()),
        _here / "lens.db",  # last resort (may not exist)
    )

# 
# CATEGORY  MG bq3a attribute prefix mapping
# bq3a_1 = CF, bq3a_2 = AC, bq3a_3 = MG, bq3a_4 = LED, bq3a_5 = WH, bq3a_6 = WP
# 
CAT_ATTR_PREFIX = {
    "Mixer Grinder": "bq3a_3_",
    "Ceiling Fans": "bq3a_1_",
    "Air Coolers": "bq3a_2_",
    "LED Batten": "bq3a_4_",
    "Water Heater": "bq3a_5_",
    "Water Pumps": "bq3a_6_",
}

# Brand NPS variables for each category (bq2b_X = brand label)
# All categories share the same bq2b_X codes but different brands are shown
# We'll auto-discover from the variables table.

def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _df(conn, sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)

def _get_choice_map(variable_code: str) -> dict:
    """Fetch value -> label mapping from choices table for a variable."""
    conn = _conn()
    df = _df(conn, "SELECT choice_value, label_en FROM choices WHERE variable_code = ?", (variable_code,))
    conn.close()
    return {str(int(row["choice_value"]) if isinstance(row["choice_value"], (int, float)) else row["choice_value"]): row["label_en"] 
            for _, row in df.iterrows()}


# 
# TOOL 1  Brand NPS / Recommendation Score Comparison
# 
def brand_nps_comparison(category: str = "all") -> dict:
    """
    Compare brands on recommendation likelihood (bq2b, 010 scale).
    Returns: mean, median, n, Top Box (10), top-2-box%, bottom-2-box%, NPS proxy.
    
    NPS proxy = %Promoters(910)  %Detractors(06)
    """
    conn = _conn()
    
    cat_filter = "AND rsp.category = ?" if category.lower() != "all" else ""
    params = (category,) if category.lower() != "all" else ()

    # All bq2b_X variables = brand-level recommendation scores
    df = _df(conn, f"""
        SELECT v.variable_code, v.label_en AS brand_name,
               r.value_numeric AS score,
               rsp.category, rsp.city, rsp.gender, rsp.sec_class, rsp.zone
        FROM variables v
        JOIN responses r ON v.variable_code = r.variable_code
        JOIN respondents rsp ON r.respondent_id = rsp.respondent_id
        WHERE v.variable_code LIKE 'bq2b_%'
          AND v.variable_code != 'bq2b'
          AND v.section = 'Brand Health - Usage'
          AND LENGTH(v.label_en) < 50
          AND r.value_numeric IS NOT NULL
          {cat_filter}
    """, params)
    conn.close()

    if df.empty:
        return {"error": f"No NPS data found for {category}", "data": []}

    results = []
    for brand, grp in df.groupby("brand_name"):
        scores = grp["score"].dropna()
        n = len(scores)
        if n < 5:
            continue
        mean_score = round(scores.mean(), 2)
        median_score = round(scores.median(), 2)

        #  10-point scale (bq2b: 010) 
        # Top Box (TB)         = 10 only
        # Top 2 Box (T2B)      = 9 + 10   Promoters
        # Bottom 6 Box (B6B)   = 0+1+2+3+4+5  Detractors
        # Passives             = 6+7+8
        # NPS = T2B%  B6B%
        tb_pct   = round((scores == 10).sum() / n * 100, 1)
        t2b_pct  = round((scores >= 9).sum() / n * 100, 1)   # Promoters (910)
        b6b_pct  = round((scores <= 5).sum() / n * 100, 1)   # Detractors (05)
        pass_pct = round((scores.between(6, 8)).sum() / n * 100, 1)  # Passives (68)
        nps      = round(t2b_pct - b6b_pct, 1)               # T2B  B6B

        results.append({
            "brand":          brand,
            "mean_score":     mean_score,
            "median_score":   median_score,
            "nps":            nps,
            "top_box_pct":    tb_pct,    # TB  = 10
            "t2b_pct":        t2b_pct,   # T2B = 910 (Promoters %)
            "passives_pct":   pass_pct,  # Passives = 68
            "b6b_pct":        b6b_pct,   # B6B = 05 (Detractors %)
            "n":              int(n),
        })

    results.sort(key=lambda x: x["nps"], reverse=True)
    leader  = results[0]  if results else {}
    laggard = results[-1] if results else {}

    return {
        "metric": "NPS (T2B %  B6B %) | 10-pt scale  bq2b",
        "scale_note": "T2B = 910 (Promoters) | Passives = 68 | B6B = 05 (Detractors) | NPS = T2B  B6B",
        "category": category,
        "n_brands": len(results),
        "total_responses": int(df.shape[0]),
        "leader":  leader,
        "laggard": laggard,
        "gap_leader_to_laggard": round(leader.get("nps", 0) - laggard.get("nps", 0), 1) if results else 0,
        "brands": results,
        "chart": {
            "type": "bar",
            "title": f"NPS by Brand  {category}",
            "x_label": "Brand",
            "y_label": "NPS (T2B  B6B)",
            "data": [{"label": r["brand"], "value": r["nps"]} for r in results]
        }
    }


# 
# TOOL 2  Overall Satisfaction by City (bq5)
# 
def satisfaction_by_city(category: str = "Mixer Grinder") -> dict:
    """
    Average bq5 satisfaction (010) broken down by city.
    Also computes top-2-box % (910) and bottom-2-box % (04).
    """
    conn = _conn()
    df = _df(conn, """
        SELECT rsp.city, r.value_numeric AS score,
               rsp.gender, rsp.sec_class, rsp.zone
        FROM respondents rsp
        JOIN responses r ON rsp.respondent_id = r.respondent_id
        WHERE r.variable_code = 'bq5'
          AND rsp.category = ?
          AND r.value_numeric IS NOT NULL
    """, (category,))
    conn.close()

    if df.empty:
        return {"error": f"No bq5 data for {category}"}

    overall_mean = round(df["score"].mean(), 2)
    overall_n = len(df)

    city_stats = []
    for city, grp in df.groupby("city"):
        scores = grp["score"]
        n = len(scores)
        if n < 10:
            continue
        mean_s = round(scores.mean(), 2)
        # 10-point scale (bq5: 010)
        # T2B = 9+10  |  B6B = 05
        t2b = round((scores >= 9).mean() * 100, 1)   # T2B
        b6b = round((scores <= 5).mean() * 100, 1)   # B6B
        city_stats.append({
            "city":             city,
            "mean_satisfaction": mean_s,
            "t2b_pct":          t2b,    # T2B = 910
            "b6b_pct":          b6b,    # B6B = 05
            "vs_average":       round(mean_s - overall_mean, 2),
            "n":                int(n),
        })

    city_stats.sort(key=lambda x: x["mean_satisfaction"], reverse=True)

    # Seg by gender
    gender_stats = {}
    for g, grp in df.groupby("gender"):
        gender_stats[g] = {
            "mean": round(grp["score"].mean(), 2),
            "n": len(grp),
            "t2b_pct": round((grp["score"] >= 9).mean() * 100, 1),  # T2B
        }

    # Seg by SEC
    sec_stats = {}
    for s, grp in df.groupby("sec_class"):
        sec_stats[s] = {
            "mean": round(grp["score"].mean(), 2),
            "n": len(grp),
        }

    return {
        "metric": "Overall Satisfaction (bq5) | 10-pt scale",
        "scale_note": "T2B = 910 | B6B = 05",
        "category": category,
        "overall_mean": overall_mean,
        "overall_n": int(overall_n),
        "overall_t2b_pct": round((df["score"] >= 9).mean() * 100, 1),  # T2B
        "overall_b6b_pct": round((df["score"] <= 5).mean() * 100, 1),  # B6B
        "std_dev": round(df["score"].std(), 2),
        "best_city":  city_stats[0]  if city_stats else {},
        "worst_city": city_stats[-1] if city_stats else {},
        "city_range": round(city_stats[0]["mean_satisfaction"] - city_stats[-1]["mean_satisfaction"], 2) if city_stats else 0,
        "cities":           city_stats,
        "gender_breakdown": gender_stats,
        "sec_breakdown":    sec_stats,
        "chart": {
            "type": "bar",
            "title": f"Avg Satisfaction by City  {category}",
            "x_label": "City",
            "y_label": "Mean Satisfaction (010)",
            "data": [{"label": c["city"], "value": c["mean_satisfaction"]} for c in city_stats]
        }
    }


# 
# TOOL 3  Attribute Importance Ranking (bq3a, 17)
# 
def attribute_importance_ranking(category: str = "Mixer Grinder",
                                  feature_bucket: Optional[str] = None,
                                  top_n: int = 15) -> dict:
    """
    Rank all need attributes by mean importance (17 scale).
    Converts to % of max (7  100) for intuitive communication.
    Groups by feature_bucket.
    """
    prefix = CAT_ATTR_PREFIX.get(category, "bq3a_3_")
    conn = _conn()

    query = """
        SELECT v.variable_code, v.label_en, v.feature_bucket,
               r.value_numeric AS score
        FROM variables v
        JOIN responses r ON v.variable_code = r.variable_code
        JOIN respondents rsp ON r.respondent_id = rsp.respondent_id
        WHERE v.variable_code LIKE ?
          AND LENGTH(v.variable_code) > LENGTH(?)
          AND r.value_numeric IS NOT NULL
          AND rsp.category = ?
    """
    params = [prefix + "%", prefix, category]

    if feature_bucket:
        query += " AND v.feature_bucket = ?"
        params.append(feature_bucket)

    df = _df(conn, query, params)
    conn.close()

    if df.empty:
        return {"error": f"No attribute data for {category}"}

    # Clean labels  strip "[Importance]" suffix
    df["label_en"] = df["label_en"].str.replace(r"\s*\[Importance\]", "", regex=True).str.strip()

    attr_stats = []
    for (code, label, bucket), grp in df.groupby(["variable_code", "label_en", "feature_bucket"]):
        scores = grp["score"].dropna()
        n = len(scores)
        if n < 20:
            continue
        mean_imp = round(scores.mean(), 3)
        pct_max  = round(mean_imp / 7 * 100, 1)

        #  7-point scale (bq3a: 17) 
        # Top Box (TB)   = 7 only
        # Top 2 Box (T2B) = 6 + 7  (% saying "Very Important" or "Important")
        # Bottom 2 Box (B2B) = 1 + 2
        tb_pct  = round((scores == 7).mean() * 100, 1)   # TB  = 7
        t2b_pct = round((scores >= 6).mean() * 100, 1)   # T2B = 67
        b2b_pct = round((scores <= 2).mean() * 100, 1)   # B2B = 12

        attr_stats.append({
            "variable_code":  code,
            "attribute":      label,
            "feature_bucket": bucket or "General",
            "mean_importance": mean_imp,
            "pct_of_max":     pct_max,
            "tb_pct":         tb_pct,   # TB  = 7/7
            "t2b_pct":        t2b_pct,  # T2B = 67/7
            "b2b_pct":        b2b_pct,  # B2B = 12/7
            "n":              int(n),
        })

    attr_stats.sort(key=lambda x: x["mean_importance"], reverse=True)
    top = attr_stats[:top_n]
    ranked = list(range(1, len(attr_stats) + 1))
    for i, a in enumerate(attr_stats):
        a["rank"] = i + 1

    # Feature bucket averages
    bucket_df = pd.DataFrame(attr_stats)
    bucket_avgs = []
    if not bucket_df.empty:
        for bkt, grp in bucket_df.groupby("feature_bucket"):
            bucket_avgs.append({
                "bucket": bkt,
                "mean_importance": round(grp["mean_importance"].mean(), 2),
                "n_attributes": len(grp),
            })
        bucket_avgs.sort(key=lambda x: x["mean_importance"], reverse=True)

    overall_mean = round(pd.DataFrame(attr_stats)["mean_importance"].mean(), 2) if attr_stats else 0

    return {
        "metric": "Attribute Importance (bq3a, 17 scale  % of max)",
        "category": category,
        "overall_mean_importance": overall_mean,
        "overall_pct_of_max": round(overall_mean / 7 * 100, 1),
        "n_attributes": len(attr_stats),
        "top_attribute": top[0] if top else {},
        "bottom_attribute": attr_stats[-1] if attr_stats else {},
        "gap_top_bottom": round(
            (top[0]["mean_importance"] - attr_stats[-1]["mean_importance"]) if top and attr_stats else 0, 3
        ),
        "top_attributes": top,
        "all_attributes": attr_stats,
        "feature_bucket_averages": bucket_avgs,
        "chart": {
            "type": "bar",
            "title": f"Top {top_n} Attribute Importance  {category}",
            "x_label": "Attribute",
            "y_label": "Mean Importance (17)",
            "data": [{"label": a["attribute"][:40], "value": a["mean_importance"]} for a in top]
        }
    }


# 
# TOOL 4  ImportanceSatisfaction (IS) Gap Analysis
# 
def importance_satisfaction_gap(category: str = "Mixer Grinder") -> dict:
    """
    Computes Importance (bq3a, 17) vs Satisfaction (bq5, 010 scaled to 17).
    Identifies: Critical Pain Points (high imp, low sat),
    Hidden Strengths (low imp, high sat),
    Competitive Differentiators (high imp, high sat).
    
    Satisfaction is rescaled: sat_scaled = bq5 / 10 * 6 + 1  ( 17 scale)
    """
    prefix = CAT_ATTR_PREFIX.get(category, "bq3a_3_")
    conn = _conn()

    imp_df = _df(conn, """
        SELECT v.variable_code, v.label_en, v.feature_bucket,
               AVG(r.value_numeric) AS mean_importance,
               COUNT(r.value_numeric) AS n_imp
        FROM variables v
        JOIN responses r ON v.variable_code = r.variable_code
        JOIN respondents rsp ON r.respondent_id = rsp.respondent_id
        WHERE v.variable_code LIKE ?
          AND LENGTH(v.variable_code) > LENGTH(?)
          AND r.value_numeric IS NOT NULL
          AND rsp.category = ?
        GROUP BY v.variable_code, v.label_en, v.feature_bucket
        HAVING n_imp >= 20
    """, (prefix + "%", prefix, category))

    # Overall satisfaction (bq5) for same category respondents
    sat_overall = _df(conn, """
        SELECT AVG(r.value_numeric) AS mean_sat, COUNT(*) AS n_sat
        FROM responses r
        JOIN respondents rsp ON r.respondent_id = rsp.respondent_id
        WHERE r.variable_code = 'bq5'
          AND rsp.category = ?
          AND r.value_numeric IS NOT NULL
    """, (category,))
    conn.close()

    if imp_df.empty:
        return {"error": f"No importance data for {category}"}

    imp_df["label_en"] = imp_df["label_en"].str.replace(r"\s*\[Importance\]", "", regex=True).str.strip()
    imp_df["mean_importance"] = imp_df["mean_importance"].round(2)

    # Scale satisfaction to 17
    mean_sat_raw = sat_overall["mean_sat"].iloc[0] if not sat_overall.empty else None
    mean_sat_scaled = round(float(mean_sat_raw) / 10 * 6 + 1, 2) if mean_sat_raw is not None else None
    n_sat = int(sat_overall["n_sat"].iloc[0]) if not sat_overall.empty else 0

    # Use overall satisfaction as the proxy (we don't have attribute-level satisfaction)
    # The IS gap = importance - sat_scaled (positive = unmet need)
    analysis = []
    for _, row in imp_df.iterrows():
        imp = float(row["mean_importance"])
        # Do not replace mean_sat_scaled with 0 if it's None. Just skip gap calc.
        gap = round(imp - mean_sat_scaled, 2) if mean_sat_scaled is not None else None
        analysis.append({
            "attribute": row["label_en"],
            "variable_code": row["variable_code"],
            "feature_bucket": row["feature_bucket"] or "General",
            "mean_importance": imp,
            "estimated_satisfaction_scaled": mean_sat_scaled,
            "is_gap": gap,
            "n_importance": int(row["n_imp"]),
        })

    analysis.sort(key=lambda x: x["mean_importance"], reverse=True)

    # Quadrant classification based on importance median
    imp_median = np.median([a["mean_importance"] for a in analysis])

    critical_pain_points = [
        a for a in analysis
        if a["mean_importance"] >= imp_median and (a["is_gap"] or 0) > 0.5
    ]
    competitive_differentiators = [
        a for a in analysis
        if a["mean_importance"] >= imp_median and (a["is_gap"] or 0) <= 0.5
    ]
    low_priority = [
        a for a in analysis if a["mean_importance"] < imp_median
    ]

    return {
        "metric": "ImportanceSatisfaction Gap Analysis",
        "category": category,
        "overall_satisfaction_raw": round(float(mean_sat_raw), 2) if mean_sat_raw else None,
        "overall_satisfaction_scaled_1_7": mean_sat_scaled,
        "n_satisfaction_respondents": n_sat,
        "importance_median": round(float(imp_median), 2),
        "critical_pain_points": critical_pain_points[:8],
        "competitive_differentiators": competitive_differentiators[:8],
        "low_priority_attributes": low_priority[:5],
        "chart": {
            "type": "bar",
            "title": f"Top Attributes by Importance vs Satisfaction Gap  {category}",
            "x_label": "Attribute",
            "y_label": "Mean Importance (17)",
            "data": [{"label": a["attribute"][:40], "value": a["mean_importance"]}
                     for a in analysis[:10]]
        }
    }


# 
# TOOL 5  Demographic Cross-Tab (satisfaction or any metric by segment)
# 
def demographic_crosstab(variable_code: str = "bq5",
                         segment: str = "gender",
                         category: str = "Mixer Grinder") -> dict:
    """
    Cross-tab a numeric variable by a demographic segment.
    segment options: gender, city, zone, age_band, sec_class
    """
    valid_segments = {"gender", "city", "zone", "age_band", "sec_class"}
    if segment not in valid_segments:
        return {"error": f"Invalid segment '{segment}'. Must be one of: {valid_segments}"}

    conn = _conn()
    df = _df(conn, f"""
        SELECT rsp.{segment} AS segment_value,
               r.value_numeric AS score,
               v.label_en AS metric_label
        FROM respondents rsp
        JOIN responses r ON rsp.respondent_id = r.respondent_id
        JOIN variables v ON r.variable_code = v.variable_code
        WHERE r.variable_code = ?
          AND rsp.category = ?
          AND r.value_numeric IS NOT NULL
    """, (variable_code, category))
    conn.close()

    if df.empty:
        return {"error": f"No data for {variable_code} / {category}"}

    metric_label = df["metric_label"].iloc[0] if not df.empty else variable_code
    overall_mean = round(df["score"].mean(), 2)
    overall_n = len(df)

    seg_stats = []
    for seg_val, grp in df.groupby("segment_value"):
        scores = grp["score"]
        n = len(scores)
        if n < 5:
            continue
            
        mean_s = round(scores.mean(), 2)
        
        # If this is bq2b (NPS), we calculate the actual NPS proxy in addition to mean
        if variable_code.lower() == 'bq2b':
            promoters = round((scores >= 9).mean() * 100, 1)
            detractors = round((scores <= 6).mean() * 100, 1)
            nps = round(promoters - detractors, 1)
            seg_stats.append({
                "segment": str(seg_val),
                "mean": mean_s,
                "nps": nps,
                "promoters_pct": promoters,
                "detractors_pct": detractors,
                "n": int(n),
                "share_pct": round(n / overall_n * 100, 1),
                "vs_overall_mean": round(mean_s - overall_mean, 2),
            })
        else:
            std_s = round(scores.std(), 2)
            seg_stats.append({
                "segment": str(seg_val),
                "mean": mean_s,
                "std": std_s,
                "n": int(n),
                "share_pct": round(n / overall_n * 100, 1),
                "vs_overall": round(mean_s - overall_mean, 2),
            })

    seg_stats.sort(key=lambda x: x["mean"], reverse=True)

    return {
        "metric": f"{metric_label} by {segment}",
        "variable_code": variable_code,
        "segment": segment,
        "category": category,
        "overall_mean": overall_mean,
        "overall_n": int(overall_n),
        "segments": seg_stats,
        "best_segment": seg_stats[0] if seg_stats else {},
        "worst_segment": seg_stats[-1] if seg_stats else {},
        "range": round(seg_stats[0]["mean"] - seg_stats[-1]["mean"], 2) if seg_stats else 0,
        "chart": {
            "type": "bar",
            "title": f"{metric_label[:40]} by {segment.replace('_', ' ').title()}  {category}",
            "x_label": segment.replace("_", " ").title(),
            "y_label": "Mean Score",
            "data": [{"label": s["segment"], "value": s["mean"]} for s in seg_stats]
        }
    }


# 
# TOOL 6  Feature Bucket Importance Summary
# 
def feature_bucket_summary(category: str = "Mixer Grinder") -> dict:
    """
    Aggregate importance scores by feature bucket (Product Performance,
    Design, After Sales Support, etc.).
    Shows which category of features matters most overall.
    """
    prefix = CAT_ATTR_PREFIX.get(category, "bq3a_3_")
    conn = _conn()
    df = _df(conn, """
        SELECT v.feature_bucket, r.value_numeric AS score, v.variable_code
        FROM variables v
        JOIN responses r ON v.variable_code = r.variable_code
        JOIN respondents rsp ON r.respondent_id = rsp.respondent_id
        WHERE v.variable_code LIKE ?
          AND LENGTH(v.variable_code) > LENGTH(?)
          AND v.feature_bucket IS NOT NULL
          AND r.value_numeric IS NOT NULL
          AND rsp.category = ?
    """, (prefix + "%", prefix, category))
    conn.close()

    if df.empty:
        return {"error": f"No feature bucket data for {category}"}

    overall_mean = round(df["score"].mean(), 2)
    bucket_stats = []
    for bucket, grp in df.groupby("feature_bucket"):
        scores = grp["score"]
        n_attrs = grp["variable_code"].nunique()
        bucket_stats.append({
            "bucket": bucket,
            "mean_importance": round(scores.mean(), 2),
            "pct_of_max": round(scores.mean() / 7 * 100, 1),
            "n_attributes": int(n_attrs),
            "n_responses": int(len(scores)),
            "vs_overall": round(scores.mean() - overall_mean, 2),
        })

    bucket_stats.sort(key=lambda x: x["mean_importance"], reverse=True)

    return {
        "metric": "Feature Bucket Importance Summary",
        "category": category,
        "overall_mean_importance": overall_mean,
        "n_buckets": len(bucket_stats),
        "top_bucket": bucket_stats[0] if bucket_stats else {},
        "bottom_bucket": bucket_stats[-1] if bucket_stats else {},
        "buckets": bucket_stats,
        "chart": {
            "type": "bar",
            "title": f"Importance by Feature Category  {category}",
            "x_label": "Feature Category",
            "y_label": "Mean Importance (17)",
            "data": [{"label": b["bucket"], "value": b["mean_importance"]} for b in bucket_stats]
        }
    }


# 
# TOOL 7  Category Sample Statistics (population overview)
# 
def category_overview(category: str = "Mixer Grinder") -> dict:
    """
    Basic population statistics for a category:
    total respondents, gender split, zone split, age profile,
    SEC class distribution, city count.
    """
    conn = _conn()
    df = _df(conn, """
        SELECT gender, zone, age_band, sec_class, city, respondent_type, is_affluent
        FROM respondents
        WHERE category = ?
    """, (category,))
    conn.close()

    if df.empty:
        return {"error": f"No respondents for {category}"}

    n = len(df)

    def pct_breakdown(col):
        vc = df[col].value_counts()
        return [{"value": str(k), "count": int(v), "pct": round(v / n * 100, 1)}
                for k, v in vc.items()]

    return {
        "category": category,
        "total_respondents": int(n),
        "gender_breakdown": pct_breakdown("gender"),
        "zone_breakdown": pct_breakdown("zone"),
        "age_band_breakdown": pct_breakdown("age_band"),
        "sec_class_breakdown": pct_breakdown("sec_class"),
        "city_count": int(df["city"].nunique()),
        "affluent_pct": round(df["is_affluent"].mean() * 100, 1),
        "respondent_type_breakdown": pct_breakdown("respondent_type"),
    }


# 
# TOOL 8  Appliance Recent Purchase Ranking (MQ3B)
# 
def appliance_recent_purchase_ranking(category: str = "all") -> dict:
    """
    Analyzes MQ3b (which are the 3 appliances that you have purchased recently).
    value_text is normalized space-separated integers (e.g. "4 8 10").
    value_rank1 holds the rank-1 (first-chosen) appliance code directly.
    """
    conn = _conn()
    df = _df(conn, """
        SELECT r.value_text, r.value_rank1
        FROM responses r
        WHERE r.variable_code = 'mq3b'
          AND r.value_text IS NOT NULL
    """)
    conn.close()

    if df.empty:
        return {"error": "No data found for mq3b"}

    rank1_counts = {}
    any_rank_counts = {}

    for _, row in df.iterrows():
        vt = row["value_text"]
        r1 = row["value_rank1"]

        items = [x for x in str(vt).split() if x.isdigit()]

        for item in items:
            any_rank_counts[item] = any_rank_counts.get(item, 0) + 1

        # Use value_rank1 directly for rank-1 count
        if r1 is not None and not pd.isna(r1):
            key = str(int(r1))
            rank1_counts[key] = rank1_counts.get(key, 0) + 1

    total_responses = len(df)
    
    # Map appliance codes to names
    app_map = _get_choice_map("mq3b")
    
    # Let's map appliance codes back if not available, we just return codes
    results = []
    for code, count in any_rank_counts.items():
        name = app_map.get(str(code), f"Appliance {code}")
        results.append({
            "appliance": name,
            "appliance_code": code,
            "rank_1_count": rank1_counts.get(code, 0),
            "rank_1_pct": round(rank1_counts.get(code, 0) / total_responses * 100, 1),
            "any_rank_count": count,
            "any_rank_pct": round(count / total_responses * 100, 1)
        })

    results.sort(key=lambda x: x["any_rank_count"], reverse=True)

    return {
        "metric": "Recent Purchase Sequence (MQ3b)",
        "category": category,
        "total_responses": int(total_responses),
        "rankings": results,
        "chart": {
            "type": "bar",
            "title": "Top Recent Purchases",
            "x_label": "Appliance",
            "y_label": "Total Mentions",
            "data": [{"label": r["appliance"], "value": r["any_rank_count"]} for r in results[:10]]
        }
    }


# 
# TOOL 9  Brand Funnel Deep Dive (BQ1 Series)
# 
def brand_funnel_deep_dive(category: str = "all") -> dict:
    """
    Computes the Brand Funnel:
    TOM (bq1a) -> Spontaneous (bq1b) -> Awareness (bq1c) -> Ever Used (bq1d) ->
    Current Use (bq1e) -> Consideration (bq1f) -> Preference (bq1g) -> Purchase/Recent (bq1h)

    bq1b (spont_multi) and bq1c (aided_multi) have normalized space-separated value_text.
    value_rank1 holds the first-mentioned brand for ordered questions (bq1b).
    """
    conn = _conn()

    cat_filter = "AND rsp.category = ?" if category.lower() != "all" else ""
    params = (category,) if category.lower() != "all" else ()

    df = _df(conn, f"""
        SELECT r.variable_code, r.value_text, r.value_numeric, r.value_rank1,
               rsp.respondent_id
        FROM responses r
        JOIN respondents rsp ON r.respondent_id = rsp.respondent_id
        WHERE r.variable_code LIKE 'bq1%'
          {cat_filter}
    """, params)

    conn.close()

    if df.empty:
        return {"error": "No brand funnel data found"}

    brand_stages = {}

    def count_occurrences(sub_df, stage_key):
        for _, row in sub_df.iterrows():
            vt = row["value_text"]
            vn = row["value_numeric"]

            items = []
            if vt is not None and pd.notna(vt) and isinstance(vt, str):
                items = [str(x) for x in str(vt).split() if str(x).isdigit()]
            elif vn is not None and pd.notna(vn):
                items = [str(int(vn))]

            for item in items:
                if item not in brand_stages:
                    brand_stages[item] = {
                        "TOM": 0, "Spontaneous": 0, "SpentRank1": 0,
                        "TotalRecall": 0, "EverUsed": 0, "CurrentUse": 0,
                        "Consideration": 0, "Preference": 0, "RecentPurchase": 0
                    }
                brand_stages[item][stage_key] += 1

    def count_rank1(sub_df, rank1_key):
        """Count first-mentioned brand (value_rank1) for ordered multi-select."""
        for _, row in sub_df.iterrows():
            r1 = row["value_rank1"]
            if r1 is not None and not pd.isna(r1):
                key = str(int(r1))
                if key not in brand_stages:
                    brand_stages[key] = {
                        "TOM": 0, "Spontaneous": 0, "SpentRank1": 0,
                        "TotalRecall": 0, "EverUsed": 0, "CurrentUse": 0,
                        "Consideration": 0, "Preference": 0, "RecentPurchase": 0
                    }
                brand_stages[key][rank1_key] += 1

    # Stage maps
    count_occurrences(df[df["variable_code"] == "bq1a"], "TOM")
    count_occurrences(df[df["variable_code"] == "bq1b"], "Spontaneous")
    count_rank1(df[df["variable_code"] == "bq1b"], "SpentRank1")   # first brand mentioned = strongest spont recall
    count_occurrences(df[df["variable_code"] == "bq1c"], "TotalRecall")
    count_occurrences(df[df["variable_code"] == "bq1d"], "EverUsed")
    count_occurrences(df[df["variable_code"] == "bq1e"], "CurrentUse")
    count_occurrences(df[df["variable_code"] == "bq1f"], "Consideration")
    count_occurrences(df[df["variable_code"] == "bq1g"], "Preference")
    count_occurrences(df[df["variable_code"] == "bq1h"], "RecentPurchase")

    # Brand names are stored under bq1a in the choices table (bq1b/bq1c share the same list)
    brand_map = _get_choice_map("bq1a")

    total_n = df["respondent_id"].nunique()
    
    results = []
    for brand, metrics in brand_stages.items():
        if metrics["TotalRecall"] < 10 and metrics["TOM"] < 10: 
            continue # Filter very low noise
            
        tom   = metrics["TOM"]
        spont = metrics["Spontaneous"]
        sr1   = metrics["SpentRank1"]   # first brand mentioned in bq1b
        aw    = metrics["TotalRecall"]
        ever  = metrics["EverUsed"]
        curr  = metrics["CurrentUse"]
        cons  = metrics["Consideration"]
        pref  = metrics["Preference"]
        purch = metrics["RecentPurchase"]

        # Avoid divide by zero
        aw_rate    = round(aw   / total_n * 100, 1) if total_n else 0
        ever_rate  = round(ever / aw      * 100, 1) if aw   else 0
        curr_rate  = round(curr / ever    * 100, 1) if ever else 0
        cons_rate  = round(cons / aw      * 100, 1) if aw   else 0
        pref_rate  = round(pref / cons    * 100, 1) if cons else 0
        purch_rate = round(purch / pref   * 100, 1) if pref else (round(purch / cons * 100, 1) if cons else 0)

        brand_name = brand_map.get(str(brand), f"Brand {brand}")

        results.append({
            "brand": brand_name,
            "brand_code": str(brand),
            "tom_mentions": tom,
            "spont_mentions": spont,
            "spont_rank1_mentions": sr1,
            "awareness_mentions": aw,
            "awareness_rate_pct": aw_rate,
            "ever_used_mentions": ever,
            "ever_used_conversion_pct": ever_rate,
            "current_use_mentions": curr,
            "current_use_conversion_pct": curr_rate,
            "consideration_mentions": cons,
            "consideration_conversion_pct": cons_rate,
            "preference_mentions": pref,
            "preference_conversion_pct": pref_rate,
            "purchase_mentions": purch,
            "purchase_conversion_pct": purch_rate
        })
        
    results.sort(key=lambda x: x["awareness_mentions"], reverse=True)
    
    return {
        "metric": "Brand Funnel Metrics",
        "category": category,
        "total_respondents": int(total_n),
        "top_brands": results[:15], # Return top 15 by awareness
    }


# 
# TOOL 10  Appliance Penetration (MQ2a / MQ2b)
# 
def appliance_penetration_overview(category: str = "all") -> dict:
    """
    Calculates Penetration % based on ownership binary questions (mq2a)
    Allows grouping households into Ownership Tiers for insight.
    """
    conn = _conn()
    cat_filter = "AND rsp.category = ?" if category.lower() != "all" else ""
    params = (category,) if category.lower() != "all" else ()
    
    # Using mq2a text containing list of appliances owned
    df = _df(conn, f"""
        SELECT r.value_text, r.value_numeric, rsp.respondent_id
        FROM responses r
        JOIN respondents rsp ON r.respondent_id = rsp.respondent_id
        WHERE r.variable_code = 'mq2a'
          {cat_filter}
    """, params)
    conn.close()

    if df.empty:
        return {"error": "No ownership data found"}
        
    total_n = len(df)
    appliance_counts = {}
    tier_counts = {"Low": 0, "Medium": 0, "High": 0}
    
    for _, row in df.iterrows():
        vt = row["value_text"]
        vn = row["value_numeric"]
        
        items = []
        if vt is not None and pd.notna(vt) and isinstance(vt, str):
            items = [str(x) for x in str(vt).split() if str(x).isdigit()]
        elif vn is not None and pd.notna(vn):
            items = [str(int(vn))]
            
        if not items:
            continue
            
        # Categorize household tier by number of appliances
        count = len(items)
        if count <= 2: 
            tier_counts["Low"] += 1
        elif count <= 5:
            tier_counts["Medium"] += 1
        else:
            tier_counts["High"] += 1
            
        for item in items:
            appliance_counts[item] = appliance_counts.get(item, 0) + 1
            
    # Map appliance codes
    app_map = _get_choice_map("mq2a")

    results = []
    for app_code, counts in appliance_counts.items():
        pen_pct = round((counts / total_n) * 100, 1)
        if pen_pct >= 50:
            category_tier = "Core (Near Saturation)"
        elif pen_pct >= 20:
            category_tier = "Secondary (Established)"
        else:
            category_tier = "Emerging / Periphery"
            
        results.append({
            "appliance": app_map.get(str(app_code), f"Appliance {app_code}"),
            "appliance_code": app_code,
            "penetration_pct": pen_pct,
            "category_tier": category_tier,
            "mentions": counts
        })
        
    results.sort(key=lambda x: x["penetration_pct"], reverse=True)
    
    return {
        "metric": "Appliance Penetration & Saturation",
        "category": category,
        "total_households": int(total_n),
        "household_ownership_tiers_pct": {
            "Low_(1-2_items)": round(tier_counts["Low"]/total_n*100, 1),
            "Medium_(3-5_items)": round(tier_counts["Medium"]/total_n*100, 1),
            "High_(6+_items)": round(tier_counts["High"]/total_n*100, 1)
        },
        "appliance_penetrations": results[:20]
    }


# 
# MASTER DISPATCHER  called from app.py
# 
TOOL_REGISTRY = {
    "brand_nps_comparison": {
        "fn": brand_nps_comparison,
        "description": "Compare brands on recommendation/NPS likelihood score",
        "keywords": ["brand", "nps", "recommend", "comparison", "compare brand", "best brand", "which brand"],
    },
    "satisfaction_by_city": {
        "fn": satisfaction_by_city,
        "description": "Overall satisfaction (bq5) by city with gender and SEC breakdown",
        "keywords": ["satisfaction", "happy", "city", "region", "zone", "geographic"],
    },
    "attribute_importance_ranking": {
        "fn": attribute_importance_ranking,
        "description": "Rank all product attributes by consumer importance",
        "keywords": ["important", "importance", "attribute", "feature", "which feature", "what matters"],
    },
    "importance_satisfaction_gap": {
        "fn": importance_satisfaction_gap,
        "description": "Importance vs satisfaction gap  reveals pain points and opportunities",
        "keywords": ["gap", "pain point", "unmet", "opportunity", "disappointment", "expectation"],
    },
    "demographic_crosstab": {
        "fn": demographic_crosstab,
        "description": "Cross-tab any metric by gender, city, age band, SEC class, or zone",
        "keywords": ["gender", "male", "female", "age", "sec", "class", "segment", "demographic"],
    },
    "feature_bucket_summary": {
        "fn": feature_bucket_summary,
        "description": "Aggregate importance by feature category (Performance vs Design vs Service etc.)",
        "keywords": ["category", "bucket", "performance", "design", "service", "after sales"],
    },
    "category_overview": {
        "fn": category_overview,
        "description": "Population overview  sample size, gender/age/SEC distribution",
        "keywords": ["sample", "population", "how many", "respondent", "profile", "overview"],
    },
    "appliance_recent_purchase_ranking": {
        "fn": appliance_recent_purchase_ranking,
        "description": "Determines the sequence and popularity of recently purchased appliances",
        "keywords": ["purchase", "recent", "sequence", "order", "bought", "appliance"],
    },
    "brand_funnel_deep_dive": {
        "fn": brand_funnel_deep_dive,
        "description": "Analyzes the Brand Funnel (TOM -> Awareness -> Consideration -> Purchase)",
        "keywords": ["funnel", "awareness", "consideration", "purchase", "tom", "recall"],
    },
    "appliance_penetration_overview": {
        "fn": appliance_penetration_overview,
        "description": "Calculates penetration and ownership tiers of appliances in households",
        "keywords": ["penetration", "ownership", "saturation", "tier", "appliance count"],
    },
}


def _detect_segment(query_lower: str) -> str:
    """Detect which demographic segment the query is asking about."""
    if any(kw in query_lower for kw in ["age", "cohort", "25-35", "36-50", "young", "older", "millennial"]):
        return "age_band"
    if any(kw in query_lower for kw in ["gender", "male", "female", "men", "women", "housewife"]):
        return "gender"
    if any(kw in query_lower for kw in ["zone", "north", "south", "east", "west", "region"]):
        return "zone"
    if any(kw in query_lower for kw in ["sec", "income", "class", "affluent", "premium"]):
        return "sec_class"
    if any(kw in query_lower for kw in ["city", "cities", "geographic", "location", "where"]):
        return "city"
    return "gender"  # safe default


def select_tools(query: str, category: str = "Mixer Grinder") -> list[dict]:
    """
    Intent-aware tool selection. Each tool has:
      - anchors:  ALL must match (required terms — prevents false positives)
      - supports: at least ONE must match (optional boosters)
    If nothing matches, falls back smartly based on query type.
    """
    import inspect
    query_lower = query.lower()

    # ── Normalize category — tools cannot work with "all" ────────────────────
    if not category or category.lower() in ("all", "unknown", ""):
        category = "Mixer Grinder"

    # ── Tool intent definitions (Exhaustive Mapping) ──────────────────────────
    INTENT_MAP = [
        {
            "tool": "brand_nps_comparison",
            "anchors": [],
            "supports": [
                # Core
                "nps", "net promoter", "recommend", "promoter", "detractor",
                # Comparisons
                "brand comparison", "compare brand", "brand score", "brand rank", "best brand", "which brand",
                # Business Slang
                "loyalty score", "advocacy", "word of mouth", "brand health", "customer loyalty",
                "brand performance"
            ],
        },
        {
            "tool": "satisfaction_by_city",
            "anchors": ["satisfaction"],
            "supports": [
                # Locations
                "city", "cities", "region", "regional", "zone", "geographic", "where", "location",
                # Variants
                "state", "local", "market breakdown by city", "citywise", "regionwise", "zonewise",
                "north", "south", "east", "west"
            ],
        },
        {
            "tool": "attribute_importance_ranking",
            "anchors": [],
            "supports": [
                # Core
                "most important", "importance ranking", "which feature matters", "what matters",
                "top attribute", "rank attribute", "feature importance",
                # Synonyms
                "drivers of satisfaction", "key drivers", "what do people care about",
                "feature priority", "must have feature", "dealbreaker", "purchase criteria",
                "buying criteria", "why do they buy", "what drives choice"
            ],
        },
        {
            "tool": "importance_satisfaction_gap",
            "anchors": [],
            "supports": [
                # Core
                "gap", "pain point", "unmet need", "opportunity area", "disappointment", "expectation vs",
                # Business Strategy
                "white space", "whitespaces", "improvement area", "where to improve",
                "underperforming", "underdelivering", "frustration", "missed expectation",
                "satisfaction gap", "is gap", "performance gap"
            ],
        },
        {
            "tool": "demographic_crosstab",
            "anchors": [],
            "supports": [
                # Gender
                "by gender", "male vs female", "gender difference", "men", "women", "sex", "housewife",
                # Age
                "by age", "age group", "age cohort", "cohort", "age difference",
                "25-35", "36-50", "25 to 35", "36 to 50", "younger", "older", "young", "millennial", "gen z",
                # SEC/Income
                "by sec", "sec class", "income segment", "affluent", "socio economic", "wealth",
                "premium buyer", "budget buyer", "high sec", "low sec",
                # General Profiles
                "demographic breakdown", "profile of buyer", "customer profile", "who is buying",
                "audience profile", "customer segment", "target audience", "by segment"
            ],
        },
        {
            "tool": "feature_bucket_summary",
            "anchors": [],
            "supports": [
                # Core
                "feature category", "performance vs design", "design vs service",
                "after sales importance", "which category of feature", "bucket", "feature group",
                # Synonyms
                "macro features", "feature themes", "theme importance", "overall category driver",
                "service vs product", "looks vs performance"
            ],
        },
        {
            "tool": "category_overview",
            "anchors": [],
            "supports": [
                # Core
                "sample size", "how many respondents", "respondent profile",
                "population overview", "survey size", "who was surveyed", "respondent breakdown",
                # Synonyms
                "base size", "n size", "survey participants", "study methodology", "sample split",
                "who answered", "demographic split", "total sample"
            ],
        },
        {
            "tool": "appliance_recent_purchase_ranking",
            "anchors": [],
            "supports": [
                # Core
                "recent purchase", "last bought", "purchase sequence",
                "what appliance was bought", "purchase ranking", "bought recently",
                # Synonyms
                "latest purchase", "recent addition", "newest appliance", "buying order",
                "purchase history", "recently acquired"
            ],
        },
        {
            "tool": "brand_funnel_deep_dive",
            "anchors": [],
            "supports": [
                # Funnel Stages / Core
                "brand funnel", "funnel metrics", "awareness to purchase", "consideration funnel",
                # Specific Metrics
                "top of mind", "tom awareness", "tom", "unaided recall", "aided awareness", "aided",
                "spontaneous", "spontaneous recall", "brand recall", "total recall",
                "ever used", "trial rate", "current use", "currently used",
                "consideration", "shortlist", "considered brand",
                "preference", "preferred brand", "decision stage", "winning at decision",
                "conversion", "conversion rate", "funnel dropoff", "purchase share",
                "market share proxy"
            ],
        },
        {
            "tool": "appliance_penetration_overview",
            "anchors": [],
            "supports": [
                # Penetration & Tiers
                "penetration", "penetration rate", "saturation", "saturation point",
                "ownership tier", "ownership tiers", "household penetration", "market penetration",
                # General Ownership
                "ownership rate", "how many own", "appliance ownership", "do people own",
                "incidence rate", "category incidence", "core category", "secondary category",
                "periphery category", "emerging category", "appliance count"
            ],
        },
    ]

    selected = []

    for intent in INTENT_MAP:
        tool = intent["tool"]
        anchors = intent["anchors"]
        supports = intent["supports"]

        anchors_ok = all(a in query_lower for a in anchors)
        supports_ok = any(s in query_lower for s in supports)

        if anchors_ok and supports_ok:
            selected.append(tool)

    # Deduplicate, cap at 3 tools to stay focused
    selected = list(dict.fromkeys(selected))[:3]

    # ── Fallback ─────────────────────────────────────────────────────────────
    if not selected:
        quant_signals = ["score", "rating", "percentage", "how many", "average",
                         "mean", "compare", "rank", "breakdown", "distribution",
                         "statistics", "data", "number", "count", "metric"]
        if any(sig in query_lower for sig in quant_signals):
            selected = ["attribute_importance_ranking", "satisfaction_by_city"]
        else:
            selected = []

    # ── Detect segment for demographic_crosstab ───────────────────────────────
    detected_segment = _detect_segment(query_lower)

    results = []
    for tool_name in selected:
        fn = TOOL_REGISTRY[tool_name]["fn"]
        try:
            sig = inspect.signature(fn)
            params = sig.parameters

            # Build kwargs based on what the function actually accepts
            kwargs = {}
            if "category" in params:
                kwargs["category"] = category
            if "segment" in params and tool_name == "demographic_crosstab":
                kwargs["segment"] = detected_segment

            result = fn(**kwargs)
            results.append({
                "tool": tool_name,
                "description": TOOL_REGISTRY[tool_name]["description"],
                "result": result,
            })
        except Exception as e:
            results.append({
                "tool": tool_name,
                "description": TOOL_REGISTRY[tool_name]["description"],
                "result": {"error": str(e)},
            })

    return results


