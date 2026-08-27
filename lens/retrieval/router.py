"""
Query Router — uses OpenRouter deepseek/deepseek-v4-pro to decide:
1. Which documents to search (by reading the registry + scoring against their themes)
2. Whether SQL is needed
3. What the intent category is

Document selection is now SEMANTIC — we score every document's indexed themes
against the user query locally, zero extra API calls.
"""

import os
import re
import json
import math
from openai import OpenAI
from ingestion.pageindex_builder import load_registry
from pathlib import Path

def rprint(*args, **kwargs):
    """Fallback print that strips rich markup tags."""
    import re
    text = " ".join(str(a) for a in args)
    text = re.sub(r"\[/?[a-zA-Z_ ]*\]", "", text)
    print(text)

OR_KEY = os.getenv("OPENROUTER_API_KEY")
OR_MODEL = "deepseek/deepseek-v4-pro"
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OR_KEY)

ROUTER_SYSTEM_PROMPT = """You are a query router for LENS, a consumer intelligence system with 230+ in-depth interview transcripts and a large survey database.

For EVERY market research question, consumer voice (qualitative) adds value — set needs_qual=true by default.
Only set needs_qual=false for pure count/number queries like "how many respondents total?".

Rules:
- needs_qual: TRUE for almost all questions (brand comparison, features, satisfaction, city differences, experiences, opinions, pain points, recommendations).
- needs_quant: true if the question asks about metrics, scores, rankings, percentages, counts.
- sql_intent: brief description of what to compute (empty string if needs_quant=false).
- category_filter: the product category (e.g. "Mixer Grinder") or "all".
- confidence_threshold: your routing confidence 0.0 to 1.0.

Return valid JSON only. No markdown. No explanation.
"""

BRAND_KEYWORDS = [
    "bajaj", "philips", "sujata", "preethi", "usha", "panasonic",
    "havells", "prestige", "butterfly", "bosch", "kenstar", "crompton",
    "maharaja", "inalsa", "morphy", "kent", "orient"
]

PURE_COUNT_TERMS = [
    "sample size", "how many respondents", "total count",
    "number of surveys", "how many people surveyed"
]


# ── Local document scoring (no API) ───────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def _score_doc_against_query(query_tokens: list[str], doc_entry: dict,
                              tree_path: str) -> float:
    """
    Score a document's relevance to the query by comparing query tokens against:
      1. The document's indexed themes (from the tree JSON)
      2. The doc_id / filename (brand / category hints)
    Returns a relevance score (higher = more relevant).
    Zero API calls.
    """
    score = 0.0
    q_set = set(query_tokens)

    # 1. Score against doc filename / doc_id
    doc_id_tokens = set(_tokenize(doc_entry.get("doc_id", "")))
    doc_id_overlap = q_set & doc_id_tokens
    score += len(doc_id_overlap) * 0.6

    # 2. Score against pre-indexed themes in the tree JSON
    if tree_path and Path(tree_path).exists():
        try:
            with open(tree_path, encoding="utf-8") as f:
                tree = json.load(f)
            all_themes: list[str] = tree.get("all_themes", [])
            theme_tokens = set(_tokenize(" ".join(all_themes)))
            theme_overlap = q_set & theme_tokens
            # IDF proxy: rarer terms get higher weight
            for tok in theme_overlap:
                score += math.log1p(query_tokens.count(tok)) * 0.5
        except Exception:
            pass

    return round(score, 4)


def _select_relevant_docs(query: str, candidate_docs: list[dict],
                           max_docs: int = 6) -> list[str]:
    """
    Score every candidate document locally and return the top-max_docs by relevance.
    Falls back to a wider sample only if nothing scores above zero.
    """
    query_tokens = _tokenize(query)

    scored = []
    for doc in candidate_docs:
        tree_path = doc.get("tree_path", "")
        s = _score_doc_against_query(query_tokens, doc, tree_path)
        scored.append((doc["doc_id"], s))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Keep docs with a positive score, fall back to top-N if nothing matches
    positive = [(d, s) for d, s in scored if s > 0]
    selected = positive[:max_docs] if positive else scored[:max_docs]

    rprint(
        f"[dim]DocScorer: top docs = "
        f"{[(d, round(s, 2)) for d, s in selected[:3]]}[/dim]"
    )
    return [d for d, _ in selected]


# ── Main router ───────────────────────────────────────────────────────────────

def route_query(query: str, chat_history: list[dict] = None) -> dict:
    """
    Routes a query to the appropriate retrieval sources.
    Document selection is now semantic (local scoring), not random.
    """
    registry = load_registry()
    categories = list(set([d.get("category", "Unknown") for d in registry.get("documents", [])]))

    context_summary = ""
    if chat_history and len(chat_history) > 0:
        recent = chat_history[-4:]
        context_summary = "Recent conversation context:\n"
        for turn in recent:
            content = turn["content"]
            if isinstance(content, dict):
                text_content = content.get("answer", "") or str(content)
            else:
                text_content = str(content)
            context_summary += f"{turn['role'].upper()}: {text_content[:200]}\n"

    user_message = f"""{context_summary}

Available categories:
{json.dumps(categories, indent=2)}

User question: {query}

Return routing plan as JSON.
"""

    try:
        response = client.chat.completions.create(
            model=OR_MODEL,
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            temperature=0.0,
            max_tokens=256,
            response_format={"type": "json_object"}
        )
        raw = response.choices[0].message.content
        plan = json.loads(raw)
    except Exception as e:
        rprint(f"[red]Router (OpenRouter) failed: {e}. Using default plan.[/red]")
        plan = {
            "needs_qual": True,
            "needs_quant": True,
            "sql_intent": "general analytics",
            "category_filter": "Mixer Grinder",
            "confidence_threshold": 0.5
        }

    # Force qualitative ON for all research questions
    query_lower = query.lower()
    if not any(t in query_lower for t in PURE_COUNT_TERMS):
        plan["needs_qual"] = True

    # ── Semantic document selection (replaces random sampling) ──────────────
    cat_filter = plan.get("category_filter", "all")
    all_docs = registry.get("documents", [])

    if cat_filter.lower() != "all":
        candidate_docs = [d for d in all_docs
                          if d.get("category", "").lower() == cat_filter.lower()]
    else:
        candidate_docs = all_docs

    if plan.get("needs_qual") and candidate_docs:
        selected = _select_relevant_docs(query, candidate_docs, max_docs=6)
        plan["relevant_doc_ids"] = selected
    else:
        plan["relevant_doc_ids"] = []

    mentioned_brands = [b for b in BRAND_KEYWORDS if b in query_lower]
    rprint(
        f"[dim]Router: needs_qual={plan.get('needs_qual')} "
        f"needs_quant={plan.get('needs_quant')} "
        f"docs={len(plan.get('relevant_doc_ids', []))} "
        f"brands={mentioned_brands} "
        f"cat={plan.get('category_filter')}[/dim]"
    )
    return plan
