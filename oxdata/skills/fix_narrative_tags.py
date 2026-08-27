"""
Second-pass narrative_tags extractor.
Reads _step1_text from existing matrices → extracts narrative_tags via cheap single LLM call.
Also fixes: passages count (if < 8), investor_archetype free-text (normalise via _ENUM_MAPS).

Usage:
    python fix_narrative_tags.py --project karat-coindcx
    python fix_narrative_tags.py --project karat-coindcx --file DI_3_matrix.json
"""
import argparse, json, os, re, sys, time
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(str(_ENV_FILE), override=True)

from openai import OpenAI

_FREE_MODELS = [
    "openai/gpt-oss-120b:free",
    "moonshotai/kimi-k2.6:free",
    "google/gemma-4-31b-it:free",
]
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_REFERENCE_THEMES = [
    "gold_as_emotional_insurance", "gold_shifting_from_passive_to_active",
    "digital_gold_convenience", "portfolio_context", "investment_motivation_life_stage",
    "job_insecurity_and_diversification", "gold_format_understanding", "sgb_as_benchmark",
    "yield_mental_model_clarity", "returns_led_appeal_vs_credibility",
    "safety_led_reassurance_vs_vague_proof", "safety_plus_returns_preference",
    "coindcx_crypto_trust_gap", "trust_builders_as_adoption_conditions",
    "tax_advantage_comprehension", "t1_liquidity_language", "small_ticket_trial_bridge",
    "ui_calculator_education", "route_preference_by_segment_city",
    "tagline_protection_plus_growth",
]

_ARCHETYPE_MAP = {
    "conservative": "safety_seeker", "safety": "safety_seeker", "cautious": "safety_seeker",
    "wealth": "wealth_builder", "builder": "wealth_builder", "active": "wealth_builder",
    "opportunity": "opportunity_hunter", "hunter": "opportunity_hunter", "savvy": "opportunity_hunter",
    "calculative": "opportunity_hunter", "researcher": "opportunity_hunter", "analytical": "opportunity_hunter",
    "family": "family_protector", "protector": "family_protector",
    "disciplined": "disciplined_accumulator", "accumulator": "disciplined_accumulator", "systematic": "disciplined_accumulator",
}
_VALID_ARCHETYPES = {"wealth_builder", "safety_seeker", "opportunity_hunter", "family_protector", "disciplined_accumulator"}


def _get_client() -> OpenAI:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        print("ERROR: OPENROUTER_API_KEY missing"); sys.exit(1)
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


def _call(client, messages, max_tokens=600, temp=0.1, retry=3):
    for attempt in range(retry):
        for model in _FREE_MODELS:
            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, temperature=temp
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                err = str(e)
                if "429" in err or "503" in err:
                    wait = 4 * (2 ** attempt)
                    print(f"    Rate limit ({model}) — wait {wait}s")
                    time.sleep(wait)
                else:
                    continue
    return ""


def _norm_archetype(val: str) -> str:
    if val in _VALID_ARCHETYPES:
        return val
    v = val.lower()
    for kw, mapped in _ARCHETYPE_MAP.items():
        if kw in v:
            return mapped
    return "opportunity_hunter"  # default


def _extract_tags(client, step1_text: str, doc_id: str) -> list[str]:
    prompt = f"""You are analysing a Step 1 qualitative research analysis for {doc_id} — a CoinDCX digital gold concept testing interview.

The analysis below already rates each of 20 reference themes as STRONG EVIDENCE / MIXED EVIDENCE / LIMITED EVIDENCE / NOT IN TRANSCRIPT.
It may also mention emerging themes not in the reference list.

Reference themes:
{', '.join(_REFERENCE_THEMES)}

Your task:
1. List all themes with STRONG or MIXED evidence from the reference list.
2. Add any additional emerging themes you identify (use snake_case).
3. Return ONLY a JSON array of strings — no explanation, no markdown.

Example output: ["gold_as_emotional_insurance", "portfolio_context", "coindcx_crypto_trust_gap", "family_financial_advisor_influence"]

STEP 1 ANALYSIS:
{step1_text[:6000]}
"""
    result = _call(client, [
        {"role": "system", "content": "Return ONLY a valid JSON array of strings. No markdown, no explanation."},
        {"role": "user", "content": prompt},
    ], max_tokens=400)

    if not result:
        return []
    # Parse JSON array
    cleaned = re.sub(r"```(?:json)?", "", result).strip().rstrip("`")
    m = re.search(r"\[.*?\]", cleaned, re.DOTALL)
    if m:
        try:
            tags = json.loads(m.group())
            # clean to snake_case
            out = []
            for t in tags:
                t2 = re.sub(r"[^a-z0-9_]", "_", str(t).lower().strip()).strip("_")
                if t2 and len(t2) > 2:
                    out.append(t2)
            return out
        except Exception:
            pass
    return []


def _extract_passages(client, step1_text: str, matrix: dict, doc_id: str) -> list[dict]:
    """Extract verbatim passages from transcript using step1 as guide."""
    # Read the actual .md transcript for verbatim passages
    project_id = "karat-coindcx"
    proc_dir = _DATA_DIR / "projects" / project_id / "transcripts" / "processed"
    md_path = proc_dir / f"{doc_id}.md"
    if not md_path.exists():
        # try alternate name
        for f in proc_dir.glob(f"{doc_id}*.md"):
            md_path = f; break
    if not md_path.exists():
        return []

    body = md_path.read_text(encoding="utf-8")
    # strip frontmatter
    body = re.sub(r"^---[\s\S]*?---\n", "", body).strip()
    # Use first 8000 chars (free models context)
    body_excerpt = body[:8000]

    prompt = f"""From this interview transcript excerpt, extract 12-15 verbatim passages (each 30-80 words) that best represent:
- Portfolio and gold investment behaviour
- Reactions to the concept (spontaneous and evaluated)
- Specific claim reactions (yield, liquidity, trust)
- Barriers and concerns
- Adoption signals

Return ONLY a JSON array of objects with keys: content (verbatim text), sentiment (positive/negative/neutral/ambivalent), topic (portfolio_behaviour/gold_behaviour/concept_reaction/trust/adoption/claims/other), pain_point (bool), decision_signal (bool).

TRANSCRIPT EXCERPT:
{body_excerpt}
"""
    result = _call(client, [
        {"role": "system", "content": "Return ONLY valid JSON array. No markdown."},
        {"role": "user", "content": prompt},
    ], max_tokens=2000)

    if not result:
        return []
    cleaned = re.sub(r"```(?:json)?", "", result).strip().rstrip("`")
    m = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return []


def fix_matrix(client, matrix_path: Path, fix_passages: bool = True) -> str:
    try:
        m = json.loads(matrix_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"LOAD_ERROR: {e}"

    doc_id = m.get("doc_id", matrix_path.stem.replace("_matrix", ""))
    step1 = m.get("_step1_text", "")
    changed = False

    # Fix narrative_tags
    if not m.get("narrative_tags") and step1:
        tags = _extract_tags(client, step1, doc_id)
        if tags:
            m["narrative_tags"] = tags
            changed = True
            print(f"    tags: {tags[:5]}{'…' if len(tags)>5 else ''}")

    # Fix investor_archetype if free-text
    arch = m.get("investor_archetype", "")
    if arch and arch not in _VALID_ARCHETYPES:
        m["investor_archetype"] = _norm_archetype(arch)
        changed = True

    # Fix passages if too few
    if fix_passages and len(m.get("all_passages") or []) < 8 and step1:
        passages = _extract_passages(client, step1, m, doc_id)
        if passages and len(passages) >= 4:
            m["all_passages"] = passages
            changed = True
            print(f"    passages: extracted {len(passages)}")

    if changed:
        m["_fixed_at"] = datetime.now(timezone.utc).isoformat()
        matrix_path.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
        return "FIXED"
    return "NO_CHANGE"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--file", default=None, help="Single matrix filename")
    parser.add_argument("--no-passages", action="store_true", help="Skip passage extraction")
    args = parser.parse_args()

    matrices_dir = _DATA_DIR / "projects" / args.project / "matrices"
    if not matrices_dir.exists():
        print(f"ERROR: {matrices_dir} not found"); sys.exit(1)

    if args.file:
        files = [matrices_dir / args.file]
    else:
        files = sorted(matrices_dir.glob("*_matrix.json"))

    client = _get_client()
    ok = err = no_change = 0

    for i, fp in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {fp.name}...", end=" ", flush=True)
        status = fix_matrix(client, fp, fix_passages=not args.no_passages)
        print(status)
        if status == "FIXED":
            ok += 1
        elif status == "NO_CHANGE":
            no_change += 1
        else:
            err += 1

    print(f"\nDone: {ok} fixed, {no_change} unchanged, {err} errors")


if __name__ == "__main__":
    main()
