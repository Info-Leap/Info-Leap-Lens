import sqlite3
import pandas as pd

db_path = r"D:\antigravity project\infoleap project\info-leap\data\project_1\oxdata.db"
conn = sqlite3.connect(db_path)

print("--- DATA CALIBRATION ---")

# 1. NPS
nps_q = """
SELECT brand_name, 
       ROUND((SUM(CASE WHEN nps_score >= 9 THEN 1.0 ELSE 0 END) - 
              SUM(CASE WHEN nps_score <= 6 THEN 1.0 ELSE 0 END)) * 100.0 / COUNT(*), 1) as nps
FROM v_brand_nps 
WHERE brand_name IN ('Crompton', 'Bajaj', 'Preethi')
GROUP BY brand_name
"""
print(pd.read_sql(nps_q, conn))

# 2. Base Counts
print("\nTotal Respondents:", conn.execute("SELECT COUNT(*) FROM v_respondents").fetchone()[0])

conn.close()
