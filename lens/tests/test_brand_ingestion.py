import sqlite3
import pytest
from pathlib import Path

DB_PATH = "lens/lens.db"

def test_choices_populated():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM choices WHERE variable_code = 'bq1a'")
    count = cursor.fetchone()[0]
    conn.close()
    assert count >= 55, "Choices table for bq1a should have at least 55 brands."

def test_responses_counts():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Total respondents is 6631
    for var in ['bq1a', 'bq1b', 'bq1c', 'bq1d', 'bq1e', 'bq1f']:
        cursor.execute("SELECT COUNT(*) FROM responses WHERE variable_code = ?", (var,))
        count = cursor.fetchone()[0]
        # Some respondents might not have answered all (missing in raw), 
        # but for wave 1 awareness, most should be there.
        assert count > 6600, f"Response count for {var} should be close to 6631, found {count}"
    conn.close()

def test_bq1b_normalization():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Check that bq1b (multi-select) has value_rank1 set to first mention
    cursor.execute("SELECT value_text, value_rank1 FROM responses WHERE variable_code = 'bq1b' AND value_text IS NOT NULL LIMIT 50")
    rows = cursor.fetchall()
    assert len(rows) > 0
    for val_text, val_rank1 in rows:
        if val_text and str(val_text).strip():
            first_code = int(str(val_text).split()[0])
            assert int(val_rank1) == first_code, f"Rank1 mismatch: text={val_text} vs rank1={val_rank1}"
    conn.close()

def test_respondent_integrity():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM respondents")
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 6631, f"Total respondents should be 6631, found {count}"
