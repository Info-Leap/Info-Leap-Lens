"""
Golden Regression Test Suite â€” LENS 4.0
=========================================
Tests that the SQL capability templates return correct, known-good values.

Run before every deploy:
    python -m pytest oxdata/tests/test_golden.py -v

Adding new ground truth:
    1. Run a query manually, verify the answer is correct
    2. Add a test case below following the existing pattern
    3. The test will catch any future regression

Design: each test runs the SQL template directly against the real DB,
compares result to known-correct value with a tolerance where appropriate.
No LLM involved â€” pure SQL correctness.
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from infoleap.db_loader import get_db_path
from infoleap.skills.capabilities.nps import get_sql as nps_sql
from infoleap.skills.capabilities.awareness import get_sql as awareness_sql

DB = str(get_db_path())


def _q(sql: str) -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return pd.read_sql(sql, conn)
    finally:
        conn.close()


def _run(sql: str) -> pd.DataFrame:
    return _q(sql.strip())


# â”€â”€â”€ GROUND TRUTH (verified 2026-05-15 against oxdata.db) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

GROUND_TRUTH = {
    "total_respondents":            6631,
    "crompton_nps_national":        64.0,
    "crompton_nps_north":           64.9,
    "crompton_nps_south":           48.6,
    "crompton_aided_awareness_north": 78.2,
    "mixer_penetration_national":   93.3,
}


# â”€â”€â”€ DEMOGRAPHIC TESTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestDemographic:
    def test_total_respondent_count(self):
        df = _run("SELECT COUNT(DISTINCT respondent_id) n FROM v_respondents")
        assert int(df.iloc[0]["n"]) == GROUND_TRUTH["total_respondents"]

    def test_zone_respondents_sum_to_total(self):
        df = _run("SELECT SUM(n) total FROM (SELECT zone_name, COUNT(DISTINCT respondent_id) n FROM v_respondents GROUP BY zone_name)")
        assert int(df.iloc[0]["total"]) == GROUND_TRUTH["total_respondents"]

    def test_four_zones_present(self):
        df = _run("SELECT COUNT(DISTINCT zone_name) n FROM v_respondents")
        assert int(df.iloc[0]["n"]) >= 4  # DB has East/North/West/South

    def test_18_cities_present(self):
        df = _run("SELECT COUNT(DISTINCT city_name) n FROM v_respondents")
        assert int(df.iloc[0]["n"]) == 18


# â”€â”€â”€ NPS TESTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestNPS:
    def test_crompton_nps_national(self):
        """NPS = (promoters - detractors) / total Ã— 100. Known: 64.0"""
        sql = nps_sql("crompton nps")
        df = _run(sql)
        assert not df.empty, "Query returned no rows"
        nps_col = [c for c in df.columns if "nps" in c.lower()][0]
        val = float(df[df["brand_name"].str.lower() == "crompton"].iloc[0][nps_col])
        assert abs(val - GROUND_TRUTH["crompton_nps_national"]) <= 0.5, \
            f"Crompton NPS national: expected ~{GROUND_TRUTH['crompton_nps_national']}, got {val}"

    def test_crompton_nps_north_vs_south_two_rows(self):
        """Zone comparison must return one row per zone, not one total."""
        sql = nps_sql("crompton nps in north vs in south")
        df = _run(sql)
        assert len(df) == 2, f"Expected 2 rows (North+South), got {len(df)}"
        zones = set(df["zone_name"].str.lower())
        assert zones == {"north", "south"}, f"Wrong zones: {zones}"

    def test_crompton_nps_north_value(self):
        sql = nps_sql("crompton nps in north zone")
        df = _run(sql)
        assert not df.empty
        nps_col = [c for c in df.columns if "nps" in c.lower()][0]
        north_row = df[df.get("zone_name", pd.Series(dtype=str)).str.lower() == "north"] if "zone_name" in df.columns else df
        val = float(north_row.iloc[0][nps_col])
        assert abs(val - GROUND_TRUTH["crompton_nps_north"]) <= 0.5, \
            f"Crompton NPS North: expected ~{GROUND_TRUTH['crompton_nps_north']}, got {val}"

    def test_crompton_nps_south_value(self):
        sql = nps_sql("crompton nps in north vs in south")
        df = _run(sql)
        south = df[df["zone_name"].str.lower() == "south"].iloc[0]
        val = float(south["nps_score"])
        assert abs(val - GROUND_TRUTH["crompton_nps_south"]) <= 0.5, \
            f"Crompton NPS South: expected ~{GROUND_TRUTH['crompton_nps_south']}, got {val}"

    def test_nps_all_brands_ranking_has_multiple_brands(self):
        sql = nps_sql("nps rankings for all brands")
        df = _run(sql)
        assert len(df) >= 5, f"Expected â‰¥5 brands in NPS ranking, got {len(df)}"

    def test_nps_scores_in_valid_range(self):
        """NPS must be between -100 and +100."""
        sql = nps_sql("nps rankings for all brands")
        df = _run(sql)
        nps_col = [c for c in df.columns if "nps" in c.lower()][0]
        assert df[nps_col].between(-100, 100).all(), \
            f"NPS values out of range: {df[nps_col].describe()}"


# â”€â”€â”€ AWARENESS TESTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestAwareness:
    def test_crompton_aided_awareness_north_cumulative(self):
        """
        CRITICAL: aided% must be CUMULATIVE (TOM+SPONT+AIDED), not just AIDED stage.
        Known correct: 78.2%. If this returns ~18%, the formula is wrong.
        """
        sql = awareness_sql("crompton awareness in north zone")
        df = _run(sql)
        assert not df.empty
        assert "aided_pct" in df.columns, \
            f"Expected aided_pct column, got: {list(df.columns)}"
        val = float(df.iloc[0]["aided_pct"])
        assert val > 50, \
            f"Aided awareness looks incremental (got {val}%). Must be cumulative TOM+SPONT+AIDED."
        assert abs(val - GROUND_TRUTH["crompton_aided_awareness_north"]) <= 1.0, \
            f"Crompton aided North: expected ~{GROUND_TRUTH['crompton_aided_awareness_north']}, got {val}"

    def test_aided_always_geq_spont(self):
        """Aided (total recognition) must always â‰¥ Spont â‰¥ TOM."""
        sql = awareness_sql("crompton awareness nationally")
        df = _run(sql)
        if "aided_pct" in df.columns and "spont_pct" in df.columns and "tom_pct" in df.columns:
            assert (df["aided_pct"] >= df["spont_pct"]).all(), "aided_pct < spont_pct â€” formula bug"
            assert (df["spont_pct"] >= df["tom_pct"]).all(),   "spont_pct < tom_pct â€” formula bug"

    def test_awareness_zone_comparison_two_zones(self):
        sql = awareness_sql("crompton awareness north vs south")
        df = _run(sql)
        assert "zone_name" in df.columns, "Zone comparison must return zone_name column"
        zones = set(df["zone_name"].str.lower())
        assert {"north", "south"}.issubset(zones), f"Missing zones in comparison: {zones}"

    def test_awareness_pct_in_valid_range(self):
        sql = awareness_sql("all brands awareness nationally")
        df = _run(sql)
        for col in ["aided_pct", "spont_pct", "tom_pct"]:
            if col in df.columns:
                assert df[col].between(0, 100).all(), f"{col} out of 0-100 range"

    def test_tom_single_stage_query(self):
        sql = awareness_sql("top of mind awareness crompton")
        df = _run(sql)
        assert not df.empty
        if "awareness_pct" in df.columns:
            val = float(df.iloc[0]["awareness_pct"])
            assert 0 < val < 100


# â”€â”€â”€ OWNERSHIP TESTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestOwnership:
    def test_mixer_penetration_national(self):
        sql = """
        WITH base AS (SELECT COUNT(DISTINCT respondent_id) total FROM v_respondents)
        SELECT ROUND(COUNT(DISTINCT respondent_id)*100.0/(SELECT total FROM base),1) pct
        FROM v_kitchen_ownership WHERE LOWER(appliance_name) LIKE '%mixer%'
        """
        df = _run(sql)
        val = float(df.iloc[0]["pct"])
        assert abs(val - GROUND_TRUTH["mixer_penetration_national"]) <= 2.0, \
            f"Mixer penetration: expected ~{GROUND_TRUTH['mixer_penetration_national']}, got {val}"

    def test_penetration_between_0_and_100(self):
        df = _run("""
        WITH base AS (SELECT COUNT(DISTINCT respondent_id) total FROM v_respondents)
        SELECT appliance_name, ROUND(COUNT(DISTINCT respondent_id)*100.0/(SELECT total FROM base),1) pct
        FROM v_kitchen_ownership GROUP BY appliance_name
        """)
        assert df["pct"].between(0, 100).all(), "Penetration out of 0-100 range"


# â”€â”€â”€ SCHEMA SANITY TESTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestSchema:
    def test_all_preferred_views_exist(self):
        from infoleap.skills.schema_registry import SchemaRegistry
        reg = SchemaRegistry(DB)
        schema = reg.get_schema()
        for view in SchemaRegistry.PREFERRED_VIEWS:
            assert view in schema, f"Missing view: {view}"

    def test_awareness_stages_are_mutually_exclusive(self):
        """Each respondent should appear in ONE stage per brand (exclude DK/None brands)."""
        df = _run("""
            SELECT respondent_id, brand_name, COUNT(DISTINCT stage) n
            FROM v_brand_awareness
            WHERE brand_name NOT LIKE '%Don%t Know%' AND brand_name != 'None'
            GROUP BY respondent_id, brand_name
            HAVING n > 1
            LIMIT 5
        """)
        assert len(df) == 0, \
            f"Found {len(df)} respondents with multiple stages per brand â€” DB model changed"

    def test_nps_scores_are_0_to_10(self):
        df = _run("SELECT MIN(nps_score) mn, MAX(nps_score) mx FROM v_brand_nps")
        assert float(df.iloc[0]["mn"]) >= 0   # 0-10 scale, 0 is valid
        assert float(df.iloc[0]["mx"]) <= 10


# â”€â”€â”€ EXAMPLE STORE TESTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestExampleStore:
    def test_loads_at_least_30_examples(self):
        from infoleap.skills.example_store import ExampleStore
        store = ExampleStore()
        assert store.count() >= 30, f"Expected â‰¥30 seeded examples, got {store.count()}"

    def test_retrieves_awareness_example_for_awareness_question(self):
        from infoleap.skills.example_store import ExampleStore
        store = ExampleStore()
        results = store.retrieve("crompton awareness in north", k=3)
        assert len(results) > 0
        # Top result should have metric=awareness in metadata
        top = results[0]
        assert top.get("metadata", {}).get("metric") == "awareness", \
            f"Top result for awareness question has metric={top.get('metadata',{}).get('metric')}"

    def test_retrieves_nps_example_for_nps_question(self):
        from infoleap.skills.example_store import ExampleStore
        store = ExampleStore()
        results = store.retrieve("crompton nps score", k=3)
        assert len(results) > 0
        top_metrics = [r.get("metadata", {}).get("metric") for r in results[:2]]
        assert "nps" in top_metrics, f"NPS question top results: {top_metrics}"
