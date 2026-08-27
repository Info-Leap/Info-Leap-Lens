import os
import sqlite3
import numpy as np
import requests
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

ROOT_DIR = Path(__file__).parent.parent.parent
load_dotenv(ROOT_DIR / ".env")

DB_PATH = str(ROOT_DIR / 'data/project_1/oxdata.db')
EMBED_DIR = ROOT_DIR / 'oxdata/data/embeddings/nomic'

# --- CLOUD CONFIGURATION ---
OR_KEY = os.getenv("OPENROUTER_API_KEY")
OR_BASE_URL = "https://openrouter.ai/api/v1"
# Use OpenAI but force 768 dimensions to match local index
EMBED_MODEL = "openai/text-embedding-3-small"

client = OpenAI(base_url=OR_BASE_URL, api_key=OR_KEY)

# Global cache for embeddings
_MATRIX_CACHE = None
_ID_CACHE = None

def _load_index():
    global _MATRIX_CACHE, _ID_CACHE
    if _MATRIX_CACHE is not None:
        return _MATRIX_CACHE, _ID_CACHE

    if not Path(DB_PATH).exists():
        return None, None

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id FROM fact_transcript_segments WHERE has_nomic_embedding = 1")
        rows = cur.fetchall()
        conn.close()
    except:
        return None, None

    ids = [r[0] for r in rows]
    if not ids: return None, None

    embs = []
    valid_ids = []
    for sid in ids:
        path = EMBED_DIR / f"seg_{sid}.npy"
        if path.exists():
            embs.append(np.load(path))
            valid_ids.append(sid)
    
    if not embs: return None, None

    _MATRIX_CACHE = np.vstack(embs)
    _ID_CACHE = valid_ids
    return _MATRIX_CACHE, _ID_CACHE

def semantic_search(query: str, top_k: int = 10):
    """Perform semantic search using Cloud API for embeddings."""
    matrix, ids = _load_index()
    if matrix is None:
        return []

    # 1. Get Embedding from OpenRouter (Cloud)
    try:
        # Note: OpenAI's text-embedding-3-small supports the 'dimensions' parameter
        # We check if OpenRouter passes this through
        response = client.embeddings.create(
            model=EMBED_MODEL,
            input=query,
            extra_body={"dimensions": 768} if "openai" in EMBED_MODEL else {}
        )
        query_emb = np.array(response.data[0].embedding, dtype=np.float32)
        
    except Exception as e:
        print(f"[CloudEmbedding] Failed: {e}")
        # Final Fallback: if dimension mismatch still occurs, we truncate as last resort
        return []

    # 2. Cosine Similarity (Local NumPy - Fast & Free)
    try:
        # Final safeguard for dimension mismatch
        if query_emb.shape[0] != matrix.shape[1]:
            if query_emb.shape[0] > matrix.shape[1]:
                query_emb = query_emb[:matrix.shape[1]]
            else:
                query_emb = np.pad(query_emb, (0, matrix.shape[1] - query_emb.shape[0]))

        norm_matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
        norm_query = query_emb / np.linalg.norm(query_emb)
        similarities = np.dot(norm_matrix, norm_query)
        top_indices = np.argsort(similarities)[::-1][:top_k]
    except Exception as e:
        print(f"[Numpy Math] Failed: {e}")
        return []
    
    results = []
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        for idx in top_indices:
            sid = ids[idx]
            score = float(similarities[idx])
            cur.execute("""
                SELECT s.text, b.transcript_id, b.city, b.brand
                FROM fact_transcript_segments s
                JOIN dim_qual_quant_bridge b ON s.bridge_id = b.id
                WHERE s.id = ?
            """, (sid,))
            row = cur.fetchone()
            if row:
                results.append({
                    "id": sid, "text": row[0], "doc_id": row[1],
                    "city": row[2], "brand": row[3], "score": score
                })
        conn.close()
    except:
        pass
        
    return results
