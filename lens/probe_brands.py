"""
Probe: find where brand names actually live in the schema.
"""
import sqlite3

conn = sqlite3.connect("lens.db")
conn.row_factory = sqlite3.Row

def q(sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]

print("=" * 60)
print("1. bq3b variable labels (these ARE the brand+attribute combos)")
print("=" * 60)
for r in q("""
    SELECT variable_code, label_en, category, feature_bucket
    FROM variables
    WHERE variable_code LIKE 'bq3b%'
    ORDER BY variable_code
    LIMIT 30
"""):
    print(r)

print("\n" + "=" * 60)
print("2. bq1 / bq2 label_en (brand awareness/usage)")
print("=" * 60)
for r in q("""
    SELECT variable_code, label_en, section, question_type
    FROM variables
    WHERE variable_code LIKE 'bq1%' OR variable_code LIKE 'bq2%'
    LIMIT 20
"""):
    print(r)

print("\n" + "=" * 60)
print("3. NEED ATTRIBUTES TABLE")
print("=" * 60)
for r in q("SELECT * FROM need_attributes LIMIT 10"):
    print(r)

print("\n" + "=" * 60)
print("4. bq3b RESPONSE VALUES  what do value_numeric codes mean?")
print("   value_numeric=1 most common, but what is 20?")
print("=" * 60)
for r in q("""
    SELECT value_numeric, COUNT(*) as cnt
    FROM responses
    WHERE variable_code LIKE 'bq3b%'
    GROUP BY value_numeric
    ORDER BY cnt DESC
"""):
    print(r)

print("\n" + "=" * 60)
print("5. Variables with 'brand' in label_en (any section)")
print("=" * 60)
for r in q("""
    SELECT variable_code, label_en, section
    FROM variables
    WHERE LOWER(label_en) LIKE '%brand%' OR LOWER(label_en) LIKE '%crompton%'
       OR LOWER(label_en) LIKE '%mixer%'
    LIMIT 20
"""):
    print(r)

print("\n" + "=" * 60)
print("6. Full bq3b label_en for variables we know have lots of responses")
print("   bq3b_92, bq3b_84, bq3b_88 from earlier probe")
print("=" * 60)
for code in ['bq3b_92', 'bq3b_84', 'bq3b_88', 'bq3b_89', 'bq3b_91', 'bq3b_78', 'bq3b_79', 'bq3b_80']:
    rows = q("SELECT variable_code, label_en, category FROM variables WHERE variable_code = ?", (code,))
    for r in rows:
        print(r)

print("\n" + "=" * 60)
print("7. MG bq3b variables (brand association for Mixer Grinder)")
print("   Using bq3a_3 pattern, bq3b_3 might be MG brand assoc")
print("=" * 60)
for r in q("""
    SELECT variable_code, label_en, category
    FROM variables
    WHERE variable_code LIKE 'bq3b_3%'
    ORDER BY variable_code
    LIMIT 20
"""):
    print(r)

print("\n" + "=" * 60)
print("8. pq / aq variables for brand mentions")
print("=" * 60)
for r in q("""
    SELECT variable_code, label_en, section
    FROM variables
    WHERE section LIKE '%Brand%'
    LIMIT 20
"""):
    print(r)

conn.close()

