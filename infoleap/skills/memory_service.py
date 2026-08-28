"""
LENSMemory — ChromaDB-backed episodic + semantic memory for the LENS agent.
Absolute path resolution and safe init (disabled gracefully if ChromaDB unavailable).
"""
from datetime import datetime
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent.parent   # oxdata/
DB_PATH   = _THIS_DIR / "data" / "memory_store"
DB_PATH.mkdir(parents=True, exist_ok=True)


class LENSMemory:
    def __init__(self):
        try:
            import chromadb
            from chromadb.utils import embedding_functions
            self.client    = chromadb.PersistentClient(path=str(DB_PATH))
            self.embed_fn  = embedding_functions.DefaultEmbeddingFunction()
            self.episodes  = self.client.get_or_create_collection(
                name="lens_episodes",  embedding_function=self.embed_fn)
            self.semantics = self.client.get_or_create_collection(
                name="lens_semantics", embedding_function=self.embed_fn)
            self._available = True
        except Exception as e:
            print(f"[Memory] ChromaDB unavailable: {e}. Memory disabled.")
            self._available = False

    def store_episode(self, question: str, plan: str, summary: str) -> str | None:
        if not self._available:
            return None
        try:
            doc_id = f"ep_{int(datetime.now().timestamp())}"
            self.episodes.add(
                documents=[f"Question: {question}\nSQL: {plan}\nSummary: {summary}"],
                metadatas=[{"timestamp": str(datetime.now()), "type": "research_cycle"}],
                ids=[doc_id])
            return doc_id
        except Exception:
            return None

    def retrieve_relevant_episodes(self, query: str, limit: int = 2) -> list[str]:
        if not self._available:
            return []
        try:
            results = self.episodes.query(query_texts=[query], n_results=limit)
            return results["documents"][0] if results.get("documents") else []
        except Exception:
            return []

    def store_fact(self, entity: str, fact: str) -> None:
        if not self._available:
            return
        try:
            self.semantics.add(
                documents=[fact],
                metadatas=[{"entity": entity}],
                ids=[f"fact_{entity[:20]}_{int(datetime.now().timestamp())}"])
        except Exception:
            pass

    def recall_facts(self, entity: str, limit: int = 3) -> list[str]:
        if not self._available:
            return []
        try:
            results = self.semantics.query(query_texts=[entity], n_results=limit)
            return results["documents"][0] if results.get("documents") else []
        except Exception:
            return []


# Global singleton
memory = LENSMemory()
