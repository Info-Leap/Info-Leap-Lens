import os
import sqlite3
from lens.analytics.brand_imagery_engine import BrandImageryEngine

def test_engine_uses_correct_db():
    engine = BrandImageryEngine()
    # It should look for oxdata.db in the correct path or use db_loader
    # Since we will change it to use db_loader, let's just check the returned connection
    conn = engine._get_conn()
    # verify the DB has fact_findings_ledger (which only oxdata.db has, not lens.db usually, or we can check the path)
    
    # Simpler test: check the path the engine resolves to
    assert "oxdata.db" in str(engine.db_path) or engine.db_path is None, f"Expected oxdata.db, got {engine.db_path}"
    if conn:
        conn.close()
