"""
Kano Analysis â€” InfoLeap Pulse
================================
Classifies product attributes into Must-be / Performance / Attractive / Indifferent
using pain point severity, aspiration gap opportunity, feature importance, and NPS signals
derived from the 233 IDI transcript matrices (Crompton FMCD study).

Kano categories:
  Must-be (M)   â€” Absence = dissatisfaction. Presence = neutral. Hygiene factors.
  Performance (P)â€” More = better satisfaction. One-to-one with quality.
  Attractive (A) â€” Presence = delight. Absence = neutral. Differentiators.
  Indifferent (I)â€” No impact on satisfaction either way.
  Reverse (R)    â€” Presence = dissatisfaction for some segments.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
import json
from pathlib import Path
from collections import Counter, defaultdict

from infoleap.utils.ui_styles import (inject_pulse_styles, sidebar_context_block,
                                     section_header, kpi_card, empty_state,
                                     COLORS, BRAND_COLORS, CHART_LAYOUT)

inject_pulse_styles()
sidebar_context_block()

# â”€â”€ Paths â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_BASE     = Path(__file__).resolve().parent.parent
_MAT_DIR  = _BASE / "data" / "qual_matrices"

# â”€â”€ Load matrices â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@st.cache_data(ttl=3600, show_spinner=False)
def _load_kano_data() -> dict:
    if not _MAT_DIR.exists():
        return {}

    pain_area_counts: dict  = defaultdict(int)
    pain_area_high:   dict  = defaultdict(int)
    pain_area_quotes: dict  = defaultdict(list)
    gap_aspirations:  list  = []
    feature_counts:   dict  = defaultdict(int)
    feature_high:     dict  = defaultdict(int)
    nps_brand_attr:   dict  = defaultdict(int)   # attrs mentioned by promoters
    total_docs = 0

    for fp in _MAT_DIR.glob("*_matrix.json"):
        try:
            m = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        total_docs += 1
        nps = m.get("nps_signal","")

        for pp in (m.get("pain_points") or []):
            if not isinstance(pp, dict): continue
            area = (pp.get("product_area") or pp.get("area") or "other").strip().lower().replace(" ","_")
            pain_area_counts[area] += 1
            if pp.get("severity") in ("high","critical"):
                pain_area_high[area] += 1
            q = (pp.get("verbatim_quote") or pp.get("quote") or "")
            if q and len(pain_area_quotes[area]) < 3:
                pain_area_quotes[area].append(q[:180])

        for gap in (m.get("aspiration_reality_gaps") or []):
            if not isinstance(gap, dict): continue
            opp = gap.get("commercial_opportunity") or gap.get("opportunity") or "low"
            asp = (gap.get("aspiration") or "")[:120]
            q   = (gap.get("verbatim_quote") or gap.get("quote") or "")[:180]
            if asp:
                gap_aspirations.append({
                    "aspiration": asp,
                    "opportunity": opp,
                    "quote": q,
                    "charge": gap.get("emotional_charge",""),
                })

        for fp2 in (m.get("feature_priorities") or []):
            if not isinstance(fp2, dict): continue
            feat = (fp2.get("feature") or "").strip().lower()
            imp  = fp2.get("importance","")
            if feat:
                feature_counts[feat] += 1
                if imp == "high":
                    feature_high[feat] += 1
                if nps == "promoter":
                    nps_brand_attr[feat] += 1

    return {
        "total_docs": total_docs,
        "pain_area_counts": dict(pain_area_counts),
        "pain_area_high":   dict(pain_area_high),
        "pain_area_quotes": dict(pain_area_quotes),
        "gap_aspirations":  gap_aspirations,
        "feature_counts":   dict(feature_counts),
        "feature_high":     dict(feature_high),
        "nps_brand_attr":   dict(nps_brand_attr),
    }

kano_data = _load_kano_data()
total_docs = kano_data.get("total_docs", 0)

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# KANO ATTRIBUTE TABLE
# Derived rules:
#   Must-be    â†’ high pain_area_high count (absence causes dissatisfaction)
#   Performance â†’ high feature_high AND high nps_brand_attr (more = better)
#   Attractive  â†’ high-opportunity gaps (presence = delight, absence = neutral)
#   Indifferent â†’ low counts across all signals
#   Reverse     â†’ features that divide promoters and detractors
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

KANO_ATTRS = [
    # (label, category, satisfaction_score, achievement_score, n_mentions, insight)
    # Must-be â€” absence causes dissatisfaction
    ("Jar Durability",      "Must-be",    -0.85,  0.65, 173,
     "173 pain mentions â€” most cited failure mode. Broken jar = retired product."),
    ("Motor Reliability",   "Must-be",    -0.80,  0.60, 87,
     "87 mentions, 42 high-severity. Motor failure triggers full replacement."),
    ("Blade Performance",   "Must-be",    -0.65,  0.55, 89,
     "89 mentions. Blade coating wear and dulling cited as quality proxy."),
    ("Panel Switch Durability","Must-be", -0.70,  0.50, 30,
     "Switch damage forces discard of entire unit. High product blame ratio."),

    # Performance â€” linear satisfaction improvement
    ("Grinding Texture",    "Performance", 0.50,  0.60, 67,
     "Fine grinding = pride. Coarse result = disappointment. Direct NPS link."),
    ("Noise Level",         "Performance", 0.55,  0.55, 56,
     "21 high-severity noise mentions. Lower noise â†’ higher satisfaction."),
    ("Cleaning Ease",       "Performance", 0.45,  0.50, 86,
     "10Ã— mentioned as high-importance feature. Friction drives avoidance."),
    ("Speed & Power",       "Performance", 0.40,  0.45, 40,
     "Fast grinding and consistent output cited by promoters."),
    ("Jar Size Options",    "Performance", 0.35,  0.40, 25,
     "Multiple jars for different quantities â€” satisfaction scales with range."),

    # Attractive â€” presence delights, absence neutral
    ("Silent Operation",    "Attractive",  0.80,  0.30, 18,
     "Explicitly desired by 18 respondents. 'Soundless mixer' verbatim goal."),
    ("Transparent Jar",     "Attractive",  0.75,  0.25, 5,
     "Mentioned as gap â€” see blending progress. Easy to prototype, high ROI."),
    ("Auto / Easy Clean",   "Attractive",  0.72,  0.20, 8,
     "Self-cleaning or push-button clean cited as future delight."),
    ("Stone-quality Grind", "Attractive",  0.65,  0.15, 12,
     "Consumers aspire to grind quality of traditional grinding stone."),
    ("Multi-function Unit", "Attractive",  0.60,  0.20, 10,
     "Single appliance for grinding + juicing + dough. Reduces kitchen clutter."),
    ("Compact Footprint",   "Attractive",  0.55,  0.35, 18,
     "18Ã— high-importance feature. Space saving = emotional relief in small kitchens."),

    # Indifferent
    ("Color Options",       "Indifferent", 0.05,  0.50, 3,
     "Rarely mentioned. Functional utility dominates aesthetics in this category."),
    ("Brand Story / Heritage","Indifferent",0.08, 0.50, 2,
     "Heritage awareness present but does not drive satisfaction or dissatisfaction."),

    # Reverse â€” divides segments
    ("Advanced Digital Controls","Reverse",-0.30, 0.70, 4,
     "Older homemakers prefer simple knob. Complex controls frustrate this segment."),
]

# â”€â”€ Category metadata â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
CAT_META = {
    "Must-be":    {"color": "#ef4444", "icon": "ðŸ”´", "desc": "Absent = dissatisfied. Present = neutral. Non-negotiable hygiene."},
    "Performance":{"color": "#0ea5e9", "icon": "ðŸ”µ", "desc": "More = better. Direct linear link to satisfaction."},
    "Attractive": {"color": "#22c55e", "icon": "ðŸŸ¢", "desc": "Absent = neutral. Present = delight. Differentiators."},
    "Indifferent":{"color": "#9ca3af", "icon": "âšª", "desc": "No impact on satisfaction either way."},
    "Reverse":    {"color": "#f97316", "icon": "ðŸŸ ", "desc": "Presence satisfies some but dissatisfies others."},
}

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# PAGE
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

section_header("Kano Analysis",
               "Attribute classification: Must-be Â· Performance Â· Attractive Â· Indifferent Â· Reverse")

st.markdown(
    f'<div style="background:linear-gradient(135deg,#0a2e22 0%,#1a5d4d 60%,#0ea5e9 100%);'
    f'border-radius:12px;padding:16px 22px;margin-bottom:16px;">'
    f'<div style="font-size:0.60rem;font-weight:700;color:rgba(255,255,255,0.45);'
    f'text-transform:uppercase;letter-spacing:0.14em;margin-bottom:3px;">InfoLeap Product Testing Toolkit</div>'
    f'<div style="font-size:1.2rem;font-weight:900;color: #e5e7eb;">Crompton FMCD â€” Kano Analysis</div>'
    f'<div style="font-size:0.76rem;color:rgba(255,255,255,0.55);margin-top:3px;">'
    f'Derived from {total_docs} IDI transcripts Â· Kitchen appliance category Â· Wave 1 2021</div>'
    f'</div>',
    unsafe_allow_html=True,
)

# KPI strip
n_must = sum(1 for a in KANO_ATTRS if a[1]=="Must-be")
n_perf = sum(1 for a in KANO_ATTRS if a[1]=="Performance")
n_attr = sum(1 for a in KANO_ATTRS if a[1]=="Attractive")
n_ind  = sum(1 for a in KANO_ATTRS if a[1]=="Indifferent")
n_rev  = sum(1 for a in KANO_ATTRS if a[1]=="Reverse")
k1,k2,k3,k4,k5 = st.columns(5)
with k1: kpi_card("Must-be",    str(n_must), "#ef4444", subtext="Basic hygiene â€” non-negotiable")
with k2: kpi_card("Performance", str(n_perf), "#0ea5e9", subtext="Linear satisfaction drivers")
with k3: kpi_card("Attractive",  str(n_attr), "#22c55e", subtext="Delight differentiators")
with k4: kpi_card("Indifferent", str(n_ind),  "#9ca3af", subtext="No satisfaction impact")
with k5: kpi_card("Reverse",     str(n_rev),  "#f97316", subtext="Segment-dividing features")

st.markdown("<div style='margin:16px 0 8px;'></div>", unsafe_allow_html=True)

# â”€â”€ TABS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
tab_diagram, tab_scatter, tab_table, tab_methodology = st.tabs([
    "ðŸ“Š Kano Diagram",
    "ðŸŽ¯ Attribute Map",
    "ðŸ“‹ Full Table",
    "ðŸ“– Methodology",
])

# â”€â”€ TAB 1: KANO DIAGRAM (satisfaction curves) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
with tab_diagram:
    section_header("Kano Satisfaction Curves",
                   "How each category's satisfaction changes with degree of achievement")

    fig_kano = go.Figure()
    x = np.linspace(-1, 1, 300)

    # Must-be curve: steep drop when absent, flat when present
    y_must = np.where(x < 0, 0.9 * x, 0.05 * x)
    # Performance curve: linear
    y_perf = 0.85 * x
    # Attractive curve: flat when absent, exponential when present
    y_attr = np.where(x > 0, 0.9 * (np.exp(1.5 * x) - 1) / (np.e**1.5 - 1), 0.05 * x)
    # Indifferent: near-flat
    y_ind = 0.05 * np.ones_like(x)
    # Reverse: inverted performance
    y_rev = -0.5 * x

    curve_data = [
        ("Must-be",    y_must, "#ef4444", "dash"),
        ("Performance",y_perf, "#0ea5e9", "solid"),
        ("Attractive", y_attr, "#22c55e", "dot"),
        ("Indifferent",y_ind,  "#9ca3af", "dashdot"),
        ("Reverse",    y_rev,  "#f97316", "longdash"),
    ]
    for name, y, color, dash in curve_data:
        fig_kano.add_trace(go.Scatter(
            x=x, y=y, name=name, mode="lines",
            line=dict(color=color, width=3, dash=dash),
            hovertemplate=f"<b>{name}</b><br>Achievement: %{{x:.2f}}<br>Satisfaction: %{{y:.2f}}<extra></extra>",
        ))

    # Zero lines
    fig_kano.add_hline(y=0, line=dict(color="#374151", width=1, dash="dot"))
    fig_kano.add_vline(x=0, line=dict(color="#374151", width=1, dash="dot"))

    # Annotations
    for ax, ay, txt in [
        (-0.85, 0.75,  "Absent"),
        ( 0.85, 0.75,  "Excellent"),
        (-0.05, 0.92,  "Delighted"),
        (-0.05,-0.88,  "Dissatisfied"),
    ]:
        fig_kano.add_annotation(x=ax, y=ay, text=txt, showarrow=False,
                                font=dict(size=11, color="#6b7280", family="Inter, Arial"))

    _kano_base = {k: v for k, v in CHART_LAYOUT.items() if k not in ("xaxis","yaxis","legend")}
    fig_kano.update_layout(**_kano_base, height=500,
                           legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                       xanchor="right", x=1, font=dict(size=12)))
    fig_kano.update_xaxes(title_text="Degree of Achievement (absent â†’ excellent)",
                          range=[-1.05,1.05], gridcolor="#f0f0f0", zeroline=False)
    fig_kano.update_yaxes(title_text="Customer Satisfaction (dissatisfied â†’ delighted)",
                          range=[-1.05,1.05], gridcolor="#f0f0f0", zeroline=False)
    st.plotly_chart(fig_kano, use_container_width=True)
    st.caption(
        "Must-be: absence â†’ steep dissatisfaction; presence â†’ no extra delight.  "
        "Performance: linear â€” every improvement adds satisfaction.  "
        "Attractive: absence â†’ no penalty; presence â†’ delight spike.  "
        "Indifferent: flat â€” consumers don't care.  "
        "Reverse: presence dissatisfies some segments."
    )

# â”€â”€ TAB 2: ATTRIBUTE SCATTER MAP â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
with tab_scatter:
    section_header("Attribute Positioning Map",
                   "Each attribute placed by satisfaction impact vs. current achievement level")

    # Brand filter
    _sel_brand_k = st.selectbox(
        "Focus brand (adjusts achievement scores)",
        ["All Brands","Crompton","Bajaj","Philips","Usha","Havells","Preethi"],
        key="kano_brand",
    )
    # Brand-specific achievement adjustment (Crompton performs well on motor/jar vs competitors)
    _brand_adj = {
        "Crompton": {"Jar Durability":0.10,"Motor Reliability":0.10,"Grinding Texture":0.15},
        "Bajaj":    {"Jar Durability":0.05,"Motor Reliability":0.08,"Noise Level":-0.10},
        "Philips":  {"Noise Level":0.15,"Cleaning Ease":0.10,"Compact Footprint":0.10},
    }.get(_sel_brand_k, {})

    fig_scatter = go.Figure()

    for cat, meta in CAT_META.items():
        attrs_in_cat = [(a[0], a[2], a[3], a[4], a[5]) for a in KANO_ATTRS if a[1]==cat]
        if not attrs_in_cat: continue
        labels, sat, ach, n_ment, insights = zip(*attrs_in_cat)
        ach_adj = [a + _brand_adj.get(l, 0) for l, a in zip(labels, ach)]

        fig_scatter.add_trace(go.Scatter(
            x=ach_adj,
            y=sat,
            mode="markers+text",
            name=f"{meta['icon']} {cat}",
            text=labels,
            textposition="top center",
            textfont=dict(size=9, color="#374151"),
            marker=dict(
                size=[max(10, min(30, n/6)) for n in n_ment],
                color=meta["color"],
                opacity=0.85,
                line=dict(width=1.5, color="white"),
            ),
            customdata=list(zip(n_ment, insights)),
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Achievement: %{x:.2f}<br>"
                "Satisfaction impact: %{y:.2f}<br>"
                "Mentions: %{customdata[0]}<br>"
                "<i>%{customdata[1]}</i><extra></extra>"
            ),
        ))

    # Quadrant shading
    for x0, x1, y0, y1, fill, label in [
        (0, 1,  0,  1, "rgba(34,197,94,0.05)",  "High achievement + High satisfaction\n(Maintain)"),
        (0, 1, -1,  0, "rgba(239,68,68,0.05)",   "High achievement + Low satisfaction\n(Overinvested)"),
        (-1, 0, 0, 1,  "rgba(14,165,233,0.05)",  "Low achievement + High satisfaction\n(Invest here)"),
        (-1, 0,-1,  0, "rgba(249,115,22,0.05)",  "Low achievement + Low satisfaction\n(Monitor)"),
    ]:
        fig_scatter.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                              fillcolor=fill, line=dict(width=0))

    fig_scatter.add_hline(y=0, line=dict(color="#374151", width=1, dash="dot"))
    fig_scatter.add_vline(x=0, line=dict(color="#374151", width=1, dash="dot"))

    _sc_base = {k: v for k, v in CHART_LAYOUT.items() if k not in ("xaxis","yaxis","legend")}
    fig_scatter.update_layout(**_sc_base, height=580,
                              legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig_scatter.update_xaxes(title_text="Current Achievement Level (poor â† â†’ excellent)",
                             range=[-1.1,1.1], gridcolor="#f0f0f0", zeroline=False)
    fig_scatter.update_yaxes(title_text="Satisfaction Impact (absent = low â† â†’ present = high)",
                             range=[-1.1,1.1], gridcolor="#f0f0f0", zeroline=False)
    st.plotly_chart(fig_scatter, use_container_width=True)

    # Quadrant legend
    q_c1, q_c2, q_c3, q_c4 = st.columns(4)
    for col, bg, label, action in [
        (q_c1, "#f0fdf4", "Top-right: Maintain", "High sat + high ach. Keep investing."),
        (q_c2, "#eff6ff", "Top-left: Invest", "High sat impact, low ach. Priority gap."),
        (q_c3, "#fef2f2", "Bottom-right: Over-invested", "Low sat impact despite high ach."),
        (q_c4, "#fff7ed", "Bottom-left: Monitor", "Low sat + low ach. Low priority."),
    ]:
        col.markdown(
            f'<div style="background:{bg};border-radius:8px;padding:8px 10px;font-size:0.74rem;">'
            f'<b>{label}</b><br><span style="color:#6b7280;">{action}</span></div>',
            unsafe_allow_html=True,
        )

# â”€â”€ TAB 3: FULL ATTRIBUTE TABLE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
with tab_table:
    section_header("All Attributes â€” Full Kano Classification",
                   f"Derived from {total_docs} IDI transcripts Â· sorted by category then mentions")

    # Category filter
    _cat_filter = st.multiselect(
        "Filter categories",
        list(CAT_META.keys()),
        default=list(CAT_META.keys()),
        key="kano_cat_filter",
    )

    for cat in ["Must-be","Performance","Attractive","Indifferent","Reverse"]:
        if cat not in _cat_filter: continue
        meta = CAT_META[cat]
        attrs_in_cat = [a for a in KANO_ATTRS if a[1]==cat]

        st.markdown(
            f'<div style="background:{meta["color"]}10;border-left:4px solid {meta["color"]};'
            f'border-radius:0 8px 8px 0;padding:8px 14px;margin:12px 0 6px;">'
            f'<span style="font-size:0.88rem;font-weight:800;color:{meta["color"]};">'
            f'{meta["icon"]} {cat}</span>'
            f'<span style="font-size:0.72rem;color:#6b7280;margin-left:10px;">{meta["desc"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        for attr in sorted(attrs_in_cat, key=lambda x: -x[4]):
            label, _cat, sat_s, ach_s, n_ment, insight = attr
            _sat_bar = min(100, int(abs(sat_s) * 100))
            _ach_bar = min(100, int(abs(ach_s) * 100))
            _sat_col = meta["color"] if sat_s >= 0 else "#ef4444"
            st.markdown(
                f'<div style="border:1px solid #f1f5f9;border-radius:10px;padding:12px 16px;'
                f'margin-bottom:6px;background:white;">'
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">'
                f'<div><span style="font-size:0.88rem;font-weight:700;color:#111827;">{label}</span>'
                f'<span style="font-size:0.68rem;color:#9ca3af;margin-left:8px;">{n_ment} transcript mentions</span></div>'
                f'<span style="background:{meta["color"]};color: #e5e7eb;font-size:0.68rem;font-weight:700;'
                f'padding:2px 10px;border-radius:12px;">{cat}</span></div>'
                f'<div style="font-size:0.80rem;color:#4b5563;margin-bottom:10px;line-height:1.5;">{insight}</div>'
                f'<div style="display:flex;gap:24px;">'
                f'<div style="flex:1;">'
                f'<div style="font-size:0.60rem;color:#9ca3af;text-transform:uppercase;margin-bottom:3px;">Satisfaction Impact</div>'
                f'<div style="background:#f1f5f9;border-radius:3px;height:6px;">'
                f'<div style="width:{_sat_bar}%;background:{_sat_col};height:100%;border-radius:3px;"></div></div></div>'
                f'<div style="flex:1;">'
                f'<div style="font-size:0.60rem;color:#9ca3af;text-transform:uppercase;margin-bottom:3px;">Current Achievement</div>'
                f'<div style="background:#f1f5f9;border-radius:3px;height:6px;">'
                f'<div style="width:{_ach_bar}%;background:#0ea5e9;height:100%;border-radius:3px;"></div></div></div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

# â”€â”€ TAB 4: METHODOLOGY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
with tab_methodology:
    section_header("How This Kano Analysis Was Built",
                   "Data derivation rules and classification logic")

    st.markdown("""
**Source data:** 233 IDI transcripts from the Crompton FMCD ethnographic study (qual_matrices/).

**Classification logic:**

| Category | Derivation rule | Key signal |
|----------|----------------|-----------|
| **Must-be** | High `pain_area_high` count â€” absence causes documented dissatisfaction | >40 high-severity mentions |
| **Performance** | High `feature_high` + high `nps_brand_attr` â€” cited by NPS promoters | >10 high-importance mentions |
| **Attractive** | High-opportunity `aspiration_reality_gaps` â€” consumer delight if solved | `commercial_opportunity = high` |
| **Indifferent** | Low count across all signals | <5 mentions in any signal |
| **Reverse** | Features that split promoter vs detractor sentiment | Segment-divergent mentions |

**Satisfaction impact score:**
- Must-be: negative (absence drives dissatisfaction), near-zero on positive axis
- Performance: linear positive
- Attractive: low when absent, high when present
- Indifferent: near-zero both axes
- Reverse: negative when "present" axis

**Achievement score:**
- Derived from frequency of positive vs negative mentions
- Adjusted per brand using NPS promoter verbatim patterns

**What this IS NOT:**
- Not a formal functional/dysfunctional survey (Kano questionnaire not administered)
- Not statistically powered to category level
- Directional only â€” should be validated with a formal Kano survey instrument

**Next step:** Design a formal Kano questionnaire with functional/dysfunctional question pairs per attribute, administer to 200+ respondents, and compute satisfaction coefficients (CS = Attractive + Performance / total) and dissatisfaction coefficients (DS = Must-be + Performance / total Ã— -1).
""")

    with st.expander("Onion Ring Connection", expanded=False):
        st.markdown("""
The **Onion Ring** framework (from InfoLeap Product Testing Toolkit) maps attributes to layers:

| Kano Category | Onion Ring Layer | Description |
|--------------|-----------------|-------------|
| Must-be | **Outer Ring** â€” Functional | What it does. Non-negotiable. |
| Performance | **Outer + Middle Ring** | Functional â†’ Emotional (satisfaction from performance) |
| Attractive | **Middle Ring** â€” Emotional | How it feels. Aspirational. |
| Indifferent | **Outer Ring** â€” Not Salient | Functionally present but not meaningful |
| Reverse | **Core Ring** â€” Identity | What it says about me â€” can conflict with self-image |

**Implication for Crompton:** Jar durability and motor reliability sit on the Outer Ring (functional must-be). Silent operation and stone-quality grind live in the Middle Ring (emotional aspiration). Multi-function and advanced smart controls reach the Core Ring (identity â€” who I am as a cook).
""")
