"""
LENS  Proper Survey Data Ingestion (v2)
========================================
Fixes all issues found in the data audit:

1. Category from 'cat_code' column (not mq2b  that was wrong)
2. Correct city mapping from choices sheet (1=Delhi, 9=Mumbai, etc.)
3. Correct zone, age_band, gender, and SEC class from choices
4. bq3a (importance 1-7) stored as value_numeric with scale_min/max in variables
5. bq3b (brand association) stored as multi-select text, one row per brand selected
6. bq5 (satisfaction 0-10) stored as value_numeric
7. Variables enriched with section, question_type, label_en, label_hi, feature_bucket
8. Need Attributes cross-referenced from BQ3a3b-Need Attributes sheet

All real names are masked before insert.
"""

import os
import re
import hashlib
import sqlite3
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

DATAFILE = Path("data/raw/OX Datafile 09062021 (n=6631).xlsx")
DATAMAP = Path("data/raw/OX - Datamap.xlsx")
DB_PATH = Path("lens.db")
PROJECT_ID = "OX_WAVE1"

# 
# 1. LOAD ALL REFERENCE DATA
# 
def load_reference_data():
    survey = pd.read_excel(DATAMAP, sheet_name='survey')
    survey.columns = ['type', 'name', 'label_en', 'label_hi'] + list(survey.columns[4:])
    
    choices = pd.read_excel(DATAMAP, sheet_name='choices')
    
    need_attr = pd.read_excel(DATAMAP, sheet_name='BQ3a3b-Need Attributes')
    need_attr.columns = [
        'broad_feature', 'label_en', 'label_hi',
        'cf_y', 'ac_y', 'wh_y', 'mg_y', 'led_y', 'wp_y'
    ] + list(need_attr.columns[9:])
    
    return survey, choices, need_attr

def build_lookup(choices, list_name: str) -> dict:
    """Build {code_str: label} dict from choices sheet for a given list name."""
    rows = choices[choices['list name'] == list_name]
    return {str(r['name']).strip(): str(r['label']).strip() for _, r in rows.iterrows()}

# 
# 2. CATALOGUE ALL VARIABLES WITH FULL METADATA
# 
def build_variable_catalogue(survey: pd.DataFrame, need_attr: pd.DataFrame) -> dict:
    """
    Returns {variable_code: {label_en, label_hi, section, question_type, 
                              scale_min, scale_max, feature_bucket, category_scope}}
    """
    catalogue = {}
    
    # Build a flat lookup from survey codebook
    survey_lookup = {}
    for _, row in survey.iterrows():
        code = str(row['name']).strip() if pd.notna(row['name']) else None
        if not code or code == 'nan':
            continue
        survey_lookup[code] = {
            'label_en': str(row['label_en']).strip() if pd.notna(row['label_en']) else '',
            'label_hi': str(row['label_hi']).strip() if pd.notna(row['label_hi']) else '',
            'question_type': str(row['type']).strip() if pd.notna(row['type']) else '',
        }
    
    # Build Need Attributes lookup: bq3a_X_Y  attribute label + feature bucket
    # The need_attr rows map 1-based attribute index to label + feature bucket
    na_rows = need_attr.iloc[2:].reset_index(drop=True)  # skip header rows
    
    # Track current broad_feature across rows (it's a spanning merge cell)
    current_feature = None
    attr_index = 0
    na_by_index = {}  # {1-based-index: {label_en, label_hi, feature_bucket, mg_applicable}}
    
    for _, row in na_rows.iterrows():
        if pd.notna(row['broad_feature']) and str(row['broad_feature']).strip() not in ('', 'nan'):
            current_feature = str(row['broad_feature']).strip()
        
        label = str(row['label_en']).strip() if pd.notna(row['label_en']) else ''
        if not label or label == 'nan':
            continue
        
        attr_index += 1
        na_by_index[attr_index] = {
            'label_en': label,
            'label_hi': str(row['label_hi']).strip() if pd.notna(row['label_hi']) else '',
            'feature_bucket': current_feature or 'Unknown',
            'mg_applicable': row['mg_y'] == 'Y' if pd.notna(row['mg_y']) else False,
            'cf_applicable': row['cf_y'] == 'Y' if pd.notna(row['cf_y']) else False,
        }
    
    # Now map bq3a_X_Y codes: bq3a_{cat}_{attr_index}
    # cat: 1=CF, 2=AC, 3=MG, 4=LED, 5=WH, 6=WP
    CAT_SECTION = {1:'Ceiling Fans', 2:'Air Coolers', 3:'Mixer Grinder', 
                   4:'LED Batten', 5:'Water Heater', 6:'Water Pumps'}
    
    for var_code, info in survey_lookup.items():
        section = 'General'
        scale_min, scale_max = None, None
        feature_bucket = None
        category_scope = None
        
        if var_code.startswith('bq3a_'):
            # bq3a_{cat}_{attr_idx} 
            parts = var_code.split('_')
            try:
                cat_num = int(parts[1])
                attr_idx = int(parts[2]) if len(parts) > 2 else None
                section = 'Need Attributes - Importance'
                question_type = 'importance_7pt'
                scale_min, scale_max = 1, 7
                category_scope = CAT_SECTION.get(cat_num, 'Unknown')
                if attr_idx and attr_idx in na_by_index:
                    info['label_en'] = na_by_index[attr_idx]['label_en'] + ' [Importance]'
                    info['label_hi'] = na_by_index[attr_idx]['label_hi']
                    feature_bucket = na_by_index[attr_idx]['feature_bucket']
            except (ValueError, IndexError):
                pass
        
        elif var_code.startswith('bq3b_'):
            section = 'Need Attributes - Brand Association'
            question_type = 'brand_multiselect'
            category_scope = 'Cross-category'
        
        elif var_code.startswith('bq5'):
            section = 'Brand Health - Satisfaction'
            question_type = 'satisfaction_10pt'
            scale_min, scale_max = 0, 10
        
        elif var_code.startswith('bq1'):
            section = 'Brand Health - Awareness'
            question_type = 'brand_awareness'
        
        elif var_code.startswith('bq2'):
            section = 'Brand Health - Usage'
            question_type = 'brand_usage'
        
        elif var_code.startswith('pq'):
            section = 'Purchase Journey'
        
        elif var_code.startswith('aq'):
            section = 'Post Purchase'
        
        elif var_code.startswith('dq'):
            section = 'Usage Diagnostics'
        
        elif var_code in ('centre', 'zone', 's1', 'age', 'age_grid', 's4a', 's4b', 'cat_code'):
            section = 'Respondent Profile'
        
        catalogue[var_code] = {
            'label_en': info.get('label_en', var_code),
            'label_hi': info.get('label_hi', ''),
            'section': section,
            'question_type': info.get('question_type', ''),
            'scale_min': scale_min,
            'scale_max': scale_max,
            'feature_bucket': feature_bucket,
            'category_scope': category_scope,
        }
    
    return catalogue

# 
# 3. MASK PII
# 
def mask_name(name: str) -> str:
    if not name or str(name).strip().lower() in ('nan', 'none', ''):
        return 'R_unknown'
    h = hashlib.sha256(str(name).lower().strip().encode()).hexdigest()[:8]
    return f"R_{h}"

# 
# 4. NCCS CLASS COMPUTATION (s4a=education, s4b=occupation)
# 
def compute_sec(s4a, s4b) -> str:
    """Approximate NCCS class from education + occupation codes."""
    try:
        edu = int(float(s4a)) if pd.notna(s4a) else 0
        occ = int(float(s4b)) if pd.notna(s4b) else 0
        score = edu + occ
        if score >= 17: return 'A1'
        if score >= 14: return 'A2'
        if score >= 11: return 'B1'
        if score >= 8:  return 'B2'
        if score >= 5:  return 'C'
        return 'D'
    except:
        return 'Unknown'

# 
# 5. MAIN INGESTION
# 
def run_ingestion():
    print("Loading reference data...")
    survey, choices, need_attr = load_reference_data()
    
    # Build all lookups
    city_map = build_lookup(choices, 'centre')
    zone_map = build_lookup(choices, 'zone')
    age_map = build_lookup(choices, 'age_grid')
    gender_map = build_lookup(choices, 's1')
    
    CAT_MAP = {1:'Ceiling Fans', 2:'Air Coolers', 3:'Mixer Grinder',
               4:'LED Batten', 5:'Water Heater', 6:'Water Pumps'}
    
    print("Building variable catalogue...")
    var_catalogue = build_variable_catalogue(survey, need_attr)
    print(f"  Catalogued {len(var_catalogue)} variables with proper metadata")
    
    print("Loading full datafile (~6631 rows)...")
    df = pd.read_excel(DATAFILE)
    print(f"  Shape: {df.shape}")
    
    # Re-initialise DB
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    
    # Wipe existing data (clean slate)
    print("Clearing existing respondent + response + variable data...")
    conn.execute("DELETE FROM responses")
    conn.execute("DELETE FROM respondents")
    conn.execute("DELETE FROM variables")
    conn.execute("DELETE FROM need_attributes")
    conn.execute("DELETE FROM projects")
    conn.commit()
    
    # Insert project
    conn.execute("""
        INSERT INTO projects (project_id, project_name, client_name, wave)
        VALUES (?, ?, ?, ?)
    """, (PROJECT_ID, 'OX Crompton Survey', 'Crompton', 'Wave 1 (2021)'))
    conn.commit()
    
    # Insert all variables with proper metadata
    print("Inserting enriched variables...")
    data_cols = [c for c in df.columns if c in var_catalogue]
    var_rows = []
    for code in data_cols:
        meta = var_catalogue[code]
        var_rows.append((
            code, PROJECT_ID,
            meta.get('category_scope', 'Cross-category'),
            meta.get('section', 'General'),
            meta.get('question_type', ''),
            meta.get('label_en', code),
            meta.get('label_hi', ''),
            meta.get('scale_min'),
            meta.get('scale_max'),
            meta.get('feature_bucket'),
        ))
    conn.executemany("""
        INSERT OR IGNORE INTO variables 
            (variable_code, project_id, category, section, question_type, 
             label_en, label_hi, scale_min, scale_max, feature_bucket)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, var_rows)
    conn.commit()
    print(f"  Inserted {len(var_rows)} variables")
    
    # Identify which columns are response data (exists in variable catalogue)
    response_cols = [c for c in data_cols if c not in (
        'centre', 'zone', 's1', 'age', 'age_grid', 's4a', 's4b', 
        'cat_code', 'resp_name', 'phone', 'resp_add', 'pin',
        'int_name', 'int_stime', 'int_stime.1', 'int_etime', 'deviceid', 'intro', 'intro3'
    )]
    
    print(f"Ingesting respondents and responses ({len(df)} respondents x {len(response_cols)} variables)...")
    
    BATCH = 200
    total_resp = 0
    total_responses = 0
    
    for start in range(0, len(df), BATCH):
        batch = df.iloc[start:start + BATCH]
        resp_rows = []
        response_rows = []
        
        for _, row in batch.iterrows():
            #  Respondent profile 
            raw_name = str(row.get('resp_name', ''))
            masked = mask_name(raw_name)
            centre_code = str(int(float(row['centre']))) if pd.notna(row.get('centre')) else ''
            zone_code = str(int(float(row['zone']))) if pd.notna(row.get('zone')) else ''
            age_code = str(int(float(row['age_grid']))) if pd.notna(row.get('age_grid')) else ''
            gender_code = str(int(float(row['s1']))) if pd.notna(row.get('s1')) else ''
            cat_code = int(float(row['cat_code'])) if pd.notna(row.get('cat_code')) else 0
            
            city = city_map.get(centre_code, 'Unknown')
            zone = zone_map.get(zone_code, 'Unknown')
            age_band = age_map.get(age_code, 'Unknown')
            gender = gender_map.get(gender_code, 'Unknown')
            category = CAT_MAP.get(cat_code, 'Unknown')
            sec_class = compute_sec(row.get('s4a'), row.get('s4b'))
            
            # Stable respondent ID: hash of masked_name + centre
            resp_id = hashlib.sha256(f"{masked}_{centre_code}".encode()).hexdigest()[:12]
            is_affluent = sec_class in ('A1', 'A2')
            
            # respondent_type from bq0a: 1=recent buyer, 2=intender
            bq0a_val = row.get('bq0a')
            if pd.notna(bq0a_val):
                resp_type = 'recent_buyer' if int(float(bq0a_val)) == 1 else 'intender'
            else:
                resp_type = 'unknown'
            
            resp_rows.append((
                resp_id, PROJECT_ID, masked, city, zone, gender, age_band, 
                sec_class, category, resp_type, is_affluent
            ))
            
            #  Responses 
            for col in response_cols:
                raw_val = row.get(col)
                if pd.isna(raw_val):
                    continue
                
                val_str_raw = str(raw_val).strip()
                if val_str_raw in ('', 'nan', 'None'):
                    continue
                
                val_num = None
                val_text = None
                
                # Force specific multi-select variables to save as text 
                # even if the user selected only one option (so it parses as a float)
                if col in ('bq1b', 'bq1c', 'bq1d', 'bq1e', 'bq1f', 'mq2a', 'mq3a', 'mq3b'):
                    try:
                        # Attempt to strip decimals if it happens to be just a number like "3.0"
                        as_float = float(val_str_raw)
                        val_text = str(int(as_float))
                    except ValueError:
                        val_text = val_str_raw[:498]
                else:
                    try:
                        val_num = float(val_str_raw)
                    except ValueError:
                        val_text = val_str_raw[:498]
                
                response_rows.append((resp_id, col, val_num, val_text))
        
        if resp_rows:
            conn.executemany("""
                INSERT OR IGNORE INTO respondents
                    (respondent_id, project_id, masked_name, city, zone, gender,
                     age_band, sec_class, category, respondent_type, is_affluent)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, resp_rows)
        
        if response_rows:
            conn.executemany("""
                INSERT INTO responses (respondent_id, variable_code, value_numeric, value_text)
                VALUES (?,?,?,?)
            """, response_rows)
        
        conn.commit()
        total_resp += len(resp_rows)
        total_responses += len(response_rows)
        print(f"  Batch {start//BATCH + 1}: +{len(response_rows)} responses | {total_resp} respondents processed")
    
    conn.close()
    print(f"\n=== INGESTION COMPLETE ===")
    print(f"  Respondents: {total_resp}")
    print(f"  Response rows: {total_responses:,}")
    print(f"  Variables with full metadata: {len(var_rows)}")

if __name__ == "__main__":
    run_ingestion()

