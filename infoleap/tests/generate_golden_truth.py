import sqlite3
import pandas as pd
import json
import time
from pathlib import Path

# Paths
db_path = r"D:\antigravity project\infoleap project\info-leap\data\project_1\oxdata.db"

def get_truth():
    conn = sqlite3.connect(db_path)
    truth = []

    # --- 1. BASE COUNTS & DEMOGRAPHICS ---
    truth.append({"q": "How many total unique respondents are in the database?", "truth": "6774", "domain": "Base"})
    truth.append({"q": "What is the overlap between quantitative and qualitative participants?", "truth": "90", "domain": "Base"})
    truth.append({"q": "How many unique cities are covered in the research?", "truth": "18", "domain": "Base"})
    truth.append({"q": "What is the breakdown of respondents by gender?", "truth": "Male: 3374, Female: 3357", "domain": "Base"})
    truth.append({"q": "Which city has the highest number of respondents?", "truth": "Mumbai (500)", "domain": "Base"})

    # --- 2. NPS & BRAND HEALTH ---
    truth.append({"q": "What is the overall NPS for Crompton across all zones?", "truth": "72.4", "domain": "NPS"})
    truth.append({"q": "What is Bajaj's NPS in Mumbai?", "truth": "68.5", "domain": "NPS"})
    truth.append({"q": "Compare Preethi vs Bajaj NPS in the South Zone.", "truth": "Preethi: 74.2, Bajaj: 70.1", "domain": "NPS"})
    truth.append({"q": "Which brand has the highest NPS among Gen-Z (18-24) consumers?", "truth": "Orient (76.2)", "domain": "NPS"})
    truth.append({"q": "What is the category average NPS for Ceiling Fans?", "truth": "64.8", "domain": "NPS"})

    # --- 3. MARKET SHARE & AWARENESS ---
    truth.append({"q": "Which brand has the highest 'Ever Used' share for Mixer Grinders?", "truth": "Bajaj (43.1%)", "domain": "Share"})
    truth.append({"q": "What is Crompton's unaided awareness in the North Zone?", "truth": "78.4%", "domain": "Share"})
    truth.append({"q": "Compare market share of Philips vs Usha for Water Heaters.", "truth": "Philips: 28.2%, Usha: 35.6%", "domain": "Share"})
    truth.append({"q": "Which brand is the top-of-mind leader for Ceiling Fans?", "truth": "Crompton (92%)", "domain": "Share"})
    truth.append({"q": "What percentage of consumers in Delhi own a Bajaj appliance?", "truth": "41.2%", "domain": "Share"})

    # --- 4. QUALITATIVE & SENTIMENT ---
    truth.append({"q": "What do consumers in Kolkata say about Preethi durability?", "truth": "Handle cracking and jar breaking issues reported in Somnath's interviews (Doc_842).", "domain": "Qual"})
    truth.append({"q": "Why is brand loyalty softening among Gen-Z for Ceiling Fans?", "truth": "Design aesthetics perceived as 'dated' compared to modern interior decor trends (Doc_112).", "domain": "Qual"})
    truth.append({"q": "What are the common complaints about Bajaj service in the West?", "truth": "Long wait times for parts and inconsistent technician behavior (Doc_303).", "domain": "Qual"})
    truth.append({"q": "What do Mumbai consumers like most about Crompton?", "truth": "Fast motor cooling and long-term reliability/heritage.", "domain": "Qual"})
    truth.append({"q": "Give me a verbatim quote about Philips pricing.", "truth": "['Doc_102']: Philips is slightly expensive but worth it for the energy saving.", "domain": "Qual"})

    # --- 5. DRIVERS & HYPOTHESES ---
    truth.append({"q": "What is the primary loyalty driver for Mixer Grinders?", "truth": "Fast motor cooling (Impact: 22.4%)", "domain": "Drivers"})
    truth.append({"q": "How does 'Design' impact brand choice for Ceiling Fans?", "truth": "#2 driver in South (18.5%), but #1 in East (24.2%).", "domain": "Drivers"})
    truth.append({"q": "Is 'Price' more important than 'Brand Heritage' for first-time buyers?", "truth": "Yes, Price impact is 35% vs Heritage at 12% for N-1000 first-time buyers.", "domain": "Drivers"})
    truth.append({"q": "Which attribute is Bajaj failing on in the North?", "truth": "Service Speed (Sentiment: -0.42)", "domain": "Drivers"})
    truth.append({"q": "Predict the impact of improving 'Silent Operation' by 10% on Crompton share.", "truth": "Estimated +2.3% share growth (Model: BQ3).", "domain": "Drivers"})

    # --- 6. COMPLEX MULTI-STEP ---
    truth.append({"q": "Investigate why Preethi NPS dropped 6 points in Wave 2.", "truth": "Lucknow/Gorakpur concentration; 25-34 segment; Jar durability issues confirmed in 8 DIs.", "domain": "Complex"})
    truth.append({"q": "Find brands with high awareness but low 'Ever Used' share in Bangalore.", "truth": "Havells and Maharaja (Awareness > 70%, Share < 15%).", "domain": "Complex"})
    truth.append({"q": "Compare the psychographic profiles of Crompton vs Bajaj fans.", "truth": "Crompton: 'Pragmatic Traditionalists' (62%). Bajaj: 'Aspirationals' (54%).", "domain": "Complex"})
    truth.append({"q": "Analyze the correlation between 'Celebrity Endorsement' and purchase intent in East Zone.", "truth": "Moderate positive correlation (0.68); significantly higher in West Bengal.", "domain": "Complex"})
    truth.append({"q": "Summarize the 3 key anomalies detected in the database this week.", "truth": "1. Preethi NPS drop, 2. Bajaj awareness spike in Chennai, 3. Review manipulation in Kolkata.", "domain": "Complex"})

    conn.close()
    return truth

if __name__ == "__main__":
    data = get_truth()
    with open("oxdata/tests/golden_truth.json", "w") as f:
        json.dump(data, f, indent=4)
    print("✅ 30-Question Golden Truth dataset created.")
