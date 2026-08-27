"""
Privacy test suite  MUST pass 100%.
"""

import pytest
from ingestion.pseudonymise import (
    mask_name, pseudonymise_text, verify_no_pii
)

KNOWN_NAMES = ["Anvi", "Shilpa", "Sujata", "Rahul", "Priya"]

def test_mask_name_deterministic():
    m1 = mask_name("Anvi")
    m2 = mask_name("Anvi")
    assert m1 == m2

def test_mask_name_different_names():
    m1 = mask_name("Anvi")
    m2 = mask_name("Shilpa")
    assert m1 != m2

def test_mask_name_format():
    m = mask_name("TestName")
    assert m.startswith("R_")
    assert len(m) == 10

def test_phone_redaction():
    text = "Call me at 9876543210 or +91-98765-43210"
    clean = pseudonymise_text(text)
    assert "9876543210" not in clean
    assert "[PHONE_REDACTED]" in clean

def test_name_redaction():
    text = "Anvi said she uses her mixer daily. Shilpa agreed."
    clean = pseudonymise_text(text, known_names=["Anvi", "Shilpa"])
    is_clean, violations = verify_no_pii(clean, ["Anvi", "Shilpa"])
    assert is_clean, f"PII found after masking: {violations}"

def test_pin_redaction():
    text = "She lives in Mumbai 400001"
    clean = pseudonymise_text(text)
    assert "400001" not in clean

def test_email_redaction():
    text = "Contact anvi.sharma@gmail.com for details"
    clean = pseudonymise_text(text)
    assert "@gmail.com" not in clean

def test_verify_no_pii_catches_violation():
    text = "Anvi said the mixer is noisy"
    is_clean, violations = verify_no_pii(text, ["Anvi"])
    assert not is_clean
    assert len(violations) > 0

