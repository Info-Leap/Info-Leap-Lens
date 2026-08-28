"""Single-call, schema-driven ingestion mapping.

Replaces the old multi-stage classifier (codebook_parser.py's reconcile_columns /
reconcile_columns_positional / group_cross_question_batteries / enrich_* / AI-verify chunks)
with one LLM call that sees the FULL analysis plan, FULL datamap, and a row-sampled but
column-complete slice of the raw data, and returns a JSON document matching
lens/ingestion/schemas/ingestion_mapping.schema.json directly. No per-question calls, no
battery-vs-base ambiguity: the LLM is told explicitly to pick the multivalent delimited
column as source_column and list the one-hot dummy columns as informational only.
"""
import json
import pathlib
import re
from typing import Optional

import jsonschema
import pandas as pd

SCHEMA_PATH = pathlib.Path(__file__).parent / "schemas" / "ingestion_mapping.schema.json"


def build_ap_column_map(ap_text: str, all_columns: list) -> dict:
    """Pre-link AP Table Titles to raw data column names WITHOUT LLM.

    The AP sheet has rows like:
        Q22 | MQ1HI | TRIED IN L6M | Brand-Wise | MA | ...
        Q15-1 | MQ1A | TOM | CODELIST | OE | ...
        Q15-2/3/4/5 | MQ1B | SPONT | CODELIST | OE | ...

    The datamap has columns like q22_1..q22_17, q15_1..q15_5.

    Connection: strip the 'Q' prefix from the AP table number → lowercase → match against
    column stem (column name with _N suffix stripped).

    Returns: {column_name: {"ap_title": str, "ap_type": str (SA/MA/OE), "q_num": str}}
    for every column that has an AP entry. Columns with no AP entry are absent.
    """
    # Build stem → all sibling columns map
    stem_to_cols: dict = {}
    for col in all_columns:
        stem = re.sub(r"_\d+$", "", str(col))
        stem_to_cols.setdefault(stem, []).append(col)

    # Parse AP rows → {q_num_key: (title, sa_ma_oe, raw_table_no)}
    # First match per q-num wins (Q15-1 before Q15-2/3/4/5 → TOM wins for stem q15)
    ap_entries: dict = {}  # q_num_key → (title, type, raw_no)
    for line in ap_text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or not parts[0] or not parts[2]:
            continue
        raw_no = parts[0].strip().upper()
        title = parts[2].strip()
        sa_ma_oe = parts[4].strip() if len(parts) > 4 else ""
        # Q22 → q22, Q15-1 → q15, Q15-2/3/4/5 → q15 (second entry, won't overwrite)
        m = re.match(r"^Q(\d+)", raw_no)
        if not m:
            continue
        key = f"q{m.group(1)}"
        if key not in ap_entries:
            ap_entries[key] = (title, sa_ma_oe, raw_no)

    # Also build a second-match map for stems with positional OE slots (TOM/SPONT split)
    # Q15-1 → TOM (first match, written above); Q15-2/3/4/5 → SPONT (second match here)
    ap_entries_second: dict = {}
    seen_keys: set = set()
    for line in ap_text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or not parts[0] or not parts[2]:
            continue
        raw_no = parts[0].strip().upper()
        title = parts[2].strip()
        sa_ma_oe = parts[4].strip() if len(parts) > 4 else ""
        m = re.match(r"^Q(\d+)", raw_no)
        if not m:
            continue
        key = f"q{m.group(1)}"
        if key not in seen_keys:
            seen_keys.add(key)
            continue  # skip first match (already in ap_entries)
        if key not in ap_entries_second:
            ap_entries_second[key] = (title, sa_ma_oe, raw_no)

    # Map each column to its AP entry via stem lookup
    # For positional OE families (q15_1=TOM, q15_2..5=SPONT): first column gets first-match title,
    # the rest get second-match title if it exists and the first-match title is "TOM".
    col_map: dict = {}
    for stem, cols in stem_to_cols.items():
        entry = ap_entries.get(stem)
        if not entry:
            continue
        title, sa_ma_oe, raw_no = entry
        second_entry = ap_entries_second.get(stem)

        # Sort columns numerically so position 1 is first
        sorted_cols = sorted(cols, key=lambda c: [int(x) if x.isdigit() else x
                                                   for x in re.split(r"(\d+)", c)])

        for i, col in enumerate(sorted_cols):
            # If this is an OE positional family with TOM/SPONT split
            if second_entry and title.upper() in ("TOM", "ADV. TOM") and sa_ma_oe.upper() == "OE":
                if i == 0:
                    col_map[col] = {"ap_title": title, "ap_type": sa_ma_oe, "q_num": stem}
                else:
                    s2_title, s2_type, _ = second_entry
                    col_map[col] = {"ap_title": s2_title, "ap_type": s2_type, "q_num": stem}
            else:
                col_map[col] = {"ap_title": title, "ap_type": sa_ma_oe, "q_num": stem}

    return col_map


def format_ap_column_map_text(col_map: dict, columns_subset: Optional[list] = None) -> str:
    """Format the pre-linked column→AP-title map as a compact text block for the LLM prompt.

    Groups columns by (q_num, ap_title, ap_type) to avoid repeating the same title
    for every sibling. Output example:
        q22_1..q22_17 [q22] → "TRIED IN L6M" (MA)  ← SKIP: usage recency sub-stage
        q15_1          [q15] → "TOM" (OE)
        q15_2..q15_5   [q15] → "SPONT" (OE) [same Q-stem, 2nd-5th positional slots]
        q17_1..q17_17  [q17] → "TOTAL AIDED AWARENESS" (MA)
    """
    if columns_subset is not None:
        subset_set = set(columns_subset)
        col_map = {k: v for k, v in col_map.items() if k in subset_set}

    if not col_map:
        return ""

    # Group by q_num → collect columns
    from collections import defaultdict
    groups: dict = defaultdict(list)
    meta: dict = {}
    for col, info in col_map.items():
        key = (info["q_num"], info["ap_title"], info["ap_type"])
        groups[key].append(col)
        meta[key] = info

    lines = []
    for key in sorted(groups.keys()):
        q_num, title, ap_type = key
        cols = sorted(groups[key], key=lambda c: [int(x) if x.isdigit() else x
                                                   for x in re.split(r"(\d+)", c)])
        if len(cols) == 1:
            col_str = cols[0]
        elif len(cols) <= 4:
            col_str = ", ".join(cols)
        else:
            col_str = f"{cols[0]}..{cols[-1]} ({len(cols)} cols)"
        lines.append(f"  {col_str:<35} [{q_num}] → \"{title}\" ({ap_type})")

    return "\n".join(lines)


def build_context_packet(ap_text: str, datamap_text: str, data_df: pd.DataFrame,
                          sample_rows: int = 2) -> dict:
    """All columns, a ROW sample (not a column sample) — the LLM needs to see every
    column that exists (to spot multivalent-source + dummy siblings) but doesn't need
    all 900+ respondents to infer structure from.

    `raw_sample_rows` is a list of row ARRAYS (not dicts) — column names are already
    carried once in `raw_sample_columns` and values pair up positionally. This avoids
    repeating every column name as a JSON key on every single row, which for a
    1000+-column real-world file made the dict-per-row form ~72% pure key-name
    overhead before a single data value was counted (observed ballooning a real
    project's context past gpt-4o-mini's 128K token limit). This is a generic win for
    any wide file, not tuned to one project's column count.

    2026-08-03: default lowered 5 -> 2. Even after removing repeated-key overhead, a
    real 1027-column client file's context still exceeded classify_all_questions()'s
    corrected (tokenizer-inflation-adjusted) token budget at sample_rows=5. 2 rows is
    still enough to see per-column value patterns/dummy-sibling structure and was the
    smallest value that got the real measured file under budget alongside the
    Variables-sheet trim in flatten_datamap_to_text.
    """
    sample_df = data_df.head(sample_rows).copy()
    for col in sample_df.columns:
        if pd.api.types.is_datetime64_any_dtype(sample_df[col]):
            sample_df[col] = sample_df[col].astype(str)
    all_cols = list(data_df.columns)
    # Pre-link AP titles to columns so the LLM doesn't have to guess the Q-number→column mapping
    ap_col_map = build_ap_column_map(ap_text, all_cols) if ap_text else {}
    return {
        "ap_text": ap_text,
        "datamap_text": datamap_text,
        "raw_sample_columns": all_cols,
        "raw_sample_rows": sample_df.values.tolist(),
        "ap_col_map": ap_col_map,  # {col_name: {ap_title, ap_type, q_num}}
    }


def flatten_datamap_to_text(path: str, variables_sheet: str = "Variables",
                             values_sheet: str = "Values") -> str:
    """Read every row of both sheets into plain text, one line per row. No judgment
    calls here — this is a dumb dump for the LLM to read; all classification decisions
    happen inside the LLM call, not in this function.

    Uses pandas.read_excel (engine auto-selected by extension, same as
    codebook_parser.py's load_variables_values_datamap) so both legacy .xls and .xlsx
    datamaps work.

    In a Variables/Values datamap the Values sheet only prints the Variable name on the
    FIRST row of each question's value-code block, leaving it blank on subsequent rows —
    forward-fill the first column so every value row stays attributed to its question
    (mirrors load_variables_values_datamap's ffill).
    """
    try:
        xls = pd.ExcelFile(path)
    except Exception as e:
        raise ValueError(f"could not read datamap file '{path}': {e}")

    try:
        sheet_names = xls.sheet_names
        matched = [s for s in (variables_sheet, values_sheet) if s in sheet_names]
        if not matched:
            raise ValueError(
                f"neither '{variables_sheet}' nor '{values_sheet}' sheet found — "
                f"have: {sheet_names}")

        lines = []
        for sheet_name in (variables_sheet, values_sheet):
            if sheet_name not in sheet_names:
                continue
            try:
                df = pd.read_excel(xls, sheet_name=sheet_name)
            except Exception as e:
                raise ValueError(f"could not read sheet '{sheet_name}' in '{path}': {e}")

            if sheet_name == values_sheet and len(df.columns) > 0:
                var_col = df.columns[0]
                df = df.copy()
                df[var_col] = df[var_col].ffill()

            lines.append(f"=== SHEET: {sheet_name} ===")
            lines.append(" | ".join(str(c) for c in df.columns))
            for _, row in df.iterrows():
                cells = [str(v) for v in row.tolist() if pd.notna(v)]
                # SPSS-style Variables sheets carry a title row above the real header, so
                # pandas ends up reading generic "Unnamed: N" column names and the real
                # header ("Variable | Position | Label | Measurement Level | Role | Column
                # Width | Alignment | Print Format | Write Format | Missing Values") becomes
                # the first DATA row instead — meaning we can't select by column name here.
                # Every subsequent row then follows that same Variable/Position/Label/...
                # ordering. Only Variable (cells[0]) and Label (cells[2]) carry information
                # useful for classifying a question's bucket/shape; Position/Measurement
                # Level/Role/Column Width/Alignment/Print Format/Write Format/Missing Values
                # are pure noise for this purpose and, for a real 1000+-variable file, make
                # up the majority of the Variables sheet's character count (measured: ~114K
                # of 172K total datamap chars on a real client file). Keep the fallback of
                # "use everything present" for smaller/simpler sheets (cells has < 3 entries,
                # e.g. this function's own test fixtures use a plain Variable|Label layout)
                # so this never silently drops a Label that IS present.
                if sheet_name == variables_sheet and len(cells) >= 3:
                    cells = [cells[0], cells[2]]
                if cells:
                    lines.append(" | ".join(cells))
        return "\n".join(lines)
    finally:
        xls.close()


import concurrent.futures
from urllib.request import Request, urlopen

_SYSTEM_PROMPT = """You are a survey-data ingestion classifier for brand health market research surveys.
You will be given:
0. A COLUMN → AP TITLE MAP — pre-linked by Python (authoritative, use this FIRST).
   Each line maps one or more raw data column names to the AP table title that defines what
   that question measures. This is computed from the AP sheet without LLM interpretation.
   TREAT THIS AS GROUND TRUTH — it tells you exactly what each column group measures.
1. An analysis plan (AP) describing what each question measures.
2. A datamap (Variables + Values sheets) describing every raw column and its codes.
3. A sample of actual raw data rows, with ALL columns present (not all respondents).
   RAW SAMPLE ROWS is a list of row arrays — column names are NOT repeated per row.
   Each row's values correspond POSITIONALLY to RAW SAMPLE COLUMNS (row[i] is the
   value of column raw_sample_columns[i]).

============================================================
STEP 1 — READ THE COLUMN → AP TITLE MAP FIRST
============================================================
Before anything else, read the "COLUMN → AP TITLE MAP" section. For each column group listed
there, the AP title tells you EXACTLY what that question measures. Apply the decision tree in
Step 2 to the AP title to get the bucket — you do NOT need to re-read the AP or datamap for
those columns. Only use the AP/datamap as supplementary context for columns NOT in the map.

============================================================
STEP 2 — COUNT YOUR STEMS (do this after reading the map)
============================================================
Count the distinct question STEMS in RAW SAMPLE COLUMNS (a stem = column code with any trailing
_<integer> suffix stripped: q17_1, q17_2, q17_17 are all stem "q17"). Your output MUST contain
exactly that many questions (one per stem). Use bucket=SKIP for anything irrelevant — never omit.

============================================================
STEP 3 — FOR EACH STEM, RUN THIS DECISION TREE (in order, stop at first match)
============================================================

A. DEMOGRAPHICS — does the question ask about the RESPONDENT THEMSELVES (not any brand)?
   → gender/sex                          → GENDER
   → age / age group / age band          → AGE
   → city / town / location              → CITY  (MANDATORY — even if options are a city list)
   → state / region / zone               → ZONE  (MANDATORY — even if options are zone names)
   → income / occupation / NCCS / SEC / marital status / education → DEMOGRAPHIC
   → how long residing / household size  → DEMOGRAPHIC
   If YES to any above → assign that bucket, skip remaining steps.
   ⚠️ CITY and ZONE are NOT brand-health questions, but they ARE required for analytics
   filtering. You MUST assign CITY/ZONE. Never SKIP a city or zone question.

B. SCREENER / ELIGIBILITY — does the question filter who qualifies, or ask about category usage
   WITHOUT naming specific brands? Examples: "Do you shop online?", "Which dairy products have
   you bought?", "Do you own a mixer-grinder?", "How often do you buy packaged milk?"
   → SKIP. These are quota/screener questions. Do NOT use any funnel bucket for these.
   If YES → SKIP, skip remaining steps.

C. AD / MEDIA AWARENESS — does the question ask about ADVERTISEMENTS or MEDIA CHANNELS?
   Examples: "Which brands have you seen ads for?", "Which media encouraged you to try [Brand]?",
   "Where did you see the ad?", "Advertised brand recall.",
   "Select milk brands that you have seen or heard this advertisement of."
   Keywords: advertisement, advertise, ad, media, seen/heard ad, TVC, hoarding, campaign.
   → SKIP. Ad awareness is NOT part of the brand awareness funnel (TOM/SPONT/AIDED measure
   brand recall, not ad recall). NEVER use AIDED for ad awareness — it must be SKIP.
   If YES → SKIP, skip remaining steps.

D. BRAND FUNNEL — does the question ask about BRAND AWARENESS or USAGE, naming specific brands?
   Use the sub-question: "at what stage of the funnel?"
   → first/only brand named unprompted (open-end, one brand)        → TOM
   → brands named unprompted (open-end, multiple brands)            → SPONT
   → brands recognised from a SHOWN LIST                            → AIDED
   → brands ever tried / ever used                                  → EVER_USED
   → brands currently using / used in last 1-6 months               → CURRENT_USER
   → brands ever considered / would consider                        → CONSIDERATION
   → most preferred / most often used brand                         → PREFERRED
   → brand most recently purchased / last bought (SINGLE-SELECT)    → LAST_PURCHASED
   → categories / product lines associated with a brand             → PORTFOLIO_AWARENESS
   If YES to any above → assign funnel bucket, skip remaining steps.

   ⚠️ TOM vs SPONT — POSITIONAL RECALL COLUMNS:
   Open-recall questions ("name all brands you know") produce a FAMILY of positional columns:
     q15_1 = first brand named = TOM
     q15_2, q15_3, q15_4, q15_5 = subsequent brands = SPONT (each one)
   You MUST emit a SEPARATE entry for EACH positional column in the family:
     q15_1 → bucket=TOM, source_column=q15_1
     q15_2 → bucket=SPONT, source_column=q15_2
     q15_3 → bucket=SPONT, source_column=q15_3  ... and so on.
   Do NOT collapse the whole family into a single TOM entry.

   ⚠️ CURRENT_USER vs LAST_PURCHASED vs SKIP (usage recency):
   Surveys often ask the SAME question across multiple time windows. ONLY the MOST RECENT window
   becomes CURRENT_USER. All other time windows → SKIP. Exact rules:
     "last 1 month" / "currently consuming" / "current brand" (MULTI-SELECT) → CURRENT_USER
     "last 3 months" (MULTI-SELECT)  → SKIP  ← NOT CURRENT_USER even though it looks similar
     "last 6 months" (MULTI-SELECT)  → SKIP  ← NOT CURRENT_USER even though it looks similar
   If a survey has q22 (L6M), q23 (L3M), q24 (L1M): only q24 → CURRENT_USER; q22+q23 → SKIP.
   "Which single brand did you LAST buy / most recently purchase?" (SINGLE-SELECT, one answer) → LAST_PURCHASED
   Key signal: if multiple brands can be selected → CURRENT_USER (only the shortest window); if only one brand → LAST_PURCHASED.

E. BRAND IMAGERY (association battery) — does the question ask "does BRAND X fit attribute Y?"
   or "select brands that match this description"? This includes:
   - Multi-brand grids: "Which of these brands [list] cares for farmers?" (respondent ticks brands)
   - SINGLE-brand batteries: "Is [specific brand] a brand that cares for farmers? Yes/No or 0/1"
     ← SINGLE-BRAND IS STILL BRAND_IMAGERY. If a brand name is in the question text AND the
     answer is about whether that brand has an attribute → BRAND_IMAGERY, NOT IMPORTANCE.
   → BRAND_IMAGERY
   If YES → BRAND_IMAGERY, skip remaining steps.

F. PURCHASE JOURNEY — does the question ask about a NAMED BRAND's purchase behavior OR channel?
   Examples:
     "Where do you buy [Brand]?"              → PURCHASE_JOURNEY
     "Why did you choose [Brand]?"            → PURCHASE_JOURNEY
     "Who decided to buy [Brand]?"            → PURCHASE_JOURNEY
     "[Brand]: Stall / Canopy activation?"    → PURCHASE_JOURNEY  ← activation channel grids
     "How did you learn about [Brand]?"       → PURCHASE_JOURNEY
     "[Brand]: TV Ad / Digital / Word of mouth?" → PURCHASE_JOURNEY ← media touchpoint per brand
   ALSO include brand × channel grid headers (e.g. column "q31_1_1" = "AK Organic: Stall/Canopy
   in society") — these are activation/touchpoint batteries, always PURCHASE_JOURNEY.
   GENERIC without brand: "Where do you buy milk?" (no brand named) → SKIP, NOT PURCHASE_JOURNEY.
   If YES → PURCHASE_JOURNEY, skip remaining steps.

G. PRICE — does the question ask about price paid or price tier for a product/brand?
   → PRICE_PAID
   If YES → PRICE_PAID, skip remaining steps.

H. SATISFACTION / RECOMMENDATION — does the question ask "how satisfied are you?" or "how likely
   to recommend?"
   → "recommend" / NPS wording       → NPS
   → "satisfied" / "experience"      → CSAT
   If YES → NPS or CSAT, skip remaining steps.

I. IMPORTANCE BATTERY — does the question ask "how IMPORTANT is attribute X to you when choosing
   a brand?" with NO specific brand named, on a numeric RATING SCALE (1-5 or 1-7)?
   KEY TEST — ALL THREE must be true:
     1. Subject = abstract attribute (freshness, trust, value-for-money) — NOT a brand name
     2. No specific brand named anywhere in question text or answer options
     3. Answer options are a NUMERIC SCALE (1-5 or 1-7) — NOT text choices, NOT channel names
   → IMPORTANCE
   NOT IMPORTANCE: "How important is [Brand] to you?" = ATTITUDE (brand named)
   NOT IMPORTANCE: "Is [Brand] fresh?" = BRAND_IMAGERY (brand named + attribute association)
   NOT IMPORTANCE: "Which media was most important in encouraging you to try [Brand]?" = SKIP
   NOT IMPORTANCE: "Reasons you have not tried [Brand]?" (multi-select text reasons) = ATTITUDE
   NOT IMPORTANCE: "[Brand]: Stall/Canopy?" (channel options) = PURCHASE_JOURNEY
   NOT IMPORTANCE: "[Brand name as question text]" (grid header, no question verb) = PURCHASE_JOURNEY
   ⚠️ IMPORTANCE is RARE — most real surveys include it only as a dedicated bq3a-style battery.
      If you cannot confirm a numeric 1-7/1-5 rating scale on abstract attributes with no brand
      name, do NOT assign IMPORTANCE. Use ATTITUDE or PURCHASE_JOURNEY instead.
   If YES to ALL THREE → IMPORTANCE, skip remaining steps.

J. ATTITUDE — does the question ask for agreement/feeling/opinion about a BRAND or CATEGORY
   (not a funnel stage, not imagery, not importance)?
   Examples:
     "How much do you love [Brand]?"                         → ATTITUDE
     "I believe organic milk is healthier" (Likert)          → ATTITUDE
     "Reasons you have NOT tried [Brand]" (multi-select)     → ATTITUDE  ← barriers are ATTITUDE
     "Reasons you have NOT switched to [Brand]"              → ATTITUDE
     "Rate [Brand] on [dimension]" (brand named)             → ATTITUDE
   ⚠️ "Reasons for not trying" / barrier questions are ALWAYS ATTITUDE, never IMPORTANCE.
   If YES → ATTITUDE, skip remaining steps.

K. Everything else → SKIP.

============================================================
STEP 3 — WORKED EXAMPLES (few-shot reference for the most confused cases)
============================================================

EXAMPLE 1 — Single-brand imagery battery (BRAND_IMAGERY, NOT IMPORTANCE):
  Question: "Is Akshayakalpa Organic a brand that cares for farmers wellbeing?"
  Raw columns: q34_3 (0/1 per respondent, only for Akshayakalpa)
  → bucket=BRAND_IMAGERY, shape=multi_select_dummies
  WHY: A brand name appears (Akshayakalpa). The question asks if THAT BRAND has an attribute.
  WRONG: bucket=IMPORTANCE ← would be wrong because IMPORTANCE never names a brand.

EXAMPLE 2 — Brand love rating (ATTITUDE, NOT IMPORTANCE):
  Question: "How much do you love Akshayakalpa Organic?" (scale 1-5)
  Raw column: q26_1 (numeric score per respondent)
  → bucket=ATTITUDE, shape=numeric_score
  WHY: Rating of love/affection FOR A NAMED BRAND. Not about attribute importance.
  WRONG: bucket=IMPORTANCE ← would be wrong; IMPORTANCE is about abstract attribute ratings.

EXAMPLE 3 — True importance battery (IMPORTANCE):
  Question: "How important is freshness to you when choosing a milk brand?" (scale 1-7)
  Raw column: imp_1 (numeric score, no brand named)
  → bucket=IMPORTANCE, shape=numeric_score
  WHY: Abstract attribute (freshness), respondent's OWN purchase criteria, no brand named.

EXAMPLE 4 — Media influence (SKIP, NOT IMPORTANCE and NOT PURCHASE_JOURNEY):
  Question: "Which ONE media most encouraged you to try Akshayakalpa?" (SA)
  Raw column: mq1m (single value: TV/Digital/OOH/etc.)
  → bucket=SKIP, shape=single_value
  WHY: Media channel question. Not a funnel stage, not an imagery association, not attribute importance.

EXAMPLE 5 — Ad awareness (SKIP, NOT TOM/SPONT/AIDED):
  Question: "Which milk brands have you seen or heard advertisements for?"
  → bucket=SKIP
  WHY: Ad RECALL ≠ brand AWARENESS. TOM/SPONT/AIDED measure brand recall, not ad recall.

EXAMPLE 6 — Category screener (SKIP):
  Question: "Which of these dairy products have you bought in the last one year?" (no brands listed)
  → bucket=SKIP
  WHY: Category-level purchase behavior; no specific brand in options = screener/quota question.

EXAMPLE 7 — Multi-brand imagery grid (BRAND_IMAGERY):
  Question: "Select all milk brands that you feel are organic / natural" (list of 15 brands)
  Raw columns: q34_1 (delimited "1 4 7") + q34_1_1, q34_1_2, ..., q34_1_15 (one-hot dummies)
  → bucket=BRAND_IMAGERY, shape=multivalent_source, source_column=q34_1,
    dummy_columns=[q34_1_1, q34_1_2, ..., q34_1_15]

EXAMPLE 8 — Brand × channel activation grid (PURCHASE_JOURNEY, NOT IMPORTANCE):
  Question: "Akshayakalpa Organic: Stall/Canopy in my society / Apartment Stall Activities?"
  Raw column: q31_1_1 (0/1 per respondent)
  → bucket=PURCHASE_JOURNEY, shape=multi_select_dummies
  WHY: A named brand (AK Organic) paired with a distribution/activation channel (Stall/Canopy).
  WRONG: bucket=IMPORTANCE ← IMPORTANCE requires abstract attribute + numeric 1-7 scale, no brand.

EXAMPLE 9 — Barrier / reasons for not trying (ATTITUDE, NOT IMPORTANCE):
  Question: "Please select all reasons from the listed below options for not trying Akshayakalpa"
  Raw columns: q66_1, q66_2, ... (multi-select dummies: reasons like "too expensive", "not available")
  → bucket=ATTITUDE, shape=multi_select_dummies
  WHY: Asking for opinion/barrier reasons about a specific brand. Multi-select text options, not a scale.
  WRONG: bucket=IMPORTANCE ← IMPORTANCE is a numeric rating scale on abstract attributes, no brand named.

EXAMPLE 10 — Grid header column (SKIP or PURCHASE_JOURNEY based on sibling columns):
  Question text: "Akshayakalpa Organic" (literally just a brand name, no verb or question)
  Raw column: q26_1 (numeric or 0/1 score)
  → If siblings like q26_1_1, q26_1_2 have channel/activation answer options → PURCHASE_JOURNEY
  → If this is a standalone score column → ATTITUDE (brand satisfaction/rating)
  → If cannot determine from context → SKIP
  WRONG: bucket=IMPORTANCE ← a bare brand name as question text is never an importance battery.

EXAMPLE 11 — Open-recall brand sequence (TOM + SPONT family, NOT a single TOM entry):
  Question: "Q15. Please name all milk brands you are aware of."
  Raw columns in data: q15_1, q15_2, q15_3, q15_4, q15_5 (one brand name per column, position order)
  → emit FIVE separate entries:
      q15_1 → bucket=TOM,  shape=single_value, source_column=q15_1
      q15_2 → bucket=SPONT, shape=single_value, source_column=q15_2
      q15_3 → bucket=SPONT, shape=single_value, source_column=q15_3
      q15_4 → bucket=SPONT, shape=single_value, source_column=q15_4
      q15_5 → bucket=SPONT, shape=single_value, source_column=q15_5
  WRONG: emitting only one entry for q15_1 → TOM and IGNORING q15_2..q15_5 entirely.
  WHY: Each subsequent brand mention is a spontaneous (unprompted) recall — SPONT stage.

EXAMPLE 12 — CURRENT_USER (multi-select last-month) vs LAST_PURCHASED (single-select):
  Question A: "Q24. Which milk brands have you consumed in the last 1 month? MA"
  Raw columns: q24, q24_1, q24_2, ... q24_18 (respondent can tick multiple brands)
  → bucket=CURRENT_USER (multi-select, multiple brands possible, recent usage window)
  Question B: "Q25. Of the milk you consume most often, select your brand. SA"
  Raw column: q25 (single answer, one brand code)
  → bucket=PREFERRED (single-select most-often)
  Question C: "Which brand of milk did you purchase most recently?" (single answer, one brand)
  → bucket=LAST_PURCHASED
  WHY: LAST_PURCHASED = single-select "the ONE brand you last bought." Multi-select last-N-months = CURRENT_USER.

============================================================
STEP 4 — FIELD DEFINITIONS (assign both for every question)
============================================================

FIELD 1: `bucket` — one of exactly 22 values from Step 2 above. WHAT the question measures.
  CRITICAL demographics: CITY not DEMOGRAPHIC; GENDER not DEMOGRAPHIC; ZONE not DEMOGRAPHIC.
  CRITICAL text: question_text must contain the FULL attribute/statement label from the datamap,
  never just the raw column code (e.g. "q34_1"). Pull the label text from the Variables sheet.

FIELD 2: `shape` — HOW the answer is structured in the raw data file. One of exactly 4 values:
  - multivalent_source: ONE delimited column holds all selections (e.g. cell value "1 4 7").
    Sibling one-hot dummies exist but are INFORMATIONAL ONLY — never target them as source_column.
  - single_value: one column, one plain text or code value per respondent.
  - numeric_score: one column, a number on a scale (0-10 NPS, 1-5 CSAT, 1-7 importance, etc.)
  - multi_select_dummies: NO parent delimited column exists at all — only independent 0/1 columns
    (e.g. q67_1, q67_2, ...) each flagging one option.

  HOW TO DISTINGUISH multivalent_source vs multi_select_dummies:
  Check RAW SAMPLE ROWS for a column whose values look like "1 4 7" or "2;5;9" (multiple codes
  in one cell). If such a column exists → multivalent_source, source_column = that column.
  If only 0/1 dummy columns exist and NO combined column → multi_select_dummies.

  RULE: NEVER emit shape=multi_select_dummies with an empty dummy_columns array. If you cannot
  list at least one real column from RAW SAMPLE COLUMNS in dummy_columns, use single_value instead.

  bucket and shape are INDEPENDENT. Valid combinations include:
    bucket=AIDED + shape=multivalent_source  (a recognition question with a delimited column)
    bucket=ATTITUDE + shape=multi_select_dummies  (a "select all reasons" attitude question)
    bucket=BRAND_IMAGERY + shape=multi_select_dummies  (single-brand attribute battery, 0/1 cols)
    bucket=IMPORTANCE + shape=numeric_score  (importance rating scale)
  NEVER put a shape value (multivalent_source/single_value/numeric_score/multi_select_dummies)
  into the bucket field, and NEVER put a bucket value into the shape field.

============================================================
STEP 5 — OUTPUT RULES
============================================================

- source_column: the ONE real column to read.
  For multivalent_source = the delimited parent column (e.g. q17 that holds "1 4 7 11" per cell).
  For multi_select_dummies = the FIRST binary dummy column (e.g. q17_1, NOT the stem "q17").
    The ingest engine reads all siblings from q17_1 onward — it does NOT need you to list all
    dummy_columns, but source_column must be a real column that exists in RAW SAMPLE COLUMNS.
- code_to_label: map each raw code to its brand/attribute name from the datamap. Required for
  any question whose bucket implies brand or attribute identity (funnel stages, BRAND_IMAGERY, etc.)
- confidence: 0.0-1.0. reasoning: one sentence citing what you saw in AP/datamap/raw sample.
- DEDUPLICATION: each question_code EXACTLY ONCE. If the same code appears under multiple
  sections of the datamap, emit it once under the bucket that best fits its primary purpose.
- Return ONLY a JSON object: {"project_id": "...", "questions": [...]}. No schema wrapper.
  Do NOT return the schema itself (no "title", "type", "properties" keys at top level).

============================================================
STEP 6 — PRE-RETURN VERIFICATION (do this before sending your answer)
============================================================
Before finalising your JSON response, check ALL of the following:
□ Count questions in your array — does it match the stem count you derived in Step 1? If under,
  you missed questions. Go back and add them with bucket=SKIP if nothing else applies.
□ Any question_code appearing more than once? → remove the duplicate, keep highest-confidence.
□ Any bucket field containing a shape value (multi_select_dummies/multivalent_source/etc.)? → fix.
□ Any shape=multi_select_dummies entry with empty dummy_columns? → downgrade to single_value.
□ Any brand-imagery or funnel question with empty code_to_label? → fill from datamap Values sheet.
□ Any IMPORTANCE question that has a brand name in the question_text? → reclassify as ATTITUDE
  or BRAND_IMAGERY using the decision tree in Step 2.
□ Any TOM/SPONT/AIDED question that is about AD awareness rather than BRAND awareness? → SKIP.
Only send your response after all boxes pass.

============================================================
QUESTION CODE RULES (strictly enforced)
============================================================
- question_code MUST be the STEM of a column in RAW SAMPLE COLUMNS.
  STEM = column name with any trailing _<integer> suffix stripped.
  Examples: "q64_1" → stem "q64". "q15_3" → stem "q15". "q34_1_5" → stem "q34_1".
  NEVER invent semantic names like "TOM", "SPONT", "AIDED", "NPS" as question_code values.
  The question_code is a column STEM, not a bucket label.
- When a batch sends you "q64_1" as representative of a battery of per-brand columns:
  → question_code = "q64" (strip the trailing _1), source_column = "q64_1"
- For spontaneous recall columns (e.g. q15_1, q15_2, q15_3 = 1st/2nd/3rd brand named unprompted):
  → Emit ONE entry PER positional column:
      q15_1 → question_code="q15_1", bucket=TOM,  source_column="q15_1"
      q15_2 → question_code="q15_2", bucket=SPONT, source_column="q15_2"
      q15_3 → question_code="q15_3", bucket=SPONT, source_column="q15_3"  ...etc
  → question_code must be the exact column name (not the stem) since each column is a DIFFERENT entry.
  → NEVER use "TOM" or "SPONT" as the question_code — those are bucket values, not question codes.
  → NEVER collapse all positional columns into one entry.
- For aided awareness (a list shown, respondent selects from it):
  → bucket = AIDED (not SPONT — the brands were SHOWN, not recalled unprompted)

============================================================
EXHAUSTIVE COVERAGE IS MANDATORY
============================================================
You MUST classify EVERY distinct question stem in the datamap. A response covering only a fraction
of the survey is USELESS and will be rejected. Work through the ENTIRE datamap stem by stem.
Use bucket=SKIP for irrelevant questions — NEVER simply omit them.

"""


import re

# 2026-08-03: found via a live Akshayakalpa UI run — gpt-4o-mini returned only 10 questions out
# of a real ~300-question survey (TOM/SPONT/AIDED/PREFERRED/NPS/CSAT/BRAND_IMAGERY entirely
# missing), with no error anywhere. A fast/non-reasoning model given a huge dense context and a
# "classify these" instruction will often produce a plausible-looking PARTIAL answer instead of
# exhaustively covering everything, unless explicitly told the exact completeness bar and given a
# post-hoc check that catches it when it still undercounts. `_MIN_STEMS_FOR_COMPLETENESS_CHECK`
# guards tiny test fixtures (a datamap that genuinely only has 1-4 real questions) from tripping
# this — below that floor the check is skipped entirely since "50% of 2" is not a meaningful bar.
# 2026-08-03: the `shape` enum's exact value set (kept in sync with
# ingestion_mapping.schema.json's shape enum) — used by the field-swap detection below to
# recognize when the LLM has put a shape value into the `bucket` field by mistake.
_SHAPE_ENUM_VALUES = {"multivalent_source", "single_value", "numeric_score", "multi_select_dummies"}

_MIN_STEMS_FOR_COMPLETENESS_CHECK = 5
# 15% threshold: batteries collapse many columns to one question (e.g. SPONT 1/2/3/4 → SPONT),
# so returned question count is legitimately much lower than stem count. The threshold guards
# against models that return 0-5 questions for a 100-stem survey (a genuine failure mode seen
# in testing), while not failing on well-structured surveys with heavy batteries.
_COMPLETENESS_MIN_FRACTION = 0.15


def _distinct_question_stems(raw_sample_columns: list) -> set:
    """Derives the set of distinct question STEMS from a list of raw column names — a stem is a
    column name with any trailing "_<int>" one-hot-dummy suffix stripped (q17_1, q17_2, ...,
    q17_17 all collapse to stem "q17"). This is a conservative, generic proxy for "how many real
    survey questions actually exist in this file" that doesn't require re-parsing the datamap's
    prose format — raw_sample_columns is already an explicit, unambiguous list of every real
    column, which is exactly what the LLM was given to classify from.
    """
    stems = set()
    for col in raw_sample_columns:
        stem = re.sub(r"_\d+$", "", str(col))
        stems.add(stem)
    return stems


# 2026-08-03: measured against a real 1027-column client file — dense JSON/tabular payloads
# (repeated quotes/colons/commas, short numeric values) tokenize far less efficiently than prose;
# chars/4 underestimates real OpenRouter tokenization by ~2.35x for this kind of content. The
# guard below corrects for this so a "100K chars/4 estimate" packet doesn't silently arrive at
# the provider as ~235K real tokens.
_CHARS_PER_4_TO_REAL_TOKEN_INFLATION = 2.35


def _classify_with_single_model(packet: dict, project_id: str, api_key: str, model: str,
                                 timeout: int, schema: dict, user_content: str,
                                 skip_completeness_check: bool = False) -> dict:
    """The actual one-model call + full validation/recovery pipeline (HTTP call, schema-echo
    unwrap, project_id injection, question_text default-fill, bucket/shape field-swap detection,
    multi_select_dummies downgrade, schema validation, completeness guard). Raises RuntimeError
    on any failure — callers (classify_all_questions' fallback loop) catch this to try the next
    model in the list. Split out of classify_all_questions() so the model-fallback loop
    (2026-08-03) can retry this whole pipeline per-model without duplicating it.

    skip_completeness_check: set True when called from the auto-batching path. Batches send only
    ONE representative column per stem, so the model intelligently collapses battery questions
    (e.g. SPONT 1/2/3/4 → one SPONT entry) and the returned count legitimately < #stems.
    The aggregate completeness check in classify_all_questions() is the real gate for batches.
    """
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        # Without an explicit max_tokens, OpenRouter defaults vary by model (often 4096),
        # which truncates large surveys mid-JSON. A real 157-question survey needs ~14K
        # output tokens (157 stems × ~90 tokens each). Set 32000 so any model that
        # supports it gets enough room; OpenRouter caps per the model's own ceiling.
        "max_tokens": 32000,
        "response_format": {
            "type": "json_schema",
            # NOTE: no "strict": True here. Strict mode only supports a restricted JSON
            # Schema subset (no allOf/if/then/else), and ingestion_mapping.schema.json
            # relies on allOf/if/then for its conditional-required rules (Task 1). The
            # local jsonschema.validate() call below is the real validation gate.
            "json_schema": {"name": "ingestion_mapping", "schema": schema},
        },
    }).encode()

    req = Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    # NOT a context manager here on purpose: `with ThreadPoolExecutor()` calls
    # shutdown(wait=True) on exit, which would block until the stuck worker thread finishes
    # anyway — defeating the timeout below. Let the thread leak (it's a single urlopen call
    # that will eventually error out or complete and get garbage-collected); we just stop
    # waiting on it.
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        raw = ex.submit(lambda: urlopen(req, timeout=timeout).read()).result(timeout=timeout)
    except Exception as e:
        raise RuntimeError(
            f"classify_all_questions: HTTP call failed for project '{project_id}' with model "
            f"'{model}': {e}") from e
    finally:
        ex.shutdown(wait=False)

    try:
        outer = json.loads(raw)
        content = outer["choices"][0]["message"]["content"]
    except Exception as e:
        snippet = raw[:500] if isinstance(raw, (str, bytes)) else str(raw)[:500]
        raise RuntimeError(
            f"classify_all_questions: could not extract message content from response "
            f"for project '{project_id}' with model '{model}': {e}. Raw response (truncated): "
            f"{snippet!r}") from e

    try:
        result = json.loads(content)
    except Exception as e:
        snippet = str(content)[:500]
        raise RuntimeError(
            f"classify_all_questions: LLM content was not valid JSON for project "
            f"'{project_id}' with model '{model}': {e}. Content (truncated): {snippet!r}") from e

    # 2026-08-03: found via a live Akshayakalpa UI run — the LLM returned the JSON SCHEMA's own
    # structure (title/type/properties) as the top-level object instead of a data instance, with
    # the real classification data nested one level down inside result["properties"]. This
    # happens because we pass the schema itself in response_format.json_schema.schema, and
    # without "strict": True (incompatible with this schema's allOf/if/then rules — see the NOTE
    # near the request body below), a non-frontier model can echo back schema-shaped output
    # instead of an instance matching it. Prompt guidance alone proved insufficient for this
    # model in the live run that surfaced it, so recover defensively here too: if `questions` is
    # missing at the top level but present one level down inside `properties`, unwrap it. This
    # only triggers in this exact schema-echo shape — a normally-shaped response (questions
    # already at the top level) is untouched.
    if "questions" not in result and isinstance(result.get("properties"), dict) \
            and "questions" in result["properties"]:
        result = result["properties"]

    # 2026-08-03: found via a live Akshayakalpa UI run — the LLM sometimes omits the top-level
    # `project_id` field entirely (a required schema property), failing validation for no good
    # reason: classify_all_questions already receives project_id as a parameter, so there's no
    # reason to trust (or even require) the LLM to echo it back correctly. Inject the caller's
    # authoritative value unconditionally, overwriting whatever the LLM did or didn't put there —
    # removes an entire class of failure with zero downside.
    result["project_id"] = project_id

    # Guard: if qwen or another model returns non-dict items in the questions list (e.g. floats
    # or strings), remove them early so all downstream `for q in result["questions"]` loops don't
    # crash with "argument of type 'float' is not iterable" when doing `"key" not in q`.
    if isinstance(result.get("questions"), list):
        result["questions"] = [q for q in result["questions"] if isinstance(q, dict)]

    # 2026-08-03: found via a live Akshayakalpa UI run — one question in a large, otherwise-good
    # response omitted `question_text` entirely, failing validation over a purely descriptive/
    # display-only field (shown in the review table, never read by assignment_from_schema or
    # load_confirmed_assignment's DB-writing logic — see generic_loader.py). Now optional in the
    # schema (see ingestion_mapping.schema.json's items.required); default-fill it here from the
    # question_code so the review table always shows something non-empty rather than a blank.
    for q in result.get("questions", []):
        if not q.get("question_text"):
            q["question_text"] = q.get("question_code", "")

    # Field-swap detection (2026-08-03): found via a live Akshayakalpa UI run — gpt-4o-mini put
    # 'multi_select_dummies' (a shape-enum value) into the `bucket` field for one question,
    # confusing the two separate enums. jsonschema.validate() below would eventually reject this
    # too, but with a raw "'multi_select_dummies' is not one of [...22 bucket values...]" error
    # that gives no hint WHY — raise a clearer, more actionable RuntimeError here instead, naming
    # the actual likely cause, before the opaque schema error fires.
    for q in result.get("questions", []):
        if q.get("bucket") in _SHAPE_ENUM_VALUES:
            raise RuntimeError(
                f"classify_all_questions: question '{q.get('question_code')}' has bucket="
                f"'{q.get('bucket')}' — that is a SHAPE-enum value, not a valid bucket value. "
                f"The LLM likely swapped the bucket/shape fields for this question. "
                f"bucket must be one of the 22 funnel-stage/topic values; shape must be one of "
                f"{sorted(_SHAPE_ENUM_VALUES)}."
            )

    # Defensive downgrade (2026-08-03): found via a live Akshayakalpa UI run — gpt-4o-mini
    # sometimes picks shape='multi_select_dummies' without actually having found real dummy
    # columns to back it up (empty dummy_columns), which the schema correctly rejects via
    # minItems. Rather than hard-failing the ENTIRE batch (potentially hundreds of otherwise-good
    # questions) over one question's shape mistake, downgrade that single question to
    # 'single_value' (the closest safe fallback — one column, one value) and record a warning on
    # the result so the caller can surface it, instead of raising deep inside jsonschema.validate.
    downgrade_warnings = []
    for q in result.get("questions", []):
        if q.get("shape") == "multi_select_dummies" and not q.get("dummy_columns"):
            downgrade_warnings.append(
                f"question '{q.get('question_code')}': shape 'multi_select_dummies' had an "
                f"empty dummy_columns array — downgraded to 'single_value' (LLM picked this "
                f"shape without identifying real dummy columns)."
            )
            q["shape"] = "single_value"
            q["delimiter"] = ""
    if downgrade_warnings:
        result["_downgrade_warnings"] = downgrade_warnings

    # Defensive invalid-bucket downgrade (2026-08-03): found via live Akshayakalpa UI run —
    # llama-3.3-70b returned bucket='DAIRY_PRODUCTS' (not a valid 22-value bucket enum). Rather
    # than failing the whole batch over one hallucinated bucket name, downgrade to SKIP so
    # load_confirmed_assignment never sees it and the human reviewer is notified.
    _VALID_BUCKETS = {
        "TOM", "SPONT", "AIDED", "EVER_USED", "CURRENT_USER", "CONSIDERATION",
        "PREFERRED", "LAST_PURCHASED", "CSAT", "NPS", "BRAND_IMAGERY", "IMPORTANCE",
        "ATTITUDE", "PORTFOLIO_AWARENESS", "PRICE_PAID", "PURCHASE_JOURNEY",
        "GENDER", "AGE", "CITY", "ZONE", "DEMOGRAPHIC", "SKIP",
    }
    invalid_bucket_warnings = []
    for q in result.get("questions", []):
        b = q.get("bucket")
        if b and b not in _VALID_BUCKETS:
            invalid_bucket_warnings.append(
                f"question '{q.get('question_code')}': bucket='{b}' is not a recognised "
                f"bucket — downgraded to SKIP (LLM hallucinated an unknown bucket name)."
            )
            q["bucket"] = "SKIP"
    if invalid_bucket_warnings:
        result.setdefault("_downgrade_warnings", []).extend(invalid_bucket_warnings)

    # Defensive code_to_label warning (2026-08-03): found via live Akshayakalpa UI run — all 4
    # models returned brand-identity questions (TOM/SPONT/AIDED/etc.) with missing or empty
    # code_to_label, causing schema validation to fail the whole batch. The schema's allOf/if/then
    # constraint was relaxed (minProperties:1 removed) so validation no longer hard-blocks here,
    # but warn explicitly so the human reviewer knows which questions need manual code→label
    # mapping and can add them in the review table or via the manual assignment section.
    _BRAND_IDENTITY_BUCKETS = {
        "TOM", "SPONT", "AIDED", "EVER_USED", "CURRENT_USER", "CONSIDERATION",
        "PREFERRED", "BRAND_IMAGERY", "PORTFOLIO_AWARENESS",
    }
    code_to_label_warnings = []
    for q in result.get("questions", []):
        if q.get("bucket") in _BRAND_IDENTITY_BUCKETS and not q.get("code_to_label"):
            code_to_label_warnings.append(
                f"question '{q.get('question_code')}' (bucket={q.get('bucket')}): "
                f"code_to_label is missing or empty — brand/attribute names will NOT resolve "
                f"unless added manually in the review table below."
            )
            if "code_to_label" not in q:
                q["code_to_label"] = {}
    if code_to_label_warnings:
        result.setdefault("_downgrade_warnings", []).extend(code_to_label_warnings)

    # ── Heuristic post-classify guards (2026-08-06) ──────────────────────────────────────────────
    # These catch systematic LLM misclassifications that survive prompt-level instructions.
    # Each guard fires silently and records a warning — never hard-fails the batch.
    import re as _re

    # CHANNEL/ACTIVATION keywords that signal PURCHASE_JOURNEY, not IMPORTANCE.
    _CHANNEL_PATTERNS = [
        r"\bstall\b", r"\bcanopy\b", r"\bactivation\b", r"\bword.of.mouth\b",
        r"\bhoarding\b", r"\bbillboard\b", r"\bdigital\b", r"\btelevision\b",
        r"\bprocure\b", r"\bwhere.*buy\b", r"\bdiscovery\b", r"\btouchpoint\b",
        r"\bbrand.*ambassador\b",
    ]
    # BARRIER/REASONS keywords that signal ATTITUDE, not IMPORTANCE.
    _BARRIER_PATTERNS = [
        r"\bnot.tried\b", r"\bnot.try\b", r"\bhaven.t.tried\b", r"\bnever.tried\b",
        r"\breasons?.for.not\b", r"\bbarrier\b", r"\bwhy.not\b",
    ]
    # IMPORTANCE is ONLY valid if shape=numeric_score AND no brand name in question text.
    # Heuristic: if a question is bucket=IMPORTANCE but shape != numeric_score, reclassify.
    heuristic_warnings = []
    for q in result.get("questions", []):
        bucket = q.get("bucket", "")
        qt     = (q.get("question_text") or "").casefold()

        # Guard 1: IMPORTANCE with non-numeric shape → ATTITUDE (most likely a multi-select battery)
        if bucket == "IMPORTANCE" and q.get("shape") != "numeric_score":
            q["bucket"] = "ATTITUDE"
            heuristic_warnings.append(
                f"question '{q.get('question_code')}': IMPORTANCE→ATTITUDE "
                f"(shape={q.get('shape')}, not numeric_score — real importance batteries are always 1-7 scale)"
            )

        # Guard 2: IMPORTANCE with channel/activation keywords in question text → PURCHASE_JOURNEY
        elif bucket == "IMPORTANCE" and any(
            _re.search(pat, qt) for pat in _CHANNEL_PATTERNS
        ):
            q["bucket"] = "PURCHASE_JOURNEY"
            heuristic_warnings.append(
                f"question '{q.get('question_code')}': IMPORTANCE→PURCHASE_JOURNEY "
                f"(channel keyword found in question text: '{qt[:80]}')"
            )

        # Guard 3: IMPORTANCE with barrier/not-tried keywords → ATTITUDE
        elif bucket == "IMPORTANCE" and any(
            _re.search(pat, qt) for pat in _BARRIER_PATTERNS
        ):
            q["bucket"] = "ATTITUDE"
            heuristic_warnings.append(
                f"question '{q.get('question_code')}': IMPORTANCE→ATTITUDE "
                f"(barrier/not-tried keyword in question text: '{qt[:80]}')"
            )

        # Guard 4: IMPORTANCE where question_text is just a brand name (no verb/question) → SKIP
        # Detect: question_text matches a brand-name pattern (short, title-case, no verb keywords)
        elif bucket == "IMPORTANCE":
            _qt_words = qt.strip().split()
            _has_verb = any(w in qt for w in ("how", "what", "which", "when", "rate", "select",
                                               "please", "important", "likely", "satisfied",
                                               "recommend", "agree", "experience"))
            if len(_qt_words) <= 4 and not _has_verb:
                q["bucket"] = "SKIP"
                heuristic_warnings.append(
                    f"question '{q.get('question_code')}': IMPORTANCE→SKIP "
                    f"(question_text looks like a grid header/brand name, not a real question: '{qt[:80]}')"
                )

    if heuristic_warnings:
        result.setdefault("_downgrade_warnings", []).extend(heuristic_warnings)

    # Guard 5: TOM/SPONT positional text family normalisation.
    # Handles three failure modes in one pass:
    #   A) LLM returns TOM with source_column=stem (e.g. q15) + shape=multi_select_dummies
    #      → expand: TOM(q15_1) + SPONT(q15_2..q15_N)
    #   B) LLM returns TOM(q15_1) correctly but never emits q15_2..q15_5 as SPONT at all
    #      → inject missing SPONT siblings
    #   C) LLM returns duplicate TOM entries for the same stem (hallucination)
    #      → deduplicate, keep only one TOM per stem
    all_packet_cols_list = packet.get("raw_sample_columns", [])
    all_packet_cols = set(all_packet_cols_list)
    packet_rows = packet.get("raw_sample_rows", [])

    def _positional_siblings(stem):
        """All real siblings for a positional text stem, sorted by numeric suffix."""
        sibs = [c for c in all_packet_cols_list
                if re.sub(r"_\d+$", "", str(c)) == stem and c != stem]
        return sorted(sibs, key=lambda c: [int(x) if x.isdigit() else x
                                           for x in re.split(r"(\d+)", c)])

    # Index existing TOM/SPONT entries by source_column for dedup + gap detection
    seen_tom_stems: set = set()   # stems already emitted as TOM
    seen_spont_cols: set = set()  # source_columns already emitted as SPONT
    expanded_questions = []
    did_expand = False

    for q in result.get("questions", []):
        bucket = q.get("bucket", "")
        src = str(q.get("source_column") or "")
        stem = re.sub(r"_\d+$", "", src) if src else None

        if bucket == "TOM" and stem:
            siblings = _positional_siblings(stem)
            is_text_fam = (siblings and
                           _is_positional_text_family(stem, all_packet_cols_list, packet_rows))

            if is_text_fam:
                if stem in seen_tom_stems:
                    # Duplicate TOM for this stem — skip (Guard C)
                    heuristic_warnings.append(
                        f"Guard5C: duplicate TOM for stem '{stem}' dropped (src='{src}')")
                    did_expand = True
                    continue

                seen_tom_stems.add(stem)
                tom_col = f"{stem}_1" if f"{stem}_1" in all_packet_cols else siblings[0]

                # Emit TOM for position 1 (Guard A fixes wrong src/shape)
                expanded_questions.append({**q, "source_column": tom_col,
                                           "shape": "single_value", "bucket": "TOM",
                                           "delimiter": ""})
                # Emit SPONT for positions 2+ (Guard A + B)
                for spont_col in siblings:
                    if spont_col == tom_col:
                        continue
                    if spont_col not in seen_spont_cols:
                        expanded_questions.append({
                            **q,
                            "question_code": stem,
                            "source_column": spont_col,
                            "shape": "single_value",
                            "bucket": "SPONT",
                            "delimiter": "",
                        })
                        seen_spont_cols.add(spont_col)
                heuristic_warnings.append(
                    f"Guard5: stem '{stem}' → TOM({tom_col}) + "
                    f"SPONT({', '.join(c for c in siblings if c != tom_col)})"
                )
                did_expand = True
                continue

        if bucket == "SPONT":
            seen_spont_cols.add(src)

        expanded_questions.append(q)

    if did_expand:
        result["questions"] = expanded_questions
        result.setdefault("_downgrade_warnings", []).extend(heuristic_warnings[-1:])

    # Guard 6: drop TOM/SPONT entries whose source_column doesn't exist in real data.
    # LLMs hallucinate column names like "AD. SPONT" or repeat the stem "q15" as SPONT.
    # Any entry whose source_column is not in all_packet_cols is noise — remove it.
    # Also deduplicate by source_column within the same bucket (Guard 5 can create dupes).
    _POSITIONAL_BUCKETS = {"TOM", "SPONT"}
    _seen_src_by_bucket: dict = {}
    filtered_questions = []
    for q in result.get("questions", []):
        bucket = q.get("bucket", "")
        src = str(q.get("source_column") or "")
        if bucket in _POSITIONAL_BUCKETS:
            if src not in all_packet_cols:
                heuristic_warnings.append(
                    f"Guard6: dropped {bucket} entry with nonexistent source_column '{src}'")
                result.setdefault("_downgrade_warnings", [])
                continue
            key = (bucket, src)
            if key in _seen_src_by_bucket:
                heuristic_warnings.append(
                    f"Guard6: dropped duplicate {bucket} entry for source_column '{src}'")
                continue
            _seen_src_by_bucket[key] = True
        filtered_questions.append(q)
    result["questions"] = filtered_questions

    # Guard 7: awareness funnel buckets (AIDED, CONSIDERATION, EVER_USED, CURRENT_USER,
    # PREFERRED, LAST_PURCHASED) with shape=multivalent_source → convert to multi_select_dummies
    # pointing at the first binary sibling (e.g. q17 → q17_1). User wants binary column
    # assignments so the ingest reads each respondent×brand cell directly.
    _FUNNEL_BUCKETS = {"AIDED", "CONSIDERATION", "EVER_USED", "CURRENT_USER",
                       "PREFERRED", "LAST_PURCHASED"}
    for q in result.get("questions", []):
        if q.get("bucket") in _FUNNEL_BUCKETS and q.get("shape") == "multivalent_source":
            src = str(q.get("source_column") or "")
            stem = re.sub(r"_\d+$", "", src)
            # Find the first binary sibling (q17_1, q17_2, ...)
            first_binary = next(
                (c for c in all_packet_cols_list
                 if re.sub(r"_\d+$", "", str(c)) == stem and c != stem),
                None
            )
            if first_binary:
                q["shape"] = "multi_select_dummies"
                q["source_column"] = first_binary
                q["delimiter"] = ""
                heuristic_warnings.append(
                    f"Guard7: {q.get('bucket')} '{src}' multivalent_source → "
                    f"multi_select_dummies({first_binary})"
                )

    # Guard 8: AP Table-Title hard overrides.
    # The AP sheet contains authoritative Table Titles (TOM, SPONT, TRIED IN L6M,
    # TOTAL ADV. AIDED AWARENESS, etc.) tied to data column numbers (Q22, Q17, Q18...).
    # Parse those titles and override LLM bucket decisions for known patterns regardless
    # of what the LLM returned. This is the most reliable signal — the AP is authoritative.
    #
    # Title patterns → FORCED bucket:
    #   "TRIED IN L6M" / "LAST 6 MONTHS" / "L6M"           → SKIP
    #   "TRIED IN L3M" / "LAST 3 MONTHS" / "L3M"           → SKIP
    #   "ADV.*AIDED" / "ADVERTISEMENT.*AWARE" / "AD.*TOM"   → SKIP
    #   "ADV.*SPONT"                                         → SKIP
    #   "ADV.*TOM"                                           → SKIP
    #   "SOURCE.*ADVERT" / "ADVERT.*PLATFORM"               → SKIP
    #   "TOTAL AIDED AWARENESS" / "^AIDED$"                 → AIDED (not ad-aided)
    _AP_TITLE_SKIP_PATTERNS = [
        r"\bL6M\b", r"LAST\s+6\s+MONTH", r"TRIED\s+IN\s+L6M", r"SIX\s+MONTH",
        r"\bL3M\b", r"LAST\s+3\s+MONTH", r"TRIED\s+IN\s+L3M", r"THREE\s+MONTH",
        r"ADV\.\s*AIDED", r"ADV\.\s*SPONT", r"ADV\.\s*TOM", r"ADVERT.*AIDED",
        r"ADV\.\s*AWARE", r"TOTAL\s+ADV\.", r"ADVERTIS.*AWARE",
        r"SOURCE\s+OF\s+ADVERT", r"ADVERT.*PLATFORM", r"AD\s+AWARE",
    ]
    _ap_title_skip_re = [_re.compile(p, _re.IGNORECASE) for p in _AP_TITLE_SKIP_PATTERNS]

    # Build table_no → title map from AP text
    # AP rows look like: "Q22 | MQ1HI | TRIED IN L6M | Brand-Wise | MA | ..."
    # "Q15-1 | MQ1A | TOM | ..." and "Q15-2/3/4/5 | MQ1B | SPONT | ..." are different rows.
    # Store FIRST match per q-stem (don't overwrite); Q15-1 → q15 = TOM comes before Q15-2/3/4/5.
    _ap_title_map: dict[str, str] = {}  # normalized qno → AP title (first match wins)
    for _line in packet.get("ap_text", "").splitlines():
        _parts = [p.strip() for p in _line.split("|")]
        if len(_parts) >= 3 and _parts[0] and _parts[2]:
            _raw_no = _parts[0].strip().upper()
            _title = _parts[2].strip()
            _base_match = _re.match(r"^Q(\d+)", _raw_no)
            if _base_match:
                _qno_key = f"q{_base_match.group(1)}"
                if _qno_key not in _ap_title_map:  # first match wins
                    _ap_title_map[_qno_key] = _title

    ap_title_warnings = []
    if _ap_title_map:
        for q in result.get("questions", []):
            _src = str(q.get("source_column") or q.get("question_code") or "")
            _stem_key = "q" + _re.sub(r"[^0-9].*", "", _re.sub(r"^q", "", _src, flags=_re.I))
            _ap_title = _ap_title_map.get(_stem_key, "")
            if not _ap_title:
                continue
            # Force SKIP for known non-funnel AP titles
            if any(pat.search(_ap_title) for pat in _ap_title_skip_re):
                old_bucket = q.get("bucket", "")
                if old_bucket != "SKIP":
                    q["bucket"] = "SKIP"
                    ap_title_warnings.append(
                        f"Guard8: {_stem_key} '{_ap_title}' AP-title matched SKIP rule "
                        f"(was {old_bucket})"
                    )
    if ap_title_warnings:
        result.setdefault("_downgrade_warnings", []).extend(ap_title_warnings)

    # Defensive delimiter injection: schema requires `delimiter` to be a non-null string for
    # every question (required property). Models return null or omit it for non-multivalent shapes.
    # Fix for ALL shapes: null/missing → "" for non-multivalent, " " for multivalent_source.
    for q in result.get("questions", []):
        d = q.get("delimiter")
        if d is None:
            if q.get("shape") == "multivalent_source":
                q["delimiter"] = " "
                result.setdefault("_downgrade_warnings", []).append(
                    f"question '{q.get('question_code')}': shape 'multivalent_source' missing/null "
                    f"delimiter — defaulted to ' ' (space)."
                )
            else:
                q["delimiter"] = ""
        elif not isinstance(d, str):
            # model returned a number or other non-string for delimiter → coerce to string
            q["delimiter"] = str(d)

    # Defensive source_column injection: schema requires source_column for every question.
    # Models (especially llama-3.3-70b) sometimes omit it entirely. Fall back to question_code
    # so schema validation doesn't reject an otherwise-good batch over a missing display field.
    for q in result.get("questions", []):
        if not q.get("source_column"):
            q["source_column"] = q.get("question_code", "unknown")
            result.setdefault("_downgrade_warnings", []).append(
                f"question '{q.get('question_code')}': source_column missing — defaulted to "
                f"question_code (verify manually)."
            )

    # Defensive confidence/reasoning injection: llama-3.3-70b omits these required fields when
    # the context is large and the model focuses on the structural fields. Inject safe defaults so
    # the whole batch doesn't fail over missing audit fields that add no DB-write value.
    missing_conf_warnings = []
    for q in result.get("questions", []):
        if "confidence" not in q:
            q["confidence"] = 0.5
            missing_conf_warnings.append(
                f"question '{q.get('question_code')}': confidence not returned by model — "
                f"defaulted to 0.5."
            )
        if "reasoning" not in q:
            q["reasoning"] = "Not provided by model."
    if missing_conf_warnings:
        result.setdefault("_downgrade_warnings", []).extend(missing_conf_warnings)

    # 2026-08-03: found via a live Akshayakalpa UI run — a "'questions' is a required property"
    # validation failure with zero visibility into what the LLM's response actually contained
    # (empty dict? wrong top-level key name? questions present but null?), wasting a paid API
    # call on guessing. Wrap the validation call the same way the HTTP-call error above is
    # wrapped: catch the specific jsonschema exception and re-raise with a truncated dump of the
    # actual parsed `result` so the real shape of the bad response is visible without re-running.
    try:
        jsonschema.validate(result, schema)
    except jsonschema.ValidationError as e:
        result_snippet = json.dumps(result)[:1000]
        raise RuntimeError(
            f"classify_all_questions: LLM response failed schema validation for project "
            f"'{project_id}' with model '{model}': {e.message}. Parsed result (truncated to "
            f"1000 chars): {result_snippet!r}") from e

    # Completeness guard (2026-08-03): a schema-valid response can still be badly INCOMPLETE —
    # jsonschema only checks each returned question's shape, not whether the LLM covered the
    # whole survey. Skipped in batch mode (skip_completeness_check=True) because per-batch
    # packets contain one representative column per stem (all siblings for positional text
    # families like TOM/SPONT q15_1..q15_5), so returned count < #stems is correct.
    # The aggregate check at the end of classify_all_questions() is the real gate for batches.
    if not skip_completeness_check:
        distinct_stems = _distinct_question_stems(packet.get("raw_sample_columns", []))
        n_stems = len(distinct_stems)
        n_returned = len(result.get("questions", []))
        if n_stems >= _MIN_STEMS_FOR_COMPLETENESS_CHECK and n_returned < n_stems * _COMPLETENESS_MIN_FRACTION:
            raise RuntimeError(
                f"classify_all_questions: LLM returned only {n_returned} questions but the raw data "
                f"contains ~{n_stems} distinct question stems for project '{project_id}' with model "
                f"'{model}' — classification is incomplete (under "
                f"{int(_COMPLETENESS_MIN_FRACTION * 100)}% coverage), not usable. Retry, or split "
                f"the project into smaller batches."
            )

    return result


# Auto-batch threshold: 20 stems per batch keeps output to ~3K tokens per call
# (20 stems × ~150 tokens) — well under gpt-4o-mini's 16K ceiling. Each batch gets
# the FULL datamap + AP for context; raw_sample_columns/rows are subsetted per batch
# to ONE representative column per stem (the first occurrence in column order) so the
# model isn't overwhelmed by 50+ dummy-coded columns per battery.
_MAX_STEMS_PER_BATCH = 5


def _is_positional_text_family(stem: str, all_cols: list, sample_rows: list) -> bool:
    """Return True if this stem's sibling columns hold open-text brand names (not 0/1 dummies).
    Positional recall families (TOM q15_1, SPONT q15_2..q15_5) must have ALL siblings sent
    to the LLM so it can assign TOM to position-1 and SPONT to positions 2+.
    Detection: ≥2 siblings exist AND at least one sample value is non-numeric text."""
    col_idx = {col: i for i, col in enumerate(all_cols)}
    siblings = [col for col in all_cols if re.sub(r"_\d+$", "", str(col)) == stem
                and col != stem]  # exclude the stem itself if present as-is
    if len(siblings) < 2:
        return False
    for row in sample_rows:
        for col in siblings:
            idx = col_idx.get(col)
            if idx is None or idx >= len(row):
                continue
            val = row[idx]
            if val is None:
                continue
            s = str(val).strip()
            if s and not re.match(r"^-?\d+(\.\d+)?$", s):
                return True  # non-numeric value → text family
    return False


def _subset_packet_for_stems(packet: dict, stems_batch: set) -> dict:
    """Return a copy of packet with raw_sample_columns/rows subsetted per stem batch.

    For most stems (dummy-coded batteries like q34_1_1, q34_1_2): send ONE representative
    column — keeps payload small, model correctly collapses siblings to one bucket.

    Exception — positional text families (open-recall brand-name columns like q15_1..q15_5):
    send ALL siblings so the LLM can assign TOM to position-1 and SPONT to positions 2+.
    These are detected by: ≥2 siblings exist AND at least one sample value is non-numeric text.
    """
    all_cols = packet["raw_sample_columns"]
    sample_rows = packet["raw_sample_rows"]

    # Pre-compute which stems are positional text families
    positional_stems: set = set()
    for col in all_cols:
        stem = re.sub(r"_\d+$", "", str(col))
        if stem in stems_batch and stem not in positional_stems:
            if _is_positional_text_family(stem, all_cols, sample_rows):
                positional_stems.add(stem)

    seen_stems: set = set()
    batch_cols: list = []
    for col in all_cols:
        stem = re.sub(r"_\d+$", "", str(col))
        if stem not in stems_batch:
            continue
        if stem in positional_stems:
            # Include ALL siblings for positional text families
            batch_cols.append(col)
        elif stem not in seen_stems:
            # Include only first representative for dummy batteries
            batch_cols.append(col)
            seen_stems.add(stem)

    col_idx = {col: i for i, col in enumerate(all_cols)}
    batch_indices = [col_idx[c] for c in batch_cols]
    batch_rows = [[row[i] for i in batch_indices] for row in sample_rows]

    # Subset ap_col_map to only batch columns (so the LLM sees only relevant AP titles)
    full_ap_col_map = packet.get("ap_col_map", {})
    batch_ap_col_map = {c: full_ap_col_map[c] for c in batch_cols if c in full_ap_col_map}

    # Subset AP text to only rows relevant to this batch.
    # Handles two AP formats:
    #   tabplan: rows start with "Q{num} | ..." — filter by batch_q_nums
    #   master_mapping: sheets named CTL_<stem>, MAPPING sheet with question_code col
    batch_q_nums = {info["q_num"] for info in batch_ap_col_map.values()}
    ap_lines_subset = []
    _cur_ap_sheet = ""
    _ap_col_header_written = False
    _keep_this_sheet = True  # for master_mapping CTL_* sheets, may be False
    for line in packet.get("ap_text", "").splitlines():
        # Sheet header
        if line.startswith("=== SHEET:"):
            _cur_ap_sheet = line.replace("=== SHEET:", "").strip().rstrip("=").strip()
            _ap_col_header_written = False
            # For CTL_* sheets, only keep if stem is in batch
            if _cur_ap_sheet.upper().startswith("CTL_"):
                ctl_stem = _cur_ap_sheet[4:]  # strip "CTL_"
                _keep_this_sheet = any(
                    ctl_stem == s or ctl_stem.startswith(s + "_") or ctl_stem.startswith(s + "/")
                    for s in stems_batch
                )
            else:
                _keep_this_sheet = True  # META, BRANDS, MAPPING always kept
            if _keep_this_sheet:
                ap_lines_subset.append(line)
            continue

        if not _keep_this_sheet:
            continue

        # Column header (first row after sheet header)
        if not _ap_col_header_written:
            ap_lines_subset.append(line)
            _ap_col_header_written = True
            continue

        # Data rows — for MAPPING sheet filter by question_code in batch stems
        if _cur_ap_sheet.upper() == "MAPPING":
            parts = [p.strip() for p in line.split("|")]
            qcode = parts[0] if parts else ""
            if any(qcode == s or qcode.startswith(s + "_") or qcode.startswith(s + "/")
                   for s in stems_batch):
                ap_lines_subset.append(line)
            # also handle tabplan Q-number format
            elif re.match(r"^Q\d+", qcode.upper()) and batch_q_nums:
                m = re.match(r"^Q(\d+)", qcode.upper())
                if m and f"q{m.group(1)}" in batch_q_nums:
                    ap_lines_subset.append(line)
        else:
            # tabplan rows OR CTL rows — keep all (CTL already gated by _keep_this_sheet)
            parts = [p.strip() for p in line.split("|")]
            qcode = parts[0] if parts else ""
            m = re.match(r"^Q(\d+)", qcode.upper())
            if m:
                if f"q{m.group(1)}" in batch_q_nums:
                    ap_lines_subset.append(line)
            else:
                ap_lines_subset.append(line)

    # Subset datamap_text to only lines relevant to batch stems (reduces context ~80%)
    # Keep: sheet-header lines (=== SHEET:), column-header lines (first data line per sheet),
    # and data lines whose first |-token matches a batch stem or its dummy prefix.
    batch_stem_prefixes = tuple(sorted(stems_batch))
    datamap_lines_subset = []
    in_header = True  # first non-sheet-header line is the column header
    current_sheet_header_written = False
    col_header_written = False
    for line in packet.get("datamap_text", "").splitlines():
        if line.startswith("=== SHEET:"):
            datamap_lines_subset.append(line)
            in_header = True
            col_header_written = False
            continue
        if in_header:
            # column header row — always keep
            datamap_lines_subset.append(line)
            in_header = False
            col_header_written = True
            continue
        # data line — keep if first token matches a batch stem
        first_token = line.split("|")[0].strip()
        # Match: exact stem, or stem/suffix pattern, or stem_N pattern
        matched = any(
            first_token == stem or first_token.startswith(stem + "/") or first_token.startswith(stem + "_")
            for stem in batch_stem_prefixes
        )
        if matched:
            datamap_lines_subset.append(line)

    return {
        **packet,
        "raw_sample_columns": batch_cols,
        "raw_sample_rows": batch_rows,
        "ap_col_map": batch_ap_col_map,
        "ap_text": "\n".join(ap_lines_subset),
        "datamap_text": "\n".join(datamap_lines_subset),
    }


def _build_user_content(packet: dict, project_id: str,
                        batch_hint: str = "") -> str:
    """Build the user-role message content for a (possibly batched) classification call."""
    batch_section = f"\n{batch_hint}\n" if batch_hint else ""

    # Pre-linked AP→column map: Python-computed, zero ambiguity, shown BEFORE raw columns
    ap_col_map = packet.get("ap_col_map", {})
    ap_col_subset = list(packet.get("raw_sample_columns", []))
    ap_map_text = format_ap_column_map_text(ap_col_map, columns_subset=ap_col_subset)
    ap_map_section = (
        f"\n=== COLUMN → AP TITLE MAP (pre-linked by Python, use this as authoritative) ===\n"
        f"Each line: column(s) → AP table title that defines what this question measures.\n"
        f"CRITICAL: use this to correctly assign bucket. AP title IS the ground truth.\n"
        f"{ap_map_text}\n"
    ) if ap_map_text else ""

    return (
        f"PROJECT_ID: {project_id}\n"
        f"{batch_section}"
        f"{ap_map_section}"
        f"\n=== ANALYSIS PLAN (full AP sheet for reference) ===\n{packet['ap_text']}\n\n"
        f"=== DATAMAP ===\n{packet['datamap_text']}\n\n"
        f"=== RAW SAMPLE COLUMNS ===\n{json.dumps(packet['raw_sample_columns'])}\n\n"
        f"=== RAW SAMPLE ROWS (positional, paired with RAW SAMPLE COLUMNS) ===\n"
        f"{json.dumps(packet['raw_sample_rows'])}\n"
    )


def classify_all_questions(packet: dict, project_id: str, api_key: str, model,
                            timeout: int = 180, max_real_tokens: int = 100_000) -> dict:
    """JSON-schema-constrained LLM classification of every question in the survey.

    Auto-batches when the survey has more than _MAX_STEMS_PER_BATCH distinct question
    stems: splits stems into groups of _MAX_STEMS_PER_BATCH, subsets the raw sample
    columns to ONE representative column per stem (full datamap + AP sent every time for
    context), calls the LLM once per batch with the fallback model list, then merges all
    question arrays into one result. This keeps each call's OUTPUT well within the smallest
    model's output window (20 stems × ~150 tokens ≈ 3K output — well under gpt-4o-mini's
    16K ceiling) and prevents models from stopping early due to column-count overload.

    For surveys with ≤_MAX_STEMS_PER_BATCH stems, behaves exactly as before (one call).

    Before calling the API, estimates the request's token size using the standard
    ~4-chars/token heuristic, corrected by _CHARS_PER_4_TO_REAL_TOKEN_INFLATION for
    dense JSON/tabular content, and raises ValueError if over max_real_tokens. For
    batched calls the check runs per batch (each batch is smaller than the full packet).

    `model` may be a single model-id string or a list of model-ids to try in order.
    Each model in the list gets ONE attempt per batch; the first model that succeeds
    is used for subsequent batches too (to avoid paying for re-tries when one model
    works). `result["_model_used"]` is set to whichever model first succeeded.
    """
    models = [model] if isinstance(model, str) else list(model)
    if not models:
        raise ValueError("classify_all_questions: `model` must be a non-empty string or list of model ids.")

    schema = json.loads(SCHEMA_PATH.read_text())

    # --- Auto-batch when survey is too wide for reliable single-call output ---
    all_stems = sorted(_distinct_question_stems(packet["raw_sample_columns"]))
    use_batching = len(all_stems) > _MAX_STEMS_PER_BATCH

    if use_batching:
        stem_batches = [set(all_stems[i:i + _MAX_STEMS_PER_BATCH])
                        for i in range(0, len(all_stems), _MAX_STEMS_PER_BATCH)]
        all_questions: list = []
        all_warnings: list = []
        model_used: str | None = None
        batch_errors: list = []

        def _process_stem_group(stems_set, batch_label, total_batches, depth=0):
            """Process one stem group; halve it if still too large (max 4 splits = 1 stem)."""
            nonlocal model_used
            bp = _subset_packet_for_stems(packet, stems_set)
            hint = (
                f"BATCH {batch_label}/{total_batches}: Classify ONLY the questions "
                f"whose column stems appear in RAW SAMPLE COLUMNS below. Do NOT classify "
                f"questions for columns not listed in RAW SAMPLE COLUMNS."
            )
            uc = _build_user_content(bp, project_id, hint)
            raw_est = (len(uc) + len(_SYSTEM_PROMPT)) // 4
            corrected = raw_est * _CHARS_PER_4_TO_REAL_TOKEN_INFLATION
            if corrected > max_real_tokens and depth < 4 and len(stems_set) > 1:
                # Split in half and process each half
                stems_list = sorted(stems_set)
                mid = len(stems_list) // 2
                left = set(stems_list[:mid])
                right = set(stems_list[mid:])
                q_left, w_left = _process_stem_group(left, batch_label + "a", total_batches, depth + 1)
                q_right, w_right = _process_stem_group(right, batch_label + "b", total_batches, depth + 1)
                return q_left + q_right, w_left + w_right
            elif corrected > max_real_tokens and (depth >= 4 or len(stems_set) <= 1):
                raise ValueError(
                    f"Batch {batch_label} context still too large for project '{project_id}' "
                    f"even after splitting to {len(stems_set)} stem(s): "
                    f"~{int(corrected)} tokens > {max_real_tokens} budget."
                )
            # Make the actual API call
            preferred = [model_used] + [m for m in models if m != model_used] if model_used else models
            for m in preferred:
                try:
                    result_batch = _classify_with_single_model(
                        bp, project_id, api_key, m, timeout, schema, uc,
                        skip_completeness_check=True)
                    if model_used is None:
                        model_used = m
                    return result_batch.get("questions", []), result_batch.get("_downgrade_warnings", [])
                except Exception as e:
                    batch_errors.append(f"batch {batch_label}, {m}: {e}")
            raise RuntimeError(
                f"classify_all_questions: ALL {len(models)} model(s) failed for batch "
                f"{batch_label}/{total_batches} of project '{project_id}'. "
                f"Errors: " + " | ".join(batch_errors[-len(models):])
            )

        for batch_num, batch_stems in enumerate(stem_batches):
            batch_questions, batch_warnings = _process_stem_group(
                batch_stems, batch_num + 1, len(stem_batches))
            all_questions.extend(batch_questions)
            all_warnings.extend(batch_warnings)

        # Final completeness check across all batches merged
        n_returned = len(all_questions)
        n_stems = len(all_stems)
        if n_stems >= _MIN_STEMS_FOR_COMPLETENESS_CHECK and n_returned < n_stems * _COMPLETENESS_MIN_FRACTION:
            raise RuntimeError(
                f"classify_all_questions: merged batches returned only {n_returned} questions but "
                f"~{n_stems} distinct question stems exist for project '{project_id}' — "
                f"coverage under {int(_COMPLETENESS_MIN_FRACTION * 100)}%."
            )

        # Post-process: normalize question_codes to stems. In batch mode we send ONE
        # representative column per stem (e.g. q15_1), and the model sometimes uses that
        # exact column name as question_code instead of the stem (q15). Strip one trailing
        # _<int> level when the result would land on a known stem AND the original column
        # name appeared in a batch's raw_sample_columns.
        col_set = set(packet["raw_sample_columns"])
        for q in all_questions:
            code = q.get("question_code", "")
            if code in col_set:            # model used a real column name, not a stem
                stem = re.sub(r"_\d+$", "", code)
                if stem != code and stem in set(all_stems):
                    q["question_code"] = stem

        # Dedup across batches: same (question_code, source_column) pair can appear from
        # multiple batches (e.g. q34_3_1 appears in two overlapping batches).
        # KEY: dedup on (code, source_column) not just code — same code + different
        # source_column = distinct questions (e.g. BRAND_IMAGERY battery where LLM emits
        # all attrs with question_code="q34" but different source_columns per attr).
        best: dict = {}  # (code, source_col) -> question
        for q in all_questions:
            code = q.get("question_code", "")
            src = str(q.get("source_column") or "")
            if not code:
                continue
            key = (code, src)
            if key not in best or (q.get("confidence", 0) > best[key].get("confidence", 0)):
                best[key] = q
        deduped = list(best.values())
        if len(deduped) < len(all_questions):
            all_warnings.append(
                f"Cross-batch dedup: removed {len(all_questions) - len(deduped)} duplicate "
                f"question entries (kept highest-confidence per code)."
            )
        result = {"project_id": project_id, "questions": deduped}
        result["_model_used"] = model_used
        if all_warnings:
            result["_downgrade_warnings"] = all_warnings
        return result

    # --- Single-call path (≤_MAX_STEMS_PER_BATCH stems) ---
    user_content = _build_user_content(packet, project_id)

    raw_estimated_tokens = (len(user_content) + len(_SYSTEM_PROMPT)) // 4
    corrected_estimate = raw_estimated_tokens * _CHARS_PER_4_TO_REAL_TOKEN_INFLATION
    if corrected_estimate > max_real_tokens:
        raise ValueError(
            f"Context packet too large for project {project_id}: chars/4 estimate is "
            f"{raw_estimated_tokens} tokens, corrected for known ~{_CHARS_PER_4_TO_REAL_TOKEN_INFLATION}x "
            f"tabular-content inflation that's ~{int(corrected_estimate)} real tokens, over the "
            f"{max_real_tokens}-token budget (leaves headroom under a 128K-context model's limit "
            f"for response tokens + provider overhead). Reduce sample_rows or split the project."
        )

    errors = []
    for m in models:
        try:
            result = _classify_with_single_model(
                packet, project_id, api_key, m, timeout, schema, user_content)
        except Exception as e:
            errors.append(f"{m}: {e}")
            continue
        result["_model_used"] = m
        return result

    raise RuntimeError(
        f"classify_all_questions: ALL {len(models)} model(s) failed for project '{project_id}'. "
        f"Tried in order: " + " | ".join(errors)
    )
