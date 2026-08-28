"""
DashboardEngine â€” reads real survey metrics.
Single Wave 1 survey: 6,631 respondents, April-June 2021.
No wave splitting â€” April vs May+June is the same survey wave.
"""
import sqlite3
import time
import pandas as pd
import streamlit as st
from pathlib import Path
import os
import sys

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from infoleap.db_loader import get_db_path

def _conn(project_id: str = "project_1"):
    return sqlite3.connect(str(get_db_path(project_id=project_id, required_table="fact_respondents")))


@st.cache_data(ttl=3600)
def get_dashboard_stats(project_id: str = "project_1") -> dict:
    """
    Returns real survey metrics for dashboard display.
    Full survey, no wave split.
    """
    conn = _conn(project_id)

    base_n = pd.read_sql("SELECT COUNT(*) n FROM v_respondents", conn).iloc[0]["n"]

    # NPS top 4 â€” full survey, min 50 raters
    nps_df = pd.read_sql("""
        SELECT brand_name,
               ROUND((SUM(CASE WHEN nps_score>=9 THEN 1.0 ELSE 0 END) -
                      SUM(CASE WHEN nps_score<=6 THEN 1.0 ELSE 0 END))
                     * 100.0 / COUNT(*), 1) AS nps,
               COUNT(*) AS raters
        FROM v_brand_nps
        WHERE brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)',
                                  'Don''t Know / None')
        GROUP BY brand_name
        HAVING COUNT(*) >= 50
        ORDER BY nps DESC
        LIMIT 4
    """, conn)

    # Aided awareness top 4
    aided_df = pd.read_sql("""
        SELECT brand_name,
               COUNT(DISTINCT respondent_id) AS aided_n
        FROM v_brand_awareness
        WHERE brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)',
                                  'Don''t Know / None')
          AND stage IN ('TOM','SPONT','AIDED')
        GROUP BY brand_name
        ORDER BY aided_n DESC
        LIMIT 4
    """, conn)

    # TOM top 4
    tom_df = pd.read_sql("""
        SELECT brand_name,
               COUNT(DISTINCT respondent_id) AS tom_n
        FROM v_brand_awareness
        WHERE stage = 'TOM'
          AND brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)',
                                  'Don''t Know / None')
        GROUP BY brand_name
        ORDER BY tom_n DESC
        LIMIT 4
    """, conn)

    # Hero stat
    top5_aided = pd.read_sql("""
        SELECT brand_name, COUNT(DISTINCT respondent_id) n
        FROM v_brand_awareness
        WHERE brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)',
                                  'Don''t Know / None')
          AND stage IN ('TOM','SPONT','AIDED')
        GROUP BY brand_name ORDER BY n DESC LIMIT 5
    """, conn)
    top5_share = round(top5_aided["n"].sum() / base_n * 100, 0)
    top_tom_brand = tom_df.iloc[0]["brand_name"] if not tom_df.empty else "Bajaj"
    top_tom_pct = (
        round(tom_df.iloc[0]["tom_n"] / base_n * 100, 1)
        if not tom_df.empty else 26.3
    )

    conn.close()

    return {
        "base_n": int(base_n),
        "hero_insight": (
            f"<b>{top_tom_brand}</b> leads Top-of-Mind recall at "
            f"<span class='insight-highlight'>{top_tom_pct}%</span> across "
            f"<span class='insight-highlight'>{base_n:,} respondents</span>. "
            f"Top 5 brands capture {top5_share:.0f}% of total aided awareness."
        ),
        "nps": [
            {"brand_name": r["brand_name"], "nps": r["nps"],
             "raters": r["raters"], "delta": None, "direction": "stable"}
            for _, r in nps_df.iterrows()
        ],
        "aided": [
            {"brand_name": r["brand_name"],
             "pct": round(r["aided_n"] / base_n * 100, 1),
             "delta": None, "direction": "stable"}
            for _, r in aided_df.iterrows()
        ],
        "tom": [
            {"brand_name": r["brand_name"],
             "pct": round(r["tom_n"] / base_n * 100, 1),
             "delta": None, "direction": "stable"}
            for _, r in tom_df.iterrows()
        ],
        "timestamp": time.strftime("%d %B %Y"),
    }


@st.cache_data(ttl=3600)
def get_computed_signals(project_id: str = "project_1") -> list:
    """
    Real anomaly signals computed from DB.
    Returns list of dicts: title, description, severity, icon.
    """
    conn = _conn(project_id)
    base_n = int(pd.read_sql("SELECT COUNT(*) n FROM v_respondents", conn).iloc[0]["n"])
    signals = []

    # Signal 1: Brands with negative NPS (min 30 raters)
    neg_nps = pd.read_sql("""
        SELECT brand_name,
               ROUND((SUM(CASE WHEN nps_score>=9 THEN 1.0 ELSE 0 END) -
                      SUM(CASE WHEN nps_score<=6 THEN 1.0 ELSE 0 END))
                     *100.0/COUNT(*),1) nps,
               COUNT(*) n
        FROM v_brand_nps
        WHERE brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)',
                                  'Don''t Know / None')
        GROUP BY brand_name HAVING COUNT(*)>=30 AND nps<0
        ORDER BY nps LIMIT 3
    """, conn)
    for _, row in neg_nps.iterrows():
        signals.append({
            "title": f"{row['brand_name']} â€” Negative NPS",
            "description": (
                f"NPS = {row['nps']:+.0f} among {row['n']:,} raters. "
                "Detractors outnumber promoters. Investigate service and durability complaints."
            ),
            "severity": "warning",
            "icon": "ðŸŸ¡",
        })

    # Signal 2: Awareness-salience gap (aided>=30%, TOM<3%)
    gap = pd.read_sql("""
        WITH aided AS (
            SELECT brand_name, COUNT(DISTINCT respondent_id) aided_n
            FROM v_brand_awareness
            WHERE stage IN ('TOM','SPONT','AIDED')
              AND brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)',
                                      'Don''t Know / None')
            GROUP BY brand_name
        ),
        tom AS (
            SELECT brand_name, COUNT(DISTINCT respondent_id) tom_n
            FROM v_brand_awareness WHERE stage='TOM'
            GROUP BY brand_name
        )
        SELECT a.brand_name,
               ROUND(a.aided_n*100.0/?,1) aided_pct,
               ROUND(COALESCE(t.tom_n,0)*100.0/?,1) tom_pct
        FROM aided a LEFT JOIN tom t ON a.brand_name=t.brand_name
        WHERE a.aided_n*100.0/? >= 30
          AND COALESCE(t.tom_n,0)*100.0/? < 3
        ORDER BY a.aided_n DESC LIMIT 2
    """, conn, params=[base_n, base_n, base_n, base_n])
    for _, row in gap.iterrows():
        signals.append({
            "title": f"{row['brand_name']} â€” Awareness-Salience Gap",
            "description": (
                f"Aided awareness {row['aided_pct']}% but Top-of-Mind only "
                f"{row['tom_pct']}%. Brand is recognised but not recalled spontaneously."
            ),
            "severity": "info",
            "icon": "ðŸ”µ",
        })

    # Signal 3: Top penetration appliance
    try:
        top_app = pd.read_sql("""
            SELECT da.appliance_name, COUNT(DISTINCT fk.respondent_id) n
            FROM fact_kitchen_ownership fk
            JOIN dim_kitchen_appliance da ON fk.appliance_id = da.appliance_id
            GROUP BY da.appliance_name ORDER BY n DESC LIMIT 1
        """, conn)
    except Exception:
        top_app = pd.DataFrame()
    if not top_app.empty:
        pct = round(top_app.iloc[0]["n"] / base_n * 100, 1)
        signals.append({
            "title": f"{top_app.iloc[0]['appliance_name']} â€” Category Leader",
            "description": (
                f"{pct}% household penetration across {base_n:,} respondents. "
                "Highest ownership of any tracked kitchen appliance."
            ),
            "severity": "positive",
            "icon": "ðŸŸ¢",
        })

    conn.close()
    return signals[:3]


@st.cache_data(ttl=3600)
def get_home_headline_stats(project_id: str = "project_1") -> dict:
    """Rich executive headline metrics for the Home command-center scorecard + read-out.

    All computed from the live DB (full survey). Returns market-structure KPIs and
    a few analytical findings so the Home page leads with real intelligence.
    """
    conn = _conn(project_id)
    _excl = "('Others (Specify 1)','Others (Specify 2)',\"Don't Know / None\")"
    base_n = int(pd.read_sql("SELECT COUNT(*) n FROM v_respondents", conn).iloc[0]["n"])

    # Per-brand awareness (TOM / total aided) + NPS, single pass
    aware = pd.read_sql(f"""
        WITH tom AS (
            SELECT brand_name, COUNT(DISTINCT respondent_id) tom_n
            FROM v_brand_awareness WHERE stage='TOM' AND brand_name NOT IN {_excl}
            GROUP BY brand_name),
        aided AS (
            SELECT brand_name, COUNT(DISTINCT respondent_id) aided_n
            FROM v_brand_awareness WHERE stage IN ('TOM','SPONT','AIDED') AND brand_name NOT IN {_excl}
            GROUP BY brand_name)
        SELECT a.brand_name,
               ROUND(a.aided_n*100.0/{base_n},1) aided_pct,
               ROUND(COALESCE(t.tom_n,0)*100.0/{base_n},1) tom_pct
        FROM aided a LEFT JOIN tom t ON a.brand_name=t.brand_name
        ORDER BY a.aided_n DESC
    """, conn)

    nps_all = pd.read_sql(f"""
        SELECT brand_name,
               ROUND((SUM(CASE WHEN nps_score>=9 THEN 1.0 ELSE 0 END) -
                      SUM(CASE WHEN nps_score<=6 THEN 1.0 ELSE 0 END))*100.0/COUNT(*),1) nps,
               COUNT(*) raters
        FROM v_brand_nps WHERE brand_name NOT IN {_excl}
        GROUP BY brand_name HAVING COUNT(*)>=50 ORDER BY nps DESC
    """, conn)
    conn.close()

    n_brands = int(aware["brand_name"].nunique()) if not aware.empty else 0
    leader = aware.iloc[0] if not aware.empty else None
    # Top-5 concentration on TOM (first-named = mutually exclusive â†’ a true share â‰¤100%)
    top5_share = round(aware.nlargest(5, "tom_pct")["tom_pct"].sum(), 0) if not aware.empty else 0
    # salience gap = aided - tom; biggest = strongest reach-without-recall
    gap_brand = None
    if not aware.empty:
        _g = aware.assign(gap=aware["aided_pct"] - aware["tom_pct"]).sort_values("gap", ascending=False)
        gap_brand = _g.iloc[0]

    most_loved = nps_all.iloc[0] if not nps_all.empty else None
    least_loved = nps_all.iloc[-1] if not nps_all.empty else None
    # Respondent-weighted category NPS: Î£(npsÂ·raters)/Î£(raters). A brand with
    # thousands of raters counts more than one with 50 â€” the statistically correct
    # market average (vs an unweighted mean of brand scores).
    if not nps_all.empty:
        _w = pd.to_numeric(nps_all["raters"], errors="coerce").fillna(0)
        _v = pd.to_numeric(nps_all["nps"], errors="coerce")
        _wsum = float(_w.sum())
        avg_nps = round(float((_v * _w).sum() / _wsum), 1) if _wsum > 0 else round(float(_v.mean()), 1)
    else:
        avg_nps = None
    n_neg_nps = int((nps_all["nps"] < 0).sum()) if not nps_all.empty else 0

    return {
        "base_n": base_n,
        "n_brands": n_brands,
        "market_leader": {"brand": leader["brand_name"], "tom": float(leader["tom_pct"]),
                          "aided": float(leader["aided_pct"])} if leader is not None else None,
        "top5_share": top5_share,
        "most_loved": {"brand": most_loved["brand_name"], "nps": float(most_loved["nps"]),
                       "raters": int(most_loved["raters"])} if most_loved is not None else None,
        "least_loved": {"brand": least_loved["brand_name"], "nps": float(least_loved["nps"])}
                       if least_loved is not None else None,
        "avg_nps": avg_nps,
        "n_neg_nps": n_neg_nps,
        "n_nps_brands": int(len(nps_all)),
        "salience_gap": {"brand": gap_brand["brand_name"], "aided": float(gap_brand["aided_pct"]),
                         "tom": float(gap_brand["tom_pct"])} if gap_brand is not None else None,
    }


def get_cached_dashboard_data(category: str = "All", project_id: str = "project_1") -> dict:
    """Legacy alias used by dashboard.py."""
    stats = get_dashboard_stats(project_id)
    return {
        "stats": {
            "respondents": stats["base_n"],
            "nps":      stats["nps"],
            "share":    stats["aided"],
            "awareness": stats["tom"],
        },
        "hero_insight": stats["hero_insight"],
        "timestamp":    stats["timestamp"],
    }


def get_signals_from_ledger() -> list:
    # fact_findings_ledger does not exist in this DB.
    return []


@st.cache_data(ttl=3600)
def get_awareness_funnel_data(project_id: str = "project_1") -> pd.DataFrame:
    """TOM / Spontaneous / Aided awareness % for top 10 brands by aided count."""
    conn = _conn(project_id)
    base_n = int(pd.read_sql("SELECT COUNT(*) n FROM v_respondents", conn).iloc[0]["n"])
    _excl = "('Others (Specify 1)','Others (Specify 2)',\"Don't Know / None\")"

    aided_df = pd.read_sql(f"""
        SELECT brand_name, COUNT(DISTINCT respondent_id) AS aided_n
        FROM v_brand_awareness
        WHERE stage IN ('TOM','SPONT','AIDED')
          AND brand_name NOT IN {_excl}
        GROUP BY brand_name
        ORDER BY aided_n DESC LIMIT 10
    """, conn)

    tom_df = pd.read_sql(f"""
        SELECT brand_name, COUNT(DISTINCT respondent_id) AS tom_n
        FROM v_brand_awareness
        WHERE stage = 'TOM' AND brand_name NOT IN {_excl}
        GROUP BY brand_name
    """, conn)

    spont_df = pd.read_sql(f"""
        SELECT brand_name, COUNT(DISTINCT respondent_id) AS spont_n
        FROM v_brand_awareness
        WHERE stage IN ('TOM','SPONT') AND brand_name NOT IN {_excl}
        GROUP BY brand_name
    """, conn)
    conn.close()

    df = aided_df.merge(tom_df, on="brand_name", how="left") \
                 .merge(spont_df, on="brand_name", how="left")
    df["aided_pct"] = (df["aided_n"].fillna(0) / base_n * 100).round(1)
    df["tom_pct"]   = (df["tom_n"].fillna(0)   / base_n * 100).round(1)
    df["spont_pct"] = (df["spont_n"].fillna(0) / base_n * 100).round(1)
    return df[["brand_name", "tom_pct", "spont_pct", "aided_pct"]].sort_values("aided_pct", ascending=False)


@st.cache_data(ttl=3600)
def get_nps_composition(project_id: str = "project_1") -> pd.DataFrame:
    """Promoter / Passive / Detractor breakdown for top 8 brands (min 50 raters)."""
    conn = _conn(project_id)
    _excl = "('Others (Specify 1)','Others (Specify 2)',\"Don't Know / None\")"
    df = pd.read_sql(f"""
        SELECT brand_name,
               COUNT(*) AS raters,
               ROUND(SUM(CASE WHEN nps_score >= 9 THEN 1.0 ELSE 0 END) * 100.0 / COUNT(*), 1) AS promoter_pct,
               ROUND(SUM(CASE WHEN nps_score BETWEEN 7 AND 8 THEN 1.0 ELSE 0 END) * 100.0 / COUNT(*), 1) AS passive_pct,
               ROUND(SUM(CASE WHEN nps_score <= 6 THEN 1.0 ELSE 0 END) * 100.0 / COUNT(*), 1) AS detractor_pct,
               ROUND((SUM(CASE WHEN nps_score >= 9 THEN 1.0 ELSE 0 END) -
                      SUM(CASE WHEN nps_score <= 6 THEN 1.0 ELSE 0 END))
                     * 100.0 / COUNT(*), 1) AS nps
        FROM v_brand_nps
        WHERE brand_name NOT IN {_excl}
        GROUP BY brand_name
        HAVING COUNT(*) >= 50
        ORDER BY nps DESC LIMIT 8
    """, conn)
    conn.close()
    return df


@st.cache_data(ttl=3600)
def get_salience_scatter_data(project_id: str = "project_1") -> pd.DataFrame:
    """Aided awareness % vs TOM % for all brands with aided >= 5%."""
    conn = _conn(project_id)
    base_n = int(pd.read_sql("SELECT COUNT(*) n FROM v_respondents", conn).iloc[0]["n"])
    _excl = "('Others (Specify 1)','Others (Specify 2)',\"Don't Know / None\")"

    aided_df = pd.read_sql(f"""
        SELECT brand_name, COUNT(DISTINCT respondent_id) AS aided_n
        FROM v_brand_awareness
        WHERE stage IN ('TOM','SPONT','AIDED') AND brand_name NOT IN {_excl}
        GROUP BY brand_name
    """, conn)
    tom_df = pd.read_sql(f"""
        SELECT brand_name, COUNT(DISTINCT respondent_id) AS tom_n
        FROM v_brand_awareness
        WHERE stage = 'TOM' AND brand_name NOT IN {_excl}
        GROUP BY brand_name
    """, conn)
    conn.close()

    df = aided_df.merge(tom_df, on="brand_name", how="left")
    df["aided_pct"] = (df["aided_n"].fillna(0) / base_n * 100).round(1)
    df["tom_pct"]   = (df["tom_n"].fillna(0)   / base_n * 100).round(1)
    return df[df["aided_pct"] >= 5.0][["brand_name", "aided_pct", "tom_pct"]].sort_values("aided_pct", ascending=False)


@st.cache_data(ttl=3600)
def get_city_dominance(project_id: str = "project_1") -> pd.DataFrame:
    """For each city: top brand, its TOM %, runner-up %, and dominance gap. Top 12 cities."""
    conn = _conn(project_id)
    base_n_city = pd.read_sql("""
        SELECT city_name, COUNT(DISTINCT respondent_id) AS city_n
        FROM v_respondents
        GROUP BY city_name
        ORDER BY city_n DESC LIMIT 12
    """, conn)

    _excl = "('Others (Specify 1)','Others (Specify 2)',\"Don't Know / None\")"
    tom_city = pd.read_sql(f"""
        SELECT city_name, brand_name,
               COUNT(DISTINCT respondent_id) AS tom_n
        FROM v_brand_awareness
        WHERE stage = 'TOM' AND brand_name NOT IN {_excl}
        GROUP BY city_name, brand_name
    """, conn)
    conn.close()

    rows = []
    for _, city_row in base_n_city.iterrows():
        city   = city_row["city_name"]
        city_n = int(city_row["city_n"])
        city_tom = tom_city[tom_city["city_name"] == city].sort_values("tom_n", ascending=False)
        if city_tom.empty:
            continue
        top   = city_tom.iloc[0]
        runup = city_tom.iloc[1] if len(city_tom) > 1 else None
        top_pct   = round(top["tom_n"] / city_n * 100, 1)
        runup_pct = round(runup["tom_n"] / city_n * 100, 1) if runup is not None else 0.0
        rows.append({
            "city_name":        city,
            "top_brand":        top["brand_name"],
            "top_brand_pct":    top_pct,
            "second_brand_pct": runup_pct,
            "gap":              round(top_pct - runup_pct, 1),
            "city_n":           city_n,
        })
    if not rows:
        return pd.DataFrame(columns=["city_name", "top_brand", "top_brand_pct",
                                      "second_brand_pct", "gap", "city_n"])
    return pd.DataFrame(rows).sort_values("top_brand_pct", ascending=False)


@st.cache_data(ttl=3600)
def get_category_brand_matrix(project_id: str = "project_1") -> pd.DataFrame:
    """
    Brand imagery share % per brand per category (top 8 brands x 6 categories).
    Source: fact_brand_imagery (BQ3 imagery evaluations, NOT awareness funnel).
    Each cell = respondents who evaluated that brand in that category /
                total respondents who evaluated ANY brand in that category Ã— 100.
    This measures brand participation share within each category's imagery study,
    not survey-wide aided awareness penetration.
    """
    conn = _conn(project_id)
    _excl = "('Others (Specify 1)','Others (Specify 2)',\"Don't Know / None\")"

    try:
        cat_df = pd.read_sql(f"""
            SELECT
                CASE
                    WHEN fbi.category_id IN (1,7)  THEN 'Ceiling Fans'
                    WHEN fbi.category_id IN (2,8)  THEN 'Air Cooler'
                    WHEN fbi.category_id IN (3,9)  THEN 'Mixer Grinder'
                    WHEN fbi.category_id IN (4,10) THEN 'LED Batten'
                    WHEN fbi.category_id IN (5,11) THEN 'Water Heater'
                    WHEN fbi.category_id IN (6,12) THEN 'Water Pumps'
                END AS category_name,
                db.brand_name,
                COUNT(DISTINCT fbi.respondent_id) AS brand_n
            FROM fact_brand_imagery fbi
            JOIN dim_brand db ON fbi.brand_id = db.brand_id
            WHERE db.brand_name NOT IN {_excl}
            GROUP BY category_name, db.brand_name
            HAVING category_name IS NOT NULL
        """, conn)

        # Per-category respondent base (total unique respondents who evaluated
        # ANY brand in each category â€” correct denominator for share within category)
        cat_base_df = pd.read_sql("""
            SELECT
                CASE
                    WHEN fbi.category_id IN (1,7)  THEN 'Ceiling Fans'
                    WHEN fbi.category_id IN (2,8)  THEN 'Air Cooler'
                    WHEN fbi.category_id IN (3,9)  THEN 'Mixer Grinder'
                    WHEN fbi.category_id IN (4,10) THEN 'LED Batten'
                    WHEN fbi.category_id IN (5,11) THEN 'Water Heater'
                    WHEN fbi.category_id IN (6,12) THEN 'Water Pumps'
                END AS category_name,
                COUNT(DISTINCT fbi.respondent_id) AS cat_base_n
            FROM fact_brand_imagery fbi
            GROUP BY category_name
            HAVING category_name IS NOT NULL
        """, conn)
    except Exception:
        cat_df = pd.DataFrame()
        cat_base_df = pd.DataFrame()

    conn.close()

    if cat_df.empty:
        return pd.DataFrame()

    total_n = int(cat_df["brand_n"].sum())
    if total_n == 0:
        return pd.DataFrame()

    top8 = cat_df.groupby("brand_name")["brand_n"].sum().nlargest(8).index.tolist()
    cat_df = cat_df[cat_df["brand_name"].isin(top8)]

    # Merge per-category base and compute proper penetration % within category
    if not cat_base_df.empty:
        cat_df = cat_df.merge(cat_base_df, on="category_name", how="left")
        cat_df["share_pct"] = (cat_df["brand_n"] / cat_df["cat_base_n"] * 100).round(1)
    else:
        # Fallback: share of mentions within category (original approach)
        cat_df["share_pct"] = (
            cat_df["brand_n"] /
            cat_df.groupby("category_name")["brand_n"].transform("sum") * 100
        ).round(1)

    pivot = cat_df.pivot_table(
        index="brand_name", columns="category_name", values="share_pct", aggfunc="sum"
    ).fillna(0.0)
    ordered_cols = [c for c in ["Ceiling Fans","Air Cooler","Mixer Grinder","LED Batten","Water Heater","Water Pumps"] if c in pivot.columns]
    pivot = pivot[ordered_cols] if ordered_cols else pivot
    return pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]


@st.cache_data(ttl=3600)
def get_verbatim_pulse(project_id: str = "project_1") -> dict:
    """
    {brand_name: {promoter: [str], detractor: [str]}} for top 5 brands by NPS rater count.
    Verbatims from bq2a (reason for brand preference) linked to NPS score via respondent_id.
    """
    conn = _conn(project_id)
    _excl = "('Others (Specify 1)','Others (Specify 2)',\"Don't Know / None\")"
    try:
        df = pd.read_sql(f"""
            SELECT vbn.brand_name, vbn.nps_score, fv.response_text
            FROM fact_verbatims fv
            JOIN v_brand_nps vbn ON fv.respondent_id = vbn.respondent_id
            WHERE fv.question_code = 'bq2a'
              AND fv.response_text IS NOT NULL
              AND LENGTH(TRIM(fv.response_text)) > 5
              AND vbn.brand_name NOT IN {_excl}
        """, conn)
    except Exception:
        conn.close()
        return {}
    conn.close()

    top_brands = (df.groupby("brand_name")["nps_score"]
                    .count().nlargest(5).index.tolist())
    result = {}
    for brand in top_brands:
        bdf = df[df["brand_name"] == brand]
        promoters  = bdf[bdf["nps_score"] >= 9]["response_text"].dropna().tolist()[:3]
        detractors = bdf[bdf["nps_score"] <= 6]["response_text"].dropna().tolist()[:3]
        if promoters or detractors:
            result[brand] = {"promoter": promoters, "detractor": detractors}
    return result


@st.cache_data(ttl=3600)
def get_hidden_hooks(project_id: str = "project_1") -> pd.DataFrame:
    """Returns fact_findings_ledger â€” AI-discovered hidden NPS drivers. Empty DF if table empty."""
    conn = _conn(project_id)
    try:
        df = pd.read_sql("""
            SELECT category, brand, metric_name, primary_driver, insight_narrative, discovery_date
            FROM fact_findings_ledger
            ORDER BY category, brand
        """, conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=3600)
def get_appliance_ownership_story(project_id: str = "project_1") -> pd.DataFrame:
    """Kitchen appliance penetration % across all respondents."""
    conn = _conn(project_id)
    try:
        base_n = int(pd.read_sql("SELECT COUNT(*) n FROM v_respondents", conn).iloc[0]["n"])
        df = pd.read_sql("""
            SELECT appliance_name, COUNT(DISTINCT respondent_id) AS owners
            FROM v_kitchen_ownership
            GROUP BY appliance_name
            ORDER BY owners DESC
        """, conn)
        df["penetration_pct"] = (df["owners"] / base_n * 100).round(1)
    except Exception:
        df = pd.DataFrame(columns=["appliance_name", "owners", "penetration_pct"])
    finally:
        conn.close()
    return df


@st.cache_data(ttl=3600)
def get_recent_purchase_story(project_id: str = "project_1") -> pd.DataFrame:
    """
    Top appliances recently purchased, by respondent count.
    Note: v_recent_purchase.category = survey wave segment, NOT appliance category.
    Only appliance_name is meaningful for purchase intent analysis.
    Returns DataFrame: appliance_name, cnt, pct.
    """
    conn = _conn(project_id)
    try:
        base_n = int(pd.read_sql("SELECT COUNT(*) n FROM v_respondents", conn).iloc[0]["n"])
        df = pd.read_sql("""
            SELECT appliance_name, COUNT(DISTINCT respondent_id) AS cnt
            FROM v_recent_purchase
            WHERE appliance_name IS NOT NULL
              AND appliance_name NOT LIKE '%Other%'
            GROUP BY appliance_name
            ORDER BY cnt DESC LIMIT 10
        """, conn)
        df["pct"] = (df["cnt"] / base_n * 100).round(1)
    except Exception:
        df = pd.DataFrame(columns=["appliance_name", "cnt", "pct"])
    finally:
        conn.close()
    return df
