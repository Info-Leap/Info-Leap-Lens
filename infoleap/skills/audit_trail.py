"""
Atomistic audit-trail logging for the qual extraction pipeline.

Pattern taken directly from published LLM-qualitative-coding methodology research (process
auditability): for every LLM call that produces or touches respondent data, preserve — per call,
not per batch — the exact prompt sent, the model/params used, the raw response, and (where
applicable) the parsed/structured result and timestamp. This is what lets a reviewer answer
"why did the model score this respondent's trust as medium" or "did this schema-reconciliation
merge actually have real justification" without re-running anything.

One append-only JSONL file per project — data/projects/{id}/audit_trail.jsonl — one line per call,
chronological. JSONL (not one file per call) so it stays cheap to append and easy to grep/filter/tail
without a directory listing exploding into thousands of files on a large project.

Usage:
    from audit_trail import log_llm_call
    log_llm_call(project_id, doc_id="DI_1", step="step2_extraction", model="deepseek/deepseek-chat",
                 temp=0.1, max_tokens=8000, prompt=full_prompt_text, raw_output=raw_response,
                 parsed_result=parsed_matrix, extra={"quality_score": 80.8})
"""
import json
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def log_llm_call(
    project_id: str,
    doc_id: str | None,
    step: str,
    model: str | None,
    temp: float | None,
    max_tokens: int | None,
    prompt: str,
    raw_output: str,
    parsed_result: dict | list | None = None,
    extra: dict | None = None,
) -> None:
    """
    Append one audit record. Never raises — a logging failure must not break the actual
    extraction it's trying to document. Silently no-ops on write failure (printed, not raised).
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "doc_id": doc_id,
        "step": step,
        "model": model,
        "temp": temp,
        "max_tokens": max_tokens,
        "prompt_chars": len(prompt) if prompt else 0,
        "prompt": prompt,
        "raw_output": raw_output,
        "parsed_result": parsed_result,
    }
    if extra:
        record["extra"] = extra

    try:
        project_dir = _DATA_DIR / "projects" / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        log_path = project_dir / "audit_trail.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  WARNING: audit log write failed (non-fatal): {e}")


def read_audit_trail(project_id: str, doc_id: str | None = None, step: str | None = None,
                      limit: int | None = None) -> list[dict]:
    """Read back audit records, optionally filtered by doc_id and/or step. Most recent last
    (file order = chronological). limit returns only the last N matching records."""
    log_path = _DATA_DIR / "projects" / project_id / "audit_trail.jsonl"
    if not log_path.exists():
        return []
    records = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if doc_id and rec.get("doc_id") != doc_id:
                continue
            if step and rec.get("step") != step:
                continue
            records.append(rec)
    if limit:
        records = records[-limit:]
    return records
