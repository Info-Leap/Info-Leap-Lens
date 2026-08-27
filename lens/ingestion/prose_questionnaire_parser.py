"""
LENS Prose Questionnaire Parser — 3rd codebook loader type (2026-07-28)
========================================================================
Handles the case documented as unsolved in .planning/MULTIPROJECT_INGESTION_LOG_2026-07-27.md
(the 2026-07-27 "Akshayakalpa" entries): a client whose ONLY question-code + answer-option source
is a legacy binary .doc questionnaire (real text, but flattened prose — option label immediately
followed by its numeric code with no separator, e.g. "Male 1 Female 2") with NO shared identifier
between the questionnaire's own numbering (SQ1a, MQ1e...) and the data file's actual column names
(sq1b, mq1e_1...). Two problems, two AI passes, deliberately kept separate and always
confidence-capped / human-confirmed — same "never silently guess" principle as the rest of this
pipeline:

  1. STRUCTURE EXTRACTION (this module's `parse_questionnaire_to_questions`): an LLM reads the
     flattened prose and returns {question code, question text, value_labels} — turning a doc with
     zero machine-readable structure into the same QuestionDef shape codebook_parser.py already
     uses for XLSForm/AP-tabplan codebooks. This is a genuinely different problem from #2: it's
     PURE TEXT EXTRACTION, no data file involved yet.

  2. COLUMN CROSSWALK (`ai_crosswalk_match` in codebook_parser.py, not here): given the structured
     questions from #1, decide which of the data file's actual columns each one corresponds to —
     using response-pattern fingerprinting (does a candidate column's real distinct-value count
     match the question's option count?) as a hard structural pre-filter, with an LLM choosing
     among the surviving candidates using question text + real sample value distributions. This
     is what makes the match "AI-assisted, not AI-guessed" — the LLM never sees a column that's
     already structurally implausible.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from .codebook_parser import QuestionDef


def extract_doc_text(doc_path: str) -> str:
    """Extract plain text from a legacy binary .doc file via Word COM automation.

    Only works on Windows with MS Word installed — this is a real, hard dependency (python-docx
    only reads the zip-based .docx format; there is no reliable pure-Python .doc reader). Raises
    RuntimeError with a clear message on any other platform or if Word isn't available, rather
    than silently returning empty text that would look like "no content" instead of "can't read
    this file type here".
    """
    try:
        import win32com.client
    except ImportError as e:
        raise RuntimeError(
            "Reading .doc questionnaires requires pywin32 + MS Word installed (Windows only). "
            "Convert the file to .docx in Word first if this environment can't run COM automation."
        ) from e

    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(str(Path(doc_path).resolve()))
        text = doc.Range().Text
        doc.Close(False)
    finally:
        word.Quit()
    return text


_PARSE_SYSTEM_PROMPT = """You are extracting a structured codebook from a flattened market-research \
questionnaire. The source is a Word doc where formatting (separate table cells/columns) was lost on \
text extraction, so an answer option's label is immediately followed by its numeric code with no \
separator (e.g. "Male 1 Female 2" means option 1=Male, option 2=Female; "Bangalore1Chennai2" means \
1=Bangalore, 2=Chennai).

For EVERY substantive question you can identify (skip section headers, programmer instructions in \
ALL CAPS like "PROG:", "ASK IF CODED...", quota/routing notes — those are instructions TO THE \
SURVEY PLATFORM, not questions themselves):
- code: the question's own identifier as written (e.g. "SQ1a", "MQ1e", "B1"). If truly no code is \
  visible for an obvious question, invent a short slug from the first few words instead of skipping it.
- text: the actual question text a respondent would read (strip instructions/routing notes).
- options: list of {"code": <int or string as it appears>, "label": <the option's text>} for \
  every answer option you can find, IN ORDER. Leave empty [] for open-ended/numeric-entry questions \
  with no fixed option list (e.g. "what is your age in years" with no coded bands).
- shape: "single" (one answer, SA / single-select) | "multi" (MA / multi-select, respondent can \
  pick several) | "open" (free text / numeric entry, no options).

Return STRICT JSON: a single array of these objects, nothing else — no markdown fences, no prose \
before or after. Every option code you output must be exactly as it appears near the label (do not \
renumber or reorder options)."""


def _chunk_text(text: str, max_chars: int = 9000) -> list[str]:
    """Split the extracted doc text into overlapping-free chunks at a paragraph-ish boundary so a
    very long questionnaire doesn't blow the model's practical single-call output budget (each
    chunk's worth of questions has to fit in one JSON response). Real Akshayakalpa doc is ~21K
    chars / ~65 questions — comfortably 2-3 chunks at this size, not dozens, so this stays a small
    number of calls, not a call-per-question scheme."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            # back off to the nearest sentence-ish break so a question isn't split mid-option-list
            back = text.rfind(". ", start, end)
            if back > start + max_chars // 2:
                end = back + 2
        chunks.append(text[start:end])
        start = end
    return chunks


def _call_llm_extract(chunk: str, api_key: str, model: str, timeout: int = 90) -> list[dict]:
    import urllib.request

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _PARSE_SYSTEM_PROMPT},
            {"role": "user", "content": f"QUESTIONNAIRE TEXT CHUNK:\n\n{chunk}"},
        ],
        "max_tokens": 8000,
        "temperature": 0.1,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://infoleap.ai"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    # Models occasionally trail a partial object if max_tokens is hit mid-array — truncate to the
    # last complete `}` before the final `]` rather than fail the whole chunk over one cut-off tail.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        last_close = text.rfind("}")
        if last_close == -1:
            raise
        repaired = text[:last_close + 1] + "]"
        return json.loads(repaired)


_BRAND_GRID_SYSTEM_PROMPT = """Market-research questionnaires almost always define ONE master list \
of brand names with numeric codes, reused across many later questions (aided awareness, \
consideration, ever-tried, current-use, etc. all ask "select which of these brands..." against the \
SAME list). In a flattened prose export, this master list usually appears as a grid/table that got \
mangled into a dense run of "BrandName<digits>BrandName<digits>..." with several question columns' \
codes glued together per brand row (so the SAME brand name can be immediately followed by several \
numbers, one per question column, not just one). Read the text below and find that master brand \
list. For EACH brand, extract its NAME and its PRIMARY numeric CODE (the code that identifies the \
brand consistently across questions — usually the first number after the brand name, which is often \
also the same value repeated as an option code in awareness/consideration/trial questions). Ignore \
"None of the above" / "Others, please specify" rows. Return STRICT JSON: an array of \
{"code": <int>, "label": <brand name>}, one entry per real brand, nothing else."""


def _call_llm_extract_brand_map(text: str, api_key: str, model: str, timeout: int = 90) -> dict:
    import urllib.request

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _BRAND_GRID_SYSTEM_PROMPT},
            {"role": "user", "content": f"QUESTIONNAIRE TEXT:\n\n{text}"},
        ],
        "max_tokens": 3000,
        "temperature": 0.1,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://infoleap.ai"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    raw = data["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    rows = json.loads(raw)
    brand_map = {}
    for row in rows:
        try:
            code = int(row["code"])
        except (KeyError, TypeError, ValueError):
            continue
        label = row.get("label")
        if label:
            brand_map[code] = label
    return brand_map


def parse_questionnaire_to_questions(
    doc_path: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> list[QuestionDef]:
    """Full pipeline: extract .doc text -> chunk -> AI-parse each chunk -> merge into QuestionDefs.

    Returns the SAME QuestionDef shape as load_xlsform_datamap/load_ap_tabplan_datamap, so this
    plugs into build_mapping_report() as a third `codebook_format` option without changing anything
    downstream of "I have a list of QuestionDef objects."

    Raises RuntimeError (not a silent empty list) if no API key is available or every chunk fails
    — a codebook that silently came back empty would be indistinguishable from "this questionnaire
    genuinely has no questions," which is never true.
    """
    if not (api_key or os.getenv("OPENROUTER_API_KEY")):
        try:
            from dotenv import load_dotenv
            load_dotenv(str(Path(__file__).resolve().parent.parent.parent / "oxdata" / ".env"),
                        override=False)
        except Exception:
            pass
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("No OPENROUTER_API_KEY available — prose questionnaire parsing requires "
                           "an LLM call, there is no non-AI fallback for this codebook shape.")
    chosen_model = model or os.getenv("OPENROUTER_MODEL_PRO") or os.getenv("OPENROUTER_MODEL_MINI")
    if not chosen_model:
        raise RuntimeError("No OPENROUTER_MODEL_PRO/MINI configured.")

    text = extract_doc_text(doc_path)
    chunks = _chunk_text(text)

    questions: list[QuestionDef] = []
    seen_codes: set = set()
    errors = []
    for i, chunk in enumerate(chunks):
        try:
            rows = _call_llm_extract(chunk, key, chosen_model)
        except Exception as e:
            errors.append(f"chunk {i+1}/{len(chunks)}: {e}")
            continue
        for row in rows:
            code = str(row.get("code", "")).strip()
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            opts = row.get("options") or []
            value_labels = {}
            for opt in opts:
                oc = opt.get("code")
                if oc is None:
                    continue
                # keep both the raw and int-coerced key when possible — data cells may come back
                # as int or float depending on pandas dtype inference, and this dict is looked up
                # both ways downstream (see generic_loader.extract_* functions' `.get(val, ...)`
                # then `.get(int(val), ...)` fallback pattern).
                value_labels[oc] = opt.get("label", str(oc))
                try:
                    value_labels[int(oc)] = opt.get("label", str(oc))
                except (TypeError, ValueError):
                    pass
            questions.append(QuestionDef(
                code=code,
                text=str(row.get("text", code)),
                list_name=f"prose:{code}" if value_labels else None,
                value_labels=value_labels,
                response_type={"single": "SA", "multi": "MA", "open": "OE"}.get(
                    row.get("shape"), None),
            ))

    if not questions:
        raise RuntimeError(f"AI extraction produced zero questions from {len(chunks)} chunk(s). "
                            f"Errors: {errors or 'none reported — check the doc text itself.'}")

    # 2026-07-28: backfill the shared brand-code list — see module docstring and
    # _BRAND_QUESTION_HINTS. Trigger is deliberately narrow — EXACTLY zero options AND the word
    # "brand" itself in the question text — not the looser multi-keyword/`<3` version tried
    # first: that version would have overwritten perfectly good small-option-count questions too
    # (e.g. "Are you aware of the Good Food for School Programme? Yes/No" matches the "aware"
    # keyword and has 2 options, but 2 real options is NOT a parse failure — only a question that
    # came back with ZERO options at all is actually the grid-mangling failure this backfill
    # exists to fix).
    needs_backfill = [
        q for q in questions
        if len(q.value_labels) == 0 and "brand" in q.text.lower()
    ]
    if needs_backfill:
        try:
            brand_map = _call_llm_extract_brand_map(text, key, chosen_model)
        except Exception:
            brand_map = {}
        if brand_map:
            full_labels = dict(brand_map)
            for code, label in list(brand_map.items()):
                full_labels[str(code)] = label
            for q in needs_backfill:
                q.value_labels = full_labels
                q.list_name = "prose:brand_master_list"
                if q.response_type is None:
                    q.response_type = "MA"

    return questions
