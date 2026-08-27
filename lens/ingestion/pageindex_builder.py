"""
Builds a section-aware PageIndex tree for each processed document.
Uses Groq to split the document into logical sections, extract key passages,
and build a searchable tree. Falls back to chunk-based indexing for large docs.

Trees are saved as JSON to pageindex_trees/
The document_registry.json is updated after each successful build.
"""

import os
import json
import re
import time
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path
from datetime import datetime
def rprint(*args, **kwargs):
    import re
    text = " ".join(str(a) for a in args)
    text = re.sub(r"\[/?[a-zA-Z_ ]*\]", "", text)
    print(text)

TREES_DIR = Path("pageindex_trees")
REGISTRY_PATH = Path("data/registry/document_registry.json")
CHUNK_SIZE = 3000   # chars per chunk sent to LLM
MAX_CHUNKS = 6      # max chunks to summarise per doc (rate limit guard)

def load_registry() -> dict:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH) as f:
            return json.load(f)
    return {"documents": []}

def save_registry(registry: dict):
    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2)

def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split text into overlapping chunks."""
    words = text.split()
    chunks = []
    step = int(chunk_size * 0.9 / 5)   # ~90% of chunk_size in words
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + step])
        if chunk.strip():
            chunks.append(chunk)
    return chunks

def _groq_summarise_chunk(groq_client, chunk: str, doc_name: str, chunk_idx: int, model: str) -> dict:
    """Ask Groq to extract topics, themes, and key passages from a text chunk."""
    prompt = f"""You are indexing a market research interview transcript.
Document: {doc_name} | Chunk {chunk_idx + 1}

Extract the key insights from this transcript excerpt. Return a JSON object with:
- "title": a short section title (max 8 words)
- "themes": list of 2-5 key topic strings (e.g. "noise complaint", "jar capacity", "brand trust")
- "passages": list of up to 3 objects, each with:
    - "content": a verbatim quote or close paraphrase (100-200 words) showing a key consumer insight
    - "sentiment": "positive" | "negative" | "neutral"
    - "topic": what product/attribute this passage is about

Return ONLY valid JSON. No markdown. No explanation.

Transcript chunk:
{chunk[:3000]}"""

    try:
        resp = groq_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1024,
            response_format={"type": "json_object"}
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {
            "title": f"Section {chunk_idx + 1}",
            "themes": [],
            "passages": [{"content": chunk[:500], "sentiment": "neutral", "topic": "general"}]
        }

def build_tree_for_document(
    doc_path: str,
    doc_type: str,
    category: str,
    project_id: str,
    llm_model: str = "llama-3.3-70b-versatile",
) -> str:
    """
    Builds a rich PageIndex tree for a single document.
    Returns path to the saved tree JSON.
    """
    import groq

    TREES_DIR.mkdir(parents=True, exist_ok=True)
    doc_path = Path(doc_path)

    # Skip if already indexed
    tree_filename = doc_path.stem + "_tree.json"
    tree_path = TREES_DIR / tree_filename
    if tree_path.exists():
        rprint(f"  [dim]Skipping (already indexed):[/dim] {doc_path.name}")
        registry = load_registry()
        if not any(d["doc_id"] == doc_path.stem for d in registry["documents"]):
            with open(doc_path, 'r', encoding='utf-8') as f:
                content = f.read()
            _update_registry(registry, doc_path, doc_type, category, project_id, str(tree_path), content)
        return str(tree_path)

    rprint(f"[blue]Building tree:[/blue] {doc_path.name}")

    with open(doc_path, 'r', encoding='utf-8') as f:
        content = f.read()

    groq_client = groq.Groq(api_key=os.getenv("GROQ_API_KEY"))

    # Split into chunks and summarise
    chunks = _chunk_text(content)[:MAX_CHUNKS]
    sections = []
    for i, chunk in enumerate(chunks):
        section = _groq_summarise_chunk(groq_client, chunk, doc_path.name, i, llm_model)
        sections.append(section)
        if i < len(chunks) - 1:
            time.sleep(1.2)   # ~50 RPM guard for Groq free tier

    # Infer category from filename if "Mixer Grinder" not set
    inferred_category = _infer_category(doc_path.name, category)

    tree = {
        "doc_id": doc_path.stem,
        "filename": doc_path.name,
        "doc_type": doc_type,
        "category": inferred_category,
        "project_id": project_id,
        "word_count": len(content.split()),
        "sections": sections,
        "all_themes": list({t for s in sections for t in s.get("themes", [])})
    }

    with open(tree_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2, ensure_ascii=False)

    rprint(f"  [green]Tree saved:[/green] {tree_path.name} ({len(sections)} sections, {len(tree['all_themes'])} themes)")

    registry = load_registry()
    _update_registry(registry, doc_path, doc_type, inferred_category, project_id, str(tree_path), content)

    return str(tree_path)

def _infer_category(filename: str, default: str) -> str:
    """Infer product category from filename."""
    name_lower = filename.lower()
    if "mixer" in name_lower or "grinder" in name_lower or "juicer" in name_lower:
        return "Mixer Grinder"
    if "fan" in name_lower:
        return "Ceiling Fans"
    if "cooler" in name_lower:
        return "Air Coolers"
    if "smoothie" in name_lower or "blender" in name_lower:
        return "Mixer Grinder"   # smoothie makers are in MG category
    return default

def _update_registry(registry, doc_path, doc_type, category, project_id, tree_path_str, content):
    doc_entry = {
        "doc_id": doc_path.stem,
        "filename": doc_path.name,
        "doc_type": doc_type,
        "category": category,
        "project_id": project_id,
        "tree_path": tree_path_str,
        "indexed_at": datetime.now().isoformat(),
        "word_count": len(content.split())
    }
    registry["documents"] = [d for d in registry["documents"] if d["doc_id"] != doc_path.stem]
    registry["documents"].append(doc_entry)
    save_registry(registry)

def build_all_trees(processed_dir: str = "data/processed",
                    category: str = "Mixer Grinder",
                    project_id: str = "OX_WAVE1"):
    """Builds PageIndex trees for all processed markdown files."""
    processed_dir = Path(processed_dir)
    md_files = list(processed_dir.glob("*_clean.md"))

    if not md_files:
        rprint("[yellow]No processed markdown files found. Run ingestion first.[/yellow]")
        return []

    already_indexed = {f.stem.replace("_tree", "") for f in TREES_DIR.glob("*_tree.json")}
    pending = [f for f in md_files if f.stem not in already_indexed]
    rprint(f"[cyan]Total: {len(md_files)} | Already indexed: {len(already_indexed)} | To build: {len(pending)}[/cyan]")

    tree_paths = []
    for i, md_file in enumerate(md_files):
        try:
            tree_path = build_tree_for_document(
                doc_path=str(md_file),
                doc_type="transcript",
                category=category,
                project_id=project_id
            )
            tree_paths.append(tree_path)
        except Exception as e:
            rprint(f"[red]ERROR for {md_file.name}:[/red] {e}")
        # Brief pause every 10 docs to stay within Groq TPM limits
        if (i + 1) % 10 == 0:
            rprint(f"  [dim]Progress: {i+1}/{len(md_files)}[/dim]")
            time.sleep(5)

    rprint(f"\n[green]Built/verified {len(tree_paths)}/{len(md_files)} trees.[/green]")
    return tree_paths

