import sqlite3
import json
import os
from pathlib import Path

def check_progress():
    print("=== LENS FINAL PROGRESS AUDIT ===")
    
    # 1. Database Check
    db_path = "lens.db"
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        res_count = conn.execute("SELECT count(*) FROM respondents").fetchone()[0]
        resp_count = conn.execute("SELECT count(*) FROM responses").fetchone()[0]
        var_count = conn.execute("SELECT count(*) FROM variables").fetchone()[0]
        conn.close()
        print(f"Database: {db_path} FOUND")
        print(f"  - Respondents Ingested: {res_count}")
        print(f"  - Total Responses Ingested: {resp_count}")
        print(f"  - Variables Profiled: {var_count}")
    else:
        print(f"Error: {db_path} NOT FOUND")

    # 2. PageIndex Trees Check
    trees_dir = Path("pageindex_trees")
    if trees_dir.exists():
        json_count = len(list(trees_dir.glob("*.json")))
        print(f"PageIndex Trees: {json_count} files generated")
    else:
        print("Error: pageindex_trees/ directory NOT FOUND")

    # 3. Registry Check
    reg_path = "data/registry/document_registry.json"
    if os.path.exists(reg_path):
        with open(reg_path) as f:
            reg = json.load(f)
            print(f"Registry: {len(reg.get('documents', []))} documents registered")
    else:
        print(f"Error: {reg_path} NOT FOUND")

if __name__ == "__main__":
    check_progress()

