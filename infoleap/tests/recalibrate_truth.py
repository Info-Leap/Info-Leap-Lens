import sqlite3
import pandas as pd
import json
from pathlib import Path

# Paths
db_path = r"D:\antigravity project\infoleap project\info-leap\data\project_1\oxdata.db"

def calibrate_truth():
    conn = sqlite3.connect(db_path)
    truth = []

    # --- 1. BASE COUNTS & DEMOGRAPHICS (DYNAMIC) ---
    res_total = conn.execute("SELECT COUNT(DISTINCT respondent_id) FROM fact_respondents").fetchone()[0]
    truth.append({"q": "How many total unique respondents are in the database?", "truth": str(res_total), "domain": "Base"})

    res_overlap = conn.execute("SELECT COUNT(DISTINCT respondent_id) FROM dim_qual_quant_bridge").fetchone()[0]
    truth.append({"q": "What is the overlap between quantitative and qualitative participants?", "truth": str(res_overlap), "domain": "Base"})

    res_cities = conn.execute("SELECT COUNT(DISTINCT city_name) FROM v_respondents").fetchone()[0]
    truth.append({"q": "How many unique cities are covered in the research?", "truth": str(res_cities), "domain": "Base"})

    df_gen = pd.read_sql("SELECT gender, COUNT(*) as c FROM v_respondents GROUP BY gender", conn)
    gen_str = ", ".join([f"{r['gender']}: {r['c']}" for _, r in df_gen.iterrows()])
    truth.append({"q": "What is the breakdown of respondents by gender?", "truth": gen_str, "domain": "Base"})

    df_city = pd.read_sql("SELECT city_name, COUNT(*) as c FROM v_respondents GROUP BY city_name ORDER BY c DESC LIMIT 1", conn)
    city_str = f"{df_city.iloc[0]['city_name']} ({df_city.iloc[0]['c']})"
    truth.append({"q": "Which city has the highest number of respondents?", "truth": city_str, "domain": "Base"})

    # --- 2. NPS & BRAND HEALTH (DYNAMIC) ---
    def get_nps(brand, city=None):
        where = f"WHERE brand_name = '{brand}'"
        if city: where += f" AND city_name = '{city}'"
        df = pd.read_sql(f"SELECT nps_score FROM v_brand_nps {where}", conn)
        p = len(df[df['nps_score'] >= 9])
        d = len(df[df['nps_score'] <= 6])
        t = len(df)
        return ((p - d) / t) * 100 if t > 0 else 0

    truth.append({"q": "What is the overall NPS for Crompton across all zones?", "truth": f"{get_nps('Crompton'):.1f}", "domain": "NPS"})
    truth.append({"q": "What is Bajaj's NPS in Mumbai?", "truth": f"{get_nps('Bajaj', 'Mumbai'):.1f}", "domain": "NPS"})
    
    # Preethi South NPS (Approx)
    df_p_south = pd.read_sql("SELECT nps_score FROM v_brand_nps WHERE brand_name = 'Preethi' AND zone_name = 'South'", conn)
    p = len(df_p_south[df_p_south['nps_score'] >= 9])
    d = len(df_p_south[df_p_south['nps_score'] <= 6])
    n_p_s = ((p - d) / len(df_p_south)) * 100 if len(df_p_south) > 0 else 0
    truth.append({"q": "Compare Preethi vs Bajaj NPS in the South Zone.", "truth": f"Preethi: {n_p_s:.1f}", "domain": "NPS"})

    # --- 3. MARKET SHARE (DYNAMIC PROXY) ---
    df_share = pd.read_sql("SELECT brand_name, COUNT(*) as owners FROM v_brand_nps GROUP BY brand_name ORDER BY owners DESC LIMIT 1", conn)
    share_str = f"{df_share.iloc[0]['brand_name']}"
    truth.append({"q": "Which brand has the highest 'Ever Used' share for Mixer Grinders?", "truth": share_str, "domain": "Share"})

    # --- 4. QUALITATIVE (HARDCODED from reference or sample) ---
    truth.append({"q": "What do consumers in Kolkata say about Preethi durability?", "truth": "Handle cracking and jar breaking issues reported (e.g. Doc_842).", "domain": "Qual"})
    truth.append({"q": "What do Mumbai consumers like most about Crompton?", "truth": "Fast motor cooling and reliability.", "domain": "Qual"})

    conn.close()
    return truth

if __name__ == "__main__":
    data = calibrate_truth()
    # Merge with existing qualitative/complex truths (they are harder to auto-generate)
    # For now, let's just write the verified base truths.
    with open("oxdata/tests/golden_truth.json", "w") as f:
        json.dump(data, f, indent=4)
    print("✅ Calibrated Golden Truth created.")
