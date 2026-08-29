"""
Two-step LLM extraction pipeline. Per-transcript, per-project.
Step 1: Free-form analysis pass (think)
Step 2: Schema-constrained JSON extraction (structure)

Usage:
    python project_extractor.py --project karat-coindcx
    python project_extractor.py --project karat-coindcx --force  # re-extract all
    python project_extractor.py --project karat-coindcx --file "DI 1_Male_Digital Gold_Delhi.m4a.docx"
"""

import argparse
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

# Windows' console defaults to cp1252, which can't encode most emoji (e.g. ⚠) — a bare
# print() with one crashes the whole CLI run with a misleading UnicodeEncodeError, discarding a
# transcript that had already been successfully extracted. errors="replace" makes this class of
# bug non-fatal everywhere in this script, not just at the one call site that happened to trip it.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

# ── env loading (matches qual_retriever.py pattern) ─────────────────────────
from dotenv import load_dotenv

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(str(_ENV_FILE), override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import call_llm, call_llm_safe, LLMCallError  # noqa: E402  (after sys.path patch)

# ── paths ────────────────────────────────────────────────────────────────────
_SKILLS_DIR = Path(__file__).resolve().parent
_OXDATA_DIR = _SKILLS_DIR.parent
_DATA_DIR = _OXDATA_DIR / "data"

_STEP1_TOKENS = 2000
_STEP2_TOKENS = 8000

# ── LLM client (Gemini, via shared llm_client.py) ─────────────────────────────

def _get_client():
    """Kept for call-site compatibility — llm_client manages its own client internally."""
    return None


def _call_llm(
    client,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    model_index: int = 0,
    json_mode: bool = False,
) -> tuple[str, int]:
    """
    Call the shared Gemini client. Returns (content_string, 0) — model_index kept in the
    signature only so existing call sites don't need to change; there is no rotation anymore,
    just retries inside llm_client. Raises LLMCallError (a RuntimeError) on total failure —
    callers already catch RuntimeError, so failures surface as extraction_report errors instead
    of silently returning empty strings.
    """
    content = call_llm(messages, max_tokens=max_tokens, temp=temperature, json_mode=json_mode)
    return content, 0


# ── YAML frontmatter parser ───────────────────────────────────────────────────

def _parse_frontmatter(md_text: str) -> tuple[dict, str]:
    """
    Extract YAML frontmatter from .md content.
    Returns (meta_dict, body_text).
    Simple line-by-line parser — no PyYAML dependency.
    """
    meta = {}
    body = md_text

    if md_text.startswith("---"):
        lines = md_text.split("\n")
        end_idx = None
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end_idx = i
                break
        if end_idx:
            for line in lines[1:end_idx]:
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"')
            body = "\n".join(lines[end_idx + 1:])

    return meta, body


# ── JSON extraction helper ────────────────────────────────────────────────────

def _repair_json(text: str) -> str:
    """
    Fix common LLM JSON errors:
    1. Trailing commas before } or ]
    2. Array closed with } instead of ]
    3. Object closed with ] instead of }
    4. <think>...</think> reasoning blocks before JSON
    """
    # Strip reasoning model think blocks
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()

    # Fix trailing commas: ,\n} or ,\n]
    text = re.sub(r",(\s*[}\]])", r"\1", text)

    # Simple bracket balancer for common case:
    # Walk through and track stack, fix mismatched closers
    stack = []
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch in ('{', '['):
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '[':
                # LLM closed array with } — fix to ]
                chars[i] = ']'
                stack.pop()
            elif stack and stack[-1] == '{':
                stack.pop()
        elif ch == ']':
            if stack and stack[-1] == '{':
                # LLM closed object with ] — fix to }
                chars[i] = '}'
                stack.pop()
            elif stack and stack[-1] == '[':
                stack.pop()

    return "".join(chars)


def _extract_json(text: str) -> dict | None:
    """
    Try to parse JSON from LLM output.
    Handles: markdown fences, reasoning blocks, common bracket mismatches.
    """
    if not text or not text.strip():
        return None

    # Strip markdown fences
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    cleaned = cleaned.rstrip("`").strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try repair
    try:
        repaired = _repair_json(cleaned)
        return json.loads(repaired)
    except (json.JSONDecodeError, Exception):
        pass

    # Try to find first { ... } block
    m = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if m:
        block = m.group(1)
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_repair_json(block))
        except (json.JSONDecodeError, Exception):
            pass

    return None


# ── enum normaliser ──────────────────────────────────────────────────────────

def _collect_enum_values(schema: dict) -> dict[str, list[str]]:
    """
    Read the allowed values for every enum field straight out of extraction_schema.json —
    Layer 1 (universal, e.g. nps_signal/severity/sentiment) and Layer 2 (project-specific,
    whatever this project's schema actually declares today). Keyed by bare field name
    (last path segment, lowercased), matching however deep the field is nested in the matrix —
    this is schema-driven, so it stays correct even after the schema is regenerated and field
    names change, instead of drifting out of sync like the old hardcoded table did.
    """
    out: dict[str, list[str]] = {}

    def walk(node, key_hint: str | None = None):
        if not isinstance(node, dict):
            return
        if node.get("type") == "enum" and node.get("values"):
            if key_hint:
                out[key_hint.lower()] = list(node["values"])
            return
        for k, v in node.items():
            if isinstance(v, dict):
                walk(v, key_hint=k)

    walk(schema.get("layer1", {}).get("fields", {}))
    for fname, fdef in schema.get("layer2", {}).get("fields", {}).items():
        if fdef.get("type") == "enum" and fdef.get("values"):
            out[fname.lower()] = list(fdef["values"])
        for sf in fdef.get("sub_fields", []) or []:
            if sf.get("type") == "enum" and sf.get("values") and sf.get("name"):
                out[str(sf["name"]).lower()] = list(sf["values"])

    return out


def _norm(value: str | None, allowed: list[str], field_key: str = "") -> str | None:
    """Map free-text LLM output to the nearest allowed enum value from allowed (declared
    in the schema for this field). Falls back through: exact ci match -> substring match ->
    fuzzy nearest -> original value unchanged (so it's still visible/flaggable, never silently
    dropped). field_key is unused here now — kept in the signature for call-site compatibility;
    a prior version had a hardcoded per-field keyword remap table that silently went stale across
    schema regenerations (referenced field names that no longer existed in the live schema) —
    removed rather than fixed, since substring+fuzzy already covers the same job generically."""
    if value is None or not allowed:
        return value
    v = str(value).lower().strip()

    for a in allowed:
        if v == a.lower():
            return a

    for a in allowed:
        if a.lower() in v or v in a.lower():
            return a

    close = difflib.get_close_matches(v, [a.lower() for a in allowed], n=1, cutoff=0.6)
    if close:
        idx = [a.lower() for a in allowed].index(close[0])
        return allowed[idx]

    return value  # no confident match — leave as-is for human review


def _coerce_field_types(m: dict, schema: dict) -> dict:
    """Schema-on-write type enforcement — coerce each top-level field to match the TYPE its
    current schema declares, immediately after extraction and before the matrix ever reaches
    disk. This is the industry-standard fix for this class of bug (validate/coerce structured
    LLM output against the schema at write time — the pattern used by libraries like Instructor/
    Pydantic for LLM extraction, and by schema-registry "transformation layer" approaches for
    evolving data pipelines) rather than making every downstream reader defensively guess at
    shape. Found live via a full-project audit: 22 of ~49 fields had 2+ different Python types
    across just 23 records (e.g. adoption_barriers stored as a list in 4 records, a bare string
    in 1) because a field's declared shape changed across discovery/schema-reconcile runs and
    already-written records were never migrated.

    Scoped deliberately to the ONE safe, lossless case: a schema-declared "array" field that got
    written as a scalar (string/number/bool) — wrapped into a single-item list, never dropped.
    Does NOT touch object-vs-string drift (e.g. coindcx_trust's legacy nested-dict shape) —
    that case already has dedicated, non-lossy read-time normalizers in the dashboard renderer
    (_trust_gap_value etc.) that extract the richer sub-structure; blindly flattening it here
    would destroy exactly the data those normalizers depend on.
    """
    fields = schema.get("layer2", {}).get("fields", {})
    coerced = []
    for fname, fdef in fields.items():
        if fname not in m or m[fname] is None:
            continue
        v = m[fname]
        if fdef.get("type") == "array" and not isinstance(v, list):
            if isinstance(v, (str, int, float, bool)):
                m[fname] = [v]
                coerced.append(f"{fname}: {type(v).__name__} -> list")
            elif isinstance(v, dict):
                m[fname] = [v]
                coerced.append(f"{fname}: dict -> list-of-one")
    if coerced:
        m["_type_coerced"] = coerced
    return m


def _normalise_matrix(m: dict, schema: dict) -> dict:
    """Post-process extracted matrix to normalise every enum field the schema declares,
    at whatever nesting depth it appears — schema-driven, works for any project's field
    names, not just CoinDCX's.

    Also catches the "NEW: <label>" convention (master_prompt now tells the model to use this
    instead of force-fitting an answer that doesn't match any declared value) and records it to
    matrix["_unmatched_categories"] — surfaced in the Extraction Studio so a reviewer sees when
    real transcript content didn't fit the current schema, instead of it being silently lost."""
    enum_values = _collect_enum_values(schema)
    unmatched: list[dict] = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                p = f"{path}.{k}" if path else k
                if isinstance(v, str) and k.lower() in enum_values:
                    stripped = v.strip()
                    if re.match(r"(?i)^new\s*:\s*", stripped):
                        label = re.sub(r"(?i)^new\s*:\s*", "", stripped).strip()
                        unmatched.append({"path": p, "field": k, "proposed_value": label})
                        node[k] = label
                        node[f"_{k}_new_category"] = True
                    else:
                        node[k] = _norm(v, enum_values[k.lower()], field_key=k.lower())
                elif isinstance(v, (dict, list)):
                    walk(v, p)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    if enum_values:
        walk(m)
    if unmatched:
        m["_unmatched_categories"] = unmatched
    return m


# ── verbatim verification gate ────────────────────────────────────────────────
# Runs INSIDE extraction, before the matrix hits disk — unlike the old standalone
# verify_verbatims.py pass, which only annotated matrices after the fact with no effect on
# what actually got written. Schema-driven, same pattern as _collect_enum_values: reads which
# fields THIS schema marks "exact copy" (hard verbatim, meant to be one literal sentence — a
# failure there means the model invented/smoothed it) vs advisory content fields like
# all_passages.content (meant to be a close paraphrase digest per its own schema rule, not a
# literal excerpt — scored, never dropped).

def _collect_hard_verbatim_fields(schema: dict) -> set[str]:
    out: set[str] = set()

    def walk(node, key_hint: str | None = None):
        if not isinstance(node, dict):
            return
        if key_hint and "exact copy" in str(node.get("rule", "")).lower():
            out.add(key_hint.lower())
        for k, v in node.items():
            if isinstance(v, dict):
                walk(v, key_hint=k)

    walk(schema.get("layer1", {}).get("fields", {}))
    for fname, fdef in schema.get("layer2", {}).get("fields", {}).items():
        # Defensive: only a "string" field can legitimately be hard-verbatim — an enum label or a
        # boolean can never appear verbatim in the transcript. Schema-writing code shouldn't
        # produce this combo anymore, but guard here too for schemas already written to disk.
        if fdef.get("values") or fdef.get("type", "string") != "string":
            continue
        if "exact copy" in str(fdef.get("rule", "")).lower() or fdef.get("verbatim_field"):
            out.add(fname.lower())
        for sf in fdef.get("sub_fields", []) or []:
            if sf.get("values") or sf.get("type", "string") != "string":
                continue
            if sf.get("name") and ("exact copy" in str(sf.get("rule", "")).lower() or sf.get("verbatim_field")):
                out.add(str(sf["name"]).lower())
    return out


def _norm_match_text(t: str) -> str:
    t = t.lower()
    t = re.sub(r"[‘’“”–—]", "'", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def gate_matrix(matrix: dict, schema: dict, transcript_body: str) -> dict:
    """
    Hard-verbatim fields that fail the source-text check get nulled + flagged with
    `_<field>_unverified: true` — never left in place looking like a real quote. Advisory
    content fields (all_passages.content) are only scored via `_content_verified` per item,
    same as before, and roll up into `_quality_score` / `_quality_label` / `_needs_review` so a
    reviewer can spot-check flagged matrices in the Studio instead of the app silently trusting
    unverified narrative text as if it were grounded.
    """
    if not transcript_body:
        return matrix

    norm_source = _norm_match_text(transcript_body)
    hard_fields = _collect_hard_verbatim_fields(schema)
    stats = {"hard_checked": 0, "hard_dropped": 0, "soft_checked": 0, "soft_verified": 0}
    dropped_log = []

    def matches(v: str) -> tuple[bool, str]:
        raw = str(v).strip()
        # Model sometimes wraps an already-correct verbatim quote in its own extra pair of quote
        # marks (e.g. value is literally `"RBI norms build confidence."`) — that outer pair isn't
        # part of the transcript text, so leave it in and the exact/word-overlap check below fails
        # even though the underlying quote is genuinely verbatim. Strip one matched layer only.
        if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
            raw = raw[1:-1]
        nv = _norm_match_text(raw)
        if len(nv) < 12:
            return True, "too_short_to_check"
        if nv in norm_source:
            return True, "exact"
        words = nv.split()
        if len(words) >= 6:
            overlap = sum(1 for w in words if w in norm_source) / len(words)
            if overlap >= 0.75:
                return True, "word_overlap"
        return False, "no_match"

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                p = f"{path}.{k}" if path else k
                if isinstance(v, str) and v.strip() and k.lower() in hard_fields:
                    stats["hard_checked"] += 1
                    ok, _method = matches(v)
                    if not ok:
                        stats["hard_dropped"] += 1
                        dropped_log.append({"path": p, "value": v[:120]})
                        node[k] = None
                        node[f"_{k}_unverified"] = True
                elif isinstance(v, str) and k.lower() == "content" and len(v.split()) >= 8:
                    stats["soft_checked"] += 1
                    ok, method = matches(v)
                    node["_content_verified"] = ok
                    node["_content_verify_method"] = method
                    if ok:
                        stats["soft_verified"] += 1
                elif isinstance(v, (dict, list)):
                    walk(v, p)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(matrix)

    total = stats["hard_checked"] + stats["soft_checked"]
    if total:
        score = 100.0
        if stats["hard_checked"]:
            score -= 60.0 * (stats["hard_dropped"] / stats["hard_checked"])
        if stats["soft_checked"]:
            score -= 40.0 * (1 - stats["soft_verified"] / stats["soft_checked"])
        score = round(max(0.0, score), 1)
    else:
        score = 100.0
    label = "excellent" if score >= 80 else ("good" if score >= 60 else ("poor" if score >= 30 else "critical"))

    matrix["_quality_score"] = score
    matrix["_quality_label"] = label
    matrix["_needs_review"] = label in ("poor", "critical") or stats["hard_dropped"] > 0
    matrix["_gate"] = {**stats, "dropped": dropped_log}
    return matrix


def gate_matrices_by_quality(matrices: list[dict]) -> tuple[list[dict], list[dict]]:
    """Splits matrices into (kept, excluded). gate_matrix() has always computed
    _quality_score/_quality_label/_needs_review per matrix, but nothing downstream ever acted on
    it — a "critical"-quality matrix (verbatim fidelity check failed badly, score <30) rendered
    into every chart's aggregate counts exactly like a clean "excellent" one, with only a badge
    in Extraction Studio as the sole visibility. This is the first actual gate: "critical"
    matrices are excluded from aggregate chart data by default (the renderer surfaces a visible
    warning naming which respondents and why, never a silent drop). "poor" and above still
    render — this only stops the worst tier, extractions bad enough that the data likely isn't
    real, not merely thin or slightly stale."""
    kept, excluded = [], []
    for m in matrices:
        if m.get("_quality_label") == "critical":
            excluded.append(m)
        else:
            kept.append(m)
    return kept, excluded


def fields_populated_in_matrices(project_id: str) -> dict[str, int]:
    """Top-level field name -> how many existing matrices actually have it populated (non-null).
    Ground truth for what a schema regeneration is about to drop — used by the Extraction Studio's
    field-review panel so dropping a field that's already live data requires a conscious choice,
    not a silent side effect of the next discovery run."""
    project_dir = _DATA_DIR / "projects" / project_id
    matrices_dir = project_dir / "matrices"
    counts: dict[str, int] = {}
    if not matrices_dir.exists():
        return counts
    universal = {"respondent", "pain_points", "nps_signal", "emotional_resolution", "narrative_tags",
                 "doc_id", "filename", "word_count", "all_passages", "most_powerful_verbatim"}
    for mf in matrices_dir.glob("*_matrix.json"):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for k, v in m.items():
            if k.startswith("_") or k in universal or v in (None, "", [], {}):
                continue
            counts[k] = counts.get(k, 0) + 1
    return counts


def matrix_staleness(project_id: str, schema: dict) -> dict[str, list[str]]:
    """doc_id -> list of current-schema field names this matrix predates (extracted before those
    fields existed, or before a later schema regen added/renamed them). Matrices are never
    auto-updated when the schema gains a field — a matrix from before today's restore work, for
    instance, has no way to know it's now missing 16 fields the dashboard expects. This is pure
    visibility, not a fix — surfaced in the Extraction Studio so re-running extraction on stale
    files is a conscious choice, not a silent gap nobody notices until a chart looks thin."""
    project_dir = _DATA_DIR / "projects" / project_id
    matrices_dir = project_dir / "matrices"
    current_fields = set(schema.get("layer2", {}).get("fields", {}).keys())
    stale: dict[str, list[str]] = {}
    if not matrices_dir.exists():
        return stale
    for mf in matrices_dir.glob("*_matrix.json"):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        snapshot = set(m.get("_schema_fields_at_extraction") or [])
        if not snapshot:
            continue  # extracted before this tracking existed — no baseline to compare against
        missing = sorted(current_fields - snapshot)
        if missing:
            stale[m.get("doc_id", mf.stem)] = missing
    return stale


def pipeline_sync_status(project_id: str) -> dict:
    """Point-in-time check for whether schema / matrices / ui_config.json / value
    reconciliation are still in sync for a project. Each stage (Discovery, extraction,
    ui_config generation, reconcile_project) is independently re-runnable, so the same
    project can have its schema, matrices, and dashboard config each reflect a different
    moment — this doesn't fix that, it surfaces it, since nothing else today tells a
    dashboard viewer "this chart's data is stale" at a glance."""
    project_dir = _DATA_DIR / "projects" / project_id
    schema_path = project_dir / "schema" / "extraction_schema.json"
    ui_config_path = project_dir / "schema" / "ui_config.json"
    matrices_dir = project_dir / "matrices"
    recon_path = project_dir / "reconciliation_report.json"

    status = {"stale_matrices": {}, "ui_config_stale": False, "reconcile_stale": False,
              "warnings": []}

    if not schema_path.exists() or not matrices_dir.exists():
        return status

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception:
        return status

    status["stale_matrices"] = matrix_staleness(project_id, schema)
    if status["stale_matrices"]:
        n_fields = sum(len(v) for v in status["stale_matrices"].values())
        status["warnings"].append(
            f"{len(status['stale_matrices'])} matrix(es) predate the current schema "
            f"({n_fields} missing field-instances) — re-run extraction to backfill."
        )

    matrix_files = list(matrices_dir.glob("*_matrix.json"))
    if not matrix_files:
        return status
    latest_matrix_mtime = max(f.stat().st_mtime for f in matrix_files)

    if ui_config_path.exists() and ui_config_path.stat().st_mtime < latest_matrix_mtime:
        status["ui_config_stale"] = True
        status["warnings"].append(
            "ui_config.json predates the most recent extraction batch — regenerate the "
            "dashboard config to pick up new/changed fields."
        )
    if schema_path.stat().st_mtime > latest_matrix_mtime:
        status["warnings"].append(
            "extraction_schema.json was regenerated after the last extraction batch — "
            "matrices (and any ui_config built from them) reflect an older schema."
        )
    if recon_path.exists():
        try:
            recon = json.loads(recon_path.read_text(encoding="utf-8"))
            reconciled_at = recon.get("reconciled_at")
            if reconciled_at:
                recon_dt = datetime.fromisoformat(reconciled_at.replace("Z", "+00:00"))
                if recon_dt.timestamp() < latest_matrix_mtime:
                    status["reconcile_stale"] = True
                    status["warnings"].append(
                        "Value-level reconciliation hasn't re-run since the last extraction "
                        "batch — new label variants across respondents may be unmerged."
                    )
        except Exception:
            pass

    return status


# ── post-extraction reconciliation ────────────────────────────────────────────

def reconcile_project(project_id: str, model: str | None = None) -> dict:
    """
    Run once after a batch of matrices is committed. Per-transcript extraction happens
    independently, so if the schema's enum values changed between runs (e.g. discovery
    re-ran and proposed 'self_reliant_decision_maker' where an earlier run proposed
    'independent_decision_maker' for the same underlying archetype), those near-duplicates
    all pass individual normalisation cleanly but fragment what should be one category across
    the project. This clusters true synonyms and rewrites matrices with one canonical label —
    never silently: every merge is logged to reconciliation_report.json.
    """
    project_dir = _DATA_DIR / "projects" / project_id
    matrices_dir = project_dir / "matrices"
    matrix_files = sorted(matrices_dir.glob("*_matrix.json"))
    if not matrix_files:
        return {"merged_fields": {}, "matrices_checked": 0}

    matrices = []
    for mf in matrix_files:
        try:
            matrices.append((mf, json.loads(mf.read_text(encoding="utf-8"))))
        except Exception:
            continue

    field_values: dict[str, set] = {}

    def collect(node):
        if isinstance(node, dict):
            for k, v in node.items():
                # Internal audit/gate metadata (_gate, _verification, _quality_score,
                # _step1_text, etc.) — never respondent-facing categorical data. Found live:
                # without this skip, _gate.dropped[].path values like
                # "pain_points[0].verbatim_quote" and _verification's per-field "confidence"
                # scores were being treated as real fields and "reconciled" alongside
                # genuine ones like city/segment/reaction.
                if k.startswith("_"):
                    continue
                if isinstance(v, str) and v.strip():
                    field_values.setdefault(k.lower(), set()).add(v.strip())
                elif isinstance(v, (dict, list)):
                    collect(v)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    for _, m in matrices:
        collect(m)

    merge_map: dict[str, dict[str, str]] = {}

    for field, values in field_values.items():
        if len(values) < 2 or len(values) > 15:
            continue
        # skip free-text-y fields (verbatim quotes, narrative text) — closed categories only
        if any(len(v.split()) > 6 for v in values):
            continue

        prompt = (
            "These are the distinct values actually used for the field "
            f"'{field}' across {len(matrices)} interviews in one qualitative research project:\n"
            f"{json.dumps(sorted(values), ensure_ascii=False)}\n\n"
            "Some may be synonyms created by re-running extraction at different times "
            "(e.g. 'self_reliant_decision_maker' and 'independent_decision_maker' meaning the "
            "same thing). Cluster true synonyms and pick ONE canonical label per cluster "
            "(prefer the more descriptive one). Genuinely distinct categories must map to "
            "themselves, unchanged — do not force a merge that isn't real.\n\n"
            "Return ONLY a JSON object mapping every input value to its canonical label, e.g.\n"
            '{"value1": "canonical1", "value2": "canonical1", "value3": "value3"}'
        )
        try:
            raw = call_llm([{"role": "user", "content": prompt}], max_tokens=1500, temp=0.1,
                            json_mode=True, model=model)
            mapping = json.loads(raw)
        except Exception as e:
            print(f"  reconcile: field '{field}' skipped ({e})")
            continue

        # Require a genuinely non-empty mapping — found live: an empty {} response from the
        # model (call succeeded, but returned nothing usable) passed the old check anyway,
        # since 0 < len(values) is trivially true, and got reported as a fake "successful
        # merge" with an empty map (harmless when applied, since nothing matches an empty
        # dict, but misleading in the report — looks like real work happened when it didn't).
        if not isinstance(mapping, dict) or not mapping:
            print(f"  reconcile: field '{field}' got an empty/invalid mapping from the model — skipped, not counted as a merge")
            continue
        if len(set(mapping.values())) < len(values):
            merge_map[field] = mapping

    if not merge_map:
        report = {"merged_fields": {}, "matrices_checked": len(matrices)}
        print("  Reconciliation: no fragmentation found — nothing to merge.")
        return report

    def apply(node):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                fk = k.lower()
                if isinstance(v, str) and fk in merge_map and v.strip() in merge_map[fk]:
                    node[k] = merge_map[fk][v.strip()]
                elif isinstance(v, (dict, list)):
                    apply(v)
        elif isinstance(node, list):
            for item in node:
                apply(item)

    for mf, m in matrices:
        apply(m)
        mf.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    report = {
        "project_id": project_id,
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "matrices_checked": len(matrices),
        "merged_fields": merge_map,
    }
    report_path = project_dir / "reconciliation_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Reconciliation: merged {len(merge_map)} field(s) — report at {report_path}")
    return report


def reconcile_schema_fields(project_id: str) -> dict:
    """
    One level up from reconcile_project() — that merges duplicate VALUES within one field
    (e.g. 'self_reliant' vs 'independent' archetype labels). This merges duplicate FIELD NAMES
    in the schema itself (e.g. 'coindcx_trust' vs 'crypto_trust_gap' vs 'trust_in_digital_platforms'
    — same concept, invented under a different name by a different discovery run).

    Root cause this fixes: every discovery run is a fresh LLM call with no memory of what fields
    already exist, so it has no way to know a concept is already covered under a different name.
    merge-protection (added earlier) stops fields from being silently DROPPED across regenerations,
    but does nothing to stop them being silently DUPLICATED — found live: this project's schema grew
    from 19 well-populated fields to 70, with 51 of them near-duplicates of the other 19, requiring
    a full manual audit against matrix ground truth to untangle. This function is that audit,
    automated, so it doesn't have to be done by hand again.

    Uses field name + description + real population count (grounds the LLM in what's actually
    used, not just plausible-sounding names) to decide clusters. Never merges on name alone.
    """
    project_dir = _DATA_DIR / "projects" / project_id
    schema_path = project_dir / "schema" / "extraction_schema.json"
    if not schema_path.exists():
        return {"merged_fields": {}, "fields_checked": 0}

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    fields = schema.get("layer2", {}).get("fields", {})
    if len(fields) < 2:
        return {"merged_fields": {}, "fields_checked": len(fields)}

    pop_counts = fields_populated_in_matrices(project_id)

    field_summary = []
    for fname, fdef in fields.items():
        desc = fdef.get("description", "")
        sub_fields = fdef.get("sub_fields") or []
        sub_names = [sf.get("name") for sf in sub_fields]
        # The enum `values` list is the single strongest duplicate signal available — two fields
        # with byte-identical (or near-identical) value sets are almost always the same concept
        # discovered twice under different names. Omitting this (as a prior version of this
        # function did) forces the LLM to judge purely off name/description text and misses
        # exactly the duplicates that matter most — found live: 'gold_formats_aware' and
        # 'gold_awareness' had identical value lists and were never merged because the LLM
        # never saw the values at all.
        field_summary.append({
            "field_name": fname,
            "description": desc,
            "values": fdef.get("values"),
            "sub_fields": [
                {"name": sf.get("name"), "values": sf.get("values")} for sf in sub_fields
            ] if sub_fields else sub_names,
            "populated_in_n_matrices": pop_counts.get(fname, 0),
        })
    field_summary.sort(key=lambda x: -x["populated_in_n_matrices"])

    prompt = (
        "These are all the fields currently declared in one qualitative research project's extraction "
        "schema, each with its description, its enum 'values' list (if any), any sub-fields with their "
        "own values, and how many real interviews actually have data for it (higher = more established, "
        "lower = likely a recent one-off from a single discovery run that reinvented an existing concept "
        "under a new name):\n\n"
        f"{json.dumps(field_summary, ensure_ascii=False, indent=2)}\n\n"
        "Find genuine semantic duplicates — fields that measure the SAME underlying concept under "
        "different names. Duplicate clusters are not always pairs — a concept can be reinvented THREE OR "
        "MORE times across different discovery runs (e.g. 'trust_builders', 'trust_indicators', and "
        "'trust_conditions' all separately measuring what builds platform trust); find the FULL cluster, "
        "not just the closest pair.\n\n"
        "Strongest signal, check this first: identical or near-identical 'values' lists (or sub-field "
        "values lists) between two fields is almost always the same concept under a different name, "
        "even if the field names and descriptions look different on the surface. Matching or highly "
        "overlapping value sets should be treated as strong evidence for merging.\n\n"
        "For each duplicate cluster, pick the field with the HIGHEST populated_in_n_matrices as "
        "canonical (ties: pick the more descriptive name). Distinct concepts map to themselves "
        "unchanged — do not force a merge that isn't real.\n\n"
        "Return ONLY a JSON object containing ONE ENTRY PER FIELD THAT SHOULD BE MERGED — key is the "
        "duplicate field_name to drop, value is its canonical field_name to keep. Omit every field that "
        "is NOT part of a duplicate cluster (most fields will not appear in your output at all — do not "
        "include self-mappings or the full field list)."
    )

    # Empty/malformed responses observed live from at least one model on this call — retry once
    # with a fresh call before giving up, rather than surfacing a transient failure as "no
    # duplicates found" (which would be silently wrong, not just unlucky).
    mapping = None
    last_err = None
    for attempt in range(2):
        try:
            raw = call_llm([{"role": "user", "content": prompt}], max_tokens=6000, temp=0.1, json_mode=True)
            mapping = _extract_json(raw)  # strips ```json fences — a fallback model wrapped its
                                            # response in them despite json_mode, found live
            if mapping is None:
                raise ValueError(f"could not parse JSON from response: {raw[:200]!r}")
            try:
                from audit_trail import log_llm_call
                log_llm_call(project_id, None, "schema_field_reconciliation", "default", 0.1, 3000,
                             prompt, raw, parsed_result=mapping)
            except Exception:
                pass
            break
        except Exception as e:
            last_err = e
            print(f"  Schema reconciliation attempt {attempt+1} failed: {e}")
    if mapping is None:
        return {"merged_fields": {}, "fields_checked": len(fields), "error": str(last_err)}

    if not isinstance(mapping, dict):
        return {"merged_fields": {}, "fields_checked": len(fields)}

    # Only real merges — canonical != original, and canonical must actually exist in the schema.
    merge_map = {k: v for k, v in mapping.items()
                 if k in fields and v in fields and k != v}

    if not merge_map:
        print("  Schema reconciliation: no field-name duplicates found.")
        return {"merged_fields": {}, "fields_checked": len(fields)}

    print(f"  Schema reconciliation: merging {len(merge_map)} duplicate field(s): {merge_map}")

    # Drop the non-canonical field defs from the schema.
    new_fields = {k: v for k, v in fields.items() if k not in merge_map}
    schema["layer2"]["fields"] = new_fields
    schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")

    # Rewrite matrices: copy any data sitting under a duplicate name into the canonical field
    # (only if the canonical slot is currently empty — never overwrite real canonical data with a
    # thinner duplicate's data), then remove the duplicate key.
    matrices_dir = project_dir / "matrices"
    n_matrices_touched = 0
    for mf in matrices_dir.glob("*_matrix.json"):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        changed = False
        for dup_name, canon_name in merge_map.items():
            if dup_name not in m:
                continue
            dup_val = m.pop(dup_name)
            changed = True
            canon_empty = m.get(canon_name) in (None, "", [], {})
            if canon_empty and dup_val not in (None, "", [], {}):
                m[canon_name] = dup_val
        if changed:
            mf.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
            n_matrices_touched += 1

    report = {
        "project_id": project_id,
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "fields_checked": len(fields),
        "merged_fields": merge_map,
        "matrices_touched": n_matrices_touched,
        "schema_fields_after": len(new_fields),
    }
    report_path = project_dir / "schema_reconciliation_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved: {report_path}")
    return report


# ── doc_id from filename ──────────────────────────────────────────────────────

def _doc_id_from_filename(filename: str) -> str:
    """DI 10_Male_Stock Investor_Lucknow.m4a.docx → DI_10"""
    m = re.match(r"DI\s*(\d+)", filename, re.IGNORECASE)
    if m:
        return f"DI_{m.group(1)}"
    return re.sub(r"[^\w]", "_", filename)[:40]


# ── per-transcript extraction ─────────────────────────────────────────────────

def split_master_prompt(master_prompt: str) -> tuple[str, str]:
    """Split master_prompt.txt into (step1_section, step2_section). Supports both
    'PHASE 2' (new format) and 'STEP 2' (legacy format). Shared by CLI + Extraction Studio UI."""
    for _sep_label, _sep_marker in [("PHASE 2", "---\n\nPHASE 2"), ("STEP 2", "---\n\nSTEP 2")]:
        if _sep_marker in master_prompt:
            step1_section = master_prompt.split(_sep_marker)[0].strip()
            step2_section = _sep_label + " —" + master_prompt.split(_sep_marker)[1].split("—", 1)[-1]
            return step1_section, step2_section
    # Fallback: treat everything before first "{" as step1, rest as step2
    _brace_pos = master_prompt.find('\n{')
    if _brace_pos != -1:
        return master_prompt[:_brace_pos].strip(), master_prompt[_brace_pos:].strip()
    return master_prompt, master_prompt


def run_step1(step1_section: str, body: str, model: str | None = None,
              project_id: str | None = None, doc_id: str | None = None) -> str:
    """Step 1 — free-form reasoning pass over one transcript. Importable so the Extraction
    Studio UI can call it directly and pause here for human review before Step 2 runs.

    project_id/doc_id are optional purely for audit-trail logging — when given, the exact
    prompt sent and raw response received are appended to data/projects/{id}/audit_trail.jsonl.
    """
    user_content = (
        f"{step1_section}\n\n"
        "---\nTRANSCRIPT BEGIN\n---\n\n"
        f"{body.strip()}\n\n"
        "---\nTRANSCRIPT END\n---"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert qualitative research analyst specialising in "
                "financial products and consumer behaviour in India. "
                "Be analytical, evidence-based, and cite exact transcript language."
            ),
        },
        {"role": "user", "content": user_content},
    ]
    result = call_llm(messages, max_tokens=_STEP1_TOKENS, temp=0.3, json_mode=False, model=model,
                       reasoning_effort="medium")
    if project_id:
        try:
            from audit_trail import log_llm_call
            log_llm_call(project_id, doc_id, "step1_free_form_analysis",
                         model or "default", 0.3, _STEP1_TOKENS, user_content, result)
        except Exception:
            pass  # audit logging must never break the actual extraction
    return result


def run_step2(step2_section: str, step1_text: str, respondent_hint: dict, model: str | None = None,
              project_id: str | None = None, doc_id: str | None = None) -> str:
    """Step 2 — schema-constrained structured JSON extraction, using an (optionally
    human-edited) Step 1 result as its evidence source. Importable for the same reason.
    project_id/doc_id: see run_step1 — same optional audit-trail logging."""
    user_content = (
        f"STEP 1 ANALYSIS (use this as your evidence source):\n{step1_text}\n\n"
        "---\n\n"
        f"{step2_section}\n\n"
        "Pre-filled fields (keep exactly as shown — do not modify):\n"
        f"{json.dumps(respondent_hint, ensure_ascii=False)}\n\n"
        "Now output the complete JSON. Follow all ENUM CONSTRAINTS exactly."
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a precise JSON extractor. Output ONLY valid JSON — "
                "no markdown, no commentary, no code fences. "
                "Follow ENUM CONSTRAINTS exactly — output the exact string shown, no free text."
            ),
        },
        {"role": "user", "content": user_content},
    ]
    result = call_llm(messages, max_tokens=_STEP2_TOKENS, temp=0.1, json_mode=True, model=model,
                       reasoning_effort="low")
    if project_id:
        try:
            from audit_trail import log_llm_call
            parsed = _extract_json(result)
            log_llm_call(project_id, doc_id, "step2_structured_extraction",
                         model or "default", 0.1, _STEP2_TOKENS, user_content, result,
                         parsed_result=parsed)
        except Exception:
            pass
    return result


def run_reflection_retry(parsed: dict, schema: dict, body: str, model: str | None = None,
                          project_id: str | None = None, doc_id: str | None = None) -> dict:
    """
    Shared self-correction step, called after every Step 2 extraction that's already gone
    through _coerce_field_types + _normalise_matrix + gate_matrix — importable so BOTH the CLI
    pipeline (_extract_transcript) and the Extraction Studio's "Finalize" button in
    quote_explorer.py get the same protection. Found live via a full-project audit: the Studio's
    Finalize block had re-implemented coerce/normalise/gate inline but never called this step,
    so any respondent finalized through the Studio UI (as opposed to the CLI) silently skipped
    self-correction entirely — confirmed on a real respondent (DI_1, re-extracted via the Studio
    this session) that had 5/6 hard-verbatim fields dropped by the gate with
    `_reflection_retry_attempted` never even set, because the retry code was simply never
    reached from that code path.

    Prior behaviour: a dropped hard-verbatim field was simply nulled and the whole matrix
    flagged _needs_review — the model never learned it failed, so the same paraphrase-instead-
    of-copy mistake had no chance to self-correct. Research on schema-optimized extraction
    (PARSE/SCOPE, arXiv 2510.08623) found that telling the model exactly which fields failed
    verification and why, then retrying ONLY those fields, cuts errors ~92% versus a blind
    re-prompt or just accepting the failure. One retry, scoped to the actual failures, not a
    full re-extraction — cheap and targeted.

    v2 fix (found live via the audit trail): the first version retried a whole top-level field
    (e.g. "pain_points") by asking the model for JUST that key, then overwrote the original with
    whatever thin structure came back — which silently destroyed severity/issue_description/
    product_area on every pain point, not just the one broken verbatim_quote. Fix: send the
    model its OWN current value for each retried field as context, and explicitly instruct it to
    preserve every other key and only touch the specific verbatim sub-fields that failed —
    including permission to leave verbatim_quote null (keeping issue_description) when a pain
    point is a genuine synthesis across the interview with no single quotable line, rather than
    forcing it to keep re-paraphrasing the same non-existent quote.
    """
    _dropped = parsed.get("_gate", {}).get("dropped", [])
    if not _dropped:
        return parsed

    _retry_fields = sorted({d["path"].split(".")[0].split("[")[0] for d in _dropped})
    _failure_lines = "\n".join(
        f'- {d["path"]}: you wrote "{d["value"]}" — this exact text was NOT found in the '
        f"transcript (character-for-character). You paraphrased, cleaned up grammar, or "
        f"invented it."
        for d in _dropped
    )
    _current_values = {fname: parsed.get(fname) for fname in _retry_fields}
    _retry_prompt = f"""Your previous extraction for these specific fields failed verbatim
verification — the text you wrote does not appear character-for-character in the transcript:

{_failure_lines}

This usually happens for one of two reasons:
1. The source transcript has broken grammar, code-switching, or run-on speech, and you
   (understandably) cleaned it up into readable English instead of copying it exactly.
2. The point you identified is a genuine SYNTHESIS across multiple parts of the interview — a real
   pattern you correctly noticed, but not something any single sentence states verbatim.

For case 1: find the closest matching passage below and copy it EXACTLY as written, including any
broken grammar, code-switching, or awkward phrasing. Do NOT clean up the grammar.
For case 2: set the verbatim field to null instead of inventing or re-paraphrasing a quote — the
non-verbatim fields (like issue_description) can still describe the synthesized insight; only the
literal quote field needs to be null. Do not guess a third time.

Here is your own current value for each affected field, unchanged — copy every key EXACTLY as
shown EXCEPT the specific verbatim text named above, which you must either correct (case 1) or
null out (case 2). Do not drop, rename, or omit any other key (severity, issue_description,
product_area, etc. must survive unchanged):
{json.dumps(_current_values, ensure_ascii=False, indent=2)}

TRANSCRIPT:
{body.strip()}

Return ONLY a JSON object with these same top-level keys, same structure as shown above, values
corrected per the rules: {', '.join(_retry_fields)}
Return ONLY valid JSON, no markdown fences, no explanation."""

    try:
        _retry_raw = call_llm_safe(
            [{"role": "user", "content": _retry_prompt}],
            max_tokens=2000, temp=0.05, json_mode=True, model=model,
        )
        _retry_parsed = _extract_json(_retry_raw) if _retry_raw else None
    except Exception:
        _retry_raw = None
        _retry_parsed = None

    if project_id:
        try:
            from audit_trail import log_llm_call
            log_llm_call(project_id, doc_id, "reflection_retry", model or "default", 0.05, 2000,
                         _retry_prompt, _retry_raw or "", parsed_result=_retry_parsed,
                         extra={"fields_retried": _retry_fields, "gate_before_retry": _dropped})
        except Exception:
            pass

    if _retry_parsed:
        for fname in _retry_fields:
            if fname not in _retry_parsed:
                continue
            _new_val = _retry_parsed[fname]
            _old_val = _current_values.get(fname)
            # Safety check: for array fields, only accept the retry if it's still a list of the
            # same length — a shorter/malformed list means the model dropped items instead of
            # correcting them in place, and the original (with its correct severity/
            # issue_description/etc.) is safer to keep than a truncated replacement.
            if isinstance(_old_val, list):
                if isinstance(_new_val, list) and len(_new_val) == len(_old_val):
                    parsed[fname] = _new_val
                # else: silently keep original — malformed retry, don't risk data loss
            elif _new_val not in (None, ""):
                parsed[fname] = _new_val
            elif _new_val is None and isinstance(_old_val, dict):
                parsed[fname] = _new_val
        # re-normalise and re-gate after the retry so _quality_score reflects the real outcome,
        # not the pre-retry state — never let a stale score look better or worse than reality.
        parsed = _coerce_field_types(parsed, schema)
        parsed = _normalise_matrix(parsed, schema)
        parsed = gate_matrix(parsed, schema, body)
        parsed["_reflection_retry_attempted"] = True
        parsed["_reflection_retry_fields"] = _retry_fields

    return parsed


def _extract_transcript(
    client,
    md_path: Path,
    master_prompt: str,
    schema: dict,
    doc_id: str,
    source_filename: str,
    model_index: int = 0,
    model: str | None = None,
    project_id: str | None = None,
) -> tuple[dict, str]:
    """
    Run two-step extraction for a single transcript .md file.
    Returns (result_dict, status_string).
    result_dict has keys: doc_id, step1_text, matrix, raw_step2 (on parse fail).
    """
    md_text = md_path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(md_text)
    step1_section, step2_section = split_master_prompt(master_prompt)

    # ── Step 1: free-form analysis ────────────────────────────────────────────
    step1_text = run_step1(step1_section, body, model=model, project_id=project_id, doc_id=doc_id)

    # ── Step 2: structured JSON extraction ────────────────────────────────────
    respondent_hint = {
        "doc_id": doc_id,
        "filename": source_filename,
        "word_count": len(body.split()),
        "respondent": {
            "city": meta.get("city"),
            "segment": meta.get("segment"),
            "gender": meta.get("gender"),
            "age_band": None,
            "occupation": None,
        },
    }

    step2_text = run_step2(step2_section, step1_text, respondent_hint, model=model,
                            project_id=project_id, doc_id=doc_id)

    parsed = _extract_json(step2_text)

    if parsed is None:
        return (
            {
                "doc_id": doc_id,
                "step1_text": step1_text,
                "raw_step2": step2_text,
                "parse_error": True,
            },
            "PARSE_ERROR",
        )

    # ensure required identifiers are present
    parsed.setdefault("doc_id", doc_id)
    parsed.setdefault("filename", source_filename)
    parsed.setdefault("word_count", len(body.split()))
    if "respondent" not in parsed:
        parsed["respondent"] = respondent_hint["respondent"]
    else:
        for k, v in respondent_hint["respondent"].items():
            parsed["respondent"].setdefault(k, v)

    # normalise enum fields + enforce declared types (schema-on-write — see _coerce_field_types)
    parsed = _coerce_field_types(parsed, schema)
    parsed = _normalise_matrix(parsed, schema)
    parsed = gate_matrix(parsed, schema, body)
    parsed = run_reflection_retry(parsed, schema, body, model=model,
                                   project_id=project_id, doc_id=doc_id)

    parsed["_step1_text"] = step1_text
    parsed["_extracted_at"] = datetime.now(timezone.utc).isoformat()
    parsed["_schema_fields_at_extraction"] = sorted(schema.get("layer2", {}).get("fields", {}).keys())

    return (
        {"doc_id": doc_id, "matrix": parsed},
        "OK",
    )


# ── main pipeline ─────────────────────────────────────────────────────────────

def run_extraction(project_id: str, force: bool = False, single_file: str | None = None):
    """
    Run full extraction for a project or a single transcript file.
    """
    project_dir = _DATA_DIR / "projects" / project_id
    project_json_path = project_dir / "project.json"

    if not project_json_path.exists():
        print(f"ERROR: project.json not found at {project_json_path}")
        sys.exit(1)

    project_cfg = json.loads(project_json_path.read_text(encoding="utf-8"))

    schema_path = _DATA_DIR / project_cfg["data_paths"]["schema"]
    prompt_path = project_dir / "schema" / "master_prompt.txt"
    transcripts_processed_dir = project_dir / "transcripts" / "processed"
    matrices_dir = project_dir / "matrices"

    for p in [schema_path, prompt_path]:
        if not p.exists():
            print(f"ERROR: required file not found: {p}")
            sys.exit(1)

    matrices_dir.mkdir(parents=True, exist_ok=True)

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    master_prompt = prompt_path.read_text(encoding="utf-8")

    # load processed index to map source filenames → .md paths
    index_path = project_dir / "transcripts" / "processed_index.json"
    if not index_path.exists():
        print(f"ERROR: processed_index.json not found. Run docx_to_md.py first.")
        sys.exit(1)

    index = json.loads(index_path.read_text(encoding="utf-8"))

    # collect files to process
    if single_file:
        if single_file not in index:
            # try partial match
            matches = [k for k in index if single_file in k]
            if not matches:
                print(f"ERROR: file '{single_file}' not found in processed index")
                sys.exit(1)
            single_file = matches[0]
        items = [(single_file, index[single_file])]
    else:
        items = list(index.items())

    # filter out error entries
    items = [(f, v) for f, v in items if v.get("status") in ("ok", "skipped")]

    total = len(items)
    if total == 0:
        print("No processable transcripts found.")
        sys.exit(0)

    client = _get_client()
    report = {}
    llm_calls = 0
    llm_failures = 0

    for i, (source_filename, entry) in enumerate(items, 1):
        doc_id = _doc_id_from_filename(source_filename)
        meta = entry.get("metadata", {})
        segment = meta.get("segment", "")
        city = meta.get("city", "")
        gender = meta.get("gender", "")
        label = f"[{i}/{total}] {doc_id} {gender} {segment} {city}"

        matrix_path = matrices_dir / f"{doc_id}_matrix.json"
        if matrix_path.exists() and not force:
            print(f"{label} — SKIP (matrix exists)")
            report[source_filename] = {"status": "skipped", "doc_id": doc_id}
            continue

        # resolve .md path
        md_rel = entry.get("output_md")
        if md_rel:
            md_path = project_dir / md_rel
        else:
            # fallback: look in processed dir
            stem = re.sub(r"[^\w\-]", "_", source_filename.replace(".docx", ""))
            md_path = transcripts_processed_dir / (stem + ".md")

        if not md_path.exists():
            print(f"{label} — ERROR: .md not found at {md_path}")
            report[source_filename] = {"status": "error", "error": f"md not found: {md_path}", "doc_id": doc_id}
            continue

        try:
            print(f"{label} — Step1...", end="", flush=True)
            llm_calls += 2  # step1 + step2
            result, status = _extract_transcript(
                client,
                md_path,
                master_prompt,
                schema,
                doc_id,
                source_filename,
                project_id=project_id,
            )

            if status == "OK":
                matrix_path.write_text(
                    json.dumps(result["matrix"], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                q_label = result["matrix"].get("_quality_label", "n/a")
                q_flag = " [NEEDS REVIEW]" if result["matrix"].get("_needs_review") else ""
                print(f" Step2 OK — saved ({matrix_path.name}) [quality: {q_label}]{q_flag}")
                report[source_filename] = {
                    "status": "ok", "doc_id": doc_id, "matrix_file": matrix_path.name,
                    "quality_label": q_label, "needs_review": result["matrix"].get("_needs_review", False),
                }

            else:  # PARSE_ERROR
                raw_path = matrices_dir / f"{doc_id}_raw_step2.txt"
                raw_path.write_text(result.get("raw_step2", ""), encoding="utf-8")
                step1_path = matrices_dir / f"{doc_id}_step1.txt"
                step1_path.write_text(result.get("step1_text", ""), encoding="utf-8")
                print(f" Step2 PARSE_ERROR — raw saved to {raw_path.name}")
                report[source_filename] = {
                    "status": "parse_error",
                    "doc_id": doc_id,
                    "raw_file": raw_path.name,
                }

        except (RuntimeError, LLMCallError) as exc:
            llm_failures += 1
            print(f" ERROR: {exc}")
            report[source_filename] = {"status": "error", "error": str(exc), "doc_id": doc_id}
        except Exception as exc:
            print(f" UNEXPECTED ERROR: {exc}")
            report[source_filename] = {"status": "error", "error": str(exc), "doc_id": doc_id}

    # ── write extraction report ───────────────────────────────────────────────
    ok = sum(1 for v in report.values() if v["status"] == "ok")
    skipped = sum(1 for v in report.values() if v["status"] == "skipped")
    errors = sum(1 for v in report.values() if v["status"] in ("error", "parse_error"))

    extraction_report = {
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "ok": ok,
        "skipped": skipped,
        "errors": errors,
        "llm_telemetry": {"calls_attempted": llm_calls, "hard_failures": llm_failures},
        "files": report,
    }

    report_path = project_dir / "extraction_report.json"
    report_path.write_text(
        json.dumps(extraction_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nExtraction complete: {ok} OK, {skipped} skipped, {errors} errors")
    print(f"Report: {report_path}")

    # Auto-upload matrices to Drive so cloud can sync them
    try:
        from infoleap.gdrive.client import DriveClient
        _dc = DriveClient()
        if _dc._svc is not None:
            _fid = _dc.upload_qual_matrices(project_id, str(project_dir / "matrices"))
            if _fid:
                print(f"Drive: matrices.zip uploaded ({_fid})")
    except Exception as _e:
        print(f"Drive upload skipped: {_e}")


def main():
    parser = argparse.ArgumentParser(description="Run LLM extraction pipeline on transcripts")
    parser.add_argument("--project", required=True, help="Project ID (e.g. karat-coindcx)")
    parser.add_argument("--force", action="store_true", help="Re-extract already processed files")
    parser.add_argument("--file", default=None, help="Extract a single source filename only")
    parser.add_argument("--reconcile", action="store_true",
                         help="Only run the post-extraction reconciliation pass (no extraction)")
    args = parser.parse_args()
    if args.reconcile:
        reconcile_project(args.project)
    else:
        run_extraction(args.project, force=args.force, single_file=args.file)


if __name__ == "__main__":
    main()
