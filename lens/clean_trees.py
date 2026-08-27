import os
import json
from pathlib import Path

TREES_DIR = Path("pageindex_trees")

def clean_old_format_jsons():
    deleted_count = 0
    for json_file in TREES_DIR.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Old format has 'root', new format has 'sections'
            if 'root' in data and 'sections' not in data:
                print(f"Deleting old format JSON: {json_file.name}")
                json_file.unlink()
                deleted_count += 1
        except Exception as e:
            print(f"Error checking {json_file.name}: {e}")
            
    print(f"\nDeleted {deleted_count} old format JSON files.")

if __name__ == "__main__":
    clean_old_format_jsons()

