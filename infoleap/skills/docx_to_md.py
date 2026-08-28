"""
Converts .docx transcript files to cleaned .md format.
Preserves M:/R: dialogue markers. Extracts metadata from filename.
Usage:
    python docx_to_md.py --project karat-coindcx
    python docx_to_md.py --project karat-coindcx --force   # re-process existing
"""

import argparse
import json
import re
import sys
from pathlib import Path
from zipfile import BadZipFile

try:
    from docx import Document
except ImportError:
    print("ERROR: python-docx not installed. Run: pip install python-docx")
    sys.exit(1)

# ── path resolution ─────────────────────────────────────────────────────────
_SKILLS_DIR = Path(__file__).resolve().parent
_OXDATA_DIR = _SKILLS_DIR.parent
_DATA_DIR = _OXDATA_DIR / "data"


def _parse_filename(filename: str) -> dict:
    """
    Extract metadata from DI transcript filename.

    Supported patterns (after stripping trailing .docx):
      DI {n}_{gender}_{segment}_{city}.m4a
      DI {n}_{gender}_{segment}_{city}.mp4
      DI {n}_{gender}_{segment}_{city}
    Returns dict with keys: di_number, gender, segment, city.
    Unknown fields are set to null.
    """
    stem = filename
    # strip .docx
    if stem.lower().endswith(".docx"):
        stem = stem[:-5]
    # strip trailing audio extension (.m4a / .mp4 / .wav / .mp3)
    stem = re.sub(r"\.(m4a|mp4|wav|mp3)$", "", stem, flags=re.IGNORECASE)

    meta = {
        "di_number": None,
        "gender": None,
        "segment": None,
        "city": None,
    }

    # match: "DI <number>_<gender>_<segment>_<city>"
    # segment can contain spaces (e.g. "Stock Investor", "Digital Gold",
    # "Stock InvestorTrader")
    m = re.match(
        r"^DI\s+(\d+)_([^_]+)_(.+)_([^_]+)$",
        stem,
        re.IGNORECASE,
    )
    if m:
        meta["di_number"] = m.group(1)
        meta["gender"] = m.group(2).strip()
        meta["segment"] = m.group(3).strip()
        meta["city"] = m.group(4).strip()
    else:
        # fallback: split on underscore, best-effort
        parts = stem.split("_")
        if parts:
            di_part = parts[0].strip()
            dm = re.match(r"DI\s+(\d+)", di_part, re.IGNORECASE)
            if dm:
                meta["di_number"] = dm.group(1)
            if len(parts) >= 2:
                meta["gender"] = parts[1].strip()
            if len(parts) >= 3:
                meta["segment"] = parts[2].strip()
            if len(parts) >= 4:
                meta["city"] = parts[3].strip()

    return meta


def _is_cruft(text: str) -> bool:
    """Return True for lines that are page numbers, timestamps, or pure artifacts."""
    stripped = text.strip()
    if not stripped:
        return True
    # standalone page numbers
    if re.match(r"^\d+$", stripped):
        return True
    # timestamps like 00:12:34 or 0:12:34
    if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", stripped):
        return True
    return False


def _extract_text_from_docx(docx_path: Path) -> list[str]:
    """
    Read paragraphs from .docx. Returns list of non-empty, non-cruft strings.
    Preserves M: / R: speaker markers.
    """
    doc = Document(str(docx_path))
    lines = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if _is_cruft(text):
            continue
        lines.append(text)
    return lines


def _build_md(meta: dict, lines: list[str], source_file: str) -> str:
    """Build .md content with YAML frontmatter + body paragraphs."""
    segment = meta.get("segment") or "Unknown"
    city = meta.get("city") or "Unknown"
    gender = meta.get("gender") or "Unknown"
    di_number = meta.get("di_number") or "?"

    frontmatter = (
        "---\n"
        f'di_number: "{di_number}"\n'
        f"gender: {gender}\n"
        f"segment: {segment}\n"
        f"city: {city}\n"
        f"source_file: {source_file}\n"
        "---\n"
    )

    body = "\n\n".join(lines)
    return frontmatter + "\n" + body + "\n"


def process_project(project_id: str, force: bool = False) -> dict:
    """
    Convert all .docx files in projects/{project_id}/transcripts/ to .md.
    Writes output to transcripts/processed/.
    Returns index dict.
    """
    transcripts_dir = _DATA_DIR / "projects" / project_id / "transcripts"
    processed_dir = transcripts_dir / "processed"

    if not transcripts_dir.exists():
        print(f"ERROR: transcripts dir not found: {transcripts_dir}")
        sys.exit(1)

    processed_dir.mkdir(parents=True, exist_ok=True)

    docx_files = sorted(
        transcripts_dir.glob("*.docx"),
        key=lambda p: (
            int(re.search(r"DI\s*(\d+)", p.name, re.IGNORECASE).group(1))
            if re.search(r"DI\s*(\d+)", p.name, re.IGNORECASE)
            else 9999
        ),
    )

    if not docx_files:
        print(f"No .docx files found in {transcripts_dir}")
        return {}

    total = len(docx_files)
    index = {}

    for i, docx_path in enumerate(docx_files, 1):
        filename = docx_path.name
        meta = _parse_filename(filename)
        di_num = meta.get("di_number") or "unknown"
        segment = meta.get("segment") or "Unknown"
        city = meta.get("city") or "Unknown"

        out_stem = re.sub(r"[^\w\-]", "_", filename.replace(".docx", ""))
        out_filename = out_stem + ".md"
        out_path = processed_dir / out_filename

        label = f"[{i}/{total}] DI {di_num} {meta.get('gender','')} {segment} {city}"

        if out_path.exists() and not force:
            print(f"{label} — SKIP (already processed)")
            index[filename] = {
                "output_md": str(out_path.relative_to(_DATA_DIR / "projects" / project_id)),
                "metadata": meta,
                "status": "skipped",
            }
            continue

        try:
            lines = _extract_text_from_docx(docx_path)
            md_content = _build_md(meta, lines, filename)
            out_path.write_text(md_content, encoding="utf-8")
            word_count = sum(len(line.split()) for line in lines)
            print(f"{label} — OK ({word_count} words, {len(lines)} paragraphs)")
            index[filename] = {
                "output_md": str(out_path.relative_to(_DATA_DIR / "projects" / project_id)),
                "metadata": meta,
                "word_count": word_count,
                "paragraph_count": len(lines),
                "status": "ok",
            }
        except FileNotFoundError as exc:
            print(f"{label} — ERROR: file not found: {exc}")
            index[filename] = {"status": "error", "error": str(exc), "metadata": meta}
        except BadZipFile as exc:
            print(f"{label} — ERROR: corrupt docx (BadZipFile): {exc}")
            index[filename] = {"status": "error", "error": f"BadZipFile: {exc}", "metadata": meta}
        except Exception as exc:
            print(f"{label} — ERROR: {exc}")
            index[filename] = {"status": "error", "error": str(exc), "metadata": meta}

    # write index
    index_path = transcripts_dir / "processed_index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nIndex written: {index_path}")

    ok = sum(1 for v in index.values() if v.get("status") == "ok")
    skipped = sum(1 for v in index.values() if v.get("status") == "skipped")
    errors = sum(1 for v in index.values() if v.get("status") == "error")
    print(f"Done: {ok} converted, {skipped} skipped, {errors} errors (total {total})")

    return index


def main():
    parser = argparse.ArgumentParser(description="Convert .docx transcripts to .md")
    parser.add_argument("--project", required=True, help="Project ID (e.g. karat-coindcx)")
    parser.add_argument("--force", action="store_true", help="Re-process already converted files")
    args = parser.parse_args()
    process_project(args.project, force=args.force)


if __name__ == "__main__":
    main()
