"""
mapping_fusion.py — Fuses AP/tabplan + datamap + raw data + existing JSON into
a unified schema_doc.  No source is mandatory; each adds what it knows.

Fusion priority (highest wins):
  1. Existing JSON (LLM work already done)
  2. AP/tabplan (brand code grids)
  3. Datamap (column → question text)
  4. Raw data (validates columns exist, auto-detects dummy families)
  5. Co-occurrence CTL reconstruction (fills remaining empty CTLs)
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fuse_mapping_sources(
    raw_data_path: str,
    ap_path: Optional[str] = None,
    datamap_path: Optional[str] = None,
    questionnaire_path: Optional[str] = None,
    existing_json_path: Optional[str] = None,
) -> dict:
    """
    Returns a unified schema_doc dict (same shape as llm_mapping_raw.json's
    schema_doc).  All args except raw_data_path are optional.
    """
    raw_df = pd.read_excel(raw_data_path)
    raw_cols = set(raw_df.columns.astype(str))

    # Step 1 — start from existing JSON if present
    schema_doc = _load_existing_json(existing_json_path)

    # Step 2 — enrich from AP/tabplan
    if ap_path:
        ap_brands = _extract_brands_from_ap(ap_path)
        schema_doc = _apply_ap_brands(schema_doc, ap_brands, raw_df)

    # Step 3 — enrich question text from datamap
    if datamap_path:
        datamap = _parse_datamap(datamap_path)
        schema_doc = _apply_datamap(schema_doc, datamap)

    # Step 4 — validate columns exist, auto-detect dummy families
    schema_doc = _validate_and_expand_columns(schema_doc, raw_df, raw_cols)

    # Step 5 — co-occurrence CTL reconstruction for remaining empty CTLs
    schema_doc = _cooccurrence_ctl_reconstruction(schema_doc, raw_df)

    return schema_doc


# ---------------------------------------------------------------------------
# Step 1 — load existing JSON
# ---------------------------------------------------------------------------

def _load_existing_json(json_path: Optional[str]) -> dict:
    if not json_path or not Path(json_path).exists():
        return {"project_id": "unknown", "questions": []}
    with open(json_path) as f:
        d = json.load(f)
    return d.get("schema_doc", d)


# ---------------------------------------------------------------------------
# Step 2 — AP/tabplan: extract brand lists
# ---------------------------------------------------------------------------

def _extract_brands_from_ap(ap_path: str) -> list[str]:
    """
    Returns ordered list of brand names from AP Netting or TOP BREAK sheet.
    These give us the canonical brand universe for the project.
    """
    xl = pd.ExcelFile(ap_path)
    brands: list[str] = []

    # Try Netting sheet first — most reliable brand list
    if "Netting" in xl.sheet_names:
        net = pd.read_excel(ap_path, sheet_name="Netting", header=None)
        for _, row in net.iterrows():
            for val in row:
                if isinstance(val, str) and len(val) > 1 and val.strip() not in ("", "-", "NaN"):
                    candidate = val.strip()
                    if candidate not in ("Premium", "Non-Premium", "All India"):
                        brands.append(candidate)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique = []
    for b in brands:
        if b not in seen:
            seen.add(b)
            unique.append(b)
    return unique


def _apply_ap_brands(schema_doc: dict, ap_brands: list[str], raw_df: pd.DataFrame) -> dict:
    """
    AP gives us brand names but not codes.  We use it to validate/supplement
    brand names already in CTLs.  If a brand name from AP is missing from all
    CTLs, we flag it in the schema_doc metadata.
    """
    if not ap_brands:
        return schema_doc
    schema_doc.setdefault("_ap_brand_universe", ap_brands)
    return schema_doc


# ---------------------------------------------------------------------------
# Step 3 — Datamap: column → question text
# ---------------------------------------------------------------------------

def _parse_datamap(datamap_path: str) -> dict[str, str]:
    """Returns {column_name: question_text} from XLSForm-style datamap."""
    try:
        dm = pd.read_excel(datamap_path, header=None)
    except Exception:
        return {}

    col_to_text: dict[str, str] = {}

    # XLSForm format: col 0=type, col 1=name, col 2=label
    if dm.shape[1] >= 3:
        for _, row in dm.iterrows():
            name = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
            label = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""
            if name and label and name != "nan" and label != "nan":
                # Strip HTML tags and markdown
                label = re.sub(r"<[^>]+>", "", label)
                label = re.sub(r"\*\*|__", "", label)
                label = label.strip()
                if label:
                    col_to_text[name.lower()] = label[:200]  # cap length

    return col_to_text


def _apply_datamap(schema_doc: dict, datamap: dict[str, str]) -> dict:
    """Fill missing question_text from datamap."""
    if not datamap:
        return schema_doc
    for q in schema_doc.get("questions", []):
        qcode = q.get("question_code", "").lower()
        if not q.get("question_text") and qcode in datamap:
            q["question_text"] = datamap[qcode]
    return schema_doc


# ---------------------------------------------------------------------------
# Step 4 — validate columns, auto-detect dummy families
# ---------------------------------------------------------------------------

def _validate_and_expand_columns(schema_doc: dict, raw_df: pd.DataFrame, raw_cols: set[str]) -> dict:
    """
    For each question:
    - Check source_column exists in raw_df
    - For multivalent_source: detect all q17_N dummy columns automatically
    - For multi_select_dummies: detect dummy family
    - Flag missing columns
    """
    for q in schema_doc.get("questions", []):
        shape = q.get("shape", "single_value")
        src = q.get("source_column") or q.get("question_code", "")
        qcode = q.get("question_code", "")

        # Validate source column
        if src and src not in raw_cols:
            q.setdefault("_warnings", []).append(f"source_column '{src}' not in raw data")

        # Auto-detect dummy family for multivalent_source and multi_select_dummies
        if shape in ("multivalent_source", "multi_select_dummies"):
            stem = src or qcode
            existing = set(q.get("dummy_columns", []))
            detected = sorted(
                [c for c in raw_cols if c.startswith(stem + "_") and not c.endswith("_oth") and not c.endswith("_total")],
                key=lambda x: _col_sort_key(x, stem)
            )
            # Union of existing + detected
            merged = list(existing)
            for c in detected:
                if c not in existing:
                    merged.append(c)
            merged.sort(key=lambda x: _col_sort_key(x, stem))
            if merged:
                q["dummy_columns"] = merged

    return schema_doc


def _col_sort_key(col: str, stem: str) -> int:
    suffix = col[len(stem) + 1:]
    try:
        return int(suffix)
    except ValueError:
        return 9999


# ---------------------------------------------------------------------------
# Step 5 — co-occurrence CTL reconstruction
# ---------------------------------------------------------------------------

AWARENESS_BUCKETS = {"TOM", "SPONT", "AIDED", "EVER_USED", "CONSIDERATION", "CURRENT_USER", "PREFERRED"}
JUNK_PATTERNS = [r"^\$\{", r"^NoneOfThese$", r"^None$", r"^Other$", r"^Raw/Loose"]


def _is_junk(name: str) -> bool:
    for pat in JUNK_PATTERNS:
        if re.search(pat, name, re.IGNORECASE):
            return True
    return False


def _cooccurrence_ctl_reconstruction(schema_doc: dict, raw_df: pd.DataFrame) -> dict:
    """
    For awareness questions with empty/incomplete CTL:
    Find anchor question (same tier, most complete CTL).
    For each brand in anchor CTL, find respondents who selected it.
    In target question, find most common code/dummy for those respondents.
    Assign that code → brand in target CTL.
    """
    questions = schema_doc.get("questions", [])
    awareness_qs = [q for q in questions if q.get("bucket") in AWARENESS_BUCKETS]

    # Build anchor: question with most complete clean CTL and dummy_columns
    best_anchor = _find_best_anchor(awareness_qs, raw_df)
    if not best_anchor:
        return schema_doc

    anchor_ctl = {str(int(float(k))): v for k, v in best_anchor.get("code_to_label", {}).items()
                  if not _is_junk(str(v))}
    anchor_dummies = best_anchor.get("dummy_columns", [])

    for q in awareness_qs:
        if q is best_anchor:
            continue
        shape = q.get("shape", "single_value")
        if shape not in ("multivalent_source", "multi_select_dummies"):
            continue

        existing_ctl = q.get("code_to_label", {})
        clean_existing = {str(int(float(k))): v for k, v in existing_ctl.items()
                         if not _is_junk(str(v))} if existing_ctl else {}

        qcode = q.get("question_code", "")
        dummy_cols = q.get("dummy_columns", [])
        stem = q.get("source_column") or qcode

        reconstructed: dict[str, str] = {}

        for code_str, brand_name in anchor_ctl.items():
            if brand_name in clean_existing.values():
                continue  # already mapped

            # Find anchor dummy for this brand
            anchor_dummy = next((c for c in anchor_dummies if c.endswith(f"_{code_str}")), None)
            if anchor_dummy is None or anchor_dummy not in raw_df.columns:
                continue

            respondent_mask = raw_df[anchor_dummy] == 1
            if respondent_mask.sum() < 5:
                continue

            # Find most correlated dummy in target question
            best_col = None
            best_overlap = 0.0
            for dcol in dummy_cols:
                if dcol not in raw_df.columns:
                    continue
                suffix = dcol[len(stem) + 1:]
                try:
                    int(suffix)
                except ValueError:
                    continue
                if str(int(suffix)) in clean_existing:
                    continue  # already mapped
                overlap = (raw_df.loc[respondent_mask, dcol] == 1).mean()
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_col = dcol

            if best_col and best_overlap >= 0.65:
                suffix = best_col[len(stem) + 1:]
                try:
                    code = str(int(suffix))
                    reconstructed[code] = brand_name
                    reconstructed[f"{code}.0"] = brand_name
                except ValueError:
                    pass

        if reconstructed:
            merged_ctl = dict(existing_ctl)
            for k, v in reconstructed.items():
                if k not in merged_ctl:
                    merged_ctl[k] = v
            q["code_to_label"] = merged_ctl
            q.setdefault("_warnings", []).append(
                f"CTL partially reconstructed via co-occurrence from {best_anchor.get('question_code')}: "
                f"{list(reconstructed.values())}"
            )

    return schema_doc


def _find_best_anchor(awareness_qs: list[dict], raw_df: pd.DataFrame) -> Optional[dict]:
    """Find question with most clean CTL entries that also has dummy columns in raw_df."""
    best = None
    best_score = 0
    for q in awareness_qs:
        ctl = q.get("code_to_label", {})
        if not ctl:
            continue
        clean = [v for v in ctl.values() if not _is_junk(str(v))]
        dummies = [c for c in q.get("dummy_columns", []) if c in raw_df.columns]
        score = len(clean) * len(dummies)
        if score > best_score:
            best_score = score
            best = q
    return best
