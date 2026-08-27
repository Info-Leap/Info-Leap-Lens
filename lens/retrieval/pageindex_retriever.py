"""
Searches the PageIndex forest for relevant passages using OpenRouter.
"""

import os
import json
import re
from pathlib import Path
from openai import OpenAI
from ingestion.pageindex_builder import load_registry

def rprint(*args, **kwargs):
    import re
    text = " ".join(str(a) for a in args)
    text = re.sub(r"\[/?[a-zA-Z_ ]*\]", "", text)
    print(text)

OR_KEY = os.getenv("OPENROUTER_API_KEY")
OR_MODEL = "deepseek/deepseek-v4-pro"
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OR_KEY)

def search_tree(tree: dict, query: str, doc_id: str) -> list[dict]:
    """
    Performs reasoning-based retrieval over a single PageIndex tree using OpenRouter.
    """
    RETRIEVAL_PROMPT = f"""You are searching a document index tree to find passages
relevant to this query: "{query}"

The tree structure below represents a document's table of contents with summaries.
Identify the most relevant sections and return their content.

Tree:
{json.dumps(tree, indent=2)[:6000]}

Return a JSON array of relevant passages. Each passage must have:
- section_title: the section heading
- content: the relevant text (verbatim from tree node)
- relevance_score: 0.0 to 1.0
- doc_id: "{doc_id}"

Return only passages with relevance_score >= 0.5.
Return valid JSON array only. No markdown."""

    try:
        response = client.chat.completions.create(
            model=OR_MODEL,
            messages=[
                {"role": "user", "content": RETRIEVAL_PROMPT}
            ],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        
        raw = response.choices[0].message.content
        result = json.loads(raw)
    except Exception as e:
        rprint(f"[red]Tree search (OpenRouter) failed for {doc_id}: {e}[/red]")
        return []

    # Handle both {"passages": [...]} and direct array
    if isinstance(result, dict):
        passages = result.get("passages", result.get("results", []))
    else:
        passages = result
    return passages if isinstance(passages, list) else []

def retrieve_from_forest(query: str, relevant_doc_ids: list[str]) -> list[dict]:
    """
    Master retrieval function.
    """
    registry = load_registry()
    doc_map = {d["doc_id"]: d for d in registry.get("documents", [])}

    all_passages = []

    for doc_id in relevant_doc_ids:
        if doc_id not in doc_map:
            rprint(f"[yellow]Doc ID {doc_id} not in registry, skipping.[/yellow]")
            continue

        doc_entry = doc_map[doc_id]
        tree_path = doc_entry.get("tree_path")

        if not tree_path or not Path(tree_path).exists():
            rprint(f"[yellow]Tree not found for {doc_id}, skipping.[/yellow]")
            continue

        with open(tree_path) as f:
            tree = json.load(f)

        rprint(f"[blue]Searching tree:[/blue] {doc_id}")
        passages = search_tree(tree, query, doc_id)

        # Enrich with doc metadata
        for p in passages:
            p["doc_type"] = doc_entry.get("doc_type", "unknown")
            p["category"] = doc_entry.get("category", "unknown")
            p["filename"] = doc_entry.get("filename", "")

        all_passages.extend(passages)

    # Sort by relevance
    all_passages.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    rprint(f"  Found {len(all_passages)} relevant passages across {len(relevant_doc_ids)} docs.")
    return all_passages[:10]  # top 10 passages
