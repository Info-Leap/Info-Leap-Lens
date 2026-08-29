"""
Findings Generator — produces research findings per DG section from extracted matrices.

For each section in report_structure.json:
1. Aggregates relevant schema fields across all matrices
2. Pulls verified verbatims only (quality_score >= 60)
3. Generates AI narrative: finding + evidence + strategic implication
4. Caches to projects/{id}/findings/{section_id}.json

Usage:
    python findings_generator.py --project karat-coindcx
    python findings_generator.py --project karat-coindcx --section concept_reaction
    python findings_generator.py --project karat-coindcx --force   # regen all
"""

import argparse, json, os, re, sys, time
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

from dotenv import load_dotenv
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(str(_ENV_FILE), override=True)

from openai import OpenAI

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_FREE_MODELS = [
    # Best free models for research findings (large, high reasoning quality)
    "deepseek/deepseek-r1:free",           # 671B reasoning — best for analytical work
    "qwen/qwen3-235b-a22b:free",           # 235B — strong analytical + long context
    "moonshotai/kimi-k2.6:free",           # Good reasoning, reliable
    "meta-llama/llama-4-scout:free",       # Reliable, fast
    "openai/gpt-oss-120b:free",            # 120B general
    "microsoft/phi-4-reasoning:free",      # Reasoning specialist
    "google/gemma-4-31b-it:free",          # Smaller fallback only
]

def _get_models(env_file: Path) -> list[str]:
    """Strictly free models only — no paid fallback."""
    return list(_FREE_MODELS)

_FINDING_MAX_TOKENS = 1800
_BACKOFF = 4


def _get_client() -> OpenAI:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        print("ERROR: OPENROUTER_API_KEY missing"); sys.exit(1)
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


def _call_llm(client, messages, max_tokens=1800, temp=0.3, retries=3) -> str:
    """Pinned-primary-model strategy (2026-07-27 fix — see the 2026-07-17 in-app rebuild's
    _call_llm_pinned() in quote_explorer.py, which this now mirrors): the FIRST model in
    _FREE_MODELS is the designated primary and is retried (with backoff) on transient failures
    (429/503/502) before falling through to the next model. This used to round-robin across all
    7 models every single call regardless of success — meaning the same section, re-run twice,
    could get a materially different narrative depending on which model happened to answer that
    attempt. Findings are meant to be reproducible; rotation is now fallback-only, not the
    default strategy.
    """
    models = _get_models(_ENV_FILE)
    for model_idx, model in enumerate(models):
        for retry in range(retries):
            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, temperature=temp
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                err = str(e)
                if "429" in err or "503" in err or "502" in err:
                    wait = _BACKOFF * (2 ** retry)
                    print(f"    [{model}] unavailable -> wait {wait}s "
                          f"(retry {retry+1}/{retries}, model {model_idx+1}/{len(models)})")
                    time.sleep(wait)
                else:
                    # Non-transient error (bad request, auth, etc.) — retrying the same model
                    # won't help, move straight to the next one instead of burning retries.
                    print(f"    [{model}] failed non-transiently ({err[:120]}) -> next model")
                    break
    return ""


def _safe_get(obj, dotpath: str):
    """Get nested value by dot.path. Returns None if missing."""
    parts = dotpath.replace("[]", "").split(".")
    cur = obj
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list):
            return cur
        else:
            return None
    return cur


def _get_verified_verbatims(matrix: dict, field_path: str, max_n: int = 5) -> list[dict]:
    """
    Extract verified verbatims from a specific field path.
    Returns list of {text, doc_id, segment, city, verified}.
    """
    results = []
    doc_id = matrix.get("doc_id", "")
    resp = matrix.get("respondent", {})
    seg = resp.get("segment") or resp.get("brand_owned") or ""
    city = resp.get("city", "")
    qs = matrix.get("_quality_score", 0) or 0

    # Handle array fields like "key_claim_reactions[].verbatim"
    if "[]." in field_path:
        base, subkey = field_path.split("[].")
        items = _safe_get(matrix, base) or []
        for item in items:
            if not isinstance(item, dict): continue
            text = item.get(subkey, "")
            if not text or len(str(text)) < 8: continue
            verified = item.get(f"_{subkey}_verified")
            if verified is False: continue  # skip confirmed paraphrases
            results.append({
                "text": str(text),
                "doc_id": doc_id, "segment": seg, "city": city,
                "verified": verified, "quality_score": qs,
                "claim": item.get("claim", ""),
                "reaction": item.get("reaction", ""),
            })
    else:
        text = _safe_get(matrix, field_path)
        if text and len(str(text)) > 8:
            # Check if this field has a verification result
            field_results = (matrix.get("_verification", {}) or {}).get("field_results", {})
            check = field_results.get(field_path, {})
            if check.get("found") is False: return []  # confirmed paraphrase
            results.append({
                "text": str(text),
                "doc_id": doc_id, "segment": seg, "city": city,
                "verified": check.get("found"), "quality_score": qs,
            })

    return results[:max_n]


def _aggregate_section(matrices: list[dict], section: dict) -> dict:
    """
    Aggregate all matrix data for a single report section.
    Returns structured context dict for the finding generator.
    """
    agg = section.get("aggregation", {})
    n = len(matrices)

    result = {
        "section_id": section["id"],
        "section_title": section["title"],
        "n_interviews": n,
        "finding_question": section["finding_question"],
        "distributions": {},
        "segment_breakdown": {},
        "numeric_avgs": {},
        "verbatims": [],
        "theme_frequency": {},
        "claim_reactions": {},
    }

    # ── Universal Layer 1 pain_points — only for pain/barrier/summary sections ───
    # Only include pain data when the section schema explicitly asks for it
    _pain_relevant_sections = {'pain_points_workarounds','pain_points','adoption','strategic_summary',
                                'category_need_buckets','aspiration_gaps','claim_credibility'}
    _include_pain = section.get("id","") in _pain_relevant_sections or \
                    any('pain' in f.lower() or 'barrier' in f.lower()
                        for f in section.get("schema_fields", []))

    from collections import Counter as _Counter
    _pp_area = _Counter(); _pp_sev = _Counter(); _pp_respondents_with_pain = 0
    for m in matrices:
        pps = m.get("pain_points") or []
        if pps: _pp_respondents_with_pain += 1
        for pp in pps:
            if not isinstance(pp, dict): continue
            area = pp.get("product_area") or pp.get("area", "other")
            sev  = pp.get("severity", "")
            if area: _pp_area[area] += 1
            if sev:  _pp_sev[sev] += 1
    total_pain_mentions = sum(_pp_area.values())
    if _include_pain and _pp_area:
        # Store ALL categories (not just top 10) so denominator is correct
        result["distributions"]["pain_point_areas_MENTION_COUNT"] = dict(_pp_area.most_common())
        result["distributions"]["pain_respondents_with_any_pain"] = {
            "respondents_with_pain": _pp_respondents_with_pain,
            "total_interviews": n,
            "total_pain_mentions": total_pain_mentions,  # = sum of ALL categories
            "note": f"pain_point_areas shows MENTION COUNTS — total {total_pain_mentions} mentions across {_pp_respondents_with_pain} of {n} interviews. Use total_pain_mentions as denominator."
        }
    if _include_pain and _pp_sev:
        result["distributions"]["pain_point_severity_MENTION_COUNT"] = dict(_pp_sev.most_common())

    # ── Always aggregate nps_signal and emotional_resolution ─────────────────────
    _nps = _Counter(); _emo = _Counter()
    for m in matrices:
        n_ = m.get("nps_signal", "")
        e_ = m.get("emotional_resolution", "")
        if n_: _nps[n_] += 1
        if e_: _emo[e_] += 1
    if _nps: result["distributions"]["nps_signal"] = dict(_nps.most_common())
    if _emo: result["distributions"]["emotional_resolution"] = dict(_emo.most_common())

    # ── Distributions ─────────────────────────────────────────────────────────
    for field in agg.get("distributions", []):
        dist = Counter()
        for m in matrices:
            val = _safe_get(m, field)
            if val and isinstance(val, str):
                dist[val] += 1
        if dist:
            result["distributions"][field.split(".")[-1]] = dict(dist.most_common())

    # ── Segment breakdown ──────────────────────────────────────────────────────
    # Use segment_key from report section if provided, else auto-detect
    _seg_key = agg.get("segment_key") or section.get("aggregation", {}).get("segment_key", "segment")
    def _get_seg(m):
        resp = m.get("respondent", {})
        return resp.get("segment") or resp.get("brand_owned") or resp.get(_seg_key) or ""
    if agg.get("segment_breakdown"):
        segs = list({_get_seg(m) for m in matrices if _get_seg(m)})
        for seg in segs:
            seg_ms = [m for m in matrices if _get_seg(m) == seg]
            seg_data = {"n": len(seg_ms)}
            # Distributions per segment
            for field in agg.get("distributions", []):
                dist = Counter()
                for m in seg_ms:
                    val = _safe_get(m, field)
                    if val and isinstance(val, str): dist[val] += 1
                if dist:
                    seg_data[field.split(".")[-1]] = dict(dist.most_common())
            # Numeric avgs per segment
            for field in agg.get("numeric_avg", []):
                vals = [_safe_get(m, field) for m in seg_ms if isinstance(_safe_get(m, field), (int, float))]
                if vals:
                    seg_data[f"avg_{field.split('.')[-1]}"] = round(sum(vals) / len(vals), 1)
            result["segment_breakdown"][seg] = seg_data

    # ── Numeric averages ───────────────────────────────────────────────────────
    for field in agg.get("numeric_avg", []):
        vals = [_safe_get(m, field) for m in matrices if isinstance(_safe_get(m, field), (int, float))]
        if vals:
            result["numeric_avgs"][field.split(".")[-1]] = round(sum(vals) / len(vals), 1)

    # ── Verified verbatims ─────────────────────────────────────────────────────
    for field_path in agg.get("verbatim_fields", []):
        for m in matrices:
            vbs = _get_verified_verbatims(m, field_path, max_n=3)
            result["verbatims"].extend(vbs)

    # Sort verbatims: verified first, then by quality score
    result["verbatims"].sort(key=lambda x: (-(x.get("verified") or 0), -(x.get("quality_score") or 0)))
    result["verbatims"] = result["verbatims"][:12]

    # ── Frequency fields (e.g. trust_builders_cited) ──────────────────────────
    for field in agg.get("frequency_fields", []):
        freq = Counter()
        for m in matrices:
            items = _safe_get(m, field) or []
            if isinstance(items, list):
                for item in items:
                    if item: freq[str(item)] += 1
        if freq:
            result["distributions"][field.split(".")[-1]] = dict(freq.most_common(10))

    # ── Claim reaction breakdown ───────────────────────────────────────────────
    if agg.get("claim_reaction_breakdown"):
        claim_data: dict = defaultdict(lambda: {"reactions": Counter(), "verbatims": []})
        for m in matrices:
            seg = m.get("respondent", {}).get("segment", "")
            city = m.get("respondent", {}).get("city", "")
            for cr in (m.get("key_claim_reactions") or []):
                if not isinstance(cr, dict): continue
                claim = cr.get("claim", "")
                reaction = cr.get("reaction", "")
                verbatim = cr.get("verbatim", "")
                if claim:
                    claim_data[claim]["reactions"][reaction] += 1
                    if verbatim and len(verbatim) > 8 and len(claim_data[claim]["verbatims"]) < 3:
                        # Check if verbatim is verified
                        field_results = (m.get("_verification", {}) or {}).get("field_results", {})
                        # Find matching field result
                        is_verified = None
                        for fpath, fcheck in field_results.items():
                            if "key_claim_reactions" in fpath and "verbatim" in fpath:
                                if verbatim[:25].lower() in str(fcheck):
                                    is_verified = fcheck.get("found")
                                    break
                        if is_verified is not False:  # include verified and unknown, skip confirmed bad
                            claim_data[claim]["verbatims"].append({
                                "text": verbatim, "reaction": reaction,
                                "segment": seg, "city": city,
                            })
        result["claim_reactions"] = {
            claim: {
                "reactions": dict(data["reactions"].most_common()),
                "total": sum(data["reactions"].values()),
                "dominant_reaction": data["reactions"].most_common(1)[0][0] if data["reactions"] else "none",
                "verbatims": data["verbatims"],
            }
            for claim, data in claim_data.items()
        }

    # ── Theme frequency ────────────────────────────────────────────────────────
    if agg.get("theme_frequency"):
        theme_freq = Counter()
        for m in matrices:
            for t in (m.get("narrative_tags") or []):
                if t: theme_freq[t] += 1
        result["theme_frequency"] = dict(theme_freq.most_common(20))

    return result


def _build_finding_prompt(context: dict, section: dict, all_context: dict, project_meta: dict = None) -> str:
    """Build the finding generation prompt for a single section."""
    project_meta = project_meta or {}
    project_name = project_meta.get("display_name", "qualitative research study")
    study_type = project_meta.get("study_type", "qualitative")
    description = project_meta.get("description", "")
    segment_key_label = project_meta.get("segment_key", "segment").replace("_", " ")
    seg_lines = ""
    for seg, sdata in context.get("segment_breakdown", {}).items():
        seg_lines += f"\n  {seg} (n={sdata['n']}): "
        for k, v in sdata.items():
            if k != "n":
                seg_lines += f"{k}={v}  "

    dist_lines = ""
    for field, dist in context.get("distributions", {}).items():
        if not isinstance(dist, dict):
            continue
        # Skip metadata dicts (values are not numeric)
        if any(isinstance(v, dict) for v in dist.values()):
            # Special case: pain respondent context — show as plain note
            if "note" in dist:
                dist_lines += f"\n  {field}: {dist.get('note','')} (total_mentions={dist.get('total_pain_mentions','?')}, respondents_with_pain={dist.get('respondents_with_pain','?')} of {dist.get('total_interviews','?')})"
            continue
        numeric_vals = {k: v for k, v in dist.items() if isinstance(v, (int, float))}
        if not numeric_vals:
            continue
        total = sum(numeric_vals.values())
        dist_lines += f"\n  {field}: " + "  ".join(f"{k}={v}/{total}" for k, v in list(numeric_vals.items())[:6])

    num_lines = ""
    for field, avg in context.get("numeric_avgs", {}).items():
        num_lines += f"\n  avg {field}: {avg}"

    verbatim_lines = ""
    for i, v in enumerate(context.get("verbatims", [])[:8]):
        vmark = "[VERIFIED]" if v.get("verified") else ("[UNVERIFIED]" if v.get("verified") is False else "[?]")
        verbatim_lines += f'\n  {i+1}. {vmark} "{v["text"][:180]}" — {v.get("segment","")}, {v.get("city","")}'

    claim_lines = ""
    for claim, cdata in context.get("claim_reactions", {}).items():
        total = cdata.get("total", 0)
        dom = cdata.get("dominant_reaction", "")
        reactions_str = "  ".join(f'{r}:{c}' for r, c in list(cdata["reactions"].items())[:4])
        claim_lines += f"\n  {claim}: {reactions_str} (n={total}, dominant={dom})"
        for vb in cdata.get("verbatims", [])[:2]:
            claim_lines += f'\n    → "{vb["text"][:120]}" — {vb.get("segment","")}, {vb.get("city","")}'

    theme_lines = ""
    for theme, cnt in list(context.get("theme_frequency", {}).items())[:10]:
        theme_lines += f"\n  {theme}: {cnt}/{context['n_interviews']}"

    prompt = f"""You are a Senior Qualitative Research Consultant writing findings for InfoLeap's client report.

PROJECT: {project_name}
STUDY TYPE: {study_type}
{f"DESCRIPTION: {description}" if description else ""}
SECTION: {context["section_title"]}
DG REFERENCE: {section["dg_section"]}
RESEARCH QUESTION: {context["finding_question"]}
INTERVIEWS ANALYSED: {context["n_interviews"]}
SEGMENT GROUPING: by {segment_key_label}

SELECTED VERBATIMS — COPY THESE INTO KEY VERBATIMS SECTION:
{verbatim_lines if verbatim_lines else "  [no verbatims available for this section]"}

AGGREGATED DATA:
Distributions (counts = unique respondents unless noted):{dist_lines if dist_lines else " [none]"}
Numeric averages (out of 10 scale):{num_lines if num_lines else " [none]"}
Segment breakdown:{seg_lines if seg_lines else " [none]"}
{f"Claim reactions:{claim_lines}" if claim_lines else ""}
{f"Theme frequency:{theme_lines}" if theme_lines else ""}

WRITE A RESEARCH FINDING IN THIS EXACT FORMAT:

FINDING:
[2-4 sentences. State what is true across the majority of respondents. Cite specific numbers from the data (e.g. "7 of {context['n_interviews']} respondents"). Do not hedge — state the finding directly. Do not repeat the research question. Ground every claim in the data above.]

SEGMENT DIFFERENCES:
[2-3 bullet points. How does this finding differ by {segment_key_label}? Only include if the SEGMENT BREAKDOWN data above shows real differences. If no meaningful difference — write "No material segment difference observed."]

KEY VERBATIMS:
[Copy 2-3 quotes from SELECTED VERBATIMS at the top of this prompt.
Format exactly: "quote text" — brand/segment, city
If a quote above is marked [VERIFIED], include that tag.
Write "DATA NOT AVAILABLE" ONLY if SELECTED VERBATIMS above shows "[no verbatims available]".
NEVER invent quotes.]

STRATEGIC IMPLICATION:
[1-2 sentences. What does this mean for the client? Specific and actionable. Based only on the data above.]

DATA NOT AVAILABLE IN TRANSCRIPTS / FIELD NOTES: [State what is missing. Otherwise: All key questions answered.]

RULES — non-negotiable:
- Every number must come from the distributions/segment_breakdown above — no invented statistics
- Fields ending in _MENTION_COUNT are total mentions (one respondent can have multiple) — say "X mentions" or "X of total pain mentions", NOT "X of N respondents"
- Use "respondents_with_pain" field for unique respondent count ("X of N respondents reported pain points")
- Every verbatim must appear word-for-word in the VERBATIM EVIDENCE section — never paraphrase or invent
- Every doc_id must come from the verbatim data above — never invent IDs
- If data is thin for a section, acknowledge it rather than padding with invented evidence
- Use exact numbers: say "X of {context['n_interviews']} respondents" not just "X respondents"
- Write for the client, not for pattern-filling"""

    return prompt


def generate_finding(
    client: OpenAI,
    matrices: list[dict],
    section: dict,
    all_sections: list[dict],
    force: bool = False,
    findings_dir: Path = None,
    project_meta: dict = None,
    cache_prefix: str = "",
) -> dict:
    """Generate and cache a finding for one section."""
    section_id = section["id"]
    out_path = findings_dir / f"{cache_prefix}{section_id}.json" if findings_dir else None

    if out_path and out_path.exists() and not force:
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if existing.get("finding_text"):
            return existing

    # Aggregate data for this section
    context = _aggregate_section(matrices, section)

    # Build prompt
    prompt = _build_finding_prompt(context, section, {}, project_meta=project_meta)

    # Call LLM
    messages = [
        {
            "role": "system",
            "content": (
                "You are a Senior Qualitative Research Consultant at an Indian market research firm. "
                "Write precise, evidence-grounded findings — no hedging, no generic language. "
                "Every claim must trace to data. Verbatims must be exact."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    print(f"  Generating: {cache_prefix}{section_id}...", end="", flush=True)
    finding_text = _call_llm(client, messages, max_tokens=_FINDING_MAX_TOKENS, temp=0.25)

    if not finding_text:
        print(" FAILED (LLM unavailable)")
        return {"section_id": section_id, "error": "llm_unavailable", "context": context}

    print(f" OK ({len(finding_text)} chars)")

    result = {
        "section_id": section_id,
        "dg_section": section.get("dg_section"),
        "title": section["title"],
        "finding_question": section["finding_question"],
        "n_interviews": context["n_interviews"],
        "finding_text": finding_text,
        "context": context,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "cache_prefix": cache_prefix.rstrip("_"),
        }
    }

    if out_path:
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    return result


def run_generation(project_id: str, section_id: str | None = None, force: bool = False, segment: str | None = None, city: str | None = None):
    project_dir = _DATA_DIR / "projects" / project_id
    matrices_dir = project_dir / "matrices"
    findings_dir = project_dir / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)

    # Load report structure
    rs_path = project_dir / "source_docs" / "report_structure.json"
    if not rs_path.exists():
        print(f"ERROR: report_structure.json not found at {rs_path}")
        sys.exit(1)

    report_structure = json.loads(rs_path.read_text(encoding="utf-8"))
    sections = report_structure.get("sections", [])

    # Load project metadata for prompt context
    project_meta = {}
    pj_path = project_dir / "project.json"
    if pj_path.exists():
        project_meta = json.loads(pj_path.read_text(encoding="utf-8"))

    # Load matrices (only those with quality_score >= 40 — skip critical failures)
    if not matrices_dir.exists():
        print("ERROR: matrices dir not found"); sys.exit(1)

    matrices = []
    skipped_low_quality = 0
    for fp in sorted(matrices_dir.glob("*_matrix.json")):
        try:
            m = json.loads(fp.read_text(encoding="utf-8"))
            qs = m.get("_quality_score")
            if qs is not None and qs < 40:
                skipped_low_quality += 1
                print(f"  SKIP (low quality {qs}%): {fp.name}")
                continue
            matrices.append(m)
        except Exception as e:
            print(f"  LOAD ERROR {fp.name}: {e}")

    if not matrices:
        print("ERROR: No matrices found"); sys.exit(1)

    # Apply filters
    if segment:
        matrices = [m for m in matrices if m.get("respondent", {}).get("segment") == segment or m.get("respondent", {}).get("brand_owned") == segment]
    if city:
        matrices = [m for m in matrices if m.get("respondent", {}).get("city") == city]

    if not matrices:
        print(f"ERROR: No matrices found for filters segment={segment}, city={city}")
        return

    # Determine cache prefix
    cache_prefix = ""
    if segment: cache_prefix += f"{segment}_"
    if city: cache_prefix += f"{city}_"

    print(f"\nProject: {project_id}")
    if cache_prefix:
        print(f"Filters: {cache_prefix.rstrip('_')}")
    print(f"Matrices: {len(matrices)} usable, {skipped_low_quality} skipped (low quality)")
    print(f"Sections: {len(sections)}")
    print()

    # Filter sections
    if section_id:
        sections = [s for s in sections if s["id"] == section_id]
        if not sections:
            print(f"ERROR: Section '{section_id}' not found"); sys.exit(1)

    client = _get_client()
    results = []

    for section in sections:
        result = generate_finding(client, matrices, section, sections, force=force, findings_dir=findings_dir, project_meta=project_meta, cache_prefix=cache_prefix)
        results.append(result)

    # Write index (different index if filtered)
    index_name = f"{cache_prefix}index.json" if cache_prefix else "index.json"
    index = {
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_interviews_used": len(matrices),
        "filters": {
            "segment": segment,
            "city": city
        },
        "sections": [
            {
                "id": r["section_id"],
                "title": r.get("title", ""),
                "dg_section": r.get("dg_section", ""),
                "status": "ok" if r.get("finding_text") else "error",
                "generated_at": r.get("generated_at"),
            }
            for r in results
        ],
    }
    (findings_dir / index_name).write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    ok = sum(1 for r in results if r.get("finding_text"))
    print(f"\nDone: {ok}/{len(results)} findings generated -> {findings_dir}")

    # Auto-upload findings + schema to Drive
    try:
        from infoleap.gdrive.client import DriveClient
        _dc = DriveClient()
        if _dc._svc is not None:
            _fid = _dc.upload_qual_findings(project_id, str(findings_dir))
            if _fid:
                print(f"Drive: findings.zip uploaded ({_fid})")
            _sfid = _dc.upload_qual_schema(project_id, str(project_dir / "schema"))
            if _sfid:
                print(f"Drive: schema.zip uploaded ({_sfid})")
    except Exception as _e:
        print(f"Drive upload skipped: {_e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--section", default=None)
    parser.add_argument("--segment", default=None)
    parser.add_argument("--city", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    run_generation(args.project, section_id=args.section, force=args.force, segment=args.segment, city=args.city)


if __name__ == "__main__":
    main()
