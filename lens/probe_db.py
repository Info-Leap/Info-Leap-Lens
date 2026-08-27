"""
Quick probe of lens.db to understand the real data shape.
Run: python probe_db.py
"""
import sqlite3, json

conn = sqlite3.connect("lens.db")
conn.row_factory = sqlite3.Row

def q(sql, params=()):
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

print("=" * 60)
print("1. RESPONDENT COUNTS BY CATEGORY")
print("=" * 60)
for r in q("SELECT category, COUNT(*) as n FROM respondents GROUP BY category ORDER BY n DESC"):
    print(r)

print("\n" + "=" * 60)
print("2. VARIABLE SECTIONS IN DB")
print("=" * 60)
for r in q("SELECT section, question_type, COUNT(*) as n FROM variables GROUP BY section, question_type ORDER BY n DESC LIMIT 20"):
    print(r)

print("\n" + "=" * 60)
print("3. SAMPLE bq5 VARIABLES (satisfaction)  first 5")
print("=" * 60)
bq5_vars = q("SELECT variable_code, label_en, scale_min, scale_max FROM variables WHERE variable_code LIKE 'bq5%' LIMIT 10")
for r in bq5_vars:
    print(r)

print("\n" + "=" * 60)
print("4. SAMPLE bq5 RESPONSES  what values exist?")
print("=" * 60)
for r in q("""
    SELECT r.variable_code, r.value_numeric, r.value_text, COUNT(*) as cnt
    FROM responses r
    WHERE r.variable_code LIKE 'bq5%'
    GROUP BY r.variable_code, r.value_numeric
    ORDER BY cnt DESC LIMIT 20
"""):
    print(r)

print("\n" + "=" * 60)
print("5. SAMPLE bq3a VARIABLES (importance)  first 10")
print("=" * 60)
for r in q("SELECT variable_code, label_en, feature_bucket FROM variables WHERE variable_code LIKE 'bq3a_3%' LIMIT 10"):
    print(r)

print("\n" + "=" * 60)
print("6. AVERAGE IMPORTANCE RATINGS (bq3a for MG = cat 3)")
print("=" * 60)
for r in q("""
    SELECT v.variable_code, v.label_en, v.feature_bucket,
           ROUND(AVG(r.value_numeric), 2) AS avg_importance,
           COUNT(r.value_numeric) AS n
    FROM variables v
    JOIN responses r ON v.variable_code = r.variable_code
    JOIN respondents rsp ON r.respondent_id = rsp.respondent_id
    WHERE v.variable_code LIKE 'bq3a_3_%'
    AND rsp.category = 'Mixer Grinder'
    AND r.value_numeric IS NOT NULL
    GROUP BY v.variable_code, v.label_en, v.feature_bucket
    ORDER BY avg_importance DESC
    LIMIT 15
"""):
    print(r)

print("\n" + "=" * 60)
print("7. SAMPLE bq3b (brand association)  what's in value_text?")
print("=" * 60)
for r in q("""
    SELECT variable_code, value_text, value_numeric, COUNT(*) as n
    FROM responses
    WHERE variable_code LIKE 'bq3b%'
    GROUP BY variable_code, value_text
    ORDER BY n DESC LIMIT 15
"""):
    print(r)

print("\n" + "=" * 60)
print("8. bq5 AVG by respondent category (correct satisfaction)")
print("=" * 60)
for r in q("""
    SELECT rsp.category, rsp.city,
           ROUND(AVG(res.value_numeric), 2) AS avg_satisfaction,
           COUNT(DISTINCT rsp.respondent_id) AS n_respondents
    FROM respondents rsp
    JOIN responses res ON rsp.respondent_id = res.respondent_id
    WHERE res.variable_code = 'bq5'
    AND rsp.category = 'Mixer Grinder'
    AND res.value_numeric IS NOT NULL
    GROUP BY rsp.city
    ORDER BY avg_satisfaction DESC
"""):
    print(r)

print("\n" + "=" * 60)
print("9. DISTINCT bq5 variable codes that exist")
print("=" * 60)
for r in q("SELECT DISTINCT variable_code FROM responses WHERE variable_code LIKE 'bq5%' LIMIT 10"):
    print(r)

print("\n" + "=" * 60)
print("10. GENDER BREAKDOWN for MG")
print("=" * 60)
for r in q("""
    SELECT gender, COUNT(*) as n 
    FROM respondents WHERE category='Mixer Grinder' 
    GROUP BY gender
"""):
    print(r)

conn.close()
print("\nDone.")

