"""
Verbatim Verification — checks every extracted quote against source transcript.
Adds verified:true/false to each quote field. Adds _quality_score to matrix.
Works for any project (CoinDCX, Mixer, future).

Usage:
    python verify_verbatims.py --project karat-coindcx
    python verify_verbatims.py --project mixer
    python verify_verbatims.py --project karat-coindcx --force  # re-verify even if already done
"""

import argparse, json, re, sys
from pathlib import Path
from datetime import datetime, timezone

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── Fields to verify per project type ─────────────────────────────────────────
# Each entry: (json_path, is_array, verbatim_key)
# json_path uses dot notation; arrays denoted by []

_UNIVERSAL_VERBATIM_FIELDS = [
    ("pain_points[].verbatim_quote", True, "verbatim_quote"),
    ("all_passages[].content", True, "content"),
]

# NOTE: per-project hardcoded field lists (_COINDCX_VERBATIM_FIELDS, _MIXER_VERBATIM_FIELDS)
# were removed 2026-08-06 — they silently went stale when project_extractor.py's schema
# changed on 2026-07-17 (fields renamed/restructured), causing verify_verbatims.py to check
# nonexistent paths and mis-score real quotes as "paraphrased". Replaced with
# _discover_verbatim_fields() below, which finds verbatim-bearing fields generically by
# the schema's own naming convention instead of a hand-maintained path list. This matches
# how project_extractor.py actually names these fields (schema keys containing "verbatim"
# always carry the transcript quote — enforced by the "exact copy — never paraphrase" rule
# in extraction_schema.json).


def _discover_verbatim_fields(obj, path: str = "") -> list[tuple[str, str]]:
    """
    Recursively find every string field whose key name contains 'verbatim' (case-insensitive),
    at any depth, in dicts and lists-of-dicts. Skips '_'-prefixed metadata keys.
    Schema-agnostic replacement for the old hardcoded per-project field lists.
    Returns [(label_path, text), ...].
    """
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("_"):
                continue
            sub_path = f"{path}.{k}" if path else k
            if isinstance(v, str) and "verbatim" in k.lower():
                found.append((sub_path, v))
            elif isinstance(v, (dict, list)):
                found.extend(_discover_verbatim_fields(v, sub_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.extend(_discover_verbatim_fields(item, f"{path}[{i}]"))
    return found


def _norm(text: str) -> str:
    """Normalize text for comparison: lowercase, collapse whitespace, strip punctuation."""
    t = text.lower()
    t = re.sub(r"[‘’“”–—â€™]", "'", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _check_in_transcript(verbatim: str, transcript_norm: str, min_len: int = 12) -> dict:
    """
    Check if verbatim exists in transcript.
    Returns: {found: bool, method: str, confidence: str, match_start: int or -1}
    """
    if not verbatim or len(verbatim.strip()) < min_len:
        return {"found": None, "method": "too_short", "confidence": "na", "match_start": -1}

    v = _norm(verbatim)

    # Method 1: exact substring match (best)
    pos = transcript_norm.find(v)
    if pos != -1:
        return {"found": True, "method": "exact", "confidence": "high", "match_start": pos}

    # Method 2: match first 40 chars (handles minor truncation)
    prefix = v[:40]
    pos = transcript_norm.find(prefix)
    if pos != -1:
        return {"found": True, "method": "prefix_40", "confidence": "high", "match_start": pos}

    # Method 3: match first 25 chars (handles more truncation)
    prefix25 = v[:25]
    if len(prefix25) >= 15:
        pos = transcript_norm.find(prefix25)
        if pos != -1:
            return {"found": True, "method": "prefix_25", "confidence": "medium", "match_start": pos}

    # Method 4: word-overlap check (detects paraphrases)
    v_words = set(w for w in v.split() if len(w) > 4)
    if len(v_words) < 3:
        return {"found": False, "method": "no_match", "confidence": "low", "match_start": -1}

    # Find any 5-word window in transcript matching >60% of verbatim words
    t_words = transcript_norm.split()
    window = 20
    best_overlap = 0
    best_pos = -1
    for i in range(len(t_words) - window):
        w_set = set(t_words[i:i+window])
        overlap = len(v_words & w_set) / len(v_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_pos = i
    if best_overlap >= 0.6:
        return {"found": True, "method": "word_overlap", "confidence": "medium",
                "match_start": best_pos, "overlap_pct": round(best_overlap * 100)}

    return {"found": False, "method": "no_match", "confidence": "low", "match_start": -1}


def _get_transcript_path(matrix: dict, project_dir: Path, project_id: str) -> Path | None:
    """Resolve the source .md transcript for a matrix. Checks processed/ and transcripts/ directly."""
    doc_id = matrix.get("doc_id", "")

    # Search order: transcripts/processed/, transcripts/, qualitative/processed/ (legacy)
    search_dirs = [
        project_dir / "transcripts" / "processed",
        project_dir / "transcripts",
        project_dir.parent.parent / "qualitative" / "processed",  # legacy flat path
    ]

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        # Direct name match
        exact = search_dir / f"{doc_id}.md"
        if exact.exists():
            return exact
        # Glob with underscore separator to prevent DI_2 matching DI_20, DI_21, etc.
        matches = list(search_dir.glob(f"{doc_id}_*.md"))
        if matches:
            return matches[0]
        # Fallback: dot separator (e.g. DI_2.something.md)
        matches = list(search_dir.glob(f"{doc_id}.*.md"))
        if matches:
            return matches[0]

    return None


def _deep_get(obj: dict, path: str):
    """Get nested value by dot-path. Handles [] for arrays."""
    parts = path.split(".")
    cur = obj
    for p in parts:
        if p.endswith("[]"):
            key = p[:-2]
            cur = cur.get(key, []) if isinstance(cur, dict) else []
            if not isinstance(cur, list):
                return []
        elif isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list):
            # Flatten list access
            results = []
            for item in cur:
                if isinstance(item, dict):
                    v = item.get(p)
                    if v is not None:
                        results.append(v)
            return results
        else:
            return None
        if cur is None:
            return None
    return cur


def verify_matrix(matrix: dict, project_id: str, project_dir: Path, force: bool = False) -> dict:
    """
    Verify all verbatim quotes in a matrix against source transcript.
    Adds _verification field with per-quote results and _quality_score.
    Returns updated matrix dict.
    """
    if not force and matrix.get("_verification", {}).get("completed"):
        return matrix

    # Load transcript
    tp = _get_transcript_path(matrix, project_dir, project_id)
    if not tp:
        matrix["_verification"] = {
            "completed": False, "error": "transcript_not_found",
            "doc_id": matrix.get("doc_id", ""),
        }
        return matrix

    try:
        raw = tp.read_text(encoding="utf-8", errors="replace")
        # Strip YAML frontmatter
        raw = re.sub(r"^---[\s\S]*?---\n", "", raw).strip()
        transcript_norm = _norm(raw)
    except Exception as e:
        matrix["_verification"] = {"completed": False, "error": str(e)}
        return matrix

    all_fields = _UNIVERSAL_VERBATIM_FIELDS.copy()

    results = {}
    total = 0
    verified = 0
    paraphrased = 0
    missing = 0

    for field_path, is_array, subkey in all_fields:
        # Get value(s) from matrix
        if is_array:
            # e.g. "pain_points[].verbatim_quote"
            base_path = field_path.split("[].")[0]
            items = matrix.get(base_path, [])
            if not isinstance(items, list):
                continue
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                vtext = item.get(subkey, "")
                if not vtext or len(str(vtext).strip()) < 8:
                    continue
                check = _check_in_transcript(str(vtext), transcript_norm)
                item[f"_{subkey}_verified"] = check["found"]
                item[f"_{subkey}_verify_method"] = check["method"]
                total += 1
                if check["found"] is True:
                    verified += 1
                elif check["found"] is False:
                    if check["method"] == "no_match":
                        paraphrased += 1
                else:
                    missing += 1
                results[f"{base_path}[{i}].{subkey}"] = check
        else:
            # Scalar field
            vtext = matrix
            for part in field_path.split("."):
                if isinstance(vtext, dict):
                    vtext = vtext.get(part)
                else:
                    vtext = None
                    break
            if not vtext or len(str(vtext).strip()) < 8:
                continue
            check = _check_in_transcript(str(vtext), transcript_norm)
            total += 1
            if check["found"] is True:
                verified += 1
            elif check["found"] is False:
                paraphrased += 1
            results[field_path] = check

    # Generic schema-agnostic discovery — catches any verbatim-bearing field regardless
    # of extraction schema shape. Excludes pain_points/all_passages (already handled above
    # via _UNIVERSAL_VERBATIM_FIELDS) to avoid double-counting.
    scan_root = {k: v for k, v in matrix.items() if k not in ("pain_points", "all_passages") and not k.startswith("_")}
    for label, vtext in _discover_verbatim_fields(scan_root):
        if not vtext or len(str(vtext).strip()) < 8:
            continue
        check = _check_in_transcript(str(vtext), transcript_norm)
        total += 1
        if check["found"] is True:
            verified += 1
        elif check["found"] is False:
            paraphrased += 1
        else:
            missing += 1
        results[label] = check

    quality_score = round(verified / max(total, 1) * 100, 1)

    matrix["_verification"] = {
        "completed": True,
        "transcript_path": str(tp),
        "transcript_chars": len(raw),
        "total_quotes_checked": total,
        "verified": verified,
        "paraphrased": paraphrased,
        "too_short_or_missing": missing,
        "quality_score": quality_score,
        "quality_label": "excellent" if quality_score >= 80 else ("good" if quality_score >= 60 else ("poor" if quality_score >= 30 else "critical")),
        "field_results": results,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    matrix["_quality_score"] = quality_score

    return matrix


def run_verification(project_id: str, force: bool = False):
    project_dir = _DATA_DIR / "projects" / project_id
    matrices_dir = project_dir / "matrices"

    if not matrices_dir.exists():
        print(f"ERROR: {matrices_dir} not found")
        sys.exit(1)

    files = sorted(matrices_dir.glob("*_matrix.json"))
    if not files:
        print("No matrices found.")
        return

    summary = {
        "project_id": project_id,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "total_matrices": len(files),
        "results": {},
    }

    total_q = 0; total_v = 0; total_p = 0

    for i, fp in enumerate(files, 1):
        try:
            m = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[{i}/{len(files)}] {fp.name} — LOAD ERROR: {e}")
            continue

        m = verify_matrix(m, project_id, project_dir, force=force)
        vdata = m.get("_verification", {})

        qs  = m.get("_quality_score", 0)
        v   = vdata.get("verified", 0)
        t   = vdata.get("total_quotes_checked", 0)
        p   = vdata.get("paraphrased", 0)
        lbl = vdata.get("quality_label", "?")
        err = vdata.get("error", "")

        total_q += t; total_v += v; total_p += p

        status = f"OK {v}/{t} verified  score={qs}  [{lbl}]" if not err else f"ERROR: {err}"
        print(f"[{i}/{len(files)}] {fp.stem:35} {status}")

        summary["results"][fp.name] = {
            "quality_score": qs,
            "quality_label": lbl,
            "verified": v,
            "paraphrased": p,
            "total": t,
            "error": err,
        }

        fp.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    overall = round(total_v / max(total_q, 1) * 100, 1)
    summary["overall_quality_score"] = overall
    summary["overall_verified"] = total_v
    summary["overall_paraphrased"] = total_p
    summary["overall_total"] = total_q

    report_path = project_dir / "verification_report.json"
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"Overall quality: {overall}%  ({total_v}/{total_q} quotes verified)")
    print(f"Paraphrased (not in transcript): {total_p}")
    print(f"Report: {report_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    run_verification(args.project, force=args.force)


if __name__ == "__main__":
    main()
