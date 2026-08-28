"""
Qualitative Retrieval Engine for OxData.
Optimized for OpenRouter Reliability.
"""

import os
import json
import sqlite3
import re
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from openai import OpenAI
from dotenv import load_dotenv
import streamlit as st

# Attempt to import semantic search but handle failure gracefully
try:
    from infoleap.skills.semantic_retriever import semantic_search
except:
    semantic_search = None

OR_MODEL = "meta-llama/llama-4-scout:free"

_FREE_MODELS_QR = [
    "meta-llama/llama-4-scout:free",
    "google/gemma-3-12b-it:free",
    "mistralai/mistral-7b-instruct:free",
    "qwen/qwen-2-7b-instruct:free",
]

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

def _get_key() -> str:
    """Lazy key loader â€” tries env first, then re-reads .env with override."""
    key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        try:
            load_dotenv(str(_ENV_FILE), override=True)
            key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        except Exception:
            pass
    return key or ""

_client = None

def get_client():
    global _client
    key = _get_key()
    if _client is None or not key:
        _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
    return _client

BASE_DIR = Path(__file__).parent.parent
TREES_DIR = BASE_DIR / "data" / "pageindex_trees"

INDEX_DB = BASE_DIR / "data" / "qual_index.db"

async def search_qual_trees(query: str, filters: Dict[str, str] = None, progress_callback=None) -> List[Dict[str, Any]]:
    filters = filters or {}
    brand = filters.get("brand")
    city = filters.get("city")
    category = filters.get("category")
    theme = filters.get("theme")
    
    q_low = query.lower()
    hybrid_results = []
    
    # --- 1. INDEXED LOOKUP (Instant) ---
    doc_ids = []
    if INDEX_DB.exists():
        conn = sqlite3.connect(INDEX_DB)
        cur = conn.cursor()
        
        sql = "SELECT DISTINCT doc_id FROM qual_index WHERE 1=1"
        params = []
        if brand:
            sql += " AND UPPER(brand) = UPPER(?)"
            params.append(brand)
        if city:
            sql += " AND UPPER(city) = UPPER(?)"
            params.append(city)
            
        cur.execute(sql, params)
        doc_ids = [r[0] for r in cur.fetchall()]
        conn.close()
        
    if not doc_ids: return []

    # --- 2. FAST CONTENT SCORING (Only for Indexed Docs) ---
    for doc_id in doc_ids:
        await asyncio.sleep(0)
        f_path = TREES_DIR / f"{doc_id}_tree.json"
        if not f_path.exists(): continue
        
        try:
            with open(f_path, encoding='utf-8') as f:
                tree = json.load(f)
            
            # Category Filter (Robust)
            if category and category != "All Categories":
                tree_cat = tree.get("category", "").lower()
                user_cat = category.lower()
                # Basic fuzzy/substring match
                if user_cat not in tree_cat and tree_cat not in user_cat:
                    # Special check for common mappings
                    if "mixer" in user_cat and "mixer" not in tree_cat: continue
                    if "fan" in user_cat and "fan" not in tree_cat: continue
                    if user_cat != "all" and tree_cat != "unknown":
                        continue

            for s in tree.get("sections", []):
                # Theme Filter (at section level)
                if theme and theme != "All Themes":
                    if not any(theme.lower() in t.lower() for t in s.get("themes", [])):
                        continue

                for p in s.get("passages", []):
                    if p.get("content", "").lstrip().startswith("---"):
                        continue
                    content_low = p["content"].lower()

                    content = p.get("content", "")
                    if not content or len(content) < 30:
                        continue

                    # Base score: passage from an indexed (brand/city filtered) doc = always relevant
                    # Additional boosts for keyword/phrase matches
                    score = 5  # base â€” doc was already pre-filtered at index level
                    if q_low and q_low in content_low: score += 50
                    if brand and brand.lower() in content_low: score += 15
                    if city and city.lower() in content_low: score += 10

                    # Keyword match boost
                    if q_low:
                        keywords = re.findall(r'\w+', q_low)
                        match_count = sum(1 for kw in keywords if kw in content_low and len(kw) > 3)
                        score += (match_count * 5)

                    hybrid_results.append({
                        "doc_id": doc_id,
                        "content": content,
                        "score": score,
                        "brand": brand or tree.get("brand", "Unknown"),
                        "city": city or tree.get("city", "Unknown"),
                        "category": tree.get("category", "Unknown")
                        })
        except: continue

    # Deduplicate and Sort
    hybrid_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return hybrid_results[:50]

def synthesize_qual_insights(query: str, passages: List[Dict]) -> Dict:
    if not passages:
        return {"summary": "No qualitative context found.", "themes": []}
    if not _get_key():
        return {"summary": "OpenRouter API key not found â€” check oxdata/.env", "themes": []}

    # Build a rich evidence block â€” include brand, city, and content for each passage
    evidence_lines = []
    for i, p in enumerate(passages[:6], 1):
        brand = p.get("brand", "Unknown")
        city  = p.get("city",  "Unknown")
        text  = p.get("content", "")[:400].strip()
        evidence_lines.append(f'[{i}] {brand} | {city}\n    "{text}"')
    evidence_block = "\n\n".join(evidence_lines)

    system = (
        "You are a qualitative research analyst interpreting in-depth consumer interviews (IDIs) "
        "from an Indian electrical appliances study (FMCD category: fans, mixer-grinders, water heaters, room coolers). "
        "Respondents are middle-class Indian household decision-makers across 18 cities. "
        "Your job is to surface what consumers ACTUALLY THINK â€” not to summarise neutrally, "
        "but to identify the underlying belief, emotion, or concern that drives behaviour. "
        "Be specific: name brands, name cities, quote actual words from the evidence. "
        "Never write generic statements that could apply to any category."
    )

    prompt = f"""RESEARCH QUESTION: {query}

INTERVIEW EVIDENCE ({len(passages)} passages retrieved, top 6 shown):
{evidence_block}

Write a structured qualitative synthesis in EXACTLY this format:

CORE FINDING: [One bold, decisive sentence â€” what is the dominant consumer belief or behaviour pattern revealed by these interviews? Cite at least one brand or city name.]

EVIDENCE THEMES: [2â€“3 sentences identifying the 2â€“3 recurring themes across passages. For each theme, quote or paraphrase a specific passage. Format: 'Theme 1: consumers in [city] associate [brand] with [attribute], as seen in passage [N]...']

DIVERGENT SIGNAL: [1 sentence on any CONTRADICTORY or SURPRISING finding in the evidence â€” a quote that cuts against the dominant pattern. If none, state the strongest point of consensus instead.]

STRATEGIC IMPLICATION: [1â€“2 sentences on what this qualitative evidence implies for brand strategy. Be actionable â€” what should the brand team do differently based on what consumers are actually saying in interviews?]

Rules: Bold (**) key terms. Reference specific passage numbers [N] when citing evidence. No hedging."""

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": prompt},
    ]
    raw = None
    for model in _FREE_MODELS_QR:
        try:
            client = get_client()
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.25,
                max_tokens=600,
            )
            raw = response.choices[0].message.content.strip()
            if raw:
                break
        except Exception:
            continue

    if not raw:
        return {"summary": "Synthesis unavailable â€” all free models failed.", "themes": []}

    themes = []
    for line in raw.split("\n"):
        for marker in ("EVIDENCE THEMES:", "DIVERGENT SIGNAL:", "STRATEGIC IMPLICATION:"):
            if line.startswith(marker):
                themes.append(line[len(marker):].strip())
    return {"summary": raw, "themes": themes}
