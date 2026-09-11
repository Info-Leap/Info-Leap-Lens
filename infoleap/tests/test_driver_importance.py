import pandas as pd
import sqlite3
import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.getcwd())

from infoleap.analytics.driver_analysis_engine import DriverAnalysisEngine

def test_regression_logic():
    print("--- TESTING DRIVER IMPORTANCE (REGRESSION) ---")
    # Use real data from infoleap.db
    db_path = "oxdata/data/project_1/oxdata.db"
    
    # Selected drivers for Mixer Grinder
    drivers = [78, 79, 80, 85, 87] # Quality, Easy use, Innovation, Value, Affordable
    
    # Mixer Grinder category codes
    cats = [4]
    
    engine = DriverAnalysisEngine(db_path=db_path, category_codes=cats)
    
    # Run the engine
    # We want to see if nps_impacts are computed
    res = engine.run(
        driver_ids=drivers,
        brands=["Bajaj", "Crompton", "Havells", "Philips"],
        compare_by="overall"
    )
    
    if res["status"] == "ok":
        impacts = res["nps_impacts"]
        print("\nDerived NPS Impacts (Coefficients):")
        print(impacts.sort_values(ascending=False).to_string())
        
        # Validation: Coefficients should not all be zero if there's enough data
        if impacts.abs().sum() > 0:
            print("\nSUCCESS: Non-zero impacts derived.")
        else:
            print("\nWARNING: All impacts are zero. Check sample size or data availability.")
            
        # Check summary table
        print("\nSummary Table (Top 2 Brands):")
        print(res["summary_table"].head(2).to_string())
    else:
        print(f"FAILED: {res.get('message', 'Unknown error')}")

def test_demographic_filtering():
    print("\n--- TESTING DEMOGRAPHIC FILTERING ---")
    db_path = "oxdata/data/project_1/oxdata.db"
    drivers = [78, 79]
    cats = [4]
    
    # Test North Zone
    print("Running for North zone...")
    engine_n = DriverAnalysisEngine(db_path=db_path, category_codes=cats, zone="North")
    res_n = engine_n.run(driver_ids=drivers)
    print(f"North Brands: {len(res_n.get('summary_table', []))}")
    
    # Test South Zone
    print("Running for South zone...")
    engine_s = DriverAnalysisEngine(db_path=db_path, category_codes=cats, zone="South")
    res_s = engine_s.run(driver_ids=drivers)
    print(f"South Brands: {len(res_s.get('summary_table', []))}")
    
    if len(res_n.get('summary_table', [])) != len(res_s.get('summary_table', [])):
         print("SUCCESS: Demographics produced different brand sets.")
    else:
         print("NOTE: Demographics produced same brand counts (could be normal for small sets).")

if __name__ == "__main__":
    test_regression_logic()
    test_demographic_filtering()
