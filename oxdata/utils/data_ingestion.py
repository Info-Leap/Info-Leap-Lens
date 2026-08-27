import sqlite3
import pandas as pd
import os
import sys
from pathlib import Path

# Ensure project root is in path
current_dir = Path(__file__).resolve().parent
oxdata_dir = current_dir.parent
project_root = oxdata_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from oxdata.db_loader import get_db_path

class DataIngestionEngine:
    def __init__(self):
        self.db_path = get_db_path()

    def bootstrap_external_sources(self):
        """Creates and populates Amazon and Social tables with simulated data."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        # 1. Amazon Reviews Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fact_amazon_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_name TEXT,
                city_name TEXT,
                rating REAL,
                review_text TEXT,
                review_date DATE,
                is_flagged BOOLEAN DEFAULT 0
            )
        """)
        
        # 2. Social Sentiment Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fact_social_sentiment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT, -- Twitter, YouTube, Flipkart
                brand_name TEXT,
                sentiment_score REAL, -- -1 to 1
                volume INTEGER,
                log_date DATE
            )
        """)
        
        # 3. Seed Data (Simulating the 'Marketing Pulse' video findings)
        amazon_data = [
            ("Preethi", "Kolkata", 4.7, "Best mixer grinder, very good quality. recommended.", "2024-04-15", 1),
            ("Preethi", "Kolkata", 4.8, "Very nice product. Working fine.", "2024-04-16", 1),
            ("Preethi", "Kolkata", 4.7, "Good quality mixer. Satisfied.", "2024-04-17", 1),
            ("Bajaj", "Mumbai", 4.2, "Decent product for the price.", "2024-04-10", 0)
        ]
        cur.executemany("INSERT INTO fact_amazon_reviews (brand_name, city_name, rating, review_text, review_date, is_flagged) VALUES (?, ?, ?, ?, ?, ?)", amazon_data)
        
        social_data = [
            ("Twitter", "Preethi", -0.4, 36800000, "2024-04-20"),
            ("YouTube", "Bajaj", 0.6, 1800000000, "2024-04-20"),
            ("Flipkart", "Philips", 0.2, 50000, "2024-04-20")
        ]
        cur.executemany("INSERT INTO fact_social_sentiment (source, brand_name, sentiment_score, volume, log_date) VALUES (?, ?, ?, ?, ?)", social_data)
        
        conn.commit()
        conn.close()
        return True

if __name__ == "__main__":
    engine = DataIngestionEngine()
    engine.bootstrap_external_sources()
    print("✅ External sources bootstrapped.")
