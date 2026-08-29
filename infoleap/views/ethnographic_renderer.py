"""
ethnographic_renderer.py — Generic renderer for ethnographic / U&A study dashboards.

Entry point:
    render_ethnographic(proj, base_path, call_openrouter_fn)

Works with any project whose registry entry has study_type = "ethnographic".
All insights computed from matrices — zero hardcoded narrative.

Current project: Crompton FMCD — Mixer & Food Processor Study (mixer)
Matrix schema: ethnographic_appliance (233 IDIs, 8 brands, 18 cities)

6 tabs:
    1  Consumer Profiles    — segments by brand_owned, cook identity, journey stage
    2  Brand Landscape      — relationship health, loyalty, advocacy, blame attribution
    3  Pain Points          — severity matrix, workarounds, competitor threat signals
    4  Aspiration & Need    — aspiration gaps, JTBD, unspoken needs, peak moments
    5  Purchase Journey     — decision drivers, social dynamics, cultural context
    6  Study Report         — AI-generated downloadable report
"""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path
from typing import Callable

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from infoleap.utils.ui_styles import (
    COLORS, CHART_LAYOUT, kpi_card, empty_state, section_header,
)
from infoleap.config.project_1 import MIXER_SECTION_LABELS as _ETH_SIGNAL_MAP

_P = COLORS

_SENT_COLOR = {
    "positive":   _P.get("green",   "#22c55e"),
    "negative":   _P.get("red",     "#ef4444"),
    "neutral":    _P.get("neutral", "#9ca3af"),
    "ambivalent": _P.get("amber",   "#f59e0b"),
    "mixed":      _P.get("amber",   "#f59e0b"),
}

_SEV_COLOR = {
    "critical": "#ef4444",
    "high":     "#f97316",
    "medium":   "#fbbf24",
    "low":      "#86efac",
}

_PALETTE = px.colors.qualitative.Set2


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get(obj, path: str, default=None):
    for key in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
            if obj is None:
                return default
        else:
            return default
    return obj if obj is not None else default


def _avg(vals):
    nums = [float(v) for v in vals if v is not None and str(v).replace(".", "", 1).lstrip("-").isdigit()]
    return round(sum(nums) / len(nums), 1) if nums else 0.0


def _count_field(matrices, path: str) -> dict:
    c = Counter()
    for m in matrices:
        v = _get(m, path)
        if v:
            c[str(v)] += 1
    return dict(c)


def _count_nested(matrices, list_field: str, key: str) -> dict:
    """Count values from list-of-dict field."""
    c = Counter()
    for m in matrices:
        items = _get(m, list_field, []) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                v = item.get(key)
                if v:
                    c[str(v)] += 1
    return dict(c)


def _seg_matrices(matrices, segment_val: str, seg_key: str = "respondent.brand_owned") -> list:
    return [m for m in matrices if (_get(m, seg_key) or "").lower() == segment_val.lower()]


def _filter_matrices(matrices, filters: dict) -> list:
    out = []
    for m in matrices:
        keep = True
        for path, vals in filters.items():
            if not vals:
                continue
            v = str(_get(m, path) or "")
            if v not in vals:
                keep = False
                break
        if keep:
            out.append(m)
    return out


def _all_passages(matrices, topics: list[str] | None = None,
                  pain_only: bool = False, decision_only: bool = False) -> list[dict]:
    out = []
    for m in matrices:
        seg = _get(m, "respondent.brand_owned", "")
        city = _get(m, "respondent.city", "")
        for p in (_get(m, "all_passages", []) or []):
            if not isinstance(p, dict) or not p.get("content"):
                continue
            if topics:
                topic_val = str(p.get("topic", "")).lower()
                if not any(t.lower() in topic_val for t in topics):
                    continue
            if pain_only and not p.get("pain_point"):
                continue
            if decision_only and not p.get("decision_signal"):
                continue
            out.append({**p, "_brand": seg, "_city": city})
    return out


def _pain_severity_matrix(matrices) -> dict:
    """product_area -> {severity -> count}"""
    data: dict = {}
    for m in matrices:
        for p in (m.get("pain_points") or []):
            if not isinstance(p, dict):
                continue
            area = str(p.get("product_area") or "unspecified").strip().lower()
            sev = str(p.get("severity") or "medium").strip().lower()
            data.setdefault(area, {})
            data[area][sev] = data[area].get(sev, 0) + 1
    return data


def _aspiration_gaps(matrices) -> list[dict]:
    out = []
    for m in matrices:
        brand = _get(m, "respondent.brand_owned", "")
        for item in (m.get("aspiration_reality_gaps") or []):
            if isinstance(item, dict) and item.get("aspiration"):
                out.append({
                    "aspiration":             item.get("aspiration", ""),
                    "current_reality":        item.get("current_reality", ""),
                    "commercial_opportunity": item.get("commercial_opportunity", "medium"),
                    "emotional_charge":       item.get("emotional_charge", "medium"),
                    "verbatim_quote":         item.get("verbatim_quote", ""),
                    "brand":                  brand,
                })
    return out


def _jtbd_list(matrices) -> list[str]:
    out = []
    for m in matrices:
        for j in (m.get("jobs_to_be_done") or []):
            if isinstance(j, str) and j.strip():
                out.append(j.strip())
    return out


def _unspoken_needs_list(matrices) -> list[str]:
    out = []
    for m in matrices:
        for n in (m.get("unspoken_needs") or []):
            if isinstance(n, str) and n.strip():
                out.append(n.strip())
    return out


def _blame_counts(matrices) -> tuple[dict, dict]:
    """Returns (self_blame_by_brand, product_blame_by_brand)."""
    self_b: dict = {}
    prod_b: dict = {}
    for m in matrices:
        brand = _get(m, "respondent.brand_owned", "Unknown")
        self_b[brand] = self_b.get(brand, 0) + len(m.get("self_blame_instances") or [])
        prod_b[brand] = prod_b.get(brand, 0) + len(m.get("product_blame_instances") or [])
    return self_b, prod_b


# ─────────────────────────────────────────────────────────────────────────────
# UI components
# ─────────────────────────────────────────────────────────────────────────────

def _insight_banner(headline: str, subtext: str = "") -> None:
    sub_html = f'<p style="margin:4px 0 0;font-size:0.82rem;color:#9ca3af;line-height:1.5;">{subtext}</p>' if subtext else ""
    st.markdown(
        f'<div style="border-left:4px solid {_P.get("teal","#0ea5e9")};'
        f'border-radius:6px;background:rgba(14,165,233,0.07);padding:12px 18px;margin:12px 0 18px;">'
        f'<p style="margin:0;font-size:0.95rem;font-weight:600;color:#f1f5f9;line-height:1.5;">{headline}</p>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def _full_verbatim_wall(passages: list[dict], key_prefix: str,
                        title: str = "All Verbatims") -> None:
    if not passages:
        empty_state("No verbatims for current filters.")
        return

    with st.expander(f"**{title}** ({len(passages)} verbatims)", expanded=False):
        sentiments = sorted({p.get("sentiment", "neutral") for p in passages})
        brands = sorted({p.get("_brand") or "" for p in passages if p.get("_brand")})

        c1, c2, c3, c4 = st.columns([2, 2, 1, 3])
        f_sent = c1.multiselect("Sentiment", sentiments, default=[], key=f"{key_prefix}_sent")
        f_brand = c2.multiselect("Brand", brands, default=[], key=f"{key_prefix}_brand")
        f_pain = c3.checkbox("Pain only", key=f"{key_prefix}_pain")
        f_kw = c4.text_input("Keyword", placeholder="filter text…", key=f"{key_prefix}_kw")

        filtered = [
            p for p in passages
            if (not f_sent or p.get("sentiment") in f_sent)
            and (not f_brand or p.get("_brand") in f_brand)
            and (not f_pain or p.get("pain_point"))
            and (not f_kw or f_kw.lower() in p.get("content", "").lower())
        ]

        pos_n = sum(1 for p in filtered if p.get("sentiment") == "positive")
        neg_n = sum(1 for p in filtered if p.get("sentiment") == "negative")
        neu_n = len(filtered) - pos_n - neg_n
        st.markdown(
            f'<p style="font-size:0.82rem;color:#9ca3af;margin:8px 0 12px;">'
            f'<b style="color:#f1f5f9;">{len(filtered)}</b> verbatims '
            f'· <span style="color:#22c55e;">▲ {pos_n} positive</span>'
            f' · <span style="color:#ef4444;">▼ {neg_n} negative</span>'
            f' · <span style="color:#9ca3af;">● {neu_n} neutral/other</span></p>',
            unsafe_allow_html=True,
        )

        page_size = 12
        total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
        page_key = f"{key_prefix}_page"
        if page_key not in st.session_state:
            st.session_state[page_key] = 0
        page = st.session_state[page_key]
        page = min(page, total_pages - 1)

        for p in filtered[page * page_size: (page + 1) * page_size]:
            sent = p.get("sentiment", "neutral")
            color = _SENT_COLOR.get(sent, _P.get("neutral", "#9ca3af"))
            meta = " · ".join(filter(None, [
                p.get("_brand") or "", p.get("_city") or "",
                p.get("topic") or "", sent,
            ]))
            safe_meta = html.escape(str(meta))
            safe_content = html.escape(str(p.get("content", "")))
            st.markdown(
                f'<div style="border-left:3px solid {color};'
                f'border-radius:5px;background:#1e293b;'
                f'padding:10px 14px;margin:6px 0;">'
                f'<p style="font-size:0.7rem;color:#94a3b8;margin:0 0 4px;">{safe_meta}</p>'
                f'<p style="font-size:0.9rem;color:#e2e8f0;margin:0;line-height:1.6;">'
                f'"{safe_content}"</p></div>',
                unsafe_allow_html=True,
            )

        if total_pages > 1:
            pc1, pc2, pc3 = st.columns([1, 3, 1])
            if pc1.button("◀ Prev", key=f"{key_prefix}_prev", disabled=page == 0):
                st.session_state[page_key] = page - 1
                st.rerun()
            pc2.markdown(
                f'<p style="text-align:center;color:#9ca3af;font-size:0.82rem;">'
                f'Page {page+1} / {total_pages}</p>', unsafe_allow_html=True)
            if pc3.button("Next ▶", key=f"{key_prefix}_next", disabled=page >= total_pages - 1):
                st.session_state[page_key] = page + 1
                st.rerun()


def _finding_block(finding: dict, call_or: Callable, regen_prompt: str, regen_key: str) -> None:
    if not finding:
        return
    text = finding.get("finding_text") or finding.get("text") or ""
    title = finding.get("title") or finding.get("section_title") or "Finding"
    n = finding.get("n_interviews") or finding.get("n_interviews_used") or ""

    with st.expander(f"**Research Finding: {title}**" + (f" (n={n})" if n else ""), expanded=False):
        if text:
            st.markdown(text)

        col1, col2 = st.columns([3, 1])
        rk = f"regen_{regen_key}"
        if rk not in st.session_state:
            st.session_state[rk] = None

        if col2.button("✦ Regenerate", key=f"btn_{regen_key}"):
            with st.spinner("Generating…"):
                st.session_state[rk] = call_or(regen_prompt) or "_No response._"

        if st.session_state.get(rk):
            st.markdown("---")
            st.markdown(st.session_state[rk])


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def _load_matrices(matrices_dir: str) -> list[dict]:
    out = []
    for p in Path(matrices_dir).glob("*_matrix.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


@st.cache_data(ttl=900, show_spinner=False)
def _load_finding(findings_dir: str, section_id: str) -> dict:
    p = Path(findings_dir) / f"{section_id}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Tab renderers
# ─────────────────────────────────────────────────────────────────────────────

def _render_consumer_profiles(matrices: list, findings_dir: str, call_or: Callable) -> None:
    n = len(matrices)
    if n == 0:
        empty_state("No data for current filters.")
        return

    # ── Insight banner (computed) ──────────────────────────────────────────────
    journey_dist = _count_field(matrices, "respondent.journey_stage")
    nps_dist = _count_field(matrices, "nps_signal")
    top_journey = max(journey_dist, key=journey_dist.get) if journey_dist else "—"
    promoters = nps_dist.get("promoter", 0)
    detractors = nps_dist.get("detractor", 0)
    _insight_banner(
        f"{n} respondents · {promoters} promoters · {detractors} detractors "
        f"· dominant journey stage: {top_journey.replace('_',' ')}",
        "All counts from live matrix data. No hardcoded values.",
    )

    # ── Brand-segment profile cards ────────────────────────────────────────────
    section_header("Segment Profile Cards", "One card per brand owned — all metrics computed from matrices")
    brands_raw = _count_field(matrices, "respondent.brand_owned")
    segs = sorted(brands_raw, key=brands_raw.get, reverse=True)[:8]
    if not segs:
        empty_state("No brand_owned values found.")
        return

    palette = _PALETTE
    cols_per_row = 4
    for row_start in range(0, len(segs), cols_per_row):
        row_segs = segs[row_start:row_start + cols_per_row]
        cols = st.columns(len(row_segs))
        for col, seg in zip(cols, row_segs):
            seg_ms = _seg_matrices(matrices, seg)
            sn = len(seg_ms)
            promo = sum(1 for m in seg_ms if (m.get("nps_signal") or "").lower() == "promoter")
            detr = sum(1 for m in seg_ms if (m.get("nps_signal") or "").lower() == "detractor")
            nps_score = round(promo / sn * 100 - detr / sn * 100, 1) if sn else 0
            rel_dist = _count_field(seg_ms, "brand_relationship.relationship_stage")
            top_rel = max(rel_dist, key=rel_dist.get, default="—").replace("_", " ") if rel_dist else "—"
            loyal_n = sum(1 for m in seg_ms if
                          (_get(m, "brand_relationship.relationship_stage") or "").lower() in ("settled_satisfied", "loyal", "honeymoon"))
            j_dist = _count_field(seg_ms, "respondent.journey_stage")
            top_j = max(j_dist, key=j_dist.get, default="—").replace("_", " ") if j_dist else "—"
            color = palette[segs.index(seg) % len(palette)]

            with col:
                st.markdown(
                    f'<div style="border:1px solid {color};border-radius:8px;'
                    f'background:#1e293b;padding:14px 16px;margin-bottom:12px;">'
                    f'<p style="margin:0 0 4px;font-size:0.78rem;font-weight:700;'
                    f'letter-spacing:0.08em;text-transform:uppercase;color:{color};">{seg}</p>'
                    f'<p style="margin:0 0 8px;font-size:1.5rem;font-weight:700;color:#f1f5f9;">{sn}</p>'
                    f'<p style="margin:0;font-size:0.8rem;color:#9ca3af;line-height:1.6;">'
                    f'NPS score: <b style="color:#f1f5f9;">{nps_score:+.0f}</b><br>'
                    f'Promoters: <b style="color:#22c55e;">{promo}</b> · Detractors: <b style="color:#ef4444;">{detr}</b><br>'
                    f'Relationship: <b style="color:#f1f5f9;">{top_rel}</b><br>'
                    f'Loyal/satisfied: <b style="color:#f1f5f9;">{loyal_n}</b><br>'
                    f'Journey: <b style="color:#f1f5f9;">{top_j}</b>'
                    f'</p></div>',
                    unsafe_allow_html=True,
                )
                if st.button(f"✦ Generate {seg} Narrative", key=f"prof_narr_{seg}"):
                    prompt = (
                        f"Write a concise consumer segment profile for '{seg}' brand owners "
                        f"in an in-home ethnographic study. Data: n={sn}, NPS score={nps_score:+.0f}, "
                        f"promoters={promo}, detractors={detr}, relationship={top_rel}, journey={top_j}. "
                        f"Use market research language. 3-4 sentences. No filler."
                    )
                    key = f"prof_narr_text_{seg}"
                    st.session_state[key] = call_or(prompt) or "_No response._"
                if st.session_state.get(f"prof_narr_text_{seg}"):
                    st.markdown(st.session_state[f"prof_narr_text_{seg}"])

    # ── Journey stage × brand distribution ────────────────────────────────────
    section_header("Journey Stage by Brand", "Recent buyer vs loyal user vs intender split")
    rows = []
    for seg in segs:
        seg_ms = _seg_matrices(matrices, seg)
        jd = _count_field(seg_ms, "respondent.journey_stage")
        for stage, cnt in jd.items():
            rows.append({"Brand": seg, "Journey Stage": stage.replace("_", " "), "Count": cnt})
    if rows:
        df_j = pd.DataFrame(rows)
        fig_j = px.bar(df_j, x="Brand", y="Count", color="Journey Stage",
                       barmode="stack", color_discrete_sequence=_PALETTE)
        fig_j.update_layout(**{**CHART_LAYOUT, "height": 380,
                               "title": "Journey Stage Distribution by Brand"})
        st.plotly_chart(fig_j, use_container_width=True)

    # ── NPS signal league table ────────────────────────────────────────────────
    section_header("NPS Signal League Table", "Promoter / Passive / Detractor per brand")
    nps_rows = []
    for seg in segs:
        seg_ms = _seg_matrices(matrices, seg)
        sn = len(seg_ms)
        nd = _count_field(seg_ms, "nps_signal")
        promo = nd.get("promoter", 0)
        passive = nd.get("passive", 0)
        detr = nd.get("detractor", 0)
        nps_score = round(promo / sn * 100 - detr / sn * 100, 1) if sn else 0
        nps_rows.append({"Brand": seg, "Promoters": promo, "Passives": passive,
                         "Detractors": detr, "NPS Score": nps_score, "N": sn})
    if nps_rows:
        df_nps = pd.DataFrame(nps_rows).sort_values("NPS Score", ascending=False)
        fig_nps = go.Figure()
        for col_, color_ in [("Promoters", "#22c55e"), ("Passives", "#fbbf24"), ("Detractors", "#ef4444")]:
            fig_nps.add_trace(go.Bar(name=col_, x=df_nps["Brand"], y=df_nps[col_],
                                     marker_color=color_))
        fig_nps.update_layout(**{**CHART_LAYOUT, "barmode": "stack", "height": 350,
                                 "title": "NPS Signal by Brand"})
        st.plotly_chart(fig_nps, use_container_width=True)

        from infoleap.skills.prompt_templates import build_methodology_note
        with st.expander("How is this calculated?", expanded=False):
            st.caption(build_methodology_note(
                metric="NPS Score",
                formula="(promoters / n * 100) - (detractors / n * 100), per brand",
                source="nps_signal field per respondent, grouped by respondent.brand_owned",
            ))

    # ── Verbatim wall: identity passages ──────────────────────────────────────
    id_passages = _all_passages(matrices, topics=["identity", "cook", "self-image", "pride", "role"])
    _full_verbatim_wall(id_passages, "prof_wall", "Consumer Identity Verbatims")

    # ── Finding block ──────────────────────────────────────────────────────────
    f1 = _load_finding(findings_dir, "respondent_profile")
    _finding_block(f1, call_or,
        regen_prompt=(
            f"Synthesise key findings about consumer profiles from an ethnographic FMCD study. "
            f"n={n} respondents. Journey stages: {dict(list(_count_field(matrices,'respondent.journey_stage').items())[:4])}. "
            f"NPS distribution: {_count_field(matrices, 'nps_signal')}. "
            f"Brands: {segs[:6]}. 4-5 bullet insights. Use market research language."
        ),
        regen_key="respondent_profile",
    )


def _render_brand_landscape(matrices: list, findings_dir: str, call_or: Callable) -> None:
    n = len(matrices)
    section_header("Brand Relationship Health", "Relationship stage distribution per brand")

    brands_raw = _count_field(matrices, "respondent.brand_owned")
    segs = sorted(brands_raw, key=brands_raw.get, reverse=True)[:8]

    # ── Relationship stage per brand ───────────────────────────────────────────
    rel_stages = ["settled_satisfied", "honeymoon", "strained", "at_risk", "resigned_acceptance"]
    stage_colors = {"settled_satisfied": "#22c55e", "honeymoon": "#86efac",
                    "strained": "#f97316", "at_risk": "#ef4444",
                    "resigned_acceptance": "#9ca3af"}

    rel_dist = _count_field(matrices, "brand_relationship.relationship_stage")
    top_healthy = sum(rel_dist.get(s, 0) for s in ("settled_satisfied", "honeymoon"))
    at_risk_n = rel_dist.get("at_risk", 0)
    strained_n = rel_dist.get("strained", 0)
    _insight_banner(
        f"{top_healthy}/{n} in healthy relationship · {strained_n} strained · {at_risk_n} at risk",
        "Relationship stage from brand_relationship.relationship_stage field.",
    )

    rows = []
    for seg in segs:
        seg_ms = _seg_matrices(matrices, seg)
        rd = _count_field(seg_ms, "brand_relationship.relationship_stage")
        for stage in rel_stages:
            rows.append({"Brand": seg, "Stage": stage.replace("_", " "), "Count": rd.get(stage, 0)})
    if rows:
        df_r = pd.DataFrame(rows)
        fig_r = px.bar(df_r, x="Brand", y="Count", color="Stage", barmode="stack",
                       color_discrete_map={k.replace("_", " "): v for k, v in stage_colors.items()})
        fig_r.update_layout(**{**CHART_LAYOUT, "height": 370,
                               "title": "Brand Relationship Stage by Brand Owned"})
        st.plotly_chart(fig_r, use_container_width=True)

    # ── Loyalty depth ──────────────────────────────────────────────────────────
    # NOTE: these two pies route through chart_renderer's shared registry (unlike
    # every other chart in this file, which builds its own px/go figure using this
    # file's _PALETTE and per-chart heights). chart_renderer._render_pie uses its
    # own COLORS palette and a fixed height=460, not _PALETTE/height=320 — a visible
    # styling divergence from neighboring charts on this same tab, accepted as a
    # known tradeoff for this pilot rather than forcing chart_renderer to match one
    # page's local palette. Revisit if/when more of this file migrates to the
    # shared registry (at which point _PALETTE vs COLORS should be reconciled once,
    # not per-chart).
    from infoleap.views.chart_adapter import render_counts
    section_header("Loyalty Depth & Advocacy")
    c1, c2 = st.columns(2)
    with c1:
        ld = _count_field(matrices, "brand_relationship.loyalty_depth")
        if ld:
            render_counts(ld, label="Loyalty Depth", chart_type="donut", question="loyalty depth distribution")
    with c2:
        adv = _count_field(matrices, "brand_relationship.advocacy_likelihood")
        if adv:
            render_counts(adv, label="Advocacy Likelihood", chart_type="donut", question="advocacy likelihood distribution")

    # ── Blame attribution ──────────────────────────────────────────────────────
    section_header("Blame Attribution", "Self-blame vs product-blame per brand")
    self_b, prod_b = _blame_counts(matrices)
    blame_brands = sorted(set(self_b) | set(prod_b),
                          key=lambda b: self_b.get(b, 0) + prod_b.get(b, 0), reverse=True)[:8]
    if blame_brands:
        fig_bl = go.Figure()
        fig_bl.add_trace(go.Bar(name="Self-blame", x=blame_brands,
                                y=[self_b.get(b, 0) for b in blame_brands],
                                marker_color="#fbbf24"))
        fig_bl.add_trace(go.Bar(name="Product-blame", x=blame_brands,
                                y=[prod_b.get(b, 0) for b in blame_brands],
                                marker_color="#ef4444"))
        fig_bl.update_layout(**{**CHART_LAYOUT, "barmode": "group", "height": 340,
                                "title": "Blame Attribution — Self vs Product"})
        st.plotly_chart(fig_bl, use_container_width=True)
        st.caption("Product-blame = respondent attributes failure to brand/product. Self-blame = respondent takes responsibility.")

        from infoleap.skills.prompt_templates import build_methodology_note
        with st.expander("How is this calculated?", expanded=False):
            st.caption(build_methodology_note(
                metric="Blame Attribution",
                formula="count(self_blame_instances) vs count(product_blame_instances), per brand",
                source="self_blame_instances / product_blame_instances list fields per respondent matrix",
            ))

    # ── Brand mentions sentiment ───────────────────────────────────────────────
    section_header("Brand Mentions Sentiment", "Across all transcript brand_mentions")
    bm_data: dict[str, Counter] = {}
    for m in matrices:
        for bm in (m.get("brand_mentions") or []):
            if isinstance(bm, dict):
                b = str(bm.get("brand") or "").strip()
                s = str(bm.get("sentiment") or "neutral").strip()
                if b:
                    bm_data.setdefault(b, Counter())
                    bm_data[b][s] += 1
    if bm_data:
        top_mentioned = sorted(bm_data, key=lambda b: sum(bm_data[b].values()), reverse=True)[:10]
        bm_rows = []
        for b in top_mentioned:
            for sent, cnt in bm_data[b].items():
                bm_rows.append({"Brand": b, "Sentiment": sent, "Mentions": cnt})
        df_bm = pd.DataFrame(bm_rows)
        sent_color_map = {"positive": "#22c55e", "negative": "#ef4444", "neutral": "#9ca3af"}
        fig_bm = px.bar(df_bm, x="Brand", y="Mentions", color="Sentiment",
                        barmode="stack", color_discrete_map=sent_color_map)
        fig_bm.update_layout(**{**CHART_LAYOUT, "height": 360,
                                "title": "Brand Mentions Sentiment"})
        st.plotly_chart(fig_bm, use_container_width=True)

    # ── Verbatim wall: relationship passages ───────────────────────────────────
    rel_passages = _all_passages(matrices, topics=["relationship", "loyalty", "brand", "trust", "service"])
    _full_verbatim_wall(rel_passages, "brand_wall", "Brand Relationship Verbatims")

    # ── Finding block ──────────────────────────────────────────────────────────
    f = _load_finding(findings_dir, "brand_relationship")
    _finding_block(f, call_or,
        regen_prompt=(
            f"Analyse brand relationship health from an ethnographic FMCD study. "
            f"n={n}. Relationship distribution: {rel_dist}. "
            f"Loyalty depth: {_count_field(matrices,'brand_relationship.loyalty_depth')}. "
            f"Advocacy: {_count_field(matrices,'brand_relationship.advocacy_likelihood')}. "
            f"4-5 bullet insights. Strategic implications for a brand manager."
        ),
        regen_key="brand_relationship",
    )


def _render_pain_points(matrices: list, findings_dir: str, call_or: Callable) -> None:
    n = len(matrices)

    # ── Pain matrix ────────────────────────────────────────────────────────────
    all_pains: list[dict] = []
    for m in matrices:
        brand = _get(m, "respondent.brand_owned", "")
        for p in (m.get("pain_points") or []):
            if isinstance(p, dict):
                all_pains.append({**p, "_brand": brand})

    total_mentions = len(all_pains)
    high_sev = sum(1 for p in all_pains if p.get("severity") in ("critical", "high"))
    top_area_dist = Counter(p.get("product_area", "unknown") for p in all_pains)
    top_area = top_area_dist.most_common(1)[0][0] if top_area_dist else "—"

    _insight_banner(
        f"{total_mentions} total pain mentions · {high_sev} high/critical severity "
        f"· top problem area: {top_area}",
        f"All counts aggregated from pain_points arrays across {len(matrices)} respondent matrices.",
    )

    section_header("Pain Severity Matrix", "Product area × severity bubble chart")
    sev_matrix = _pain_severity_matrix(matrices)
    if sev_matrix:
        bubble_rows = []
        for area, sev_dict in sev_matrix.items():
            for sev, cnt in sev_dict.items():
                bubble_rows.append({"Area": area, "Severity": sev, "Count": cnt})
        df_b = pd.DataFrame(bubble_rows)
        sev_order = ["critical", "high", "medium", "low"]
        df_b["sev_order"] = df_b["Severity"].map(lambda s: sev_order.index(s) if s in sev_order else 99)
        df_b = df_b.sort_values(["sev_order", "Count"], ascending=[True, False])
        fig_bub = px.scatter(df_b, x="Severity", y="Area", size="Count",
                             color="Severity",
                             color_discrete_map={k: v for k, v in _SEV_COLOR.items()},
                             size_max=60, title="Pain Area × Severity Bubble Chart")
        fig_bub.update_layout(**{**CHART_LAYOUT, "height": max(400, len(sev_matrix) * 50 + 120)})
        st.plotly_chart(fig_bub, use_container_width=True)

    # ── Top pain issues ────────────────────────────────────────────────────────
    section_header("Top Pain Issues", "By total mentions across all severity levels")
    area_sev_rows = []
    for area, sev_dict in sev_matrix.items():
        total = sum(sev_dict.values())
        high = sev_dict.get("critical", 0) + sev_dict.get("high", 0)
        area_sev_rows.append({"Area": area, "Total Mentions": total, "High+Critical": high})
    if area_sev_rows:
        df_area = pd.DataFrame(area_sev_rows).sort_values("Total Mentions", ascending=True)
        fig_area = go.Figure()
        fig_area.add_trace(go.Bar(name="Total", y=df_area["Area"], x=df_area["Total Mentions"],
                                  orientation="h", marker_color="#60a5fa"))
        fig_area.add_trace(go.Bar(name="High+Critical", y=df_area["Area"], x=df_area["High+Critical"],
                                  orientation="h", marker_color="#ef4444"))
        fig_area.update_layout(**{**CHART_LAYOUT, "barmode": "overlay", "height": max(350, len(df_area) * 40 + 100),
                                  "title": "Pain Issues by Product Area"})
        st.plotly_chart(fig_area, use_container_width=True)

    # ── Brand × pain area ─────────────────────────────────────────────────────
    section_header("Brand Pain Profile", "Which brands carry most complaints in which areas")
    bp_data: dict[str, dict] = {}
    for p in all_pains:
        b = p.get("_brand") or "Unknown"
        area = p.get("product_area") or "unspecified"
        bp_data.setdefault(b, Counter())
        bp_data[b][area] += 1
    top_bp_brands = sorted(bp_data, key=lambda b: sum(bp_data[b].values()), reverse=True)[:8]
    top_areas_list = [a for a, _ in top_area_dist.most_common(8)]
    if top_bp_brands and top_areas_list:
        hm_z = [[bp_data.get(b, {}).get(a, 0) for a in top_areas_list] for b in top_bp_brands]
        fig_hm = go.Figure(go.Heatmap(
            z=hm_z, x=top_areas_list, y=top_bp_brands,
            colorscale="Reds", hoverongaps=False,
            hovertemplate="<b>%{y}</b> — %{x}<br>%{z} mentions<extra></extra>",
        ))
        fig_hm.update_layout(**{**CHART_LAYOUT, "height": max(350, len(top_bp_brands) * 45 + 120),
                                "title": "Pain Mentions by Brand × Product Area"})
        st.plotly_chart(fig_hm, use_container_width=True)

    # ── Verbatim wall: pain passages ───────────────────────────────────────────
    pain_passages = _all_passages(matrices, pain_only=True)
    _full_verbatim_wall(pain_passages, "pain_wall", "Pain Point Verbatims (All)")

    # ── Finding block ──────────────────────────────────────────────────────────
    f = _load_finding(findings_dir, "pain_points_workarounds")
    _finding_block(f, call_or,
        regen_prompt=(
            f"Analyse pain points from an ethnographic FMCD mixer/appliance study. "
            f"Total pain mentions: {total_mentions}. High/critical: {high_sev}. "
            f"Top areas by count: {dict(top_area_dist.most_common(5))}. "
            f"n={n} respondents. 4-5 bullet strategic implications for product team."
        ),
        regen_key="pain_points_workarounds",
    )


def _render_aspiration(matrices: list, findings_dir: str, call_or: Callable) -> None:
    n = len(matrices)
    section_header("Aspiration-Reality Gaps", "Commercial opportunity × emotional charge matrix")

    gaps = _aspiration_gaps(matrices)
    opp_dist = Counter(g["commercial_opportunity"] for g in gaps)
    em_dist = Counter(g["emotional_charge"] for g in gaps)
    high_opp = opp_dist.get("high", 0)
    high_em = em_dist.get("high", 0)

    _insight_banner(
        f"{len(gaps)} aspiration gaps identified · {high_opp} high commercial opportunity "
        f"· {high_em} high emotional charge",
        "From aspiration_reality_gaps arrays across all matrices.",
    )

    # ── Opportunity matrix scatter ─────────────────────────────────────────────
    if gaps:
        opp_order = {"high": 3, "medium": 2, "low": 1}
        em_order  = {"high": 3, "medium": 2, "low": 1}
        df_g = pd.DataFrame(gaps)
        df_g["opp_num"] = df_g["commercial_opportunity"].map(lambda x: opp_order.get(str(x).lower(), 2))
        df_g["em_num"]  = df_g["emotional_charge"].map(lambda x: em_order.get(str(x).lower(), 2))
        agg = df_g.groupby(["commercial_opportunity", "emotional_charge"]).size().reset_index(name="Count")
        agg["opp_num"] = agg["commercial_opportunity"].map(lambda x: opp_order.get(str(x).lower(), 2))
        agg["em_num"]  = agg["emotional_charge"].map(lambda x: em_order.get(str(x).lower(), 2))
        fig_sc = px.scatter(agg, x="commercial_opportunity", y="emotional_charge",
                            size="Count", color="Count",
                            color_continuous_scale="OrRd", size_max=70,
                            labels={"commercial_opportunity": "Commercial Opportunity",
                                    "emotional_charge": "Emotional Charge"},
                            title="Aspiration Gap Opportunity Matrix")
        fig_sc.update_layout(**{**CHART_LAYOUT, "height": 420})
        st.plotly_chart(fig_sc, use_container_width=True)

        # High-priority gap list
        high_gaps = [g for g in gaps if str(g.get("commercial_opportunity","")).lower() == "high"
                     and str(g.get("emotional_charge","")).lower() == "high"]
        if high_gaps:
            st.markdown(f"**High-priority gaps** (high commercial × high emotional) — {len(high_gaps)}:")
            for g in high_gaps[:8]:
                q = g.get("verbatim_quote", "")
                b = g.get("brand", "")
                st.markdown(
                    f'<div style="border-left:3px solid #ef4444;padding:8px 14px;'
                    f'margin:6px 0;background:rgba(239,68,68,0.06);border-radius:4px;">'
                    f'<p style="margin:0 0 2px;font-size:0.82rem;color:#9ca3af;">'
                    f'<b>{b}</b> · {g.get("commercial_opportunity","").upper()}</p>'
                    f'<p style="margin:0 0 4px;font-size:0.88rem;font-weight:600;color:#f1f5f9;">'
                    f'{g.get("aspiration","")}</p>'
                    + (f'<p style="margin:0;font-size:0.82rem;color:#9ca3af;">&ldquo;{q}&rdquo;</p>' if q else "")
                    + '</div>',
                    unsafe_allow_html=True,
                )

    # ── Jobs To Be Done ────────────────────────────────────────────────────────
    section_header("Jobs To Be Done", "Frequency of JTBD across respondents")
    jtbd_all = _jtbd_list(matrices)
    if jtbd_all:
        jtbd_c = Counter(jtbd_all).most_common(15)
        df_jt = pd.DataFrame(jtbd_c, columns=["JTBD", "Count"])
        fig_jt = px.bar(df_jt, x="Count", y="JTBD", orientation="h",
                        color="Count", color_continuous_scale="Blues",
                        title="Top Jobs To Be Done")
        fig_jt.update_layout(**{**CHART_LAYOUT, "height": max(300, len(df_jt) * 28 + 80)})
        st.plotly_chart(fig_jt, use_container_width=True)

    # ── Unspoken needs ─────────────────────────────────────────────────────────
    section_header("Unspoken Needs", "Inferred from workarounds and frustrations")
    needs = _unspoken_needs_list(matrices)
    if needs:
        nc = Counter(needs).most_common(12)
        for need, cnt in nc:
            pct_bar = min(cnt / (nc[0][1] or 1) * 100, 100)
            st.markdown(
                f'<div style="margin:4px 0;">'
                f'<p style="margin:0 0 2px;font-size:0.88rem;color:#e2e8f0;">{need}</p>'
                f'<div style="background:rgba(255,255,255,0.1);border-radius:3px;height:6px;">'
                f'<div style="width:{pct_bar:.0f}%;background:#0ea5e9;height:100%;border-radius:3px;"></div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

    # ── Peak emotional moments ─────────────────────────────────────────────────
    section_header("Peak Emotional Moments")
    pem_rows = []
    for m in matrices:
        pem = m.get("peak_emotional_moment")
        if isinstance(pem, dict) and pem.get("verbatim_quote"):
            pem_rows.append({
                "emotion": pem.get("emotion", ""),
                "trigger": pem.get("trigger", ""),
                "quote":   pem.get("verbatim_quote", ""),
                "brand":   _get(m, "respondent.brand_owned", ""),
            })
    if pem_rows:
        emotion_dist = Counter(p["emotion"] for p in pem_rows if p["emotion"])
        c1, c2 = st.columns([2, 3])
        with c1:
            fig_em = px.pie(names=list(emotion_dist.keys()),
                            values=list(emotion_dist.values()),
                            title="Peak Emotion Distribution",
                            color_discrete_sequence=_PALETTE)
            fig_em.update_layout(**{**CHART_LAYOUT, "height": 300})
            st.plotly_chart(fig_em, use_container_width=True)
        with c2:
            st.markdown("**Top Peak Moments:**")
            for p in pem_rows[:5]:
                st.markdown(
                    f'<div style="border-left:3px solid #fbbf24;padding:8px 14px;margin:6px 0;">'
                    f'<p style="margin:0;font-size:0.8rem;color:#9ca3af;">'
                    f'{p["brand"]} · {p["emotion"]} · {p["trigger"]}</p>'
                    f'<p style="margin:4px 0 0;font-size:0.88rem;color:#e2e8f0;">&ldquo;{p["quote"][:180]}&rdquo;</p>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ── Verbatim wall: aspiration passages ────────────────────────────────────
    asp_passages = _all_passages(matrices, topics=["aspiration", "need", "wish", "want", "gap", "desire"])
    _full_verbatim_wall(asp_passages, "asp_wall", "Aspiration & Need Verbatims")

    # ── Finding block ──────────────────────────────────────────────────────────
    for sec in ["aspiration_gaps", "category_need_buckets"]:
        f = _load_finding(findings_dir, sec)
        if f:
            _finding_block(f, call_or,
                regen_prompt=(
                    f"Synthesise aspiration-reality gaps from FMCD ethnographic study. "
                    f"Total gaps: {len(gaps)}. High-opportunity: {high_opp}. "
                    f"JTBD top: {[j for j, _ in Counter(jtbd_all).most_common(5)] if jtbd_all else []}. "
                    f"4-5 bullets. Commercial opportunity framing."
                ),
                regen_key=sec,
            )


def _render_purchase_journey(matrices: list, findings_dir: str, call_or: Callable) -> None:
    n = len(matrices)

    dd_dist = _count_nested(matrices, "decision_drivers", "driver")
    src_dist = _count_nested(matrices, "decision_drivers", "info_source")
    pd_dist = _count_field(matrices, "social_dynamics.purchase_decision_maker")
    price_dist = _count_field(matrices, "price_sensitivity")

    _insight_banner(
        f"Top decision driver: {max(dd_dist, key=dd_dist.get, default='—')} · "
        f"Top info source: {max(src_dist, key=src_dist.get, default='—')} · "
        f"Top decision maker: {max(pd_dist, key=pd_dist.get, default='—').replace('_',' ')}",
        "From decision_drivers, social_dynamics, price_sensitivity fields.",
    )

    # ── Decision drivers ───────────────────────────────────────────────────────
    section_header("Decision Drivers", "What drives purchase decisions")
    c1, c2 = st.columns(2)
    with c1:
        if dd_dist:
            df_dd = pd.DataFrame(sorted(dd_dist.items(), key=lambda x: -x[1])[:12],
                                 columns=["Driver", "Count"])
            fig_dd = px.bar(df_dd, x="Count", y="Driver", orientation="h",
                            color="Count", color_continuous_scale="Blues",
                            title="Decision Drivers")
            fig_dd.update_layout(**{**CHART_LAYOUT, "height": max(300, len(df_dd) * 28 + 80)})
            st.plotly_chart(fig_dd, use_container_width=True)
    with c2:
        if src_dist:
            fig_src = px.pie(names=list(src_dist.keys()), values=list(src_dist.values()),
                             title="Info Source", color_discrete_sequence=_PALETTE)
            fig_src.update_layout(**{**CHART_LAYOUT, "height": 360})
            st.plotly_chart(fig_src, use_container_width=True)

    # ── Feature priorities ─────────────────────────────────────────────────────
    section_header("Feature Priorities", "High vs medium vs low importance features")
    fp_data: dict[str, Counter] = {}
    for m in matrices:
        for fp in (m.get("feature_priorities") or []):
            if isinstance(fp, dict):
                feat = str(fp.get("feature") or "").strip()
                imp = str(fp.get("importance") or "medium").strip()
                if feat:
                    fp_data.setdefault(feat, Counter())
                    fp_data[feat][imp] += 1
    if fp_data:
        top_features = sorted(fp_data, key=lambda f: fp_data[f].get("high", 0), reverse=True)[:12]
        fp_rows = []
        for feat in top_features:
            for imp, cnt in fp_data[feat].items():
                fp_rows.append({"Feature": feat, "Importance": imp, "Count": cnt})
        df_fp = pd.DataFrame(fp_rows)
        imp_color = {"high": "#22c55e", "medium": "#fbbf24", "low": "#9ca3af"}
        fig_fp = px.bar(df_fp, x="Count", y="Feature", color="Importance",
                        barmode="stack", orientation="h",
                        color_discrete_map=imp_color, title="Feature Priority Ranking")
        fig_fp.update_layout(**{**CHART_LAYOUT, "height": max(360, len(top_features) * 35 + 100)})
        st.plotly_chart(fig_fp, use_container_width=True)

    # ── Social dynamics ────────────────────────────────────────────────────────
    section_header("Social & Kitchen Dynamics")
    c1, c2, c3 = st.columns(3)
    with c1:
        if pd_dist:
            fig_pd = px.pie(names=list(pd_dist.keys()), values=list(pd_dist.values()),
                            title="Purchase Decision Maker", color_discrete_sequence=_PALETTE)
            fig_pd.update_layout(**{**CHART_LAYOUT, "height": 300})
            st.plotly_chart(fig_pd, use_container_width=True)
    with c2:
        cl_dist = _count_field(matrices, "social_dynamics.cooking_labor")
        if cl_dist:
            fig_cl = px.pie(names=list(cl_dist.keys()), values=list(cl_dist.values()),
                            title="Cooking Labor", color_discrete_sequence=_PALETTE)
            fig_cl.update_layout(**{**CHART_LAYOUT, "height": 300})
            st.plotly_chart(fig_cl, use_container_width=True)
    with c3:
        if price_dist:
            fig_pr = px.pie(names=list(price_dist.keys()), values=list(price_dist.values()),
                            title="Price Sensitivity", color_discrete_sequence=_PALETTE)
            fig_pr.update_layout(**{**CHART_LAYOUT, "height": 300})
            st.plotly_chart(fig_pr, use_container_width=True)

    # ── Cultural context ───────────────────────────────────────────────────────
    section_header("Cultural & Kitchen Context")
    rfc_dist = _count_field(matrices, "cultural_context.regional_food_culture")
    tvm_dist = _count_field(matrices, "cultural_context.traditional_vs_modern")
    c1, c2 = st.columns(2)
    with c1:
        if rfc_dist:
            df_rfc = pd.DataFrame(sorted(rfc_dist.items(), key=lambda x: -x[1])[:10],
                                  columns=["Culture", "Count"])
            fig_rfc = px.bar(df_rfc, x="Count", y="Culture", orientation="h",
                             color="Count", color_continuous_scale="Greens",
                             title="Regional Food Culture")
            fig_rfc.update_layout(**{**CHART_LAYOUT, "height": 320})
            st.plotly_chart(fig_rfc, use_container_width=True)
    with c2:
        if tvm_dist:
            fig_tvm = px.pie(names=list(tvm_dist.keys()), values=list(tvm_dist.values()),
                             title="Traditional vs Modern Cooking",
                             color_discrete_sequence=_PALETTE)
            fig_tvm.update_layout(**{**CHART_LAYOUT, "height": 320})
            st.plotly_chart(fig_tvm, use_container_width=True)

    # ── Verbatim wall: decision passages ──────────────────────────────────────
    dec_passages = _all_passages(matrices, decision_only=True)
    _full_verbatim_wall(dec_passages, "dec_wall", "Purchase Decision Verbatims")

    # ── Finding blocks ─────────────────────────────────────────────────────────
    for sec in ["purchase_journey", "kitchen_context"]:
        f = _load_finding(findings_dir, sec)
        if f:
            _finding_block(f, call_or,
                regen_prompt=(
                    f"Synthesise purchase journey insights from FMCD ethnographic study. "
                    f"n={n}. Top driver: {max(dd_dist, key=dd_dist.get, default='—') if dd_dist else '—'}. "
                    f"Top source: {max(src_dist, key=src_dist.get, default='—') if src_dist else '—'}. "
                    f"Decision maker: {dict(list(pd_dist.items())[:3])}. "
                    f"4-5 bullet insights. Channel and messaging implications."
                ),
                regen_key=sec,
            )


def _render_study_report(matrices: list, findings_dir: str,
                         proj: dict, call_or: Callable) -> None:
    section_header("AI-Generated Study Report",
                   "Full research report built from all findings + computed statistics")

    study_name = proj.get("display_name") or proj.get("id") or "Ethnographic Study"
    n = len(matrices)

    style_opts = ["Full Research Report", "Executive Summary",
                  "Strategic Briefing (CEO)", "Product Team Briefing (PM)"]
    style = st.radio("Report style", style_opts, horizontal=True, key="eth_report_style")

    report_key = "eth_generated_report"
    if report_key not in st.session_state:
        st.session_state[report_key] = None

    if st.button("✦ Generate Report", key="eth_gen_report_btn"):
        all_findings_text = []
        sec_ids = ["respondent_profile", "brand_relationship", "category_need_buckets",
                   "kitchen_context", "pain_points_workarounds", "purchase_journey",
                   "aspiration_gaps", "strategic_summary"]
        for sec in sec_ids:
            f = _load_finding(findings_dir, sec)
            if f:
                ft = f.get("finding_text") or ""
                if ft:
                    all_findings_text.append(f"### {f.get('title',sec)}\n{ft[:1200]}")

        pain_top = dict(Counter(
            p.get("product_area", "unknown")
            for m in matrices for p in (m.get("pain_points") or []) if isinstance(p, dict)
        ).most_common(5))
        nps_d = _count_field(matrices, "nps_signal")
        rel_d = _count_field(matrices, "brand_relationship.relationship_stage")
        dd_d = _count_nested(matrices, "decision_drivers", "driver")

        best_verbatims = []
        brands_found = sorted({_get(m, "respondent.brand_owned", "") for m in matrices if _get(m, "respondent.brand_owned")})
        for brand in brands_found[:6]:
            seg_ms = _seg_matrices(matrices, brand)
            for m in seg_ms[:1]:
                pem = m.get("peak_emotional_moment")
                if isinstance(pem, dict) and pem.get("verbatim_quote"):
                    best_verbatims.append(f"[{brand}] {pem['verbatim_quote'][:120]}")

        style_instr = {
            "Full Research Report": "Write a comprehensive 6-section research report with methodology, findings, and strategic recommendations.",
            "Executive Summary":    "Write a tight executive summary (1 page): headline finding, 3 key insights, 3 strategic priorities.",
            "Strategic Briefing (CEO)": "Write a CEO briefing: market context, 3 top risks, 3 growth opportunities, 3 executive decisions needed.",
            "Product Team Briefing (PM)": "Write a PM briefing: top 5 pain points, top 3 unmet needs, 5 feature recommendations with priority ranking.",
        }.get(style, "")

        prompt = f"""{style_instr}

Study: {study_name}
n = {n} in-home ethnographic IDIs
Brands: {', '.join(brands_found[:8])}

NPS signal: {nps_d}
Brand relationship: {rel_d}
Top pain areas: {pain_top}
Top decision drivers: {dict(list(dd_d.items())[:5]) if dd_d else {}}

Key verbatims (one per brand):
{chr(10).join(best_verbatims)}

Research findings:
{chr(10).join(all_findings_text[:6])}

Write in clear, professional market research language. No filler. Cite statistics where available."""

        with st.spinner("Generating report…"):
            result = call_or(prompt)
        st.session_state[report_key] = result or "_No response from AI._"

    if st.session_state.get(report_key):
        report = st.session_state[report_key]
        st.markdown(report)
        st.markdown("---")
        c1, c2 = st.columns(2)
        c1.download_button(
            "↓ Download Report (.md)",
            data=report.encode("utf-8"),
            file_name=f"{study_name.lower().replace(' ','_')}_report.md",
            mime="text/markdown",
            key="eth_dl_md",
        )
        c2.download_button(
            "↓ Download Report (.txt)",
            data=report.encode("utf-8"),
            file_name=f"{study_name.lower().replace(' ','_')}_report.txt",
            mime="text/plain",
            key="eth_dl_txt",
        )

    # ── Section-by-section regen ───────────────────────────────────────────────
    st.markdown("---")
    section_header("Section-by-Section Findings")
    sec_configs = [
        ("respondent_profile",    "Consumer Profiles"),
        ("brand_relationship",    "Brand Landscape"),
        ("pain_points_workarounds","Pain Points & Workarounds"),
        ("aspiration_gaps",       "Aspiration-Reality Gaps"),
        ("category_need_buckets", "Category Need Buckets"),
        ("purchase_journey",      "Purchase Journey"),
        ("kitchen_context",       "Kitchen Context"),
        ("strategic_summary",     "Strategic Summary"),
    ]
    for sec_id, sec_title in sec_configs:
        f = _load_finding(findings_dir, sec_id)
        if f:
            _finding_block(f, call_or,
                regen_prompt=f"Rephrase and expand the {sec_title} findings from this ethnographic study, n={n}. Use FINDING/DETAIL/ACTION format.",
                regen_key=f"report_{sec_id}",
            )

    # ── Complete verbatim archive ──────────────────────────────────────────────
    st.markdown("---")
    all_p = _all_passages(matrices)
    _full_verbatim_wall(all_p, "report_archive", f"Complete Verbatim Archive ({len(all_p)} passages)")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _render_options_menu(proj_id: str, call_llm_fn: Callable) -> list[str]:
    """Show 2-4 LLM-recommended analysis focus areas for this study, with rationale,
    before the tabs render. Cached in session_state per project so a Streamlit
    rerun (e.g. from filter widget interaction) doesn't re-call the LLM every time.
    Returns the list of recommended signal keys (subset of _ETH_SIGNAL_MAP keys),
    or [] if the LLM is unavailable/returns nothing parseable.
    """
    from infoleap.skills.prompt_templates import build_options_menu_prompt

    cache_key = f"eth_options_menu_{proj_id}"
    if cache_key not in st.session_state:
        prompt = build_options_menu_prompt(
            entity=proj_id, available_signals=list(_ETH_SIGNAL_MAP.keys())
        )
        raw = call_llm_fn(prompt, system="Analysis options planner. Numbered list only, no narrative.") or ""
        # Substring match against the raw list, not strict per-line parsing. Safe
        # in practice: the 5 signal keys are distinctive multi-word identifiers,
        # none a substring of another. The one realistic false-positive is the
        # LLM's rationale prose for one item incidentally name-dropping another
        # signal key without formally recommending it — acceptable given the
        # prompt already constrains the reply to a numbered 2-4 item list.
        recommended = [key for key in _ETH_SIGNAL_MAP if key in raw]
        st.session_state[cache_key] = {"raw": raw, "recommended": recommended}

    cached = st.session_state[cache_key]
    if cached["raw"]:
        with st.expander("💡 Recommended focus areas for this data", expanded=False):
            st.markdown(cached["raw"])
    return cached["recommended"]


def _reorder_tab_labels_by_recommendation(labels: list[str], recommended_keys: list[str]) -> list[str]:
    """Put LLM-recommended tabs first (marked with a star), keep all others in
    original relative order after them. Never drops a tab."""
    recommended_names = {_ETH_SIGNAL_MAP[k] for k in recommended_keys if k in _ETH_SIGNAL_MAP}
    marked = [f"⭐ {lbl}" if lbl in recommended_names else lbl for lbl in labels]
    recommended_first = [lbl for lbl in marked if lbl.startswith("⭐ ")]
    rest = [lbl for lbl in marked if not lbl.startswith("⭐ ")]
    return recommended_first + rest


def render_ethnographic(proj: dict, base_path: Path,
                        call_openrouter_fn: Callable) -> None:
    """
    Main entry point for ethnographic study dashboard.

    proj             — registry entry (id, display_name, data_paths, etc.)
    base_path        — resolved Path to oxdata/ directory
    call_openrouter_fn — fn(prompt: str) -> str | None
    """
    proj_id = proj.get("id", "")
    proj_paths = proj.get("data_paths") or {}

    matrices_dir = base_path / proj_paths.get("matrices", f"data/projects/{proj_id}/matrices")
    findings_dir = base_path / "data" / "projects" / proj_id / "findings"

    matrices_all = _load_matrices(str(matrices_dir))
    if not matrices_all:
        empty_state("No interview data extracted yet for this project. Upload transcripts and run the extraction pipeline to see quotes here.")
        return

    # Quality gate: "critical"-quality extractions (verbatim fidelity check failed badly) are
    # excluded from every chart's aggregate data, not just flagged — previously advisory only.
    from infoleap.skills.project_extractor import gate_matrices_by_quality
    matrices_all, _excluded_matrices = gate_matrices_by_quality(matrices_all)
    if _excluded_matrices:
        st.warning(
            f"⚠ {len(_excluded_matrices)} respondent(s) excluded from every chart below — "
            f"critical-quality extraction (verbatim fidelity check failed badly): "
            + ", ".join(m.get("doc_id", "?") for m in _excluded_matrices)
            + ". Re-run extraction for these files in Extraction Studio."
        )
    if not matrices_all:
        empty_state("All matrices for this project are critical-quality — nothing to render. "
                    "Re-run extraction in Extraction Studio.")
        return

    call_or = call_openrouter_fn

    # ── Global filters ─────────────────────────────────────────────────────────
    with st.expander("**Filters**", expanded=False):
        all_brands = sorted({_get(m, "respondent.brand_owned") for m in matrices_all
                             if _get(m, "respondent.brand_owned")})
        all_cities = sorted({_get(m, "respondent.city") for m in matrices_all
                             if _get(m, "respondent.city")})
        all_stages = sorted({_get(m, "respondent.journey_stage") for m in matrices_all
                             if _get(m, "respondent.journey_stage")})

        fc1, fc2, fc3 = st.columns(3)
        f_brands  = fc1.multiselect("Brand", all_brands,  default=[], key="eth_f_brand")
        f_cities  = fc2.multiselect("City",  all_cities,  default=[], key="eth_f_city")
        f_stages  = fc3.multiselect("Journey Stage", all_stages, default=[], key="eth_f_stage")

    filters = {
        "respondent.brand_owned":   [b for b in f_brands] or None,
        "respondent.city":          [c for c in f_cities] or None,
        "respondent.journey_stage": [s for s in f_stages] or None,
    }
    matrices = _filter_matrices(matrices_all, {k: v for k, v in filters.items() if v})

    n = len(matrices)
    n_all = len(matrices_all)
    st.caption(
        f"**{n}** respondents"
        + (f" (filtered from {n_all})" if n < n_all else "")
        + f" · {proj.get('display_name','')}"
    )

    if n == 0:
        empty_state("No data matches current filters. Clear filters to see all respondents.")
        return

    # ── Options menu: LLM proposes which content areas are worth attention ──────
    recommended_signals = _render_options_menu(proj_id, call_or)

    # ── 6 tabs (recommended ones surfaced first, nothing dropped) ───────────────
    _tab_labels = list(_ETH_SIGNAL_MAP.values()) + ["Study Report"]
    _ordered_labels = _reorder_tab_labels_by_recommendation(_tab_labels, recommended_signals)
    _tabs = dict(zip(_ordered_labels, st.tabs(_ordered_labels)))

    def _tab(base_label: str):
        # Look up by base label, tolerating the "⭐ " recommended-marker prefix.
        # Correctness relies on no _ETH_SIGNAL_MAP value ever starting with the
        # literal "⭐ " itself — safe given it's a small hardcoded dict of plain
        # tab names, but worth knowing if that dict ever changes.
        return _tabs.get(base_label) or _tabs.get(f"⭐ {base_label}")

    with _tab("Consumer Profiles"):
        _render_consumer_profiles(matrices, str(findings_dir), call_or)

    with _tab("Brand Landscape"):
        _render_brand_landscape(matrices, str(findings_dir), call_or)

    with _tab("Pain Points"):
        _render_pain_points(matrices, str(findings_dir), call_or)

    with _tab("Aspiration & Need"):
        _render_aspiration(matrices, str(findings_dir), call_or)

    with _tab("Purchase Journey"):
        _render_purchase_journey(matrices, str(findings_dir), call_or)

    with _tab("Study Report"):
        _render_study_report(matrices, str(findings_dir), proj, call_or)
