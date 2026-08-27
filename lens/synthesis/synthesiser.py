"""
LENS Synthesis Engine  Skills-Based, Token-Efficient, Multi-Backend with Fallback
===================================================================================
Architecture:
  - Skills:     Concise, focused system prompts loaded from /skills/*.md
  - Evidence:   Compressed tabular format (NOT raw JSON)  ~300 tokens per tool
  - Model stack: OpenRouter deepseek/deepseek-v4-pro
"""

import os
import json
import re
import pathlib
from openai import OpenAI

def rprint(*args, **kwargs):
    import re
    text = " ".join(str(a) for a in args)
    text = re.sub(r"\[/?[a-zA-Z_ ]*\]", "", text)
    print(text)

#  Skills directory 
SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"

# ── Skill selection ──────────────────────────────────────────────────────────
SKILL_PRIORITY = [
    (
        "demographic_segment",
        [
            "age", "age cohort", "age group", "25-35", "36-50", "cohort",
            "city", "region", "gender", "male", "female", "sec class",
            "demographic", "who buys", "geographic", "zone",
            "north", "south", "east", "west", "urban", "rural",
            "ownership", "by segment",
        ],
    ),
    (
        "attribute_driver",
        [
            "attribute", "feature", "most important feature", "what matters",
            "driver", "importance ranking", "rank attribute",
            "pain point", "gap", "after sales", "performance vs",
        ],
    ),
    (
        "brand_health",
        [
            "brand comparison", "compare brand", "brand vs brand",
            "nps", "brand nps", "brand score", "brand rank",
            "which brand is better", "best brand", "brand leader",
            "promoter", "detractor",
        ],
    ),
]


def load_skill(skill_id: str) -> str:
    """Load a skill prompt from the skills directory."""
    skill_file = SKILLS_DIR / f"{skill_id}.md"
    if skill_file.exists():
        return skill_file.read_text(encoding="utf-8")
    fallback = SKILLS_DIR / "general_insight.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    return "You are a market research analyst. Return valid JSON with an 'answer' field."


def select_skill(query: str) -> str:
    """Pick the most relevant skill. Checked in priority order — first match wins."""
    q = query.lower()
    for skill_id, keywords in SKILL_PRIORITY:
        if any(kw in q for kw in keywords):
            rprint(f"Skill selected: {skill_id} (matched keywords in query)")
            return skill_id
    rprint("Skill selected: general_insight (default)")
    return "general_insight"


#  Evidence Compression 
def _fmt_brand_nps(r: dict) -> str:
    """Compress brand_nps_comparison into a concise table."""
    brands = r.get("brands", [])
    if not brands:
        return ""
    leader = r.get("leader", {})
    laggard = r.get("laggard", {})
    lines = [
        f"BRAND NPS DATA ({r.get('category','')}, n={r.get('total_responses',0):,})",
        f"Leader: {leader.get('brand','?')} NPS={leader.get('nps', leader.get('nps_proxy','?'))} | Laggard: {laggard.get('brand','?')} NPS={laggard.get('nps', laggard.get('nps_proxy','?'))} | Gap: {r.get('gap_leader_to_laggard','?')} pts",
        "",
        "Brand            | NPS   | Mean  | Promoters% | Detractors% | n",
        "-----------------|-------|-------|------------|-------------|----",
    ]
    for b in brands[:12]:  # cap at 12 brands
        nps_val = b.get('nps', b.get('nps_proxy', '?'))
        promoters = b.get('t2b_pct', b.get('promoters_pct', '?'))
        detractors = b.get('b6b_pct', b.get('detractors_pct', '?'))
        lines.append(
            f"{b['brand']:<17}| {nps_val:>5} | {b.get('mean_score','?'):>5} | "
            f"{promoters:>10} | {detractors:>11} | {b.get('n','?')}"
        )
    return "\n".join(lines)


def _fmt_satisfaction_city(r: dict) -> str:
    """Compress satisfaction_by_city into a concise table."""
    t2b_overall = r.get('overall_t2b_pct', r.get('overall_top2box_pct', '?'))
    lines = [
        f"SATISFACTION BY CITY ({r.get('category','')}, overall mean={r.get('overall_mean','?')}, n={r.get('overall_n',0):,})",
        f"Top-2-Box (9-10): {t2b_overall}% | City range: {r.get('city_range','?')} pts",
        "",
        "City             | Mean  | T2B%  | vs Avg | n",
        "-----------------|-------|-------|--------|----",
    ]
    for c in (r.get("cities") or [])[:12]:
        t2b = c.get('t2b_pct', c.get('top2box_pct', '?'))
        lines.append(
            f"{c.get('city','?'):<17}| {c.get('mean_satisfaction','?'):>5} | {t2b:>5} | "
            f"{c.get('vs_average','?'):>6} | {c.get('n','?')}"
        )
    gender = r.get("gender_breakdown", {})
    if gender:
        lines.append("")
        for g, v in gender.items():
            t2b_g = v.get('t2b_pct', v.get('top2box_pct', '?'))
            lines.append(f"Gender {g}: mean={v.get('mean','?')} T2B={t2b_g}% n={v.get('n','?')}")
    return "\n".join(lines)


def _fmt_attribute_importance(r: dict) -> str:
    """Compress attribute_importance_ranking into a concise table."""
    top = r.get("top_attributes", [])
    if not top:
        return ""
    bot = r.get("bottom_attribute", {})
    lines = [
        f"ATTRIBUTE IMPORTANCE ({r.get('category','')}, scale 1-7, n~{top[0].get('n',0) if top else 0})",
        f"Gap top-bottom: {r.get('gap_top_bottom','?')}",
        "",
        "Rank | Attribute                              | Mean | %Max | T2B% | Bucket",
        "-----|----------------------------------------|------|------|------|--------",
    ]
    for a in top[:10]:
        attr_short = a.get("attribute", "")[:38]
        t2b = a.get('t2b_pct', a.get('top2box_pct', '?'))
        lines.append(
            f"{a.get('rank','?'):>4} | {attr_short:<38} | {a.get('mean_importance','?'):>4} | {a.get('pct_of_max','?'):>4} | "
            f"{t2b:>4} | {a.get('feature_bucket','?')}"
        )
    if bot:
        lines.append(f"\nLowest: {bot.get('attribute','?')} mean={bot.get('mean_importance','?')}")
    return "\n".join(lines)


def _fmt_is_gap(r: dict) -> str:
    """Compress importance_satisfaction_gap."""
    pain = r.get("critical_pain_points", [])
    if not pain:
        return f"IS GAP: Overall sat={r.get('overall_satisfaction_raw','?')}/10 (scaled 1-7: {r.get('overall_satisfaction_scaled_1_7','?')}). No critical pain points found."
    lines = [
        f"IMPORTANCE-SATISFACTION GAP ({r.get('category','')})",
        f"Overall satisfaction: {r.get('overall_satisfaction_raw','?')}/10 (scaled 1-7: {r.get('overall_satisfaction_scaled_1_7','?')})",
        "",
        "PAIN POINTS (high importance, satisfaction gap > 0.5):",
        "Attribute                              | Importance | IS Gap | Bucket",
        "---------------------------------------|------------|--------|--------",
    ]
    for p in pain[:6]:
        lines.append(
            f"{p['attribute'][:38]:<38} | {p['mean_importance']:>10} | {p['is_gap']:>6} | {p['feature_bucket']}"
        )
    return "\n".join(lines)


def _fmt_demographic_crosstab(r: dict) -> str:
    """Compress demographic_crosstab."""
    segs = r.get("segments", [])
    if not segs:
        return ""
    lines = [
        f"DEMOGRAPHIC CROSSTAB: {r.get('dimension','')}  {r.get('metric','')} ({r.get('category','')})",
        f"Best segment: {r.get('best_segment','')} | Worst: {r.get('worst_segment','')} | Range: {r.get('range','')} pts",
        "",
        "Segment          | Value | n",
        "-----------------|-------|----",
    ]
    for s in segs[:10]:
        cols = list(s.values())
        lines.append(f"{str(cols[0]):<17}| {str(cols[1]):>5} | {str(cols[-1])}")
    return "\n".join(lines)


def _fmt_feature_buckets(r: dict) -> str:
    """Compress feature_bucket_summary."""
    buckets = r.get("buckets", [])
    if not buckets:
        return ""
    lines = [
        f"FEATURE CATEGORY SUMMARY ({r.get('category','')})",
        "",
        "Category            | Mean | %Max | vs Overall | n_attrs",
        "--------------------|------|------|------------|--------",
    ]
    for b in buckets:
        lines.append(
            f"{b['bucket']:<20}| {b['mean_importance']:>4} | {b['pct_of_max']:>4} | "
            f"{b['vs_overall']:>10} | {b['n_attributes']}"
        )
    return "\n".join(lines)


def _fmt_category_overview(r: dict) -> str:
    """Compress category_overview."""
    gd = r.get("gender_breakdown", [])
    gender_str = " | ".join([f"{g['value']}: {g['pct']}% (n={g['count']})" for g in gd]) if gd else "N/A"
    return (
        f"CATEGORY OVERVIEW: {r.get('category','')} | "
        f"Total respondents: {r.get('total_respondents',0):,} | "
        f"Gender: {gender_str}"
    )


TOOL_FORMATTERS = {
    "brand_nps_comparison": _fmt_brand_nps,
    "satisfaction_by_city": _fmt_satisfaction_city,
    "attribute_importance_ranking": _fmt_attribute_importance,
    "importance_satisfaction_gap": _fmt_is_gap,
    "demographic_crosstab": _fmt_demographic_crosstab,
    "feature_bucket_summary": _fmt_feature_buckets,
    "category_overview": _fmt_category_overview,
}


def compress_evidence(analytics_results: list, qual_passages: list) -> str:
    """
    Converts raw analytics tool dicts into token-efficient tabular text.
    Drops full JSON blobs. Target: <3,000 tokens for the evidence block.
    """
    parts = []

    for item in analytics_results:
        tool = item.get("tool", "")
        result = item.get("result", {})
        if result.get("error"):
            continue
        formatter = TOOL_FORMATTERS.get(tool)
        if formatter:
            try:
                formatted = formatter(result)
                if formatted:
                    parts.append(formatted)
            except Exception as e:
                rprint(f"[yellow]Evidence compression warning ({tool}): {e}[/yellow]")

    if qual_passages:
        parts.append("\nQUALITATIVE EVIDENCE (Interview passages):")
        for p in qual_passages[:5]:
            parts.append(
                f"[{p.get('doc_id','?')}: {p.get('section_title','section')}] "
                f"(relevance={p.get('relevance_score','?')})\n"
                f"{p.get('content','')[:300]}"
            )

    if not parts:
        return "No evidence available for this query."

    return "\n\n".join(parts)


#  JSON Extraction 
def _extract_json(raw: str) -> dict:
    """Robustly extract JSON from LLM output, capturing think blocks."""
    if not raw:
        return {}
    
    # 1. Capture <think> content if present
    thinking = ""
    think_match = re.search(r"<think>(.*?)</think>", raw, re.DOTALL)
    if think_match:
        thinking = think_match.group(1).strip()
    
    # 2. Strip <think> blocks for JSON parsing
    clean_raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    
    # 3. Strip markdown fences
    clean_raw = re.sub(r"```(?:json)?", "", clean_raw).replace("```", "").strip()
    
    # 4. Parse JSON
    result = {}
    try:
        result = json.loads(clean_raw)
    except json.JSONDecodeError:
        # Extract first JSON object
        match = re.search(r"\{.*\}", clean_raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(0))
            except json.JSONDecodeError:
                result = {"answer": clean_raw[:1500]}
        else:
            result = {"answer": clean_raw[:1500]}

    # Ensure thinking is preserved in the final dict
    if thinking and not result.get("thinking"):
        result["thinking"] = thinking
        
    return result


def _ensure_defaults(result: dict) -> dict:
    """Make sure all expected keys exist and are normalized."""
    for key in ["themes", "unmet_needs", "qual_citations", "quant_citations",
                "key_findings", "follow_up_questions"]:
        if key not in result or not isinstance(result[key], list):
            result[key] = []
    
    # Normalize themes (ensure list of dicts)
    normalized_themes = []
    for t in result["themes"]:
        if isinstance(t, dict):
            normalized_themes.append(t)
        elif isinstance(t, str):
            normalized_themes.append({"name": "Theme", "summary": t, "sentiment": "neutral"})
    result["themes"] = normalized_themes

    # Normalize qual_citations (ensure list of dicts)
    normalized_qual = []
    for c in result["qual_citations"]:
        if isinstance(c, dict):
            normalized_qual.append(c)
        elif isinstance(c, str):
            normalized_qual.append({"doc_id": "EVIDENCE", "section": "Source", "quote": c})
    result["qual_citations"] = normalized_qual

    # Normalize quant_citations (ensure list of dicts)
    normalized_quant = []
    for c in result["quant_citations"]:
        if isinstance(c, dict):
            normalized_quant.append(c)
        elif isinstance(c, str):
            normalized_quant.append({"variable": "DATA", "statistic": "Value", "value": c})
    result["quant_citations"] = normalized_quant

    if "chart_suggestion" not in result or not isinstance(result["chart_suggestion"], dict):
        result["chart_suggestion"] = {"type": "none", "data": []}
    if not result.get("answer"):
        result["answer"] = "No answer generated."
    if "thinking" not in result:
        result["thinking"] = ""
    return result


#  Backend Caller 
def _call_openrouter(system: str, user: str, model: str, api_key: str) -> str:
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        temperature=0.1,
        max_tokens=3072,
    )
    content = resp.choices[0].message.content or ""
    # Capture native reasoning trace if available
    reasoning = getattr(resp.choices[0].message, "reasoning", None)
    if reasoning:
        return f"<think>\n{reasoning}\n</think>\n{content}"
    return content


#  Public Synthesise Function 
def synthesise(
    query: str,
    qual_passages: list,
    quant_results: dict,
    chat_history: list = None,
    model_config: dict = None,
    qual_findings: list = None,
) -> dict:
    """
    Args:
        qual_findings: List of manipulated/grouped qualitative findings from Qual Engine.
    """
    OR_MODEL = "deepseek/deepseek-v4-pro"
    
    if model_config is None:
        model_config = {
            "backend": "openrouter",
            "model": OR_MODEL,
            "api_key": None
        }

    analytics_results = quant_results.get("analytics_tools", [])

    # 1. Compress evidence
    evidence_block = compress_evidence(analytics_results, qual_passages)
    if not evidence_block:
        evidence_block = "NO QUANTITATIVE EVIDENCE AVAILABLE FOR THIS SPECIFIC QUERY."

    # 2. Select skill dynamically based on evidence presence
    skill_id = select_skill(query)
    if skill_id == "general_insight" and not analytics_results:
        skill_id = "qualitative_insight"
        
    system_prompt = load_skill(skill_id)
    rprint(f"[blue]Skill:[/blue] {skill_id}")
    
    #  Enriched Qualitative Findings 
    qual_finding_block = ""
    if qual_findings:
        qual_finding_block = "QUALITATIVE THEMATIC FINDINGS (from pattern analysis):\n"
        for f in qual_findings:
            obs = f.get('observation') or f.get('finding') or ""
            quote = f.get('key_quote') or f.get('quote') or ""
            sent = f.get('sentiment') or ""
            qual_finding_block += f"- [{sent}] {obs}\n  Quote: \"{quote}\"\n"
        qual_finding_block += "\n"

    # 3. Optional chat history
    history_block = ""
    if chat_history:
        recent = chat_history[-3:]
        lines = []
        for turn in recent:
            content = turn.get("content", "")
            if isinstance(content, dict):
                content = content.get("answer", "")[:200]
            else:
                content = str(content)[:200]
            lines.append(f"{turn['role'].upper()}: {content}")
        if lines:
            history_block = "RECENT CONTEXT:\n" + "\n".join(lines) + "\n\n"

    # 4. Build user message
    qual_section = f"QUALITATIVE PATTERNS:\n{qual_finding_block}\n" if qual_finding_block else ""
    user_message = (
        f"{history_block}"
        f"{qual_section}"
        f"QUANTITATIVE EVIDENCE:\n{evidence_block}\n\n"
        f"QUESTION: {query}\n\n"
        f"Populate the 'themes' array with 2-4 consumer themes drawn from the qualitative passages above. "
        f"Return a valid JSON object following the schema exactly."
    )

    model_name = model_config.get("model", OR_MODEL)
    api_key = model_config.get("api_key") or None

    rprint(f"[blue]Synthesis:[/blue] OpenRouter/{model_name} | evidence={len(evidence_block)} chars")

    try:
        raw = _call_openrouter(system_prompt, user_message, model_name, api_key)
    except Exception as e:
        rprint(f"[red]OpenRouter synthesis failed: {e}[/red]")
        return _ensure_defaults({
            "answer": f"OpenRouter synthesis failed: {e}",
            "confidence": "LOW",
            "confidence_reason": "API call failed.",
            "key_findings": [f"Error: {e}"]
        })

    rprint(f"[green]Synthesis complete using OpenRouter[/green]")
    result = _extract_json(raw or "")
    return _ensure_defaults(result)
