import os
import json
import sqlite3
from pathlib import Path

TREES_DIR = Path('oxdata/data/pageindex_trees')
DB_PATH = Path('oxdata/data/qual_index.db')

def build_index():
    print("Building qualitative index...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('DROP TABLE IF EXISTS qual_index')
    cur.execute('CREATE TABLE qual_index (brand TEXT, city TEXT, doc_id TEXT)')
    
    brands = ['Bajaj', 'Crompton', 'Havells', 'Philips', 'Usha', 'Prestige', 'Butterfly', 'Maharaja', 'Kent']
    cities = ['Lucknow', 'Delhi', 'Patna', 'Mumbai', 'Ahmedabad', 'Kolkata', 'Chennai', 'Bangalore', 'Hyderabad', 'Bhubaneswar']
    
    for f_path in TREES_DIR.glob('*_tree.json'):
        try:
            with open(f_path, encoding='utf-8') as f:
                content = f.read().lower()
            
            doc_id = f_path.stem.replace('_tree', '')
            found_brands = [b for b in brands if b.lower() in content]
            found_cities = [c for c in cities if c.lower() in content]
            
            if not found_brands: found_brands = [None]
            if not found_cities: found_cities = [None]
            
            for b in found_brands:
                for c in found_cities:
                    cur.execute('INSERT INTO qual_index VALUES (?, ?, ?)', (b, c, doc_id))
        except Exception as e:
            print(f"Error indexing {f_path}: {e}")
            
    conn.commit()
    conn.close()
    print("Index built successfully.")

if __name__ == "__main__":
    build_index()
