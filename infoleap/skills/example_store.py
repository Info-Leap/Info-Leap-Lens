"""
ExampleStore — Few-shot SQL retrieval via TF-IDF + cosine similarity.

Stores (question, sql, metadata) pairs. Given a new question, retrieves
the top-k most similar past examples to use as few-shot context for SQL
generation. No external API required — pure Python.

Usage:
    store = ExampleStore()
    examples = store.retrieve("crompton awareness in north zone", k=3)
    store.add("what is bajaj nps in west?", "SELECT ... FROM v_brand_nps ...", {"metric": "nps"})
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Optional

_DEFAULT_EXAMPLES_PATH = Path(__file__).parent / "examples" / "infoleap_examples.json"
_ADDITIONS_PATH = Path(__file__).parent / "examples" / "user_examples.json"


# ─── TF-IDF ENGINE ────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _tfidf_vectors(corpus: list[list[str]]) -> tuple[dict[str, float], list[dict[str, float]]]:
    """
    Compute IDF over corpus and per-doc TF-IDF vectors.
    Returns (idf_map, doc_vectors).
    """
    n = len(corpus)
    if n == 0:
        return {}, []

    # IDF: log((N + 1) / (df + 1)) + 1  (smoothed)
    df: Counter = Counter()
    for tokens in corpus:
        df.update(set(tokens))
    idf = {term: math.log((n + 1) / (count + 1)) + 1 for term, count in df.items()}

    # Per-doc TF-IDF
    doc_vecs = []
    for tokens in corpus:
        tf = Counter(tokens)
        total = max(len(tokens), 1)
        vec = {t: (count / total) * idf.get(t, 1.0) for t, count in tf.items()}
        doc_vecs.append(vec)

    return idf, doc_vecs


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse TF-IDF vectors."""
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ─── EXAMPLE STORE ────────────────────────────────────────────────────────────

class ExampleStore:
    """
    Few-shot SQL example retrieval.

    Backed by JSON files (seeded + user-added). Builds TF-IDF index lazily on
    first retrieve() call. Index invalidated whenever add() is called.
    """

    def __init__(
        self,
        seeded_path: Path = _DEFAULT_EXAMPLES_PATH,
        additions_path: Path = _ADDITIONS_PATH,
    ):
        self._seeded_path = seeded_path
        self._additions_path = additions_path
        self._examples: list[dict] = []
        self._idf: dict[str, float] = {}
        self._doc_vecs: list[dict[str, float]] = []
        self._dirty = True  # force index rebuild on first use
        self._loaded = False

    # ── Public API ──────────────────────────────────────────────────────────

    def retrieve(self, question: str, k: int = 3) -> list[dict]:
        """
        Return top-k most similar examples for `question`.

        Each result is a dict with keys: question, sql, metadata, score.
        """
        self._ensure_loaded()
        self._ensure_index()

        if not self._examples:
            return []

        q_tokens = _tokenize(question)
        q_vec = {t: (1.0) * self._idf.get(t, 0.0) for t in q_tokens}

        scored = []
        for i, doc_vec in enumerate(self._doc_vecs):
            score = _cosine(q_vec, doc_vec)
            scored.append((score, i))

        scored.sort(reverse=True)
        results = []
        for score, idx in scored[:k]:
            ex = dict(self._examples[idx])
            ex["score"] = round(score, 4)
            results.append(ex)
        return results

    def add(self, question: str, sql: str, metadata: Optional[dict] = None) -> None:
        """Persist a new (question, sql) example to user_examples.json."""
        self._ensure_loaded()
        entry = {"question": question, "sql": sql.strip(), "metadata": metadata or {}}
        self._examples.append(entry)
        self._dirty = True

        # Persist to user additions file
        existing = []
        if self._additions_path.exists():
            try:
                existing = json.loads(self._additions_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.append(entry)
        self._additions_path.parent.mkdir(parents=True, exist_ok=True)
        self._additions_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def format_for_prompt(self, examples: list[dict]) -> str:
        """Format retrieved examples as few-shot context for LLM SQL generation prompt."""
        if not examples:
            return ""
        lines = ["--- SIMILAR SQL EXAMPLES (use as reference) ---"]
        for i, ex in enumerate(examples, 1):
            lines.append(f"\nExample {i} (similarity: {ex.get('score', '?'):.2f}):")
            lines.append(f"Q: {ex['question']}")
            lines.append(f"SQL:\n{ex['sql']}")
            meta = ex.get("metadata", {})
            if meta.get("metric"):
                lines.append(f"Metric: {meta['metric']}")
        lines.append("--- END EXAMPLES ---")
        return "\n".join(lines)

    def count(self) -> int:
        self._ensure_loaded()
        return len(self._examples)

    # ── Internal ────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        examples = []
        # Load seeded examples
        if self._seeded_path.exists():
            try:
                examples.extend(
                    json.loads(self._seeded_path.read_text(encoding="utf-8"))
                )
            except Exception as e:
                print(f"[ExampleStore] Failed to load seeded examples: {e}")
        # Load user additions
        if self._additions_path.exists():
            try:
                examples.extend(
                    json.loads(self._additions_path.read_text(encoding="utf-8"))
                )
            except Exception as e:
                print(f"[ExampleStore] Failed to load user examples: {e}")
        self._examples = examples
        self._loaded = True

    def _ensure_index(self) -> None:
        if not self._dirty:
            return
        corpus = [_tokenize(ex["question"]) for ex in self._examples]
        self._idf, self._doc_vecs = _tfidf_vectors(corpus)
        self._dirty = False


# Singleton for import convenience
example_store = ExampleStore()
