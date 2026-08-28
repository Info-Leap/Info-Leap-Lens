"""
Verbatim Grouping Engine.

Collects verbatim quotes already extracted into a project's matrices, proposes sentiment x
theme groups grounded ONLY in those real quotes (same "cite what you were given, never invent"
discipline schema_generator.py's discovery pass already uses for respondent_types/emergent_topics
— see discovery_prompt in schema_generator.py), and persists user-editable group definitions plus
manual per-quote assignment overrides to schema/verbatim_groups.json.

Groups are proposed dynamically per project (not a fixed universal taxonomy) because studies vary
too much in domain for one fixed list to fit all of them — the LLM names groups after what the
quotes actually say. The user can rename, delete, merge, or manually reassign any quote afterward;
nothing here is locked.
"""
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_MODEL = "deepseek/deepseek-chat"  # matches the primary extraction model (see llm_client.py)


def _get_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        try:
            load_dotenv(str(_ENV_FILE), override=True)
            key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        except Exception:
            pass
    return key or ""


def _call_llm(prompt: str, system: str, max_tokens: int = 4000) -> str:
    key = _get_key()
    if not key:
        return ""
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("```")
        t = parts[1] if len(parts) > 1 else t
        if t.startswith("json"):
            t = t[4:]
    try:
        return json.loads(t)
    except Exception:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except Exception:
                return None
    return None


def collect_verbatims(matrices_dir: Path, verbatim_fields: set[str], cap: int = 300) -> list[dict]:
    """Pull every string value under a declared verbatim field (top-level or nested inside an
    object field's sub_fields), plus the always-verbatim all_passages/pain_points/
    most_powerful_verbatim fields every schema carries. Deduplicated, capped for LLM context."""
    out: list[dict] = []
    seen: set[tuple] = set()
    if not matrices_dir or not matrices_dir.exists():
        return out
    always_verbatim = {"all_passages", "pain_points", "most_powerful_verbatim"}

    for mf in sorted(matrices_dir.glob("*_matrix.json")):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        doc_id = m.get("doc_id") or mf.stem.replace("_matrix", "")

        def walk(node, field_hint: str):
            if isinstance(node, str):
                q = node.strip()
                if len(q) >= 20 and not q.startswith("NEW:"):
                    key = (doc_id, field_hint, q)
                    if key not in seen:
                        seen.add(key)
                        out.append({"doc_id": doc_id, "field": field_hint, "quote": q})
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v, field_hint)
            elif isinstance(node, list):
                for item in node:
                    walk(item, field_hint)

        for fname, fval in m.items():
            if fname.startswith("_") or fname in ("doc_id", "filename", "respondent"):
                continue
            if fname.lower() in verbatim_fields or fname.lower() in always_verbatim:
                walk(fval, fname)
        if len(out) >= cap:
            break
    return out[:cap]


def propose_groups(quotes: list[dict], project_name: str, study_type: str) -> Optional[list[dict]]:
    """One grounded LLM call: propose 4-10 sentiment x theme groups from real quotes only.
    Every group cites quotes by index into `quotes` — the response is never trusted to contain
    quote text of its own, so nothing here can hallucinate a quote."""
    if not quotes:
        return None
    quote_block = "\n".join(
        f'[{i}] doc_id={q["doc_id"]} field={q["field"]}: "{q["quote"][:300]}"'
        for i, q in enumerate(quotes)
    )
    prompt = f"""You are grouping real respondent verbatims from a qualitative study into a small
number of groups a researcher can scan quickly — a mix of SENTIMENT (positive/negative/mixed/
neutral) and THEME (what the quote is actually about: behaviour, barrier, need, trust, price,
whatever genuinely fits). Do not force a generic template; name groups after what these specific
quotes actually say, at whatever axes fit THIS study's content.

PROJECT: {project_name} (study type: {study_type})

QUOTES (numbered — cite by number only, never invent or alter a quote):
{quote_block}

Return 4-10 groups. Every group's quote_numbers must reference only numbers from the list above.
A quote may appear in more than one group if it genuinely fits both (e.g. negative + price).

Return ONLY valid JSON, no markdown fences:
{{"groups": [
  {{"group_id": "snake_case_id", "name": "Human-readable name",
    "sentiment": "positive|negative|mixed|neutral", "theme": "short theme label",
    "definition": "1 sentence describing what belongs here", "quote_numbers": [0, 3, 7]}}
]}}"""
    raw = _call_llm(
        prompt,
        "You are a qualitative research analyst grouping real respondent quotes. Reuse only the "
        "quotes you were given, cited by number — never invent or alter one. Return only valid JSON.",
        max_tokens=4000,
    )
    data = _extract_json(raw)
    if not data or not isinstance(data.get("groups"), list):
        return None

    groups = []
    for g in data["groups"]:
        qnums = [n for n in (g.get("quote_numbers") or []) if isinstance(n, int) and 0 <= n < len(quotes)]
        if not qnums:
            continue
        groups.append({
            "group_id": g.get("group_id") or str(g.get("name", "group")).lower().replace(" ", "_"),
            "name": g.get("name", "Untitled group"),
            "sentiment": g.get("sentiment", "neutral"),
            "theme": g.get("theme", ""),
            "definition": g.get("definition", ""),
            "members": [
                {"doc_id": quotes[n]["doc_id"], "field": quotes[n]["field"], "quote": quotes[n]["quote"]}
                for n in qnums
            ],
        })
    return groups or None


def quote_key(doc_id: str, field: str, quote: str) -> str:
    return hashlib.sha1(f"{doc_id}|{field}|{quote}".encode("utf-8")).hexdigest()[:12]


def _groups_path(project_dir: Path) -> Path:
    return project_dir / "schema" / "verbatim_groups.json"


def load_groups(project_dir: Path) -> dict:
    p = _groups_path(project_dir)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"groups": [], "assignments": {}, "generated_at": None}


def save_groups(project_dir: Path, data: dict) -> None:
    p = _groups_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
