"""
Theme Clustering Engine — cross-interview theme discovery over qualitative verbatim passages.

Why this exists: a single respondent's `all_passages[]` is per-interview text with no link to
what other respondents said. Answering "what did people actually say about X, across all 23
interviews" today means either a full LLM re-read of every transcript (costly, repeated per
question) or manually skimming — this module groups semantically similar passages across the
whole project once, locally, so cross-interview themes become a queryable structure instead of
raw text.

Deliberately NOT a vector database / RAG-for-retrieval setup — at typical project scale
(a few hundred passages) that adds infrastructure with no benefit; full-context reasoning beats
retrieval below a few hundred passages (see arXiv:2511.08505, "Structured RAG for Answering
Aggregative Questions"). This is the other, distinct use of embeddings the research supports:
BERT-style embeddings + density clustering (HDBSCAN) is the validated technique specifically for
*cross-document theme discovery*, not lookup (arXiv:2403.04819, "Automating the Information
Extraction from Semi-Structured Interview Transcripts"; arXiv:2509.25244, "Neo-Grounded Theory").

Cost discipline: embeddings + clustering run 100% locally (sentence-transformers on CPU, no API
calls). The only optional LLM usage is ONE batched call to name all clusters at once — never one
call per cluster, never one call per passage.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np

_MODEL_NAME = "all-mpnet-base-v2"
_model = None

# Corpus-size-aware default: literature default of min_cluster_size=10 assumes 100s-1000s of
# docs; a typical qual project here has only ~200-500 total passages (23 respondents x ~15-22
# passages each), so a smaller min_cluster_size is needed or almost everything becomes noise.
_DEFAULT_MIN_CLUSTER_SIZE = 3

_STOPWORDS = {
    "the","a","an","and","or","but","is","are","was","were","be","been","being","to","of","in",
    "on","for","with","as","by","at","this","that","it","its","i","you","he","she","they","we",
    "not","no","do","does","did","have","has","had","will","would","could","should","can","if",
    "so","just","like","would","my","me","their","them","them","about","from","than","then",
    "also","would","really","think","know","get","got","going","gonna","actually","maybe",
}


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def extract_passages(matrices: list[dict], dedup_threshold: float = 0.97) -> list[dict]:
    """Flatten all_passages across every matrix into one list, tagged with respondent metadata.

    Also deduplicates near-identical passages via embedding cosine similarity — found live:
    DI_11's matrix had the exact same passage entered twice (an extraction artifact, not two
    distinct observations). Exact-string dedup would miss near-duplicates with minor rephrasing;
    embedding similarity catches both.
    """
    out: list[dict] = []
    for m in matrices:
        resp = m.get("respondent") or {}
        doc_id = m.get("doc_id") or m.get("filename") or "unknown"
        for i, p in enumerate(m.get("all_passages") or []):
            if not isinstance(p, dict):
                continue
            content = (p.get("content") or "").strip()
            if len(content) < 20:
                continue
            out.append({
                "content": content,
                "sentiment": p.get("sentiment") or "neutral",
                "topic": p.get("topic") or "",
                "pain_point": bool(p.get("pain_point")),
                "decision_signal": bool(p.get("decision_signal")),
                "doc_id": doc_id,
                "segment": resp.get("segment") or "",
                "city": resp.get("city") or "",
                "_passage_idx": i,
            })

    if len(out) < 2:
        return out

    model = _get_model()
    emb = model.encode([p["content"] for p in out], normalize_embeddings=True, show_progress_bar=False)

    keep: list[int] = []
    dropped: list[int] = []
    for idx in range(len(out)):
        is_dup = False
        for kidx in keep:
            sim = float(np.dot(emb[idx], emb[kidx]))
            if sim >= dedup_threshold:
                is_dup = True
                break
        if is_dup:
            dropped.append(idx)
        else:
            keep.append(idx)

    if dropped:
        for idx in dropped:
            out[idx]["_dedup_dropped"] = True
        print(f"  theme_clustering_engine: dropped {len(dropped)} near-duplicate passage(s) "
              f"(>= {dedup_threshold:.0%} similarity) — likely extraction artifacts, not "
              f"distinct observations")

    return [p for i, p in enumerate(out) if i not in dropped]


def _embed(passages: list[dict]) -> np.ndarray:
    model = _get_model()
    return np.asarray(model.encode(
        [p["content"] for p in passages], normalize_embeddings=True, show_progress_bar=False))


def _cluster(embeddings: np.ndarray, min_cluster_size: int) -> np.ndarray:
    from sklearn.cluster import HDBSCAN
    # metric="cosine" needs precomputed or algorithm support; embeddings are already
    # L2-normalized so euclidean distance on them is monotonic with cosine distance — avoids a
    # separate distance-matrix computation while giving the same cluster structure.
    clusterer = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=1, metric="euclidean",
                         copy=True)
    return clusterer.fit_predict(embeddings)


def _deterministic_label(texts: list[str], n_words: int = 3) -> str:
    """No-LLM-cost cluster label: most frequent non-stopword terms across member passages."""
    counts: Counter = Counter()
    for t in texts:
        for w in re.findall(r"[a-zA-Z']+", t.lower()):
            if len(w) > 2 and w not in _STOPWORDS:
                counts[w] += 1
    top = [w for w, _ in counts.most_common(n_words)]
    return " / ".join(top) if top else "misc"


def summarize_clusters(passages: list[dict], labels: np.ndarray) -> dict:
    """Group passages by cluster label, compute coverage/sentiment stats per theme.

    Returns {"themes": [...], "unclustered": [...]} — HDBSCAN noise points (label -1) are
    reported separately, never silently dropped: a passage that didn't join a cluster is still
    real respondent evidence, just not a repeated pattern (matches this pipeline's
    never-fabricate/never-hide discipline elsewhere).
    """
    by_label: dict[int, list[dict]] = {}
    for p, lbl in zip(passages, labels):
        by_label.setdefault(int(lbl), []).append(p)

    themes = []
    for lbl, members in by_label.items():
        if lbl == -1:
            continue
        doc_ids = sorted({m["doc_id"] for m in members})
        sentiment_counts = Counter(m["sentiment"] for m in members)
        pain_count = sum(1 for m in members if m["pain_point"])
        themes.append({
            "cluster_id": lbl,
            "label": _deterministic_label([m["content"] for m in members]),
            "size": len(members),
            "respondent_coverage": len(doc_ids),
            "doc_ids": doc_ids,
            "sentiment_mix": dict(sentiment_counts),
            "pain_point_share": round(pain_count / len(members), 2) if members else 0.0,
            "sample_quotes": [
                {"content": m["content"], "doc_id": m["doc_id"], "segment": m["segment"]}
                for m in members[:5]
            ],
        })
    # This project's own extraction prompt is explicit: "at least two independent respondents
    # ... before coding it as a segment-level finding" / a single-voice cluster is a
    # "single-respondent signal," not a theme. A cluster can pass min_cluster_size on passage
    # count alone while all passages come from one talkative respondent — apply the same
    # evidentiary bar here that the rest of this pipeline already holds itself to.
    single_respondent_signals = [t for t in themes if t["respondent_coverage"] < 2]
    themes = [t for t in themes if t["respondent_coverage"] >= 2]
    themes.sort(key=lambda t: t["respondent_coverage"], reverse=True)

    unclustered = by_label.get(-1, [])

    return {
        "themes": themes,
        "single_respondent_signals": single_respondent_signals,
        "unclustered_count": len(unclustered),
        "unclustered_sample": [
            {"content": m["content"], "doc_id": m["doc_id"]} for m in unclustered[:10]
        ],
        "total_passages": len(passages),
    }


def label_clusters_with_llm(result: dict, call_fn) -> dict:
    """Optional: replace deterministic word-frequency labels with human-readable ones.

    ONE batched LLM call for every cluster at once — never per-cluster, never per-passage. This
    is the cost discipline established elsewhere in this pipeline (_enrich_missing_narratives
    batches similarly). Falls back silently to the deterministic label on any failure — labeling
    quality never blocks the underlying data.

    call_fn: a callable taking a single prompt string and returning the raw text response —
    matches this codebase's existing `call_or`/`call_openrouter_fn` shape used throughout the
    renderer, so no adapter is needed at call sites.
    """
    themes = result.get("themes", [])
    if not themes:
        return result

    listing = "\n".join(
        f"{t['cluster_id']}. keywords: {t['label']} | sample quotes: "
        + " || ".join(q["content"][:120] for q in t["sample_quotes"][:3])
        for t in themes
    )
    prompt = (
        "Each numbered group below is a cluster of similar respondent quotes from qualitative "
        "interviews. Give each cluster a short (3-6 word) human-readable theme label — describe "
        "what respondents are actually saying, not generic categories. Return ONLY a JSON object "
        "mapping cluster_id (as string) to label, nothing else.\n\n" + listing
    )
    try:
        raw = call_fn(prompt)
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        mapping = json.loads(raw)
        for t in themes:
            new_label = mapping.get(str(t["cluster_id"]))
            if new_label and isinstance(new_label, str):
                t["label"] = new_label.strip()
    except Exception as e:
        print(f"  theme_clustering_engine: LLM labeling failed ({e}) — keeping deterministic labels")
    return result


def compute_theme_clusters(
    matrices: list[dict],
    min_cluster_size: int = _DEFAULT_MIN_CLUSTER_SIZE,
    cache_path: Optional[Path] = None,
    force: bool = False,
) -> dict:
    """Full pipeline: extract -> dedup -> embed -> cluster -> summarize. Cached to disk keyed by
    a hash of the input passages, since embedding is the expensive step and matrices don't change
    between dashboard renders."""
    passages = extract_passages(matrices)
    if len(passages) < min_cluster_size:
        return {"themes": [], "unclustered_count": len(passages),
                "unclustered_sample": [], "total_passages": len(passages),
                "note": "too few passages to cluster"}

    content_hash = hashlib.sha256(
        "||".join(p["content"] for p in passages).encode("utf-8")).hexdigest()[:16]

    if cache_path and not force and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("_content_hash") == content_hash:
                return cached
        except Exception:
            pass

    embeddings = _embed(passages)
    labels = _cluster(embeddings, min_cluster_size=min_cluster_size)
    result = summarize_clusters(passages, labels)
    result["_content_hash"] = content_hash
    result["_min_cluster_size"] = min_cluster_size

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    return result
