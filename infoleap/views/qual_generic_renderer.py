"""
qual_generic_renderer.py — InfoLeap Pulse
==========================================
Generic Streamlit renderer for any qualitative research project.
Reads ui_config.json and renders 7 analysis tabs.

Entry point: render_generic_project(proj, ui_config, base_path, call_openrouter_fn)
"""
from __future__ import annotations

import html as _html_mod
import json
import re as _re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

import plotly.graph_objects as go
import streamlit as st

from infoleap.utils.ui_styles import (BRAND_COLORS, CHART_LAYOUT, COLORS,
                                    section_header, kpi_card, page_banner, empty_state,
                                    signal_score_card)

# -- Design tokens -------------------------------------------------------------
_P: dict = COLORS
_BRAND_PAL: list = BRAND_COLORS
_FONT = "Inter, Arial, sans-serif"
_NEON = "#38bdf8" # Neon Cyan from styles

# ==============================================================================
# UTILITIES
# ==============================================================================

def _get_nested(obj: Any, path: str) -> Any:
    """'a.b.c' -> obj['a']['b']['c']. Returns None on any miss."""
    try:
        for key in path.split("."):
            if isinstance(obj, dict): obj = obj.get(key)
            elif isinstance(obj, list) and key.isdigit(): obj = obj[int(key)]
            else: return None
        return obj
    except: return None

def _safe_avg(vals: list) -> Optional[float]:
    nums = [v for v in vals if isinstance(v, (int, float)) and v is not None]
    return sum(nums) / len(nums) if nums else None

def _sev_color(sev: str) -> str:
    return {"critical": _P["red"], "high": _P["orange"], "medium": _P["amber"], "low": _P["green"]}.get((sev or "").lower(), _P["neutral"])

def _sent_color(sent: str) -> tuple[str, str]:
    return {"positive": (_P["green"], "▲"), "negative": (_P["red"], "▼"), "neutral": (_P["neutral"], "—")}.get((sent or "").lower(), (_P["neutral"], "·"))

def _md_bold(text: str) -> str:
    return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

def _build_highlight_idx(matrix: dict) -> list:
    idx = []
    for pp in (matrix.get("pain_points") or []):
        if isinstance(pp, dict) and pp.get("verbatim_quote"):
            frag = _re.sub(r"\s+", " ", pp["verbatim_quote"].lower())[:60]
            idx.append((frag, (pp.get("severity") or "").title() + " Pain", _P["red"]))
    for p in (matrix.get("all_passages") or []):
        if isinstance(p, dict) and p.get("decision_signal") and p.get("content"):
            frag = _re.sub(r"\s+", " ", p["content"].lower())[:60]
            idx.append((frag, "→ decision", _P["teal"]))
    mpv = matrix.get("most_powerful_verbatim")
    if mpv:
        frag = _re.sub(r"\s+", " ", mpv.lower())[:60]
        idx.append((frag, "★ key quote", _P["purple"]))
    return idx

def _parse_ai_sections(text: str, keys: list[str]) -> dict[str, list[str]]:
    sections = {k: [] for k in keys}; current = None; escaped = "|".join(_re.escape(k) for k in keys)
    pattern = _re.compile(r"^\*{0,2}(" + escaped + r")\*{0,2}:?\*{0,2}\s*$|^\*{0,2}(" + escaped + r")\*{0,2}:(.*)$", _re.I)
    for line in text.strip().splitlines():
        ls = line.strip(); m = pattern.match(ls)
        if m:
            key = (m.group(1) or m.group(2) or "").upper(); rest = (m.group(3) or "").strip().strip("*").strip()
            if key in sections: current = key; 
            if rest: sections[current].append(rest)
        elif current and ls: sections[current].append(ls.strip("*").strip())
    return sections

# ==============================================================================
# CONFIG + DATA
# ==============================================================================

def load_ui_config(proj_id: str, schema_dir: Path) -> dict:
    path = schema_dir / "ui_config.json"
    if not path.exists(): return {}
    try: return json.loads(path.read_text(encoding="utf-8"))
    except: return {}

def _apply_filter(matrices: list[dict], filter_key: str, filter_val: str) -> list[dict]:
    if not filter_val or filter_val == "All": return matrices
    return [m for m in matrices if str(_get_nested(m, filter_key) or "") == filter_val]

def _compute_kpi_value(field_cfg: dict, matrices: list[dict]) -> str:
    path = field_cfg.get("path", ""); fmt = field_cfg.get("format", ""); total = len(matrices)
    if fmt == "/10":
        avg = _safe_avg([_get_nested(m, path) for m in matrices])
        return f"{avg:.1f}/10" if avg is not None else "—"
    if fmt == "promoter_count":
        n = sum(1 for m in matrices if (m.get("nps_signal") or "").lower() == "promoter")
        return f"{n}/{total}"
    if fmt == "high_count":
        n = sum(1 for m in matrices if str(_get_nested(m, path) or "").lower() == "high")
        return f"{n}/{total}"
    avg = _safe_avg([_get_nested(m, path) for m in matrices])
    return f"{avg:.1f}" if avg is not None else "—"

def _compute_signal_score(score_cfg: dict, matrices: list[dict]) -> int:
    stype = score_cfg.get("type", ""); total = len(matrices)
    if not total: return 0
    if stype == "field_avg_scaled":
        scale = score_cfg.get("scale", 10); field = score_cfg.get("field", "")
        avg = _safe_avg([_get_nested(m, field) for m in matrices])
        return min(100, int(avg / scale * 100)) if avg is not None else 0
    if stype == "pain_severity":
        weights = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        total_w = sum(sum(weights.get((pp.get("severity") or "").lower(), 0) for pp in (m.get("pain_points") or [])) for m in matrices)
        max_w = sum(len(m.get("pain_points") or []) * 4 for m in matrices)
        return min(100, int(total_w / max_w * 100)) if max_w else 0
    if stype == "quality_avg":
        avg = _safe_avg([m.get("_quality_score") for m in matrices])
        return int(avg) if avg is not None else 0
    return 0


def _compute_health_signals(matrices: list[dict], ui_config: dict) -> dict:
    """Compute universal satisfaction / risk / opportunity scores from available fields."""
    n = len(matrices) or 1
    # Satisfaction: NPS promoter + positive emotional resolution
    promoters = sum(1 for m in matrices if (m.get("nps_signal") or "").lower() == "promoter")
    pos_res = sum(1 for m in matrices if (m.get("emotional_resolution") or "").lower() == "positive")
    # Try custom score fields from ui_config signal_scores
    custom_sat = None
    for sc in (ui_config.get("signal_scores") or []):
        if sc.get("higher_is_better") and sc.get("type") == "field_avg_scaled":
            v = _compute_signal_score(sc, matrices)
            if v: custom_sat = v; break
    satisfaction = custom_sat if custom_sat is not None else min(100, round(promoters / n * 50 + pos_res / n * 50))
    # Risk: high-severity pain points + product blame ratio
    hi_pain = sum(sum(1 for pp in (m.get("pain_points") or []) if isinstance(pp, dict) and (pp.get("severity") or "").lower() == "high") for m in matrices)
    prod_blame = sum(len(m.get("product_blame_instances") or []) for m in matrices)
    self_blame = sum(len(m.get("self_blame_instances") or []) for m in matrices)
    blame_total = max(prod_blame + self_blame, 1)
    risk = min(100, round(hi_pain / n * 12 + prod_blame / blame_total * 40))
    # Opportunity: aspiration gaps + unspoken needs
    asp_gaps = sum(len(m.get("aspiration_reality_gaps") or []) for m in matrices)
    unspoken = sum(len(m.get("unspoken_needs") or []) for m in matrices)
    opportunity = min(100, round(asp_gaps / n * 8 + unspoken / n * 5))
    return {"satisfaction": satisfaction, "risk": risk, "opportunity": opportunity}


def _load_study_brief(proj: dict, base_path: Path) -> dict:
    """Read master_prompt.txt and extract STUDY / TYPE / SUMMARY."""
    import re as _re2
    proj_id = proj.get("id", "")
    schema_dir = (proj.get("abs_paths") or {}).get("schema") or base_path / "data" / "projects" / proj_id / "schema"
    prompt_path = Path(schema_dir) / "master_prompt.txt"
    if not prompt_path.exists():
        return {}
    try:
        text = prompt_path.read_text(encoding="utf-8", errors="replace")
        study = _re2.search(r"^STUDY:\s*(.+)", text, _re2.MULTILINE)
        stype = _re2.search(r"^TYPE:\s*(.+)", text, _re2.MULTILINE)
        segs = _re2.search(r"^RESPONDENT SEGMENTS:\s*(.+)", text, _re2.MULTILINE)
        summary_m = _re2.search(r"^SUMMARY:\s*(.+?)(?=\n---|\n\n[A-Z]{2,})", text, _re2.MULTILINE | _re2.DOTALL)
        return {
            "study": study.group(1).strip() if study else "",
            "type": stype.group(1).strip() if stype else "",
            "segments": segs.group(1).strip() if segs else "",
            "summary": summary_m.group(1).strip() if summary_m else "",
        }
    except Exception:
        return {}

# ==============================================================================
# PREMIUM RENDERING COMPONENTS
# ==============================================================================

def _render_study_brief_card(ui_config: dict, brief: dict) -> None:
    study_ctx = ui_config.get("study_context", "")
    study_name = brief.get("study") or study_ctx
    study_type = brief.get("type", "")
    segments = brief.get("segments", "")
    summary = brief.get("summary", "")
    if not (study_name or summary): return
    type_badge = (f'<span style="font-size:0.6rem;font-weight:900;background:#334155;color:#94a3b8;'
                  f'padding:3px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:0.08em;">'
                  f'{_html_mod.escape(study_type)}</span> ') if study_type else ""
    seg_html = ""
    if segments:
        seg_items = "".join(
            f'<span style="font-size:0.62rem;background:#1e40af20;color:#1e40af;padding:2px 8px;border-radius:10px;margin-right:4px;">'
            f'{_html_mod.escape(s.strip())}</span>'
            for s in segments.split(",") if s.strip()
        )
        seg_html = f'<div style="margin-top:10px;">{seg_items}</div>'
    summary_html = ""
    if summary:
        clean = summary.replace("\n", " ").strip()[:400]
        summary_html = (f'<div style="margin-top:12px;font-size:0.82rem;color:rgba(255,255,255,0.75);'
                        f'line-height:1.7;">{_html_mod.escape(clean)}{"..." if len(summary)>400 else ""}</div>')
    st.markdown(
        f'<div style="background:linear-gradient(135deg,#0f172a,#1e293b);border:1px solid rgba(255,255,255,0.08);'
        f'border-radius:16px;padding:24px 28px;margin-bottom:20px;">'
        f'<div style="font-size:0.6rem;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.12em;margin-bottom:6px;">Study Brief</div>'
        f'{type_badge}'
        f'<div style="font-size:1.1rem;font-weight:800;color:white;margin-top:4px;">{_html_mod.escape(study_name)}</div>'
        f'{seg_html}{summary_html}</div>',
        unsafe_allow_html=True,
    )


def _render_signal_scores(matrices: list[dict], ui_config: dict) -> None:
    sigs = _compute_health_signals(matrices, ui_config)
    cols = st.columns(3)
    specs = [
        ("Satisfaction", sigs["satisfaction"], True, "At risk", "Strong"),
        ("Risk Level",   sigs["risk"],         False, "Safe",   "Critical"),
        ("Opportunity",  sigs["opportunity"],   True, "Low",    "High"),
    ]
    for col, (lbl, val, higher_good, lo, hi) in zip(cols, specs):
        with col:
            signal_score_card(lbl, val, higher_good, lo, hi)


def _render_filter_bar(matrices: list[dict], filter_fields: list[dict]) -> tuple[list[dict], dict]:
    if not filter_fields: return matrices, {}
    cols = st.columns(len(filter_fields))
    filtered = matrices; active_filters = {}
    for col, ff in zip(cols, filter_fields):
        key = ff.get("key", ""); label = ff.get("label", key)
        vals = sorted({str(_get_nested(m, key) or "—") for m in matrices if _get_nested(m, key)})
        opts = ["All"] + vals
        chosen = col.selectbox(label, opts, key=f"filter_{key}")
        filtered = _apply_filter(filtered, key, chosen)
        if chosen != "All": active_filters[key] = chosen
    return filtered, active_filters

def _render_distribution(field_path: str, label: str, matrices: list[dict], ordered_values: list[str] = None, chart_type: str = "h_bar") -> None:
    """Render a label distribution for one matrix field as a chart.

    chart_type is a chart_renderer._CHART_REGISTRY key (e.g. "h_bar", "donut",
    "v_bar"), normally sourced from a ui_config.json field's "chart" value.
    Pass "auto" to let select_chart_type_llm() pick based on the data shape.
    """
    vals = [str(_get_nested(m, field_path) or "—") for m in matrices]
    counts = Counter(v for v in vals if v != "—")
    if not counts: return
    items = sorted(counts.items(), key=lambda x: (ordered_values.index(x[0]) if ordered_values and x[0] in ordered_values else -x[1]))
    ordered_counts = {k: v for k, v in items}
    from infoleap.views.chart_adapter import render_counts
    render_counts(ordered_counts, label=label, chart_type=chart_type, question=f"distribution of {label}")

def _render_verbatim_block(field_path: str, label: str, matrices: list[dict], max_items: int = 5, accent: str = None) -> None:
    accent = accent or _P["purple"]
    quotes = [str(_get_nested(m, field_path)).strip() for m in matrices if _get_nested(m, field_path)]
    quotes = [q for q in quotes if q][:max_items]
    if not quotes: return
    st.markdown(f'<div style="font-size:0.68rem;font-weight:800;color:{accent};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px;">{_html_mod.escape(label)}</div>', unsafe_allow_html=True)
    for q in quotes:
        st.markdown(f'<div style="border-left:3.5px solid {accent};padding:12px 16px;background:{accent}08;border-radius:0 12px 12px 0;margin-bottom:8px;"><span style="font-size:0.88rem;color:#1f2937;line-height:1.7;font-family:Georgia,serif;font-style:italic;">&ldquo;{_html_mod.escape(q)}&rdquo;</span></div>', unsafe_allow_html=True)

def _quote_card(text: str, label: str = "", accent: str = None) -> None:
    accent = accent or _P["purple"]
    label_html = (f'<div style="font-size:0.65rem;font-weight:800;color:{accent};text-transform:uppercase;margin-bottom:4px;">'
                  f'{_html_mod.escape(label)}</div>') if label else ""
    st.markdown(f'<div style="border-left:3.5px solid {accent};padding:12px 16px;background:{accent}08;border-radius:0 12px 12px 0;margin-bottom:8px;">'
                f'{label_html}'
                f'<span style="font-size:0.88rem;color:#1f2937;line-height:1.7;font-family:Georgia,serif;font-style:italic;">&ldquo;{_html_mod.escape(str(text))}&rdquo;</span></div>',
                unsafe_allow_html=True)

# ==============================================================================
# TAB 4 SECTION TYPE RENDERERS
# ==============================================================================

def _render_sec_distribution(sec: dict, matrices: list[dict]) -> None:
    for f in (sec.get("fields") or []):
        _render_distribution(f.get("path", ""), f.get("label", ""), matrices, chart_type=f.get("chart", "h_bar"))

def _render_sec_route_detail_grid(sec: dict, matrices: list[dict]) -> None:
    r1_label = sec.get("route1_label", "Route 1"); r2_label = sec.get("route2_label", "Route 2")
    r1_attrs = sec.get("route1_attributes") or []; r2_attrs = sec.get("route2_attributes") or []
    score_flds = sec.get("score_fields") or []
    if score_flds:
        scols = st.columns(len(score_flds))
        for i, sf in enumerate(score_flds):
            avg = _safe_avg([_get_nested(m, sf.get("path","")) for m in matrices])
            with scols[i]: kpi_card(sf.get("label",""), f"{avg:.1f}/10" if avg is not None else "—", _BRAND_PAL[i % len(_BRAND_PAL)])
        st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)
    lcol, rcol = st.columns(2)
    for col, label, attrs in [(lcol, r1_label, r1_attrs), (rcol, r2_label, r2_attrs)]:
        with col:
            st.markdown(f'<div style="font-size:0.75rem;font-weight:800;color:#374151;margin-bottom:12px;">{_html_mod.escape(label)}</div>', unsafe_allow_html=True)
            for attr in attrs:
                path = attr.get("path", ""); lbl = attr.get("label", path.split(".")[-1])
                vals = [str(_get_nested(m, path) or "") for m in matrices if _get_nested(m, path)]
                counts = Counter(vals); total = len(vals) or 1
                top = counts.most_common(1)[0] if counts else ("—", 0)
                pct = round(top[1] / total * 100)
                color = _P["green"] if top[0] in ("yes", "strong", "high", "clear") else (_P["red"] if top[0] in ("no", "weak", "low", "confused") else _P["amber"])
                st.markdown(f'<div style="display:flex;justify-content:space-between;padding:8px 12px;border-radius:8px;background:#f9fafb;margin-bottom:6px;border-left:3px solid {color};">'
                            f'<span style="font-size:0.82rem;color:#374151;">{_html_mod.escape(lbl)}</span>'
                            f'<span style="font-size:0.82rem;font-weight:700;color:{color};">{_html_mod.escape(top[0])} ({pct}%)</span></div>', unsafe_allow_html=True)

def _render_sec_claims(sec: dict, matrices: list[dict]) -> None:
    claims_field = sec.get("claims_field", "key_claim_reactions")
    all_claims: list[dict] = []
    for m in matrices:
        val = _get_nested(m, claims_field)
        if isinstance(val, list): all_claims.extend(val)
    if not all_claims: st.caption("No claim data."); return
    by_claim: dict[str, list] = defaultdict(list)
    for c in all_claims:
        key = c.get("claim") or "unknown"
        by_claim[key].append(c.get("reaction") or "unknown")
    REACTION_ORDER = ["excited", "reassured", "neutral", "confused", "skeptical", "resistant"]
    REACTION_COLOR = {"excited": _P["green"], "reassured": _P["teal"], "neutral": _P["neutral"],
                      "confused": _P["amber"], "skeptical": _P["orange"], "resistant": _P["red"]}
    for claim, reactions in sorted(by_claim.items()):
        counts = Counter(reactions); total = len(reactions) or 1
        bars_html = "".join(
            f'<span style="display:inline-block;background:{REACTION_COLOR.get(r, _P["neutral"])};color:white;'
            f'font-size:0.6rem;padding:2px 7px;border-radius:12px;margin-right:4px;">{_html_mod.escape(r)} {counts[r]}</span>'
            for r in REACTION_ORDER if counts.get(r)
        )
        st.markdown(f'<div style="padding:10px 14px;border-radius:10px;background:#f9fafb;margin-bottom:8px;">'
                    f'<div style="font-size:0.82rem;font-weight:700;color:#111827;margin-bottom:6px;">{_html_mod.escape(claim.replace("_"," "))}</div>'
                    f'<div>{bars_html}</div></div>', unsafe_allow_html=True)

def _render_sec_taglines(sec: dict, matrices: list[dict]) -> None:
    pref_field = sec.get("preference_field", "tagline_reaction.preferred_tagline")
    verb_field = sec.get("verbatim_field", "tagline_reaction.tagline_verbatim")
    lcol, rcol = st.columns(2)
    with lcol:
        _render_distribution(pref_field, "Tagline Preference", matrices)
    with rcol:
        _render_verbatim_block(verb_field, "Respondent Reactions", matrices, max_items=5)

def _render_sec_adoption_detail(sec: dict, matrices: list[dict]) -> None:
    drivers_field = sec.get("drivers_field"); barriers_field = sec.get("barriers_field")
    lcol, rcol = st.columns(2)
    def _tag_cloud(field: str, label: str, color: str, col) -> None:
        tags: list[str] = []
        for m in matrices:
            val = _get_nested(m, field)
            if isinstance(val, list): tags.extend(str(v) for v in val if v)
            elif val: tags.append(str(val))
        counts = Counter(tags)
        with col:
            st.markdown(f'<div style="font-size:0.7rem;font-weight:800;color:{color};text-transform:uppercase;margin-bottom:10px;">{_html_mod.escape(label)}</div>', unsafe_allow_html=True)
            for tag, cnt in counts.most_common(12):
                st.markdown(f'<div style="display:flex;justify-content:space-between;padding:6px 10px;background:{color}10;border-radius:8px;margin-bottom:5px;">'
                            f'<span style="font-size:0.8rem;color:#374151;">{_html_mod.escape(tag.replace("_"," "))}</span>'
                            f'<span style="font-size:0.75rem;font-weight:700;color:{color};">{cnt}</span></div>', unsafe_allow_html=True)
    if drivers_field: _tag_cloud(drivers_field, "Adoption Drivers", _P["green"], lcol)
    if barriers_field: _tag_cloud(barriers_field, "Adoption Barriers", _P["red"], rcol)

def _render_sec_benchmark(sec: dict, matrices: list[dict]) -> None:
    field = sec.get("field", "benchmark_comparisons")
    bk = sec.get("benchmark_key", "benchmark"); vk = sec.get("verdict_key", "verdict")
    all_bench: list[dict] = []
    for m in matrices:
        val = _get_nested(m, field)
        if isinstance(val, list): all_bench.extend(val)
    if not all_bench: st.caption("No benchmark data."); return
    by_bench: dict[str, list] = defaultdict(list)
    for b in all_bench:
        by_bench[str(b.get(bk) or "other")].append(str(b.get(vk) or "unclear"))
    VERDICT_COLOR = {"this_product_better": _P["green"], "this_product_worse": _P["red"], "parity": _P["amber"], "unclear": _P["neutral"]}
    for bench, verdicts in sorted(by_bench.items()):
        counts = Counter(verdicts); total = len(verdicts) or 1
        top_v = counts.most_common(1)[0][0]
        color = VERDICT_COLOR.get(top_v, _P["neutral"])
        bars = "".join(
            f'<span style="font-size:0.6rem;padding:2px 7px;border-radius:10px;background:{VERDICT_COLOR.get(v,_P["neutral"])};color:white;margin-right:3px;">{_html_mod.escape(v.replace("_"," "))} {counts[v]}</span>'
            for v, _ in counts.most_common()
        )
        st.markdown(f'<div style="padding:10px 14px;border-radius:10px;background:#f9fafb;margin-bottom:8px;border-left:3px solid {color};">'
                    f'<div style="font-size:0.85rem;font-weight:700;color:#111827;margin-bottom:5px;">{_html_mod.escape(bench)}</div>'
                    f'<div>{bars}</div></div>', unsafe_allow_html=True)

def _render_sec_score_grid(sec: dict, matrices: list[dict]) -> None:
    fields = sec.get("fields") or []
    if not fields: return
    cols = st.columns(min(len(fields), 4))
    for i, sf in enumerate(fields[:4]):
        avg = _safe_avg([_get_nested(m, sf.get("path","")) for m in matrices])
        with cols[i]: kpi_card(sf.get("label",""), f"{avg:.1f}" if avg is not None else "—", _BRAND_PAL[i % len(_BRAND_PAL)])

def _render_sec_verbatim_list(sec: dict, matrices: list[dict]) -> None:
    field = sec.get("field", ""); max_items = sec.get("max_items", 10)
    _render_verbatim_block(field, sec.get("title", "Verbatims"), matrices, max_items=max_items)

def _render_sec_portfolio_context(sec: dict, matrices: list[dict]) -> None:
    platforms_field = sec.get("platforms_field"); gold_role_field = sec.get("gold_role_field")
    alloc_fields = sec.get("allocation_fields") or []
    if gold_role_field: _render_distribution(gold_role_field, "Role / Category Behavior", matrices)
    if platforms_field:
        platforms: list[str] = []
        for m in matrices:
            val = _get_nested(m, platforms_field)
            if isinstance(val, list): platforms.extend(str(v) for v in val if v)
            elif val: platforms.append(str(val))
        counts = Counter(platforms); total = len(platforms) or 1
        if counts:
            labels_out = list(counts.keys()); values_out = list(counts.values())
            fig = go.Figure(go.Bar(x=values_out, y=labels_out, orientation="h",
                                   marker_color=[_BRAND_PAL[i % len(_BRAND_PAL)] for i in range(len(labels_out))],
                                   text=[f"{v} ({round(v/total*100)}%)" for v in values_out], textposition="outside"))
            layout = CHART_LAYOUT.copy()
            layout.update({"title": {"text": "Platforms Used", "font": {"size": 13}},
                           "height": max(160, len(labels_out) * 36 + 60), "showlegend": False})
            layout["yaxis"] = {"autorange": "reversed"}
            fig.update_layout(layout)
            st.plotly_chart(fig, use_container_width=True)
    if alloc_fields:
        section_header("Avg Portfolio Allocation", accent=_P["neutral"])
        acols = st.columns(min(len(alloc_fields), 6))
        for i, af in enumerate(alloc_fields[:6]):
            avg = _safe_avg([_get_nested(m, af.get("path","")) for m in matrices])
            with acols[i]: kpi_card(af.get("label",""), f"{avg:.0f}%" if avg is not None else "—", _BRAND_PAL[i % len(_BRAND_PAL)])

_TAB4_RENDERERS = {
    "distribution": _render_sec_distribution,
    "route_detail_grid": _render_sec_route_detail_grid,
    "claims": _render_sec_claims,
    "taglines": _render_sec_taglines,
    "portfolio_context": _render_sec_portfolio_context,
    "adoption_detail": _render_sec_adoption_detail,
    "benchmark": _render_sec_benchmark,
    "score_grid": _render_sec_score_grid,
    "verbatim_list": _render_sec_verbatim_list,
}

# ==============================================================================
# TAB RENDERERS
# ==============================================================================

def _render_tab1_deep_dive(matrices: list[dict], all_matrices: list[dict], ui_config: dict, proj_id: str, insight_cache_path: Path, call_openrouter_fn: Callable, study_context: str, brief: dict = None) -> None:
    seg_key = ui_config.get("segment_key", "segment"); entity_label = ui_config.get("entity_label", "Segment")
    kpi_fields = ui_config.get("kpi_fields") or []

    # Study brief card (fixed, always shown if brief exists)
    if brief:
        _render_study_brief_card(ui_config, brief)

    entities = sorted({str(_get_nested(m, f"respondent.{seg_key}") or _get_nested(m, seg_key) or "—") for m in matrices if _get_nested(m, f"respondent.{seg_key}") or _get_nested(m, seg_key)})
    if not entities: return
    sel_entity = st.selectbox(f"Select {entity_label}", ["All"] + entities, key="tab1_entity_sel")
    view = matrices if sel_entity == "All" else [m for m in matrices if str(_get_nested(m, f"respondent.{seg_key}") or _get_nested(m, seg_key) or "") == sel_entity]

    # Fixed KPI strip: universal metrics
    n = len(view)
    promoters = sum(1 for m in view if (m.get("nps_signal") or "").lower() == "promoter")
    pos_res = sum(1 for m in view if (m.get("emotional_resolution") or "").lower() == "positive")
    all_pp_count = sum(len(m.get("pain_points") or []) for m in view)
    asp_gaps_count = sum(len(m.get("aspiration_reality_gaps") or []) for m in view)
    fixed_kpis = [
        ("Interviews", str(n), _P["teal"]),
        ("Pain Points", str(all_pp_count), _P["red"]),
        ("NPS Promoters", f"{promoters}/{n}", _P["green"]),
        ("Positive End", f"{round(pos_res/n*100) if n else 0}%", _P["teal"]),
    ]
    if asp_gaps_count:
        fixed_kpis.append(("Aspiration Gaps", str(asp_gaps_count), _P["amber"]))
    # Custom KPI fields from ui_config on top of fixed
    extra_kpis = [(kf.get("label", ""), _compute_kpi_value(kf, view), _P.get(kf.get("color", "teal"), _P["teal"])) for kf in kpi_fields]
    all_kpis = fixed_kpis + extra_kpis
    kcols = st.columns(len(all_kpis))
    for col, (lbl, val, color) in zip(kcols, all_kpis):
        with col: kpi_card(lbl, val, color)

    st.markdown("<div style='margin:16px 0;'></div>", unsafe_allow_html=True)

    # Signal scores — always computed from universal fields
    section_header("Study Health Signals", accent=_P["purple"])
    _render_signal_scores(view, ui_config)

    st.markdown("<div style='margin:16px 0;'></div>", unsafe_allow_html=True)
    lcol, rcol = st.columns(2)
    with lcol:
        section_header("Top Pain Points", accent=_P["red"])
        all_pp = sorted([(pp, str(_get_nested(m, f"respondent.{seg_key}") or "?")) for m in view for pp in (m.get("pain_points") or [])], key=lambda x: {"critical":0,"high":1,"medium":2,"low":3}.get((x[0].get("severity") or "").lower(), 4))
        for pp, seg in all_pp[:8]:
            sev = (pp.get("severity") or "low").lower(); sc = _sev_color(sev)
            st.markdown(f'<div style="border:1px solid {sc}20;border-radius:12px;padding:14px;margin-bottom:10px;background:white;"><div style="display:flex;justify-content:space-between;margin-bottom:6px;"><span style="font-size:0.6rem;font-weight:900;background:{sc};color:white;padding:3px 9px;border-radius:20px;text-transform:uppercase;">{sev}</span><span style="font-size:0.65rem;color:#9ca3af;font-weight:600;">{_html_mod.escape(seg)}</span></div><div style="font-size:0.9rem;font-weight:700;color:#111827;margin-bottom:5px;">{_html_mod.escape(pp.get("issue_description",""))}</div></div>', unsafe_allow_html=True)
    with rcol:
        section_header("Key Distributions")
        for d in (ui_config.get("tab1_distributions") or []): _render_distribution(d.get("path",""), d.get("label",""), view)
        _render_verbatim_block("tab1_verbatim_fields", "", view)  # silent if missing
        for vf in (ui_config.get("tab1_verbatim_fields") or []):
            _render_verbatim_block(vf.get("path",""), vf.get("label",""), view, max_items=3)

    # AI Synthesis Card
    if call_openrouter_fn and insight_cache_path and view:
        section_header("AI Synthesis", accent=_P["green"])
        cache_key = f"{proj_id}_{sel_entity}_v2"
        cache = json.loads(insight_cache_path.read_text(encoding="utf-8")) if insight_cache_path.exists() else {}
        if cache.get(cache_key):
            st.markdown(f'<div style="background:radial-gradient(circle at top right, #1e293b, #0f172a);border-radius:24px;padding:32px;border:1px solid rgba(255,255,255,0.1);color:white;"><div style="font-size:0.75rem;text-transform:uppercase;letter-spacing:0.15em;color:{_NEON};font-weight:800;margin-bottom:15px;">Intelligence Synthesis</div><div style="font-size:0.9rem;line-height:1.8;color:rgba(255,255,255,0.9);">{_md_bold(_html_mod.escape(cache[cache_key]))}</div></div>', unsafe_allow_html=True)
            if st.button("Regenerate"): cache.pop(cache_key); insight_cache_path.write_text(json.dumps(cache)); st.rerun()
        else:
            if st.button("✨ Generate AI Synthesis", type="primary"):
                with st.spinner("Analyzing..."):
                    res = call_openrouter_fn(f"Synthesize findings for {sel_entity} project {proj_id}.", "Research analyst bot.")
                    if res: cache[cache_key] = res; insight_cache_path.write_text(json.dumps(cache)); st.rerun()

def _render_tab2_pain(matrices: list[dict], ui_config: dict) -> None:
    section_header("Pain Points & Barriers")
    n = len(matrices) or 1
    all_pp = [pp for m in matrices for pp in (m.get("pain_points") or []) if isinstance(pp, dict)]
    all_barriers = [b for m in matrices for b in (m.get("adoption", {}).get("barriers") if isinstance(m.get("adoption"), dict) else (m.get("barriers") or [])) if b]

    if not all_pp:
        st.info("No pain points extracted. Run extraction pipeline first.")
        return

    # Top row: severity counts
    sev_counts = Counter((pp.get("severity") or "unknown").lower() for pp in all_pp)
    sev_cols = st.columns(4)
    for col, sev, color in zip(sev_cols, ["critical", "high", "medium", "low"], [_P["red"], _P["orange"], _P["amber"], _P["green"]]):
        with col: kpi_card(sev.title(), str(sev_counts.get(sev, 0)), color)

    st.markdown("<div style='margin:16px 0;'></div>", unsafe_allow_html=True)

    # Full pain point list with verbatims, sorted by severity
    lcol, rcol = st.columns([3, 2])
    with lcol:
        section_header("Pain Point Detail", accent=_P["red"])
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
        sorted_pp = sorted(all_pp, key=lambda p: severity_order.get((p.get("severity") or "unknown").lower(), 4))
        for pp in sorted_pp[:20]:
            sev = (pp.get("severity") or "low").lower(); sc = _sev_color(sev)
            area = (pp.get("product_area") or pp.get("area") or "").replace("_", " ")
            verbatim = pp.get("verbatim_quote") or ""
            verb_html = ""
            if verbatim:
                verb_html = (f'<div style="margin-top:6px;font-size:0.78rem;font-style:italic;color:#6b7280;'
                             f'border-left:2px solid {sc}40;padding-left:8px;">'
                             f'&ldquo;{_html_mod.escape(str(verbatim)[:200])}&rdquo;</div>')
            area_badge = (f'<span style="font-size:0.58rem;background:#f3f4f6;color:#6b7280;'
                          f'padding:2px 7px;border-radius:10px;margin-left:6px;">{_html_mod.escape(area)}</span>') if area else ""
            st.markdown(
                f'<div style="border:1px solid {sc}20;border-radius:12px;padding:12px 14px;margin-bottom:10px;background:white;">'
                f'<div style="display:flex;align-items:center;margin-bottom:6px;">'
                f'<span style="font-size:0.6rem;font-weight:900;background:{sc};color:white;padding:3px 9px;border-radius:20px;text-transform:uppercase;">{sev}</span>'
                f'{area_badge}</div>'
                f'<div style="font-size:0.88rem;font-weight:700;color:#111827;">{_html_mod.escape(pp.get("issue_description",""))}</div>'
                f'{verb_html}</div>',
                unsafe_allow_html=True,
            )

    with rcol:
        # Pain area distribution
        _render_distribution("pain_points.product_area", "Pain by Area", matrices)

        # Self-blame vs product-blame split
        self_blame_count = sum(len(m.get("self_blame_instances") or []) for m in matrices)
        prod_blame_count = sum(len(m.get("product_blame_instances") or []) for m in matrices)
        if self_blame_count + prod_blame_count > 0:
            section_header("Blame Attribution", accent=_P["purple"])
            total_b = self_blame_count + prod_blame_count
            sb_pct = round(self_blame_count / total_b * 100)
            pb_pct = 100 - sb_pct
            st.markdown(
                f'<div style="background:#f9fafb;border-radius:12px;padding:14px;margin-bottom:12px;">'
                f'<div style="display:flex;margin-bottom:8px;">'
                f'<div style="flex:{sb_pct};background:{_P["purple"]};height:12px;border-radius:6px 0 0 6px;"></div>'
                f'<div style="flex:{pb_pct};background:{_P["red"]};height:12px;border-radius:0 6px 6px 0;"></div></div>'
                f'<div style="display:flex;justify-content:space-between;font-size:0.72rem;">'
                f'<span style="color:{_P["purple"]};font-weight:700;">Self-blame {sb_pct}%</span>'
                f'<span style="color:{_P["red"]};font-weight:700;">Product-blame {pb_pct}%</span></div>'
                f'<div style="font-size:0.65rem;color:#9ca3af;margin-top:6px;">'
                f'Self-blame = commercial headroom. High product-blame = urgent fix signal.</div></div>',
                unsafe_allow_html=True,
            )

        # Adoption barriers if available
        if all_barriers:
            section_header("Adoption Barriers", accent=_P["orange"])
            barrier_counts = Counter(str(b).replace("_", " ") for b in all_barriers)
            for b, cnt in barrier_counts.most_common(8):
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;padding:6px 10px;'
                    f'background:{_P["orange"]}10;border-radius:8px;margin-bottom:5px;">'
                    f'<span style="font-size:0.8rem;color:#374151;">{_html_mod.escape(b)}</span>'
                    f'<span style="font-size:0.75rem;font-weight:700;color:{_P["orange"]};">{cnt}</span></div>',
                    unsafe_allow_html=True,
                )


def _collect_list_field(matrices: list[dict], field: str) -> Counter:
    """Collect string items from a list field across all matrices."""
    counts: Counter = Counter()
    for m in matrices:
        val = m.get(field)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    counts[item.strip()] += 1
                elif isinstance(item, dict):
                    # Try common keys for label
                    label = item.get("driver") or item.get("feature") or item.get("job") or item.get("need") or str(item)
                    if label: counts[str(label).strip()] += 1
    return counts


def _render_theme_bar(counts: Counter, title: str, color: str, max_show: int = 10) -> None:
    if not counts: return
    section_header(title, accent=color)
    total = max(sum(counts.values()), 1)
    for label, cnt in counts.most_common(max_show):
        pct = round(cnt / total * 100)
        clean_label = label.replace("_", " ").strip()
        # Skip if looks like transcript fragment (>80 chars or has repeated punctuation)
        if len(clean_label) > 80: clean_label = clean_label[:77] + "..."
        st.markdown(
            f'<div style="margin-bottom:7px;">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:2px;">'
            f'<span style="font-size:0.8rem;color:#374151;">{_html_mod.escape(clean_label)}</span>'
            f'<span style="font-size:0.75rem;color:{color};font-weight:700;">{cnt}</span></div>'
            f'<div style="background:#f1f5f9;border-radius:3px;height:5px;">'
            f'<div style="width:{min(pct*2,100)}%;background:{color};height:100%;border-radius:3px;"></div></div>'
            f'</div>', unsafe_allow_html=True)


def _render_tab3_themes(matrices: list[dict], ui_config: dict) -> None:
    section_header("Themes & Narratives")

    # Collect structured intelligence fields (exist in ethnographic projects like Mixer)
    decision_drivers = _collect_list_field(matrices, "decision_drivers")
    feature_priorities = _collect_list_field(matrices, "feature_priorities")
    jobs = _collect_list_field(matrices, "jobs_to_be_done")
    unspoken = _collect_list_field(matrices, "unspoken_needs")

    # narrative_tags: only use as supplement — filter to reference framework tags
    ref_tags: list[str] = ui_config.get("tab3_reference_tags") or []
    raw_tags: Counter = Counter()
    for m in matrices:
        for t in (m.get("narrative_tags") or []):
            if isinstance(t, str) and t.strip() and len(t) < 70 and all(ord(c) < 128 for c in t):
                raw_tags[t.strip()] += 1

    # Tags aligned to framework: only show tags that are in ref_tags or are clean English
    if ref_tags:
        framework_tags = Counter({t: raw_tags[t] for t in ref_tags if raw_tags.get(t, 0) > 0})
        orphan_tags = Counter({t: c for t, c in raw_tags.items() if t not in ref_tags and c >= 2})
    else:
        framework_tags = raw_tags
        orphan_tags: Counter = Counter()

    has_structured = any([decision_drivers, feature_priorities, jobs, unspoken])
    has_tags = bool(framework_tags or orphan_tags)

    if not has_structured and not has_tags:
        st.info("No theme data extracted yet. Run the full extraction pipeline to populate this view.")
        return

    # Layout: structured fields get priority
    if has_structured:
        lcol, rcol = st.columns(2)
        with lcol:
            if decision_drivers: _render_theme_bar(decision_drivers, "Decision Drivers", _P["orange"])
            if jobs: _render_theme_bar(jobs, "Jobs to Be Done", _P["teal"])
        with rcol:
            if feature_priorities: _render_theme_bar(feature_priorities, "Feature Priorities", _P["blue"])
            if unspoken: _render_theme_bar(unspoken, "Unspoken Needs", _P["purple"])

    # Aspiration-Reality Gaps: show as distinct theme block
    all_gaps = [gap for m in matrices for gap in (m.get("aspiration_reality_gaps") or []) if gap]
    if all_gaps:
        section_header("Aspiration-Reality Gaps", accent=_P["amber"])
        gap_by_opp = sorted(
            [g for g in all_gaps if isinstance(g, dict)],
            key=lambda g: {"high": 0, "medium": 1, "low": 2}.get((g.get("opportunity") or "low").lower(), 3)
        )
        for gap in gap_by_opp[:8]:
            opp = (gap.get("opportunity") or "low").lower()
            opp_color = _P["green"] if opp == "high" else (_P["amber"] if opp == "medium" else _P["neutral"])
            gap_text = gap.get("gap") or gap.get("description") or str(gap)
            if len(gap_text) > 120: gap_text = gap_text[:117] + "..."
            verbatim = gap.get("verbatim_quote") or gap.get("verbatim") or ""
            verbatim_html = ""
            if verbatim:
                verbatim_html = (f'<div style="margin-top:6px;font-size:0.78rem;font-style:italic;color:#6b7280;">'
                                 f'&ldquo;{_html_mod.escape(str(verbatim)[:200])}&rdquo;</div>')
            st.markdown(
                f'<div style="border-left:3px solid {opp_color};padding:10px 14px;background:#f9fafb;'
                f'border-radius:0 10px 10px 0;margin-bottom:8px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
                f'<div style="font-size:0.85rem;font-weight:600;color:#111827;flex:1;">{_html_mod.escape(gap_text)}</div>'
                f'<span style="font-size:0.58rem;font-weight:800;background:{opp_color};color:white;'
                f'padding:2px 8px;border-radius:10px;text-transform:uppercase;margin-left:8px;white-space:nowrap;">{opp} opp</span></div>'
                f'{verbatim_html}</div>',
                unsafe_allow_html=True,
            )

    # Reference framework alignment
    if ref_tags and framework_tags:
        st.markdown("<div style='margin:16px 0;'></div>", unsafe_allow_html=True)
        missing_ref = [t for t in ref_tags if not raw_tags.get(t)]
        coverage = len(framework_tags) / len(ref_tags) * 100 if ref_tags else 100
        lcol2, rcol2 = st.columns([1, 3])
        with lcol2:
            kpi_card("Framework Coverage", f"{coverage:.0f}%", _P["green"] if coverage >= 70 else _P["amber"])
        with rcol2:
            section_header("Theme Framework Alignment", accent=_P["teal"])
            for t, cnt in sorted(framework_tags.items(), key=lambda x: -x[1]):
                st.markdown(
                    f'<div style="display:inline-block;background:{_P["teal"]}15;color:#0f766e;'
                    f'font-size:0.73rem;padding:3px 10px;border-radius:12px;margin:3px;">'
                    f'{_html_mod.escape(t.replace("_"," "))} ({cnt})</div>',
                    unsafe_allow_html=True,
                )
            if missing_ref:
                st.markdown(f'<div style="font-size:0.65rem;color:#9ca3af;margin-top:10px;">Not emerged: '
                            f'{", ".join(t.replace("_"," ") for t in missing_ref[:10])}</div>', unsafe_allow_html=True)


def _render_tab4_analysis(matrices: list[dict], ui_config: dict) -> None:
    tab4_sections = ui_config.get("tab4_sections") or []
    if not tab4_sections:
        st.info("No analysis sections defined. Add tab4_sections to ui_config.json.")
        return
    for sec in tab4_sections:
        stype = sec.get("type", "")
        title = sec.get("title", stype.replace("_", " ").title())
        with st.expander(title, expanded=True):
            renderer = _TAB4_RENDERERS.get(stype)
            if renderer:
                renderer(sec, matrices)
            else:
                st.caption(f"Unknown section type: {stype}")


def _render_tab5_health(matrices: list[dict], ui_config: dict) -> None:
    section_header("Study Health")
    # Quality scores from _quality_score
    q_scores = [m.get("_quality_score") for m in matrices if m.get("_quality_score") is not None]
    verified_rates = []
    for m in matrices:
        v = m.get("_verification") or {}
        total_c = v.get("total_quotes_checked", 0)
        verified_c = v.get("verified", 0)
        if total_c: verified_rates.append(verified_c / total_c * 100)

    kcols = st.columns(4)
    with kcols[0]: kpi_card("Interviews", str(len(matrices)), _P["teal"])
    if q_scores:
        avg_q = sum(q_scores) / len(q_scores)
        with kcols[1]: kpi_card("Avg Quality Score", f"{avg_q:.0f}/100", _P["green"] if avg_q >= 80 else _P["amber"])
        excellent = sum(1 for s in q_scores if s >= 80)
        with kcols[2]: kpi_card("Excellent (80+)", f"{excellent}/{len(q_scores)}", _P["green"])
    if verified_rates:
        avg_vr = sum(verified_rates) / len(verified_rates)
        with kcols[3]: kpi_card("Verbatim Verified", f"{avg_vr:.0f}%", _P["teal"])

    st.markdown("<div style='margin:16px 0;'></div>", unsafe_allow_html=True)

    # Quality score distribution
    if q_scores:
        lcol, rcol = st.columns(2)
        with lcol:
            section_header("Quality Score Distribution", accent=_P["teal"])
            buckets = {"Excellent (80+)": 0, "Good (60-79)": 0, "Needs Review (<60)": 0}
            for s in q_scores:
                if s >= 80: buckets["Excellent (80+)"] += 1
                elif s >= 60: buckets["Good (60-79)"] += 1
                else: buckets["Needs Review (<60)"] += 1
            fig = go.Figure(go.Bar(x=list(buckets.values()), y=list(buckets.keys()), orientation="h",
                                   marker_color=[_P["green"], _P["amber"], _P["red"]],
                                   text=[str(v) for v in buckets.values()], textposition="outside"))
            layout = CHART_LAYOUT.copy()
            layout.update({"height": 200, "showlegend": False, "margin": {"l": 160}})
            layout["yaxis"] = {"autorange": "reversed"}
            fig.update_layout(layout)
            st.plotly_chart(fig, use_container_width=True)
        with rcol:
            section_header("Per-Interview Scores", accent=_P["teal"])
            doc_ids = [m.get("doc_id", f"#{i}") for i, m in enumerate(matrices) if m.get("_quality_score") is not None]
            scores = q_scores
            fig2 = go.Figure(go.Bar(x=scores, y=doc_ids, orientation="h",
                                    marker_color=[_P["green"] if s >= 80 else (_P["amber"] if s >= 60 else _P["red"]) for s in scores],
                                    text=[f"{s:.0f}" for s in scores], textposition="outside"))
            layout2 = CHART_LAYOUT.copy()
            layout2.update({"height": max(200, len(doc_ids) * 28 + 60), "showlegend": False})
            layout2["yaxis"] = {"autorange": "reversed"}
            fig2.update_layout(layout2)
            st.plotly_chart(fig2, use_container_width=True)

    # Study-specific trust/health fields from ui_config
    trust_field = ui_config.get("tab5_trust_field")
    trust_verb = ui_config.get("tab5_trust_verbatim")
    trust_builders = ui_config.get("tab5_trust_builders")
    tab5_dists = ui_config.get("tab5_distributions") or []
    if trust_field or tab5_dists:
        section_header("Study-Specific Health Signals", accent=_P["purple"])
        if trust_field:
            lcol, rcol = st.columns(2)
            with lcol: _render_distribution(trust_field, trust_field.split(".")[-1].replace("_", " ").title(), matrices)
            if trust_verb:
                with rcol: _render_verbatim_block(trust_verb, "Health Signal Verbatims", matrices, max_items=4, accent=_P["purple"])
        if trust_builders:
            section_header("Trust / Signal Builders Cited", accent=_P["teal"])
            builders: list[str] = []
            for m in matrices:
                val = _get_nested(m, trust_builders)
                if isinstance(val, list): builders.extend(str(v) for v in val if v)
            counts = Counter(builders)
            if counts:
                items = counts.most_common(15)
                fig3 = go.Figure(go.Bar(x=[v for _, v in items], y=[k for k, _ in items], orientation="h",
                                        marker_color=[_BRAND_PAL[i % len(_BRAND_PAL)] for i in range(len(items))],
                                        text=[str(v) for _, v in items], textposition="outside"))
                layout3 = CHART_LAYOUT.copy()
                layout3.update({"height": max(160, len(items) * 32 + 60), "showlegend": False})
                layout3["yaxis"] = {"autorange": "reversed"}
                fig3.update_layout(layout3)
                st.plotly_chart(fig3, use_container_width=True)
        for dist in tab5_dists:
            _render_distribution(dist.get("path", ""), dist.get("label", ""), matrices)


def _render_tab6_search(matrices: list[dict]) -> None:
    section_header("Verbatim Search")
    query = st.text_input("Search across all passages and pain points", placeholder="e.g. trust, gold, yield...", key="tab6_search_q")
    if not query: st.caption("Enter a search term above."); return
    q_lower = query.lower()
    results: list[dict] = []
    for m in matrices:
        doc_id = m.get("doc_id", "?")
        seg = str(m.get("respondent", {}).get("segment") or "")
        city = str(m.get("respondent", {}).get("city") or "")
        for pp in (m.get("pain_points") or []):
            text = str(pp.get("verbatim_quote") or "")
            if q_lower in text.lower():
                results.append({"doc": doc_id, "ctx": f"{seg} / {city}", "type": f'Pain [{pp.get("severity","")}]',
                                 "text": text, "color": _sev_color(pp.get("severity", "low"))})
        for p in (m.get("all_passages") or []):
            text = str(p.get("content") or "")
            if q_lower in text.lower():
                sent = p.get("sentiment", "neutral")
                sc, _ = _sent_color(sent)
                results.append({"doc": doc_id, "ctx": f"{seg} / {city}", "type": f'Passage [{p.get("topic","")}]',
                                 "text": text, "color": sc})
    st.markdown(f'<div style="font-size:0.8rem;color:#6b7280;margin-bottom:12px;">{len(results)} result(s) for <b>{_html_mod.escape(query)}</b></div>', unsafe_allow_html=True)
    if not results: st.info("No matches found."); return
    for r in results[:50]:
        lo = r["text"].lower(); idx = lo.find(q_lower)
        if idx >= 0:
            pre = _html_mod.escape(r["text"][max(0, idx-40):idx])
            match = _html_mod.escape(r["text"][idx:idx+len(query)])
            post = _html_mod.escape(r["text"][idx+len(query):idx+len(query)+80])
            snippet = f'{pre}<mark style="background:#fef08a;padding:0 2px;">{match}</mark>{post}'
        else:
            snippet = _html_mod.escape(r["text"][:160])
        st.markdown(f'<div style="border-left:3px solid {r["color"]};padding:10px 14px;background:#f9fafb;border-radius:0 10px 10px 0;margin-bottom:10px;">'
                    f'<div style="display:flex;gap:8px;margin-bottom:6px;">'
                    f'<span style="font-size:0.65rem;font-weight:800;background:{r["color"]};color:white;padding:2px 8px;border-radius:10px;">{_html_mod.escape(r["type"])}</span>'
                    f'<span style="font-size:0.65rem;color:#6b7280;">{_html_mod.escape(r["doc"])} &middot; {_html_mod.escape(r["ctx"])}</span></div>'
                    f'<div style="font-size:0.85rem;color:#374151;line-height:1.6;font-style:italic;">...{snippet}...</div></div>', unsafe_allow_html=True)


def _render_tab7_inspector(matrices: list[dict], ui_config: dict, proj: dict) -> None:
    seg_key = ui_config.get("segment_key", "segment"); proj_id = proj.get("id", ""); abs_paths = proj.get("abs_paths", {})
    open_key = f"tab7_open_{proj_id}"
    if st.session_state.get(open_key):
        m = next((x for x in matrices if x.get("doc_id") == st.session_state[open_key]), None)
        if m:
            if st.button("← Back"): st.session_state[open_key] = None; st.rerun()
            st.markdown(f'<div style="background:#0f172a;border-radius:16px;padding:24px;color:white;margin-bottom:20px;"><div style="font-size:1.6rem;font-weight:900;">{m.get("doc_id","")}</div></div>', unsafe_allow_html=True)
            raw_p = abs_paths.get("transcripts") / "processed" / f"{m.get('doc_id')}.md"
            if raw_p.exists():
                text = raw_p.read_text(encoding="utf-8", errors="replace")
                st.markdown('<div style="height:600px;overflow-y:auto;background:white;border:1px solid #e2e8f0;border-radius:12px;padding:20px;line-height:1.7;">' + _html_mod.escape(text).replace("\n", "<br>") + '</div>', unsafe_allow_html=True)
            return
    rows = [matrices[i:i + 3] for i in range(0, len(matrices), 3)]
    for row in rows:
        cols = st.columns(3)
        for i, m in enumerate(row):
            with cols[i]:
                st.markdown(f'<div style="border-top:4px solid {_BRAND_PAL[i % 5]};border-radius:8px;padding:16px;background:white;box-shadow:0 4px 6px rgba(0,0,0,0.04);margin-bottom:15px;"><div style="font-size:0.95rem;font-weight:700;">{m.get("doc_id","")}</div><div style="font-size:0.75rem;color:#6b7280;">Segment: {str(_get_nested(m, seg_key))}</div></div>', unsafe_allow_html=True)
                if st.button("Read Interview", key=f"read_{m.get('doc_id')}", use_container_width=True): st.session_state[open_key] = m.get("doc_id"); st.rerun()

def _render_findings_report(proj_id: str, rs_path: Path, findings_dir: Path, proj_dn: str, base_path: Path, call_or_key: Any, active_filters: dict) -> None:
    findings_dir.mkdir(parents=True, exist_ok=True); prefix = ""
    if active_filters:
        seg = active_filters.get("respondent.segment") or active_filters.get("segment") or active_filters.get("respondent.brand_owned")
        city = active_filters.get("respondent.city") or active_filters.get("city")
        if seg: prefix += f"{seg}_"; 
        if city: prefix += f"{city}_"
    idx_p = findings_dir / f"{prefix}index.json"
    if not idx_p.exists():
        st.info(f"No findings for {prefix.rstrip('_') or 'Full Study'}.")
        if st.button(f"Generate Findings for {prefix.rstrip('_') or 'Full Study'}", type="primary"):
            args = [sys.executable, str(base_path/"skills/findings_generator.py"), "--project", proj_id]
            if active_filters:
                if seg: args.extend(["--segment", seg])
                if city: args.extend(["--city", city])
            with st.spinner("Generating..."):
                res = subprocess.run(args, capture_output=True, text=True, cwd=str(base_path.parent))
                if res.returncode == 0: st.success("Done!"); st.rerun()
                else: st.error("Failed."); st.code(res.stderr)
        return
    idx = json.loads(idx_p.read_text(encoding="utf-8"))
    sections = idx if isinstance(idx, list) else idx.get("sections", [])
    section_header("Strategic Findings", f"{prefix.rstrip('_') or 'Full Study'}")
    for sec in sections:
        fpath = findings_dir / f"{prefix}{sec.get('id','')}.json"
        with st.expander(f"📋 {sec.get('title','')}"):
            if fpath.exists():
                fd = json.loads(fpath.read_text(encoding="utf-8"))
                st.markdown(f'<div style="font-size:0.9rem;line-height:1.75;color:#374151;">{_md_bold(_html_mod.escape(fd.get("finding_text","")))}</div>', unsafe_allow_html=True)
            else: st.caption("Section empty.")

# ==============================================================================
# ENTRY POINT
# ==============================================================================

def render_generic_project(proj: dict, ui_config: dict, base_path: Path, call_openrouter_fn: Optional[Callable] = None) -> None:
    proj_id = proj.get("id", ""); proj_dn = proj.get("display_name", proj_id)
    page_banner(proj_dn, ui_config.get("study_context", "Intelligence Engine"), eyebrow="ACTIVE PROJECT")
    m_dir = proj.get("abs_paths", {}).get("matrices")
    all_m = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(m_dir.glob("*_matrix.json"))] if m_dir and m_dir.exists() else []
    if not all_m: empty_state("No data found.", icon="📂"); return
    # Quality gate: "critical"-quality extractions (verbatim fidelity check failed badly) are
    # excluded from every chart's aggregate data, not just flagged in the Health tab below —
    # previously advisory only.
    from infoleap.skills.project_extractor import gate_matrices_by_quality
    all_m, _excluded_m = gate_matrices_by_quality(all_m)
    if _excluded_m:
        st.warning(
            f"⚠ {len(_excluded_m)} respondent(s) excluded from every chart below — "
            f"critical-quality extraction (verbatim fidelity check failed badly): "
            + ", ".join(m.get("doc_id", "?") for m in _excluded_m)
            + ". Re-run extraction for these files in Extraction Studio."
        )
    if not all_m: empty_state("All matrices are critical-quality — nothing to render. Re-run "
                              "extraction in Extraction Studio.", icon="📂"); return
    f_fields = ui_config.get("filter_fields") or []
    if f_fields: matrices, active_filters = _render_filter_bar(all_m, f_fields)
    else: matrices = all_m; active_filters = {}
    st.markdown(f'<div style="font-size:0.75rem;color:#6b7280;margin-bottom:20px;">Showing <b>{len(matrices)}</b> of {len(all_m)} interviews</div>', unsafe_allow_html=True)
    _brief = _load_study_brief(proj, base_path)
    tabs = st.tabs(["🔍 Deep Dive", "⚠ Pain Points", "🏷 Themes", f"📊 {ui_config.get('tab4_label','Analysis')}", "🔒 Health", "💬 Search", "🔬 Inspector"])
    with tabs[0]: _render_tab1_deep_dive(matrices, all_m, ui_config, proj_id, base_path/"data/projects"/proj_id/"insights_cache.json", call_openrouter_fn, ui_config.get("study_context",""), brief=_brief)
    with tabs[1]: _render_tab2_pain(matrices, ui_config)
    with tabs[2]: _render_tab3_themes(matrices, ui_config)
    with tabs[3]: _render_tab4_analysis(matrices, ui_config)
    with tabs[4]: _render_tab5_health(matrices, ui_config)
    with tabs[5]: _render_tab6_search(matrices)
    with tabs[6]: _render_tab7_inspector(matrices, ui_config, proj)
    st.markdown("---")
    _render_findings_report(proj_id, proj.get("abs_paths",{}).get("source_docs")/"report_structure.json", m_dir.parent/"findings", proj_dn, base_path, call_openrouter_fn, active_filters)
