"""
data_layer.py — compute survey metrics from raw Excel + schema_doc (llm_mapping_raw.json).

Replaces ad-hoc SQL queries in brand_health.py for new projects. Each compute_*() function
takes (raw_df, schema_doc) and returns a clean DataFrame matching what the analytics engines
and brand_health.py sections expect.

SQLite path still used for project_1 (existing data, no raw Excel stored).
This path activates when oxdata/data/{project_id}/raw_data.xlsx exists.

IMPORTANT — AIDED semantics (cumulative vs exclusive):
  data_layer computes AIDED as CUMULATIVE — a respondent who mentioned a brand spontaneously
  (TOM/SPONT) is ALSO counted in AIDED, because aided awareness means "knows the brand".
  This means TOM% ≤ SPONT% ≤ AIDED% always holds.

  SQLite ingestion (generic_loader.py) uses EXCLUSIVE stages — each respondent×brand pair
  is stored in only ONE stage (the deepest reached). So SQLite AIDED = only incremental
  recognizers who did NOT already appear in SPONT/TOM. Same raw data → lower AIDED% in SQLite.

  This divergence is by design. Do not try to reconcile the numbers — they answer different
  questions. data_layer AIDED = "total brand awareness"; SQLite AIDED = "incremental lift".
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Brand name junk filter
# ---------------------------------------------------------------------------

# Brand names that are metadata artifacts, not real brands.
# Applied as a post-filter in all compute functions that produce brand_name columns.
_JUNK_BRAND_PATTERNS = [
    r"^\$\{.*\}$",          # ODK variable placeholders like ${q17_oth}
]
_JUNK_BRAND_EXACT = {
    "noneofthese", "none of these", "none of the above", "nota",
    "other", "others", "any other", "other brands",
    "don't know", "dont know", "dk", "not applicable", "na", "n/a",
    "refused", "can't say", "cant say", "not sure", "no brand",
    "provilac",  # known test-row artifact in AK dataset
}

def _is_junk_brand(name: str) -> bool:
    """Return True if the brand name is a metadata artifact, not a real brand."""
    if not name:
        return True
    nl = name.strip().lower()
    if nl in _JUNK_BRAND_EXACT:
        return True
    for pat in _JUNK_BRAND_PATTERNS:
        if re.match(pat, name.strip()):
            return True
    return False


def _filter_junk_brands(df: pd.DataFrame, col: str = "brand_name") -> pd.DataFrame:
    """Drop rows where brand_name column is a junk value."""
    if df.empty or col not in df.columns:
        return df
    return df[~df[col].apply(_is_junk_brand)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent / "data"
_ROOT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def get_raw_data_path(project_id: str) -> Optional[Path]:
    """Return path to raw_data.xlsx if it exists, else None.
    Also checks project_meta.json for a 'raw_data_path' override.
    """
    # Check project_meta.json override first
    for base in (_DATA_DIR, _ROOT_DATA_DIR):
        meta_p = base / project_id / "project_meta.json"
        if meta_p.exists():
            try:
                import json
                with open(meta_p, encoding="utf-8") as _f:
                    _meta = json.load(_f)
                override = _meta.get("raw_data_path")
                if override:
                    op = Path(override)
                    if op.exists():
                        return op
            except Exception:
                pass

    candidates = [
        _DATA_DIR / project_id / "raw_data.xlsx",
        _DATA_DIR / project_id / "raw_data.csv",
        _DATA_DIR / project_id / "master_mapping.xlsx",
        _ROOT_DATA_DIR / project_id / "raw_data.xlsx",
        _ROOT_DATA_DIR / project_id / "raw_data.csv",
        _ROOT_DATA_DIR / project_id / "master_mapping.xlsx",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def get_master_excel_path(project_id: str) -> Optional[Path]:
    """Return path to master_mapping.xlsx if it exists (preferred schema source)."""
    for base in (_DATA_DIR, _ROOT_DATA_DIR):
        p = base / project_id / "master_mapping.xlsx"
        if p.exists():
            return p
    return None


def get_schema_doc_path(project_id: str) -> Optional[Path]:
    """Return path to llm_mapping_raw.json fallback."""
    for base in (_DATA_DIR, _ROOT_DATA_DIR):
        p = base / project_id / "llm_mapping_raw.json"
        if p.exists():
            return p
    return None


def project_has_raw_layer(project_id: str) -> bool:
    """True if both raw_data + some schema source exist for this project.
    Old-format master_mapping.xlsx (no RAW_DATA sheet) = False; those projects use SQLite path."""
    has_schema = get_master_excel_path(project_id) is not None or get_schema_doc_path(project_id) is not None
    raw_path = get_raw_data_path(project_id)
    if raw_path is None or not has_schema:
        return False
    if str(raw_path).endswith("master_mapping.xlsx"):
        try:
            xl = pd.ExcelFile(raw_path)
            if "RAW_DATA" not in xl.sheet_names:
                return False
        except Exception:
            return False
    return True


# ---------------------------------------------------------------------------
# Loaders (cached in-process)
# ---------------------------------------------------------------------------

_raw_cache: dict[str, pd.DataFrame] = {}
_schema_cache: dict[str, dict] = {}
_schema_mtime: dict[str, float] = {}  # tracks file mtime for cache invalidation

# Streamlit cache wrapper — import conditionally so data_layer works outside Streamlit too
try:
    import streamlit as st
    _st_available = True
except ImportError:
    _st_available = False


def load_raw_df(project_id: str) -> pd.DataFrame:
    """Load raw survey file, one row per respondent. Cached per project per process."""
    if project_id in _raw_cache:
        return _raw_cache[project_id]
    p = get_raw_data_path(project_id)
    if p is None:
        raise FileNotFoundError(f"No raw_data file found for project '{project_id}'")
    if str(p).endswith(".csv"):
        df = pd.read_csv(p, low_memory=False)
    elif str(p).endswith("master_mapping.xlsx"):
        _xl = pd.ExcelFile(p)
        if "RAW_DATA" not in _xl.sheet_names:
            raise ValueError(
                f"master_mapping.xlsx for '{project_id}' is old format (no RAW_DATA sheet). "
                "Use SQLite path or migrate to new 6-sheet format."
            )
        df = pd.read_excel(_xl, sheet_name="RAW_DATA")
    else:
        df = pd.read_excel(p)
    df.columns = [str(c).strip() for c in df.columns]
    _raw_cache[project_id] = df
    return df


def load_schema_doc(project_id: str) -> dict:
    """Load schema_doc. Priority: master_mapping.xlsx > llm_mapping_raw.json.
    Cached per project; auto-invalidates if source file changes on disk.
    """
    # Prefer master Excel if present
    excel_p = get_master_excel_path(project_id)
    json_p = get_schema_doc_path(project_id)

    if excel_p is not None:
        current_mtime = os.path.getmtime(excel_p)
        if project_id in _schema_cache and _schema_mtime.get(project_id) == current_mtime:
            return _schema_cache[project_id]
        from infoleap.ingestion.master_excel import read_master_excel
        doc = read_master_excel(str(excel_p))
        _schema_cache[project_id] = doc
        _schema_mtime[project_id] = current_mtime
        return doc

    # Fallback: llm_mapping_raw.json
    if json_p is None:
        raise FileNotFoundError(
            f"No schema source found for project '{project_id}'. "
            "Expected master_mapping.xlsx or llm_mapping_raw.json in oxdata/data/{project_id}/"
        )
    current_mtime = os.path.getmtime(json_p)
    if project_id in _schema_cache and _schema_mtime.get(project_id) == current_mtime:
        return _schema_cache[project_id]
    with open(json_p, encoding="utf-8") as f:
        raw = json.load(f)
    doc = raw.get("schema_doc", raw)
    _schema_cache[project_id] = doc
    _schema_mtime[project_id] = current_mtime
    return doc


def clear_project_cache(project_id: str) -> None:
    _raw_cache.pop(project_id, None)
    _schema_cache.pop(project_id, None)
    _schema_mtime.pop(project_id, None)


# ---------------------------------------------------------------------------
# Schema introspection helpers
# ---------------------------------------------------------------------------

def get_questions_for_bucket(schema_doc: dict, bucket: str) -> list[dict]:
    return [q for q in schema_doc.get("questions", []) if q.get("bucket") == bucket]


def extract_brand_index_map(schema_doc: dict) -> dict[int, str]:
    """
    Build {brand_integer_index: brand_name} from funnel questions' code_to_label.

    Funnel questions (CONSIDERATION/EVER_USED/CURRENT_USER/PREFERRED/AIDED/SPONT) tend to have
    the fullest code_to_label covering all tracked brands. The richest one wins.
    brand_integer_index corresponds to the suffix in multi-brand columns: q34_1_1 = brand 1.
    """
    PRIORITY = ["CONSIDERATION", "EVER_USED", "CURRENT_USER", "PREFERRED",
                "AIDED", "SPONT", "TOM", "NPS", "CSAT"]
    best: dict[int, str] = {}
    for bucket in PRIORITY:
        for q in get_questions_for_bucket(schema_doc, bucket):
            ctl = q.get("code_to_label") or {}
            if not ctl:
                continue
            parsed = {}
            for k, v in ctl.items():
                try:
                    brand = str(v).strip()
                    if not _is_junk_brand(brand):
                        parsed[int(float(k))] = brand
                except (ValueError, TypeError):
                    pass
            # Compare only clean (non-junk) entries — a question with 15 entries
            # where 6 are junk should not beat one with 12 real brands
            if len(parsed) > len(best):
                best = parsed
    return best


def _stem_and_brand(source_column: str) -> tuple[str, int]:
    """
    Parse 'q34_1_3' → stem='q34_1', brand_idx=3.
    Parse 'q65_2'   → stem='q65',   brand_idx=2.
    Returns ('', 0) if no trailing integer suffix found.
    """
    m = re.match(r"^(.+?)_(\d+)$", source_column)
    if m:
        return m.group(1), int(m.group(2))
    return source_column, 0


def _infer_brand_columns(stem: str, brand_idx_map: dict[int, str], raw_cols: set[str]) -> dict[int, str]:
    """
    Given stem 'q34_1' and brand_idx_map {1: 'AK', 2: 'CD', ...},
    return {brand_idx: col_name} for columns that actually exist in raw_cols.
    """
    out = {}
    for idx in brand_idx_map:
        col = f"{stem}_{idx}"
        if col in raw_cols:
            out[idx] = col
    return out


# ---------------------------------------------------------------------------
# Respondent base
# ---------------------------------------------------------------------------

def compute_respondents(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns DataFrame: respondent_id, gender, age, age_band, city_name, zone_name.
    respondent_id = raw_df index (0-based row number) if no explicit ID column found.
    """
    raw_cols = set(raw_df.columns)
    out = pd.DataFrame(index=raw_df.index)

    # respondent_id: first col that looks like an ID
    id_col = None
    for c in ["respondent_id", "resp_id", "id", "serial", "s_no", "serial_no"]:
        if c in raw_cols:
            id_col = c
            break
    out["respondent_id"] = raw_df[id_col] if id_col else raw_df.index + 1

    # GENDER
    for q in get_questions_for_bucket(schema_doc, "GENDER"):
        col = q.get("source_column", "")
        if col in raw_cols:
            ctl = q.get("code_to_label") or {}
            raw_vals = raw_df[col].astype(str)
            if ctl:
                ctl_str = {str(int(float(k))) if _is_numeric(k) else str(k): v for k, v in ctl.items()}
                out["gender"] = raw_vals.map(ctl_str).fillna(raw_vals)
            else:
                out["gender"] = raw_vals
            break

    # AGE
    for q in get_questions_for_bucket(schema_doc, "AGE"):
        col = q.get("source_column", "")
        if col in raw_cols:
            ctl = q.get("code_to_label") or {}
            raw_vals = raw_df[col]
            ctl_used = False
            if ctl:
                ctl_str = {str(int(float(k))) if _is_numeric(k) else str(k): v for k, v in ctl.items()}
                mapped = raw_vals.astype(str).map(ctl_str)
                hit_rate = mapped.notna().mean()
                if hit_rate >= 0.1:
                    out["age_band"] = mapped.fillna(raw_vals.astype(str))
                    ctl_used = True
            if not ctl_used:
                numeric_ages = pd.to_numeric(raw_vals, errors="coerce")
                if numeric_ages.notna().sum() > 0 and numeric_ages.dropna().between(10, 99).mean() > 0.5:
                    # Raw numeric ages — auto-bin into standard market research bands
                    out["age"] = numeric_ages
                    bins = [0, 17, 24, 34, 44, 54, 64, 99]
                    labels = ["<18", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
                    out["age_band"] = pd.cut(numeric_ages, bins=bins, labels=labels).astype(str)
                else:
                    out["age_band"] = raw_vals.astype(str)
            break

    # CITY
    for q in get_questions_for_bucket(schema_doc, "CITY"):
        col = q.get("source_column", "")
        if col in raw_cols:
            ctl = q.get("code_to_label") or {}
            raw_vals = raw_df[col].astype(str)
            if ctl:
                ctl_str = {str(int(float(k))) if _is_numeric(k) else str(k): v for k, v in ctl.items()}
                out["city_name"] = raw_vals.map(ctl_str).fillna(raw_vals)
            else:
                out["city_name"] = raw_vals
            break

    # ZONE
    for q in get_questions_for_bucket(schema_doc, "ZONE"):
        col = q.get("source_column", "")
        if col in raw_cols:
            ctl = q.get("code_to_label") or {}
            raw_vals = raw_df[col].astype(str)
            if ctl:
                ctl_str = {str(int(float(k))) if _is_numeric(k) else str(k): v for k, v in ctl.items()}
                out["zone_name"] = raw_vals.map(ctl_str).fillna(raw_vals)
            else:
                out["zone_name"] = raw_vals
            break

    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Brand name normalization
# ---------------------------------------------------------------------------

def _normalize_brand_names(df: pd.DataFrame, col: str = "brand_name") -> pd.DataFrame:
    """
    Merge brand name spelling variants that are likely the same brand.
    Groups by difflib similarity > 0.88; canonical = most frequent spelling.
    Prevents double-counting when two questions use slightly different spellings
    (e.g. "Sid's Farm" vs "Sid Farm" vs "Sids Farm").
    """
    if df.empty or col not in df.columns:
        return df
    from difflib import SequenceMatcher

    brands = df[col].unique().tolist()
    if len(brands) <= 1:
        return df

    # Build merge map: minor → canonical
    canonical_map: dict[str, str] = {}
    freq = df[col].value_counts().to_dict()

    for i, a in enumerate(brands):
        if a in canonical_map:
            continue
        for b in brands[i + 1:]:
            if b in canonical_map:
                continue
            ratio = SequenceMatcher(None, _normalize_brand_str(a), _normalize_brand_str(b)).ratio()
            if ratio >= 0.88:
                # Merge less-frequent into more-frequent spelling
                keep = a if freq.get(a, 0) >= freq.get(b, 0) else b
                drop = b if keep == a else a
                canonical_map[drop] = keep

    if canonical_map:
        df = df.copy()
        df[col] = df[col].map(lambda x: canonical_map.get(x, x))
        df = df.drop_duplicates()

    return df


# ---------------------------------------------------------------------------
# Brand Awareness
# ---------------------------------------------------------------------------

def compute_brand_awareness(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns long DataFrame: respondent_id, brand_name, stage.
    One row per respondent×brand×stage combination (only rows where awareness = True).
    """
    AWARENESS_BUCKETS = [
        "TOM", "SPONT", "AIDED", "EVER_USED", "CURRENT_USER",
        "CONSIDERATION", "PREFERRED", "LAST_PURCHASED"
    ]
    raw_cols = set(raw_df.columns)
    resp_col = _get_respondent_id_col(raw_df)
    rows = []

    brand_idx_map = extract_brand_index_map(schema_doc)

    for stage in AWARENESS_BUCKETS:
        for q in get_questions_for_bucket(schema_doc, stage):
            col = q.get("source_column", "")
            shape = q.get("shape", "")
            ctl = _parse_ctl(q.get("code_to_label") or {})
            # Fall back to brand_idx_map when no code_to_label for brand buckets
            if not ctl and brand_idx_map:
                ctl = {str(k): v for k, v in brand_idx_map.items()}
                ctl.update({f"{k}.0": v for k, v in brand_idx_map.items()})

            if col not in raw_cols:
                continue

            if shape in ("single_value", "multivalent_source", "multi_select", ""):
                dummy_cols_q = q.get("dummy_columns") or []
                # If dummy_columns exist for a multivalent_source, prefer them — they're binary and
                # unambiguous. Source_column space-coded values use a different numbering that can
                # include NoneOfThese/other codes that misalign with dummy column positions.
                #
                # Auto-supplement: schema may omit some valid dummy cols (e.g. q17_17 for Pride of Cows
                # if schema only lists q17_1..q17_15). Add any raw_df cols matching {stem}_{int} that
                # map to a known brand and aren't already in the list.
                # Use dummy columns when: explicit multivalent_source shape, OR single_value
                # with dummy_columns populated (e.g. AK AIDED/CONSIDERATION where codebook
                # parser tagged shape='single_value' but the question has brand dummy cols).
                _use_dummies = shape == "multivalent_source" or (shape == "single_value" and dummy_cols_q)
                if _use_dummies and (dummy_cols_q or col in raw_cols):
                    stem = col  # e.g. "q17"
                    existing_set = set(dummy_cols_q)
                    for raw_col in raw_cols:
                        if raw_col in existing_set:
                            continue
                        if not raw_col.startswith(stem + "_"):
                            continue
                        suffix = raw_col[len(stem) + 1:]
                        try:
                            sidx = int(suffix)
                        except ValueError:
                            continue
                        # Only use question-specific CTL to decide if col is valid
                        # (brand_idx_map comes from other questions with different codes)
                        _sidx_brand = ctl.get(str(sidx)) or ctl.get(f"{sidx}.0")
                        if not _sidx_brand:
                            _sidx_brand = brand_idx_map.get(sidx) if not ctl else None
                        if _sidx_brand and not _is_junk_brand(_sidx_brand):
                            dummy_cols_q = list(existing_set) + [raw_col]
                            existing_set.add(raw_col)
                if _use_dummies and dummy_cols_q:
                    for dcol in dummy_cols_q:
                        if dcol not in raw_cols:
                            continue
                        # Parse brand index from trailing integer, supporting both
                        # underscore-suffix (bq1b_1) and slash-suffix (bq1b/1) formats.
                        _last = re.split(r"[_/]", dcol)[-1]
                        try:
                            idx = int(_last)
                        except (ValueError, IndexError):
                            continue
                        # Question-specific CTL takes priority (codes may differ per question)
                        brand = ctl.get(str(idx)) or ctl.get(f"{idx}.0")
                        if not brand:
                            brand = brand_idx_map.get(idx)
                        if not brand or _is_junk_brand(brand):
                            continue
                        mask = pd.to_numeric(raw_df[dcol], errors="coerce").fillna(0) == 1
                        for row_idx in raw_df[mask].index:
                            resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                            rows.append({"respondent_id": resp_id, "brand_name": brand, "stage": stage})
                else:
                    delim = q.get("delimiter") or ""
                    series = raw_df[col].astype(str)
                    for row_idx, val in series.items():
                        resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                        val = str(val).strip()
                        if val in ("nan", "None", ""):
                            continue
                        if delim:
                            codes = [v.strip() for v in val.split(delim) if v.strip() and v.strip() not in ("nan", "None", "")]
                        else:
                            codes = [val]
                        for code in codes:
                            brand = ctl.get(code) or ctl.get(_normalize_code(code))
                            if not brand:
                                brand = _match_brand_name(code, brand_idx_map)
                            if brand:
                                rows.append({"respondent_id": resp_id, "brand_name": brand, "stage": stage})

            elif shape == "multi_select_dummies":
                # source_column is a stem; each branded dummy in dummy_columns
                dummy_cols = q.get("dummy_columns") or []
                # also try stem-pattern if dummy_columns empty
                if not dummy_cols:
                    stem, _ = _stem_and_brand(col)
                    brand_idx_map = extract_brand_index_map(schema_doc)
                    brand_cols = _infer_brand_columns(stem, brand_idx_map, raw_cols)
                    for idx, bcol in brand_cols.items():
                        brand = brand_idx_map.get(idx, f"Brand {idx}")
                        mask = raw_df[bcol].fillna(0).astype(float) == 1
                        for row_idx in raw_df[mask].index:
                            resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                            rows.append({"respondent_id": resp_id, "brand_name": brand, "stage": stage})
                else:
                    for dcol in dummy_cols:
                        if dcol not in raw_cols:
                            continue
                        brand_code = dcol.split("_")[-1] if "_" in dcol else dcol
                        brand = ctl.get(brand_code) or ctl.get(f"{brand_code}.0")
                        if not brand:
                            try:
                                brand = brand_idx_map.get(int(brand_code))
                            except (ValueError, TypeError):
                                pass
                        if not brand:
                            continue  # skip unmapped columns (don't use col name as brand)
                        if _is_junk_brand(brand):
                            continue
                        mask = pd.to_numeric(raw_df[dcol], errors="coerce").fillna(0) == 1
                        for row_idx in raw_df[mask].index:
                            resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                            rows.append({"respondent_id": resp_id, "brand_name": brand, "stage": stage})

    if not rows:
        return pd.DataFrame(columns=["respondent_id", "brand_name", "stage"])
    df = _filter_junk_brands(pd.DataFrame(rows).drop_duplicates())
    return _normalize_brand_names(df)


# ---------------------------------------------------------------------------
# Brand Imagery (BQ3b)
# ---------------------------------------------------------------------------

def compute_brand_imagery(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns long DataFrame: respondent_id, brand_name, attr_label.
    One row per respondent×brand×attribute where association = 1.

    BRAND_IMAGERY schema: one question per attribute, source_column = q34_attr_1 (first brand).
    Other brand columns are q34_attr_2, q34_attr_3, etc.
    brand_idx_map {1: 'AK', 2: 'CD', ...} gives brand_idx → brand_name.
    """
    raw_cols = set(raw_df.columns)
    resp_col = _get_respondent_id_col(raw_df)
    brand_idx_map = extract_brand_index_map(schema_doc)
    rows = []

    for q in get_questions_for_bucket(schema_doc, "BRAND_IMAGERY"):
        attr_label = (q.get("question_text") or "").strip()
        col = q.get("source_column", "")
        shape = q.get("shape", "")
        if not col or not attr_label:
            continue

        ctl = _parse_ctl(q.get("code_to_label") or {})

        # multi_select_dummies: explicit dummy_columns list (bq3b_1/1, bq3b_1/2, ...)
        # Each col suffix is the brand code.
        if shape == "multi_select_dummies" and q.get("dummy_columns"):
            for dcol in q["dummy_columns"]:
                if dcol not in raw_cols:
                    continue
                # Brand code = last segment after _ or /
                _parts = re.split(r"[_/]", dcol)
                brand_code = _parts[-1]
                brand_name = (
                    ctl.get(brand_code) or ctl.get(f"{brand_code}.0") or
                    (brand_idx_map.get(int(brand_code)) if brand_code.isdigit() else None)
                )
                if not brand_name or _is_junk_brand(brand_name):
                    continue
                vals = pd.to_numeric(raw_df[dcol], errors="coerce").fillna(0)
                for row_idx in raw_df[vals == 1].index:
                    resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                    rows.append({"respondent_id": resp_id, "brand_name": brand_name, "attr_label": attr_label})
            continue

        # Default: source_column encodes attr×brand index; infer sibling brand columns.
        stem, first_brand_idx = _stem_and_brand(col)
        if not stem:
            continue

        # Map brand indices to actual column names
        brand_cols = _infer_brand_columns(stem, brand_idx_map, raw_cols)
        if not brand_cols:
            # Fallback: just use source_column with whatever brand it represents
            if col in raw_cols and first_brand_idx in brand_idx_map:
                brand_cols = {first_brand_idx: col}

        for idx, bcol in brand_cols.items():
            brand_name = brand_idx_map.get(idx, f"Brand {idx}")
            vals = pd.to_numeric(raw_df[bcol], errors="coerce").fillna(0)
            mask = vals == 1
            for row_idx in raw_df[mask].index:
                resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                rows.append({"respondent_id": resp_id, "brand_name": brand_name, "attr_label": attr_label})

    if not rows:
        return pd.DataFrame(columns=["respondent_id", "brand_name", "attr_label"])
    return _filter_junk_brands(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# NPS
# ---------------------------------------------------------------------------

def compute_nps(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns: respondent_id, brand_name, nps_score.
    NPS question: source_column = q65_1 (for brand 1). Other brands: q65_2, q65_3, ...
    """
    raw_cols = set(raw_df.columns)
    resp_col = _get_respondent_id_col(raw_df)
    brand_idx_map = extract_brand_index_map(schema_doc)
    rows = []

    for q in get_questions_for_bucket(schema_doc, "NPS"):
        col = q.get("source_column", "")
        if col not in raw_cols:
            continue
        stem, first_brand_idx = _stem_and_brand(col)

        # Check if there are sibling brand columns
        brand_cols = _infer_brand_columns(stem, brand_idx_map, raw_cols)
        if not brand_cols:
            # Single-brand NPS — brand name from question_text or code_to_label
            brand_name = _brand_from_question(q, first_brand_idx, brand_idx_map)
            brand_cols = {first_brand_idx: col}

        for idx, bcol in brand_cols.items():
            brand_name = brand_idx_map.get(idx, f"Brand {idx}")
            scores = pd.to_numeric(raw_df[bcol], errors="coerce")
            for row_idx, score in scores.items():
                if pd.notna(score):
                    resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                    rows.append({"respondent_id": resp_id, "brand_name": brand_name, "nps_score": score})

    if not rows:
        return pd.DataFrame(columns=["respondent_id", "brand_name", "nps_score"])
    return _filter_junk_brands(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# CSAT
# ---------------------------------------------------------------------------

def compute_csat(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns: respondent_id, brand_name, csat_score.
    """
    raw_cols = set(raw_df.columns)
    resp_col = _get_respondent_id_col(raw_df)
    brand_idx_map = extract_brand_index_map(schema_doc)
    rows = []

    for q in get_questions_for_bucket(schema_doc, "CSAT"):
        col = q.get("source_column", "")
        if col not in raw_cols:
            continue

        # Check for paired pattern: score in col, brand code in a separate column
        notes = q.get("notes", "") or ""
        brand_src_col = None
        for part in notes.split(";"):
            part = part.strip()
            if part.startswith("brand_source_column:"):
                brand_src_col = part.split(":", 1)[1].strip()
                break

        if brand_src_col and brand_src_col in raw_cols:
            ctl = q.get("code_to_label", {})
            scores = pd.to_numeric(raw_df[col], errors="coerce")
            brand_codes = raw_df[brand_src_col]
            for row_idx, score in scores.items():
                if pd.notna(score):
                    resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                    raw_code = brand_codes.iloc[row_idx]
                    brand_name = ctl.get(str(raw_code), ctl.get(
                        str(int(float(raw_code))) if pd.notna(raw_code) else "",
                        f"Brand {raw_code}"
                    )) if pd.notna(raw_code) else None
                    if brand_name:
                        rows.append({"respondent_id": resp_id, "brand_name": brand_name, "csat_score": score})
            continue

        stem, first_brand_idx = _stem_and_brand(col)
        brand_cols = _infer_brand_columns(stem, brand_idx_map, raw_cols)
        if not brand_cols:
            brand_name = _brand_from_question(q, first_brand_idx, brand_idx_map)
            brand_cols = {first_brand_idx: col}

        for idx, bcol in brand_cols.items():
            brand_name = brand_idx_map.get(idx, f"Brand {idx}")
            scores = pd.to_numeric(raw_df[bcol], errors="coerce")
            for row_idx, score in scores.items():
                if pd.notna(score):
                    resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                    rows.append({"respondent_id": resp_id, "brand_name": brand_name, "csat_score": score})

    if not rows:
        return pd.DataFrame(columns=["respondent_id", "brand_name", "csat_score"])
    return _filter_junk_brands(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# Importance (BQ3a)
# ---------------------------------------------------------------------------

def compute_importance(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns: respondent_id, attr_label, importance_score.
    IMPORTANCE: one question per attribute, numeric_score shape.
    """
    raw_cols = set(raw_df.columns)
    resp_col = _get_respondent_id_col(raw_df)
    rows = []

    for q in get_questions_for_bucket(schema_doc, "IMPORTANCE"):
        col = q.get("source_column", "")
        attr_label = (q.get("question_text") or q.get("question_code", "")).strip()
        if col not in raw_cols or not attr_label:
            continue
        scores = pd.to_numeric(raw_df[col], errors="coerce")
        for row_idx, score in scores.items():
            if pd.notna(score):
                resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                rows.append({"respondent_id": resp_id, "attr_label": attr_label, "importance_score": score})

    if not rows:
        return pd.DataFrame(columns=["respondent_id", "attr_label", "importance_score"])
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Attitudes
# ---------------------------------------------------------------------------

def compute_attitudes(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns: respondent_id, attr_label, rating.
    """
    raw_cols = set(raw_df.columns)
    resp_col = _get_respondent_id_col(raw_df)
    rows = []

    for q in get_questions_for_bucket(schema_doc, "ATTITUDE"):
        col = q.get("source_column", "")
        attr_label = (q.get("question_text") or q.get("question_code", "")).strip()
        if col not in raw_cols or not attr_label:
            continue
        ratings = pd.to_numeric(raw_df[col], errors="coerce")
        for row_idx, rating in ratings.items():
            if pd.notna(rating):
                resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                rows.append({"respondent_id": resp_id, "attr_label": attr_label, "rating": rating})

    if not rows:
        return pd.DataFrame(columns=["respondent_id", "attr_label", "rating"])
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Price Paid
# ---------------------------------------------------------------------------

def compute_price_paid(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns: respondent_id, brand_name, price_tier.
    """
    raw_cols = set(raw_df.columns)
    resp_col = _get_respondent_id_col(raw_df)
    brand_idx_map = extract_brand_index_map(schema_doc)
    rows = []

    for q in get_questions_for_bucket(schema_doc, "PRICE_PAID"):
        col = q.get("source_column", "")
        if col not in raw_cols:
            continue
        ctl = _parse_ctl(q.get("code_to_label") or {})
        stem, first_brand_idx = _stem_and_brand(col)
        brand_cols = _infer_brand_columns(stem, brand_idx_map, raw_cols)
        if not brand_cols:
            brand_cols = {first_brand_idx: col}

        for idx, bcol in brand_cols.items():
            brand_name = brand_idx_map.get(idx, f"Brand {idx}")
            vals = raw_df[bcol].astype(str)
            for row_idx, val in vals.items():
                if val and val not in ("nan", ""):
                    resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                    price_tier = ctl.get(val) or ctl.get(_normalize_code(val)) or val
                    rows.append({"respondent_id": resp_id, "brand_name": brand_name, "price_tier": price_tier})

    if not rows:
        return pd.DataFrame(columns=["respondent_id", "brand_name", "price_tier"])
    return _filter_junk_brands(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# Purchase Journey
# ---------------------------------------------------------------------------

def compute_purchase_journey(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns: respondent_id, question_code, question_text, answer.
    """
    raw_cols = set(raw_df.columns)
    resp_col = _get_respondent_id_col(raw_df)
    rows = []

    for q in get_questions_for_bucket(schema_doc, "PURCHASE_JOURNEY"):
        col = q.get("source_column", "")
        qcode = q.get("question_code", "")
        qtext = (q.get("question_text") or "").strip()
        if col not in raw_cols:
            continue
        ctl = _parse_ctl(q.get("code_to_label") or {})
        for row_idx, val in raw_df[col].items():
            val_str = str(val).strip()
            if val_str and val_str not in ("nan", ""):
                resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                answer = ctl.get(val_str) or ctl.get(_normalize_code(val_str)) or val_str
                rows.append({"respondent_id": resp_id, "question_code": qcode,
                             "question_text": qtext, "answer": answer})

    if not rows:
        return pd.DataFrame(columns=["respondent_id", "question_code", "question_text", "answer"])
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Portfolio Awareness (BQ6)
# ---------------------------------------------------------------------------

def compute_portfolio_awareness(raw_df: pd.DataFrame, schema_doc: dict) -> pd.DataFrame:
    """
    Returns: respondent_id, brand_name, category_name.
    """
    raw_cols = set(raw_df.columns)
    resp_col = _get_respondent_id_col(raw_df)
    brand_idx_map = extract_brand_index_map(schema_doc)
    rows = []

    for q in get_questions_for_bucket(schema_doc, "PORTFOLIO_AWARENESS"):
        col = q.get("source_column", "")
        if col not in raw_cols:
            continue
        ctl = _parse_ctl(q.get("code_to_label") or {})
        category_name = (q.get("question_text") or q.get("question_code", "")).strip()
        stem, first_brand_idx = _stem_and_brand(col)
        brand_cols = _infer_brand_columns(stem, brand_idx_map, raw_cols)
        if not brand_cols:
            brand_cols = {first_brand_idx: col}

        for idx, bcol in brand_cols.items():
            brand_name = brand_idx_map.get(idx, f"Brand {idx}")
            mask = raw_df[bcol].fillna(0).astype(float) == 1
            for row_idx in raw_df[mask].index:
                resp_id = raw_df.at[row_idx, resp_col] if resp_col else row_idx + 1
                rows.append({"respondent_id": resp_id, "brand_name": brand_name,
                             "category_name": category_name})

    if not rows:
        return pd.DataFrame(columns=["respondent_id", "brand_name", "category_name"])
    return _filter_junk_brands(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# Aggregation helpers (analytics-ready outputs)
# ---------------------------------------------------------------------------

def get_funnel_pct(awareness_df: pd.DataFrame, brand_name: str,
                   base_n: Optional[int] = None) -> pd.DataFrame:
    """
    Returns: stage, count, pct — funnel % for one brand.
    STAGE_ORDER sets display order.
    """
    STAGE_ORDER = ["TOM", "SPONT", "AIDED", "EVER_USED", "CURRENT_USER",
                   "CONSIDERATION", "PREFERRED", "LAST_PURCHASED"]
    if awareness_df.empty:
        return pd.DataFrame(columns=["stage", "count", "pct"])
    df = awareness_df[awareness_df["brand_name"] == brand_name]
    if base_n is None:
        base_n = awareness_df["respondent_id"].nunique()
    counts = df.groupby("stage")["respondent_id"].nunique().reset_index()
    counts.columns = ["stage", "count"]
    counts["pct"] = (counts["count"] / base_n * 100).round(1)
    # order by STAGE_ORDER
    stage_idx = {s: i for i, s in enumerate(STAGE_ORDER)}
    counts["_order"] = counts["stage"].map(stage_idx).fillna(99)
    counts = counts.sort_values("_order").drop(columns=["_order"])
    return counts.reset_index(drop=True)


def get_imagery_pct(imagery_df: pd.DataFrame, brand_name: str,
                    base_n: Optional[int] = None, top_n: int = 15) -> pd.DataFrame:
    """
    Returns: attr_label, count, pct — top-N attribute associations for one brand.
    association% = COUNT(DISTINCT respondent_id) / base_n.
    """
    if imagery_df.empty:
        return pd.DataFrame(columns=["attr_label", "count", "pct"])
    df = imagery_df[imagery_df["brand_name"] == brand_name]
    if base_n is None:
        # base = all respondents who answered any imagery question (not brand-filtered)
        # Using brand-filtered base inflates % because it excludes respondents who saw the
        # question but didn't associate any attribute with this brand.
        base_n = imagery_df["respondent_id"].nunique()
        if base_n == 0:
            base_n = 1
    counts = (df.groupby("attr_label")["respondent_id"]
               .nunique()
               .reset_index()
               .rename(columns={"respondent_id": "count"}))
    counts["pct"] = (counts["count"] / base_n * 100).round(1)
    return counts.nlargest(top_n, "pct").reset_index(drop=True)


def get_nps_score(nps_df: pd.DataFrame, brand_name: str) -> dict:
    """
    Returns dict: {nps_score, promoters_pct, passives_pct, detractors_pct, base_n}.
    NPS = promoters% - detractors%. promoters = score 9-10, detractors = 0-6.
    """
    if nps_df.empty:
        return {}
    df = nps_df[nps_df["brand_name"] == brand_name]["nps_score"].dropna()
    if df.empty:
        return {}
    n = len(df)
    prom = (df >= 9).sum() / n * 100
    detrac = (df <= 6).sum() / n * 100
    passiv = 100 - prom - detrac
    return {
        "nps_score": round(prom - detrac, 1),
        "promoters_pct": round(prom, 1),
        "passives_pct": round(passiv, 1),
        "detractors_pct": round(detrac, 1),
        "base_n": n,
    }


def get_csat_score(csat_df: pd.DataFrame, brand_name: str) -> dict:
    """
    Returns dict: {csat_score (0-5 scale), base_n}.
    Raw score assumed 0-10; displayed /5.
    """
    if csat_df.empty:
        return {}
    df = csat_df[csat_df["brand_name"] == brand_name]["csat_score"].dropna()
    if df.empty:
        return {}
    raw_mean = df.mean()
    col_max = df.max()
    # Use actual max to detect scale: max > 5.5 means 0-10 scale → display on /5
    displayed = round(raw_mean / 2 if col_max > 5.5 else raw_mean, 2)
    return {"csat_score": displayed, "base_n": len(df)}


def get_all_brands(awareness_df: pd.DataFrame) -> list[str]:
    """All brand names present in awareness data, sorted."""
    if awareness_df.empty:
        return []
    return sorted(awareness_df["brand_name"].unique().tolist())


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _get_respondent_id_col(raw_df: pd.DataFrame) -> Optional[str]:
    for c in ["respondent_id", "resp_id", "id", "serial", "s_no", "serial_no", "respid"]:
        if c in raw_df.columns:
            return c
    return None


def _parse_ctl(ctl: dict) -> dict[str, str]:
    """Normalize code_to_label keys: '1.0' → '1'."""
    out = {}
    for k, v in ctl.items():
        out[str(k).strip()] = str(v).strip()
        try:
            out[str(int(float(k)))] = str(v).strip()
        except (ValueError, TypeError):
            pass
    return out


def _normalize_code(val: str) -> str:
    """'1.0' → '1', '2.0' → '2'."""
    try:
        return str(int(float(val)))
    except (ValueError, TypeError):
        return val


def _is_numeric(val: str) -> bool:
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


def _normalize_brand_str(s: str) -> str:
    """Normalize brand name for fuzzy matching: lowercase, strip punctuation/possessives."""
    s = s.lower().strip()
    s = re.sub(r"'s\b", "", s)   # remove possessives ("sid's" → "sid")
    s = re.sub(r"[^\w\s]", "", s)  # strip remaining punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _match_brand_name(raw_val: str, brand_idx_map: dict[int, str]) -> Optional[str]:
    """
    When raw value is already a brand name string (not a code), find the canonical brand_name.
    Tries: exact match → case-insensitive → normalized substring → word-overlap.
    """
    raw_lower = raw_val.strip().lower()
    raw_norm = _normalize_brand_str(raw_val)

    # Pass 1: exact / case-insensitive match
    for brand_name in brand_idx_map.values():
        if brand_name.lower() == raw_lower:
            return brand_name

    # Pass 2: substring (raw inside brand or brand inside raw)
    # Require minimum length to avoid false matches ("Milk" matching "Milky Mist" and "Milk Brand")
    # If multiple brands match via substring, skip all — ambiguous match is worse than no match.
    _sub_matches = []
    for brand_name in brand_idx_map.values():
        b_lower = brand_name.lower()
        if len(raw_lower) >= 5 and len(b_lower) >= 5:
            if raw_lower in b_lower or b_lower in raw_lower:
                _sub_matches.append(brand_name)
    if len(_sub_matches) == 1:
        return _sub_matches[0]
    # Multiple matches = ambiguous; fall through to next pass

    # Pass 3: normalized substring (strips apostrophes, punctuation)
    for brand_name in brand_idx_map.values():
        b_norm = _normalize_brand_str(brand_name)
        if raw_norm and b_norm and (raw_norm in b_norm or b_norm in raw_norm):
            return brand_name

    # Pass 4: first-word match (e.g. "Sid Farm" → "Sid's Farm" via first word "sid")
    raw_words = raw_norm.split()
    if raw_words:
        first_word = raw_words[0]
        if len(first_word) >= 4:  # avoid short word false matches
            for brand_name in brand_idx_map.values():
                b_words = _normalize_brand_str(brand_name).split()
                if b_words and b_words[0] == first_word and len(b_words) <= len(raw_words) + 2:
                    return brand_name

    return None


def _brand_from_question(q: dict, brand_idx: int, brand_idx_map: dict[int, str]) -> str:
    """Extract brand name from question_text or brand_idx_map fallback."""
    brand = brand_idx_map.get(brand_idx)
    if brand:
        return brand
    # Try parsing from question_text: "q65_1. Recommendation: Akshayakalpa Organic"
    qt = q.get("question_text", "")
    if ":" in qt:
        return qt.split(":", 1)[-1].strip()
    return f"Brand {brand_idx}"


# ---------------------------------------------------------------------------
# Project-level facade — cached, ready for brand_health.py to call
# ---------------------------------------------------------------------------

class ProjectDataLayer:
    """
    One instance per project. Loads raw_df + schema_doc once, exposes all compute fns.
    Use get_project_layer(project_id) to get a cached instance.
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._raw_df: Optional[pd.DataFrame] = None
        self._schema_doc: Optional[dict] = None
        self._awareness_df: Optional[pd.DataFrame] = None
        self._imagery_df: Optional[pd.DataFrame] = None
        self._nps_df: Optional[pd.DataFrame] = None
        self._csat_df: Optional[pd.DataFrame] = None
        self._demographics_df: Optional[pd.DataFrame] = None
        self._importance_df: Optional[pd.DataFrame] = None
        self._attitudes_df: Optional[pd.DataFrame] = None
        self._price_paid_df: Optional[pd.DataFrame] = None
        self._purchase_journey_df: Optional[pd.DataFrame] = None

    @property
    def raw_df(self) -> pd.DataFrame:
        if self._raw_df is None:
            self._raw_df = load_raw_df(self.project_id)
        return self._raw_df

    @property
    def n_respondents(self) -> int:
        return len(self.raw_df)

    @property
    def schema_doc(self) -> dict:
        if self._schema_doc is None:
            self._schema_doc = load_schema_doc(self.project_id)
        return self._schema_doc

    @property
    def awareness(self) -> pd.DataFrame:
        if self._awareness_df is None:
            self._awareness_df = compute_brand_awareness(self.raw_df, self.schema_doc)
        return self._awareness_df

    @property
    def imagery(self) -> pd.DataFrame:
        if self._imagery_df is None:
            self._imagery_df = compute_brand_imagery(self.raw_df, self.schema_doc)
        return self._imagery_df

    @property
    def nps(self) -> pd.DataFrame:
        if self._nps_df is None:
            self._nps_df = compute_nps(self.raw_df, self.schema_doc)
        return self._nps_df

    @property
    def csat(self) -> pd.DataFrame:
        if self._csat_df is None:
            self._csat_df = compute_csat(self.raw_df, self.schema_doc)
        return self._csat_df

    @property
    def demographics(self) -> pd.DataFrame:
        if self._demographics_df is None:
            self._demographics_df = compute_respondents(self.raw_df, self.schema_doc)
        return self._demographics_df

    @property
    def importance(self) -> pd.DataFrame:
        if self._importance_df is None:
            self._importance_df = compute_importance(self.raw_df, self.schema_doc)
        return self._importance_df

    @property
    def attitudes(self) -> pd.DataFrame:
        if self._attitudes_df is None:
            self._attitudes_df = compute_attitudes(self.raw_df, self.schema_doc)
        return self._attitudes_df

    @property
    def price_paid(self) -> pd.DataFrame:
        if self._price_paid_df is None:
            self._price_paid_df = compute_price_paid(self.raw_df, self.schema_doc)
        return self._price_paid_df

    @property
    def purchase_journey(self) -> pd.DataFrame:
        if self._purchase_journey_df is None:
            self._purchase_journey_df = compute_purchase_journey(self.raw_df, self.schema_doc)
        return self._purchase_journey_df

    @property
    def brand_index_map(self) -> dict[int, str]:
        return extract_brand_index_map(self.schema_doc)

    @property
    def all_brands(self) -> list[str]:
        return get_all_brands(self.awareness)

    def funnel_pct(self, brand_name: str, base_n: Optional[int] = None) -> pd.DataFrame:
        if base_n is None:
            base_n = len(self.raw_df)
        return get_funnel_pct(self.awareness, brand_name, base_n=base_n)

    def imagery_pct(self, brand_name: str, top_n: int = 15) -> pd.DataFrame:
        return get_imagery_pct(self.imagery, brand_name, top_n=top_n)

    def nps_score(self, brand_name: str) -> dict:
        return get_nps_score(self.nps, brand_name)

    def csat_score(self, brand_name: str) -> dict:
        return get_csat_score(self.csat, brand_name)

    def invalidate(self) -> None:
        """Clear all cached DataFrames (e.g. after re-ingest)."""
        for attr in vars(self):
            if attr.startswith("_") and attr.endswith("_df"):
                setattr(self, attr, None)
        clear_project_cache(self.project_id)


_layer_cache: dict[str, "ProjectDataLayer"] = {}


def get_project_layer(project_id: str) -> "ProjectDataLayer":
    """
    Get (or create) cached ProjectDataLayer for project_id.
    Returns None if project has no raw Excel layer (falls back to SQLite path).
    """
    if not project_has_raw_layer(project_id):
        return None
    if project_id not in _layer_cache:
        _layer_cache[project_id] = ProjectDataLayer(project_id)
    return _layer_cache[project_id]


def invalidate_project(project_id: str) -> None:
    """Call after re-ingesting a project to clear all caches."""
    if project_id in _layer_cache:
        _layer_cache[project_id].invalidate()
        del _layer_cache[project_id]
    clear_project_cache(project_id)
