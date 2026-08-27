"""
Contextual Insights Layer — Benchmark Matching (0 API Calls)
=============================================================
Pure-Python module that enriches query results with domain-specific
benchmark context. Output is injected into the existing summary prompt,
adding ~100 tokens of context with ZERO additional API calls.

Key public functions:
  get_benchmark_context(df, skill_key, question, benchmarks) → str
  build_enriched_prompt(question, df, comparison_summary, benchmark_context) → str
"""

from __future__ import annotations

import pandas as pd


# ── helper ─────────────────────────────────────────────────────────────────────

def _has_col(df: pd.DataFrame, *frags: str) -> str | None:
    """Return the first column whose name contains any of the given fragments."""
    for col in df.columns:
        cl = col.lower()
        for f in frags:
            if f.lower() in cl:
                return col
    return None


# ── benchmark matcher ──────────────────────────────────────────────────────────

def get_benchmark_context(
    df: pd.DataFrame,
    skill_key: str,
    question: str,
    benchmarks: dict,
) -> str:
    """
    Return benchmark interpretation strings for the query result.

    Examines the DataFrame and skill context to produce human-readable
    domain insights like "NPS of 64 is excellent (industry avg: 45)".

    Args:
        df:         The query result DataFrame.
        skill_key:  The active skill/capability ID (e.g., "nps", "awareness").
        question:   The original user question (for intent detection).
        benchmarks: The BENCHMARKS dict from project config.

    Returns:
        A pipe-separated string of benchmark observations, or "" if none apply.
    """
    if df is None or df.empty or not benchmarks:
        return ""

    contexts: list[str] = []

    # ── NPS benchmarks ─────────────────────────────────────────────────────
    if skill_key == "nps":
        nps_col = _has_col(df, "nps")
        if nps_col and pd.api.types.is_numeric_dtype(df[nps_col]) and len(df) >= 1:
            nps_val = float(df[nps_col].iloc[0])
            ind_avg = benchmarks.get("nps_industry_avg", 45)
            excellent = benchmarks.get("nps_excellent", 70)
            good = benchmarks.get("nps_good", 50)

            if nps_val >= excellent:
                contexts.append(
                    f"NPS of {nps_val:.0f} is excellent (industry avg: {ind_avg})"
                )
            elif nps_val >= good:
                contexts.append(
                    f"NPS of {nps_val:.0f} is good (above industry avg of {ind_avg})"
                )
            else:
                contexts.append(
                    f"NPS of {nps_val:.0f} is below industry good threshold of {good}"
                )

            # If multiple brands, add category-level insight
            if len(df) > 1:
                all_above = all(
                    float(row[nps_col]) >= ind_avg
                    for _, row in df.iterrows()
                    if pd.notna(row[nps_col])
                )
                if all_above:
                    contexts.append(
                        f"All {len(df)} brands above industry avg of {ind_avg} — strong category"
                    )

    # ── Awareness benchmarks ───────────────────────────────────────────────
    if skill_key == "awareness":
        pct_col = _has_col(df, "pct", "percent", "share")
        stage_col = _has_col(df, "stage")
        q_lower = question.lower()

        if pct_col and pd.api.types.is_numeric_dtype(df[pct_col]) and len(df) >= 1:
            val = float(df[pct_col].iloc[0])

            # Determine which awareness type we're looking at
            is_tom = (
                (stage_col and any(
                    "tom" in str(row).lower()
                    for row in df[stage_col].unique()
                ))
                or "tom" in q_lower
                or "top of mind" in q_lower
            )
            is_spont = (
                (stage_col and any(
                    "spont" in str(row).lower()
                    for row in df[stage_col].unique()
                ))
                or "spontaneous" in q_lower
                or "spont" in q_lower
            )

            if is_tom:
                tom_dom = benchmarks.get("tom_dominant", 30)
                tom_str = benchmarks.get("tom_strong", 20)
                if val >= tom_dom:
                    contexts.append(
                        f"TOM of {val:.1f}% indicates dominant mind share"
                    )
                elif val >= tom_str:
                    contexts.append(
                        f"TOM of {val:.1f}% is strong for a multi-brand category"
                    )
                else:
                    contexts.append(
                        f"TOM of {val:.1f}% is moderate — below strong threshold of {tom_str}%"
                    )
            elif is_spont:
                spont_str = benchmarks.get("spont_strong", 50)
                if val >= spont_str:
                    contexts.append(
                        f"Spontaneous awareness of {val:.1f}% is strong — well-established brand"
                    )
                else:
                    contexts.append(
                        f"Spontaneous awareness at {val:.1f}% (strong threshold: {spont_str}%)"
                    )

        # Funnel analysis: if multiple stages exist
        if stage_col and len(df) > 1:
            stages = {str(row[stage_col]).upper(): float(row[pct_col])
                      for _, row in df.iterrows()
                      if pct_col and pd.notna(row.get(pct_col))}
            tom_v = stages.get("TOM", 0)
            spont_v = stages.get("SPONT", 0)
            total_v = stages.get("AIDED", stages.get("TOTAL", 0))

            if tom_v > 0 and spont_v > 0:
                ratio = spont_v / tom_v
                contexts.append(
                    f"TOM→SPONT conversion ratio: {ratio:.1f}x"
                )
            if total_v > 0:
                total_bench = benchmarks.get("total_awareness_good", 70)
                if total_v >= total_bench:
                    contexts.append(
                        f"Total awareness at {total_v:.1f}% — mainstream brand"
                    )

    # ── Ownership benchmarks ───────────────────────────────────────────────
    if skill_key == "ownership":
        pct_col = _has_col(df, "pct", "percent", "penetration", "share")
        if pct_col and pd.api.types.is_numeric_dtype(df[pct_col]) and len(df) >= 1:
            val = float(df[pct_col].iloc[0])
            high = benchmarks.get("penetration_high", 50)
            niche = benchmarks.get("penetration_niche", 10)

            if val >= high:
                contexts.append(
                    f"Penetration of {val:.1f}% — near-universal ownership category"
                )
            elif val <= niche:
                contexts.append(
                    f"Penetration of {val:.1f}% — niche/emerging category"
                )

    # ── Sample size warnings (apply to ALL skills) ─────────────────────────
    count_col = _has_col(df, "count", "total", "raters", "base")
    if count_col and pd.api.types.is_numeric_dtype(df[count_col]):
        min_reliable = benchmarks.get("sample_min_reliable", 50)
        low_warn = benchmarks.get("sample_low_warning", 30)
        for _, row in df.iterrows():
            n = row[count_col]
            if pd.notna(n):
                n = int(n)
                if n < low_warn:
                    contexts.append(
                        f"⚠️ Low sample size (n={n}) — interpret with caution"
                    )
                    break
                elif n < min_reliable:
                    contexts.append(
                        f"Note: Sample size (n={n}) is borderline — use as directional"
                    )
                    break

    return " | ".join(contexts) if contexts else ""


# ── enriched summary prompt builder ────────────────────────────────────────────

def build_enriched_prompt(
    question: str,
    df: pd.DataFrame,
    comparison_summary: str = "",
    benchmark_context: str = "",
    session_context: str = "",
    research_base: str = "",
    verbatims: list[dict] = None,
) -> str:
    """
    Build an enriched summary prompt that includes comparison, benchmark context, and qualitative verbatims.
    """
    preview = df.head(5).to_string(index=False) if df is not None else "(no data)"
    row_count = len(df) if df is not None else 0

    # Build context lines — only include non-empty ones
    context_lines: list[str] = []
    if research_base:
        context_lines.append(f"- Research Base Profile: {research_base}")
    if comparison_summary:
        context_lines.append(f"- Comparison: {comparison_summary}")
    if benchmark_context:
        context_lines.append(f"- Benchmarks: {benchmark_context}")
    if session_context:
        context_lines.append(f"- Session: {session_context}")
    
    if verbatims:
        verb_text = "\n".join([f"[{v['source']}]: {v['text'][:300]}" for v in verbatims[:8]])
        context_lines.append(f"- MANDATORY SOURCE ID LEDGER (Use these!):\n{verb_text}")

    context_block = "\n".join(context_lines) if context_lines else "(none)"

    prompt = (
        f"User asked: {question}\n\n"
        f"Result Table:\n{preview}\n\n"
        f"SOURCE ID LEDGER:\n{context_block}\n\n"
        f"TASK: Write a research summary.\n\n"
        f"STEP 1: INTERNAL AUDIT (MANDATORY)\n"
        f"Before writing the summary, you MUST perform an internal audit inside `<thought>` tags.\n"
        f"In your thought block, you MUST:\n"
        f"1. **Number Check**: List the specific numbers from the table you will include.\n"
        f"2. **Evidence Check**: Does the 'SOURCE ID LEDGER' above contain raw interview quotes (e.g. Doc_123)? If YES, you MUST cite them. If NO, skip citation.\n"
        f"3. **Logic Check**: Does the context ledger actually support the user's question?\n\n"
        f"STEP 2: RESEARCH SUMMARY\n"
        f"STRICT ARCHITECTURAL RULES:\n"
        f"1. **CONDITIONAL CITATIONS**: IF AND ONLY IF interview quotes are provided in the ledger, you MUST cite at least 3 unique Source IDs (e.g., [Doc_123]). If NO quotes are provided (quantitative query), DO NOT mention Source IDs or apologize for their absence.\n"
        f"2. **NUMERICAL FIDELITY**: You MUST mention at least 2 primary numbers from the Result Table.\n"
        f"3. **STRUCTURAL TEMPLATE**: \n"
        f"   - Sentence 1: Direct answer with numbers and segment labels.\n"
        f"   - Sentence 2-3: Data synthesis or consumer feedback (with citations if available).\n"
        f"   - Sentence 4: Competitive context or conclusion.\n"
        f"4. **ZERO BLUFFING**: If no Source IDs exist in the ledger, do not mention consumer 'sayings'. Only use table numbers."
    )


    return prompt
