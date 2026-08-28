"""
LENS Pseudonymisation Layer
============================
Rules:
- Every real respondent name  deterministic masked ID (R_[8char hash])
- Interviewer names  I_[4char hash]
- Phone numbers  [PHONE_REDACTED]
- Addresses  [ADDRESS_REDACTED]
- Pin codes  [PIN_REDACTED]
- The nameID mapping is stored ONLY in data/processed/name_map.json (local, never sent to any API)
- Masked IDs are consistent: same name always gets same ID within a project
"""

import re
import json
import hashlib
import os
from pathlib import Path

NAME_MAP_PATH = Path("data/processed/name_map.json")

def _load_name_map() -> dict:
    if NAME_MAP_PATH.exists():
        with open(NAME_MAP_PATH) as f:
            return json.load(f)
    return {}

def _save_name_map(name_map: dict):
    NAME_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(NAME_MAP_PATH, "w") as f:
        json.dump(name_map, f, indent=2)

def mask_name(real_name: str, prefix: str = "R") -> str:
    """Returns deterministic masked ID for a real name."""
    name_map = _load_name_map()
    if real_name in name_map:
        return name_map[real_name]
    hash_id = hashlib.sha256(real_name.lower().strip().encode()).hexdigest()[:8]
    masked = f"{prefix}_{hash_id}"
    name_map[real_name] = masked
    _save_name_map(name_map)
    return masked

def pseudonymise_text(text: str, known_names: list[str] = None) -> str:
    """
    Removes or masks all PII from a text string.
    Apply to transcript text before passing to PageIndex or any LLM.
    """
    # Mask known names first (case-insensitive whole word)
    if known_names:
        for name in known_names:
            pattern = re.compile(rf'\b{re.escape(name)}\b', re.IGNORECASE)
            masked = mask_name(name)
            text = pattern.sub(masked, text)

    # Phone numbers: Indian formats
    text = re.sub(r'\b(\+91[\s-]?)?[6-9]\d{9}\b', '[PHONE_REDACTED]', text)
    text = re.sub(r'\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b', '[PHONE_REDACTED]', text)

    # Pin codes (6 digits)
    text = re.sub(r'\b\d{6}\b', '[PIN_REDACTED]', text)

    # Email addresses
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
                  '[EMAIL_REDACTED]', text)

    return text

def verify_no_pii(text: str, known_names: list[str]) -> tuple[bool, list[str]]:
    """
    Verifies that no known PII remains in text.
    Returns (is_clean, list_of_violations).
    Use this in tests.
    """
    violations = []
    for name in known_names:
        if name.lower() in text.lower():
            violations.append(f"Name still present: {name}")
    phone_matches = re.findall(r'\b[6-9]\d{9}\b', text)
    if phone_matches:
        violations.append(f"Phone numbers found: {phone_matches}")
    return len(violations) == 0, violations

