"""
Test data loader for validation against Excel reference files.
Loads raw association % matrices from oxdata/data/test_reference/*.json
and provides them as DataFrames compatible with BIP engine and CAN MAP engine.
"""

import json
import os
import pandas as pd
from pathlib import Path

_REF_DIR = Path(__file__).parent.parent / "data" / "test_reference"


def list_test_datasets() -> list[dict]:
    """Return metadata for all available test datasets."""
    results = []
    if not _REF_DIR.exists():
        return results
    for f in _REF_DIR.glob("*.json"):
        try:
            with open(f) as fh:
                d = json.load(fh)
            results.append({
                "key": f.stem,
                "label": d.get("dataset", f.stem),
                "source": d.get("source", ""),
                "brands": d.get("brands", []),
                "attributes": d.get("attributes", []),
                "file": str(f),
            })
        except Exception:
            pass
    return results


def load_test_matrix(key: str) -> pd.DataFrame:
    """
    Load a test reference dataset and return as brands × attributes DataFrame.
    Values are raw % association.

    Parameters
    ----------
    key : str  — stem of the JSON file (e.g. 'normalisation_raw')

    Returns
    -------
    pd.DataFrame  index=brand_names, columns=attribute_labels
    """
    fpath = _REF_DIR / f"{key}.json"
    if not fpath.exists():
        return pd.DataFrame()

    with open(fpath) as f:
        d = json.load(f)

    brands = d.get("brands", [])
    attrs  = d.get("attributes", [])
    raw    = d.get("raw_association_pct", {})

    if not brands or not attrs or not raw:
        return pd.DataFrame()

    rows = {}
    for b in brands:
        vals = raw.get(b, [])
        if len(vals) == len(attrs):
            rows[b] = vals

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows, index=attrs).T  # brands × attrs
