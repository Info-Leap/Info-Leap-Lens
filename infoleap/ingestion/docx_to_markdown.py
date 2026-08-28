"""
Converts DOCX interview transcripts to clean markdown.
Pseudonymisation is applied BEFORE any other processing.
"""

import os
from pathlib import Path
from docx import Document
from ingestion.pseudonymise import pseudonymise_text, mask_name
def rprint(*args, **kwargs):
    import re
    text = " ".join(str(a) for a in args)
    text = re.sub(r"\[/?[a-zA-Z_ ]*\]", "", text)
    print(text)

def extract_names_from_filename(filename: str) -> list[str]:
    """
    Attempts to extract respondent names from filename conventions.
    e.g. TRANSCRIPT-Anvi_Sujata_MG_Mumbai.docx  ['Anvi', 'Sujata']
    """
    stem = Path(filename).stem
    # Remove common prefixes
    for prefix in ['TRANSCRIPT-', 'TRANSCRIPT_', 'transcript_']:
        stem = stem.replace(prefix, '')
    # Split on underscores, take first 2 parts as potential names
    parts = stem.split('_')
    # Filter: names are typically capitalised and short
    names = [p for p in parts if len(p) > 2 and p[0].isupper() and p.isalpha()]
    return names[:2]  # max 2 names per file

def docx_to_markdown(docx_path: str, output_dir: str = "data/processed") -> str:
    """
    Converts a DOCX transcript to pseudonymised markdown.
    Returns path to the output markdown file.
    """
    docx_path = Path(docx_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rprint(f"[blue]Processing:[/blue] {docx_path.name}")

    # Extract known names from filename for targeted masking
    known_names = extract_names_from_filename(docx_path.name)
    rprint(f"  Names to mask: {known_names}")

    # Read DOCX
    doc = Document(str(docx_path))
    lines = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        # Detect headings
        if para.style.name.startswith('Heading'):
            level = para.style.name[-1] if para.style.name[-1].isdigit() else '2'
            lines.append(f"{'#' * int(level)} {text}")
        else:
            lines.append(text)

    full_text = '\n\n'.join(lines)

    # PSEUDONYMISE  this runs before anything else
    clean_text = pseudonymise_text(full_text, known_names=known_names)

    # Build metadata header
    metadata = f"""---
source_file: {docx_path.name}
document_type: transcript
category: unknown  # will be set by registry
masked_respondents: {[mask_name(n) for n in known_names]}
processing_note: All PII removed. Real names replaced with masked IDs.
---

"""
    output_text = metadata + clean_text

    # Write output
    output_filename = docx_path.stem + "_clean.md"
    output_path = output_dir / output_filename
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output_text)

    rprint(f"  [green]Saved:[/green] {output_path}")
    return str(output_path)

def process_all_transcripts(raw_dir: str = "data/raw") -> list[str]:
    """Processes all DOCX files found in raw_dir."""
    raw_dir = Path(raw_dir)
    docx_files = list(raw_dir.rglob("*.docx"))

    if not docx_files:
        rprint("[yellow]No DOCX files found in data/raw/[/yellow]")
        return []

    output_paths = []
    for f in docx_files:
        if f.name.startswith('~$'): continue # skip temp files
        try:
            out = docx_to_markdown(str(f))
            output_paths.append(out)
        except Exception as e:
            rprint(f"[red]ERROR processing {f.name}:[/red] {e}")

    rprint(f"\n[green]Processed {len(output_paths)}/{len(docx_files)} transcripts.[/green]")
    return output_paths

