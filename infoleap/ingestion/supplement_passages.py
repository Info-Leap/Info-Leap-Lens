"""
Supplements existing matrices with raw-extracted respondent passages.
NO LLM calls â€” purely deterministic. Free to run any time.
Reads raw MD files, extracts all R: turns, merges into existing matrices.

Run:
    python lens/ingestion/supplement_passages.py
"""

import sys, json, re
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

MATRICES_DIR  = Path(__file__).parent.parent.parent / "oxdata" / "data" / "qual_matrices"
PROCESSED_DIR = Path(__file__).parent.parent.parent / "oxdata" / "data" / "qualitative" / "processed"

# Import helpers from builder
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from infoleap.ingestion.transcript_matrix_builder import _extract_raw_passages, _merge_passages


def supplement_all():
    matrix_files = sorted(MATRICES_DIR.glob("*_matrix.json"))
    if not matrix_files:
        print("No matrix files found.")
        return

    before_counts = []
    after_counts  = []
    updated = 0

    for mpath in matrix_files:
        m = json.loads(mpath.read_text(encoding="utf-8"))
        doc_id = m.get("doc_id", mpath.stem.replace("_matrix",""))

        # Find corresponding raw MD
        raw_path = PROCESSED_DIR / f"{doc_id}.md"
        if not raw_path.exists():
            continue

        raw_md = raw_path.read_text(encoding="utf-8")
        raw_passages = _extract_raw_passages(raw_md)

        if not raw_passages:
            continue

        existing = m.get("all_passages", [])
        merged   = _merge_passages(existing, raw_passages)

        before = len(existing)
        after  = len(merged)
        before_counts.append(before)
        after_counts.append(after)

        if after > before:
            m["all_passages"] = merged
            m["_passages_supplemented"] = True
            mpath.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
            updated += 1
            if updated <= 10 or updated % 25 == 0:
                print(f"  [{updated}] {doc_id[:50]}: {before} -> {after} passages")

    n = len(before_counts)
    if n > 0:
        avg_before = sum(before_counts) / n
        avg_after  = sum(after_counts)  / n
        print(f"\nDone. {updated}/{n} docs supplemented.")
        print(f"Avg passages: {avg_before:.1f} -> {avg_after:.1f}")
        print(f"Docs reaching >=10 passages: {sum(1 for c in after_counts if c >= 10)}/{n}")


if __name__ == "__main__":
    supplement_all()
