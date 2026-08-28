"""
Repairs matrices that failed JSON parsing by reading their raw_step2.txt files
and applying the JSON repair function. Much faster than re-extracting.

Usage:
    python repair_matrices.py --project karat-coindcx
"""
import argparse, json, re, sys
from pathlib import Path
from datetime import datetime, timezone

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _repair_json(text: str) -> str:
    # Strip reasoning blocks
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    text = re.sub(r"<\|begin_of_thought\|>[\s\S]*?<\|end_of_thought\|>", "", text).strip()
    # Strip markdown fences
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    # Fix trailing commas
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    # Fix mismatched brackets
    stack = []
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch in ('{', '['):
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '[':
                chars[i] = ']'; stack.pop()
            elif stack and stack[-1] == '{':
                stack.pop()
        elif ch == ']':
            if stack and stack[-1] == '{':
                chars[i] = '}'; stack.pop()
            elif stack and stack[-1] == '[':
                stack.pop()
    return "".join(chars)


def _close_truncated(text: str) -> str:
    """Close truncated JSON by appending missing bracket closers."""
    stack = []; in_str = False; esc = False
    last_safe = 0
    for i, ch in enumerate(text):
        if esc: esc = False; continue
        if ch == '\\' and in_str: esc = True; continue
        if ch == '"': in_str = not in_str; continue
        if in_str: continue
        if ch in ('{', '['): stack.append(ch)
        elif ch in ('}', ']'):
            if stack: stack.pop()
            if not stack: last_safe = i + 1
    if not stack:
        return text
    # Truncate to last safe position, strip trailing comma, close
    safe = text[:last_safe] if last_safe > 0 else text
    # Re-scan safe portion to find remaining unclosed
    stk2 = []; in_s = False; esc2 = False
    for ch in safe:
        if esc2: esc2=False; continue
        if ch=='\\' and in_s: esc2=True; continue
        if ch=='"': in_s=not in_s; continue
        if in_s: continue
        if ch in ('{','['): stk2.append(ch)
        elif ch in ('}',']') and stk2: stk2.pop()
    closer_map = {"{": "}", "[": "]"}
    closers = "".join(closer_map[c] for c in reversed(stk2))
    return safe.rstrip(',').rstrip() + closers


def _extract_json(text: str) -> dict | None:
    if not text or not text.strip():
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    attempts = [cleaned, _repair_json(cleaned), _close_truncated(cleaned),
                _repair_json(_close_truncated(cleaned))]
    for attempt in attempts:
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, Exception):
            pass
    m = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if m:
        block = m.group(1)
        for attempt in [block, _repair_json(block), _close_truncated(block),
                        _repair_json(_close_truncated(block))]:
            try:
                return json.loads(attempt)
            except (json.JSONDecodeError, Exception):
                pass
    return None


def repair_project(project_id: str):
    matrices_dir = _DATA_DIR / "projects" / project_id / "matrices"
    raw_files = list(matrices_dir.glob("*_raw_step2.txt"))

    if not raw_files:
        print("No raw step2 files found.")
        return

    ok = 0; failed = 0; empty = 0

    for raw_path in sorted(raw_files):
        doc_id = raw_path.stem.replace("_raw_step2", "")
        matrix_path = matrices_dir / f"{doc_id}_matrix.json"
        step1_path = matrices_dir / f"{doc_id}_step1.txt"

        raw_text = raw_path.read_text(encoding="utf-8", errors="replace").strip()

        if not raw_text:
            print(f"  {doc_id}: EMPTY raw file — skipping")
            empty += 1
            continue

        parsed = _extract_json(raw_text)
        if parsed is None:
            print(f"  {doc_id}: REPAIR FAILED — JSON unrecoverable")
            failed += 1
            continue

        # Load existing matrix (may have stale data)
        existing = {}
        if matrix_path.exists():
            try:
                existing = json.loads(matrix_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Ensure identifiers are set
        parsed.setdefault("doc_id", doc_id)
        parsed.setdefault("filename", existing.get("filename", ""))
        if "respondent" not in parsed and "respondent" in existing:
            parsed["respondent"] = existing["respondent"]

        # Restore step1 text
        if step1_path.exists():
            parsed["_step1_text"] = step1_path.read_text(encoding="utf-8", errors="replace")
        elif "_step1_text" in existing:
            parsed["_step1_text"] = existing["_step1_text"]

        parsed["_repaired_at"] = datetime.now(timezone.utc).isoformat()
        parsed.pop("parse_error", None)

        matrix_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {doc_id}: REPAIRED -> archetype={parsed.get('investor_archetype')} route={parsed.get('preferred_route')} claims={len(parsed.get('key_claim_reactions') or [])}")
        ok += 1

    print(f"\nDone: {ok} repaired, {empty} empty (need re-extract), {failed} unrecoverable")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    repair_project(args.project)


if __name__ == "__main__":
    main()
