"""
LENS Local Tree Navigator
=========================
Zero-API qualitative retrieval engine.

How it works:
  1. Parse the user query into intent signals (keywords, topics, sentiment target)
  2. Walk each PageIndex tree JSON locally
  3. Score every section by theme overlap + keyword match
  4. Collect the top-N passages WITHOUT any API call
  5. Only the final compact evidence block is sent to the synthesis LLM

This replaces the previous approach of calling Groq/Gemini once per document
(5 docs = 5 API calls) with a single, local, transparent scoring pass.
"""

import json
import re
import math
from pathlib import Path
def rprint(*args, **kwargs):
    import re
    text = " ".join(str(a) for a in args)
    text = re.sub(r"\[/?[a-zA-Z_ ]*\]", "", text)
    print(text)
from ingestion.pageindex_builder import load_registry


#  Intent signal extraction 
# Synonym map: normalise user words to index theme tokens
SYNONYM_MAP = {
    # Product attributes
    "noisy": "noise", "loud": "noise", "quiet": "noise", "silent": "noise",
    "slow": "speed", "fast": "speed", "rpm": "speed", "powerful": "power",
    "weak": "power", "motor": "motor", "jar": "jar capacity", "blades": "blade",
    "durability": "long life", "durable": "long life", "build": "quality",
    "break": "repair", "broke": "repair", "repair": "repair", "service": "service",
    "warranty": "warranty", "price": "price", "expensive": "price",
    "cheap": "price", "affordable": "price", "value": "value for money",
    "grind": "grinding", "grinding": "grinding", "mix": "mixing",
    "design": "design", "look": "design", "colour": "design", "color": "design",
    "trust": "brand trust", "brand": "brand trust", "recommend": "recommendation",
    # Brands
    "bajaj": "bajaj", "prestige": "prestige", "preethi": "preethi",
    "butterfly": "butterfly", "maharaja": "maharaja", "sujata": "sujata",
    "usha": "usha", "inalsa": "inalsa", "philips": "philips",
    # Sentiment
    "happy": "positive", "satisfied": "positive", "good": "positive",
    "love": "positive", "unhappy": "negative", "dissatisfied": "negative",
    "bad": "negative", "hate": "negative", "problem": "negative",
    "issue": "negative", "complaint": "negative",
    # Research topics
    "awareness": "awareness", "consider": "consideration",
    "purchase": "purchase", "buy": "purchase", "bought": "purchase",
    "funnel": "brand funnel", "penetration": "ownership",
    "segment": "demographic", "zone": "zone", "city": "city",
    "north": "north zone", "south": "south zone",
    "east": "east zone", "west": "west zone",
}

# Topic  which tree sections/themes to prioritise
TOPIC_AFFINITY = {
    "noise":           ["noise complaint", "silent operation", "motor noise"],
    "grinding":        ["grinding performance", "jar capacity", "blade"],
    "price":           ["price", "value for money", "affordability"],
    "design":          ["design", "aesthetics", "build quality"],
    "service":         ["after sales", "service", "warranty", "repair"],
    "purchase":        ["purchase decision", "buying reason", "recommendation"],
    "brand trust":     ["brand trust", "loyalty", "recommendation"],
    "positive":        ["satisfaction", "positive experience"],
    "negative":        ["complaint", "negative experience", "pain point"],
}


def extract_query_signals(query: str) -> dict:
    """
    Locally parse the query into structured intent signals.
    Returns weights for scoring without any API call.
    """
    q_lower = query.lower()
    tokens = re.findall(r"\b\w+\b", q_lower)

    # Normalize via synonym map
    normalized = []
    for t in tokens:
        normalized.append(SYNONYM_MAP.get(t, t))

    # Build TF weights (term frequency in normalized tokens)
    tf: dict[str, float] = {}
    for t in normalized:
        tf[t] = tf.get(t, 0) + 1

    # Expand to affinity themes
    expanded_themes: list[str] = []
    for token, related_themes in TOPIC_AFFINITY.items():
        if token in tf:
            expanded_themes.extend(related_themes)

    # Detect explicit brand mentions
    brands = [t for t in normalized if t in [
        "bajaj", "prestige", "preethi", "butterfly", "maharaja",
        "sujata", "usha", "inalsa", "philips", "kenstar", "morphy"
    ]]

    # Detect sentiment target
    sentiment_focus = None
    if any(t in tf for t in ["negative", "complaint", "issue", "problem"]):
        sentiment_focus = "negative"
    elif any(t in tf for t in ["positive", "happy", "satisfied", "love"]):
        sentiment_focus = "positive"

    return {
        "raw_tokens": tokens,
        "normalized_tokens": normalized,
        "tf_weights": tf,
        "expanded_themes": expanded_themes,
        "brand_mentions": brands,
        "sentiment_focus": sentiment_focus,
    }


#  Scoring engine 

def _cosine_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """Simple cosine similarity between two token lists (no external deps)."""
    if not query_tokens or not doc_tokens:
        return 0.0

    q_set = set(query_tokens)
    d_set = set(doc_tokens)
    intersection = q_set & d_set

    if not intersection:
        return 0.0

    # Weighted by IDF approximation: rare tokens score higher
    score = 0.0
    for token in intersection:
        # Treat token frequency in query as weight
        q_freq = query_tokens.count(token)
        d_freq = doc_tokens.count(token)
        # Simplified TF-IDF: log(1 + tf) scaling
        score += math.log1p(q_freq) * math.log1p(d_freq)

    # Normalize
    q_mag = math.sqrt(sum(math.log1p(query_tokens.count(t)) ** 2 for t in q_set))
    d_mag = math.sqrt(sum(math.log1p(doc_tokens.count(t)) ** 2 for t in d_set))

    if q_mag == 0 or d_mag == 0:
        return 0.0
    return score / (q_mag * d_mag)


def score_section(section: dict, signals: dict) -> float:
    """
    Score a single PageIndex tree section against query signals.
    Returns a relevance float 0.0  1.0.
    """
    score = 0.0
    themes = [t.lower() for t in section.get("themes", [])]
    title = section.get("title", "").lower()
    passages = section.get("passages", [])

    # Passage content tokens
    passage_text = " ".join(p.get("content", "") for p in passages).lower()
    passage_tokens = re.findall(r"\b\w+\b", passage_text)

    # 1. Theme overlap with query TF weights (high weight)
    for theme in themes:
        theme_tokens = re.findall(r"\b\w+\b", theme)
        for t in theme_tokens:
            if t in signals["tf_weights"]:
                score += 0.4 * signals["tf_weights"][t]
            if t in signals.get("expanded_themes", []):
                score += 0.3

    # 2. Title overlap
    title_tokens = re.findall(r"\b\w+\b", title)
    for t in title_tokens:
        if t in signals["tf_weights"]:
            score += 0.2 * signals["tf_weights"][t]

    # 3. Cosine similarity of passage text to query
    cosine = _cosine_score(signals["normalized_tokens"], passage_tokens)
    score += 1.2 * cosine

    # 4. Brand boost: if the user mentions a brand and the section discusses it
    for brand in signals.get("brand_mentions", []):
        if brand in passage_text or brand in title:
            score += 0.5

    # 5. Sentiment alignment
    if signals.get("sentiment_focus"):
        for p in passages:
            if p.get("sentiment", "").lower() == signals["sentiment_focus"]:
                score += 0.2

    return round(min(score, 1.0), 4)


#  Master traversal function 

def local_search_tree(tree: dict, signals: dict, doc_id: str,
                      threshold: float = 0.1) -> list[dict]:
    """
    Walk one PageIndex tree and collect relevant passages WITHOUT API calls.
    """
    collected = []
    sections = tree.get("sections", [])

    for section in sections:
        sec_score = score_section(section, signals)
        if sec_score < threshold:
            continue

        passages = section.get("passages", [])
        for p in passages:
            # If sentiment filter is active, respect it
            if signals.get("sentiment_focus"):
                if p.get("sentiment", "neutral") != signals["sentiment_focus"]:
                    continue

            collected.append({
                "doc_id": doc_id,
                "section_title": section.get("title", "Unknown Section"),
                "content": p.get("content", ""),
                "sentiment": p.get("sentiment", "neutral"),
                "topic": p.get("topic", ""),
                "relevance_score": sec_score,
                "themes": section.get("themes", []),
            })

    return collected


def retrieve_local(query: str, relevant_doc_ids: list[str],
                   top_k: int = 8, threshold: float = 0.08) -> dict:
    """
    Master retrieval function. Zero API calls.
    
    Returns:
        {
          "passages": [...],         # Top-k relevant passages
          "signals": {...},          # The parsed intent (for transparency)
          "docs_searched": [...],    # Which documents were searched
          "docs_skipped": [...],     # Which docs were not found
        }
    """
    signals = extract_query_signals(query)
    rprint(f"[cyan]Local Navigator:[/cyan] Signals -> tokens={signals['normalized_tokens'][:5]}, "
           f"brands={signals['brand_mentions']}, sentiment={signals['sentiment_focus']}")

    registry = load_registry()
    doc_map = {d["doc_id"]: d for d in registry.get("documents", [])}

    all_passages = []
    docs_searched = []
    docs_skipped = []

    for doc_id in relevant_doc_ids:
        if doc_id not in doc_map:
            docs_skipped.append({"doc_id": doc_id, "reason": "not in registry"})
            continue

        doc_entry = doc_map[doc_id]
        tree_path = doc_entry.get("tree_path")

        if not tree_path or not Path(tree_path).exists():
            docs_skipped.append({"doc_id": doc_id, "reason": "tree file missing"})
            continue

        with open(tree_path, encoding="utf-8") as f:
            tree = json.load(f)

        passages = local_search_tree(tree, signals, doc_id, threshold=threshold)
        all_passages.extend(passages)
        docs_searched.append({
            "doc_id": doc_id,
            "category": doc_entry.get("category", "unknown"),
            "passages_found": len(passages),
        })

    # Sort globally by relevance and return top-k
    all_passages.sort(key=lambda x: x["relevance_score"], reverse=True)
    top_passages = all_passages[:top_k]

    rprint(f"[cyan]Local Navigator:[/cyan] Searched {len(docs_searched)} docs -> "
           f"{len(all_passages)} candidates -> top {len(top_passages)} selected")

    return {
        "passages": top_passages,
        "signals": signals,
        "docs_searched": docs_searched,
        "docs_skipped": docs_skipped,
        "total_candidates": len(all_passages),
    }

