import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Ensure project root is in path
current_dir = Path(__file__).resolve().parent
oxdata_dir = current_dir.parent
project_root = oxdata_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from oxdata.db_loader import get_db_path

class SignalsEngine:
    def __init__(self):
        self.db_path = get_db_path()

    def detect_nps_anomalies(self, category: str):
        """Minimal logic: Scans for NPS city outliers."""
        conn = sqlite3.connect(self.db_path)
        bridge_table = "v_kitchen_ownership" if category == "Mixer Grinder" else "v_room_appliances"
        
        # 1. Fetch NPS by city
        query = f"""
            SELECT city_name, 
                   ROUND((SUM(CASE WHEN nps_score >= 9 THEN 1.0 ELSE 0 END) - 
                          SUM(CASE WHEN nps_score <= 6 THEN 1.0 ELSE 0 END)) * 100.0 / COUNT(*), 1) as nps
            FROM v_brand_nps
            WHERE respondent_id IN (SELECT respondent_id FROM {bridge_table} WHERE appliance_name = '{category}')
            GROUP BY city_name
            HAVING COUNT(*) >= 5
        """
        df = pd.read_sql(query, conn)
        conn.close()

        if df.empty: return []

        avg_nps = df['nps'].mean()
        std_nps = df['nps'].std() if len(df) > 1 else 0
        
        signals = []
        
        # 2. Find Outliers (Threshold: 1.2 std deviations)
        for _, row in df.iterrows():
            delta = row['nps'] - avg_nps
            if abs(delta) > (1.2 * std_nps) and std_nps > 0:
                severity = "Critical" if delta < 0 else "Positive"
                title = f"{'NPS Volatility' if delta < 0 else 'Market Surge'} in {row['city_name']}"
                description = f"NPS for {category} in {row['city_name']} is {row['nps']}, deviate from average ({avg_nps:.1f}) by {abs(delta):.1f} points."
                rec = "Investigate supply chain and retail feedback." if delta < 0 else "Capitalize on positive sentiment in regional marketing."
                
                signals.append({
                    "title": title,
                    "description": description,
                    "severity": severity,
                    "recommendation": rec,
                    "metric": f"{row['nps']} NPS"
                })
        
        return signals

    def detect_amazon_anomalies(self, category: str):
        """Finds brands with >0.5 star drop on Amazon, triangulated with NPS."""
        conn = sqlite3.connect(self.db_path)
        bridge_table = "v_kitchen_ownership" if category == "Mixer Grinder" else "v_room_appliances"
        
        # 1. Get Amazon Ratings (Current vs Previous Month)
        query_amazon = f"""
            WITH DateBounds AS (
                SELECT MAX(review_date) as max_date FROM fact_amazon_reviews
            ),
            MonthlyRatings AS (
                SELECT 
                    brand_name,
                    AVG(CASE WHEN review_date > date((SELECT max_date FROM DateBounds), '-30 days') THEN rating END) as current_avg,
                    AVG(CASE WHEN review_date <= date((SELECT max_date FROM DateBounds), '-30 days') 
                             AND review_date > date((SELECT max_date FROM DateBounds), '-60 days') THEN rating END) as prev_avg
                FROM fact_amazon_reviews
                WHERE category = '{category}'
                GROUP BY brand_name
            )
            SELECT brand_name, current_avg, prev_avg, (prev_avg - current_avg) as drop_val
            FROM MonthlyRatings
            WHERE (prev_avg - current_avg) > 0.5
        """
        amazon_df = pd.read_sql(query_amazon, conn)
        
        if amazon_df.empty:
            conn.close()
            return []
            
        # 2. Get Brand NPS for Triangulation
        query_nps = f"""
            SELECT brand_name,
                   ROUND((SUM(CASE WHEN nps_score >= 9 THEN 1.0 ELSE 0 END) - 
                          SUM(CASE WHEN nps_score <= 6 THEN 1.0 ELSE 0 END)) * 100.0 / COUNT(*), 1) as nps
            FROM v_brand_nps
            WHERE respondent_id IN (SELECT respondent_id FROM {bridge_table} WHERE appliance_name = '{category}')
            GROUP BY brand_name
            HAVING COUNT(*) >= 5
        """
        nps_df = pd.read_sql(query_nps, conn)
        conn.close()
        
        signals = []
        for _, row in amazon_df.iterrows():
            brand = row['brand_name']
            nps_match = nps_df[nps_df['brand_name'] == brand]
            nps_val = nps_match['nps'].values[0] if not nps_match.empty else None
            
            # Triangulation Logic
            if nps_val is not None and nps_val < 20: 
                severity = "Critical"
                title = f"Quality Crisis: {brand} {category}"
                desc = f"Amazon ratings dropped by {row['drop_val']:.1f} stars. Survey NPS is also low ({nps_val})."
                rec = "Urgent QA audit of recent batches and production line inspection."
            else:
                severity = "Warning"
                title = f"Feedback Gap: {brand} {category}"
                desc = f"Amazon ratings dropped by {row['drop_val']:.1f} stars, but survey sentiment remains stable."
                rec = "Investigate shipping, packaging, or counterfeit issues specific to the Amazon channel."
                
            signals.append({
                "title": title,
                "description": desc,
                "severity": severity,
                "recommendation": rec,
                "metric": f"-{row['drop_val']:.1f} Stars (Amazon)"
            })
            
        return signals

    def get_all_signals(self, category: str = "All") -> list:
        """
        Compute real anomaly signals from the survey DB.
        Replaces previous queries to fact_amazon_reviews and fact_findings_ledger
        (neither table exists in the current schema).
        """
        conn = sqlite3.connect(str(self.db_path))
        signals = []

        try:
            base_n = int(
                pd.read_sql("SELECT COUNT(*) n FROM v_respondents", conn).iloc[0]["n"]
            )

            # 1. Brands with negative NPS (min 30 raters)
            neg = pd.read_sql("""
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
            for _, row in neg.iterrows():
                signals.append({
                    "title": f"Negative NPS — {row['brand_name']}",
                    "description": (
                        f"NPS = {row['nps']:+.0f} among {row['n']:,} raters. "
                        "Detractors outnumber promoters."
                    ),
                    "severity": "Critical",
                    "metric": f"NPS {row['nps']:+.0f}",
                    "recommendation": (
                        f"Investigate service complaints for {row['brand_name']}. "
                        "Cross-check qualitative transcripts for durability themes."
                    ),
                })

            # 2. Awareness-salience gap (aided>=35%, TOM<3%)
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
                WHERE a.aided_n*100.0/? >= 35
                  AND COALESCE(t.tom_n,0)*100.0/? < 3
                ORDER BY a.aided_n DESC LIMIT 3
            """, conn, params=[base_n, base_n, base_n, base_n])
            for _, row in gap.iterrows():
                signals.append({
                    "title": f"Salience Gap — {row['brand_name']}",
                    "description": (
                        f"Aided awareness {row['aided_pct']}% but Top-of-Mind "
                        f"only {row['tom_pct']}%. Brand recognised but not recalled spontaneously."
                    ),
                    "severity": "Warning",
                    "metric": f"TOM {row['tom_pct']}% vs Aided {row['aided_pct']}%",
                    "recommendation": (
                        "Invest in salience-building media. "
                        "Focus on distinctive brand assets and recall cues."
                    ),
                })

        except Exception as e:
            signals.append({
                "title": "Signal computation error",
                "description": str(e),
                "severity": "Warning",
                "metric": "—",
                "recommendation": "Check DB connectivity.",
            })
        finally:
            conn.close()

        return signals
