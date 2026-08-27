"""
LENS Codebook Parser — shared dataclasses, target-bucket schema, and export helpers
====================================================================================
2026-08-03: this module used to be the full codebook -> mapping-report classifier
pipeline (load codebook -> reconcile against data columns -> fuzzy-match value labels
-> guess a bucket per question). That pipeline has been replaced by
`lens/ingestion/schema_ingest.py`'s single-call, schema-driven classifier — see that
module and `generic_loader.py::assignment_from_schema`. A deep-clean audit (2026-08-03)
confirmed every reconciliation/classification function that used to live here has zero
real callers left anywhere in the repo, so they were deleted along with their helpers.

What's still genuinely used, and by whom:
  - `QuestionDef` — the dataclass shape `prose_questionnaire_parser.py` builds its
    extracted questions into (`from .codebook_parser import QuestionDef`).
  - `MappingGuess` — the dataclass `oxdata/views/add_project.py` adapts schema_ingest's
    plain-dict question rows into, so the review-table UI and the export functions
    below have one common row shape to work with.
  - `TARGET_BUCKETS` — the fixed set of Brand Health buckets (TOM/SPONT/CSAT/...) a
    human assigns each question to; used by `add_project.py` to populate the bucket
    dropdown in the review table.
  - `export_column_map_excel` / `export_column_map_json` — generate the reviewable
    Excel/JSON exports `add_project.py`'s "Export" buttons hand to the user for client
    signoff. Both call `suggest_bucket()` (kept below as their one real internal
    dependency, along with its own `_suggest_bucket_from_text`/`_ROLE_TO_BUCKET`/
    `_TEXT_TO_BUCKET` helpers) to fill in a starting bucket for any row the caller
    didn't already confirm.

Nothing here writes to the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class QuestionDef:
    """One row from the codebook's question list, before touching the data file."""
    code: str                      # e.g. 'bq1d' — the codebook's question identifier
    text: str                      # question label / prompt text
    list_name: Optional[str]       # choice-list name to join against, or None (open/numeric)
    value_labels: dict = field(default_factory=dict)   # {code: label} from the choices sheet
    # response type hint from a tab-plan-style codebook (SA=single, MA=multi, OE=open-end).
    # None for loaders that don't carry this (e.g. XLSForm).
    response_type: Optional[str] = None


@dataclass
class MappingGuess:
    """One row of the question-to-bucket mapping review table (built by
    add_project.py's `_guess_from_schema_row` from schema_ingest's classifier output,
    not by anything in this module anymore) — the shape export_column_map_excel/json
    and the review-table UI both consume."""
    question_code: str
    question_text: str
    shape: str
    guessed_role: str              # e.g. 'brand_awareness_stage', 'importance_battery', 'unknown'
    confidence: float              # 0.0-1.0
    matched_dimension: Optional[str]   # e.g. 'dim_brand' if value labels matched brand names
    evidence: str                  # short human-readable why
    n_data_columns: int
    sample_values: list = field(default_factory=list)
    # the actual data column names this guess covers — needed so the bucket-assignment
    # UI (add_project.py) can gather real source columns per confirmed bucket.
    data_columns: list = field(default_factory=list)
    # this question's {code: label} value-label map — needed so add_project.py's
    # "Confirm Bucket Assignment" step can hand generic_loader.load_confirmed_assignment()
    # a real value_labels_by_code dict.
    value_labels: dict = field(default_factory=dict)
    # real raw cell values from data_columns[0], shown to the human reviewer alongside
    # the decoded sample_values above so a wrong/misleading value-label guess is visible.
    raw_data_sample: str = ""
    # AI-identified delimiter character for a synthesized combined column, if any.
    preferred_delimiter: Optional[str] = None


# ── Step 5 schema: buckets — the FIXED target schema every project maps onto ───
# This is the "common point" answer: the classifier's guessed_role is free-text and
# open-ended, but the actual database schema Brand Health reads from is fixed and
# small. TARGET_BUCKETS is that fixed set. A human assigns each source question to
# exactly one bucket (or SKIP) before anything gets ingested — see the "Assign to
# bucket" step in oxdata/views/add_project.py. Bucket names match fact table / stage
# names directly (fact_brand_awareness.stage values, fact_satisfaction, etc.) so a
# confirmed bucket assignment maps onto the schema with no further translation needed.
TARGET_BUCKETS = {
    "TOM":                  "Top-of-mind brand awareness (first brand named, unprompted)",
    "SPONT":                "Spontaneous/unprompted brand recall (named after the first)",
    "AIDED":                "Aided/prompted brand awareness (recognised from a shown list)",
    "EVER_USED":            "Ever used the brand",
    "CURRENT_USER":         "Currently uses the brand",
    "CONSIDERATION":        "Would consider / considered in the past",
    "PREFERRED":            "Most preferred / most-often-used brand",
    "LAST_PURCHASED":       "Most recently purchased brand",
    "CSAT":                 "Satisfaction score (e.g. 0-10 happiness/experience scale)",
    "NPS":                  "Likelihood to recommend (Net Promoter Score question)",
    "BRAND_IMAGERY":        "Brand/attribute association battery (\"which brands fit X\")",
    "IMPORTANCE":           "Attribute importance rating battery",
    "ATTITUDE":             "Category attitude / agreement rating battery",
    "PORTFOLIO_AWARENESS":  "Which categories/products a brand is associated with",
    "PRICE_PAID":           "Price paid / price tier",
    "PURCHASE_JOURNEY":     "Purchase journey / channel / decision-driver questions",
    # split from one generic DEMOGRAPHIC into specific fields — every Segment Filter
    # dropdown (Zone/Gender/Age/City) on every Brand Health tab needs ONE of these
    # specifically, not a vague "this is demographic" — the loader has to know exactly
    # which fact_respondents column to write. DEMOGRAPHIC itself stays as a catch-all
    # for demographic fields that aren't one of these four (income, occupation, NCCS,
    # etc.) — accepted, not yet written anywhere.
    "GENDER":               "Respondent gender",
    "AGE":                  "Respondent age (numeric or age-band)",
    "CITY":                 "Respondent city",
    "ZONE":                 "Respondent zone/region (North/South/East/West or similar)",
    "DEMOGRAPHIC":          "Other respondent demographic (income, occupation, NCCS, ...)",
    "SKIP":                 "Not used for Brand Health — leave unmapped",
}

# Best-effort starting suggestion per classifier guessed_role — a human still confirms every
# row (see add_project.py), this only saves them from starting at "SKIP" for the obvious cases.
# Anything not listed here (unknown, unmatched, *_unclassified, numeric_open, unknown_battery,
# small_range_numeric_unclassified) defaults to SKIP — deliberately: a low-confidence guess
# should never pre-select a real bucket, only a confident/structural one should.
_ROLE_TO_BUCKET = {
    "brand_awareness_tom":        "TOM",
    "brand_awareness_spont":      "SPONT",
    "brand_awareness_aided":      "AIDED",
    "brand_funnel_ever_used":     "EVER_USED",
    "brand_funnel_current_user":  "CURRENT_USER",
    "brand_funnel_consideration": "CONSIDERATION",
    "brand_funnel_preferred":     "PREFERRED",
    "brand_funnel_last_purchased": "LAST_PURCHASED",
    "brand_imagery":              "BRAND_IMAGERY",
    "brand_multiselect_unclassified": "BRAND_IMAGERY",
    "importance_battery":         "IMPORTANCE",
    "attitude_battery":           "ATTITUDE",
    "portfolio_awareness":        "PORTFOLIO_AWARENESS",
    # satisfaction_or_nps_score is genuinely ambiguous (CSAT vs NPS look identical structurally —
    # both are small numeric scales) — suggest CSAT as the more common case, human corrects to
    # NPS when the question text says "recommend" instead of "satisfied"/"happy".
    "satisfaction_or_nps_score":  "CSAT",
    "demographic_numeric":        "AGE",
    "demographic_or_admin":       "DEMOGRAPHIC",
}

# The ONLY signal available for label-less codebooks (AP-tabplan-style) — leans on literal
# market-research terminology rather than looser keywords.
_TEXT_TO_BUCKET = [
    ("TOM",            ["top of mind", "top-of-mind", r"\btom\b"]),
    ("SPONT",          ["spontaneous", "spont"]),
    ("AIDED",          ["aided awareness", "total aided", "aided"]),
    ("EVER_USED",      ["ever tried", "ever used"]),
    # "tried in l6m/l3m/l1m" (and spelled-out "last six/three/1 months") is a common recency-
    # window phrasing for "do you currently use this" in AP tab-plans — same funnel stage as
    # CURRENT_USER, just worded as a lookback window instead of the word "current".
    ("CURRENT_USER",   ["currently use", "current user", "currently using", "tried in l6m",
                         "tried in l3m", "tried in l1m", "in the last six months",
                         "in the last three months", "in the last 1 month", "last six months",
                         "last three months", "consumed in the last"]),
    ("CONSIDERATION",  ["considered in the past", "ever considered", "consideration", "would consider"]),
    ("PREFERRED",      ["moub", "most often used", "most preferred", "prefer most"]),
    ("LAST_PURCHASED", ["last purchased", "recently purchased", "purchased most"]),
    ("NPS",            ["recommend", "likelihood to advocate", "nps", "advocate",
                         "likelihood.*recommend", "would you suggest", "how likely.*suggest",
                         "net promoter"]),
    ("CSAT",           ["overall experience", "satisf", "happy", "csat"]),
    # "imegery" tolerates the real-world typo seen in Akshayakalpa's own AP tab-plan titles
    # ("BRAND IMEGERY") — a client's source file misspelling a section header shouldn't sink
    # that section's rows to SKIP.
    ("BRAND_IMAGERY",  ["imagery", "imegery", "brand you love", "trusted choice",
                         "unique when compared"]),
    ("PORTFOLIO_AWARENESS", ["categories/products", "electrical products are made by"]),
    ("PRICE_PAID",     ["price per", "price paid", "smartphone price"]),
    # generic "where/how/why/who" purchase-behavior wording — not brand-funnel-staged, not a
    # rating scale, just channel/decision-maker/reason questions.
    ("PURCHASE_JOURNEY", ["where do you buy", "places you buy", "buy.*from", "where do you "
                          "purchase", "who decides", "purchase channel", "where.*research",
                          "why.*bought", "why.*purchase", "how did you (?:hear|come to know)"]),
    # specific demographic fields checked BEFORE the generic DEMOGRAPHIC catch-all —
    # order matters here (first match wins), so these have to come first or "gender" etc. would
    # never be reached once DEMOGRAPHIC's broader net catches it first.
    ("GENDER",         [r"\bgender\b", "your sex", "male/female", r"\bsex\b"]),
    ("AGE",            ["your age", "age_post code", "age group", "age band", "age in years", r"\bage\b"]),
    ("CITY",           [r"\bcity\b", r"\blocation\b", r"\btown\b", r"\bmetro\b", "which city", "select the city", "your city"]),
    ("ZONE",           [r"\bzone\b", r"\bregion\b", "north/south/east/west"]),
    ("DEMOGRAPHIC",    ["center", "centre", "monthly income", "marital status", "occupation",
                         "nccs"]),
]



def _suggest_bucket_from_text(question_text: str) -> str:
    # Strip HTML markup before keyword matching — otherwise a phrase like "select the <b>city</b>
    # where" never matches a "select the city" keyword (the tag sits in the middle of the
    # phrase). Keep the [AP_TABLE_TITLE=...] marker's CONTENT (e.g. "TOM", "TOTAL AIDED
    # AWARENESS") — only unwrap the marker syntax itself, since that title text is exactly what
    # most of the keyword rules below match on.
    t = re.sub(r"^\[AP_TABLE_TITLE=([^\]]*)\]\s*", r"\1 ", str(question_text))
    t = re.sub(r"<[^>]+>", "", t).lower()
    for bucket, keywords in _TEXT_TO_BUCKET:
        if any(re.search(k, t) for k in keywords):
            return bucket
    return "SKIP"


def suggest_bucket(guessed_role: str, question_text: str = "") -> str:
    """Starting bucket suggestion — always human-overridable. Tries the classifier's guessed_role
    first (works when value labels were available to fuzzy-match), then falls back to keyword
    matching on the question text itself (works even for label-less codebooks like AP tab-plans).

    Exception: `demographic_or_admin` is a generic CATCH-ALL role (fires whenever a small option
    set doesn't dimension-match brand/attribute — e.g. gender or city SA questions look
    structurally identical to any other small-option demographic question). Short-circuiting on
    it before ever checking question text meant real GENDER/CITY/ZONE questions (which DO have a
    specific bucket + text keywords, see _TEXT_TO_BUCKET) were silently swallowed into the generic
    DEMOGRAPHIC bucket (which `generic_loader.py` accepts but never writes). Fix: for this one
    generic role, prefer a more specific text-keyword match if one exists; only fall back to
    DEMOGRAPHIC if the text doesn't match anything more specific either.
    """
    from_role = _ROLE_TO_BUCKET.get(guessed_role)
    if from_role and from_role == "DEMOGRAPHIC":
        from_text = _suggest_bucket_from_text(question_text)
        if from_text not in ("SKIP", "DEMOGRAPHIC"):
            return from_text
        return from_role
    if guessed_role == "satisfaction_or_nps_score":
        # This shape is genuinely ambiguous between CSAT and NPS — default CSAT, but override
        # to NPS if the text specifically says "recommend" or NPS-specific variants.
        from_text = _suggest_bucket_from_text(question_text)
        if from_text == "NPS":
            return from_text
        return from_role
    # When the LLM guessed a SPECIFIC awareness/funnel role (not generic), trust it over
    # text keyword match — text keywords are broader and can misfire on question preamble.
    # Only use text keyword as primary when role is unknown/generic.
    _SPECIFIC_ROLES = {
        "brand_awareness_tom", "brand_awareness_spont", "brand_awareness_aided",
        "brand_funnel_ever_used", "brand_funnel_current_user", "brand_funnel_consideration",
        "brand_funnel_preferred", "brand_funnel_last_purchased",
        "brand_imagery", "importance_battery", "attitude_battery", "portfolio_awareness",
    }
    if guessed_role in _SPECIFIC_ROLES and from_role:
        # Still allow NPS override on satisfaction_or_nps_score (handled above)
        return from_role
    from_text = _suggest_bucket_from_text(question_text)
    if from_text not in ("SKIP", "BRAND_IMAGERY"):
        return from_text
    if from_role:
        return from_role
    return from_text


def export_column_map_excel(guesses: list[MappingGuess],
                            confirmed_assignment: Optional[dict] = None,
                            value_labels_by_code: Optional[dict] = None) -> bytes:
    """Generate a reviewable multi-sheet Excel workbook (.xlsx) detailing question-to-bucket
    mappings and brand/value-label crosswalks for client signoff."""
    import io
    import pandas as pd

    bucket_map = {}
    if confirmed_assignment:
        for bucket, items in confirmed_assignment.items():
            for item in items:
                qcode = item.get("question_code")
                if qcode:
                    bucket_map[qcode] = bucket

    map_rows = []
    for g in guesses:
        assigned_b = bucket_map.get(g.question_code) or suggest_bucket(g.guessed_role, g.question_text)
        v_labels = (value_labels_by_code.get(g.question_code, g.value_labels)
                    if value_labels_by_code is not None else g.value_labels)
        v_labels_str = ", ".join(f"{k}={v}" for k, v in list(v_labels.items())[:8]) if v_labels else ""
        map_rows.append({
            "Question Code": g.question_code,
            "Question Text": g.question_text,
            "Assigned Bucket": assigned_b,
            "Guessed Role": g.guessed_role,
            "Data Columns": ", ".join(str(c) for c in g.data_columns),
            "Column Count": g.n_data_columns,
            "Detected Shape": g.shape,
            "Confidence": round(g.confidence, 2),
            "Value Labels / Brands": v_labels_str,
            "Evidence": g.evidence,
            "Raw Data Sample": g.raw_data_sample,
        })
    df_map = pd.DataFrame(map_rows)

    xw_rows = []
    for g in guesses:
        assigned_b = bucket_map.get(g.question_code) or suggest_bucket(g.guessed_role, g.question_text)
        v_labels = (value_labels_by_code.get(g.question_code, g.value_labels)
                    if value_labels_by_code is not None else g.value_labels)
        if v_labels:
            for code, label in v_labels.items():
                xw_rows.append({
                    "Question Code": g.question_code,
                    "Question Text": g.question_text,
                    "Assigned Bucket": assigned_b,
                    "Option Code": code,
                    "Mapped Brand / Value Label": label,
                    "Data Columns": ", ".join(str(c) for c in g.data_columns),
                })
        else:
            xw_rows.append({
                "Question Code": g.question_code,
                "Question Text": g.question_text,
                "Assigned Bucket": assigned_b,
                "Option Code": "(open / un-coded)",
                "Mapped Brand / Value Label": "(raw cell values used)",
                "Data Columns": ", ".join(str(c) for c in g.data_columns),
            })
    df_xw = pd.DataFrame(xw_rows)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_map.to_excel(writer, sheet_name="Column_Bucket_Map", index=False)
        df_xw.to_excel(writer, sheet_name="Brand_Value_Crosswalk", index=False)
    return buf.getvalue()


def export_column_map_json(guesses: list[MappingGuess],
                           confirmed_assignment: Optional[dict] = None,
                           value_labels_by_code: Optional[dict] = None) -> str:
    """Generate a structured JSON export of final bucket and brand/value assignments for client signoff."""
    import json
    bucket_map = {}
    if confirmed_assignment:
        for bucket, items in confirmed_assignment.items():
            for item in items:
                qcode = item.get("question_code")
                if qcode:
                    bucket_map[qcode] = bucket

    out = []
    for g in guesses:
        assigned_b = bucket_map.get(g.question_code) or suggest_bucket(g.guessed_role, g.question_text)
        v_labels = (value_labels_by_code.get(g.question_code, g.value_labels)
                    if value_labels_by_code is not None else g.value_labels)
        out.append({
            "question_code": g.question_code,
            "question_text": g.question_text,
            "assigned_bucket": assigned_b,
            "guessed_role": g.guessed_role,
            "data_columns": [str(c) for c in g.data_columns],
            "shape": g.shape,
            "confidence": round(g.confidence, 2),
            "value_labels": v_labels,
            "evidence": g.evidence,
            "raw_data_sample": g.raw_data_sample,
        })
    return json.dumps({"questions": out}, indent=2)
