import pytest
import sqlite3
import pandas as pd
import sys
import os
sys.path.append(os.getcwd())
from analytics.brand_imagery_engine import BrandImageryEngine

DB_PATH = "lens.db"

def test_brand_health_global():
    engine = BrandImageryEngine(DB_PATH)
    result = engine.get_brand_health(category="all")
    
    assert result["status"] == "success"
    assert result["base_n"] == 6631
    
    # Verify Bajaj (usually top brand)
    bajaj = next((b for b in result["brands"] if b["brand_name"] == "Bajaj"), None)
    assert bajaj is not None
    assert bajaj["tom"] == 1746
    # Spont should be >= TOM
    assert bajaj["spont"] >= bajaj["tom"]
    # Aided should be >= Spont
    assert bajaj["aided"] >= bajaj["spont"]

def test_brand_health_filtered():
    engine = BrandImageryEngine(DB_PATH)
    # Filter by North zone
    result = engine.get_brand_health(zone="North")
    assert result["status"] == "success"
    assert result["base_n"] < 6631
    assert result["base_n"] > 1000 # North has 1664 in reference

def test_insufficient_data():
    engine = BrandImageryEngine(DB_PATH)
    # Filter for a non-existent city or very recent timeframe
    result = engine.get_brand_health(city="NonExistentCity")
    assert result["status"] == "insufficient_data"
    assert result["base_n"] == 0

def test_cumulative_logic():
    engine = BrandImageryEngine(DB_PATH)
    result = engine.get_brand_health()
    for brand in result["brands"]:
        # Spont must contain TOM respondents (count-wise)
        assert brand["spont"] >= brand["tom"], f"Brand {brand['brand_name']} spont < tom"
        # Aided must contain Spont respondents
        assert brand["aided"] >= brand["spont"], f"Brand {brand['brand_name']} aided < spont"
