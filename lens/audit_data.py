"""
Deep audit of the OX Datafile structure to understand exactly what data exists
before redesigning the ingestion.
"""
import pandas as pd
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("=" * 70)
print("LENS DATA AUDIT  OX Survey Datafile")
print("=" * 70)

# Load all sheets
survey = pd.read_excel('data/raw/OX - Datamap.xlsx', sheet_name='survey')
choices = pd.read_excel('data/raw/OX - Datamap.xlsx', sheet_name='choices')
need_attr = pd.read_excel('data/raw/OX - Datamap.xlsx', sheet_name='BQ3a3b-Need Attributes')
df = pd.read_excel('data/raw/OX Datafile 09062021 (n=6631).xlsx')

print(f"\n[1] DATAFILE SHAPE: {df.shape[0]} respondents x {df.shape[1]} columns")

# Category distribution
CAT_MAP = {1: 'Ceiling Fans', 2: 'Air Coolers', 3: 'Mixer Grinder',
           4: 'LED Batten', 5: 'Water Heater', 6: 'Water Pumps'}
print("\n[2] CATEGORY (cat_code) DISTRIBUTION:")
for code, count in df['cat_code'].value_counts().items():
    print(f"  {code}  {CAT_MAP.get(int(code), '?')}: {count} respondents")

# Respondent metadata
print("\n[3] RESPONDENT METADATA COLUMNS:")
meta_cols = ['centre', 'zone', 's1', 'age', 'age_grid', 's4a', 's4b', 'cat_code', 'resp_name']
for col in meta_cols:
    if col in df.columns:
        non_null = df[col].notna().sum()
        print(f"  {col}: {non_null}/{len(df)} non-null values")

# City mapping from choices
centre_choices = choices[choices['list name'] == 'centre'][['name','label']]
print("\n[4] CENTRE CODE  CITY MAPPING:")
print(centre_choices.to_string(index=False))

# Zone mapping
zone_choices = choices[choices['list name'] == 'zone'][['name','label']]
print("\n[5] ZONE MAPPING:")
print(zone_choices.to_string(index=False))

# Age band mapping
age_choices = choices[choices['list name'] == 'age_grid'][['name','label']]
print("\n[6] AGE BAND MAPPING:")
print(age_choices.to_string(index=False))

# SEC class mapping
sec_choices = choices[choices['list name'] == 's4b'][['name','label']]
print("\n[7] SEC CLASS MAPPING (s4b):")
print(sec_choices.to_string(index=False))

# Gender mapping
gender_choices = choices[choices['list name'] == 's1'][['name','label']]
print("\n[8] GENDER MAPPING (s1):")
print(gender_choices.to_string(index=False))

# Question groups & their variable codes with proper labels
print("\n[9] QUESTION GROUPS (sections) in survey:")
# Get group rows (rows where 'type' == row's 'name' value)
type_col = survey.columns[0]  # 'type'
name_col = survey.columns[1]  # 'name'
label_col = survey.columns[2]  # 'label'
survey.columns = ['type', 'name', 'label', 'label_hi'] + list(survey.columns[4:])
groups = survey[survey['type'] == survey['name']][['name', 'label']].dropna()
print(groups.to_string(index=False))

# Key scales  bq3a (1-7 importance), bq3b (brand association), bq5 (0-10 satisfaction)
print("\n[10] KEY SCALES:")
bq3a_ch = choices[choices['list name'] == 'bq3a'][['name','label']]
print(f"  bq3a (Importance, 1-7): {bq3a_ch.iloc[0]['name']}='{bq3a_ch.iloc[0]['label']}'  {bq3a_ch.iloc[-1]['name']}='{bq3a_ch.iloc[-1]['label']}'")
bq5_ch = choices[choices['list name'] == 'bq5'][['name','label']]
print(f"  bq5 (Satisfaction 0-10): 0='{bq5_ch.iloc[0]['label']}'  10='{bq5_ch.iloc[-1]['label']}'")

# Need attributes breakdown
print("\n[11] NEED ATTRIBUTES (bq3a/bq3b) STRUCTURE:")
# Row 0 = header, row 1 = question counts per category
need_attr.columns = ['broad_feature', 'label_en', 'label_hi', 'cf_count', 'ac_count', 'wh_count', 'mg_count', 'led_count', 'wp_count'] + list(need_attr.columns[9:])
na_data = need_attr.iloc[2:].dropna(subset=['label_en'])
print(f"  Total attributes: {len(na_data)}")
mg_attrs = na_data[na_data['mg_count'].notna() & (na_data['mg_count'] == 'Y')]
print(f"  Mixer Grinder specific: {len(mg_attrs)} attributes")
print(f"  First 5 MG attributes:")
for _, row in mg_attrs.head(5).iterrows():
    print(f"    - [{row['broad_feature'] if pd.notna(row['broad_feature']) else 'same'}] {row['label_en']}")

# Variable code range
bq3a_cols = [c for c in df.columns if c.startswith('bq3a')]
bq3b_cols = [c for c in df.columns if c.startswith('bq3b') and '/' not in c and '_d' not in c]
bq5_cols = [c for c in df.columns if c.startswith('bq5')]
print(f"\n[12] KEY VARIABLE COUNTS IN DATAFILE:")
print(f"  bq3a (importance scores) cols: {len(bq3a_cols)}")
print(f"  bq3b (brand association) cols: {len(bq3b_cols)}")
print(f"  bq5 (satisfaction) cols: {len(bq5_cols)}")
print(f"  Sample bq3a values (respondent 1): {df[bq3a_cols[:5]].iloc[1].to_dict()}")
print(f"  Sample bq5 values (respondent 1): {df[bq5_cols[:5]].iloc[1].to_dict()}")

print("\n[13] WHAT WAS WRONG WITH PREVIOUS INGESTION:")
print("  a) cat_code was detected using mq2b prefix which is WRONG  should use 'cat_code' column directly")
print("  b) City codes 1-18  we had wrong mapping (14=Amritsar was wrong, 14=Patna per choices sheet)")
print("  c) variables ingested without knowing which questions map to which category sections")
print("  d) Need Attributes (bq3a/bq3b) needed the attribute labels cross-referenced from the Need Attrs sheet")
print("  e) bq3b columns have multi-select format (bq3b_1/1, bq3b_1/2 etc) = brand codes, not stored meaningfully")
print("  f) SEC class needs 2 columns: s4a (education) + s4b (occupation)  compute NCCS grade")

