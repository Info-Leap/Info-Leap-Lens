"""
Schema Generator — reads DG + AI Prompt docx files → auto-generates:
  - extraction_schema.json (Layer 1 + Layer 2)
  - master_prompt.txt
  - report_structure.json

Usage:
    python schema_generator.py --project karat-coindcx   # re-generate existing
    python schema_generator.py --project new-brand        # generate from scratch
    python schema_generator.py --project new-brand --dg source_docs/DG.docx --prompt source_docs/AI_Prompt.docx
"""
import argparse, hashlib, json, os, re, sys, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Windows' console defaults to cp1252, which can't encode most emoji — a bare print() with one
# crashes the whole CLI run with a misleading UnicodeEncodeError (found live in project_extractor.py
# this session). errors="replace" makes that class of bug non-fatal here too.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

from dotenv import load_dotenv
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(str(_ENV_FILE), override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import call_llm_safe, call_llm, LLMCallError  # noqa: E402  (after sys.path patch)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _get_client():
    """Kept for call-site compatibility — llm_client manages its own client internally now."""
    return None


def _call_llm(client, messages, max_tokens=4000, temp=0.2):
    return call_llm_safe(messages, max_tokens=max_tokens, temp=temp, json_mode=True)


def _read_docx(path: Path) -> str:
    """Extract text from a .docx file."""
    try:
        from docx import Document
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        return f"[Could not read {path.name}: {e}]"


def _extract_json_from_response(text: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown fences and reasoning blocks."""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"(\{[\s\S]*\})", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _format_anchors_line(anchors: dict | None, indent: str = "  ") -> str:
    """Render field_def["anchors"] (value -> [{"quote","doc_id"}, ...]) as one line the model
    can judge against — see generate_field_rubrics() for how these get populated. Shared by both
    the initial master_prompt generation (Step 3) and _resync_master_prompt_from_schema so an
    anchor set added by one path always shows up regardless of which path last wrote the prompt."""
    if not anchors:
        return ""
    parts = []
    for val, examples in anchors.items():
        if not examples:
            continue
        parts.append(f'{val} = "{examples[0].get("quote", "")}"')
    if not parts:
        return ""
    return f"\n{indent}Grounded examples (judge against these, not vibes): " + " | ".join(parts)


def _render_dim_json_line(d: dict) -> str:
    """Render one layer2_dimension as a line (or nested block) of the master_prompt JSON
    template. object/array-of-objects dims with declared sub_fields render as a real nested JSON
    structure with one placeholder per sub-question, instead of a single opaque comment string —
    this is what keeps every interview filling the SAME sub-key names for a bundled field."""
    fname = d.get("field_name", "field")
    sub_fields = d.get("sub_fields")
    if sub_fields and d.get("type") in ("object", "array"):
        inner = []
        for i, sf in enumerate(sub_fields):
            comma = "," if i < len(sub_fields) - 1 else ""
            ph = "ENUM — see values above" if sf.get("enum_values") else \
                 ("EXACT QUOTE" if sf.get("verbatim_field") else "null or string or number")
            inner.append(f'      "{sf.get("name","field")}": "{ph}"{comma}  // {sf.get("description","")}')
        inner_block = "\n".join(inner)
        if d.get("type") == "array":
            return f'  "{fname}": [\n    {{\n{inner_block}\n    }}\n  ],  // {d.get("description","")} — one entry per instance found'
        return f'  "{fname}": {{\n{inner_block}\n  }},'
    placeholder = "ENUM — see values above" if d.get("enum_values") else "null or string or array"
    return f'  "{fname}": {placeholder},  // {d.get("description","")}'


# ── Universal Layer 1 schema (identical for all projects) ─────────────────────
_LAYER1 = {
    "description": "Universal fields present in all project matrices",
    "fields": {
        "doc_id": {"type": "string", "required": True},
        "filename": {"type": "string", "required": True},
        "word_count": {"type": "integer"},
        "respondent": {
            "city": {"type": "string"},
            "segment": {"type": "string"},
            "gender": {"type": "string"},
            "age_band": {"type": "string"},
            "occupation": {"type": "string"}
        },
        "pain_points": {
            "type": "array",
            "items": {
                "severity": {
                    "type": "enum",
                    "values": ["critical", "high", "medium", "low"],
                    "scoring_rules": {
                        "critical": "Explicit deal-breaker, 3+ reinforcing statements",
                        "high": "Strong hesitation requiring resolution, 2+ statements",
                        "medium": "Conditional acceptance, 1-2 mentions",
                        "low": "Single passive mention"
                    }
                },
                "issue_description": {"type": "string"},
                "verbatim_quote": {"type": "string", "rule": "exact copy — never paraphrase"},
                "product_area": {"type": "string"},
                "dg_section": {"type": "string"}
            }
        },
        "nps_signal": {
            "type": "enum", "values": ["promoter", "passive", "detractor", "unclear"]
        },
        "emotional_resolution": {
            "type": "enum", "values": ["positive", "negative", "neutral"]
        },
        "narrative_tags": {
            "type": "array", "items": {"type": "string"},
            "note": "Reference themes with STRONG/MIXED evidence + any emerging themes"
        },
        "most_powerful_verbatim": {
            "type": "string",
            "rule": "exact copy — never paraphrase",
            "note": "Single most insight-carrying quote — exact copy from transcript"
        },
        "all_passages": {
            "type": "array",
            "items": {
                "content": {"type": "string", "rule": "30+ words verbatim from transcript"},
                "sentiment": {"type": "enum", "values": ["positive", "negative", "neutral", "ambivalent"]},
                "topic": {"type": "string"},
                "pain_point": {"type": "boolean"},
                "decision_signal": {"type": "boolean"},
                "section": {"type": "string"},
                "paragraph_index": {"type": "integer"}
            }
        }
    }
}


def _fallback_ui_config(study_type, segment_key, layer2_fields, reference_themes, project_name):
    """Rule-based ui_config when LLM fails — detects section types from field names."""
    int_fields = [k for k, v in layer2_fields.items() if v.get("type") == "integer"]
    enum_fields = [k for k, v in layer2_fields.items() if v.get("type") == "enum"]
    verbatim_fields = [k for k, v in layer2_fields.items()
                       if v.get("verbatim_field") or "verbatim" in k or v.get("rule", "")]
    array_fields = [k for k, v in layer2_fields.items() if v.get("type") == "array"]

    all_field_names = " ".join(layer2_fields.keys())
    first_int = int_fields[0] if int_fields else None

    kpi_fields = []
    for f in int_fields[:5]:
        kpi_fields.append({"path": f, "label": f.replace("_", " ").title(), "format": "/10", "color": "green"})

    signal_scores = [
        {"name": "Pain Level", "type": "pain_severity", "higher_is_better": False},
        {"name": "Data Quality", "type": "quality_avg", "higher_is_better": True},
    ]
    if first_int:
        signal_scores.insert(0, {
            "name": first_int.replace("_", " ").title(),
            "type": "field_avg_scaled",
            "field": first_int,
            "scale": 10,
            "higher_is_better": True
        })
    else:
        signal_scores.insert(0, {
            "name": "NPS Score", "type": "nps_score", "higher_is_better": True
        })

    tab4_label = "Concept Analysis" if study_type == "concept_testing" else "Analysis"

    tab1_distributions = [
        {"path": f, "label": f.replace("_", " ").title()} for f in enum_fields[:4]
    ]
    tab1_verbatim_flds = [
        {"path": f, "label": f.replace("_", " ").title()} for f in verbatim_fields[:3]
    ]

    filter_fields = [{"key": f"respondent.{segment_key}", "label": segment_key.replace("_", " ").title()}]
    if segment_key != "city":
        filter_fields.append({"key": "respondent.city", "label": "City"})

    # Mark these as AI-suggested since they are generated by rules, not explicit prompt requests
    for kpi in kpi_fields: kpi["ai_suggested"] = True
    for sc in signal_scores: sc["ai_suggested"] = True

    # Add strong enum candidates as filters
    for f in enum_fields:
        if any(tok in f for tok in ("route_shown", "preferred_route", "life_stage", "phase", "archetype")):
            filter_fields.append({"key": f, "label": f.replace("_", " ").title()})
            if len(filter_fields) >= 4:
                break

    # ── Detect tab4 sections from field names ─────────────────────────────────
    tab4_sections = []

    # route_detail_grid — two concept routes compared
    if "route1_evaluation" in all_field_names or "route2_evaluation" in all_field_names:
        r1_attrs = [
            {"path": k, "label": k.split(".")[-1].replace("_", " ").title()}
            for k in layer2_fields if k.startswith("route1_evaluation")
        ]
        r2_attrs = [
            {"path": k, "label": k.split(".")[-1].replace("_", " ").title()}
            for k in layer2_fields if k.startswith("route2_evaluation")
        ]
        score_flds = [
            {"path": k, "label": k.replace("_", " ").title()}
            for k in layer2_fields if "appeal_score" in k or "comprehension_score" in k
        ]
        if "preferred_route" in layer2_fields:
            tab4_sections.append({
                "title": "Route Preference",
                "type": "distribution",
                "fields": [{"path": "preferred_route", "label": "Preferred Route"}]
            })
        tab4_sections.append({
            "title": "Route Detail",
            "type": "route_detail_grid",
            "route1_label": "Route 1",
            "route2_label": "Route 2",
            "route1_attributes": r1_attrs,
            "route2_attributes": r2_attrs,
            "score_fields": score_flds
        })

    # taglines — tagline / headline preference
    if "tagline_reaction" in all_field_names:
        pref_field = next((k for k in layer2_fields if "tagline_reaction" in k and "preferred" in k), None)
        verb_field = next((k for k in layer2_fields if "tagline_reaction" in k and "verbatim" in k), None)
        tab4_sections.append({
            "title": "Taglines",
            "type": "taglines",
            "preference_field": pref_field or "tagline_reaction.preferred_tagline",
            "verbatim_field": verb_field or "tagline_reaction.tagline_verbatim"
        })

    # portfolio_context — investment / portfolio behavior
    if "portfolio_behavior" in all_field_names or "gold_behavior" in all_field_names:
        alloc_fields = [
            {"path": k, "label": k.split(".")[-1].replace("_", " ").title()}
            for k in layer2_fields if "allocation" in k
        ]
        tab4_sections.append({
            "title": "Portfolio Context",
            "type": "portfolio_context",
            "gold_role_field": next((k for k in layer2_fields if "gold_role" in k), None),
            "sgb_awareness_field": next((k for k in layer2_fields if "sgb_awareness" in k), None),
            "platforms_field": next((k for k in layer2_fields if "platform" in k), None),
            "allocation_fields": alloc_fields
        })

    # adoption_detail — structured drivers and barriers
    if "adoption" in all_field_names and ("drivers" in all_field_names or "barriers" in all_field_names):
        tab4_sections.append({
            "title": "Adoption Drivers & Barriers",
            "type": "adoption_detail",
            "drivers_field": next((k for k in layer2_fields if "adoption" in k and "driver" in k), None),
            "barriers_field": next((k for k in layer2_fields if "adoption" in k and "barrier" in k), None),
            "trial_amount_field": next((k for k in layer2_fields if "trial_amount" in k), None)
        })

    # benchmark — product vs alternatives
    if "benchmark_comparisons" in all_field_names:
        bench_field = next((k for k in layer2_fields if "benchmark_comparisons" in k), "benchmark_comparisons")
        tab4_sections.append({
            "title": "Benchmark Comparisons",
            "type": "benchmark",
            "field": bench_field,
            "benchmark_key": "benchmark",
            "verdict_key": "verdict"
        })

    # claims — key claim reactions array
    if "key_claim_reactions" in all_field_names:
        claims_field = next((k for k in layer2_fields if "key_claim_reactions" in k), "key_claim_reactions")
        tab4_sections.append({
            "title": "Claim Reactions",
            "type": "claims",
            "claims_field": claims_field,
            "claims_list": []
        })

    # score_grid — appeal / comprehension scores
    score_fields_found = [
        {"path": k, "label": k.replace("_", " ").title()}
        for k in int_fields if "score" in k or "appeal" in k or "comprehension" in k
    ]
    if score_fields_found and not any(s.get("type") == "score_grid" for s in tab4_sections):
        tab4_sections.append({
            "title": "Evaluation Scores",
            "type": "score_grid",
            "fields": score_fields_found[:4]
        })

    # verbatim_list — top verbatim fields
    for vf in verbatim_fields[:2]:
        tab4_sections.append({
            "title": vf.replace("_", " ").title(),
            "type": "verbatim_list",
            "field": vf,
            "max_items": 10
        })

    # Detect tab5 trust fields
    trust_field = next((k for k in layer2_fields if any(t in k for t in ("trust", "risk", "anxiety"))), None)
    trust_verb = next((k for k in layer2_fields if trust_field and trust_field.split("_")[0] in k and "verbatim" in k), None)
    trust_builders = next((k for k in array_fields if "trust_builder" in k or "trust_signal" in k), None)
    tab5_enums = [
        {"path": k, "label": k.replace("_", " ").title()}
        for k in enum_fields if trust_field and k != trust_field
    ][:3]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": "",  # filled by caller
        "segment_key": segment_key,
        "entity_label": "Segment" if segment_key == "segment" else "Brand",
        "study_context": f"{project_name} — {study_type}",
        "ai_insight_keys": [
            "WHAT THIS SEGMENT WANTS",
            "PAIN POINTS",
            "KEY TRUST SIGNALS",
            "STRATEGIC SIGNAL"
        ],
        "filter_fields": filter_fields,
        "kpi_fields": kpi_fields,
        "signal_scores": signal_scores,
        "tab1_distributions": tab1_distributions,
        "tab1_verbatim_fields": tab1_verbatim_flds,
        "tab3_reference_tags": reference_themes[:25],
        "tab4_label": tab4_label,
        "tab4_sections": tab4_sections,
        "tab5_trust_field": trust_field,
        "tab5_trust_verbatim": trust_verb,
        "tab5_trust_builders": trust_builders,
        "tab5_distributions": tab5_enums
    }


# ── Field-candidate scan (ground truth for the tabs-shaped ui_config prompt) ────────────────
# Same heuristics as concept_testing_renderer.py's _auto_chart_candidates, reimplemented here
# rather than imported — skills/ must not depend on views/, and this scan needs to run at
# schema-generation time, before any renderer is involved. Purpose: give the LLM the REAL
# distinct values / coverage per field instead of asking it to guess "important" fields from
# schema descriptions alone, so every chartable field gets placed somewhere (uncapped), not
# just the ones that sound important in prose.

def _detect_populated_fields(matrices: list[dict]) -> set[str]:
    """Top-level + one-level-deep field paths that are populated in at least one matrix."""
    fields: set[str] = set()
    for m in matrices:
        for k, v in m.items():
            if k.startswith("_"):
                continue
            if v:
                fields.add(k)
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        if vv:
                            fields.add(f"{k}.{kk}")
    return fields


_KNOWN_ACRONYMS = {"nps", "sgb", "fiu", "fdmf", "coindcx", "t1", "kpi", "csat", "roi"}


def _humanize_field_path(path: str) -> str:
    def _word(w: str) -> str:
        return w.upper() if w.lower() in _KNOWN_ACRONYMS else w.title()
    return " — ".join(
        " ".join(_word(w) for w in p.replace("_", " ").split(" "))
        for p in path.split(".")
    )


def _get_path(obj, path: str):
    try:
        for k in path.split("."):
            obj = obj.get(k) if isinstance(obj, dict) else None
        return obj
    except Exception:
        return None


def _load_project_matrices(project_id: str) -> list[dict]:
    matrices_dir = _DATA_DIR / "projects" / project_id / "matrices"
    if not matrices_dir.exists():
        return []
    matrices = []
    for mf in matrices_dir.glob("*_matrix.json"):
        try:
            matrices.append(json.loads(mf.read_text(encoding="utf-8")))
        except Exception:
            continue
    return matrices


def scan_numeric_score_fields(project_id: str, min_respondents: int = 3) -> list[dict]:
    """Ground truth for which fields are genuinely numeric (avg-able scores like intent_score,
    comprehension_score, appeal_score) — the LLM previously had no way to distinguish these from
    categorical fields and guessed, live bug: used the categorical "emotional_resolution"
    (positive/negative/neutral/ambivalent) as a 0-10 score_distribution, which renders empty.
    Each entry: {path, min, max, avg, respondent_coverage}."""
    matrices = _load_project_matrices(project_id)
    if not matrices:
        return []

    numeric: list[dict] = []
    for path in _detect_populated_fields(matrices):
        if path.startswith("respondent."):
            continue
        vals = []
        for m in matrices:
            v = _get_path(m, path)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        if len(vals) < min_respondents:
            continue
        numeric.append({
            "path": path,
            "min": min(vals),
            "max": max(vals),
            "avg": round(sum(vals) / len(vals), 2),
            "respondent_coverage": len(vals),
        })
    numeric.sort(key=lambda c: c["respondent_coverage"], reverse=True)
    return numeric


def scan_chartable_fields(
    project_id: str, min_values: int = 2, max_values: int = 8, min_respondents: int = 3,
) -> list[dict]:
    """Scan every matrix in `project_id` and return ground-truth chartable fields: closed
    categorical (2-8 distinct values), not free text, not stringified list/dict drift, not bare
    numeric (scores/percentages are continuous — out of scope for category charts; see
    scan_numeric_score_fields for those). Each entry:
    {path, distinct_values, respondent_coverage, sample_counts}."""
    matrices = _load_project_matrices(project_id)
    if not matrices:
        return []

    from collections import Counter

    candidates: list[dict] = []
    for path in _detect_populated_fields(matrices):
        if path.startswith("respondent."):
            continue
        counts: Counter = Counter()
        for m in matrices:
            v = _get_path(m, path)
            if v is not None and str(v).strip() not in ("", "None", "not_mentioned", "unknown"):
                counts[str(v).strip()] += 1
        if not counts:
            continue
        n_resp = sum(counts.values())
        if n_resp < min_respondents or not (min_values <= len(counts) <= max_values):
            continue
        values = list(counts.keys())
        if any(len(v.split()) > 6 for v in values):
            continue
        if any(any(ch in v for ch in "[]{}") for v in values):
            continue
        def _is_num(v: str) -> bool:
            try:
                float(v); return True
            except ValueError:
                return False
        if all(_is_num(v) for v in values):
            continue
        # A "distribution" where every value is unique to one respondent isn't a distribution —
        # found live: portfolio_behavior.equity_percentage (each respondent's own raw percentage
        # string, e.g. "60-70", "25") passed the 2-8-distinct-values check but rendered as 5 bars
        # each at height 1, carrying zero signal. Require at least one value shared by >=2
        # respondents — real clustering, not a disguised free-text dump.
        if max(counts.values()) < 2:
            continue
        candidates.append({
            "path": path,
            "distinct_values": len(counts),
            "respondent_coverage": n_resp,
            "sample_counts": dict(counts.most_common()),
        })
    candidates.sort(key=lambda c: c["respondent_coverage"], reverse=True)
    return candidates


# ── "tabs"-shaped ui_config, schema-navigated via Pydantic ──────────────────────────────────
# Consumed by concept_testing_renderer.py's SECTION_RENDERERS engine (render_concept_testing
# auto-switches to it the moment ui_config.json has a "tabs" key — no routing changes needed).
# Config keys per section type below were extracted directly from every _sec_*() function's
# sec.get(...) calls in concept_testing_renderer.py, so the LLM is asked for exactly what the
# renderer actually reads — not a shape invented independently of the render side.

_KPI_VALUE_TYPES = Literal[
    "total_n", "avg_score", "count_eq", "count_contains", "count_high", "count_any", "r1_r2_split",
]


class LegendItem(BaseModel):
    label: str
    color: str = "blue"
    description: str = ""


class KpiSpec(BaseModel):
    label: str
    # Empty string is valid — required only for value_types that actually read a field.
    # "total_n" (just the respondent count) needs no field; the real bug this guards against:
    # the LLM inventing a plausible-looking-but-nonexistent field (e.g. "respondent.id") for a
    # KPI that just wants len(matrices), which silently renders "0/23 (0%)" instead of "23".
    field: str = ""
    value_type: _KPI_VALUE_TYPES = "avg_score"
    suffix: str = ""
    eq_val: str = ""
    contains_val: str = ""
    color: str = "blue"


class SectionHeaderSection(BaseModel):
    type: Literal["section_header"]
    title: str
    description: str = ""
    color: str = "blue"


class KpiRowSection(BaseModel):
    type: Literal["kpi_row"]
    kpis: list[KpiSpec] = Field(min_length=1)


class DistributionFieldSpec(BaseModel):
    """One field entry inside a multi_distribution section, or the whole body of a standalone
    distribution section — same keys _sec_distribution/_sec_multi_distribution both read."""
    field: str
    title: str = ""
    label: str = ""
    subtitle: str = ""
    caption: str = ""
    how_to_read: str = ""
    calc_note: str = ""
    chart: Literal["h_bar", "v_bar", "donut"] = "h_bar"
    color: str = "blue"
    limit: int = 20
    list_field: bool = False
    legend: list[LegendItem] = []
    # Sub-theme within the tab (e.g. within "Respondent Profiles": "Demographics" vs
    # "Investment Behavior & Risk") — lets a tab with many fields group and filter by theme
    # instead of dumping every field flat in one grid. Optional/backward-compatible: a config
    # with no groups (or all fields sharing one) renders exactly as before, no filter shown.
    group: str = ""


class DistributionSection(DistributionFieldSpec):
    type: Literal["distribution"]


class MultiDistributionSection(BaseModel):
    type: Literal["multi_distribution"]
    columns: int = 3
    fields: list[DistributionFieldSpec] = Field(min_length=1)


class ScoreDistributionSection(BaseModel):
    type: Literal["score_distribution"]
    field: str
    title: str = ""
    subtitle: str = ""
    color: str = "blue"
    min: float = 0
    max: float = 10
    how_to_read: str = ""
    calc_note: str = ""
    legend: list[LegendItem] = []
    bands: list = []


class DivergingBarSection(BaseModel):
    type: Literal["diverging_bar"]
    title: str = ""
    subtitle: str = ""
    caption: str = ""
    drivers_field: str
    barriers_field: str
    drivers_label: str = "Drivers"
    barriers_label: str = "Barriers"
    limit: int = 10


class SegmentCardGridSection(BaseModel):
    type: Literal["segment_card_grid"]
    segment_field: str = "respondent.segment"
    metrics: list[KpiSpec] = Field(min_length=1)


class SeparatorSection(BaseModel):
    type: Literal["separator"]


class AiInsightSection(BaseModel):
    type: Literal["ai_insight"]
    title: str = "AI Research Finding"
    section_id: str
    prompt: str
    regen_key: str = ""
    context_fields: list[str] = []


class VerbatimWallSection(BaseModel):
    type: Literal["verbatim_wall_section"]
    title: str = "Verbatim Evidence"
    topics: list[str] = []
    key_prefix: str
    pain_only: bool = False


class OtherSection(BaseModel):
    """Passthrough for the remaining fixed/templated section types — route_attribute_grid,
    claim_reactions, list_distribution, portfolio_allocation, text_quotes, benchmark_summary,
    tagline_preferences, route_preference, adoption_detail, trust_builders,
    pain_points_summary, segment_kpi_table, per_respondent, study_report. These are
    single-instance-per-tab and each _sec_*() renderer already falls back gracefully via
    .get(...) defaults, so a strict per-type model isn't as load-bearing here as it is for the
    uncapped distribution types above — extra keys pass through untouched."""
    model_config = ConfigDict(extra="allow")
    type: Literal[
        "route_attribute_grid", "claim_reactions", "list_distribution", "portfolio_allocation",
        "text_quotes", "benchmark_summary", "tagline_preferences", "route_preference",
        "adoption_detail", "trust_builders", "pain_points_summary", "segment_kpi_table",
        "per_respondent", "study_report", "theme_clusters",
    ]


SectionConfig = Union[
    SectionHeaderSection, KpiRowSection, DistributionSection, MultiDistributionSection,
    ScoreDistributionSection, DivergingBarSection, SegmentCardGridSection, SeparatorSection,
    AiInsightSection, VerbatimWallSection, OtherSection,
]


class TabConfig(BaseModel):
    id: str
    label: str
    sections: list[SectionConfig] = Field(min_length=1)


class TabsUIConfig(BaseModel):
    tabs: list[TabConfig] = Field(min_length=1)


class _NarrativeFill(BaseModel):
    subtitle: str = ""
    caption: str = ""
    how_to_read: str = ""
    calc_note: str = ""
    legend: list[LegendItem] = []


class _NarrativeFillBatch(BaseModel):
    by_field: dict[str, _NarrativeFill]


def _find_thin_fields(cfg: "TabsUIConfig", candidates_by_path: dict | None = None) -> list:
    """Field-like objects missing any required narrative text. Only DistributionFieldSpec-shaped
    objects have `caption`; ScoreDistributionSection has the other three but not caption — check
    per-object which attributes actually exist rather than assuming a shared shape (crashed live
    on this exact gap). Also flags a DistributionFieldSpec with an empty legend when its field
    has <=6 distinct values — found live: legend was never requested by the main prompt at all,
    so every chart shipped with zero explanation of what each category value means (e.g. what
    "safety_seeker" actually represents) — the exact "who are we considering X" gap flagged."""
    candidates_by_path = candidates_by_path or {}
    thin: list = []
    for tab in cfg.tabs:
        for sec in tab.sections:
            fields = getattr(sec, "fields", None) or ([sec] if getattr(sec, "field", None) else [])
            for f in fields:
                required = ["subtitle", "how_to_read", "calc_note"]
                if hasattr(f, "caption"):
                    required.append("caption")
                is_thin = not all(getattr(f, attr, "") for attr in required)
                if not is_thin and hasattr(f, "legend"):
                    c = candidates_by_path.get(getattr(f, "field", ""))
                    if c and c["distinct_values"] <= 6 and not f.legend:
                        is_thin = True
                if is_thin:
                    thin.append(f)
    return thin


def _enrich_missing_narratives(cfg: "TabsUIConfig", candidates: list[dict], max_attempts: int = 3) -> None:
    """Second, narrower LLM pass for exactly the fields the main call left thin — the fields the
    coverage backstop appended (title/chart only, no narrative) and anything the main call
    produced with an empty subtitle/caption/how_to_read/calc_note despite the prompt requiring
    all four. Cheaper and more reliable than a full regen: one batched call, small schema, real
    per-field data already computed, so there's nothing left to guess at. Loops because this
    narrower call is itself an LLM instruction-following exercise — observed live returning only
    1/9 requested fields on a single attempt — so a partial response still needs a follow-up
    pass for whatever it missed rather than being treated as done. Best-effort throughout: on
    total failure this silently leaves any remaining thin fields as-is; it never blocks the
    main result."""
    by_path = {c["path"]: c for c in candidates}

    for attempt in range(max_attempts):
        thin = _find_thin_fields(cfg, by_path)
        if not thin:
            return

        field_lines = []
        for f in thin:
            c = by_path.get(f.field)
            counts = json.dumps(c["sample_counts"], ensure_ascii=False) if c else "(no sample data)"
            field_lines.append(f"- {f.field}: values seen: {counts}")
        field_block = "\n".join(field_lines)

        prompt = f"""For EVERY ONE of the {len(thin)} fields below, write its chart narrative — do not
skip any. Return JSON matching:
{{"by_field": {{"<field path>": {{"subtitle": "...", "caption": "...", "how_to_read": "...", "calc_note": "...", "legend": [{{"label": "...", "color": "blue", "description": "..."}}]}}}}}}

Requirements per field — all required, none generic:
- subtitle: one sentence, what this measures and why it was asked.
- caption: the sharpest takeaway from the REAL numbers given — name the percentages, who's
  affected, what it means for the business. Be a critical, blunt analyst — state weaknesses and
  risks plainly, don't soften them.
- how_to_read: one practical sentence on what a specific pattern in this chart would mean.
- calc_note: one sentence on how this value was produced (AI-classified from transcript
  language vs. directly reported).
- legend: one entry PER DISTINCT VALUE actually listed in "values seen" below for that field —
  explain in plain English what that category label actually means/represents for a respondent,
  e.g. for investor_archetype value "safety_seeker": {{"label": "Safety Seeker", "color": "green",
  "description": "Prioritizes capital protection over returns — needs proof of safety before
  considering yield claims."}}. Never invent a value not in "values seen".

FIELDS ({len(thin)} total — "by_field" must have exactly this many keys):
{field_block}

Return ONLY valid JSON. No markdown fences, no explanation."""

        try:
            raw = call_llm(
                [{"role": "system", "content": "You are a market-research analyst writing chart narratives. Return only valid JSON."},
                 {"role": "user", "content": prompt}],
                max_tokens=8000, temp=0.15, json_mode=True,
            )
            raw_stripped = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
            batch = _NarrativeFillBatch.model_validate_json(raw_stripped)
        except (LLMCallError, ValidationError) as e:
            print(f"  _enrich_missing_narratives: attempt {attempt + 1}/{max_attempts} call failed ({e})")
            continue

        filled = 0
        for f in thin:
            fill = batch.by_field.get(f.field)
            if not fill:
                continue
            f.subtitle = f.subtitle or fill.subtitle
            if hasattr(f, "caption"):
                f.caption = f.caption or fill.caption
            f.how_to_read = f.how_to_read or fill.how_to_read
            f.calc_note = f.calc_note or fill.calc_note
            if hasattr(f, "legend") and not f.legend:
                f.legend = fill.legend
            filled += 1
        print(f"  _enrich_missing_narratives: attempt {attempt + 1}/{max_attempts} filled "
              f"{filled}/{len(thin)} thin field(s)")

    still_thin = _find_thin_fields(cfg, by_path)
    if still_thin:
        print(f"  _enrich_missing_narratives: giving up after {max_attempts} attempts — "
              f"{len(still_thin)} field(s) remain without full narrative text: "
              f"{[f.field for f in still_thin]}")


def _sync_label_from_title(cfg: "TabsUIConfig") -> None:
    """Deterministic belt-and-suspenders for the label/title mismatch found live: the renderer
    (_sec_multi_distribution in concept_testing_renderer.py) displays "label", but "title" reads
    more naturally to a prompt author and is what actually got filled every time. The renderer
    now falls back label -> title -> raw field name too, but syncing here means the saved JSON
    itself is correct, not just correct-by-accident at render time."""
    for tab in cfg.tabs:
        for sec in tab.sections:
            fields = getattr(sec, "fields", None) or ([sec] if getattr(sec, "field", None) else [])
            for f in fields:
                if hasattr(f, "label") and hasattr(f, "title"):
                    f.label = f.label or f.title
                    f.title = f.title or f.label


def _fix_numeric_type_misuse(cfg: "TabsUIConfig", candidates: list[dict], numeric_paths: set[str]) -> None:
    """Belt-and-suspenders alongside the prompt's numeric-fields list: score_distribution and
    avg_score-value_type kpis are only valid on genuinely numeric fields — found live:
    "emotional_resolution" (positive/negative/neutral/ambivalent) was used as a 0-10
    score_distribution and rendered empty; "financial_anxiety_level" (low/medium/high) was used
    as an avg_score KPI and rendered "—". Converts a misused score_distribution into a real
    categorical distribution section instead of dropping the content, and converts a misused
    avg_score KPI into count_eq on that field's most common real value."""
    candidates_by_path = {c["path"]: c for c in candidates}
    for tab in cfg.tabs:
        fixed_sections = []
        for sec in tab.sections:
            if isinstance(sec, ScoreDistributionSection) and sec.field not in numeric_paths:
                c = candidates_by_path.get(sec.field)
                if c:
                    print(f"    _fix_numeric_type_misuse: 'score_distribution' misused on "
                          f"non-numeric field '{sec.field}' — converting to a categorical "
                          f"distribution instead of dropping it")
                    fixed_sections.append(DistributionSection(
                        type="distribution", field=sec.field,
                        title=sec.title or _humanize_field_path(sec.field),
                        label=sec.title or _humanize_field_path(sec.field),
                        subtitle=sec.subtitle, how_to_read=sec.how_to_read,
                        calc_note=sec.calc_note, legend=sec.legend,
                        chart="donut" if c["distinct_values"] <= 3 else "h_bar",
                    ))
                else:
                    print(f"    _fix_numeric_type_misuse: 'score_distribution' misused on "
                          f"non-numeric, non-candidate field '{sec.field}' — dropping section")
                continue
            fixed_sections.append(sec)
        tab.sections = fixed_sections

        for sec in tab.sections:
            for kpi in (getattr(sec, "kpis", None) or getattr(sec, "metrics", None) or []):
                if kpi.value_type == "avg_score" and kpi.field and kpi.field not in numeric_paths:
                    c = candidates_by_path.get(kpi.field)
                    if c and c["sample_counts"]:
                        top_val = next(iter(c["sample_counts"]))
                        print(f"    _fix_numeric_type_misuse: avg_score KPI '{kpi.label}' misused "
                              f"on non-numeric field '{kpi.field}' — converting to "
                              f"count_eq='{top_val}'")
                        kpi.value_type = "count_eq"
                        kpi.eq_val = top_val
                    else:
                        print(f"    _fix_numeric_type_misuse: avg_score KPI '{kpi.label}' misused "
                              f"on non-numeric field '{kpi.field}' with no candidate data — "
                              f"left as-is, will render '—'")


def _fmt_display_value(v: str) -> str:
    return " ".join(_word_upper_if_acronym(w) for w in str(v).replace("_", " ").split())


def _word_upper_if_acronym(w: str) -> str:
    return w.upper() if w.lower() in _KNOWN_ACRONYMS else w.title()


def _fix_empty_eq_val(cfg: "TabsUIConfig", candidates: list[dict]) -> None:
    """count_eq/count_contains only mean something with a real value to compare against — found
    live: every segment_card_grid metric shipped with eq_val="", which silently counts only
    respondents where the field is blank (renders as a near-universal 0% across every segment,
    for every metric). Auto-fills from that field's actual most-common real value, and relabels
    if the label just repeats the field name rather than naming the specific value being
    counted (e.g. "Investor Archetype" -> "Safety Seeker" once eq_val is filled)."""
    candidates_by_path = {c["path"]: c for c in candidates}
    for tab in cfg.tabs:
        for sec in tab.sections:
            for kpi in (getattr(sec, "kpis", None) or getattr(sec, "metrics", None) or []):
                if kpi.value_type != "count_eq" or kpi.eq_val or not kpi.field:
                    continue
                c = candidates_by_path.get(kpi.field)
                if not (c and c["sample_counts"]):
                    continue
                top_val = next(iter(c["sample_counts"]))
                print(f"    _fix_empty_eq_val: KPI '{kpi.label}' had empty eq_val on "
                      f"'{kpi.field}' — defaulting to its most-common real value '{top_val}'")
                kpi.eq_val = top_val
                field_label = _humanize_field_path(kpi.field)
                if kpi.label in (field_label, kpi.field, ""):
                    kpi.label = _fmt_display_value(top_val)


def _fill_diverging_bar_narrative(cfg: "TabsUIConfig", project_id: str) -> None:
    """diverging_bar sections aren't field-list-shaped, so _find_thin_fields/_enrich_missing_
    narratives never sees them — found live: the Trust & Adoption diverging_bar shipped with
    empty title/subtitle/caption. Filled deterministically from the real driver/barrier counts
    already in the matrices, no extra LLM call needed."""
    matrices = _load_project_matrices(project_id)
    if not matrices:
        return
    from collections import Counter

    for tab in cfg.tabs:
        for sec in tab.sections:
            if not isinstance(sec, DivergingBarSection):
                continue
            if sec.title and sec.subtitle and sec.caption:
                continue
            d_counts: Counter = Counter()
            b_counts: Counter = Counter()
            for m in matrices:
                dv = _get_path(m, sec.drivers_field)
                if isinstance(dv, list):
                    for x in dv:
                        d_counts[str(x)] += 1
                bv = _get_path(m, sec.barriers_field)
                if isinstance(bv, list):
                    for x in bv:
                        b_counts[str(x)] += 1
            sec.title = sec.title or f"{sec.drivers_label} vs {sec.barriers_label}"
            sec.subtitle = sec.subtitle or (
                "What pushes respondents toward trying this product, weighed against what "
                "holds them back.")
            if not sec.caption:
                parts = []
                top_d = d_counts.most_common(1)
                top_b = b_counts.most_common(1)
                if top_d:
                    parts.append(f"top driver: {_humanize_field_path(top_d[0][0])} "
                                 f"({top_d[0][1]} mention(s))")
                if top_b:
                    parts.append(f"top barrier: {_humanize_field_path(top_b[0][0])} "
                                 f"({top_b[0][1]} mention(s))")
                if parts:
                    sec.caption = ("Real signal from this sample — " + "; ".join(parts) +
                                   " — resolve the barrier before leading with the driver in "
                                   "messaging.")


class _TopicCluster(BaseModel):
    id: str
    label: str
    prefixes: list[str] = Field(min_length=1)


class _TopicClusterPlan(BaseModel):
    clusters: list[_TopicCluster] = Field(min_length=2, max_length=6)


# Standard tab taxonomy for a concept-testing study — stable across ANY concept-testing project
# (gold, meal-kits, insurance, whatever), because it describes the STAGE of a concept-test
# narrative, not any one project's subject matter. This is what generate_ui_config_tabs() used
# to hardcode as tab labels ("Category Context", "Concept Testing", "Route Comparison", "Trust &
# Adoption") — those names were never the problem; baking literal field-name prefixes
# (gold_behavior.*, route1_evaluation.*) into the ROUTING was. Fix: keep the study-type-level
# taxonomy as the labeling anchor, let field routing be data-driven per project.
_CONCEPT_TESTING_TAB_TAXONOMY = [
    {"id": "respondent_profiles", "label": "Respondent Profiles",
     "desc": "Demographic/psychographic/segment fields describing WHO the respondent is — not "
             "what they said about the category or the concept."},
    {"id": "category_context", "label": "Category Context & Prior Behavior",
     "desc": "Fields about the respondent's EXISTING behavior, ownership, awareness, or attitudes "
             "toward the broader product/service category, from BEFORE they saw the tested "
             "concept."},
    {"id": "concept_reaction", "label": "Concept Reaction",
     "desc": "Fields about comprehension, appeal, and reaction to the specific concept, "
             "stimulus, or tagline shown in this study."},
    {"id": "comparative_evaluation", "label": "Comparative Evaluation",
     "desc": "ONLY use if the study tests multiple concepts/routes/variants/messages against "
             "each other — fields comparing them or naming a preferred one. Omit this tab "
             "entirely if the study only tested a single concept."},
    {"id": "trust_adoption", "label": "Trust & Adoption",
     "desc": "Fields about trust, credibility, skepticism, barriers, drivers, and likelihood to "
             "adopt or purchase."},
]

# Standard tab taxonomy for an ethnographic study — stable across ANY in-home IDI or observational
# research project regardless of product category (kitchen appliances, automotive, FMCG, etc.).
# The stages describe the STRUCTURE of ethnographic inquiry, not any one study's subject matter.
_ETHNOGRAPHIC_TAB_TAXONOMY = [
    {"id": "respondent_profile", "label": "Respondent Profiles",
     "desc": "Demographic and psychographic fields describing WHO the respondent is — ownership, "
             "life stage, household context, self-identity. Not brand opinions or product experiences."},
    {"id": "brand_landscape", "label": "Brand Landscape",
     "desc": "Fields about brand relationships: current brand owned/used, NPS signals, "
             "loyalty, advocacy, sentiment, blame attribution, and competitive displacement risk."},
    {"id": "pain_points", "label": "Pain Points & Barriers",
     "desc": "Fields about problems, frustrations, workarounds, unresolved needs, and "
             "failure moments with products or the category. Severity-rated issues and "
             "specific friction events."},
    {"id": "aspiration_need", "label": "Aspiration & Unmet Need",
     "desc": "Fields about what respondents WANT but don't have — aspiration gaps, latent unmet "
             "needs, JTBD (jobs-to-be-done), peak moments, ideal product vision, emotional "
             "resolution, and desired future state."},
    {"id": "purchase_journey", "label": "Purchase Journey",
     "desc": "Fields about how the respondent researched, evaluated, decided, and purchased — "
             "information sources, influencers, trigger events, channel, price sensitivity, "
             "and post-purchase adjustment."},
]


def _derive_topic_clusters(candidates: list[dict], layer2_fields: dict,
                            project_name: str, study_summary: str,
                            numeric_fields: list[dict] | None = None,
                            study_type: str = "concept_testing") -> list[dict]:
    """Maps THIS project's real top-level schema fields onto the standard concept-testing tab
    taxonomy via one LLM call, instead of the fixed field-name-prefix routing
    generate_ui_config_tabs() used to hardcode (gold_behavior.*/route1_evaluation.*/
    coindcx_trust* — CoinDCX's own field vocabulary baked into the prompt text AND a Python
    fallback list). A new concept_testing project with different field names got empty or
    misassigned tabs 2-5 under that scheme. Earlier version of this function let the LLM invent
    a bespoke label per project (e.g. "Gold Investment Behavior") — wrong axis to generalize:
    the taxonomy above is what should stay constant across ANY concept-testing study, only the
    field ROUTING into it should be data-driven per project."""
    prefixes: dict[str, dict] = {}
    for c in candidates:
        top = c["path"].split(".")[0]
        info = prefixes.setdefault(top, {"count": 0, "paths": set()})
        info["count"] += 1
        info["paths"].add(c["path"])
    # Numeric-only groups (e.g. a route's appeal_rating with no categorical sibling field) never
    # appear in `candidates` but still need routing — otherwise the LLM has no tab to slot them
    # into and the main prompt's "attach score_distribution to the right tab" instruction has
    # nothing to attach to.
    for n in (numeric_fields or []):
        top = n["path"].split(".")[0]
        info = prefixes.setdefault(top, {"count": 0, "paths": set()})
        info["count"] += 1
        info["paths"].add(n["path"])

    # Schema-time tagging: if Discovery/Step 1 already decided a field's ui_section (per-field
    # "ui_section" key in layer2_fields — see the DASHBOARD TAB ASSIGNMENT instruction added to
    # both prompts), use that tag directly instead of re-deriving it via an LLM clustering call.
    # In practice a schema evolves incrementally — merging a fresh Discovery run into an existing
    # 50-field schema only tags the handful of fields actually touched this run, the rest keep
    # their pre-existing (untagged) definitions verbatim — so requiring 100% coverage before
    # trusting the tags would never fire on any real project. Instead: split into pre-tagged
    # (used directly, no LLM involved) and untagged (still LLM-clustered as before, on a smaller
    # candidate set), then merge both into the final cluster list by section id.
    _taxonomy_label = {t["id"]: t["label"] for t in _CONCEPT_TESTING_TAB_TAXONOMY}
    _pretagged_by_section: dict[str, list[str]] = {}
    _untagged: dict[str, dict] = {}
    for top, info in prefixes.items():
        sid = layer2_fields.get(top, {}).get("ui_section")
        if sid:
            _pretagged_by_section.setdefault(sid, []).append(top)
        else:
            _untagged[top] = info

    if not _untagged:
        _clusters = [
            {"id": sid, "label": _taxonomy_label.get(sid, sid.replace("_", " ").title()),
             "prefixes": sorted(tops)}
            for sid, tops in _pretagged_by_section.items()
        ]
        _clusters.sort(key=lambda c: 0 if c["id"] == "respondent_profiles" else 1)
        return _clusters

    prefix_lines = []
    for top, info in sorted(_untagged.items(), key=lambda kv: -kv[1]["count"]):
        desc = layer2_fields.get(top, {}).get("description", "")
        sample_paths = sorted(info["paths"])[:4]
        prefix_lines.append(f"- {top} ({info['count']} field(s), e.g. {sample_paths}): {desc}")
    prefix_block = "\n".join(prefix_lines)

    _taxonomy = (_ETHNOGRAPHIC_TAB_TAXONOMY if study_type == "ethnographic"
                 else _CONCEPT_TESTING_TAB_TAXONOMY)
    taxonomy_block = "\n".join(
        f'- id "{t["id"]}", label "{t["label"]}" — {t["desc"]}' for t in _taxonomy
    )
    _study_type_label = "ethnographic in-home IDI" if study_type == "ethnographic" else "concept-testing"

    prompt = f"""Route the top-level schema fields below onto a STANDARD set of {_study_type_label}
dashboard tabs for a qualitative research study.

PROJECT: {project_name}
STUDY SUMMARY: {study_summary}

STANDARD TAB TAXONOMY — use these exact ids/labels, do NOT invent new ones, do NOT rename them
to reference this project's specific subject matter (e.g. never "Gold Investment Behavior" —
use "Category Context & Prior Behavior" instead, which is what that tab always means, for any
concept-testing study):
{taxonomy_block}

TOP-LEVEL FIELDS — every one MUST be routed to exactly one of the tabs above, none left out:
{prefix_block}

Rules:
- Only include a tab from the taxonomy if at least one real field routes to it. Omit any tab
  with nothing to show (e.g. drop "comparative_evaluation" if this study tested only one
  concept, not multiple routes/variants).
- "respondent_profiles" is always included and always first if any demographic/psychographic
  field exists.
- Route by what each field's description says it actually measures, not by superficial name
  similarity to this project's product/category — a field about existing category behavior
  goes to "category_context" even if it happens to be named after this project's specific
  product.
- Every top-level field listed above must appear in exactly one tab's "prefixes" list.

Return ONLY valid JSON matching: {{"clusters": [{{"id": "one_of_the_taxonomy_ids", "label": "the matching taxonomy label",
"prefixes": ["field_a", "field_b"]}}]}}. No markdown fences, no explanation."""

    raw = call_llm(
        [{"role": "system", "content": "You are a UI configuration architect for a qualitative "
                                        "research platform. Return only valid JSON."},
         {"role": "user", "content": prompt}],
        max_tokens=2000, temp=0.15, json_mode=True,
    )
    raw_stripped = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    plan = _TopicClusterPlan.model_validate_json(raw_stripped)

    # Never lose a field to an LLM omission — same philosophy as the section-level backstop
    # further down. Anything left uncovered defaults onto the profile cluster.
    covered = {p for cl in plan.clusters for p in cl.prefixes}
    missing = [top for top in _untagged if top not in covered]
    if missing:
        profile_cluster = next((cl for cl in plan.clusters if cl.id == "respondent_profiles"),
                                plan.clusters[0])
        profile_cluster.prefixes.extend(missing)

    # Merge in the pre-tagged fields (decided at schema-creation time, no LLM involved) by
    # section id — extending an LLM-produced cluster if that section already exists, or adding
    # a new one (using the standard taxonomy label) if every field in that section was pre-tagged
    # and none happened to go through this run's LLM call.
    _result_by_id = {cl.id: {"id": cl.id, "label": cl.label, "prefixes": list(cl.prefixes)}
                     for cl in plan.clusters}
    for sid, tops in _pretagged_by_section.items():
        if sid in _result_by_id:
            _result_by_id[sid]["prefixes"] = sorted(set(_result_by_id[sid]["prefixes"]) | set(tops))
        else:
            _result_by_id[sid] = {"id": sid, "label": _taxonomy_label.get(sid, sid.replace("_", " ").title()),
                                   "prefixes": sorted(tops)}

    _final = list(_result_by_id.values())
    _final.sort(key=lambda c: 0 if c["id"] == "respondent_profiles" else 1)
    return _final


def generate_ui_config_tabs(project_id: str, schema: dict, structure_data: dict, pj: dict, study_type: str | None = None) -> dict | None:
    """Generate a "tabs"-shaped ui_config.json for concept_testing or ethnographic study types. Unlike
    generate_ui_config() (the older tab4_sections/kpi_fields shape consumed by
    qual_generic_renderer.py), this is schema-navigated: the LLM is given TabsUIConfig's real
    JSON Schema to fill, not just prose instructions, and validated + retried against that model
    on failure — this codebase already had one prose-only JSON call fail to parse this session.
    Also unlike generate_ui_config(), field coverage comes from scan_chartable_fields() (real
    distinct values/coverage per field) rather than schema descriptions alone, with an explicit
    instruction that every chartable field must be placed — uncapped, not "top 5"."""
    project_name = pj.get("display_name", project_id)
    study_summary = structure_data.get("study_summary", "")
    layer2_fields = schema.get("layer2", {}).get("fields", {})
    _stype = study_type or structure_data.get("study_type", "concept_testing")

    candidates = scan_chartable_fields(project_id)
    if not candidates:
        print("  generate_ui_config_tabs: no chartable fields found in matrices — skipping")
        return None
    numeric_fields = scan_numeric_score_fields(project_id)
    numeric_paths = {n["path"] for n in numeric_fields}

    field_lines = []
    for c in candidates:
        top_key = c["path"].split(".")[0]
        desc = layer2_fields.get(top_key, {}).get("description", "")
        field_lines.append(
            f"- {c['path']} ({c['distinct_values']} distinct values, "
            f"{c['respondent_coverage']} respondents populated): {desc} | "
            f"values seen: {json.dumps(c['sample_counts'], ensure_ascii=False)}"
        )
    field_block = "\n".join(field_lines)

    numeric_lines = [
        f"- {n['path']} (range {n['min']}-{n['max']}, avg {n['avg']}, "
        f"{n['respondent_coverage']} respondents)"
        for n in numeric_fields
    ]
    numeric_block = "\n".join(numeric_lines) if numeric_lines else "(none found)"

    try:
        clusters = _derive_topic_clusters(candidates, layer2_fields, project_name, study_summary,
                                           numeric_fields=numeric_fields, study_type=_stype)
    except (LLMCallError, ValidationError) as e:
        print(f"  generate_ui_config_tabs: topic clustering failed ({e}) — "
              f"falling back to a single respondent_profiles + single content tab")
        all_prefixes = sorted({c["path"].split(".")[0] for c in candidates})
        clusters = [
            {"id": "respondent_profiles", "label": "Respondent Profiles", "prefixes": all_prefixes},
            {"id": "study_findings", "label": "Study Findings", "prefixes": all_prefixes},
        ]

    tab_spec_lines = []
    for i, cl in enumerate(clusters, start=1):
        tab_spec_lines.append(
            f'{i}. id "{cl["id"]}", label "{cl["label"]}" — covers fields: '
            f'{", ".join(cl["prefixes"])}'
        )
    tab_spec_block = "\n".join(tab_spec_lines)

    schema_json = json.dumps(TabsUIConfig.model_json_schema(), indent=2)

    prompt = f"""You are configuring a Streamlit dashboard for a concept-testing qualitative research study.

PROJECT: {project_name}
STUDY SUMMARY: {study_summary}

Fill the following JSON Schema EXACTLY — your response must validate against this structure:
{schema_json}

GROUND-TRUTH CHARTABLE FIELDS — every one of these {len(candidates)} fields MUST be placed in
exactly one section, in whichever tab below fits its topic. Do not drop any field. Do not invent
fields that aren't in this list:
{field_block}

GROUND-TRUTH NUMERIC SCORE FIELDS — the ONLY fields allowed in a score_distribution section or
an avg_score-value_type kpi. Every field above (in the chartable-fields list) is CATEGORICAL —
using "avg_score" or "score_distribution" on any of them is invalid and will render broken
(silently shows "—" or an empty gauge). If a field you want to show as a score isn't in this
list, it doesn't have numeric data — chart it as a distribution/kpi_row count_eq instead:
{numeric_block}

Produce exactly {len(clusters)} tabs, in this order, with these ids and labels — this grouping was
derived from THIS project's actual field vocabulary, not a fixed template:
{tab_spec_block}

For the respondent_profiles tab specifically: a kpi_row, then a multi_distribution covering
every field assigned to it above — one entry per field, uncapped. Do NOT add a
segment_card_grid section — the per-segment breakdown it would show duplicates the
multi_distribution charts on this same tab (each already supports click-to-filter by segment),
so it's pure redundant clutter, not new information. The kpi_row on this tab MUST include
"Total Respondents" (value_type "total_n"); if a satisfaction/NPS/promoter-style categorical
field is present anywhere in the chartable-fields list, add one count_eq kpi for its
top/positive category; if the NUMERIC SCORE FIELDS list above contains an intent/adoption/
likelihood score field, add one avg_score kpi for it. Do not invent a field for any of these —
only use fields confirmed present in the chartable-fields or numeric-fields lists above.

For every OTHER tab: kpi_row + multi_distribution covering every field assigned to that tab
above. If any of that tab's fields appear in the NUMERIC SCORE FIELDS list, add a
score_distribution section for each (not just an avg in the kpi_row) — a single averaged number
hides whether responses are polarized or uniform, the distribution doesn't. If that tab's field
group includes a natural drivers/barriers (or supports/concerns, pros/cons) pair — two sibling
fields under the same parent object where one names what drives/supports something and the
other names barriers/concerns/gaps — add a diverging_bar section referencing their REAL field
paths exactly as given above; never invent or shorten a path. Free-text sub-fields under any
object (reasons/gaps/verbatim-style fields) won't appear in the chartable-fields list — they're
qualitative, not chart candidates — but are real signal, so mention their strongest theme in
that tab's ai_insight caption rather than dropping them entirely.

EVERY field inside a multi_distribution section (every tab, including respondent_profiles) MUST
also get a "group" value — a short (2-4 word) sub-theme name grouping related fields WITHIN that
tab, based on what those fields actually measure together. E.g. inside respondent_profiles, if
the fields are city/age/gender plus risk_orientation/investment_motivation plus
emotional_resolution/anxiety_level, that's three groups: "Demographics", "Investment Behavior &
Risk", "Emotional & Attitudinal State" — not one flat list of N unrelated fields. Pick 2-4 groups
per tab based on the REAL fields present (never force groups where fields don't actually cluster;
a tab with only 2-3 closely-related fields can use a single group). This powers an in-app
theme filter so a viewer can narrow a crowded tab to one sub-theme instead of scanning every
field at once.

Every tab except respondent_profiles MUST include at least one verbatim_wall_section or
text_quotes section pulling from real transcript evidence (all_passages, pain_points, or a
specific field's *_verbatim/*_reasons/*_gaps sub-field) — charts show WHAT respondents said in
aggregate, but the qualitative "why" only exists as direct quotes, and every tab needs at least
one place a reader can see actual respondent language, not just bars and percentages.

Every distribution/multi_distribution/score_distribution field entry needs ALL FIVE of these
filled — none may be left empty, and none may be a generic restatement like "Distribution of X"
or "Shows how respondents answered X". Also set "title" AND "label" to the SAME human-readable
heading text on every distribution field entry — the renderer displays "label", not "title", so
leaving "label" empty silently shows the raw field name instead of a real heading:

- title / label: same human-readable chart heading (e.g. "Financial Anxiety Level", not
  "financial_anxiety_level"). Set both keys to this identical string.
- subtitle: one sentence, what THIS SPECIFIC field measures and why it was asked — never reuse
  the same subtitle across sibling fields under the same parent object (e.g.
  route1_evaluation.fiu_understood and route1_evaluation.safety_proof_resonance measure
  different things and must get different subtitles, not both "Evaluation of the safety and
  ownership-led route").
- caption: the single sharpest, most consequential takeaway from the ACTUAL numbers above —
  state the real percentages/counts, name who's affected, and say what it means for the
  business. Be a critical, blunt analyst, not a neutral describer — if the data shows a
  weakness, gap, or risk, say so plainly instead of softening it. Example of the bar this must
  clear: "Financial Anxiety Level — 13/23 respondents (57%) sit at medium anxiety, not low —
  most of this audience isn't confident about money and needs safety-first proof before
  yield-story messaging will land, not after."
- how_to_read: one practical sentence telling the viewer what a specific pattern in this chart
  would mean, e.g. "A left-skewed bar toward 'high' or 'strong' signals a real barrier the go-
  to-market plan needs to address before launch, not a nice-to-have."
- calc_note: one sentence on how this value was actually produced — "AI-classified from how
  the respondent talked about X in the transcript" (this is inferred data, not a survey
  question — say so) vs "Directly reported by the respondent" if it's a literal answer field.

For every field with 6 or fewer distinct values, ALSO fill "legend": one entry per distinct
value ACTUALLY LISTED in that field's "values seen" above — {{"label": "...", "color": "blue",
"description": "..."}} — where description explains in plain English what that category
actually means for a respondent, not just restating the label. Example: for investor_archetype
value "safety_seeker": {{"label": "Safety Seeker", "color": "green", "description": "Prioritizes
capital protection over returns — needs proof of safety before considering yield claims."}}.
Never invent a value not in "values seen". This is the single most important content gap to
close — without it, a viewer sees a bar labeled "Safety Seeker" with no idea what that category
was actually defined to mean.

Ground every caption in the REAL sample_counts numbers given for that field above — never invent
a percentage or pattern the data doesn't support.

Chart type per field: 2-3 distinct values -> "donut", 4-8 values -> "h_bar" (or "v_bar" for
ordinal/scaled values like low/medium/high) — override with judgment where a different chart
reads better.

For any kpi that just needs the total respondent count (e.g. "Total Respondents"), use
value_type "total_n" and leave "field" empty — do NOT invent a field name like "respondent.id"
for this; it doesn't exist and will silently render as 0.

Return ONLY valid JSON matching the schema above. No markdown fences, no explanation."""

    messages = [
        {"role": "system", "content": "You are a UI configuration architect for a qualitative "
                                       "research platform. Return only valid JSON — no markdown, no commentary."},
        {"role": "user", "content": prompt},
    ]

    print("  Calling LLM for tabs-shaped ui_config...", end="", flush=True)
    raw = None
    for attempt in range(2):
        try:
            # 4 required narrative fields (subtitle/caption/how_to_read/calc_note) per field,
            # 36+ fields — the old 8000 cap was tight enough to risk mid-field truncation before
            # this prompt required real content for all four.
            raw = call_llm(messages, max_tokens=14000, temp=0.15, json_mode=True)
            # json_mode isn't universally honored — observed live wrapping valid JSON in
            # ```json fences anyway, which failed model_validate_json and burned a whole retry
            # for something regex can strip in-process for free.
            raw_stripped = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
            cfg = TabsUIConfig.model_validate_json(raw_stripped)
            print(" OK")

            # segment_card_grid duplicates the multi_distribution charts on the same tab (each
            # already segment-filterable via click-to-filter) — found live, user called it out
            # as redundant clutter. Prompt says not to generate one; strip it if it shows up
            # anyway.
            for _tab in cfg.tabs:
                _tab.sections = [s for s in _tab.sections if s.type != "segment_card_grid"]

            _fix_numeric_type_misuse(cfg, candidates, numeric_paths)
            _fix_empty_eq_val(cfg, candidates)

            # Deterministic backstop: "place every field" is a prose instruction, and this
            # session already saw the free-model rotation drop instructions under load — verify
            # against the ground-truth scan rather than trust compliance, and append whatever's
            # missing instead of silently losing it. Mirrors the merge-protection philosophy
            # already used elsewhere in this file (never let a regen silently drop coverage).
            covered: set[str] = set()
            for tab in cfg.tabs:
                for sec in tab.sections:
                    if getattr(sec, "field", None):
                        covered.add(sec.field)
                    for f in getattr(sec, "fields", []) or []:
                        covered.add(f.field)
                    for k in ("drivers_field", "barriers_field"):
                        v = getattr(sec, k, None)
                        if v:
                            covered.add(v)
            missing = [c for c in candidates if c["path"] not in covered]
            if missing:
                print(f"  generate_ui_config_tabs: LLM omitted {len(missing)} candidate field(s) "
                      f"despite instructions — appending deterministically: "
                      f"{[m['path'] for m in missing]}")
                # Route each orphaned field to the tab its topic actually belongs on, not
                # unconditionally onto respondent_profiles — found live: coindcx_trust,
                # emotional_resolution, t1_liquidity_understood, skepticism_towards_new_concepts
                # all landed on Respondent Profiles while Trust & Adoption / Category Context sat
                # nearly empty, because every LLM-omitted field was piling onto one fixed tab
                # regardless of subject matter. Uses the SAME clusters computed above (derived
                # from this project's real fields, not a hardcoded prefix list) so the fallback
                # always matches whatever tabs actually got generated this run.
                _cluster_by_prefix = {p: cl["id"] for cl in clusters for p in cl["prefixes"]}

                def _route_tab_id(path: str) -> str:
                    top = path.split(".")[0]
                    if top in _cluster_by_prefix:
                        return _cluster_by_prefix[top]
                    for prefix, tab_id in _cluster_by_prefix.items():
                        if path.startswith(prefix):
                            return tab_id
                    return "respondent_profiles"

                by_tab: dict[str, list] = {}
                for m in missing:
                    by_tab.setdefault(_route_tab_id(m["path"]), []).append(m)

                for tab_id, items in by_tab.items():
                    extra_fields = [
                        DistributionFieldSpec(
                            field=m["path"],
                            title=_humanize_field_path(m["path"]),
                            chart="donut" if m["distinct_values"] <= 3 else "h_bar",
                        ) for m in items
                    ]
                    target_tab = next((t for t in cfg.tabs if t.id == tab_id), cfg.tabs[0])
                    existing_multi = next(
                        (s for s in target_tab.sections if isinstance(s, MultiDistributionSection)), None)
                    if existing_multi:
                        existing_multi.fields.extend(extra_fields)
                    else:
                        target_tab.sections.append(
                            MultiDistributionSection(type="multi_distribution", columns=3, fields=extra_fields))

            # Deterministic backstop: verbatim/quote sections are the one thing this whole
            # pipeline exists to surface (Framework-Matrix-style grounded evidence, per this
            # project's own quote-verification discipline) yet the prose instruction above was
            # found live to be unreliable — a full regen produced zero verbatim_wall_section or
            # text_quotes anywhere in the config. Force at least one onto every non-profile tab.
            has_verbatims = {
                t.id: any(
                    isinstance(s, VerbatimWallSection) or getattr(s, "type", None) == "text_quotes"
                    for s in t.sections
                )
                for t in cfg.tabs
            }
            for tab in cfg.tabs:
                if tab.id == "respondent_profiles" or has_verbatims.get(tab.id):
                    continue
                # No fixed "trust_adoption"-named tab exists across all projects any more —
                # pain_only is a per-tab judgment the LLM already made via its own tab naming/
                # content, so default False here and let the prose instructions above (which
                # ask every non-profile tab for a verbatim section) drive content instead.
                title = f"Voice of the Respondent — {tab.label}"
                print(f"  generate_ui_config_tabs: tab '{tab.id}' had no verbatim section — "
                      f"appending deterministically")
                tab.sections.append(VerbatimWallSection(
                    type="verbatim_wall_section", title=title, topics=[],
                    key_prefix=f"vb_{tab.id}", pain_only=False))

            # Cross-interview theme clustering (local embeddings + HDBSCAN, no LLM cost) surfaces
            # patterns no single-field distribution chart can — e.g. a theme that spans free-text
            # fields across multiple respondents. This is corpus-wide, not topic-specific to one
            # tab, so it's placed once on the last non-profile tab rather than duplicated across
            # every tab — no fixed tab id (e.g. "trust_adoption") to target any more since tabs
            # are now derived per-project, so position is the only stable anchor.
            has_theme_clusters = any(
                getattr(s, "type", None) == "theme_clusters"
                for t in cfg.tabs for s in t.sections
            )
            if not has_theme_clusters:
                _non_profile_tabs = [t for t in cfg.tabs if t.id != "respondent_profiles"]
                target_tab = (_non_profile_tabs or cfg.tabs)[-1]
                target_tab.sections.append(OtherSection(
                    type="theme_clusters", title="Cross-Interview Themes",
                    top_n=8, llm_labels=True))

            _enrich_missing_narratives(cfg, candidates)
            _fill_diverging_bar_narrative(cfg, project_id)
            _sync_label_from_title(cfg)

            out = cfg.model_dump(exclude_none=True, mode="json")
            out["generated_at"] = datetime.now(timezone.utc).isoformat()
            out["project_id"] = project_id
            return out
        except ValidationError as e:
            print(f" validation failed (attempt {attempt + 1}/2): {str(e)[:300]}")
            messages.append({"role": "assistant", "content": raw or ""})
            messages.append({"role": "user", "content":
                f"Your last response failed schema validation:\n{e}\n\n"
                "Fix it and return ONLY the corrected JSON — no markdown, no explanation."})
        except LLMCallError as e:
            print(f" LLM call failed: {e}")
            break

    print("  generate_ui_config_tabs: giving up after retries — no config generated "
          "(caller falls back to the hardcoded renderer)")
    return None


def generate_ui_config(project_id: str, schema: dict, structure_data: dict, pj: dict, client) -> dict | None:
    """Generate ui_config.json via LLM with rule-based fallback."""
    project_name = pj.get("display_name", project_id)
    study_type = structure_data.get("study_type", pj.get("study_type", "qualitative"))
    segments = structure_data.get("respondent_segments", pj.get("filter_keys", []))
    study_summary = structure_data.get("study_summary", "")
    reference_themes = structure_data.get("reference_themes", [])

    layer2_fields = schema.get("layer2", {}).get("fields", {})

    # Determine segment_key from filter_keys or fallback
    filter_keys = pj.get("filter_keys", [])
    segment_key = "segment"
    for k in filter_keys:
        if k in ("brand_owned", "brand", "brand_used"):
            segment_key = k
            break

    # Build layer2 summary for the prompt
    layer2_summary_lines = []
    for fname, fdef in layer2_fields.items():
        line = f"- {fname} ({fdef.get('type','string')}): {fdef.get('description','')}"
        if fdef.get("values"):
            line += f" | values: {', '.join(str(v) for v in fdef['values'][:6])}"
        layer2_summary_lines.append(line)
    layer2_summary = "\n".join(layer2_summary_lines) if layer2_summary_lines else "(no layer2 fields)"

    prompt = f"""You are configuring a Streamlit UI for a qualitative research project.

PROJECT: {project_name}
STUDY TYPE: {study_type}
RESPONDENT SEGMENTS: {', '.join(segments) if segments else 'not specified'}
STUDY SUMMARY: {study_summary}

EXTRACTION SCHEMA FIELDS (Layer 2 — project-specific):
{layer2_summary}

IMPORTANT — some fields appear at the TOP LEVEL of the matrix JSON (not nested under a parent key),
even though they were part of Layer 2. Examples: investor_archetype, life_stage, financial_anxiety_level.
When these appear in the field list above without a dot prefix, reference them by their bare name (e.g. "investor_archetype"), not nested.

---

AVAILABLE TAB4 SECTION TYPES — choose the right ones based on what this study measures:

route_detail_grid: Use when study tests two communication routes / messages / concept variants.
  Config keys: route1_label, route2_label, route1_attributes (list of {{path, label}}), route2_attributes (list of {{path, label}}), score_fields (list of {{path, label}})
  Use when: fields include route1_evaluation.*, route2_evaluation.*, preferred_route

taglines: Use when study evaluates taglines, slogans, or headline copy.
  Config keys: preference_field (e.g. "tagline_reaction.preferred_tagline"), verbatim_field
  Use when: fields include tagline_reaction or similar naming

portfolio_context: Use when study covers investment portfolio or savings allocation behavior.
  Config keys: gold_role_field, sgb_awareness_field, platforms_field, allocation_fields (list of {{path, label}})
  Use when: fields include portfolio_behavior.*, gold_behavior.*, allocation_*

adoption_detail: Use when study has structured adoption drivers and barriers arrays.
  Config keys: drivers_field (path to array), barriers_field (path to array), trial_amount_field
  Use when: fields include adoption.drivers, adoption.barriers

benchmark: Use when study compares the product to named alternatives (SGB, FD, Physical Gold, competitors).
  Config keys: field (path to array of comparison objects), benchmark_key, verdict_key
  Use when: fields include benchmark_comparisons

claims: Use when study tests specific product claims or statements for participant reaction.
  Config keys: claims_field (path to array), claims_list (list of the actual claim strings from study docs)
  Use when: fields include key_claim_reactions or similar

distribution: Use for any enum field shown as a bar chart breakdown.
  Config keys: fields (list of {{path, label}})
  Almost always useful — include for preferred_route, life_stage, archetype, etc.

score_grid: Use for 2–4 numeric score fields displayed side by side as KPI boxes.
  Config keys: fields (list of {{path, label}})
  Use for appeal scores, comprehension scores, trust scores, etc.

verbatim_list: Use for any important verbatim string field displayed as quote cards.
  Config keys: field (string path), max_items (integer, default 10)
  Always useful for the most insight-rich verbatim fields.

---

Generate ui_config.json that tells the UI how to display this project's data.

RULES:
- segment_key: which respondent field groups interviews — "segment" for concept tests, "brand_owned" for brand studies
- entity_label: display label for the grouping ("Segment", "Brand", "Respondent Type", "Investor Segment", etc.)
- study_context: one sentence describing the study
- kpi_fields: up to 5 most important scored/count fields.
  Format options: "/10" for 0-10 scales, "promoter_count" for nps_signal, "high_count" for trust/risk enum fields, "positive_pct" for emotional fields
- signal_scores: exactly 3 composite scores.
  Types: "field_avg_scaled" (needs field + scale keys), "pain_severity", "aspiration_score", "nps_score", "quality_avg"

KPI/SCORE SELECTION PROTOCOL:
1. If the STUDY SUMMARY or STUDY TYPE explicitly requests a KPI (e.g. "measure route appeal"), INCLUDE IT and set "ai_suggested": false.
2. If you are adding a metric that was NOT explicitly requested but you think is useful based on fields, set "ai_suggested": true.
3. Every kpi_field and signal_score MUST have "ai_suggested": boolean.

- tab1_distributions: 3–5 enum fields most useful as segment breakdowns in Deep Dive tab
- tab1_verbatim_fields: up to 3 verbatim string fields to surface as quotes in Deep Dive
- filter_fields: 2–4 fields for filter dropdowns.
  ALWAYS include respondent.{segment_key} and respondent.city.
  Also include strong segment-filter candidates: route_shown, preferred_route, life_stage_phase, archetype, etc.
  Format: {{"key": "respondent.segment", "label": "Segment"}}
- ai_insight_keys: exactly 4 section names for the AI narrative panel.
  Must include a "wants/loves" section, "PAIN POINTS", a trust/signal section, "STRATEGIC SIGNAL"
- tab3_reference_tags: copy verbatim from study reference_themes (max 25 items)
- tab4_label: short label for the project-specific tab (e.g. "Route & Claims", "Concept Evaluation", "Portfolio Analysis")
- tab4_sections: LIST of section objects — include ALL applicable section types from the list above.
  Do not skip section types that match the fields. Include complete config for each.
  Include a "distribution" section for key enum fields even if other sections also cover them.
  For route_detail_grid, list ALL route attribute fields, not just 3-4.
- tab5_trust_field: the main trust/risk/health enum field path (or null)
- tab5_trust_verbatim: verbatim field path for trust explanation (or null)
- tab5_trust_builders: array field path listing trust builders cited (or null)
- tab5_distributions: up to 3 additional enum fields for Tab 5 (different from tab1)

Return ONLY valid JSON. No markdown fences, no explanation."""

    print("  Calling LLM for ui_config...", end="", flush=True)
    raw = _call_llm(client, [
        {"role": "system", "content": "You are a UI configuration architect for a qualitative research platform. Return only valid JSON — no markdown, no commentary."},
        {"role": "user", "content": prompt}
    ], max_tokens=4000, temp=0.1)

    cfg = _extract_json_from_response(raw) if raw else None

    if cfg:
        print(" OK")
    else:
        print(" FAILED — using rule-based fallback")
        cfg = _fallback_ui_config(study_type, segment_key, layer2_fields, reference_themes, project_name)

    # Stamp metadata
    cfg["generated_at"] = datetime.now(timezone.utc).isoformat()
    cfg["project_id"] = project_id

    return cfg


# ── Step 0: Transcript-grounded discovery ──────────────────────────────────

_TRANSCRIPT_EXTS = (".docx", ".md")

# DG/brief docs run 10-20K chars typically — well inside a 70B model's context window, so read
# them close to whole rather than truncating at a few thousand chars (which was silently cutting
# off exactly the concept-test / route-comparison sections that live later in these documents).
_DOC_CHAR_CAP = 24000


def _pick_sample_transcripts(transcripts_dir: Path, n: int = 4) -> list[Path]:
    """Pick the N largest transcripts (proxy for richest/most detailed interviews).
    Kept for backward compat / small projects; prefer _stratified_sample_transcripts."""
    if not transcripts_dir or not transcripts_dir.exists():
        return []
    seen_stems = set()
    candidates = []
    for p in sorted(transcripts_dir.rglob("*")):
        if p.suffix.lower() not in _TRANSCRIPT_EXTS or p.name.startswith("~$"):
            continue
        # prefer .docx over .doc for the same stem
        stem_key = str(p.with_suffix(""))
        if stem_key in seen_stems and p.suffix.lower() == ".doc":
            continue
        seen_stems.add(stem_key)
        candidates.append(p)
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[:n]


def _stratified_sample_transcripts(transcripts_dir: Path, n: int = 5) -> list[Path]:
    """
    Spread the discovery sample across whatever natural buckets the project has (subfolders,
    which are commonly per-city/per-segment splits) instead of just the N largest files overall
    — picking only by size risks discovery only ever seeing one segment/city if that group
    happens to produce the longest interviews. Falls back to global largest-N when there's no
    subfolder structure. Largest-first within each bucket (richer transcripts still preferred).
    """
    if not transcripts_dir or not transcripts_dir.exists():
        return []

    def _files_in(d: Path) -> list[Path]:
        seen, out = set(), []
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() not in _TRANSCRIPT_EXTS or p.name.startswith("~$"):
                continue
            stem_key = str(p.with_suffix(""))
            if stem_key in seen and p.suffix.lower() == ".doc":
                continue
            seen.add(stem_key)
            out.append(p)
        out.sort(key=lambda p: p.stat().st_size, reverse=True)
        return out

    subdirs = [d for d in transcripts_dir.iterdir() if d.is_dir() and d.name != "processed"]
    buckets: dict[str, list[Path]] = {}
    if subdirs:
        for d in subdirs:
            files = _files_in(d)
            if files:
                buckets[d.name] = files
    if not buckets:
        buckets = {"_all": _files_in(transcripts_dir)}
    if not any(buckets.values()):
        return []

    picked: list[Path] = []
    bucket_iters = {k: iter(v) for k, v in buckets.items() if v}
    while len(picked) < n and bucket_iters:
        for k in list(bucket_iters.keys()):
            try:
                p = next(bucket_iters[k])
            except StopIteration:
                del bucket_iters[k]
                continue
            picked.append(p)
            if len(picked) >= n:
                break
    return picked


def _read_transcript_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return _read_docx(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def _sample_transcript_arc(raw_text: str, budget: int) -> str:
    """Sample head+middle+tail thirds instead of a pure head slice. DIs build rapport and
    background early and cover concept reaction / purchase intent / decision content later —
    a head-only slice systematically misses exactly the content discovery needs most. Full text
    returned unchanged when it already fits the budget."""
    n = len(raw_text)
    if n <= budget:
        return raw_text
    third = budget // 3
    head = raw_text[:third]
    mid_start = max(third, n // 2 - third // 2)
    mid = raw_text[mid_start:mid_start + third]
    tail = raw_text[-third:]
    return f"{head}\n\n...[middle of interview omitted]...\n\n{mid}\n\n...[continues]...\n\n{tail}"


def _extract_transcript_observations(path: Path, client, max_chars: int = 150000) -> dict:
    """
    Pass 1 of discovery — read ONE transcript in isolation and extract only what's directly
    grounded in it: a short characterization and a few verbatim quotes. Narrow scope (one doc,
    no cross-document synthesis) keeps this call's hallucination surface small. Every returned
    quote is then mechanically checked against the source text — anything not found verbatim is
    dropped, not trusted. This is the guard against the single-shot "read 5 transcripts and
    synthesize" prompt shape, which is what actually invites hallucination: cramming multiple
    documents into one context window and asking for synthesis is exactly when models blend
    details across respondents or invent a "pattern" that isn't really there.
    """
    raw_text = _read_transcript_text(path)
    body = _sample_transcript_arc(raw_text, max_chars)
    prompt = f"""Read ONLY this single transcript below (head/middle/tail of the interview — some
middle content may be omitted for length, marked as such). Do not assume anything about other
interviews — you have not seen any others.

TRANSCRIPT ({path.name}):
{body}

Extract, grounded ONLY in this document:
1. characterization: 1-2 plain sentences on this respondent's behaviour/mindset — describe, don't label yet.
2. quotes: 4-8 short quotes (under 30 words each) copied EXACTLY as written — character for character,
   no paraphrasing, no cleanup. Draw quotes from ACROSS the whole document shown (background AND
   reactions/decisions/opinions later in the interview), not just the opening — that seem distinctive
   or notable about this respondent.

Return ONLY JSON, no markdown fences:
{{"characterization": "...", "quotes": ["...", "..."]}}"""

    raw = call_llm_safe([{"role": "user", "content": prompt}], max_tokens=1400, temp=0.1, json_mode=True)
    data = _extract_json_from_response(raw) if raw else None
    data = data or {}

    norm_body = re.sub(r"\s+", " ", body).lower()
    verified, dropped = [], []
    for q in data.get("quotes", []) or []:
        nq = re.sub(r"\s+", " ", str(q)).strip().lower()
        (verified if nq and nq in norm_body else dropped).append(q)

    return {
        "doc_id": path.stem,
        "filename": path.name,
        "characterization": data.get("characterization", ""),
        "quotes": verified,
        "_dropped_unverified": dropped,
    }


def discover_from_transcripts(
    project_name: str, study_type: str, dg_text: str, prompt_text: str,
    transcripts_dir: Path, client, n_samples: int = 5, max_chars_per_doc: int = 150000,
    sample_paths: list[Path] | None = None, user_scope: str | None = None,
) -> dict | None:
    """
    Two-pass, quote-verified discovery. Pass 1 reads each sample transcript alone and extracts
    only grounded, mechanically-verified quotes (see _extract_transcript_observations). Pass 2
    synthesizes archetypes/topics/dimensions from those already-verified observations — not from
    raw transcript prose — so the synthesis step reasons over material that's already been
    checked, instead of five documents' worth of prose in one context window at once.

    sample_paths: pass explicit transcript paths (e.g. from the Extraction Studio's current
    selection) to run discovery on exactly those docs instead of re-sampling from
    transcripts_dir — keeps "which transcripts get discovered" and "which get extracted" the
    same set when the caller wants that.
    """
    samples = sample_paths if sample_paths else _stratified_sample_transcripts(transcripts_dir, n_samples)
    if not samples:
        print(f"  No transcripts found in {transcripts_dir} — skipping discovery.")
        return None

    print(f"  Pass 1/2: grounded per-transcript observations from {len(samples)} transcripts "
          f"(one at a time, quote-verified): {', '.join(p.name[:35] for p in samples)}")

    observations = []
    for p in samples:
        obs = _extract_transcript_observations(p, client, max_chars=max_chars_per_doc)
        observations.append(obs)
        n_dropped = len(obs.get("_dropped_unverified", []))
        if n_dropped:
            print(f"    {p.name}: dropped {n_dropped} unverified quote(s) — not found verbatim in source")

    verified_quote_pool = {q for o in observations for q in o.get("quotes", [])}
    if not verified_quote_pool:
        print("  No verified quotes survived Pass 1 — skipping synthesis, discovery unavailable.")
        return None

    obs_block = json.dumps(
        [{"doc_id": o["doc_id"], "characterization": o["characterization"], "quotes": o["quotes"]}
         for o in observations],
        ensure_ascii=False, indent=2,
    )

    scope_block = f"""
RESEARCHER GUIDANCE (given by the human running this study — tells you what scope to look for and
how to reason about it; treat as intent/direction, still ground every claim in the observations below):
{user_scope.strip()}""" if user_scope and user_scope.strip() else ""

    _tab_taxonomy_ids = ", ".join(f'"{t["id"]}"' for t in _CONCEPT_TESTING_TAB_TAXONOMY)
    _tab_taxonomy_block = "\n".join(
        f'- "{t["id"]}" ({t["label"]}): {t["desc"]}' for t in _CONCEPT_TESTING_TAB_TAXONOMY
    )

    discovery_prompt = f"""You are a senior qualitative researcher naming patterns from evidence that has
ALREADY been extracted and verified — your job now is synthesis, not re-reading raw transcripts.

PROJECT (label, may be imprecise): {project_name}
STUDY TYPE (label, may be imprecise): {study_type}
DISCUSSION GUIDE (context only, full document): {dg_text[:_DOC_CHAR_CAP] if dg_text else '[not provided]'}
AI ANALYSIS BRIEF (context only, full document): {prompt_text[:_DOC_CHAR_CAP] if prompt_text else '[not provided]'}
{scope_block}

GROUNDED OBSERVATIONS — one entry per transcript, each quote below was mechanically confirmed to exist
verbatim in its source document. Trust these quotes. Do NOT invent a new quote, do NOT paraphrase one
of these into something smoother, and do NOT attribute a quote to a doc_id it didn't come from:
{obs_block}

Based ONLY on the observations above, answer:

1. study_domain_observed: 2-3 sentences on what this study is actually about, grounded in the observations.
2. respondent_types: 3-6 respondent archetypes you actually observe across these doc_ids. Do not reuse a
   stock list unless the evidence supports it. Each item MUST cite a doc_id and reuse one of its quotes
   verbatim: {{"type_name": "snake_case", "definition": "1 sentence, grounded", "doc_id": "which observation this came from", "distinguishing_quote": "must be copied exactly from that doc_id's quotes above"}}
3. emergent_topics: recurring topics visible across the observations, especially ones the DG/brief does
   NOT mention. Each: {{"topic": "name", "doc_id": "...", "example_quote": "must be copied exactly from that doc_id's quotes above"}}
4. suggested_dimensions: structured fields worth extracting that the DG/brief didn't ask for but the
   observations clearly support: {{"field_name": "snake_case", "type": "enum|string|integer|array|boolean",
   "description": "...", "enum_values": [...] or null, "scoring_rule": "..." or null, "verbatim_field": true/false,
   "ui_section": "one of: {_tab_taxonomy_ids} — which dashboard tab this field belongs on, decided NOW
   from what you've actually observed, not guessed later from the field name after the fact"}}
5. brief_dg_mismatch: assumptions in the DG/brief NOT supported by the observations. Empty list if none.

STANDARD DASHBOARD TAB TAXONOMY — for #4's "ui_section", pick the one that matches what each field
actually measures (these are stable dashboard sections used across every concept-testing study, not
specific to this one):
{_tab_taxonomy_block}

Return ONLY valid JSON, no markdown fences:
{{"study_domain_observed": "...", "respondent_types": [...], "emergent_topics": [...],
  "suggested_dimensions": [...], "brief_dg_mismatch": [...]}}"""

    print("  Pass 2/2: synthesizing archetypes/topics from verified observations...", end="", flush=True)
    raw = _call_llm(client, [
        {"role": "system", "content": "You are a qualitative research analyst. Reuse only the quotes you were given — never invent or paraphrase one. Return only valid JSON."},
        {"role": "user", "content": discovery_prompt}
    ], max_tokens=7000, temp=0.15)

    data = _extract_json_from_response(raw) if raw else None
    if not data:
        print(" FAILED — discovery skipped, schema will be brief-only")
        return None

    # Final safety net: any quote the synthesis pass claims must actually be in the verified pool —
    # catches paraphrase-drift even after being told to reuse quotes exactly.
    n_unverified = 0
    for t in data.get("respondent_types", []):
        if t.get("distinguishing_quote") not in verified_quote_pool:
            t["_unverified"] = True
            n_unverified += 1
    for t in data.get("emergent_topics", []):
        if t.get("example_quote") not in verified_quote_pool:
            t["_unverified"] = True
            n_unverified += 1

    print(f" OK ({len(data.get('respondent_types', []))} types, "
          f"{len(data.get('emergent_topics', []))} emergent topics, "
          f"{len(data.get('suggested_dimensions', []))} suggested dims"
          + (f", {n_unverified} flagged unverified" if n_unverified else "") + ")")
    data["_sample_docs"] = [p.name for p in samples]
    data["_observations"] = observations
    return data


def _resync_master_prompt_from_schema(project_id: str) -> None:
    """
    Rebuild master_prompt.txt's DIMENSIONS TO EXTRACT block and JSON template directly from
    extraction_schema.json's current layer2 fields — no LLM call, pure formatting. Needed
    whenever something edits the schema's field set directly (auto-reconcile, manual field-review
    panel Apply) without going through the normal Step 1-3 generation flow, so the prompt actually
    sent to every extraction call stays in sync with what the schema claims to contain. This is
    the exact fix for the bug found live earlier this session: a merge correctly updated the
    schema JSON but left the master prompt still asking for the old, pre-merge field list.
    """
    project_dir = _DATA_DIR / "projects" / project_id
    schema_path = project_dir / "schema" / "extraction_schema.json"
    mp_path = project_dir / "schema" / "master_prompt.txt"
    if not schema_path.exists() or not mp_path.exists():
        return

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    fields = schema.get("layer2", {}).get("fields", {})
    dims = []
    for fname, fdef in fields.items():
        d = {"field_name": fname, "type": fdef.get("type", "string"),
             "description": fdef.get("description", ""), "enum_values": fdef.get("values"),
             "scoring_rule": fdef.get("scoring_rule"), "verbatim_field": bool(fdef.get("rule")),
             "anchors": fdef.get("anchors")}
        if fdef.get("sub_fields"):
            d["sub_fields"] = [
                {"name": sf.get("name"), "type": sf.get("type", "string"),
                 "description": sf.get("description", ""), "enum_values": sf.get("values"),
                 "verbatim_field": bool(sf.get("rule")), "anchors": sf.get("anchors")}
                for sf in fdef["sub_fields"]
            ]
        dims.append(d)

    mp = mp_path.read_text(encoding="utf-8")
    dims_text = ""
    for dim in dims:
        fname = dim.get("field_name", ""); desc = dim.get("description", "")
        enums = dim.get("enum_values") or []
        rule = dim.get("scoring_rule", "")
        sub_fields = dim.get("sub_fields") if dim.get("type") in ("object", "array") else None
        dims_text += f"\n{fname}: {desc}"
        if enums: dims_text += f" — values: {' | '.join(enums)}"
        dims_text += _format_anchors_line(dim.get("anchors"))
        if sub_fields:
            dims_text += " — REQUIRED sub-fields (use exactly these keys):"
            for sf in sub_fields:
                sf_line = f"\n    - {sf.get('name','')} ({sf.get('type','string')}): {sf.get('description','')}"
                if sf.get("enum_values"):
                    sf_line += f" — values: {' | '.join(sf['enum_values'])}"
                sf_line += _format_anchors_line(sf.get("anchors"), indent="      ")
                dims_text += sf_line
        elif rule:
            dims_text += f" — rule: {rule}"

    json_block = "\n".join(_render_dim_json_line(d) for d in dims)
    mp = re.sub(r"(DIMENSIONS TO EXTRACT:)[\s\S]*?(\n\nENUM CONSTRAINTS)",
                lambda m: m.group(1) + dims_text + m.group(2), mp)
    m = re.search(r'(  \},\n)(  "[a-z0-9_]+":[\s\S]*?)(\n  "pain_points": \[)', mp)
    if m:
        mp = mp[:m.start(2)] + json_block + mp[m.end(2):]
        mp_path.write_text(mp, encoding="utf-8")
        print(f"  Resynced master_prompt.txt to {len(dims)} current schema fields")


# ── Force-resolve scope-named fields Discovery missed ────────────────────────────────────────
# _scope_fields_missing (above, in generate_schema) only detects the gap — a field the user
# explicitly named in scope guidance that no Discovery run ever created. The only remediation
# before this was a blind "+ Add stub" button (empty, untyped, zero evidence). This does the
# actual work: search real transcript text for genuine evidence before creating a field, and be
# honest when there isn't any, rather than fabricating one just to close the gap.

def _norm_match_text_sg(t: str) -> str:
    t = t.lower()
    t = re.sub(r"[‘’“”–—]", "'", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _quote_verified(quote: str, source_text: str) -> bool:
    """Same verification gate_matrix (project_extractor.py) uses for extracted field values —
    reimplemented locally rather than imported, since skills/ modules here are kept independent
    of each other's internals. Exact match after normalization, or >=75% word overlap for longer
    quotes — a claimed quote that doesn't verify against the source text is treated as no
    evidence, not partial evidence."""
    if not quote or not source_text:
        return False
    nq = _norm_match_text_sg(quote.strip().strip('"').strip("'"))
    if len(nq) < 8:
        return False
    ns = _norm_match_text_sg(source_text)
    if nq in ns:
        return True
    words = nq.split()
    if len(words) >= 5:
        overlap = sum(1 for w in words if w in ns) / len(words)
        return overlap >= 0.75
    return False


def _find_transcript_path(transcripts_dir: Path, filename: str) -> Path | None:
    p = transcripts_dir / filename
    if p.exists():
        return p
    stem = Path(filename).stem
    for cand in transcripts_dir.rglob("*"):
        if cand.suffix.lower() in _TRANSCRIPT_EXTS and cand.stem == stem:
            return cand
    return None


def _read_transcript_text(path: Path) -> str:
    if not path:
        return ""
    if path.suffix.lower() == ".docx":
        return _read_docx(path)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


class _FieldEvidence(BaseModel):
    found: bool
    quote: str = ""
    source_doc: str = ""
    # Plain str, not Literal — found live: the LLM sends field_type="" when found=false (it
    # doesn't matter at that point, but a strict Literal crashes the whole batch over one
    # harmless empty string on an unused field). Normalized to "string"/"enum" at use-time
    # instead of validation-time.
    field_type: str = "string"
    values: list[str] = []
    description: str = ""


class _EvidenceBatch(BaseModel):
    by_field: dict[str, _FieldEvidence]


class _FieldAnchor(BaseModel):
    value: str
    quote: str = ""
    doc_id: str = ""


class _FieldAnchorBatch(BaseModel):
    anchors: list[_FieldAnchor] = []


def generate_field_rubrics(project_id: str) -> dict:
    """
    The actual mechanism of cross-interview inconsistency: master_prompt.txt's ENUM CONSTRAINTS
    block lists bare value names ("high | medium | low") with zero examples of what makes an
    answer "high" vs "medium" — every one of N separate per-transcript extraction calls has to
    invent its own boundary for that judgment call from scratch, with no shared reference point.
    That's context getting erased between LLM calls: the reasoning behind a value never survives
    past the call that first used it.

    This grounds each enum value in a real, already-quote-verified example pulled from Discovery's
    Pass-1 observations (schema["discovery"]["_observations"] — the same verified quote pool
    Discovery's synthesis pass reasons over, see discover_from_transcripts()), so no extra
    transcript read is needed. One LLM call assigns quotes from that pool to values; every
    assigned quote is re-verified against the pool before being trusted (same discipline as
    resolve_missing_scope_fields — a claimed anchor that doesn't actually exist in the pool is
    dropped, never fabricated). The result is written to field_def["anchors"] and embedded
    directly into master_prompt.txt's DIMENSIONS block by _resync_master_prompt_from_schema /
    the Step 3 generator, so every extraction call after this judges against the same concrete
    examples instead of re-deriving its own criteria each time.

    Only targets fields/sub_fields that have enum values AND don't already have anchors — cheap
    to re-run repeatedly, it just skips whatever's already anchored.

    Returns {"anchored": [field_path, ...], "skipped_no_pool": bool}.
    """
    project_dir = _DATA_DIR / "projects" / project_id
    schema_path = project_dir / "schema" / "extraction_schema.json"
    if not schema_path.exists():
        return {"anchored": [], "skipped_no_pool": True}

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    observations = ((schema.get("discovery") or {}).get("_observations")) or []
    quote_pool: dict[str, str] = {}  # quote text -> doc_id, for verification + reverse lookup
    for obs in observations:
        for q in obs.get("quotes", []):
            quote_pool[q] = obs.get("doc_id", "")
    if not quote_pool:
        return {"anchored": [], "skipped_no_pool": True}

    fields = schema.get("layer2", {}).get("fields", {})
    targets = []  # (field_name, sub_field_name_or_None, description, values)
    for fname, fdef in fields.items():
        if fdef.get("values") and not fdef.get("anchors"):
            targets.append((fname, None, fdef.get("description", ""), fdef["values"]))
        for sf in (fdef.get("sub_fields") or []):
            if sf.get("values") and not sf.get("anchors"):
                targets.append((fname, sf.get("name"), sf.get("description", ""), sf["values"]))
    if not targets:
        return {"anchored": [], "skipped_no_pool": False}

    fields_block = "\n".join(
        f'- {("%s.%s" % (fn, sfn)) if sfn else fn}: {desc} — values: {" | ".join(vals)}'
        for fn, sfn, desc, vals in targets
    )
    quotes_block = "\n".join(f'- ({doc_id}) "{q}"' for q, doc_id in quote_pool.items())

    prompt = f"""You are grounding enum field definitions in real evidence so every future coder
(human or AI) judges the same value the same way, instead of each one inventing its own boundary.

FIELDS NEEDING GROUNDED EXAMPLES (field: description — values: possible answers):
{fields_block}

VERIFIED QUOTE POOL (already confirmed to exist verbatim in real transcripts — you may ONLY use
quotes from this exact list, copied exactly, never paraphrased or invented):
{quotes_block}

For each field above, and for each of its listed values, find ONE quote from the pool that is a
clear, unambiguous example of that specific value (not a borderline or mixed case). Skip any
value with no clear example in this pool — do not force a weak match, do not invent a quote.

Return JSON: {{"anchors": [{{"field": "field_name or field_name.sub_field_name", "value": "the enum value",
"quote": "exact quote copied from the pool above", "doc_id": "doc_id shown next to that quote"}}]}}"""

    raw = _call_llm(None, [
        {"role": "system", "content": "You ground field definitions in real evidence. Only use "
                                        "quotes copied exactly from the pool given. Return only valid JSON."},
        {"role": "user", "content": prompt},
    ], max_tokens=4000, temp=0.1)
    data = _extract_json_from_response(raw) if raw else None
    if not data:
        return {"anchored": [], "skipped_no_pool": False}

    anchored: list[str] = []
    for a in data.get("anchors", []):
        field_path = a.get("field", "")
        value = a.get("value", "")
        quote = a.get("quote", "")
        if not field_path or not value or quote not in quote_pool:
            continue  # unverified or malformed — dropped, never trusted
        doc_id = quote_pool[quote]
        if "." in field_path:
            fname, sfn = field_path.split(".", 1)
            fdef = fields.get(fname)
            target_def = None
            if fdef:
                for sf in (fdef.get("sub_fields") or []):
                    if sf.get("name") == sfn:
                        target_def = sf
                        break
        else:
            fname, sfn = field_path, None
            target_def = fields.get(fname)
        if target_def is None or value not in (target_def.get("values") or []):
            continue
        target_def.setdefault("anchors", {}).setdefault(value, []).append(
            {"quote": quote, "doc_id": doc_id})
        anchored.append(field_path)

    if anchored:
        schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
        _resync_master_prompt_from_schema(project_id)
        print(f"  generate_field_rubrics: anchored {len(set(anchored))} field(s) with grounded examples")

    return {"anchored": sorted(set(anchored)), "skipped_no_pool": False}


def resolve_missing_scope_fields(
    project_id: str, field_names: list[str], max_extra_transcripts: int = 5,
) -> dict:
    """For each name in field_names (normally schema["_scope_fields_missing"]), search real
    transcript text for genuine evidence before creating a field — never fabricates.

    Round 1: re-ask against the transcripts Discovery already read (schema["discovery"]
    ["_sample_docs"]) — cheap, no new reads. Round 2: for anything still unresolved, read up to
    `max_extra_transcripts` more transcripts not in the original sample and ask again. Anything
    still unresolved after both rounds is recorded as _scope_fields_confirmed_absent — "searched
    N transcripts, genuinely no evidence" — never silently dropped, never invented.

    Every claimed "found" is verified against the actual source text (_quote_verified) before
    being trusted — an LLM claiming evidence that doesn't actually appear in the transcript is
    treated as not-found, same as everywhere else in this codebase's quote-verification gates.

    Returns {"resolved": {field: field_def}, "confirmed_absent": [field, ...],
    "transcripts_checked": [filename, ...]}."""
    project_dir = _DATA_DIR / "projects" / project_id
    schema_path = project_dir / "schema" / "extraction_schema.json"
    transcripts_dir = project_dir / "transcripts"
    if not schema_path.exists() or not field_names:
        return {"resolved": {}, "confirmed_absent": list(field_names), "transcripts_checked": []}

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    sample_docs = (schema.get("discovery") or {}).get("_sample_docs") or []

    read_paths: list[Path] = [
        p for p in (_find_transcript_path(transcripts_dir, fn) for fn in sample_docs) if p
    ]

    remaining = list(dict.fromkeys(field_names))
    resolved: dict[str, dict] = {}
    checked_docs: list[str] = [p.name for p in read_paths]

    for round_no in range(2):
        if not remaining:
            break
        if round_no == 1:
            already = {p.name for p in read_paths}
            candidates = _stratified_sample_transcripts(transcripts_dir, 40)
            extra = [p for p in candidates if p.name not in already][:max_extra_transcripts]
            if not extra:
                break
            read_paths = read_paths + extra
            checked_docs += [p.name for p in extra]

        doc_blocks = [f"=== {p.name} ===\n{_read_transcript_text(p)[:8000]}" for p in read_paths]
        combined = "\n\n".join(doc_blocks)
        field_list = "\n".join(f"- {f}" for f in remaining)
        example = json.dumps({"by_field": {
            remaining[0]: {"found": True, "quote": "an exact phrase copied from the transcript",
                           "source_doc": "example_filename.md", "field_type": "enum",
                           "values": ["value_a", "value_b"], "description": "what this captures"},
        }}, indent=2)

        prompt = f"""Search these interview transcripts for evidence of specific research concepts.

TRANSCRIPTS:
{combined}

For each concept below, named by the human researcher running this study, search ALL transcripts
above for genuine evidence.

Return a JSON OBJECT WITH ACTUAL DATA — one entry per concept name below, filled in from what you
actually found in the transcripts above. Do NOT return a schema or type definitions — return real
values. Example shape (using placeholder content — replace with your real findings):
{example}

CONCEPTS TO SEARCH FOR ({len(remaining)} — "by_field" must have exactly these {len(remaining)} keys):
{field_list}

Rules:
- found=true ONLY if you can quote an EXACT verbatim phrase from one of the transcripts above
  that evidences this concept. The quote must be copied exactly, not paraphrased.
- found=false if no transcript above genuinely discusses this concept — do not force a match,
  do not paraphrase a loosely-related topic into "evidence." A false negative is fine; inventing
  evidence is not.
- If found, propose field_type ("string" or "enum") and, if enum, 2-6 values grounded in what
  these transcripts actually show, plus a one-sentence description of what the field captures.
- source_doc: the exact filename from the "===" headers above the quote came from.

Return ONLY valid JSON. No markdown fences, no explanation."""

        try:
            raw = call_llm(
                [{"role": "system", "content": "You are a meticulous qualitative research "
                                               "analyst. You never invent evidence — every claim "
                                               "must be a verbatim quote from the provided text. "
                                               "Return only valid JSON."},
                 {"role": "user", "content": prompt}],
                max_tokens=6000, temp=0.1, json_mode=True,
            )
            raw_stripped = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
            batch = _EvidenceBatch.model_validate_json(raw_stripped)
        except (LLMCallError, ValidationError) as e:
            print(f"  resolve_missing_scope_fields: round {round_no + 1} call failed ({e})")
            continue

        still_remaining = []
        for f in remaining:
            ev = batch.by_field.get(f)
            if not ev or not ev.found:
                still_remaining.append(f)
                continue
            src_path = next((p for p in read_paths if p.name == ev.source_doc), None)
            src_text = _read_transcript_text(src_path) if src_path else combined
            field_type = "enum" if (ev.field_type == "enum" and ev.values) else "string"
            if _quote_verified(ev.quote, src_text):
                resolved[f] = {
                    "type": field_type,
                    "description": ev.description or "Discovered via targeted evidence search.",
                    **({"values": ev.values} if field_type == "enum" else {}),
                    "_discovery_source_quote": ev.quote,
                    "_discovery_source_doc": ev.source_doc,
                }
            else:
                print(f"    resolve_missing_scope_fields: LLM claimed evidence for '{f}' but the "
                      f"quote doesn't verify against the source text — treating as not found")
                still_remaining.append(f)
        remaining = still_remaining

    if resolved:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        layer2_fields = schema.setdefault("layer2", {}).setdefault("fields", {})
        for fname, fdef in resolved.items():
            if fname not in layer2_fields:  # merge-protected — never overwrite an existing field
                layer2_fields[fname] = fdef
        schema["_scope_fields_missing"] = [
            f for f in schema.get("_scope_fields_missing", []) if f not in resolved]
        # A field resolved now may have been marked confirmed-absent by an earlier, less
        # thorough run — found live in this session's own testing — clear the contradiction.
        schema["_scope_fields_confirmed_absent"] = [
            f for f in schema.get("_scope_fields_confirmed_absent", []) if f not in resolved]
        schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
        _resync_master_prompt_from_schema(project_id)
        print(f"  resolve_missing_scope_fields: created {len(resolved)} field(s) from real "
              f"transcript evidence: {list(resolved.keys())}")

    if remaining:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        absent = sorted(set(schema.get("_scope_fields_confirmed_absent", [])) | set(remaining))
        schema["_scope_fields_confirmed_absent"] = absent
        schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  resolve_missing_scope_fields: searched {len(checked_docs)} transcript(s), "
              f"genuinely no evidence for: {remaining}")

    return {"resolved": resolved, "confirmed_absent": remaining, "transcripts_checked": checked_docs}


def generate_schema(project_id: str, dg_path: Path = None, prompt_path: Path = None, force: bool = False,
                     transcripts_dir: Path = None, n_samples: int = 5, skip_discovery: bool = False,
                     sample_paths: list[Path] | None = None, user_scope: str | None = None):
    project_dir = _DATA_DIR / "projects" / project_id
    schema_dir = project_dir / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    source_docs_dir = project_dir / "source_docs"

    schema_out   = schema_dir / "extraction_schema.json"
    prompt_out   = schema_dir / "master_prompt.txt"
    rs_out       = source_docs_dir / "report_structure.json"

    # Never overwrite existing report_structure.json with --force unless explicitly requested
    # (it maps to existing matrix field names which must stay consistent)
    _rs_exists = rs_out.exists()
    if not force and schema_out.exists() and prompt_out.exists() and _rs_exists:
        print(f"Schema already exists for {project_id}. Use --force to regenerate.")
        return

    # Auto-find DG and AI prompt if not provided
    if not dg_path:
        for pattern in ["DG_*.docx", "dg_*.docx", "*DG*.docx", "*discussion*guide*.docx"]:
            matches = list(source_docs_dir.glob(pattern))
            if matches:
                dg_path = matches[0]; break
    if not prompt_path:
        for pattern in ["AI_Prompt*.docx", "*prompt*.docx", "*analysis*.docx", "*brief*.docx"]:
            matches = list(source_docs_dir.glob(pattern))
            if matches:
                prompt_path = matches[0]; break

    # Read documents
    dg_text = _read_docx(dg_path) if dg_path and dg_path.exists() else ""
    prompt_text = _read_docx(prompt_path) if prompt_path and prompt_path.exists() else ""

    # Load project.json for context
    pj = {}
    pj_path = project_dir / "project.json"
    if pj_path.exists():
        pj = json.loads(pj_path.read_text(encoding="utf-8"))

    study_type = pj.get("study_type", "qualitative")
    project_name = pj.get("display_name", project_id)
    segments = pj.get("filter_keys", [])

    print(f"\nProject: {project_name}")
    print(f"DG: {dg_path.name if dg_path else 'not found'}")
    print(f"AI Prompt: {prompt_path.name if prompt_path else 'not found'}")
    print(f"Study type: {study_type}")
    print()

    if not dg_text and not prompt_text:
        msg = "No DG or AI Prompt found. Add them to source_docs/ and retry."
        print(f"ERROR: {msg}")
        if __name__ == "__main__":
            sys.exit(1)
        raise RuntimeError(msg)  # in-process callers (e.g. Streamlit) must not have sys.exit kill them

    client = _get_client()

    # ── Step 0: Transcript-grounded discovery ───────────────────────────────────
    discovery = None
    if not skip_discovery:
        print("Step 0: Reading sample transcripts for inductive discovery...")
        t_dir = transcripts_dir or (project_dir / "transcripts")
        discovery = discover_from_transcripts(
            project_name, study_type, dg_text, prompt_text, t_dir, client, n_samples=n_samples,
            sample_paths=sample_paths, user_scope=user_scope,
        )
    else:
        print("Step 0: Skipped (--skip-discovery)")

    discovery_block = ""
    if discovery:
        rtypes = discovery.get("respondent_types", [])
        etopics = discovery.get("emergent_topics", [])
        sdims = discovery.get("suggested_dimensions", [])
        mismatches = discovery.get("brief_dg_mismatch", [])
        discovery_block = f"""

TRANSCRIPT-GROUNDED DISCOVERY (read directly from {len(discovery.get('_sample_docs', []))} sample transcripts —
treat this as evidence about what the study ACTUALLY contains, weigh it alongside the documents above,
and prefer it where it conflicts with the documents):
Observed domain: {discovery.get('study_domain_observed', '')}
Respondent types actually observed: {json.dumps(rtypes, ensure_ascii=False)}
Emergent topics not in the brief: {json.dumps(etopics, ensure_ascii=False)}
Dimensions the brief missed but transcripts clearly support: {json.dumps(sdims, ensure_ascii=False)}
Assumptions in the brief/DG NOT supported by the transcripts (do not encode these): {json.dumps(mismatches, ensure_ascii=False)}"""

    # ── Step 1: Extract study structure and dimensions from documents ──────────
    print("Step 1: Analysing study structure...")
    structure_prompt = f"""You are designing a qualitative research data extraction schema for an AI system.
Think logically about what this specific study actually needs — do not force it into a template from a
different study type. If evidence from real transcripts is provided below, ground your schema in that
evidence over untested assumptions in the discussion guide or brief.

PROJECT: {project_name}
STUDY TYPE: {study_type}
SEGMENTS: {', '.join(segments) if segments else 'not specified'}

DISCUSSION GUIDE (full document — extract study sections and what was asked):
{dg_text[:_DOC_CHAR_CAP] if dg_text else '[not provided]'}

AI ANALYSIS BRIEF (full document — extract dimensions to capture, themes, evaluation criteria):
{prompt_text[:_DOC_CHAR_CAP] if prompt_text else '[not provided]'}
{f"RESEARCHER GUIDANCE (given by the human running this study — what scope to focus the schema on and how to reason about it): {user_scope.strip()}" if user_scope and user_scope.strip() else ""}
{discovery_block}

CRITICAL — GRANULARITY: briefs like this typically have a top-level "Research objectives" list
(usually 4-8 bullets) AND a much more detailed "REPORT STRUCTURE" section broken into numbered
sections, each with its own "Cover:" bullet list and "Must decode:" bullet list. THE TOP-LEVEL
OBJECTIVES ARE NOT ENOUGH — most of a brief's real specificity lives in those per-section "Cover:"
and "Must decode:" bullets (a brief can have 5-8 top-level objectives but 40+ granular sub-asks
buried in section detail). You MUST read every section's "Cover:" and "Must decode:" bullets, not
just the top-level objective list, and produce a layer2_dimension (or a sub_field inside a bundled
object dimension) for each genuinely distinct piece of information those bullets ask for. If the
brief also names SPECIFIC claims, certifications, proof-points, or taglines that need individual
per-claim reactions (e.g. "audited vaults", "FIU registration", "T+1 liquidity", specific tagline
text) — extract that exact list into key_claims_to_track so every interview gets scored against
the same named list, not whatever a model happens to notice.

Analyse these documents (and the transcript evidence, if provided) and extract:
1. What sections/topics does this study cover? (List each section with the key question it answers)
2. What specific dimensions must be extracted per interview? (What the LLM should tag and capture
   — walk EVERY "Cover:" and "Must decode:" bullet under EVERY report section, not just the
   top-level research objectives list)
3. What scoring criteria are mentioned? (Any explicit scales, ratings, or evaluation frameworks)
4. What themes or topics should narrative_tags include?
5. What are the 3-5 most important findings this study needs to produce?
6. COVERAGE CHECK — list every distinct research objective AND every "Cover:"/"Must decode:"
   sub-item explicitly named anywhere in the Discussion Guide and AI Analysis Brief above (e.g.
   concept/route comparison, comprehension check, purchase intent, portfolio/category behaviour,
   household decision-making role, specific goal categories, specific named claims/certifications —
   whatever THIS study's documents actually name, at whatever level of detail they name it). Every
   one of these MUST produce at least one CHARTABLE entry in layer2_dimensions below — a
   dg_sections-only mention does NOT satisfy this requirement, because a narrative report section
   cannot be charted or aggregated across respondents. Do not drop a stated objective or sub-item
   just because the sample transcript evidence above happens to be thin on it — the brief/DG
   defines what must be measured; the transcripts inform HOW to measure it, not WHETHER to. Err
   toward MORE structured fields, not fewer — a thin schema is a bigger failure than an unused
   field. It is normal and expected for a detailed brief to produce 30-50+ layer2_dimensions —
   do not self-limit to a "reasonable-sounding" small number.

{f'''DASHBOARD TAB ASSIGNMENT — this is a concept_testing study, so every layer2_dimension below
also needs a "ui_section" value (which dashboard tab this field belongs on), decided NOW while
you're reading the source documents, not guessed later from the field name after the fact. Pick
from this stable taxonomy (used across every concept-testing study, not specific to this one):
{chr(10).join(f'- "{t["id"]}" ({t["label"]}): {t["desc"]}' for t in _CONCEPT_TESTING_TAB_TAXONOMY)}
''' if study_type == "concept_testing" else ""}
NESTED FIELDS: when a dimension is naturally a bundle of sub-questions (e.g. "evaluation of a
concept route" = an appeal score + a comprehension score + a verbatim reaction, all in one), do
NOT compress that into a single free-text scoring_rule string — declare it as "type": "object"
with an explicit "sub_fields" list, one entry per sub-question, each with its own name/type/enum.
This keeps every sub-question independently checkable and consistent across every interview
instead of leaving it to each interview's LLM call to invent its own sub-key names.

CRITICALITY — every field you propose must trace back to a specific place in the DG/brief (or,
for genuinely emergent fields, to real transcript evidence in the discovery block above). Before
adding a field, ask yourself: which exact objective or Cover:/Must decode: bullet does this
answer, and why does it need THIS type/structure rather than a simpler one? If you can't answer
that concretely, don't add the field — a proposed field with no real source is noise a human then
has to catch and remove later. Fill "source_objective" and "rationale" honestly for every field;
these are read by a human reviewer to decide whether to keep, rename, or restructure each field,
so a fabricated or generic rationale defeats the entire point of asking for it.

Return as JSON:
{{
  "study_summary": "2 sentence summary of what this study is measuring",
  "study_type": "ethnographic|concept_testing|usage_context|brand_equity|other",
  "respondent_segments": ["list of segments"],
  "dg_sections": [
    {{
      "id": "snake_case_id",
      "title": "Section Title",
      "dg_reference": "Section X or similar",
      "key_question": "What does this section try to answer?",
      "schema_fields": ["field1", "field2"],
      "finding_question": "What finding should this section produce?"
    }}
  ],
  "layer2_dimensions": [
    {{
      "field_name": "snake_case_name",
      "type": "enum|string|integer|array|boolean|object",
      "description": "what this captures",
      "source_objective": "the exact research objective / Cover: / Must decode: bullet from the DG or brief this field exists to answer — quote or closely paraphrase the source document, do not invent one after the fact",
      "rationale": "1 sentence: WHY this specific type/values/structure was chosen for that objective — e.g. why an enum with these exact values instead of free text, why this needs to be an object with sub_fields instead of one field. This is what a human reviewer reads to judge whether to keep, rename, or restructure the field — a vague or missing rationale here means the field probably shouldn't exist",
      "enum_values": ["val1", "val2"] or null,
      "scoring_rule": "how to assign the value, for simple (non-object) fields only" or null,
      "verbatim_field": true/false,
      "ui_section": "one of the DASHBOARD TAB ASSIGNMENT ids above" or null (null if study_type isn't concept_testing),
      "sub_fields": [
        {{"name": "appeal_score", "type": "integer", "description": "1-10 scale"}},
        {{"name": "comprehension_score", "type": "integer", "description": "1-10 scale"}},
        {{"name": "verbatim_reaction", "type": "string", "verbatim_field": true, "description": "exact quote"}}
      ] or null — REQUIRED (non-null, 2+ entries) whenever type is "object"
    }}
  ],
  "reference_themes": ["theme1", "theme2"],
  "key_claims_to_track": ["claim1", "claim2"] or [],
  "objective_coverage": [
    {{"objective": "objective named in DG/brief", "covered_by": "layer2 field_name that charts it, or null if you could not find evidence to cover it — do NOT put a dg_section id here, it doesn't count as chartable coverage"}}
  ]
}}"""

    print("  Calling LLM...", end="", flush=True)
    structure_response = _call_llm(client, [
        {"role": "system", "content": "You are a qualitative research schema architect. Return only valid JSON."},
        {"role": "user", "content": structure_prompt}
    ], max_tokens=12000)  # bumped from 6000 — now asked for every section's Cover/Must-decode
                            # bullets, not just top-level objectives, so 30-50+ dims is expected

    structure_data = _extract_json_from_response(structure_response)
    if not structure_data:
        print(" FAILED — using fallback structure")
        structure_data = {
            "study_summary": f"{project_name} qualitative study",
            "study_type": study_type,
            "respondent_segments": segments,
            "dg_sections": [],
            "layer2_dimensions": [],
            "reference_themes": [],
            "key_claims_to_track": []
        }
    else:
        print(f" OK ({len(structure_data.get('dg_sections', []))} sections, {len(structure_data.get('layer2_dimensions', []))} dimensions)")
        _dim_names = {str(d.get("field_name", "")).lower() for d in structure_data.get("layer2_dimensions", [])}
        uncovered = [oc.get("objective") for oc in structure_data.get("objective_coverage", [])
                     if not oc.get("covered_by")]
        # Not-chartable = covered_by was set but doesn't name an actual layer2 field (e.g. the LLM
        # pointed at a dg_section instead, which can't be charted/aggregated across respondents —
        # this is the exact loophole that let 5 objectives silently get "narrative-only" coverage).
        not_chartable = [
            oc.get("objective") for oc in structure_data.get("objective_coverage", [])
            if oc.get("covered_by") and str(oc.get("covered_by")).split(",")[0].strip().lower() not in _dim_names
        ]
        if uncovered:
            print(f"  WARNING: {len(uncovered)} DG/brief objective(s) not mapped to any field: {uncovered}")
        if not_chartable:
            print(f"  WARNING: {len(not_chartable)} objective(s) covered only narratively, not by a "
                  f"chartable field: {not_chartable}")
        structure_data["_uncovered_objectives"] = uncovered
        structure_data["_not_chartable_objectives"] = not_chartable

    # ── Step 1b: Deterministic safety-net merge of discovery into structure_data ─
    # (Step 1's prompt already asked the LLM to incorporate discovery — this guarantees
    # nothing discovered gets silently dropped if the LLM ignored it.)
    if discovery:
        existing_fnames = {d.get("field_name", "").lower() for d in structure_data.get("layer2_dimensions", [])}

        rtypes = discovery.get("respondent_types", [])
        if rtypes and "respondent_archetype" not in existing_fnames and "archetype" not in existing_fnames:
            structure_data.setdefault("layer2_dimensions", []).append({
                "field_name": "respondent_archetype",
                "type": "enum",
                "description": "Respondent type as inductively observed across sample transcripts, not a stock category.",
                "enum_values": [t.get("type_name") for t in rtypes if t.get("type_name")],
                "scoring_rule": "Assign the closest-matching observed type; note in narrative_tags if a respondent doesn't fit any cleanly.",
                "verbatim_field": False,
            })

        for sd in discovery.get("suggested_dimensions", []):
            fname = str(sd.get("field_name", "")).lower()
            if fname and fname not in existing_fnames:
                structure_data.setdefault("layer2_dimensions", []).append(sd)
                existing_fnames.add(fname)

        existing_themes = {t.lower() for t in structure_data.get("reference_themes", [])}
        for et in discovery.get("emergent_topics", []):
            topic = et.get("topic")
            if topic and topic.lower() not in existing_themes:
                structure_data.setdefault("reference_themes", []).append(topic)
                existing_themes.add(topic.lower())

    # ── Step 2: Generate Layer 2 JSON schema ──────────────────────────────────
    print("Step 2: Generating extraction schema...")
    dims = structure_data.get("layer2_dimensions", [])
    layer2_fields = {}
    for dim in dims:
        fname = re.sub(r"[^a-z0-9_]", "_", str(dim.get("field_name", "")).lower()).strip("_")
        if not fname: continue
        field_def = {
            "type": dim.get("type", "string"),
            "description": dim.get("description", ""),
        }
        if dim.get("enum_values"):
            field_def["values"] = dim["enum_values"]
        if dim.get("scoring_rule"):
            field_def["scoring_rule"] = dim["scoring_rule"]
        # Which dashboard tab this field belongs on — decided at schema-creation time (Step 1
        # structure prompt / Step 0 discovery), not guessed later by a separate post-hoc
        # clustering pass. generate_ui_config_tabs() reads this directly when present, only
        # falling back to LLM re-clustering for fields that predate this tagging.
        if dim.get("ui_section"):
            field_def["ui_section"] = dim["ui_section"]
        # Provenance — why this field exists and which brief/DG objective it answers. Surfaced
        # (read-only) in the Extraction Studio's Step 2b review so a human can judge whether a
        # field is grounded or invented, instead of reviewing a bare name + one-line description
        # with no way to tell which fields trace back to a real research objective.
        if dim.get("source_objective"):
            field_def["_source_objective"] = dim["source_objective"]
        if dim.get("rationale"):
            field_def["_rationale"] = dim["rationale"]
        # "exact copy from transcript" only makes sense for a literal string value — an enum label
        # ("medium") or a boolean (true/false) can never appear verbatim in the transcript, so
        # tagging either with verbatim_field makes the gate nuke every valid answer for that field.
        # Found live: coindcx_trust (enum) and three boolean fields (trust_in_government_backing
        # etc.) all had this combo — booleans came back holding literal "STRONG EVIDENCE" text
        # (the model borrowed Phase 1's theme-evidence vocabulary, a symptom of the same
        # contradiction: a field that's semantically boolean but was told to act like free text).
        if dim.get("verbatim_field") and dim.get("type", "string") == "string":
            field_def["rule"] = "exact copy from transcript — never paraphrase"
        if dim.get("type") == "object" and dim.get("sub_fields"):
            # Declared sub-questions, not just a prose scoring_rule — keeps every interview's
            # sub-key names consistent and lets the enum/verbatim gates reach inside the object.
            sub_fields = []
            for sf in dim["sub_fields"]:
                sf_def = {"name": re.sub(r"[^a-z0-9_]", "_", str(sf.get("name", "")).lower()).strip("_"),
                          "type": sf.get("type", "string"), "description": sf.get("description", "")}
                if sf.get("enum_values"):
                    sf_def["values"] = sf["enum_values"]
                if sf.get("verbatim_field") and sf.get("type", "string") == "string":
                    sf_def["rule"] = "exact copy from transcript — never paraphrase"
                if sf_def["name"]:
                    sub_fields.append(sf_def)
            if sub_fields:
                field_def["sub_fields"] = sub_fields
        layer2_fields[fname] = field_def

    # ── Merge with the existing schema's fields, don't replace wholesale ───────
    # Each discovery run only samples 5-10 transcripts, so its own field list is naturally a
    # partial view — treating that partial view as "the whole schema" is what caused fields to
    # silently disappear every time discovery was re-run (found live: 9 fields dropped, then 13
    # more on the very next run). A field that already exists and is populated in real matrices
    # is real signal that survives regardless of what any single discovery sample happened to
    # notice — keep it unless this run's LLM redefines the exact same field name, in which case
    # the fresh definition wins (it's presumably an improvement, not a fluke).
    if schema_out.exists():
        try:
            _old_schema = json.loads(schema_out.read_text(encoding="utf-8"))
            _old_fields = _old_schema.get("layer2", {}).get("fields", {})
            _kept_from_old = {k: v for k, v in _old_fields.items() if k not in layer2_fields}
            if _kept_from_old:
                print(f"  Merging {len(_kept_from_old)} field(s) from the existing schema not "
                      f"redefined this run: {sorted(_kept_from_old.keys())}")
            layer2_fields = {**_kept_from_old, **layer2_fields}
        except Exception as e:
            print(f"  WARNING: could not merge with existing schema ({e}) — using this run's fields only")

    # ── Deterministic scope-guidance field coverage check ──────────────────────
    # _uncovered_objectives/_not_chartable_objectives above are LLM-judged and depend on
    # Discovery having sampled transcripts where the objective actually came up — a real
    # objective can be missed just because the sample didn't happen to surface it. This check
    # is deterministic instead: the user's own Scope guidance (Section "1b" in the Extraction
    # Studio, usually AI-drafted from the brief) names its OWN expected field names explicitly,
    # e.g. "**Schema fields:** `tax_advantage_noticed`, `tax_advantage_comprehension_level`" —
    # so we can directly diff those named fields against what's actually in the schema, with no
    # dependency on transcript sampling luck. Found live: a real project had two research
    # objectives (tax advantage comprehension, T+1 liquidity comprehension) whose expected field
    # names were named in the scope guidance but never actually created by any discovery run.
    # Scope-guidance field names are ASPIRATIONAL — written before Discovery runs, so Discovery
    # is free to (and routinely does) cover the same concept under a different name or nested
    # inside an object's sub_fields (e.g. scope names "route1_appeal_rating", Discovery instead
    # creates route1_evaluation.appeal_score — same concept, different shape, not a real gap).
    # An exact-name diff against only top-level field names flags this correctly-covered case as
    # "missing" — found live: 27 of 29 scope-named fields tripped a naive string-equality check
    # even though most were genuinely covered, just reorganized. Fixed with fuzzy token-overlap
    # matching against BOTH top-level field names and every sub_field name, so a real gap (no
    # matching tokens anywhere) is distinguished from a renamed/restructured one (shared tokens).
    def _tokens(name: str) -> set[str]:
        # Naive singularization (strip trailing 's') so "trial_condition" vs "trial_conditions"
        # match — found live as a real false-positive without this.
        return {t[:-1] if t.endswith("s") and len(t) > 3 else t
                for t in re.split(r"[_.]", name.lower()) if len(t) > 2}

    _scope_named_fields: list[str] = []
    if user_scope:
        for match in re.finditer(r"Schema fields?:\*{0,2}\s*([^\n]+)", user_scope, re.IGNORECASE):
            for name in match.group(1).split(","):
                name = name.strip().strip("`").strip()
                name = re.sub(r"\(.*?\)$", "", name).strip()
                if name and re.match(r"^[a-z][a-z0-9_]*$", name, re.IGNORECASE):
                    _scope_named_fields.append(name)
    _scope_named_fields = sorted(set(_scope_named_fields))

    _existing_name_tokens: list[set[str]] = []
    for fname, fdef in layer2_fields.items():
        _existing_name_tokens.append(_tokens(fname))
        for sf in (fdef.get("sub_fields") or []):
            if sf.get("name"):
                _existing_name_tokens.append(_tokens(f"{fname}_{sf['name']}"))

    _scope_fields_missing = []
    for f in _scope_named_fields:
        f_tok = _tokens(f)
        if not f_tok:
            continue
        # "Covered" if an existing field/sub-field shares 2+ tokens, or fully contains this
        # name's tokens (for short 1-2 token names) — tolerant of renaming/restructuring, but a
        # single shared generic word (e.g. both mentioning "liquidity") isn't enough to call a
        # genuinely distinct concept "covered" — found live as a real false-negative at a looser
        # 50%-of-tokens threshold.
        covered = any(len(f_tok & existing) >= 2 or f_tok <= existing
                      for existing in _existing_name_tokens)
        if not covered:
            _scope_fields_missing.append(f)

    if _scope_fields_missing:
        print(f"  WARNING: {len(_scope_fields_missing)} field(s) named explicitly in your scope "
              f"guidance have no matching field anywhere in the schema (fuzzy-checked, not just "
              f"exact name): {_scope_fields_missing}")

    schema = {
        "schema_version": "1.0",
        "project_id": project_id,
        "project_type": structure_data.get("study_type", study_type),
        "study_summary": structure_data.get("study_summary", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_documents": {
            "dg": dg_path.name if dg_path else None,
            "ai_prompt": prompt_path.name if prompt_path else None,
        },
        "discovery": discovery,  # raw transcript-grounded discovery output, or null — traceability for §Step 0
        "objective_coverage": structure_data.get("objective_coverage", []),  # DG/brief objective → field mapping, for gap visibility
        "_uncovered_objectives": structure_data.get("_uncovered_objectives", []),
        "_not_chartable_objectives": structure_data.get("_not_chartable_objectives", []),
        "_scope_fields_missing": _scope_fields_missing,
        # Hash of the exact user_scope text that got embedded into master_prompt.txt's
        # PROJECT CONTEXT & SCOPE block this run (see _context_block below). Lets the UI detect
        # when scope_notes.txt has been edited/saved since the last full Discovery run without
        # a resync — _resync_master_prompt_from_schema only rewrites the DIMENSIONS block, never
        # this one, so on-disk drift here is otherwise invisible to the user.
        "_scope_synced_hash": hashlib.sha256((user_scope or "").strip().encode("utf-8")).hexdigest(),
        "layer1": _LAYER1,
        "layer2": {
            "description": f"{project_name} — project-specific extraction dimensions",
            "fields": layer2_fields,
        }
    }
    schema_out.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved: {schema_out.name} ({len(layer2_fields)} Layer 2 fields)")

    # ── Step 3: Generate master_prompt.txt ────────────────────────────────────
    # Rebuild `dims` from the FINAL merged layer2_fields, not the raw pre-merge structure_data
    # list — otherwise the master prompt (what extraction actually sends to the LLM) silently
    # drops every field the merge just protected in the schema JSON. Found live: a merge that
    # correctly kept 33 fields in extraction_schema.json still produced a master_prompt.txt with
    # only the 10 fields from this run's fresh discovery — the schema bookkeeping was fixed, the
    # actual prompt sent to the model wasn't, so extraction would have silently regressed anyway.
    print("Step 3: Generating master prompt...")
    dims = []
    for fname, fdef in layer2_fields.items():
        d = {
            "field_name": fname, "type": fdef.get("type", "string"),
            "description": fdef.get("description", ""), "enum_values": fdef.get("values"),
            "scoring_rule": fdef.get("scoring_rule"), "verbatim_field": bool(fdef.get("rule")),
            "anchors": fdef.get("anchors"),
        }
        if fdef.get("sub_fields"):
            d["sub_fields"] = [
                {"name": sf.get("name"), "type": sf.get("type", "string"),
                 "description": sf.get("description", ""), "enum_values": sf.get("values"),
                 "verbatim_field": bool(sf.get("rule")), "anchors": sf.get("anchors")}
                for sf in fdef["sub_fields"]
            ]
        dims.append(d)

    dims_text = ""
    for dim in dims:
        fname = dim.get("field_name", "")
        desc = dim.get("description", "")
        enums = dim.get("enum_values", [])
        rule = dim.get("scoring_rule", "")
        sub_fields = dim.get("sub_fields") if dim.get("type") in ("object", "array") else None
        dims_text += f"\n{fname}: {desc}"
        if enums: dims_text += f" — values: {' | '.join(enums)}"
        dims_text += _format_anchors_line(dim.get("anchors"))
        if sub_fields:
            # Declared sub-questions — list each explicitly instead of collapsing into one prose
            # rule, so the model fills the SAME sub-key names every interview.
            dims_text += " — REQUIRED sub-fields (use exactly these keys):"
            for sf in sub_fields:
                sf_line = f"\n    - {sf.get('name','')} ({sf.get('type','string')}): {sf.get('description','')}"
                if sf.get("enum_values"):
                    sf_line += f" — values: {' | '.join(sf['enum_values'])}"
                sf_line += _format_anchors_line(sf.get("anchors"), indent="      ")
                dims_text += sf_line
        elif rule:
            dims_text += f" — rule: {rule}"

    themes = structure_data.get("reference_themes", [])
    claims = structure_data.get("key_claims_to_track", [])
    segs = structure_data.get("respondent_segments", [])

    # ── Project context & scope block — this is what every per-transcript Step 1 call sees,
    # so it carries real research grounding (DG scope + brief-vs-reality gap), not just a
    # one-line summary. Built from Step 0 discovery (if it ran) + Step 1 structure analysis.
    _domain_observed = (discovery or {}).get("study_domain_observed", "")
    _mismatches = (discovery or {}).get("brief_dg_mismatch", [])
    _dg_sections_txt = "\n".join(
        f"  - {s.get('title', '')}: {s.get('key_question', s.get('finding_question', ''))}"
        for s in structure_data.get("dg_sections", [])[:10]
    )
    _context_block = f"""PROJECT CONTEXT & SCOPE

Study: {project_name}
Type: {structure_data.get('study_type', study_type)}
{f'Respondent segments: {", ".join(segs)}' if segs else ''}

What this study measures: {structure_data.get('study_summary', '')}
{f"Researcher guidance (scope/thinking direction given by the human running this study): {user_scope.strip()}" if user_scope and user_scope.strip() else ""}
{f"What the transcripts actually show (grounded — weigh this over the summary above where they diverge): {_domain_observed}" if _domain_observed else ""}
{f"Scope — sections this study covers:{chr(10)}{_dg_sections_txt}" if _dg_sections_txt else ""}
{f"Do NOT assume — flagged during discovery as NOT supported by the real transcripts: {'; '.join(m if isinstance(m, str) else (m.get('claim') or m.get('text') or m.get('reason') or str(m)) for m in _mismatches)}" if _mismatches else ""}"""

    master_prompt = f"""You are a Senior Qualitative Research Consultant. You think like a researcher, not a transcription machine.

{_context_block}

---

PHASE 1 — FREE-FORM REASONING (no schema constraints)

Read the COMPLETE transcript before writing anything. Think through:

A. WHO IS THIS RESPONDENT?
   Profile, segment, demographics. Characterise their mindset and motivations.
   NOTE: Watch for INSIDE vs OUTSIDE VOICE — give socially expected answers vs genuine reactions.
   NOTE: Code-switching (Hindi to English) often marks emotional to rational shifts.

B. CORE BEHAVIOUR AND CONTEXT
   What are they doing, how, with what? What drives their decisions?

C. REACTIONS AND RESPONSES
   How did they respond to each key topic or stimulus? Quote their exact first reactions.

D. PAIN POINTS AND BARRIERS
   What is not working? What would they change?

E. SIGNALS AND MOTIVATIONS
   What would drive them toward adoption, recommendation, or change?

F. THEME CHECK
   For each theme below, state: STRONG EVIDENCE / MIXED EVIDENCE / LIMITED EVIDENCE / NOT IN TRANSCRIPT
   Add any EMERGING THEMES beyond this list:
{chr(10).join(f'   {i+1}. {t}' for i, t in enumerate(themes)) if themes else '   [themes to be defined per study]'}
{f'''
G. KEY CLAIMS OR CONCEPTS SHOWN
   For each claim/concept encountered, note: reaction + understanding + exact quote
{chr(10).join(f'   - {c}' for c in claims)}''' if claims else ''}

H. MOST POWERFUL VERBATIM
   The single most insight-carrying quote for this respondent.

---

PHASE 2 — STRUCTURED JSON EXTRACTION

Using ONLY evidence from Phase 1, fill this schema.

VERBATIM EXTRACTION PROTOCOL (Chain of Verification):
For EVERY verbatim_quote, verbatim, or _verbatim field:
STEP 1: Locate the exact phrase in the transcript.
STEP 2: Copy character by character. Do not shorten, paraphrase, or clean grammar — even if the
   source is broken grammar, code-switching, or run-on speech, copy it exactly as spoken.
STEP 3: If not findable verbatim — use null. Never invent, and never paraphrase-then-present-as-quote.
   This commonly happens when you've correctly identified a real pattern or concern that spans
   MULTIPLE parts of the interview rather than one quotable sentence — that is a genuine synthesis,
   not a verbatim quote, and belongs in a description field (e.g. issue_description), not in the
   verbatim field. When this happens: keep the synthesis in the description field, set the verbatim
   field to null, and do not attempt to manufacture a single sentence that was never actually said.

DIMENSIONS TO EXTRACT:{dims_text if dims_text else chr(10) + '   [dimensions generated from schema]'}

ENUM CONSTRAINTS: output the exact string shown, wherever one genuinely fits. If this respondent's
answer clearly does NOT fit any of the listed values — not even the closest one — do not force it.
Instead output "NEW: <your own short label>" (e.g. "NEW: prefers_hybrid_paper_gold") so the reviewer
can see a real value fell outside the existing options, instead of silently mis-bucketing it.

SEVERITY RULES:
critical = Explicit deal-breaker, refusal, 3+ reinforcing statements
high = Strong hesitation requiring resolution, 2+ statements
medium = Conditional acceptance, 1-2 mentions
low = Single passive mention

UNIVERSAL RULES:
- all_passages: minimum 15 entries of 30+ words verbatim — do NOT paraphrase, do NOT leave empty
- narrative_tags: fill with themes having STRONG or MIXED evidence + any emerging themes
- most_powerful_verbatim: one quote for the client deck — exact copy
- If field has no evidence: use null. Never guess.
- Return raw JSON only — no markdown, no explanation.

{{
  "doc_id": "from filename",
  "filename": "source filename",
  "word_count": 0,
  "respondent": {{
    "city": "from transcript",
    "segment": "from metadata",
    "gender": "from metadata",
    "age_band": "from transcript or null",
    "occupation": "from transcript or null"
  }},
{chr(10).join(_render_dim_json_line(d) for d in dims) if dims else '  // layer2 fields generated from schema'}
  "pain_points": [
    {{
      "severity": "critical | high | medium | low",
      "issue_description": "specific problem",
      "verbatim_quote": "EXACT QUOTE — locate in transcript first",
      "product_area": "string",
      "dg_section": null
    }}
  ],
  "nps_signal": "promoter | passive | detractor | unclear",
  "emotional_resolution": "positive | negative | neutral",
  "narrative_tags": [],
  "most_powerful_verbatim": "EXACT QUOTE for client deck",
  "all_passages": [
    {{
      "content": "VERBATIM 30+ words from transcript",
      "sentiment": "positive | negative | neutral | ambivalent",
      "topic": "string",
      "pain_point": false,
      "decision_signal": false,
      "section": null,
      "paragraph_index": 0
    }}
  ]
}}

TRANSCRIPT:
{{transcript}}
"""

    prompt_out.write_text(master_prompt, encoding="utf-8")
    print(f"  Saved: {prompt_out.name}")

    # ── Step 4: Generate report_structure.json ────────────────────────────────
    print("Step 4: Generating report structure...")
    sections = structure_data.get("dg_sections", [])

    if sections:
        report_structure = {
            "project_id": project_id,
            "report_title": project_name,
            "version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": [
                {
                    "id": s.get("id", f"section_{i+1}"),
                    "dg_section": s.get("dg_reference", f"Section {i+1}"),
                    "title": s.get("title", f"Section {i+1}"),
                    "finding_question": s.get("finding_question", s.get("key_question", "")),
                    "schema_fields": s.get("schema_fields", []),
                    "aggregation": {
                        "distributions": [],
                        "verbatim_fields": [],
                        "segment_breakdown": True
                    }
                }
                for i, s in enumerate(sections)
            ]
        }
        source_docs_dir.mkdir(parents=True, exist_ok=True)
        if not _rs_exists:  # only write if not already present
            rs_out.write_text(json.dumps(report_structure, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  Saved: {rs_out.name} ({len(sections)} sections)")
        else:
            print(f"  Skipped report_structure.json (already exists — preserving existing {len(json.loads(rs_out.read_text())['sections'])} sections)")
    else:
        print("  No sections extracted — report_structure.json not generated")

    # ── Step 5: Generate ui_config.json ──────────────────────────────────────────
    # Two shapes, two generators: qual_generic_renderer.py consumes the older
    # tab4_sections/kpi_fields shape (generate_ui_config); concept_testing_renderer.py's
    # SECTION_RENDERERS engine consumes a "tabs" array (generate_ui_config_tabs) and
    # auto-switches to it in render_concept_testing the moment ui_config.json has that key —
    # no routing changes needed. Previously concept_testing was skipped entirely here because
    # no "tabs"-shaped generator existed; generate_ui_config_tabs() closes that gap.
    _study_type_now = structure_data.get("study_type", study_type)
    _is_tabs = _study_type_now in ("concept_testing", "ethnographic")
    print(f"Step 5: Generating UI config ({'tabs' if _is_tabs else 'tab4_sections'} shape, study_type={_study_type_now})...")
    ui_config_out = schema_dir / "ui_config.json"
    if not ui_config_out.exists() or force:
        ui_cfg = (generate_ui_config_tabs(project_id, schema, structure_data, pj, study_type=_study_type_now)
                  if _is_tabs else
                  generate_ui_config(project_id, schema, structure_data, pj, client))
        if ui_cfg:
            # Same merge-protection as layer2 fields (§Step 2): each run's LLM call only sees
            # THIS run's schema and can easily under-produce or rename sections compared to a
            # previous, richer run — found live: a chart type (taglines, portfolio_context)
            # silently disappeared from the dashboard because a later regeneration produced a
            # thinner ui_config that overwrote a richer one outright. A previous entry survives
            # unless this run explicitly redefines the same key.
            if ui_config_out.exists():
                try:
                    _old_cfg = json.loads(ui_config_out.read_text(encoding="utf-8"))

                    def _merge_by_key(old_list, new_list, key_fn):
                        new_keys = {key_fn(x) for x in new_list if key_fn(x)}
                        kept = [x for x in (old_list or []) if key_fn(x) not in new_keys]
                        if kept:
                            print(f"    Merging {len(kept)} UI element(s) from the existing "
                                  f"ui_config not redefined this run: {[key_fn(x) for x in kept]}")
                        return kept + list(new_list or [])

                    if _is_concept_testing:
                        # Merge by (tab id, section type, field) — a section without a "field"
                        # key (e.g. kpi_row, separator) merges by (tab id, type) alone.
                        def _sec_key(tab_id, s):
                            return (tab_id, s.get("type"), s.get("field", ""))
                        old_tabs = {t.get("id"): t for t in (_old_cfg.get("tabs") or [])}
                        for tab in ui_cfg.get("tabs", []):
                            old_tab = old_tabs.get(tab.get("id"))
                            if not old_tab:
                                continue
                            new_keys = {_sec_key(tab["id"], s) for s in tab.get("sections", [])}
                            kept = [s for s in old_tab.get("sections", [])
                                    if _sec_key(tab["id"], s) not in new_keys]
                            if kept:
                                print(f"    Merging {len(kept)} section(s) into tab "
                                      f"'{tab['id']}' from the existing ui_config not "
                                      f"redefined this run.")
                                tab["sections"] = kept + tab.get("sections", [])
                    else:
                        ui_cfg["tab4_sections"] = _merge_by_key(
                            _old_cfg.get("tab4_sections"), ui_cfg.get("tab4_sections"),
                            lambda s: s.get("type"))
                        ui_cfg["kpi_fields"] = _merge_by_key(
                            _old_cfg.get("kpi_fields"), ui_cfg.get("kpi_fields"),
                            lambda s: s.get("path"))
                        ui_cfg["tab1_distributions"] = _merge_by_key(
                            _old_cfg.get("tab1_distributions"), ui_cfg.get("tab1_distributions"),
                            lambda s: s.get("path"))
                except Exception as e:
                    print(f"    WARNING: could not merge with existing ui_config ({e}) — using this run's UI config only")
            ui_config_out.write_text(json.dumps(ui_cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  Saved: {ui_config_out.name}")
        else:
            print("  WARNING: ui_config.json generation failed — UI will use defaults")
    else:
        print(f"  Skipped ui_config.json (already exists)")

    # ── Step 6: Auto-reconcile duplicate field names ──────────────────────────────
    # Closes the loop that merge-protection (§Step 2) opened: merge-protection stops a
    # regeneration from silently DROPPING fields, but does nothing to stop it silently
    # DUPLICATING a concept under a new name (e.g. 'coindcx_trust' vs 'crypto_trust_gap' vs
    # 'trust_in_digital_platforms' — found live, this schema grew from 19 clean fields to 70
    # bloated ones this way). Only meaningful once real matrices exist — with none, there's no
    # population-count ground truth to decide which name is canonical.
    matrices_dir_check = project_dir / "matrices"
    if matrices_dir_check.exists() and any(matrices_dir_check.glob("*_matrix.json")):
        print("Step 6: Auto-reconciling duplicate field names against existing schema...")
        try:
            from project_extractor import reconcile_schema_fields
            recon_report = reconcile_schema_fields(project_id)
            if recon_report.get("merged_fields"):
                # Field set changed — master_prompt.txt must be resynced or it'll still ask for
                # the now-removed duplicate names (the exact bug that broke extraction earlier
                # this session: schema said 33 fields, prompt only reflected 10).
                _resync_master_prompt_from_schema(project_id)
        except Exception as e:
            print(f"  Step 6 skipped (non-fatal): {e}")
    else:
        print("Step 6: Skipped auto-reconcile (no matrices yet — no population ground truth)")

    print(f"\nSchema generation complete for {project_id}")
    print(f"  -> {schema_out}")
    print(f"  -> {prompt_out}")
    if rs_out.exists():
        print(f"  -> {rs_out}")
    if ui_config_out.exists():
        print(f"  -> {ui_config_out}")
    print("\nNext: review the schema, then run:")
    print(f"  python project_extractor.py --project {project_id}")


def generate_ui_from_master_prompt(project_id: str, force: bool = False):
    """Regenerate ui_config.json using existing master_prompt.txt as context."""
    project_dir = _DATA_DIR / "projects" / project_id
    schema_dir = project_dir / "schema"
    prompt_path = schema_dir / "master_prompt.txt"
    schema_path = schema_dir / "extraction_schema.json"
    ui_out = schema_dir / "ui_config.json"

    if not prompt_path.exists():
        print(f"ERROR: master_prompt.txt not found for {project_id}")
        return

    if ui_out.exists() and not force:
        print(f"ui_config.json already exists for {project_id}. Use --force to overwrite.")
        return

    prompt_text = prompt_path.read_text(encoding="utf-8")
    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path.exists() else {"layer2": {"fields": {}}}

    # Minimal project context from project.json
    pj = {}
    pj_path = project_dir / "project.json"
    if pj_path.exists():
        pj = json.loads(pj_path.read_text(encoding="utf-8"))

    # project.json is authoritative when present — the master_prompt.txt regex sniff below is
    # a fallback for projects generated before study_type was reliably stamped there.
    study_type = pj.get("study_type")
    if not study_type:
        study_type = "qualitative"
        if "TYPE: concept_testing" in prompt_text: study_type = "concept_testing"
        elif "TYPE: ethnographic" in prompt_text: study_type = "ethnographic"

    structure_data = {
        "study_type": study_type,
        "study_summary": "",
        "respondent_segments": pj.get("filter_keys", []),
        "reference_themes": []
    }

    # Extract summary if possible
    m = re.search(r"SUMMARY: (.*)", prompt_text)
    if m: structure_data["study_summary"] = m.group(1).strip()

    # Extract themes from PHASE 1 Theme Check
    themes = []
    theme_block = re.search(r"F\. THEME CHECK[\s\S]*?G\.", prompt_text)
    if theme_block:
        for line in theme_block.group(0).splitlines():
            m_t = re.search(r"\d+\.\s+(.*)", line)
            if m_t: themes.append(m_t.group(1).strip())
    structure_data["reference_themes"] = themes

    client = _get_client()
    print(f"Regenerating UI config for {project_id} from master_prompt.txt...")
    ui_cfg = (generate_ui_config_tabs(project_id, schema, structure_data, pj, study_type=study_type)
              if study_type in ("concept_testing", "ethnographic") else
              generate_ui_config(project_id, schema, structure_data, pj, client))

    if ui_cfg:
        ui_out.write_text(json.dumps(ui_cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved: {ui_out.name}")
    else:
        print("  FAILED to generate UI config.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--dg", default=None, help="Path to Discussion Guide docx")
    parser.add_argument("--prompt", default=None, help="Path to AI Analysis Prompt docx")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--ui-only", action="store_true", help="Only regenerate ui_config.json from master_prompt.txt")
    parser.add_argument("--transcripts-dir", default=None,
                         help="Override transcripts folder for Step 0 discovery (default: projects/<id>/transcripts)")
    parser.add_argument("--sample-transcripts", type=int, default=5,
                         help="How many of the largest transcripts to read for discovery (default 4)")
    parser.add_argument("--skip-discovery", action="store_true",
                         help="Skip Step 0 transcript reading, brief-only schema generation (old behaviour)")
    args = parser.parse_args()

    if args.ui_only:
        generate_ui_from_master_prompt(args.project, force=args.force)
    else:
        dg_path = Path(args.dg) if args.dg else None
        prompt_path = Path(args.prompt) if args.prompt else None
        t_dir = Path(args.transcripts_dir) if args.transcripts_dir else None
        generate_schema(args.project, dg_path=dg_path, prompt_path=prompt_path, force=args.force,
                         transcripts_dir=t_dir, n_samples=args.sample_transcripts, skip_discovery=args.skip_discovery)


if __name__ == "__main__":
    main()
