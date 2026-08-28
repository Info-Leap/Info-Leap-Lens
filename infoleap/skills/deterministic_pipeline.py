"""
Deterministic Pipeline — LENS 4.0
===================================
Handles structured survey queries with exactly 2 LLM calls:

  Call 1 (MODEL_MINI):  Intent + parameter extraction
  Python:               SQL via capability REGISTRY + ExampleStore few-shot
  Python:               Execute SQL → DataFrame
  Python:               chart_renderer selects chart type (called in chat.py)
  Call 2 (MODEL_MINI):  Narrate result in 2-3 sentences

Fast path: brand health chart requests bypass SQL entirely and call
chart_tools.get_brand_chart() directly.

Returns FinalReport (same structure as researcher_agent) — chat.py renders
without changes.

Public API:
    is_qualitative(question) → bool
    run_deterministic_async(question, ...) → AsyncGenerator[dict, None]
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from typing import AsyncGenerator, Optional

import pandas as pd
from openai import AsyncOpenAI

# ── Project imports ──────────────────────────────────────────────────────────
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

OR_KEY      = os.getenv("OPENROUTER_API_KEY", "")
OR_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_MINI  = os.getenv("OPENROUTER_MODEL_MINI", "openai/gpt-4o-mini")
MODEL_ALT   = os.getenv("OPENROUTER_MODEL_ALT",  "openai/gpt-4.1-mini")

from infoleap.db_loader import get_db_path
from infoleap.skills.example_store import example_store
from infoleap.skills.schema_registry import get_registry
from infoleap.skills.capabilities import REGISTRY as SKILL_REGISTRY
from infoleap.researcher_agent import FinalReport

# ── Qualitative signal words (route to pydantic_ai agent instead) ────────────
_QUAL_WORDS = frozenset([
    "why", "prefer", "reason", "reasons", "feedback", "sentiment", "opinion",
    "opinions", "think", "feel", "say", "saying", "interview", "interviews",
    "verbatim", "verbatims", "consumer voice", "better than", "worse than",
    "choose", "choosing", "dislike", "like", "love", "hate", "complaint",
    "complaints", "suggest", "suggestion", "experience", "perception",
])

# ── Brand health chart fast-path triggers ────────────────────────────────────
_BRAND_HEALTH_TRIGGERS: dict[str, list[str]] = {
    "awareness_funnel":    [
        "awareness funnel", "funnel", "tom spont aided", "conversion awareness",
        "awareness conversion", "aided to tom", "tom to aided", "funnel chart",
        "awareness stages", "funnel breakdown",
    ],
    "awareness_landscape": [
        "all brands awareness", "awareness landscape", "market awareness",
        "awareness comparison all", "awareness ranking", "awareness for all",
    ],
    "nps_rankings":        [
        "nps rankings", "nps league", "best nps", "nps comparison all brands",
        "nps all brands", "nps leaderboard", "top nps brands", "nps ranking",
    ],
    "zone_comparison":     [
        "awareness by zone", "zone awareness", "regional awareness",
        "awareness zone breakdown", "zone wise awareness", "zone-wise awareness",
    ],
    "zone_nps":            [
        "nps by zone", "zone nps", "regional nps", "nps zone breakdown",
        "zone wise nps", "nps across zones",
    ],
    "city_nps":            [
        "nps by city", "city nps", "city level nps", "city wise nps",
        "nps across cities", "nps for cities",
    ],
    "radar":               [
        "brand radar", "health radar", "brand profile", "radar chart",
        "brand signature", "brand health radar",
    ],
    "positioning":         [
        "strategic map", "positioning map", "brand positioning",
        "strategic positioning", "market map", "brand map",
    ],
}

# ── Known brand names for extraction ─────────────────────────────────────────
_KNOWN_BRANDS = [
    "bajaj", "crompton", "havells", "orient", "polycab", "usha", "philips",
    "syska", "luminous", "panasonic", "voltas", "blue star", "khaitan",
    "preethi", "prestige", "butterfly", "inalsa", "morphy richards",
    "sujata", "wonderchef", "kenstar", "symphony", "maharaja", "pigeon",
    "ao smith", "racold", "hindware", "v guard", "surya", "halonix",
]

_KNOWN_ZONES = ["north", "south", "east", "west", "central"]

_KNOWN_CITIES = [
    "mumbai", "delhi", "bangalore", "chennai", "kolkata", "hyderabad",
    "pune", "ahmedabad", "lucknow", "jaipur", "gorakhpur", "surat",
    "nagpur", "indore", "bhopal", "patna", "kanpur",
]


# ─── Helper: single LLM call ─────────────────────────────────────────────────

async def _llm(prompt: str, system: str = "", model: str = MODEL_MINI) -> str:
    try:
        async with AsyncOpenAI(base_url=OR_BASE_URL, api_key=OR_KEY) as client:
            res = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": prompt},
                    ],
                    temperature=0.0,
                    max_tokens=400,
                ),
                timeout=30.0,
            )
        return res.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {e}"


# ─── Public routing helper ────────────────────────────────────────────────────

def is_qualitative(question: str) -> bool:
    """Return True if question should route to pydantic_ai agent (qual/open-ended)."""
    q = question.lower()
    return any(w in q for w in _QUAL_WORDS)


# ─── Brand health fast-path detection ────────────────────────────────────────

def _detect_brand_health(question: str) -> tuple[Optional[str], Optional[str]]:
    """
    Return (chart_type, brand_name) if question matches a brand health chart trigger.
    brand_name may be None for landscape/ranking charts that show all brands.
    """
    q = question.lower()
    for chart_type, triggers in _BRAND_HEALTH_TRIGGERS.items():
        if any(t in q for t in triggers):
            brand = _extract_brand(q)
            return chart_type, brand
    return None, None


def _extract_brand(q: str) -> Optional[str]:
    for b in _KNOWN_BRANDS:
        if b in q:
            return b.title() if " " not in b else b.title()
    return None


def _extract_zone(q: str) -> str:
    for z in _KNOWN_ZONES:
        if z in q:
            return z.capitalize()
    return "all"


def _extract_city(q: str) -> str:
    for c in _KNOWN_CITIES:
        if c in q:
            return c.capitalize()
    return "all"


# ─── Intent extraction (LLM Call 1) ──────────────────────────────────────────

_INTENT_SYSTEM = """\
You are a query classifier for a consumer survey database about electrical appliances in India.

Extract structured intent from the user's question. Return ONLY valid JSON, no prose.

JSON schema:
{
  "metric": "awareness" | "nps" | "ownership" | "purchase" | "room" | "demographic" | "general",
  "brand": "brand name or null",
  "zone": "North" | "South" | "East" | "West" | "Central" | null,
  "city": "city name or null",
  "gender": "male" | "female" | null,
  "chart_type": "funnel" | "bar" | "grouped_bar" | "pie" | "nps_waterfall" | "metric_cards" | "auto",
  "is_qualitative": false
}

Metric rules:
- "awareness"   = TOM, spontaneous, aided, recall, recognition, awareness funnel, conversion
- "nps"         = NPS, net promoter, promoter, detractor, passive, recommend, loyalty, satisfaction, rating
- "ownership"   = kitchen appliances owned (mixer, juicer, kettle, microwave, induction, etc.)
- "room"        = room appliances (fan, AC, LED, geyser, heater, tube light, air cooler, etc.)
- "purchase"    = recently bought / purchased / market share of purchases
- "demographic" = respondent counts, sample size, gender split, age distribution
- "general"     = anything else that requires custom SQL (brand list, category overlap, cross-tab, etc.)

Chart type rules:
- funnel: awareness funnel / conversion stages
- nps_waterfall: NPS breakdown with promoter/passive/detractor
- grouped_bar: comparing multiple brands or multiple metrics side-by-side
- pie: share / distribution / split
- metric_cards: single-number answer
- bar: everything else ranked

Always return valid JSON. No markdown fences.
"""

async def _extract_intent(question: str) -> dict:
    """LLM Call 1: extract metric, brand, zone, city, gender, chart_type."""
    raw = await _llm(question, system=_INTENT_SYSTEM)
    # Strip markdown fences if model adds them
    raw = re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        intent = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: keyword-based extraction
        intent = _keyword_intent(question)
    # Ensure required fields exist
    intent.setdefault("metric", "awareness")
    intent.setdefault("brand", None)
    intent.setdefault("zone", None)
    intent.setdefault("city", None)
    intent.setdefault("gender", None)
    intent.setdefault("chart_type", "auto")
    intent.setdefault("is_qualitative", False)
    return intent


def _keyword_intent(question: str) -> dict:
    """Pure-Python fallback intent extraction (no LLM)."""
    q = question.lower()
    metric = "general"  # default to general so few-shot path handles it
    for m, kws in {
        "nps":         ["nps", "promoter", "detractor", "recommend", "loyalty", "satisfaction"],
        "ownership":   ["own", "ownership", "penetration", "kitchen", "mixer", "juicer", "microwave"],
        "room":        ["fan", " ac ", "geyser", "led", "room appliance", "heater", "tube light"],
        "purchase":    ["purchase", "purchased", "bought", "buy", "market share"],
        "demographic": ["respondent", "sample", "how many people", "gender", "age band"],
        "awareness":   ["awareness", "tom", "recall", "funnel", "aided", "spontaneous", "conversion"],
    }.items():
        if any(kw in q for kw in kws):
            metric = m
            break
    return {
        "metric": metric,
        "brand": _extract_brand(q),
        "zone": next((z.capitalize() for z in _KNOWN_ZONES if z in q), None),
        "city": next((c.capitalize() for c in _KNOWN_CITIES if c in q), None),
        "gender": "female" if "female" in q else ("male" if "male" in q else None),
        "chart_type": "auto",
        "is_qualitative": False,
    }


# ─── SQL execution ────────────────────────────────────────────────────────────

def _build_instruction(question: str, intent: dict) -> str:
    """Build a deterministic instruction string for capability get_sql()."""
    parts = [question]
    if intent.get("brand"):
        parts.append(f"brand:{intent['brand']}")
    if intent.get("zone"):
        parts.append(f"zone:{intent['zone'].lower()}")
    if intent.get("city"):
        parts.append(f"city:{intent['city'].lower()}")
    if intent.get("gender"):
        parts.append(f"gender:{intent['gender']}")
    return " ".join(parts)


def _run_sql(sql: str, db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return pd.read_sql(sql, conn)
    finally:
        conn.close()


# ─── Narration (LLM Call 2) ──────────────────────────────────────────────────

_NARRATE_SYSTEM = """\
You are a senior market research analyst. Given a question and data table, write a rich analytical response:

1. **Headline** (1-2 sentences): Direct answer with exact numbers from the data.
2. **Context** (1-2 sentences): How does this compare to the overall dataset, or what pattern is notable?
3. **Breakdown** (1-2 sentences): Note the most significant segment, outlier, or trend in the data.
4. **Pulse Insight** (1 sentence, MANDATORY): Start with "Pulse Insight: " — give a strategic implication.

Rules:
- Cite EXACT numbers. No approximations unless data is unavailable.
- Be decisive — you are the authority on this data. No "it appears", "seems", "suggests".
- 4-6 sentences total minimum. Match depth to the complexity of the question.
"""

async def _narrate(question: str, df: pd.DataFrame) -> str:
    data_preview = df.head(8).to_string(index=False)
    prompt = f"Question: {question}\n\nData:\n{data_preview}\n\nAnswer:"
    result = await _llm(prompt, system=_NARRATE_SYSTEM, model=MODEL_ALT)
    if result.startswith("Error:"):
        # Fallback: generate summary from data without LLM
        cols = list(df.columns)
        rows = len(df)
        return f"Data retrieved: {rows} rows across {len(cols)} metrics ({', '.join(cols[:4])}). Pulse Insight: Review the evidence visualization for detailed breakdown."
    return result


# ─── Main pipeline ────────────────────────────────────────────────────────────

async def run_deterministic_async(
    question: str,
    db_path: Optional[str] = None,
    message_history: Optional[list] = None,  # accepted but unused; kept for API symmetry
) -> AsyncGenerator[dict, None]:
    """
    Main entry point. Yields same event dicts as run_autonomous_research_async:
      {"type": "thought", "content": str}
      {"type": "result", "data": FinalReport, "df": DataFrame, "sql": str, "engine": str}
      {"type": "error", "content": str}
    """
    if db_path is None:
        db_path = str(get_db_path())

    t0 = time.time()

    # ── Step 0: Brand health fast path check ─────────────────────────────────
    chart_type_bh, brand_bh = _detect_brand_health(question)
    if chart_type_bh:
        yield {"type": "thought", "content": f"Brand health fast path: {chart_type_bh} for {brand_bh or 'all brands'}"}
        try:
            from infoleap.skills.chart_tools import get_brand_chart
            q = question.lower()
            zone_bh = _extract_zone(q)
            fig, explanation = get_brand_chart(
                chart_type=chart_type_bh,
                brand=brand_bh,
                zone=zone_bh,
            )
            report = FinalReport(
                summary=explanation,
                thinking=f"Brand health fast path: {chart_type_bh}",
                respondent_profile="Survey: 6,631 respondents, 18 cities",
                chart_code=None,
                chart_spec=None,
                raw_sql_output=None,
                diagnostic_trace=f"Route: brand_health_fast_path | chart: {chart_type_bh} | brand: {brand_bh} | zone: {zone_bh}",
            )
            # Attach fig to report for chat.py to render directly
            report._brand_health_fig = fig
            yield {
                "type": "result", "data": report, "df": None, "sql": None,
                "brand_health_fig": fig,
                "engine": "LENS 4.0 Brand Health",
            }
            return
        except Exception as e:
            yield {"type": "thought", "content": f"Brand health fast path failed ({e}), falling back to SQL path"}

    # ── Step 1: Intent extraction (LLM Call 1) ────────────────────────────────
    yield {"type": "thought", "content": "Analysing question..."}
    intent = await _extract_intent(question)
    metric = intent.get("metric", "awareness")
    brand  = intent.get("brand")
    zone   = intent.get("zone")
    city   = intent.get("city")
    yield {"type": "thought", "content": f"Intent: metric={metric} | brand={brand} | zone={zone} | city={city}"}

    # ── Step 2: SQL via capability template ───────────────────────────────────
    instruction = _build_instruction(question, intent)
    capability  = SKILL_REGISTRY.get(metric)
    sql = None

    if capability and hasattr(capability, "get_sql"):
        try:
            sql = capability.get_sql(instruction)
            yield {"type": "thought", "content": f"Using {metric} capability template"}
        except Exception as e:
            yield {"type": "thought", "content": f"Template failed ({e}), using few-shot SQL"}

    if not sql:
        # Few-shot fallback: retrieve similar examples and ask LLM to adapt
        examples = example_store.retrieve(question, k=3)
        few_shot  = example_store.format_for_prompt(examples)
        registry  = get_registry(db_path)
        schema    = registry.get_context()
        sql_prompt = (
            f"{schema}\n\n{few_shot}\n\n"
            f"Write SQLite SQL to answer: {question}\n"
            f"Filters: brand={brand}, zone={zone}, city={city}, gender={intent.get('gender')}\n"
            "Return ONLY the SQL query, no explanation."
        )
        sql_raw = await _llm(sql_prompt, system="SQL Engineer. SQLite dialect only. Return ONLY SQL.", model=MODEL_ALT)
        sql = re.sub(r"```(?:sql)?|```", "", sql_raw).strip()
        yield {"type": "thought", "content": "Few-shot SQL generated"}
    else:
        # Augment template SQL with few-shot context in logs (not prompt; template is deterministic)
        examples = example_store.retrieve(question, k=2)
        yield {"type": "thought", "content": f"Retrieved {len(examples)} similar SQL examples for validation context"}

    # ── Step 3: Execute SQL ───────────────────────────────────────────────────
    try:
        df = await asyncio.to_thread(_run_sql, sql, db_path)
        yield {"type": "thought", "content": f"SQL returned {len(df)} rows"}
    except Exception as e:
        # SQL failed — try once with schema-grounded fix via LLM
        yield {"type": "thought", "content": f"SQL error: {e}. Attempting fix..."}
        registry = get_registry(db_path)
        fix_prompt = (
            f"Original SQL:\n{sql}\n\nError: {e}\n\n"
            f"Schema:\n{registry.get_context()}\n\n"
            "Return ONLY the fixed SQLite SQL."
        )
        fixed_raw = await _llm(fix_prompt, system="SQLite SQL fixer. Return ONLY SQL.", model=MODEL_ALT)
        sql = re.sub(r"```(?:sql)?|```", "", fixed_raw).strip()
        try:
            df = await asyncio.to_thread(_run_sql, sql, db_path)
            yield {"type": "thought", "content": f"Fixed SQL returned {len(df)} rows"}
        except Exception as e2:
            yield {"type": "error", "content": f"SQL failed after fix attempt: {e2}"}
            return

    if df.empty:
        yield {"type": "error", "content": "Query returned no data for the specified filters. Try broader filters."}
        return

    # ── Step 4: Narrate result (LLM Call 2) ───────────────────────────────────
    yield {"type": "thought", "content": "Generating insight..."}
    summary = await _narrate(question, df)

    # ── Step 5: Assemble FinalReport ──────────────────────────────────────────
    chart_hint = intent.get("chart_type", "auto")
    chart_spec = {"type": chart_hint} if chart_hint != "auto" else None

    report = FinalReport(
        summary=summary,
        thinking=f"Deterministic path: {metric} capability | {len(df)} rows",
        respondent_profile="Survey: 6,631 respondents, 18 cities",
        raw_sql_output=sql,
        chart_spec=chart_spec,
        diagnostic_trace=(
            f"Route: deterministic | metric: {metric} | brand: {brand} | "
            f"zone: {zone} | city: {city} | rows: {len(df)} | "
            f"latency: {int((time.time()-t0)*1000)}ms"
        ),
    )

    yield {
        "type": "result",
        "data": report,
        "df": df,
        "sql": sql,
        "engine": "LENS 4.0 Deterministic",
    }
