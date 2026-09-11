import unittest
import sqlite3
import os
import sys
from pathlib import Path

# Ensure project root is in path
current_dir = Path(__file__).resolve().parent
oxdata_dir = current_dir.parent
project_root = oxdata_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from infoleap.db_loader import get_db_path

class TestDataIntegration(unittest.TestCase):
    def test_external_source_tables_exist(self):
        """RED: Test that Amazon and Social tables exist in the database."""
        db_path = get_db_path()
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fact_amazon_reviews'")
        self.assertIsNotNone(cur.fetchone(), "Table 'fact_amazon_reviews' missing")
        
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fact_social_sentiment'")
        self.assertIsNotNone(cur.fetchone(), "Table 'fact_social_sentiment' missing")
        
        conn.close()

if __name__ == "__main__":
    unittest.main()
