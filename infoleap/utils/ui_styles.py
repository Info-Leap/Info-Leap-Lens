import streamlit as st
from infoleap.utils.context import ContextEngine

def inject_pulse_styles():
    """Injects the high-fidelity InfoLeap Pulse CSS + Google Fonts."""
    # Google Fonts â€” preconnect + import for all chart font options
    st.markdown(
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?'
        'family=Inter:wght@400;500;600;700;800&'
        'family=Montserrat:wght@400;600;700;800&'
        'family=Playfair+Display:wght@400;600;700&'
        'family=IBM+Plex+Sans:wght@400;500;600;700&'
        'family=Nunito:wght@400;600;700;800&'
        'family=Poppins:wght@400;500;600;700&'
        'display=swap" rel="stylesheet">',
        unsafe_allow_html=True,
    )
    st.markdown("""
        <style>
        /* Global Theme - Pulse Light Mode */
        :root {
            --primary-dark: #1a5d4d;
            --primary-vibrant: #30a76a;
            --bg-light: #f9fafb;
            --card-bg: #ffffff;
            --text-main: #111827;
            --text-secondary: #4b5563;
            --accent-red: #ef4444;
            
            /* Midnight Glass Foundation */
            --midnight-slate: #0f172a;
            --glass-bg: rgba(255, 255, 255, 0.05);
            --glass-border: rgba(255, 255, 255, 0.1);
            --neon-cyan: #38bdf8;
        }

        .stApp {
            background-color: var(--bg-light);
            color: var(--text-main);
        }

        /* Glass Card Utility */
        .glass-card {
            background: var(--glass-bg);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border: 1px solid var(--glass-border);
            border-radius: 12px;
            padding: 20px;
            color: #e5e7eb;
        }

        /* Sidebar Customization */
        [data-testid="stSidebar"] {
            background-color: var(--primary-dark) !important;
            color: #e5e7eb !important;
        }
        [data-testid="stSidebar"] * {
            color: #e5e7eb !important;
        }
        [data-testid="stSidebarNav"] {
            background-color: transparent !important;
        }

        /* â”€â”€ Sidebar widget label visibility â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
        /* Labels above selectbox / radio / multiselect */
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] .stSelectbox label,
        [data-testid="stSidebar"] .stMultiSelect label,
        [data-testid="stSidebar"] .stRadio label,
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"] span {
            color: #e5e7eb !important;
            font-weight: 600 !important;
            opacity: 1 !important;
        }

        /* Selectbox/multiselect input box â€” white bg, black text */
        [data-testid="stSidebar"] .stSelectbox > div > div,
        [data-testid="stSidebar"] .stMultiSelect > div > div {
            background-color: rgba(255,255,255,0.12) !important;
            border: 1px solid rgba(255,255,255,0.3) !important;
            border-radius: 8px !important;
        }

        /* Displayed selected value text */
        [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] span,
        [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] div,
        [data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] span,
        [data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] div {
            color: #e5e7eb !important;
        }

        /* Dropdown arrow icon */
        [data-testid="stSidebar"] .stSelectbox svg,
        [data-testid="stSidebar"] .stMultiSelect svg {
            fill: white !important;
        }

        /* Dropdown popup â€” MUST be light so options are readable */
        [data-baseweb="popover"] ul,
        [data-baseweb="menu"] {
            background-color: #ffffff !important;
        }
        [data-baseweb="popover"] li,
        [data-baseweb="menu"] li,
        [data-baseweb="option"] {
            color: #111827 !important;
        }
        [data-baseweb="option"]:hover {
            background-color: #f0fdf4 !important;
            color: #1a5d4d !important;
        }

        /* Metric/number widgets in sidebar */
        [data-testid="stSidebar"] [data-testid="stMetricValue"],
        [data-testid="stSidebar"] [data-testid="stMetricLabel"] {
            color: #e5e7eb !important;
        }
        
        /* Sidebar Logo/Header */
        .sidebar-brand {
            padding: 1rem 0;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .brand-circle {
            width: 32px;
            height: 32px;
            background-color: var(--primary-vibrant);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: #e5e7eb;
        }

        /* Cards & Containers */
        .pulse-card {
            background-color: var(--card-bg);
            border: 1px solid #e5e7eb;
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        
        .hero-insight {
            font-size: 2rem;
            line-height: 1.2;
            font-weight: 700;
            color: var(--text-main);
            margin-bottom: 2rem;
        }
        @media (max-width: 768px) {
            .hero-insight {
                font-size: 1.4rem;
            }
        }
        .insight-highlight {
            color: var(--accent-red);
        }

        /* Chat Bubbles */
        [data-testid="stChatMessage"] {
            background-color: #ffffff !important;
            border-radius: 16px !important;
            border: 1px solid #e5e7eb !important;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05) !important;
        }
        [data-testid="stChatMessage"][data-testid="stChatMessageUser"] {
            background-color: #f0fdf4 !important; /* Very light green */
            border: 1px solid #dcfce7 !important;
        }

        /* Scoreboard Bars */
        .scoreboard-bar-container {
            width: 100%;
            height: 8px;
            background-color: #f3f4f6;
            border-radius: 4px;
            margin: 8px 0;
        }
        .scoreboard-bar-fill {
            height: 100%;
            background-color: var(--primary-vibrant);
            border-radius: 4px;
        }

        /* Buttons & Actions â€” primary (type="primary") stays solid/vibrant for the one main
           call-to-action per section; secondary (the default) is now visually distinct (outline,
           muted) so utility buttons (Select all/none, Reset, etc.) stop competing for attention
           with the actual next-step action. Previously every button â€” primary or secondary â€” was
           forced to the same solid green, so an entire screen of buttons looked equally important
           and nothing signaled "this is the one to click next." */
        .stButton button,
        .stButton button[kind="secondaryFormSubmit"] {
            border-radius: 8px !important;
            background-color: transparent !important;
            color: var(--primary-vibrant) !important;
            border: 1.5px solid var(--primary-vibrant) !important;
        }
        .stButton button:hover {
            background-color: var(--primary-vibrant) !important;
            color: #e5e7eb !important;
        }
        .stButton button[kind="primary"],
        .stButton button[kind="primaryFormSubmit"] {
            background-color: var(--primary-vibrant) !important;
            color: #e5e7eb !important;
            border: none !important;
        }
        .stButton button[kind="primary"]:hover {
            filter: brightness(0.92);
        }
        
        /* Custom Loader - GLIDE Steps */
        .glide-step {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 8px;
            color: var(--text-secondary);
        }
        .glide-step-check {
            color: var(--primary-vibrant);
            font-weight: bold;
        }
        /* Sidebar Footer */
        .sidebar-footer {
            margin-top: auto;
            padding: 1.5rem 1rem;
            font-size: 0.7rem;
            color: #a7f3d0;
            border-top: 1px solid rgba(255,255,255,0.1);
        }

        /* â”€â”€ Text Highlighting â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
        mark {
            background: #fef08a;
            color: #111827;
            padding: 0px 3px;
            border-radius: 3px;
            font-weight: 700;
        }

        /* â”€â”€ Hero Banner â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
        .bh-hero {
            background: linear-gradient(135deg, #0a2e22 0%, #1a5d4d 55%, #22876b 100%);
            border-radius: 16px;
            padding: 20px 24px 18px;
            margin-bottom: 12px;
            color: #e5e7eb;
        }
        .bh-hero-eyebrow {
            font-size: 0.62rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: rgba(255,255,255,0.5);
            margin: 0 0 6px;
            font-weight: 600;
        }
        .bh-hero-name {
            font-size: 2rem;
            font-weight: 900;
            color: #e5e7eb;
            letter-spacing: -0.02em;
            line-height: 1.05;
            margin: 0 0 10px;
        }
        .bh-hero-meta {
            font-size: 0.78rem;
            color: rgba(255,255,255,0.55);
            margin: 0 0 14px;
        }
        .bh-score-tbl {
            border-collapse: separate;
            border-spacing: 20px 0;
            margin-top: 8px;
        }
        .bh-score-td {
            background: rgba(255,255,255,0.14);
            border-radius: 12px;
            padding: 14px 22px;
            text-align: center;
            min-width: 100px;
        }
        .bh-score-val {
            font-size: 1.5rem;
            font-weight: 900;
            color: #e5e7eb;
            line-height: 1.1;
        }
        .bh-score-lbl {
            font-size: 0.55rem;
            text-transform: uppercase;
            color: rgba(255,255,255,0.6);
            letter-spacing: 0.06em;
            margin-top: 3px;
        }

        /* â”€â”€ Brand Health Enhancements â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
        [data-testid="stMetric"] {
            background: white;
            border-radius: 12px;
            padding: 18px 20px !important;
            border: 1px solid #e5e7eb;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        [data-testid="stMetricLabel"] p {
            font-size: 0.68rem !important;
            font-weight: 700 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.08em !important;
            color: #6b7280 !important;
        }
        [data-testid="stMetricValue"] {
            font-size: 1.9rem !important;
            font-weight: 800 !important;
            color: #111827 !important;
            line-height: 1.1 !important;
        }
        [data-testid="stMetricDeltaIcon"] { display: none; }
        div[data-testid="stHorizontalBlock"] > div[data-testid="stVerticalBlock"] {
            gap: 0 !important;
        }
        .bh-section-header {
            margin: 28px 0 16px;
            padding-bottom: 10px;
            border-bottom: 2px solid #f3f4f6;
        }
        .bh-section-header h2 {
            margin: 0;
            font-size: 1rem;
            font-weight: 700;
            color: #1a5d4d;
            letter-spacing: -0.01em;
        }
        .bh-section-header p {
            margin: 3px 0 0;
            font-size: 0.78rem;
            color: #9ca3af;
        }
        .bh-ai-card {
            background: #fafafa;
            border: 1px solid #f0f0f0;
            border-radius: 10px;
            padding: 18px;
            box-sizing: border-box;
        }
        .bh-ai-label {
            font-size: 0.58rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            font-weight: 800;
            margin-bottom: 10px;
        }
        .bh-ai-body {
            font-size: 0.87rem;
            line-height: 1.75;
            color: #374151;
        }
        /* â”€â”€ Executive Hero â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
        .exec-hero {
            background: radial-gradient(circle at top right, #1e293b, #0f172a);
            border-radius: 24px;
            padding: 40px;
            margin-bottom: 30px;
            border: 1px solid rgba(255,255,255,0.08);
            position: relative;
            overflow: hidden;
            box-shadow: 0 20px 50px rgba(0,0,0,0.3);
        }
        .exec-hero::before {
            content: "";
            position: absolute;
            top: -20%;
            right: -10%;
            width: 50%;
            height: 100%;
            background: radial-gradient(circle, rgba(56, 189, 248, 0.1) 0%, rgba(0,0,0,0) 70%);
            z-index: 0;
        }
        .exec-hero-eyebrow {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.2em;
            color: var(--neon-cyan);
            font-weight: 800;
            margin: 0 0 10px;
        }
        .exec-hero-name {
            font-size: 3.5rem;
            font-weight: 900;
            color: #e5e7eb;
            letter-spacing: -0.04em;
            margin: 0 0 15px;
            line-height: 1;
        }
        .exec-badge {
            background: var(--neon-cyan);
            color: #0f172a;
            font-size: 0.7rem;
            font-weight: 800;
            padding: 4px 14px;
            border-radius: 30px;
            text-transform: uppercase;
            box-shadow: 0 0 20px rgba(56, 189, 248, 0.3);
        }
        
        /* â”€â”€ Shared Component Styles â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
        /* pulse-kpi-card â€” standard metric tile */
        .pulse-kpi-card {
            border-radius: 12px;
            padding: 14px 16px;
            text-align: center;
            margin-bottom: 6px;
        }
        .pulse-kpi-label {
            font-size: 0.60rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.07em;
            color: #9ca3af;
            margin-bottom: 5px;
        }
        .pulse-kpi-value {
            font-size: 1.6rem;
            font-weight: 900;
            line-height: 1.1;
        }

        /* pulse-section-header â€” standard section title bar */
        .pulse-section-header {
            border-left: 3px solid #1a5d4d;
            border-radius: 0 8px 8px 0;
            padding: 8px 16px;
            margin: 22px 0 14px;
        }
        .pulse-section-title {
            font-size: 0.92rem;
            font-weight: 800;
            letter-spacing: -0.01em;
        }

        /* pulse-empty-state â€” standard no-data placeholder */
        .pulse-empty-state {
            border: 2px dashed #e5e7eb;
            border-radius: 12px;
            padding: 24px 20px;
            text-align: center;
            min-height: 80px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }

        /* â”€â”€ Tooltips â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
        .hs-tooltip-anchor {
            cursor: help;
            font-size: 0.8rem;
            opacity: 0.6;
            position: relative;
            display: inline-block;
            transition: opacity 0.2s;
        }
        .hs-tooltip-anchor:hover { opacity: 1; }
        .hs-tooltip-box {
            display: none;
            position: absolute;
            bottom: 140%;
            left: 50%;
            transform: translateX(-50%);
            background: #1e293b;
            color: #f1f5f9;
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 12px;
            padding: 16px;
            font-size: 0.75rem;
            line-height: 1.6;
            width: 280px;
            z-index: 9999;
            text-align: left;
            white-space: normal;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            backdrop-filter: blur(10px);
        }
        .hs-tooltip-anchor:hover .hs-tooltip-box {
            display: block;
        }
        </style>
    """, unsafe_allow_html=True)

# â”€â”€ Shared Design Tokens â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Single source of truth for all views. Import from here, never redefine locally.

COLORS = {
    # Brand palette
    "teal":    "#1a5d4d",
    "green":   "#30a76a",
    "green_lt":"#86efac",
    "blue":    "#0ea5e9",
    "purple":  "#7c3aed",
    "violet":  "#8b5cf6",
    "amber":   "#f59e0b",
    "red":     "#ef4444",
    "cyan":    "#06b6d4",
    "orange":  "#f97316",
    "emerald": "#10b981",
    "pink":    "#ec4899",
    "indigo":  "#6366f1",
    # Neutral
    "neutral": "#9ca3af",
    "dark":    "#111827",
    "mid":     "#374151",
    "light":   "#6b7280",
    "border":  "#e5e7eb",
    "bg":      "#f9fafb",
    "white":   "#ffffff",
}

# Ordered palette for chart series / brand colors
BRAND_COLORS = [
    "#1a5d4d", "#0ea5e9", "#7c3aed", "#f59e0b", "#ef4444",
    "#06b6d4", "#30a76a", "#8b5cf6", "#f97316", "#10b981",
    "#ec4899", "#6366f1", "#14b8a6", "#a855f7", "#eab308",
]

ZONE_COLORS = {
    "North": "#0ea5e9",
    "South": "#30a76a",
    "East":  "#f59e0b",
    "West":  "#7c3aed",
}

# Standard Plotly chart layout â€” use in every update_layout() call
CHART_LAYOUT = dict(
    paper_bgcolor="white",
    plot_bgcolor="white",
    font=dict(family="Inter, Arial, sans-serif", size=12, color="#374151"),
    margin=dict(l=16, r=16, t=40, b=24),
    legend=dict(
        orientation="h", yanchor="bottom", y=1.02,
        xanchor="right", x=1,
        font=dict(size=11),
    ),
    xaxis=dict(gridcolor="#f0f0f0", linecolor="#e5e7eb", zeroline=False),
    yaxis=dict(gridcolor="#f0f0f0", linecolor="#e5e7eb", zeroline=False),
)


# â”€â”€ Shared UI Component Functions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def section_header(title: str, subtitle: str = "", accent: str = None):
    """Standard section header used across all pages.
    Replaces: _section_title() in dashboard, _section_header() in brand_health,
    _section() in quote_explorer, st.subheader() in other pages.
    """
    import html as _h
    col = accent or COLORS["teal"]
    sub_html = (
        f'<span style="font-size:0.74rem;color:{COLORS["neutral"]};'
        f'font-weight:400;margin-left:10px;">{_h.escape(subtitle)}</span>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="background:linear-gradient(90deg,{col}10,transparent);'
        f'border-left:3px solid {col};border-radius:0 8px 8px 0;'
        f'padding:8px 16px;margin:22px 0 14px;">'
        f'<span style="font-size:0.92rem;font-weight:800;color:{col};">{_h.escape(title)}</span>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def kpi_card(label: str, value: str, color: str = None, subtext: str = "",
             formula: str = ""):
    """Standard KPI card used across all pages.
    Replaces: _kpi_pill() in dashboard, _metric_card() in brand_health, _kpi() in quote_explorer.
    """
    import html as _h
    col = color or COLORS["teal"]
    sub_html = (
        f'<div style="font-size:0.68rem;color:{COLORS["neutral"]};margin-top:4px;'
        f'line-height:1.4;">{_h.escape(subtext)}</div>'
        if subtext else ""
    )
    formula_html = (
        f'<div style="font-size:0.60rem;color:{COLORS["light"]};margin-top:6px;'
        f'font-style:italic;line-height:1.4;border-top:1px solid {COLORS["border"]};'
        f'padding-top:5px;">{_h.escape(formula)}</div>'
        if formula else ""
    )
    st.markdown(
        f'<div style="border:1px solid {col}20;border-radius:12px;padding:14px 16px;'
        f'background:{col}06;text-align:center;margin-bottom:6px;">'
        f'<div style="font-size:0.60rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:0.07em;color:{COLORS["neutral"]};margin-bottom:5px;">'
        f'{_h.escape(label)}</div>'
        f'<div style="font-size:1.6rem;font-weight:900;color:{col};line-height:1.1;">'
        f'{_h.escape(str(value))}</div>'
        f'{sub_html}{formula_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def empty_state(message: str = "No data available", icon: str = "â—‹",
                height: int = None, action_hint: str = ""):
    """Standard empty / no-data state used across all pages.
    Replaces: st.info(), st.error(), custom dashed HTML boxes.
    """
    import html as _h
    h_style = f"min-height:{height}px;" if height else "min-height:80px;"
    hint_html = (
        f'<div style="font-size:0.74rem;color:{COLORS["neutral"]};margin-top:8px;">'
        f'{_h.escape(action_hint)}</div>'
        if action_hint else ""
    )
    st.markdown(
        f'<div style="border:2px dashed {COLORS["border"]};border-radius:12px;'
        f'padding:24px 20px;text-align:center;{h_style}'
        f'display:flex;flex-direction:column;align-items:center;justify-content:center;">'
        f'<div style="font-size:1.4rem;margin-bottom:8px;opacity:0.4;">{icon}</div>'
        f'<div style="font-size:0.84rem;color:{COLORS["light"]};font-weight:500;">'
        f'{_h.escape(message)}</div>'
        f'{hint_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def page_banner(title: str, subtitle: str = "", gradient: str = None,
                eyebrow: str = ""):
    """Top-of-page gradient banner. Replaces ad-hoc gradient headers in settings,
    repository, quote_explorer.
    """
    import html as _h
    grad = gradient or f"linear-gradient(135deg,#0a2e22 0%,{COLORS['teal']} 60%,{COLORS['blue']} 100%)"
    ey_html = (
        f'<div style="font-size:0.60rem;font-weight:700;color:rgba(255,255,255,0.45);'
        f'text-transform:uppercase;letter-spacing:0.14em;margin-bottom:4px;">'
        f'{_h.escape(eyebrow)}</div>'
        if eyebrow else ""
    )
    sub_html = (
        f'<div style="font-size:0.78rem;color:rgba(255,255,255,0.55);margin-top:4px;">'
        f'{_h.escape(subtitle)}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="background:{grad};border-radius:14px;padding:18px 24px 16px;'
        f'margin-bottom:16px;color: #e5e7eb;">'
        f'{ey_html}'
        f'<div style="font-size:1.35rem;font-weight:900;color: #e5e7eb;'
        f'letter-spacing:-0.01em;">{_h.escape(title)}</div>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def sidebar_context_block(brand=None, respondents=6631, studies=12, active=4, show_category=True):
    """Renders the 'Active Brand' context block in the sidebar.

    show_category=False: hide Switch Category selector (e.g. Brand Health â€” Wave 1 has no
    category dimension so the selector has no effect there).
    """
    from infoleap.utils.context import ContextEngine
    ContextEngine.init()
    active_cat = st.session_state.get("active_category", "All")
    display_brand = brand if brand else st.session_state.get("active_brand", "All Brands")

    with st.sidebar:
        if show_category:
            # Global Category Selector
            categories = ["All", "Ceiling Fans", "Mixer Grinder", "Air Cooler", "Water Heater", "Water Pumps", "LED Tube Light"]
            if active_cat not in categories:
                categories.append(active_cat)

            new_cat = st.selectbox("Switch Category", categories, index=categories.index(active_cat))

            if new_cat != active_cat:
                st.session_state.active_category = new_cat
                st.rerun()
        else:
            st.caption("Category filter: N/A (single-wave data)")

        st.markdown(f"""
            <div style="margin-top: 1rem; padding: 1rem; background-color: rgba(255,255,255,0.05); border-radius: 8px;">
                <div style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #a7f3d0; margin-bottom: 0.5rem;">Active Workspace</div>
                <div style="font-size: 1.1rem; font-weight: bold;">{active_cat}</div>
                <div style="font-size: 0.85rem; color: #a7f3d0; margin-top: 0.2rem;">{display_brand}</div>
                <div style="margin-top: 1rem; display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                    <div>
                        <div style="font-size: 0.65rem; color: #a7f3d0;">Respondents</div>
                        <div style="font-size: 0.9rem; font-weight: bold;">{(f'{respondents:,}' if isinstance(respondents, (int, float)) else respondents)}</div>
                    </div>
                    <div>
                        <div style="font-size: 0.65rem; color: #a7f3d0;">Studies</div>
                        <div style="font-size: 0.9rem; font-weight: bold;">{studies}</div>
                    </div>
                </div>
                <div style="margin-top: 0.5rem; font-size: 0.65rem; color: #a7f3d0;">
                    ðŸŸ¢ {active} users active in this workspace
                </div>
            </div>
        """, unsafe_allow_html=True)

def signal_score_card(label: str, value: int, higher_is_better: bool = True,
                      lo_label: str = "Low", hi_label: str = "High",
                      context: str = ""):
    """Standard signal score gauge card â€” identical across all project views.
    Use for Satisfaction / Risk / Opportunity and any derived composite score.
    value: 0-100 integer.
    higher_is_better: controls color (green=good side).
    """
    import html as _h
    if higher_is_better:
        col = COLORS["green"] if value >= 65 else (COLORS["amber"] if value >= 40 else COLORS["red"])
    else:
        col = COLORS["red"] if value >= 65 else (COLORS["amber"] if value >= 40 else COLORS["green"])
    ctx_html = (f'<div style="font-size:0.60rem;color:#9ca3af;margin-top:2px;">{_h.escape(context)}</div>'
                if context else "")
    st.markdown(
        f'<div style="background:{col}08;border:1px solid {col}22;border-radius:12px;'
        f'padding:12px 16px;display:flex;justify-content:space-between;align-items:center;">'
        f'<div>'
        f'<div style="font-size:0.58rem;font-weight:800;color:#9ca3af;text-transform:uppercase;'
        f'letter-spacing:0.06em;">{_h.escape(label)}</div>'
        f'<div style="font-size:0.60rem;color:#9ca3af;margin-top:2px;">'
        f'{_h.escape(lo_label)} &rarr; {_h.escape(hi_label)}</div>'
        f'{ctx_html}</div>'
        f'<div style="text-align:right;">'
        f'<div style="font-size:1.6rem;font-weight:900;color:{col};line-height:1;">{value}</div>'
        f'<div style="background:#e5e7eb;border-radius:3px;height:4px;width:60px;margin-top:5px;">'
        f'<div style="width:{value}%;background:{col};height:100%;border-radius:3px;"></div>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )


def scoreboard_card(label, value, pct, delta=None, direction=None):
    """Renders a single scoreboard row with a horizontal bar and delta indicator."""
    delta_html = ""
    if delta is not None and direction is not None:
        color = "#10b981" if direction == "up" else "#ef4444" if direction == "down" else "#6b7280"
        symbol = "â–²" if direction == "up" else "â–¼" if direction == "down" else "â€¢"
        delta_html = f'<span style="color: {color}; margin-left: 8px; font-weight: bold;">{symbol} {abs(delta)}</span>'

    st.markdown(f"""
        <div style="margin-bottom: 1rem;">
            <div style="display: flex; justify-content: space-between; font-size: 0.85rem;">
                <span style="font-weight: 500;">{label}</span>
                <span style="color: #111827; font-weight: 600;">{value}{delta_html}</span>
            </div>
            <div class="scoreboard-bar-container">
                <div class="scoreboard-bar-fill" style="width: {pct}%;"></div>
            </div>
        </div>
    """, unsafe_allow_html=True)
