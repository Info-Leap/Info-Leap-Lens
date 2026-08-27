"""
analytics_tools.py — Streamlit-free compute wrappers exposing the Brand Health
Python+R analytics suite to the Ask Pulse agent (researcher_agent.py).

Each function returns a concise, agent-readable TEXT summary (not a Streamlit
render), grounded entirely in live DB data. Engines reused:
  - lens.analytics.can_map_engine.run_ca_pipeline      (Correspondence Analysis)
  - lens.analytics.bip_engine.BIPNormalizationEngine   (Brand Image Profiling)
  - oxdata.skills.r_bridge.run_r_stat                  (R: regression/factor/anova/cronbach)
  - column-proportion significance (pooled two-proportion z-test, XLSTAT letters)

All functions are import-safe (no Streamlit, no global side effects) and degrade
gracefully (return an explanatory string) instead of raising.
"""

from __future__ import annotations
import sqlite3
from itertools import combinations

import numpy as np
import pandas as pd

from oxdata.db_loader import get_db_path

# Product code map (mirrors can_map_engine.PRODUCT_CODES)
_PRODUCT_CODES = {
    "all": list(range(1, 13)),
    "ceiling fans": [1, 7], "air cooler": [2, 8], "mixer grinder": [3, 9],
    "led batten": [4, 10], "water heater": [5, 11], "water pumps": [6, 12],
}


def _ro_conn():
    return sqlite3.connect(f"file:{get_db_path()}?mode=ro", uri=True)


def _cat_codes(category: str | None):
    if not category:
        return None
    return _PRODUCT_CODES.get(str(category).strip().lower())


# ─────────────────────────────────────────────────────────────────────────────
# 1. Column-proportion significance (XLSTAT / pValue sig-letter table)
# ─────────────────────────────────────────────────────────────────────────────
def _sig_letters(items, alpha=0.05):
    """items: list[(label, count, base)] → (letters, beats, pct). Excel-style labels."""
    from scipy.stats import norm as _norm

    def _col_letter(i):
        s, i = "", i + 1
        while i > 0:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    data = []
    for lbl, cnt, base in items:
        base = float(base) if base else 0.0
        p = (float(cnt) / base) if base > 0 else 0.0
        data.append([lbl, float(cnt), base, p])
    data.sort(key=lambda x: -x[3])
    letters = {d[0]: _col_letter(i) for i, d in enumerate(data)}
    beats = {d[0]: [] for d in data}
    for (la, ca, ba, pa), (lb, cb, bb, pb) in combinations(data, 2):
        if ba < 1 or bb < 1:
            continue
        pooled = (ca + cb) / (ba + bb)
        se = np.sqrt(pooled * (1 - pooled) * (1 / ba + 1 / bb))
        if se == 0:
            continue
        z = (pa - pb) / se
        pval = 2 * (1 - _norm.cdf(abs(z)))
        if pval < alpha:
            beats[la].append(letters[lb])
    pct = {d[0]: d[3] * 100 for d in data}
    return letters, beats, pct


def significance_test(metric: str = "CONSIDERATION", confidence: float = 0.95,
                      min_n: int = 30, top: int = 12) -> str:
    """Column-proportion significance test across brands on an awareness metric.
    metric: TOM|SPONT|AIDED|EVER_USED|CONSIDERATION|PREFERRED|LAST_PURCHASED.
    Returns a ranked sig-letter summary (which brands are significantly ahead)."""
    metric = (metric or "CONSIDERATION").upper()
    valid = {"TOM", "SPONT", "AIDED", "EVER_USED", "CONSIDERATION", "PREFERRED", "LAST_PURCHASED"}
    if metric not in valid:
        return f"Unknown metric '{metric}'. Choose one of: {', '.join(sorted(valid))}."
    alpha = round(1 - confidence, 4)
    conn = _ro_conn()
    try:
        base = int(pd.read_sql("SELECT COUNT(DISTINCT respondent_id) n FROM fact_respondents", conn).iloc[0, 0])
        counts = pd.read_sql(
            "SELECT b.brand_name, COUNT(DISTINCT a.respondent_id) AS n "
            "FROM fact_brand_awareness a JOIN dim_brand b ON a.brand_id=b.brand_id "
            "WHERE a.stage = ? AND b.brand_name NOT IN ('Don''t Know / None') "
            "GROUP BY b.brand_name", conn, params=[metric])
    except Exception as e:
        return f"Significance test failed: {e}"
    finally:
        conn.close()
    items = [(r["brand_name"], int(r["n"]), base) for _, r in counts.iterrows() if int(r["n"]) >= min_n]
    if len(items) < 2:
        return f"Not enough brands with ≥{min_n} respondents on {metric}."
    letters, beats, pct = _sig_letters(items, alpha=alpha)
    rows = sorted(pct, key=lambda x: -pct[x])[:top]
    lines = [f"SIGNIFICANCE TEST — {metric} (pooled two-proportion z, {confidence:.0%} conf, base N={base:,}):"]
    for b in rows:
        beat = " ".join(beats[b]) or "—"
        lines.append(f"  {letters[b]}  {b}: {pct[b]:.1f}%  · sig. higher than: {beat}")
    leader = rows[0]
    lines.append(f"Leader: {leader} ({letters[leader]}) at {pct[leader]:.1f}%, "
                 f"significantly ahead of {len(beats[leader])} of {len(items)-1} rivals. "
                 f"Brands sharing a letter are NOT significantly different.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Correspondence Analysis (CAN MAP perceptual positioning)
# ─────────────────────────────────────────────────────────────────────────────
def correspondence_analysis(category: str = "all", top_brands: int = 12) -> str:
    """Run Correspondence Analysis on brand×attribute imagery; summarise the
    perceptual map: inertia explained, axis poles, and each brand's quadrant."""
    try:
        from lens.analytics.can_map_engine import run_ca_pipeline
    except Exception as e:
        return f"CA engine unavailable: {e}"
    res = run_ca_pipeline(category_codes=_cat_codes(category), top_brands=top_brands, top_attrs=40)
    if res.get("status") != "ok":
        return f"Correspondence analysis: {res.get('message', 'insufficient data')}."
    md = res.get("map_data", {})
    ev = md.get("explained_variance", [0, 0])
    brands = md.get("brands", [])
    attrs = md.get("attrs", [])
    def _poles(idx):
        s = sorted(attrs, key=lambda a: a.get(f"F{idx+1}", a.get("F1", 0)) if idx == 0 else a.get("F2", 0))
        neg = ", ".join(a["name"][:22] for a in s[:2]) if s else "—"
        pos = ", ".join(a["name"][:22] for a in s[-2:][::-1]) if s else "—"
        return neg, pos
    n1, p1 = _poles(0); n2, p2 = _poles(1)
    lines = [
        f"CORRESPONDENCE ANALYSIS (CAN MAP) — {category}, {len(brands)} brands × {len(attrs)} attributes.",
        f"F1 explains {ev[0]:.1f}% inertia (◀ {n1}  |  {p1} ▶).",
        f"F2 explains {ev[1]:.1f}% inertia (◀ {n2}  |  {p2} ▶).",
        "Brand positions (F1, F2):",
    ]
    for b in brands[:top_brands]:
        f1, f2 = b.get("F1", 0), b.get("F2", 0)
        quad = ("upper-right" if f1 >= 0 and f2 >= 0 else "upper-left" if f1 < 0 and f2 >= 0
                else "lower-right" if f1 >= 0 else "lower-left")
        lines.append(f"  {b['name']}: F1={f1:+.2f}, F2={f2:+.2f} ({quad})")
    lines.append("Brands near an attribute pole are strongly associated with those attributes.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Brand Image Profiling (BIP normalized strengths)
# ─────────────────────────────────────────────────────────────────────────────
def brand_imagery_profile(brand: str, category: str = "all", top: int = 8) -> str:
    """BIP normalized deviation: which attributes a brand over/under-indexes on
    vs the category, with significance."""
    try:
        from lens.analytics.bip_engine import BIPNormalizationEngine
    except Exception as e:
        return f"BIP engine unavailable: {e}"
    try:
        eng = BIPNormalizationEngine(category_codes=_cat_codes(category),
                                     percentile_threshold=65, top_brands=20)
        matrix = eng.get_brand_attr_matrix(attr_section=None)
        if matrix is None or matrix.empty:
            return f"No imagery data for category '{category}'."
        tables = eng.compute_normalization(matrix)
        t3 = tables.get("table3_norm_pct")
        t14 = tables.get("table14_significance")
    except Exception as e:
        return f"BIP failed: {e}"
    if t3 is None or brand not in t3.index:
        avail = ", ".join(list(t3.index)[:10]) if t3 is not None else "—"
        return f"Brand '{brand}' not in BIP matrix. Available: {avail}."
    row = t3.loc[brand].sort_values(ascending=False)
    sig = t14.loc[brand] if (t14 is not None and brand in t14.index) else None
    def _mark(attr):
        return " (sig)" if sig is not None and str(sig.get(attr, "")).upper() == "YES" else ""
    strengths = [(a, row[a]) for a in row.index if row[a] > 0][:top]
    weaks = [(a, row[a]) for a in row.index if row[a] < 0][-top:]
    lines = [f"BRAND IMAGE PROFILING — {brand} ({category}). Normalized deviation vs category average:"]
    lines.append("  Over-indexes on (owns):")
    for a, v in strengths:
        lines.append(f"    +{v:.1f}  {a}{_mark(a)}")
    lines.append("  Under-indexes on (gaps):")
    for a, v in weaks:
        lines.append(f"    {v:.1f}  {a}{_mark(a)}")
    lines.append("(sig) = statistically significant deviation. Positive = stronger than the average brand.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Key Driver Regression (configurable DV + top-box recode + importance)
# ─────────────────────────────────────────────────────────────────────────────
def key_driver_regression(brand: str, outcome: str = "NPS", topbox_min: int = 9, top: int = 8) -> str:
    """Linear key-driver regression: which imagery attributes most drive the
    outcome (NPS/CSAT, optionally top-box recoded). Returns ranked standardized
    importance (XLSTAT convention)."""
    try:
        from oxdata.skills.r_bridge import r_available, run_r_stat
    except Exception as e:
        return f"R bridge unavailable: {e}"
    if not r_available():
        return "R is not available on this host — key driver regression requires Rscript."
    outcome = (outcome or "NPS").upper()
    conn = _ro_conn()
    try:
        if outcome == "CSAT":
            dv = pd.read_sql(
                "SELECT ba.respondent_id, ba.brand_id, s.score AS dv FROM fact_satisfaction s "
                "JOIN fact_brand_awareness ba ON s.respondent_id=ba.respondent_id AND ba.stage='LAST_PURCHASED' "
                "JOIN dim_brand db ON ba.brand_id=db.brand_id WHERE db.brand_name=?", conn, params=[brand])
        elif outcome in ("EVER_TRIED", "TRIAL"):
            # Binary trial DV: 1=ever tried, 0=aware but never tried
            aware = pd.read_sql(
                "SELECT DISTINCT fa.respondent_id, fa.brand_id FROM fact_brand_awareness fa "
                "JOIN dim_brand db ON fa.brand_id=db.brand_id "
                "WHERE fa.stage IN ('TOM','SPONT','AIDED') AND db.brand_name=?", conn, params=[brand])
            tried = pd.read_sql(
                "SELECT DISTINCT fa.respondent_id, fa.brand_id, 1 AS dv FROM fact_brand_awareness fa "
                "JOIN dim_brand db ON fa.brand_id=db.brand_id "
                "WHERE fa.stage='EVER_USED' AND db.brand_name=?", conn, params=[brand])
            dv = aware.merge(tried, on=["respondent_id", "brand_id"], how="left")
            dv["dv"] = dv["dv"].fillna(0).astype(int)
        else:
            dv = pd.read_sql(
                "SELECT n.respondent_id, n.brand_id, n.nps_score AS dv FROM fact_brand_nps n "
                "JOIN dim_brand db ON n.brand_id=db.brand_id WHERE db.brand_name=?", conn, params=[brand])
        bi = pd.read_sql(
            "SELECT bi.respondent_id, bi.brand_id, da.attr_label, bi.value FROM fact_brand_imagery bi "
            "JOIN dim_brand db ON bi.brand_id=db.brand_id "
            "JOIN dim_bq3_attribute da ON bi.attr_id=da.attr_id "
            "WHERE db.brand_name=? AND EXISTS ("
            "  SELECT 1 FROM fact_brand_awareness aw "
            "  WHERE aw.respondent_id=bi.respondent_id AND aw.brand_id=bi.brand_id "
            "  AND aw.stage IN ('TOM','SPONT','AIDED','EVER_USED','CONSIDERATION','CURRENT_USER')"
            ")", conn, params=[brand])
    except Exception as e:
        conn.close()
        return f"Key driver query failed: {e}"
    conn.close()
    if dv.empty or bi.empty:
        return f"No {outcome} + imagery data for {brand}."
    _is_ever_tried = outcome in ("EVER_TRIED", "TRIAL")
    if not _is_ever_tried:
        dv["dv"] = pd.to_numeric(dv["dv"], errors="coerce")
        dv = dv.dropna(subset=["dv"]).drop_duplicates("respondent_id")
        if topbox_min and topbox_min > 0:
            dv["dv"] = (dv["dv"] >= topbox_min).astype(int)
    # EVER_TRIED: pivot on (respondent_id, brand_id) for multi-brand long format
    if _is_ever_tried and "brand_id" in dv.columns and "brand_id" in bi.columns:
        piv = bi.pivot_table(index=["respondent_id", "brand_id"], columns="attr_label", values="value", aggfunc="max", fill_value=0)
        rdf = dv.set_index(["respondent_id", "brand_id"])[["dv"]].join(piv, how="inner").reset_index().rename(columns={"dv": "nps_score"})
    else:
        piv = bi.pivot_table(index="respondent_id", columns="attr_label", values="value", aggfunc="max", fill_value=0)
        rdf = dv.set_index("respondent_id")[["dv"]].join(piv, how="inner").reset_index().rename(columns={"dv": "nps_score"})
    if len(rdf) < 30:
        return f"Need ≥30 respondents with {outcome}+imagery for {brand} (found {len(rdf)})."
    # Binary outcome → logistic regression; continuous → OLS (XLSTAT convention)
    r_script = "logistic_regression" if (_is_ever_tried or (topbox_min and topbox_min > 0)) else "driver_regression"
    res = run_r_stat(r_script, rdf)
    if "error" in res:
        return f"Regression error: {res['error']}"
    recode = "" if not topbox_min else f" top-box (≥{topbox_min})"
    model_type = "Logistic" if r_script == "logistic_regression" else "OLS"
    r2_label = "McFadden R²" if r_script == "logistic_regression" else "R²"
    lines = [f"KEY DRIVER REGRESSION ({model_type}) — {brand}, outcome={outcome}{recode}. "
             f"{r2_label}={res.get('r_squared',0):.3f} over {res.get('n',0)} respondents.",
             "Top drivers by relative importance (standardized β):"]
    for d in res.get("significant_drivers", [])[:top]:
        direction = "raises" if (d.get("std_coef") or 0) >= 0 else "lowers"
        sig = "*" if d.get("significant") else " "
        lines.append(f"  {d.get('importance',0):5.1f}%  β={d.get('std_coef')}  {direction} {outcome}{sig}  "
                     f"{d['attribute'].replace('.', ' ')[:42]}")
    lines.append("* = p<0.05. Importance = share of total |standardized β|.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 5. R statistical tests (ANOVA / Cronbach) on NPS by segment
# ─────────────────────────────────────────────────────────────────────────────
def segment_anova(brand: str, segment: str = "zone") -> str:
    """One-way ANOVA: does the brand's NPS score differ across a segment
    (zone/gender/age_band)? R-powered, with group means."""
    try:
        from oxdata.skills.r_bridge import r_available, run_r_stat
    except Exception as e:
        return f"R bridge unavailable: {e}"
    if not r_available():
        return "R not available — ANOVA requires Rscript."
    col = {"zone": "zone_name", "gender": "gender", "age": "age_band", "age_band": "age_band"}.get(
        str(segment).lower(), "zone_name")
    conn = _ro_conn()
    try:
        df = pd.read_sql(
            f"SELECT respondent_id, {col} AS segment, nps_score AS score FROM v_brand_nps "
            "WHERE brand_name=? AND nps_score IS NOT NULL", conn, params=[brand])
    except Exception as e:
        conn.close()
        return f"ANOVA query failed: {e}"
    conn.close()
    df = df.dropna()
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df = df.dropna(subset=["score"])
    if df["segment"].nunique() < 2 or len(df) < 30:
        return f"Need ≥2 {segment} groups and ≥30 scored respondents for {brand}."
    res = run_r_stat("anova_analysis", df)
    if "error" in res:
        return f"ANOVA error: {res['error']}"
    sig = res.get("significant")
    lines = [f"ONE-WAY ANOVA — {brand} NPS score by {segment}. "
             f"F={res.get('f_stat',0):.2f}, p={res.get('p_value',1):.4f} "
             f"({'significant' if sig else 'not significant'})."]
    for g in sorted(res.get("group_means", []), key=lambda x: -x.get("mean", 0)):
        lines.append(f"  {g.get('segment')}: mean={g.get('mean'):.2f} (n={g.get('n')})")
    lines.append("Significant ⇒ NPS differs by segment; tailor strategy. Not significant ⇒ one approach fits all.")
    return "\n".join(lines)


# CLI smoke-test
if __name__ == "__main__":
    print(significance_test("CONSIDERATION")[:400]); print("---")
    print(key_driver_regression("Bajaj", "NPS", 9)[:400]); print("---")
    print(brand_imagery_profile("Bajaj")[:400])
