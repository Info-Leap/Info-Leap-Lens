"""
concept_testing_renderer.py â€” InfoLeap Pulse
=============================================
Data-driven concept testing study dashboard.
Zero hardcoded PPT content â€” all computed from matrices.

Generic: works for any concept_testing study_type project.
CoinDCX-specific field paths are accessed with _get() fallback safety.

Field structure (karat-coindcx matrices):
  respondent.{city, segment, gender, age_band, occupation}
  investor_archetype, life_stage, financial_anxiety_level
  portfolio_behavior.{portfolio_split.*, platforms_used, info_sources}
  gold_behavior.{formats_owned, gold_role, sgb_awareness, purchase_trigger}
  concept_understanding.{comprehension_score, route_shown}
  route1_evaluation.{...overall_appeal_score}
  route2_evaluation.{...overall_appeal_score}
  preferred_route, tagline_reaction.{preferred_tagline}
  key_claim_reactions[].{claim, reaction, understood, verbatim}
  coindcx_trust (top-level enum: high/medium/low), crypto_association_effect (top-level enum:
    positive/negative/neutral/conditional), trust_builders (top-level array), platform_association
    (top-level string) â€” none of these are nested under coindcx_trust despite the field's name
  adoption.{intent_score, drivers, barriers, barrier_verbatim}
  benchmark_comparisons[].{benchmark, comparison_type, verdict}
  nps_signal, emotional_resolution, pain_points[].*, all_passages[]*

Entry: render_concept_testing(proj, base_path, call_openrouter_fn)
"""
from __future__ import annotations

import html as _html
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

import plotly.graph_objects as go
import streamlit as st

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# DESIGN CONSTANTS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

_C = {
    "r1":      "#6366f1",   # indigo â€” Route 1
    "r2":      "#0ea5e9",   # sky    â€” Route 2
    "seg_dg":  "#f59e0b",   # amber  â€” Digital Gold
    "seg_st":  "#0d9488",   # teal   â€” Stock Investor
    "seg_fd":  "#8b5cf6",   # purple â€” FDMF
    "pos":     "#10b981",
    "neg":     "#ef4444",
    "neu":     "#6b7280",
    "amb":     "#f59e0b",
    "border":  "#e5e7eb",
    "surface": "#f8fafc",
    "text":    "#111827",
    "muted":   "#6b7280",
    "accent":  "#6366f1",
    "bg_deep": "#f1f5f9",
}

_SEG_COLORS: dict[str, str] = {}

_SENT_C = {
    "positive":   _C["pos"],
    "negative":   _C["neg"],
    "neutral":    _C["neu"],
    "ambivalent": _C["amb"],
    "skeptical":  "#f97316",
    "confused":   "#8b5cf6",
}

_FONT = "Inter, system-ui, Arial, sans-serif"

_CHART_BASE = dict(
    font=dict(family=_FONT, size=11, color=_C["text"]),
    paper_bgcolor="rgba(0,0,0,0)",   # transparent â€” works light + dark Streamlit themes
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=20, r=20, t=44, b=20),
    showlegend=False,
    hoverlabel=dict(
        bgcolor="white", font_size=11, font_family=_FONT,
        bordercolor=_C["border"],
    ),
)

# Subtle axis grid line style reused across charts
_GRID_STYLE = dict(showgrid=True, gridcolor="#f0f4f8", gridwidth=0.5, zeroline=False)
_NO_GRID    = dict(showgrid=False, zeroline=False)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# PURE HELPERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _get(obj: Any, path: str) -> Any:
    try:
        for k in path.split("."):
            if isinstance(obj, dict):
                obj = obj.get(k)
            elif isinstance(obj, list) and k.isdigit():
                obj = obj[int(k)]
            else:
                return None
        return obj
    except Exception:
        return None


def _esc(t: Any) -> str:
    return _html.escape(str(t) if t is not None else "")


def _fmt_val(v: Any) -> str:
    """Format snake_case / lowercase field values for human display."""
    if v is None:
        return "â€”"
    s = str(v).strip()
    if not s or s.lower() in ("none", "not_mentioned", "unknown", "â€”"):
        return "â€”"
    return s.replace("_", " ").title()


def _avg(vals: list) -> Optional[float]:
    nums = [float(v) for v in vals if v is not None and isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else None


def _pct_str(part: int, total: int) -> str:
    return f"{round(100 * part / total)}%" if total else "â€”"


def _count_field(matrices: list[dict], path: str) -> dict[str, int]:
    c: Counter = Counter()
    for m in matrices:
        v = _get(m, path)
        if v is not None and str(v).strip() not in ("", "None", "not_mentioned", "unknown"):
            c[str(v)] += 1
    return dict(c.most_common())


def _count_list_field(matrices: list[dict], path: str) -> dict[str, int]:
    c: Counter = Counter()
    for m in matrices:
        v = _get(m, path)
        if isinstance(v, list):
            for item in v:
                s = str(item).strip()
                if s and s not in ("None", "not_mentioned"):
                    c[s] += 1
        elif v and str(v).strip() not in ("", "None", "not_mentioned"):
            c[str(v)] += 1
    return dict(c.most_common())


def _group_field(matrices: list[dict], path: str) -> dict[str, list[dict]]:
    """Like _count_field but returns {label: [matching matrices]}. Same sort order."""
    groups: dict[str, list] = {}
    for m in matrices:
        v = _get(m, path)
        if v is not None and str(v).strip() not in ("", "None", "not_mentioned", "unknown"):
            groups.setdefault(str(v), []).append(m)
    return dict(sorted(groups.items(), key=lambda x: -len(x[1])))


def _group_list_field(matrices: list[dict], path: str) -> dict[str, list[dict]]:
    """Like _count_list_field but returns {label: [matching matrices]}."""
    groups: dict[str, list] = {}
    for m in matrices:
        v = _get(m, path)
        if isinstance(v, list):
            for item in v:
                s = str(item).strip()
                if s and s not in ("None", "not_mentioned"):
                    groups.setdefault(s, []).append(m)
        elif v and str(v).strip() not in ("", "None", "not_mentioned"):
            groups.setdefault(str(v), []).append(m)
    return dict(sorted(groups.items(), key=lambda x: -len(x[1])))


# â”€â”€ CoinDCX trust field normalizers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# This project went through a schema shape change mid-extraction: legacy matrices (found live:
# 16/23) store trust detail as a nested object under the top-level "coindcx_trust" key
# (coindcx_trust.crypto_trust_gap, .trust_builders_cited, .spontaneous_coindcx_association â€”
# a real GAP field, high=bad). A later schema revision flattened "coindcx_trust" itself into a
# plain enum TRUST LEVEL (high=good) for newer extractions (4/23), demoting the old sub-fields
# to separate top-level fields (crypto_association_effect, trust_builders, platform_association)
# that were never actually populated for those records either. Reading only one shape silently
# drops the other â€” these normalizers merge both so no respondent's data is invisible to the
# dashboard regardless of which schema generation extracted them.

def _trust_gap_value(m: dict) -> str | None:
    """Returns one low/medium/high GAP value (high = bad, crypto identity undermines trust)
    regardless of which schema generation this matrix came from."""
    ct = m.get("coindcx_trust")
    if isinstance(ct, dict):
        v = ct.get("crypto_trust_gap")
        return str(v).strip().lower() if v else None
    if isinstance(ct, str) and ct.strip():
        # New shape stores a TRUST LEVEL (high=good) â€” invert to gap semantics (high=bad).
        return {"high": "low", "medium": "medium", "low": "high"}.get(ct.strip().lower())
    return None


def _trust_builders_list(m: dict) -> list:
    """Always returns a list, never a bare string â€” found live: 3/23 matrices have
    top-level trust_builders as a scalar string instead of an array (an extraction
    inconsistency, not something this normalizer should propagate as char-by-char
    iteration to callers)."""
    ct = m.get("coindcx_trust")
    if isinstance(ct, dict) and ct.get("trust_builders_cited"):
        v = ct["trust_builders_cited"]
    else:
        v = m.get("trust_builders")
    if isinstance(v, list):
        return v
    if v:
        return [v]
    return []


def _platform_association_value(m: dict) -> str:
    """Returns a plain readable string regardless of whether the source is a bare string or a
    list â€” found live: some matrices store this as a single-item list, and a raw str() cast on
    a list produces its literal Python repr (e.g. "['Crypto scams create skepticism']") shown
    verbatim in the UI instead of the actual sentence."""
    ct = m.get("coindcx_trust")
    if isinstance(ct, dict) and ct.get("spontaneous_coindcx_association"):
        v = ct["spontaneous_coindcx_association"]
    else:
        v = m.get("platform_association")
    if isinstance(v, list):
        return "; ".join(str(x).strip() for x in v if str(x).strip())
    return str(v or "").strip()


def _count_trust_gap(matrices: list[dict]) -> dict[str, int]:
    c: Counter = Counter()
    for m in matrices:
        v = _trust_gap_value(m)
        if v:
            c[v] += 1
    return dict(c.most_common())


def _group_trust_gap(matrices: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list] = {}
    for m in matrices:
        v = _trust_gap_value(m)
        if v:
            groups.setdefault(v, []).append(m)
    return dict(sorted(groups.items(), key=lambda x: -len(x[1])))


def _count_trust_builders(matrices: list[dict]) -> dict[str, int]:
    c: Counter = Counter()
    for m in matrices:
        for item in _trust_builders_list(m):
            s = str(item).strip()
            if s:
                c[s] += 1
    return dict(c.most_common())


def _group_trust_builders(matrices: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list] = {}
    for m in matrices:
        for item in _trust_builders_list(m):
            s = str(item).strip()
            if s:
                groups.setdefault(s, []).append(m)
    return dict(sorted(groups.items(), key=lambda x: -len(x[1])))


_MAX_TOOLTIP_ROWS = 10


def _resp_tooltip_html(resp_list: list[dict], label: str = "", max_rows: int = _MAX_TOOLTIP_ROWS) -> str:
    """Compact HTML respondent table for Plotly hover tooltip."""
    n = len(resp_list)
    if n == 0:
        return f"<b>{_esc(label)}</b> â€” 0 respondents"
    rows = ""
    for i, m in enumerate(resp_list[:max_rows]):
        rid  = str(_get(m, "doc_id") or _get(m, "respondent.id") or f"R{i+1:02d}").strip()[:10]
        city = str(_get(m, "respondent.city") or "â€”").strip()[:12]
        seg  = str(_get(m, "respondent.segment") or "â€”").strip()[:10]
        age  = str(_get(m, "respondent.age_band") or "â€”").strip()[:8]
        gen  = str(_get(m, "respondent.gender") or "").strip()[:1].upper() or "â€”"
        rows += f"{_esc(rid)} Â· {_esc(city)} Â· {_esc(seg)} Â· {_esc(age)} Â· {_esc(gen)}<br>"
    if n > max_rows:
        rows += f"<i>+{n - max_rows} more</i>"
    sep = "â”€" * 26
    return f"<b>{_esc(label) or 'Group'}</b> â€” {n} resp<br>{sep}<br>{rows}"


def _seg_matrices(matrices: list[dict], segment: str) -> list[dict]:
    return [m for m in matrices if str(_get(m, "respondent.segment") or "") == segment]


def _norm_cdf(x: float) -> float:
    return (1 + math.erf(x / math.sqrt(2))) / 2


def _prop_ztest(n1: int, p1: float, n2: int, p2: float) -> tuple[float, float]:
    """Two-proportion z-test. Returns (z_stat, p_value)."""
    if n1 < 2 or n2 < 2:
        return 0.0, 1.0
    p_pool = (n1 * p1 + n2 * p2) / (n1 + n2)
    se = math.sqrt(max(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2), 1e-12))
    z = (p1 - p2) / se
    p_val = 2 * (1 - _norm_cdf(abs(z)))
    return round(z, 3), round(max(min(p_val, 1.0), 0.0), 4)


def _seg_color(seg: str) -> str:
    sl = seg.lower()
    if "digital" in sl or "dg" in sl:
        return _C["seg_dg"]
    if "stock" in sl:
        return _C["seg_st"]
    if "fd" in sl or "mf" in sl:
        return _C["seg_fd"]
    palette = [_C["r1"], _C["r2"], _C["seg_dg"], _C["seg_st"], _C["seg_fd"]]
    return palette[hash(seg) % len(palette)]


def _fmt_filter_ctx(active_filters: dict, n: int) -> str:
    """Human-readable filter description for AI prompts."""
    if not active_filters:
        return f"all {n} respondents (no filter)"
    parts = []
    for path, val in active_filters.items():
        key = path.split(".")[-1].replace("_", " ")
        parts.append(f"{key}={val}")
    return f"{n} respondents ({', '.join(parts)})"


def _fmt_ctx_for_prompt(ctx: dict) -> str:
    """Format computed_ctx dict as readable lines for AI prompt."""
    lines = []
    for k, v in ctx.items():
        if v is not None and str(v) not in ("â€”", "", "None"):
            lines.append(f"  â€¢ {k}: {v}")
    return "\n".join(lines) if lines else "  (no computed data)"


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CHART BUILDERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _color_gradient(base_hex: str, n: int, min_opacity: float = 0.45) -> list[str]:
    """Rank-ordered opacity gradient from base_hex â€” highest rank darkest."""
    r = int(base_hex[1:3], 16)
    g = int(base_hex[3:5], 16)
    b = int(base_hex[5:7], 16)
    return [
        f"rgba({r},{g},{b},{min_opacity + (1 - min_opacity) * (1 - i / max(n - 1, 1)):.2f})"
        for i in range(n)
    ]


def _leader_gap_info(values: list) -> "tuple[int, float, float] | None":
    """Finds the standout category among 2+ values: (index, value, gap-to-runner-up). Returns
    None when there are fewer than 2 values or the top two are tied (no real "difference" to
    highlight â€” highlighting a coin-flip as a winner would be misleading, not informative)."""
    if len(values) < 2:
        return None
    nums = [float(v) for v in values]
    ranked = sorted(range(len(nums)), key=lambda i: -nums[i])
    top_i, second_i = ranked[0], ranked[1]
    gap = nums[top_i] - nums[second_i]
    if gap <= 0:
        return None
    return top_i, nums[top_i], gap


def _h_bar(labels: list, values: list, title: str = "", color: str = _C["accent"],
           h: int = 340, gradient: bool = True,
           resp_groups: "list[list[dict]] | None" = None) -> go.Figure:
    """Horizontal ranked bar â€” gradient opacity (darkest = highest rank). The standout bar (if
    any real gap exists over the runner-up) gets a bold outline and its gap called out inline â€”
    "which one matters" should be visible at a glance, not left for the viewer to eyeball bar
    lengths."""
    n = len(labels)
    colors = _color_gradient(color, n) if gradient and n > 1 else [color] * n
    _has_resp = resp_groups and len(resp_groups) == n
    _leader = _leader_gap_info(values)
    _line_w = [3 if _leader and i == _leader[0] else 0 for i in range(n)]
    _line_c = [_C["text"] if _leader and i == _leader[0] else "rgba(0,0,0,0)" for i in range(n)]
    _bar_text = [
        (f"{v}  â–² +{_leader[2]:.0f} vs next" if _leader and i == _leader[0] else str(v))
        for i, v in enumerate(values)
    ]
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker=dict(color=colors, line=dict(width=_line_w, color=_line_c)),
        text=_bar_text,
        textposition="outside",
        textfont=dict(size=10, color=_C["muted"]),
        customdata=[_resp_tooltip_html(resp_groups[i], str(labels[i])) for i in range(n)]
            if _has_resp else None,
        hovertemplate="%{customdata}<extra></extra>"
            if _has_resp else "%{y}: <b>%{x}</b><extra></extra>",
    ))
    fig.update_layout(
        **{**_CHART_BASE,
           "height": h,
           "title": dict(text=title, font=dict(size=12, color=_C["text"]), x=0, xanchor="left"),
           "margin": dict(l=4, r=60, t=44, b=8),
           "yaxis": dict(autorange="reversed", tickfont=dict(size=10), **_NO_GRID),
           "xaxis": dict(tickfont=dict(size=9), **_GRID_STYLE),
           },
    )
    # Make long labels fit by adjusting left margin dynamically
    if labels:
        max_len = max(len(str(lb)) for lb in labels)
        left_m = min(max(max_len * 6, 100), 240)
        fig.update_layout(margin=dict(l=left_m, r=60, t=44, b=8))
    return fig


def _v_bar(labels: list, values: list, title: str = "", color: str = _C["accent"],
           h: int = 320, colors: list | None = None,
           resp_groups: "list[list[dict]] | None" = None) -> go.Figure:
    """Vertical bar â€” optional per-bar color list. Standout bar (real gap over runner-up) gets
    a bold outline and its lead margin called out inline."""
    n = len(labels)
    bar_colors = colors if colors and len(colors) == n else [color] * n
    _leader = _leader_gap_info(values)
    _line_w = [3 if _leader and i == _leader[0] else 0 for i in range(n)]
    _line_c = [_C["text"] if _leader and i == _leader[0] else "rgba(0,0,0,0)" for i in range(n)]
    txt = []
    for i, v in enumerate(values):
        base = f"{v:.1f}" if isinstance(v, float) else str(v)
        if _leader and i == _leader[0]:
            base += f"  â–²+{_leader[2]:.0f}" if not isinstance(v, float) else f"  â–²+{_leader[2]:.1f}"
        txt.append(base)
    _has_resp = resp_groups and len(resp_groups) == n
    fig = go.Figure(go.Bar(
        x=labels, y=values,
        marker=dict(color=bar_colors, line=dict(width=_line_w, color=_line_c)),
        text=txt,
        textposition="outside",
        textfont=dict(size=10, color=_C["muted"]),
        customdata=[_resp_tooltip_html(resp_groups[i], str(labels[i])) for i in range(n)]
            if _has_resp else None,
        hovertemplate="%{customdata}<extra></extra>"
            if _has_resp else "%{x}: <b>%{y}</b><extra></extra>",
    ))
    fig.update_layout(
        **{**_CHART_BASE,
           "height": h,
           "title": dict(text=title, font=dict(size=12, color=_C["text"]), x=0, xanchor="left"),
           "margin": dict(l=40, r=20, t=44, b=60),
           "xaxis": dict(tickfont=dict(size=10), **_NO_GRID),
           "yaxis": dict(**_GRID_STYLE, tickfont=dict(size=9)),
           },
    )
    return fig


def _donut(labels: list, values: list, title: str = "", colors: list | None = None,
           h: int = 340,
           resp_groups: "list[list[dict]] | None" = None) -> go.Figure:
    """Donut with the standout slice (real gap over runner-up) pulled out further and bold-
    outlined, and the center label swapped from a plain total to the leader's margin â€” a viewer
    should see who's winning without reading the legend."""
    n = len(labels)
    palette = colors or [_C["r1"], _C["r2"], _C["seg_dg"], _C["seg_st"], _C["seg_fd"],
                         _C["pos"], _C["neg"], _C["neu"]]
    total = sum(values)
    _leader = _leader_gap_info(values)
    pull = [0.12 if _leader and i == _leader[0] else 0.02 for i in range(n)]
    line_w = [3 if _leader and i == _leader[0] else 2 for i in range(n)]
    _has_resp = resp_groups and len(resp_groups) == n
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.56,
        marker=dict(colors=palette[:n], line=dict(color="white", width=line_w)),
        pull=pull,
        textinfo="percent",
        textfont=dict(size=10),
        customdata=[_resp_tooltip_html(resp_groups[i], str(labels[i])) for i in range(n)]
            if _has_resp else None,
        hovertemplate="%{customdata}<extra></extra>"
            if _has_resp else "%{label}: <b>%{value}</b> (%{percent})<extra></extra>",
        sort=False,
    ))
    if _leader and total:
        _lead_pct = round(100 * _leader[1] / total)
        _gap_pct = round(100 * _leader[2] / total)
        _center_text = (
            f"<b>{_lead_pct}%</b><br><span style='font-size:9px'>leads +{_gap_pct}pt</span>"
        )
    else:
        _center_text = f"<b>{total}</b><br><span style='font-size:9px'>total</span>"
    fig.update_layout(
        **{**_CHART_BASE,
           "height": h,
           "showlegend": True,
           "title": dict(text=title, font=dict(size=12, color=_C["text"]), x=0, xanchor="left"),
           "margin": dict(l=10, r=10, t=44, b=10),
           "legend": dict(font=dict(size=9), orientation="v", x=1.02, y=0.5),
           "annotations": [dict(
               text=_center_text,
               x=0.5, y=0.5, showarrow=False,
               font=dict(size=13, color=_C["text"]),
           )],
           },
    )
    return fig


def _chart_click_filter(fig, key: str, lbls_raw: list, field: str, enabled: bool) -> None:
    """
    Render a plotly chart. When enabled, clicking a bar/slice sets a cross-filter on `field`
    in st.session_state['ct_chart_filters'] (merged into active_filters by _render_header) and
    reruns â€” every other chart on the page, driven off the same filtered matrices list, updates
    automatically. Clicking the same slice again clears just that filter.
    """
    if not enabled:
        st.plotly_chart(fig, use_container_width=True)
        return
    event = st.plotly_chart(fig, use_container_width=True, on_select="rerun",
                             selection_mode="points", key=key)
    pts = ((event or {}).get("selection") or {}).get("points") or []
    if pts:
        idx = pts[0].get("point_index")
        if idx is not None and 0 <= idx < len(lbls_raw):
            val = lbls_raw[idx]
            cf = st.session_state.setdefault("ct_chart_filters", {})
            if cf.get(field) == val:
                cf.pop(field, None)
            else:
                cf[field] = val
            st.rerun()


def _grouped_bar(groups: list, series: dict[str, list], title: str = "",
                 h: int = 360,
                 series_resp_groups: "dict[str, list[list[dict]]] | None" = None) -> go.Figure:
    palette = [_C["r1"], _C["r2"], _C["seg_dg"], _C["seg_st"], _C["seg_fd"]]
    fig = go.Figure()
    for i, (name, vals) in enumerate(series.items()):
        ng = len(groups)
        _srg = series_resp_groups.get(name) if series_resp_groups else None
        _has_resp = _srg and len(_srg) == ng
        fig.add_trace(go.Bar(
            name=name, x=groups, y=vals,
            marker=dict(color=palette[i % len(palette)], line=dict(width=0)),
            text=[f"{v:.1f}" if isinstance(v, float) else str(v) for v in vals],
            textposition="outside",
            textfont=dict(size=9, color=_C["muted"]),
            customdata=[_resp_tooltip_html(_srg[j], f"{name} Â· {groups[j]}") for j in range(ng)]
                if _has_resp else None,
            hovertemplate="%{customdata}<extra></extra>"
                if _has_resp else f"{name} â€” %{{x}}: <b>%{{y}}</b><extra></extra>",
        ))
    fig.update_layout(
        **{**_CHART_BASE,
           "height": h,
           "barmode": "group",
           "bargap": 0.22,
           "bargroupgap": 0.06,
           "title": dict(text=title, font=dict(size=12, color=_C["text"]), x=0, xanchor="left"),
           "showlegend": True,
           "legend": dict(orientation="h", y=-0.18, font=dict(size=9)),
           "margin": dict(l=40, r=20, t=44, b=70),
           "xaxis": dict(**_NO_GRID, tickfont=dict(size=10)),
           "yaxis": dict(**_GRID_STYLE, tickfont=dict(size=9)),
           },
    )
    return fig


def _stacked_100(categories: list, series: dict[str, list], title: str = "",
                 h: int = 260) -> go.Figure:
    palette = [_C["pos"], _C["amb"], _C["neg"], _C["neu"], _C["r1"], _C["r2"]]
    fig = go.Figure()
    for i, (name, vals) in enumerate(series.items()):
        fig.add_trace(go.Bar(
            name=name, x=categories, y=vals,
            marker=dict(color=palette[i % len(palette)], line=dict(width=0)),
            text=[f"{v:.0f}%" if v else "" for v in vals],
            textposition="inside",
            textfont=dict(size=9),
            hovertemplate=f"{name} â€” %{{x}}: <b>%{{y:.0f}}%</b><extra></extra>",
        ))
    fig.update_layout(
        **{**_CHART_BASE,
           "height": h,
           "barmode": "stack",
           "title": dict(text=title, font=dict(size=12, color=_C["text"]), x=0, xanchor="left"),
           "showlegend": True,
           "legend": dict(orientation="h", y=-0.18, font=dict(size=9)),
           "margin": dict(l=40, r=20, t=44, b=70),
           "xaxis": dict(**_NO_GRID, tickfont=dict(size=10)),
           "yaxis": dict(ticksuffix="%", range=[0, 100], **_GRID_STYLE, tickfont=dict(size=9)),
           },
    )
    return fig


def _heatmap(rows: list, cols: list, z: list[list], title: str = "", h: int = 300) -> go.Figure:
    """Heatmap with red-yellow-green colorscale and white cell borders."""
    fig = go.Figure(go.Heatmap(
        z=z, x=cols, y=rows,
        colorscale=[[0.0, "#fde8e8"], [0.35, "#fef3c7"], [0.65, "#d1fae5"], [1.0, "#059669"]],
        text=[[f"<b>{v}</b>" if v else "0" for v in row] for row in z],
        texttemplate="%{text}",
        textfont=dict(size=10),
        showscale=False,
        xgap=2, ygap=2,
        hovertemplate="%{y} Ã— %{x}: <b>%{z}</b><extra></extra>",
    ))
    fig.update_layout(
        **{**_CHART_BASE,
           "height": h,
           "title": dict(text=title, font=dict(size=12, color=_C["text"]), x=0, xanchor="left"),
           "margin": dict(l=160, r=20, t=44, b=90),
           "xaxis": dict(tickangle=-35, tickfont=dict(size=9), **_NO_GRID, side="bottom"),
           "yaxis": dict(autorange="reversed", tickfont=dict(size=9), **_NO_GRID),
           },
    )
    return fig


# â”€â”€ NEW CHART TYPES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _gauge(value: float, max_val: float, title: str,
           target: float | None = None, color: str = _C["accent"],
           h: int = 280) -> go.Figure:
    """Gauge chart for a single score metric with optional target threshold."""
    steps = [
        dict(range=[0, max_val * 0.5], color="#fee2e2"),
        dict(range=[max_val * 0.5, max_val * 0.75], color="#fef3c7"),
        dict(range=[max_val * 0.75, max_val], color="#d1fae5"),
    ]
    threshold_cfg = dict(
        line=dict(color=_C["pos"], width=3),
        thickness=0.8,
        value=target,
    ) if target is not None else None
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        number=dict(
            font=dict(size=28, color=_C["text"], family=_FONT),
            suffix=f" / {max_val:.0f}",
        ),
        title=dict(text=title, font=dict(size=11, color=_C["muted"], family=_FONT)),
        gauge=dict(
            axis=dict(range=[0, max_val], tickfont=dict(size=9), tickcolor=_C["muted"],
                      nticks=6),
            bar=dict(color=color, thickness=0.38),
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
            steps=steps,
            threshold=threshold_cfg,
        ),
    ))
    fig.update_layout(**{**_CHART_BASE, "height": h,
                         "margin": dict(l=30, r=30, t=30, b=10)})
    return fig


def _funnel(stages: list, values: list, title: str = "",
            colors: list | None = None, h: int = 220,
            resp_groups: "list[list[dict]] | None" = None) -> go.Figure:
    """Funnel chart for bucketed distributions (e.g. comprehension bands)."""
    ns = len(stages)
    palette = colors or [_C["pos"], _C["amb"], _C["neg"], _C["neu"]]
    total = sum(values) or 1
    pct_texts = [f"{v}  ({round(100*v/total)}%)" for v in values]
    _has_resp = resp_groups and len(resp_groups) == ns
    fig = go.Figure(go.Funnel(
        y=stages,
        x=values,
        text=pct_texts,
        textinfo="text",
        textfont=dict(size=10, color=_C["text"]),
        marker=dict(
            color=palette[:ns],
            line=dict(color=["white"] * ns, width=1),
        ),
        connector=dict(line=dict(color=_C["border"], width=1, dash="dot")),
        customdata=[_resp_tooltip_html(resp_groups[i], str(stages[i])) for i in range(ns)]
            if _has_resp else None,
        hovertemplate="%{customdata}<extra></extra>"
            if _has_resp else "%{y}: <b>%{x}</b> respondents<extra></extra>",
    ))
    fig.update_layout(
        **{**_CHART_BASE,
           "height": h,
           "title": dict(text=title, font=dict(size=12, color=_C["text"]), x=0, xanchor="left"),
           "margin": dict(l=20, r=20, t=44, b=10),
           "funnelmode": "stack",
           },
    )
    return fig


def _diverging_bar(labels: list, pos_vals: list, neg_vals: list,
                   pos_label: str = "Drivers", neg_label: str = "Barriers",
                   title: str = "", h: int = 320,
                   pos_resp_groups: "list[list[dict]] | None" = None,
                   neg_resp_groups: "list[list[dict]] | None" = None) -> go.Figure:
    """
    Diverging horizontal bar chart: positive side (drivers/trust) and
    negative side (barriers/friction) on opposite sides of zero axis.
    """
    nl = len(labels)
    neg_vals_plot = [-v for v in neg_vals]
    _has_pos = pos_resp_groups and len(pos_resp_groups) == nl
    _has_neg = neg_resp_groups and len(neg_resp_groups) == nl
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=labels,
        x=pos_vals,
        orientation="h",
        name=pos_label,
        marker=dict(color=_C["pos"], line=dict(width=0)),
        text=[str(v) if v else "" for v in pos_vals],
        textposition="outside",
        textfont=dict(size=9, color=_C["pos"]),
        customdata=[_resp_tooltip_html(pos_resp_groups[i], f"{pos_label} Â· {labels[i]}") for i in range(nl)]
            if _has_pos else None,
        hovertemplate="%{customdata}<extra></extra>"
            if _has_pos else f"{pos_label} â€” %{{y}}: <b>%{{x}}</b><extra></extra>",
    ))
    fig.add_trace(go.Bar(
        y=labels,
        x=neg_vals_plot,
        orientation="h",
        name=neg_label,
        marker=dict(color=_C["neg"], line=dict(width=0)),
        text=[f"-{v}" if v else "" for v in neg_vals],
        textposition="outside",
        textfont=dict(size=9, color=_C["neg"]),
        customdata=[_resp_tooltip_html(neg_resp_groups[i], f"{neg_label} Â· {labels[i]}") for i in range(nl)]
            if _has_neg else [str(v) for v in neg_vals],
        hovertemplate="%{customdata}<extra></extra>"
            if _has_neg else f"{neg_label} â€” %{{y}}: <b>%{{customdata}}</b><extra></extra>",
    ))
    fig.add_vline(x=0, line_width=1, line_color=_C["border"])
    max_x = max(max(pos_vals or [1]), max(neg_vals or [1]))
    fig.update_layout(
        **{**_CHART_BASE,
           "height": h,
           "barmode": "overlay",
           "title": dict(text=title, font=dict(size=12, color=_C["text"]), x=0, xanchor="left"),
           "showlegend": True,
           "legend": dict(orientation="h", y=-0.14, font=dict(size=9)),
           "margin": dict(l=4, r=60, t=44, b=60),
           "xaxis": dict(
               range=[-(max_x * 1.3), max_x * 1.3],
               tickvals=[],
               zeroline=False,
               showgrid=False,
           ),
           "yaxis": dict(autorange="reversed", tickfont=dict(size=10), **_NO_GRID),
           },
    )
    if labels:
        max_len = max(len(str(lb)) for lb in labels)
        fig.update_layout(margin=dict(l=min(max(max_len * 6, 100), 220), r=60, t=44, b=60))
    return fig


def _bubble_scatter(x_vals: list, y_vals: list, sizes: list, labels: list,
                    colors: list, x_label: str, y_label: str,
                    title: str = "", h: int = 320) -> go.Figure:
    """Bubble scatter for segment positioning (e.g. intent vs comprehension)."""
    norm_sizes = [max(s, 1) for s in sizes]
    max_s = max(norm_sizes)
    bubble_sizes = [8 + 32 * (s / max_s) for s in norm_sizes]
    fig = go.Figure()
    for i, (x, y, sz, lbl, col) in enumerate(
            zip(x_vals, y_vals, bubble_sizes, labels, colors)):
        fig.add_trace(go.Scatter(
            x=[x], y=[y],
            mode="markers+text",
            name=lbl,
            text=[lbl],
            textposition="top center",
            textfont=dict(size=9, color=_C["text"]),
            marker=dict(
                size=sz,
                color=col,
                opacity=0.82,
                line=dict(color="white", width=2),
            ),
            hovertemplate=(
                f"<b>{lbl}</b><br>{x_label}: %{{x:.1f}}<br>"
                f"{y_label}: %{{y:.1f}}<br>n: {sizes[i]}<extra></extra>"
            ),
        ))
    fig.update_layout(
        **{**_CHART_BASE,
           "height": h,
           "showlegend": False,
           "title": dict(text=title, font=dict(size=12, color=_C["text"]), x=0, xanchor="left"),
           "margin": dict(l=60, r=20, t=44, b=50),
           "xaxis": dict(title=dict(text=x_label, font=dict(size=10)), **_GRID_STYLE, tickfont=dict(size=9)),
           "yaxis": dict(title=dict(text=y_label, font=dict(size=10)), **_GRID_STYLE, tickfont=dict(size=9)),
           },
    )
    return fig


# â”€â”€ SCHEMA DETECTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _detect_fields(matrices: list[dict]) -> set[str]:
    """Return set of top-level + one-level-deep field paths that are populated."""
    fields: set[str] = set()
    for m in matrices:
        for k, v in m.items():
            if k.startswith("_"): continue
            if v:
                fields.add(k)
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        if vv: fields.add(f"{k}.{kk}")
    return fields


# â”€â”€ AUTO CHARTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Every schema field that isn't hand-wired into a bespoke chart anywhere in this file
# still has real per-respondent data sitting in the matrices â€” invisible in the dashboard
# unless someone manually adds a chart for it. This scans for any populated, categorical,
# not-already-charted field and renders it automatically, so a new schema field (e.g. from
# a Discovery re-run, or one of the "+ Add stub" fields in Extraction Studio) gets a chart
# on the next page load with zero code changes.
#
# Fields are partitioned across tabs by topic prefix, so each tab surfaces its own dynamic
# charts instead of one dumping ground â€” e.g. route1_evaluation.* surfaces on Route Comparison,
# not Respondent Profiles. Any field matching no tab's prefix list falls through to Respondent
# Profiles (the catch-all). A field is never shown twice: each prefixed tab call only sees its
# own prefixes, and the catch-all call explicitly excludes anything any prefix list claims.

CATEGORY_PREFIXES: dict[str, list[str]] = {
    "gold_category":    ["gold_behavior.", "portfolio_behavior.", "gold_awareness"],
    "concept_testing":  ["concept_understanding.", "tagline_reaction.", "message_comprehension",
                          "key_claim_reactions", "spontaneous_reaction", "yield_comprehension"],
    "route_comparison": ["route1_evaluation.", "route2_evaluation.", "route_evaluation."],
    "brand_trust":      ["coindcx_trust", "trust_builders", "crypto_association_effect",
                          "government_affiliation_preference", "platform_association",
                          "platforms_active", "platform_preference_reason"],
}


def _prefix_claimed(path: str) -> bool:
    """True if `path` falls under any tab's CATEGORY_PREFIXES â€” used to keep the catch-all
    (Respondent Profiles) from duplicating a field a topical tab already claims."""
    for prefixes in CATEGORY_PREFIXES.values():
        for p in prefixes:
            if path == p or path.startswith(p):
                return True
    return False


def _humanize_path(path: str) -> str:
    return " â€” ".join(p.replace("_", " ").title() for p in path.split("."))


def _auto_chart_candidates(
    matrices: list[dict], exclude: set[str], min_values: int = 2, max_values: int = 8,
    min_respondents: int = 3, include_prefixes: list[str] | None = None,
) -> list[tuple[str, dict[str, int]]]:
    """Scan populated fields not in `exclude` and return chartable (path, value_counts) pairs,
    sorted by respondent coverage descending. 'Chartable' = closed categorical: 2-8 distinct
    values, not free text, not a bare number (scores/percentages need histogram/gauge treatment,
    not a category bar â€” out of scope here). If `include_prefixes` given, only paths starting
    with (or exactly matching) one of them are considered â€” used to scope this tab's slice of
    the field space (see CATEGORY_PREFIXES)."""
    candidates: list[tuple[str, dict[str, int]]] = []
    for path in _detect_fields(matrices):
        if path in exclude or path.startswith("respondent."):
            continue
        if include_prefixes is not None and not any(
            path == p or path.startswith(p) for p in include_prefixes
        ):
            continue
        counts = _count_field(matrices, path)
        if not counts:
            continue
        n_resp = sum(counts.values())
        if n_resp < min_respondents or not (min_values <= len(counts) <= max_values):
            continue
        values = list(counts.keys())
        # Free-text / verbatim fields â€” same >6-word heuristic reconcile_project() uses
        # (project_extractor.py) to decide a field is prose, not a closed category.
        if any(len(v.split()) > 6 for v in values):
            continue
        # Stringified Python list/dict reprs (legacy type-coercion drift, e.g.
        # "['trust', 'comprehension']") â€” not real scalar categories, would render as garbage
        # chart labels.
        if any(any(ch in v for ch in "[]{}") for v in values):
            continue
        # Bare numeric fields (scores, percentages) are continuous, not categorical.
        def _is_num(v: str) -> bool:
            try:
                float(v)
                return True
            except ValueError:
                return False
        if all(_is_num(v) for v in values):
            continue
        candidates.append((path, counts))
    candidates.sort(key=lambda c: sum(c[1].values()), reverse=True)
    return candidates


def _render_auto_charts(
    matrices: list[dict], exclude: set[str], max_charts: int = 6,
    include_prefixes: list[str] | None = None, key_prefix: str = "auto",
) -> None:
    """Render up to `max_charts` auto-detected charts for fields not already covered elsewhere
    on the page. Uses the same _chart_click_filter cross-filter mechanism as every other chart
    on this tab, so clicking a bar here filters the whole page identically."""
    candidates = _auto_chart_candidates(matrices, exclude, include_prefixes=include_prefixes)[:max_charts]
    if not candidates:
        return
    st.markdown("#### Additional Signals (auto-detected)")
    st.caption(
        "Every populated field not already charted above, surfaced automatically â€” no manual "
        "setup. New schema fields appear here on the next run with no code change."
    )
    for row_start in range(0, len(candidates), 3):
        row = candidates[row_start:row_start + 3]
        cols = st.columns(len(row))
        for col, (path, counts) in zip(cols, row):
            with col:
                raw_labels = list(counts.keys())
                fmt_labels = [_fmt_val(v) for v in raw_labels]
                values = list(counts.values())
                title = _humanize_path(path)
                fig = (_donut(fmt_labels, values, title)
                       if len(raw_labels) <= 3 else
                       _h_bar(fmt_labels, values, title, _C["accent"]))
                _chart_click_filter(
                    fig, key=f"ctcf_{key_prefix}_{path.replace('.', '_')}", lbls_raw=raw_labels,
                    field=path, enabled=True,
                )
                _chart_caption(f"{sum(values)} respondent(s). Click to filter every chart on this page.")


# â”€â”€ QUALITATIVE OLS REGRESSION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _ols_regression(
    matrices: list[dict],
    dep_path: str = "adoption.intent_score",
) -> dict:
    """
    Pure-Python OLS regression of adoption intent on available numeric predictors.
    Categorical fields (trust_gap, sgb_awareness, financial_anxiety_level) are
    ordinal-encoded. Returns results dict: coefficients, R2, adj-R2, importance.
    """
    # "Trust Gap" predictor uses _trust_gap_value(m), not a plain dot-path â€” this project's
    # matrices span two schema generations (see the normalizer functions defined near
    # _group_list_field) and the real trust-gap signal only resolves correctly when both
    # shapes are merged. Encoding stays gap-semantic: low gap (good) = 3, high gap (bad) = 1.
    _TRUST_ENC = {"low": 3, "medium": 2, "high": 1}
    _SGB_ENC   = {"none": 0, "low": 1, "medium": 2, "high": 3}
    _ANX_ENC   = {"low": 3, "medium": 2, "high": 1}

    predictors = [
        ("Comprehension", "concept_understanding.comprehension_score", None),
        ("R1 Appeal",     "route1_evaluation.overall_appeal_score",    None),
        ("R2 Appeal",     "route2_evaluation.overall_appeal_score",    None),
        ("Trust Gap",     _trust_gap_value,                             _TRUST_ENC),
        ("SGB Awareness", "gold_behavior.sgb_awareness",               _SGB_ENC),
        ("Fin. Anxiety",  "financial_anxiety_level",                   _ANX_ENC),
    ]

    rows: list[dict] = []
    for m in matrices:
        y_raw = _get(m, dep_path)
        if y_raw is None or not isinstance(y_raw, (int, float)):
            continue
        row: dict = {"__y": float(y_raw)}
        for label, path, enc in predictors:
            raw = path(m) if callable(path) else _get(m, path)
            if enc is not None:
                val = enc.get(str(raw).lower()) if raw is not None else None
            else:
                val = float(raw) if isinstance(raw, (int, float)) else None
            row[label] = val
        rows.append(row)

    if len(rows) < 6:
        return {"error": f"Insufficient data for regression (n={len(rows)}, need >=6)"}

    active_preds = [label for label, _, __ in predictors
                    if sum(1 for r in rows if r.get(label) is not None) >= 5]
    if not active_preds:
        return {"error": "No predictors with >=5 observations."}

    clean_rows = [r for r in rows if all(r.get(p) is not None for p in active_preds)]
    n = len(clean_rows)
    if n < len(active_preds) + 2:
        return {"error": f"n={n} too small for {len(active_preds)} predictors."}

    import statistics
    y = [r["__y"] for r in clean_rows]
    X = [[1.0] + [float(r[p]) for p in active_preds] for r in clean_rows]
    k = len(X[0])

    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]

    def _inv_mat(M):
        sz = len(M)
        aug = [[M[r][c] if c < sz else (1.0 if c - sz == r else 0.0)
                for c in range(2 * sz)] for r in range(sz)]
        for col in range(sz):
            pivot = max(range(col, sz), key=lambda r: abs(aug[r][col]))
            aug[col], aug[pivot] = aug[pivot], aug[col]
            if abs(aug[col][col]) < 1e-14:
                return None
            scale = aug[col][col]
            aug[col] = [v / scale for v in aug[col]]
            for row in range(sz):
                if row != col:
                    factor = aug[row][col]
                    aug[row] = [aug[row][c] - factor * aug[col][c] for c in range(2 * sz)]
        return [[aug[r][sz + c] for c in range(sz)] for r in range(sz)]

    inv = _inv_mat(XtX)
    if inv is None:
        return {"error": "Singular matrix â€” perfect multicollinearity detected."}

    beta = [sum(inv[i][j] * Xty[j] for j in range(k)) for i in range(k)]
    y_pred = [sum(beta[j] * X[i][j] for j in range(k)) for i in range(n)]
    y_mean = sum(y) / n
    ss_tot = sum((yi - y_mean) ** 2 for yi in y) or 1e-12
    ss_res = sum((yi - yp) ** 2 for yi, yp in zip(y, y_pred))
    r2 = max(0.0, 1 - ss_res / ss_tot)
    adj_r2 = 1 - (1 - r2) * (n - 1) / max(n - k, 1)

    sd_y = statistics.pstdev(y) or 1.0
    results = []
    for i, pred in enumerate(active_preds, start=1):
        b = beta[i]
        x_vals = [r[pred] for r in clean_rows]
        sd_x = statistics.pstdev(x_vals) or 1.0
        beta_std = b * sd_x / sd_y
        results.append({"Predictor": pred, "b": b, "beta_std": beta_std})

    total_abs = sum(abs(r["beta_std"]) for r in results) or 1.0
    for r in results:
        r["importance_pct"] = round(100 * abs(r["beta_std"]) / total_abs, 1)
    results.sort(key=lambda r: -r["importance_pct"])

    return {
        "n": n, "k": len(active_preds),
        "r2": round(r2, 3), "adj_r2": round(adj_r2, 3),
        "results": results,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# UI COMPONENTS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _section_header(title: str, description: str = "", n: int = 0,
                    color: str = _C["accent"]) -> None:
    """Consistent section title with description and respondent count badge."""
    n_badge = (
        f'<span style="background:{color}18;color:{color};font-size:0.62rem;'
        f'font-weight:800;padding:2px 9px;border-radius:20px;'
        f'margin-left:10px;vertical-align:middle;">n = {n}</span>'
    ) if n else ""
    desc_html = (
        f'<div style="font-size:0.78rem;color:{_C["muted"]};margin-top:4px;'
        f'margin-bottom:2px;line-height:1.5;">{_esc(description)}</div>'
    ) if description else ""
    st.markdown(
        f'<div style="margin-bottom:6px;">'
        f'<span style="font-size:1.08rem;font-weight:800;color:{_C["text"]};">'
        f'{_esc(title)}</span>{n_badge}</div>{desc_html}'
        f'<hr style="margin:8px 0 16px 0;border:none;border-top:2px solid {color}30;">',
        unsafe_allow_html=True,
    )


def _insight_banner(headline: str, subtext: str = "", color: str = _C["accent"]) -> None:
    sub = (
        f'<div style="font-size:0.82rem;color:{_C["muted"]};margin-top:5px;">'
        f'{_esc(subtext)}</div>'
    ) if subtext else ""
    st.markdown(
        f'<div style="border-left:4px solid {color};padding:14px 20px;margin:0 0 16px 0;'
        f'background:{color}0d;border-radius:0 10px 10px 0;">'
        f'<div style="font-size:0.6rem;font-weight:900;color:{color};'
        f'text-transform:uppercase;letter-spacing:0.12em;margin-bottom:5px;">Key Finding</div>'
        f'<div style="font-size:1.02rem;font-weight:800;color:{_C["text"]};line-height:1.4;">'
        f'{_esc(headline)}</div>{sub}</div>',
        unsafe_allow_html=True,
    )


def _chart_caption(text: str) -> None:
    """Styled caption below chart â€” interpretation note + data note."""
    st.markdown(
        f'<div style="font-size:0.78rem;color:#4b5563;padding:6px 10px 12px 10px;'
        f'border-top:2px solid #f1f5f9;margin-top:4px;line-height:1.5;">'
        f'&#128161; {_esc(text)}</div>',
        unsafe_allow_html=True,
    )


def _chart_header(title: str, subtitle: str = "", how_to_read: str = "",
                  calc_note: str = "") -> None:
    """Styled block above chart. title + subtitle + reading guide + optional calc methodology."""
    body = (
        f'<div style="font-size:1.0rem;font-weight:800;color:#111827;'
        f'letter-spacing:-0.01em;margin-bottom:4px;">{_esc(title)}</div>'
    )
    if subtitle:
        body += (
            f'<div style="font-size:0.82rem;color:#374151;margin-bottom:5px;'
            f'line-height:1.45;">{_esc(subtitle)}</div>'
        )
    if how_to_read:
        body += (
            f'<div style="font-size:0.78rem;color:#6b7280;font-style:italic;'
            f'margin-bottom:3px;">&#128270; {_esc(how_to_read)}</div>'
        )
    if calc_note:
        body += (
            f'<div style="font-size:0.72rem;color:#9ca3af;background:#f8fafc;'
            f'border-left:3px solid #e5e7eb;padding:4px 8px;margin-top:4px;border-radius:0 4px 4px 0;">'
            f'&#9883; {_esc(calc_note)}</div>'
        )
    st.markdown(f'<div style="margin:22px 0 6px 0;">{body}</div>', unsafe_allow_html=True)


def _legend_row(items: list, compact: bool = False) -> None:
    """Coloured pill legend. items = [(label, hex_color, description)].
    compact=True = no background box, smaller pills â€” for use inside columns."""
    def _legend_desc_html(desc):
        return f'<span style="color:#6b7280;"> â€” {_esc(desc)}</span>' if desc and not compact else ""

    pills = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:4px;margin:0 10px 5px 0;">'
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;'
        f'background:{col};flex-shrink:0;"></span>'
        f'<span style="font-size:0.73rem;color:#374151;"><b>{_esc(lbl)}</b>'
        f'{_legend_desc_html(desc)}'
        f'</span></span>'
        for lbl, col, desc in items
    )
    if compact:
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;margin:4px 0 8px 0;">{pills}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;margin:5px 0 12px 0;'
            f'padding:8px 12px;background:#f8fafc;border-radius:6px;'
            f'border:1px solid #e5e7eb;">{pills}</div>',
            unsafe_allow_html=True,
        )


def _kpi(col, label: str, value: str, color: str = _C["accent"], sub: str = "") -> None:
    with col:
        sub_html = (
            f'<div style="font-size:0.68rem;color:{_C["muted"]};margin-top:3px;">'
            f'{_esc(sub)}</div>'
        ) if sub else ""
        st.markdown(
            f'<div style="background:{_C["surface"]};border:1px solid {_C["border"]};'
            f'border-top:3px solid {color};border-radius:8px;padding:12px 14px;margin-bottom:6px;">'
            f'<div style="font-size:0.63rem;font-weight:700;color:{_C["muted"]};'
            f'text-transform:uppercase;letter-spacing:0.08em;">{_esc(label)}</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:{_C["text"]};margin-top:4px;">'
            f'{_esc(value)}</div>{sub_html}</div>',
            unsafe_allow_html=True,
        )


def _seg_card(seg: str, color: str, n: int, stats: dict) -> None:
    rows = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
        f'border-bottom:1px solid {_C["border"]};">'
        f'<span style="font-size:0.72rem;color:{_C["muted"]};">{_esc(k)}</span>'
        f'<span style="font-size:0.72rem;font-weight:700;color:{_C["text"]};">{_esc(str(v))}</span>'
        f'</div>'
        for k, v in stats.items() if v and str(v) not in ("None", "â€”", "")
    )
    st.markdown(
        f'<div style="border:1px solid {_C["border"]};border-top:4px solid {color};'
        f'border-radius:0 0 8px 8px;padding:14px;height:100%;">'
        f'<div style="font-size:0.6rem;font-weight:900;color:{color};'
        f'text-transform:uppercase;letter-spacing:0.1em;">Segment</div>'
        f'<div style="font-size:1rem;font-weight:800;margin:4px 0 2px;">{_esc(seg)}</div>'
        f'<div style="font-size:0.72rem;color:{_C["muted"]};margin-bottom:10px;">n = {n}</div>'
        f'{rows}</div>',
        unsafe_allow_html=True,
    )


def _attr_row(label: str, counts: dict[str, int], total: int, r1_color: str = _C["r1"]) -> None:
    order = ["strong", "yes", "full", "yes_concerned", "conditional", "partial", "no", "confused", "unclear"]
    val_colors = {
        "strong": _C["pos"], "yes": _C["pos"], "full": _C["pos"],
        "yes_concerned": _C["amb"],
        "conditional": _C["amb"], "partial": _C["amb"],
        "no": _C["neg"], "confused": _C["neg"], "unclear": _C["neu"],
    }
    sorted_keys = sorted(counts.keys(), key=lambda k: order.index(k) if k in order else 99)
    segs_html = ""
    for k in sorted_keys:
        v = counts[k]
        pct = round(100 * v / total) if total else 0
        col = val_colors.get(k, _C["neu"])
        segs_html += (
            f'<div title="{_esc(k)}: {v}" style="width:{pct}%;min-width:4px;background:{col};'
            f'height:18px;border-radius:2px;margin-right:1px;display:inline-block;"></div>'
        )
    top = sorted_keys[0] if sorted_keys else "â€”"
    top_n = counts.get(top, 0)
    top_pct = _pct_str(top_n, total)
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;padding:6px 0;'
        f'border-bottom:1px solid {_C["border"]};">'
        f'<div style="width:220px;font-size:0.73rem;color:{_C["text"]};font-weight:500;">'
        f'{_esc(label)}</div>'
        f'<div style="flex:1;display:flex;align-items:center;">{segs_html}</div>'
        f'<div style="width:90px;font-size:0.7rem;color:{_C["muted"]};text-align:right;">'
        f'{_esc(top)}: {top_pct}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _sentiment_badge(sentiment: str) -> str:
    c = _SENT_C.get((sentiment or "").lower(), _C["neu"])
    lbl = (sentiment or "neutral").title()
    return (
        f'<span style="background:{c}22;color:{c};font-size:0.58rem;font-weight:800;'
        f'padding:2px 7px;border-radius:20px;text-transform:uppercase;letter-spacing:0.06em;">'
        f'{_esc(lbl)}</span>'
    )


def _verbatim_wall(passages: list[dict], key_prefix: str, title: str = "Verbatims",
                   extra_filter_fields: list[tuple[str, str]] | None = None) -> None:
    """
    Verbatim wall with sentiment/segment/type/search filters.
    extra_filter_fields: [(label, field_name)] for section-specific filters.
    Includes CSV download of filtered passages.
    """
    if not passages:
        st.markdown(
            f'<div style="padding:20px;background:{_C["bg_deep"]};border-radius:8px;'
            f'text-align:center;color:{_C["muted"]};font-size:0.82rem;">'
            f'No passages found for the active topic filter. '
            f'This section may not have verbatim data in the current study.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(f"**{title}**")

    n_extra = len(extra_filter_fields or [])
    n_cols  = 4 + n_extra
    cols    = st.columns(n_cols)

    sents = sorted({(p.get("sentiment") or "neutral").lower() for p in passages})
    segs  = sorted({str(p.get("segment", "")) for p in passages if p.get("segment")})

    sent_f = cols[0].selectbox("Sentiment", ["All"] + sents, key=f"{key_prefix}_sf")
    seg_f  = cols[1].selectbox("Segment",   ["All"] + segs,  key=f"{key_prefix}_sgf")
    pain_f = cols[2].selectbox("Type", ["All", "Pain Points", "Decision Signals"], key=f"{key_prefix}_tf")
    search = cols[3].text_input("Search", key=f"{key_prefix}_srch", placeholder="keywordâ€¦")

    extra_selections: dict[str, str] = {}
    for i, (lbl, field) in enumerate(extra_filter_fields or []):
        vals = sorted({str(p.get(field, "") or "") for p in passages if p.get(field)})
        extra_selections[field] = cols[4 + i].selectbox(lbl, ["All"] + vals,
                                                         key=f"{key_prefix}_ex{i}")

    shown = passages
    if sent_f != "All":
        shown = [p for p in shown if (p.get("sentiment") or "").lower() == sent_f]
    if seg_f != "All":
        shown = [p for p in shown if str(p.get("segment", "")) == seg_f]
    if pain_f == "Pain Points":
        shown = [p for p in shown if p.get("pain_point")]
    elif pain_f == "Decision Signals":
        shown = [p for p in shown if p.get("decision_signal")]
    if search:
        shown = [p for p in shown if search.lower() in (p.get("content") or "").lower()]
    for field, sel in extra_selections.items():
        if sel != "All":
            shown = [p for p in shown if str(p.get(field, "") or "") == sel]

    pos = sum(1 for p in shown if (p.get("sentiment") or "").lower() == "positive")
    neg = sum(1 for p in shown if (p.get("sentiment") or "").lower() == "negative")
    neu = len(shown) - pos - neg

    stat_col, dl_col = st.columns([8, 2])
    with stat_col:
        st.markdown(
            f'<span style="font-size:0.78rem;color:{_C["muted"]};">'
            f'<b>{len(shown)}</b> passages â€” '
            f'<span style="color:{_C["pos"]}">â–² {pos} positive</span> Â· '
            f'<span style="color:{_C["neg"]}">â–¼ {neg} negative</span> Â· '
            f'<span style="color:{_C["neu"]}">â— {neu} neutral</span></span>',
            unsafe_allow_html=True,
        )
    with dl_col:
        if shown:
            try:
                import pandas as pd
                csv_data = pd.DataFrame([
                    {
                        "content":  p.get("content", ""),
                        "sentiment": p.get("sentiment", ""),
                        "segment":  p.get("segment", ""),
                        "city":     p.get("city", ""),
                        "topic":    p.get("topic", ""),
                        "pain_point": p.get("pain_point", False),
                        "decision_signal": p.get("decision_signal", False),
                        **{f: p.get(f, "") for _, f in (extra_filter_fields or [])},
                    }
                    for p in shown
                ]).to_csv(index=False).encode("utf-8")
                st.download_button(
                    "â¬‡ Export CSV",
                    data=csv_data,
                    file_name=f"{key_prefix}_verbatims.csv",
                    mime="text/csv",
                    key=f"{key_prefix}_dl",
                )
            except Exception:
                pass

    page_size = 10
    pg_key = f"{key_prefix}_pg"
    if pg_key not in st.session_state:
        st.session_state[pg_key] = 0
    total_pages = max(1, (len(shown) + page_size - 1) // page_size)
    page = min(st.session_state[pg_key], total_pages - 1)
    chunk = shown[page * page_size:(page + 1) * page_size]

    for p in chunk:
        sent  = (p.get("sentiment") or "neutral").lower()
        color = _SENT_C.get(sent, _C["neu"])
        txt   = (p.get("content") or "").strip()
        meta  = " Â· ".join(x for x in [
            str(p.get("segment", "")), str(p.get("city", "")), str(p.get("doc_id", ""))
        ] if x)
        flags = []
        if p.get("pain_point"):      flags.append("âš  pain")
        if p.get("decision_signal"): flags.append("â†’ decision")
        extra_meta = " Â· ".join(
            f"{lbl}={p.get(field, '')}"
            for lbl, field in (extra_filter_fields or [])
            if p.get(field)
        )
        badge = _sentiment_badge(sent)
        tag_chips = "".join(
            f'<span style="background:{_C["accent"]}15;color:{_C["accent"]};padding:1px 6px;'
            f'border-radius:8px;font-size:0.6rem;font-weight:700;margin-right:3px;">'
            f'{_esc(_fmt_val(t))}</span>'
            for t in (p.get("narrative_tags") or [])[:6]
        )
        tag_chips_block = f'<div style="margin-top:4px;">{tag_chips}</div>' if tag_chips else ""
        st.markdown(
            f'<div style="border-left:3px solid {color};padding:8px 14px;margin:6px 0;'
            f'border-radius:0 6px 6px 0;background:{color}06;">'
            f'{badge} <span style="font-size:0.63rem;color:{_C["muted"]};">'
            f'{_esc(meta)}'
            f'{"  Â· " + extra_meta if extra_meta else ""}'
            f'{"  Â· " + "  Â· ".join(flags) if flags else ""}'
            f'</span>'
            f'{tag_chips_block}'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown(txt)

    c1, c2, c3 = st.columns([1, 4, 1])
    if c1.button("â† Prev", key=f"{key_prefix}_prev", disabled=page == 0):
        st.session_state[pg_key] = max(0, page - 1); st.rerun()
    c2.caption(f"Page {page + 1} / {total_pages} Â· {len(shown)} passages")
    if c3.button("Next â†’", key=f"{key_prefix}_next", disabled=page >= total_pages - 1):
        st.session_state[pg_key] = page + 1; st.rerun()


def _ai_finding_robust(
    section_id: str,
    findings_dir: str,
    call_or: Callable,
    computed_ctx: dict,
    active_filters: dict,
    base_prompt: str,
    regen_key: str,
    proj_name: str = "this concept test study",
) -> None:
    """
    AI research finding block â€” data-grounded, filter-aware.

    computed_ctx: dict of computed stats from section (passed in by section renderer).
    active_filters: global filter dict {path: value}.
    base_prompt: section-specific instruction string.
    Shows pre-generated finding + structured regen with actual data baked into prompt.
    """
    finding = _load_finding(findings_dir, section_id)
    filter_ctx = _fmt_filter_ctx(active_filters, computed_ctx.get("n", 0))
    data_block  = _fmt_ctx_for_prompt(computed_ctx)

    # Display pre-generated finding if present
    text = finding.get("finding_text", "")
    if text:
        import re
        clean = re.sub(r"^([A-Z][A-Z ]{3,}:)", r"**\1**", text, flags=re.MULTILINE)
        st.markdown(clean)
        # Staleness note if filters are active (pre-generated may not match)
        if active_filters:
            st.caption(
                f"âš  Pre-generated finding may not reflect active filter: {filter_ctx}. "
                f"Use Regenerate to get a filter-aware finding."
            )
    else:
        st.markdown(
            f'<div style="padding:14px;background:{_C["bg_deep"]};border-radius:6px;'
            f'color:{_C["muted"]};font-size:0.83rem;">'
            f'No pre-generated finding available. Use Regenerate to create one '
            f'from the current data ({filter_ctx}).</div>',
            unsafe_allow_html=True,
        )

    st.markdown("")

    with st.expander("â†º Regenerate AI Finding", expanded=not bool(text)):
        st.caption(f"Will generate for: **{filter_ctx}**")
        custom_focus = st.text_area(
            "Optional: focus question or angle for the AI",
            key=f"{regen_key}_focus",
            placeholder="e.g. 'Focus on why Route 2 fails with FDMF segment' â€¦",
            height=68,
        )

        # Show the data that will be included
        with st.expander("Data context that will be sent to AI", expanded=False):
            st.code(data_block, language=None)

        if st.button("Generate Finding", key=regen_key, type="primary"):
            focus_line = f"\n\nAdditional focus: {custom_focus.strip()}" if custom_focus.strip() else ""
            prompt = f"""You are a senior market research analyst. The client can already see every number
in this data on the dashboard â€” your job is NOT to describe the data back to them, it's to tell them
something the raw numbers don't say on their own. A finding that just restates "N of M respondents did X"
is worthless; they already have that tile in front of them. Write something that would make them stop
scrolling.

STUDY: {proj_name}
SECTION: {section_id.replace("_", " ").title()}
FILTER / SCOPE: {filter_ctx}

COMPUTED DATA FROM THIS SECTION:
{data_block}

INSTRUCTIONS:
{base_prompt}

Before writing, reason through these silently (do not print your reasoning, only the final output):
- Cross-reference dimensions ONLY where the data above gives you an actual joint count (a line that
  already shows the intersection, e.g. "high anxiety (6 total) â†’ safety_seeker: 4/6"). Two separate
  marginal totals (e.g. "6 respondents are high-anxiety" and "10 are safety_seeker") do NOT tell you
  how many are both â€” do not compute, estimate, or imply an intersection number that isn't already
  given to you as a joint count. If no real cross-tab is provided for the dimensions you want to
  connect, reason about them qualitatively instead of inventing a fraction.
- Look specifically for a tension: something counter-intuitive, a segment that breaks the overall
  pattern, or a number that contradicts what you'd expect given another number in the same data. If
  the data is genuinely uniform with no tension, say that explicitly instead of manufacturing one.
- Ask "so what would I actually tell the product/marketing team to DO differently" â€” a real
  implication names a specific segment, message, proof-point, or decision, not a generic direction
  like "build trust" or "emphasize safety."

Write a structured finding with EXACTLY this format:

HEADLINE: [The one sentence a busy exec needs â€” states the tension or the non-obvious pattern, not a summary statistic]

KEY FINDINGS:
â€¢ [The cross-referenced pattern, with the specific numbers that support it]
â€¢ [The counter-intuitive or segment-breaking detail, with numbers]
â€¢ [A third finding only if it adds something the first two didn't already cover]

IMPLICATION: [A specific, actionable recommendation â€” name the segment, the message, or the decision. Not "improve trust messaging" but e.g. "lead with FIU registration specifically for FDMF investors in Tier-2 cities, where trust gap is highest and route preference is most split"]
{focus_line}

Use only the data provided. Do not invent numbers. If the data doesn't support a strong finding,
say so plainly rather than padding â€” a correctly-flagged "nothing notable here" is more useful than
a manufactured insight."""

            with st.spinner("Generating finding from live dataâ€¦"):
                r = call_or(prompt)
                if r:
                    st.success("Generated:")
                    import re
                    formatted = re.sub(r"^([A-Z][A-Z ]{3,}:)", r"**\1**", r, flags=re.MULTILINE)
                    st.markdown(formatted)
                    # Offer to save
                    if st.button("Save as pre-generated finding", key=f"{regen_key}_save"):
                        try:
                            p = Path(findings_dir) / f"{section_id}.json"
                            p.parent.mkdir(parents=True, exist_ok=True)
                            p.write_text(json.dumps({"finding_text": r}, ensure_ascii=False),
                                         encoding="utf-8")
                            st.success("Saved.")
                        except Exception as e:
                            st.error(f"Save failed: {e}")
                else:
                    st.warning("No response from OpenRouter.")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# DATA LOADERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@st.cache_data(ttl=900, show_spinner=False)
def _load_matrices(matrices_dir: str) -> list[dict]:
    d = Path(matrices_dir)
    if not d.exists():
        return []
    out = []
    for fp in sorted(d.glob("*_matrix.json")):
        try:
            m = json.loads(fp.read_text(encoding="utf-8"))
            m["_source_file"] = fp.name
            m["doc_id"] = m.get("doc_id") or fp.stem.replace("_matrix", "")
            out.append(m)
        except Exception:
            pass
    return out


@st.cache_data(ttl=900, show_spinner=False)
def _load_finding(findings_dir: str, section_id: str) -> dict:
    p = Path(findings_dir) / f"{section_id}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_passages(matrices: list[dict], topics: list[str] | None = None,
                  pain_only: bool = False, decision_only: bool = False) -> list[dict]:
    topic_set = {t.lower() for t in (topics or [])}
    out: list[dict] = []
    for m in matrices:
        resp = m.get("respondent") or {}
        pref_route  = str(_get(m, "preferred_route") or "").strip()
        route_shown = str(_get(m, "concept_understanding.route_shown") or "").strip()
        meta = {
            "segment":      resp.get("segment", ""),
            "city":         resp.get("city", ""),
            "doc_id":       m.get("doc_id", ""),
            "pref_route":   pref_route,
            "route_shown":  route_shown,
            # This respondent's declared+emergent themes (narrative_tags) â€” shown as chips per
            # quote so a reader sees which themes a passage's speaker was tagged with, including
            # any theme the model surfaced that wasn't in the study's predeclared vocabulary.
            "narrative_tags": [str(t).strip() for t in (m.get("narrative_tags") or []) if str(t).strip()],
        }
        for p in (m.get("all_passages") or []):
            if not isinstance(p, dict): continue
            if pain_only and not p.get("pain_point"): continue
            if decision_only and not p.get("decision_signal"): continue
            topic = (p.get("topic") or "").lower()
            if topic_set and topic not in topic_set: continue
            content = (p.get("content") or "").strip()
            if len(content) < 20: continue
            out.append({**p, **meta})
    return out


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SECTION 1 â€” INVESTOR PROFILES
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# Field paths already hand-built into a bespoke chart somewhere in this file (grep-verified
# against every _count_field/_count_list_field call site) â€” kept out of the auto-chart pass so
# a field never renders twice. Best-effort, not derived automatically: a stale entry here just
# means a field gets charted twice, not dropped, so it's safe to under-maintain.
EXCLUDE_AUTO_CHART: set[str] = {
    "financial_anxiety_level", "life_stage", "investor_archetype", "preferred_route",
    "gold_behavior.gold_role", "gold_behavior.sgb_awareness", "gold_behavior.purchase_trigger",
    "gold_behavior.formats_owned", "gold_behavior.digital_gold_view",
    "tagline_reaction.preferred_tagline", "portfolio_behavior.platforms_used",
    "portfolio_behavior.info_sources", "adoption.barriers", "adoption.drivers",
    "coindcx_trust", "concept_understanding.route_shown", "nps_signal", "emotional_resolution",
}


def _render_profiles(matrices: list[dict], findings_dir: str, call_or: Callable,
                     active_filters: dict, proj_name: str) -> None:
    n = len(matrices)
    segs = sorted({str(_get(m, "respondent.segment") or "") for m in matrices
                   if _get(m, "respondent.segment")})

    high_anx = sum(1 for m in matrices if (_get(m, "financial_anxiety_level") or "").lower() == "high")
    promo     = sum(1 for m in matrices if (m.get("nps_signal") or "").lower() == "promoter")
    pos_res   = sum(1 for m in matrices if (m.get("emotional_resolution") or "").lower() == "positive")
    avg_int   = _avg([_get(m, "adoption.intent_score") for m in matrices])
    top_arch  = _count_field(matrices, "investor_archetype")
    top_stage = _count_field(matrices, "life_stage")
    top_anx_d = _count_field(matrices, "financial_anxiety_level")
    top_arch_str = max(top_arch, key=top_arch.get) if top_arch else "â€”"

    # Real joint cross-tab, not just marginal totals â€” without this, a prompt that asks the AI to
    # "cross-reference dimensions" has no actual intersection data to work from, and a model asked
    # to produce a specific cross-tab number it wasn't given will fabricate a plausible-sounding one
    # instead of refusing. Found live: the AI Finding prompt asked for exactly this and the model
    # invented "high-anxiety respondents skew toward safety_seeker (4/6)" â€” a real, checkable number
    # that was never in the marginal-only data it had. Giving it the true joint counts here removes
    # the need to guess.
    from collections import Counter
    anx_arch_crosstab: dict[str, Counter] = {}
    for m in matrices:
        a = str(_get(m, "financial_anxiety_level") or "").strip().lower()
        arch = str(_get(m, "investor_archetype") or "").strip()
        if a and arch:
            anx_arch_crosstab.setdefault(a, Counter())[arch] += 1
    crosstab_lines = []
    for a, counts in sorted(anx_arch_crosstab.items()):
        total_a = sum(counts.values())
        parts = ", ".join(f"{arch}: {c}/{total_a}" for arch, c in counts.most_common())
        crosstab_lines.append(f"{a} anxiety ({total_a} total) â†’ {parts}")

    ctx = {
        "n": n,
        "segments": ", ".join(segs) if segs else "â€”",
        "high_anxiety": f"{high_anx}/{n} ({_pct_str(high_anx, n)})",
        "positive_resolution": f"{pos_res}/{n} ({_pct_str(pos_res, n)})",
        "nps_promoters": f"{promo}/{n} ({_pct_str(promo, n)})",
        "avg_adoption_intent": f"{avg_int:.1f}/10" if avg_int else "â€”",
        "dominant_archetype": _fmt_val(top_arch_str),
        "top_life_stage": _fmt_val(max(top_stage, key=top_stage.get)) if top_stage else "â€”",
        "anxiety_distribution": str(dict(list(top_anx_d.items())[:3])) if top_anx_d else "â€”",
        "archetype_distribution": str(dict(list(top_arch.items())[:3])) if top_arch else "â€”",
        "anxiety_x_archetype_crosstab": "; ".join(crosstab_lines) if crosstab_lines else "â€”",
    }

    _section_header(
        "Respondent Profiles",
        "Demographic & psychographic composition of study respondents.",
        n, _C["accent"],
    )

    _anx_pct  = round(100 * high_anx / n) if n else 0
    _res_pct  = round(100 * pos_res / n) if n else 0
    _prom_pct = round(100 * promo / n) if n else 0
    _insight_banner(
        f"{_anx_pct}% show high financial anxiety, yet {_res_pct}% resolve to positive intent â€” "
        f"{_fmt_val(top_arch_str)} archetype dominates",
        f"NPS: {promo}/{n} promoters ({_prom_pct}%) Â· {len(segs)} segments Â· safety-first positioning indicated",
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    _kpi(c1, "Respondents", str(n), _C["accent"])
    _kpi(c2, "NPS Promoters", f"{promo}/{n}", _C["pos"])
    _kpi(c3, "High Anxiety", f"{high_anx}/{n}", _C["neg"], "financial anxiety level")
    _kpi(c4, "Positive Resolution", f"{pos_res}/{n}", _C["pos"])
    _kpi(c5, "Avg Adoption Intent", f"{avg_int:.1f}/10" if avg_int else "â€”", _C["r1"])

    st.markdown("---")

    st.markdown("#### Respondent Profiles by Segment")
    cols = st.columns(max(len(segs), 1))
    for col, seg in zip(cols, segs):
        color = _seg_color(seg)
        seg_ms = _seg_matrices(matrices, seg)
        sn = len(seg_ms)
        avg_int_s  = _avg([_get(m, "adoption.intent_score") for m in seg_ms])
        avg_comp_s = _avg([_get(m, "concept_understanding.comprehension_score") for m in seg_ms])
        avg_r1     = _avg([_get(m, "route1_evaluation.overall_appeal_score") for m in seg_ms])
        avg_r2     = _avg([_get(m, "route2_evaluation.overall_appeal_score") for m in seg_ms])
        promo_s    = sum(1 for m in seg_ms if (m.get("nps_signal") or "").lower() == "promoter")
        top_arch_s = _count_field(seg_ms, "investor_archetype")
        arch_str   = max(top_arch_s, key=top_arch_s.get) if top_arch_s else "â€”"
        top_stage_s = _count_field(seg_ms, "life_stage")
        stage_str  = max(top_stage_s, key=top_stage_s.get) if top_stage_s else "â€”"
        top_anx_s  = _count_field(seg_ms, "financial_anxiety_level")
        anx_str    = max(top_anx_s, key=top_anx_s.get) if top_anx_s else "â€”"
        top_route  = _count_field(seg_ms, "preferred_route")
        route_str  = max(top_route, key=top_route.get) if top_route else "â€”"
        top_trust  = _count_trust_gap(seg_ms)
        trust_str  = max(top_trust, key=top_trust.get) if top_trust else "â€”"
        seg_card_stats = {
            "Archetype":       _fmt_val(arch_str),
            "Life stage":      _fmt_val(stage_str),
            "Anxiety level":   _fmt_val(anx_str),
            "Preferred route": _fmt_val(route_str),
        }
        if trust_str != "â€”":
            seg_card_stats["Crypto trust gap"] = _fmt_val(trust_str)
        seg_card_stats.update({
            "Avg intent":        f"{avg_int_s:.1f}/10" if avg_int_s else "â€”",
            "Avg comprehension": f"{avg_comp_s:.1f}/10" if avg_comp_s else "â€”",
            "R1 appeal":         f"{avg_r1:.1f}/10" if avg_r1 else "â€”",
            "R2 appeal":         f"{avg_r2:.1f}/10" if avg_r2 else "â€”",
            "NPS promoters":     f"{promo_s}/{sn}",
        })
        with col:
            _seg_card(seg, color, sn, seg_card_stats)

    st.markdown("---")

    fields = _detect_fields(matrices)
    anx_counts   = _count_field(matrices, "financial_anxiety_level") if "financial_anxiety_level" in fields else {}
    stage_counts = _count_field(matrices, "life_stage") if "life_stage" in fields else {}
    arch_counts  = _count_field(matrices, "investor_archetype") if "investor_archetype" in fields else {}
    anx_groups   = _group_field(matrices, "financial_anxiety_level") if "financial_anxiety_level" in fields else {}
    stage_groups = _group_field(matrices, "life_stage") if "life_stage" in fields else {}
    arch_groups  = _group_field(matrices, "investor_archetype") if "investor_archetype" in fields else {}

    col_ch1, col_ch2, col_ch3 = st.columns(3)
    if anx_counts:
        with col_ch1:
            _chart_header(
                "Financial Anxiety Level",
                subtitle="How stressed respondents feel about their finances right now.",
                how_to_read="High anxiety = needs safety-proof messaging. Low anxiety = can lead with returns.",
                calc_note="Calculated: AI-classified from interview transcript. The extraction prompt reads respondent's narrative about financial concerns and stress, then classifies as high/medium/low. Not a questionnaire â€” inferred from language and tone.",
            )
            _legend_row([
                ("High",   _C["neg"], "worried about loss â€” needs reassurance"),
                ("Medium", _C["amb"], "cautious but open"),
                ("Low",    _C["pos"], "confident investor"),
            ])
            _anx_color_map = {"high": _C["neg"], "medium": _C["amb"], "low": _C["pos"]}
            anx_keys = list(anx_counts.keys())
            anx_bar_colors = [_anx_color_map.get(k.lower(), _C["muted"]) for k in anx_keys]
            _chart_click_filter(
                _v_bar(anx_keys, list(anx_counts.values()),
                       "Financial Anxiety Level", colors=anx_bar_colors,
                       resp_groups=list(anx_groups.values())),
                key="ctcf_financial_anxiety_level", lbls_raw=anx_keys,
                field="financial_anxiety_level", enabled=True,
            )
            _chart_caption("Bar height = number of respondents. High anxiety investors respond better to safety and capital-protection messaging. Click a bar to filter every chart on this page.")
    if stage_counts:
        with col_ch2:
            _chart_header(
                "Life Stage Distribution",
                subtitle="Where each respondent is in their financial life journey.",
                how_to_read="Stage determines financial priorities â€” early career = growth, settled = preservation.",
            )
            _legend_row([
                ("Early career", _C["r1"],     "building wealth, high growth appetite"),
                ("Growing",      _C["r1"],     "accumulating assets, family milestone phase"),
                ("Established",  _C["seg_dg"], "preserving wealth, stability-focused"),
                ("Transitional", _C["amb"],    "life event change â€” volatile priorities"),
            ])
            _stage_raw = list(stage_counts.keys())
            _stage_fmt = {_fmt_val(k): v for k, v in stage_counts.items()}
            _chart_click_filter(
                _h_bar(list(_stage_fmt.keys()), list(_stage_fmt.values()),
                       "Life Stage Distribution", _C["r1"],
                       resp_groups=list(stage_groups.values())),
                key="ctcf_life_stage", lbls_raw=_stage_raw,
                field="life_stage", enabled=True,
            )
            _chart_caption("Bar length = number of respondents in that life stage. Longer bar = dominant group shaping product fit. Click a bar to filter every chart on this page.")
    if arch_counts:
        with col_ch3:
            _chart_header(
                "Investor Archetype",
                subtitle="How each respondent mentally frames investment decisions.",
                how_to_read="Archetype determines which product angle lands â€” safety vs returns vs convenience.",
                calc_note="Calculated: AI-classified from interview. The extraction prompt reads how the respondent talks about investment priorities, risk tolerance, and decision-making, then assigns one archetype. Options: Safety Seeker, Growth Oriented, Opportunity Hunter, Yield Optimizer.",
            )
            _legend_row([
                ("Safety Seeker",     _C["pos"],     "capital protection first, returns secondary"),
                ("Opportunity Hunter",_C["r1"],      "growth-focused, acts on market signals"),
                ("Growth Oriented",   _C["seg_dg"],  "systematic accumulation, long horizon"),
                ("Yield Optimizer",   _C["amb"],      "income-seeking, benchmarks against FD/SGB"),
            ])
            _arch_raw = list(arch_counts.keys())
            _arch_fmt = {_fmt_val(k): v for k, v in arch_counts.items()}
            _chart_click_filter(
                _h_bar(list(_arch_fmt.keys()), list(_arch_fmt.values()),
                       "Investor Archetype", _C["seg_dg"],
                       resp_groups=list(arch_groups.values())),
                key="ctcf_investor_archetype", lbls_raw=_arch_raw,
                field="investor_archetype", enabled=True,
            )
            _chart_caption("Dominant archetype in sample signals which product narrative to lead with â€” safety proof vs yield story. Click a bar to filter every chart on this page.")

    st.markdown("---")
    _profile_exclude = EXCLUDE_AUTO_CHART | {p for p in _detect_fields(matrices) if _prefix_claimed(p)}
    _render_auto_charts(matrices, _profile_exclude, key_prefix="profiles")

    st.markdown("---")
    st.markdown("#### Verbatim Evidence â€” Respondent Mindsets")
    _verbatim_wall(
        _get_passages(matrices, topics=[
            "portfolio_behaviour", "gold_behaviour",
            "financial_ambitions", "financial_anxiety", "life_stage",
        ]),
        "prof_vb",
    )

    st.markdown("---")
    st.markdown("#### AI Research Finding")
    _ai_finding_robust(
        "respondent_profile", findings_dir, call_or, ctx, active_filters,
        "Synthesize the investor psychology and psychographic patterns across segments. "
        "Focus on what the anxiety levels, archetypes, and intent scores reveal about "
        "receptivity to the product concept.",
        "prof_regen", proj_name,
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SECTION 2 â€” GOLD CATEGORY KNOWLEDGE
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _render_gold_category(matrices: list[dict], findings_dir: str, call_or: Callable,
                          active_filters: dict, proj_name: str) -> None:
    n = len(matrices)

    digital_owners = sum(1 for m in matrices
                         if "digital_gold" in [str(x) for x in (_get(m, "gold_behavior.formats_owned") or [])])
    sgb_aware = sum(1 for m in matrices
                    if (_get(m, "gold_behavior.sgb_awareness") or "").lower() in ("high", "medium"))
    avg_gold_pct = _avg([_get(m, "portfolio_behavior.portfolio_split.gold_pct") for m in matrices])

    format_counts    = _count_list_field(matrices, "gold_behavior.formats_owned")
    role_counts      = _count_field(matrices, "gold_behavior.gold_role")
    sgb_counts       = _count_field(matrices, "gold_behavior.sgb_awareness")
    platform_counts  = _count_list_field(matrices, "portfolio_behavior.platforms_used")
    info_counts      = _count_list_field(matrices, "portfolio_behavior.info_sources")
    trigger_counts   = _count_field(matrices, "gold_behavior.purchase_trigger")
    format_groups    = _group_list_field(matrices, "gold_behavior.formats_owned")
    role_groups      = _group_field(matrices, "gold_behavior.gold_role")
    sgb_groups       = _group_field(matrices, "gold_behavior.sgb_awareness")
    platform_groups  = _group_list_field(matrices, "portfolio_behavior.platforms_used")
    info_groups      = _group_list_field(matrices, "portfolio_behavior.info_sources")
    trigger_groups   = _group_field(matrices, "gold_behavior.purchase_trigger")

    top_role     = max(role_counts, key=role_counts.get) if role_counts else "â€”"
    top_platform = max(platform_counts, key=platform_counts.get) if platform_counts else "â€”"
    top_info_src = max(info_counts, key=info_counts.get) if info_counts else "â€”"

    ctx = {
        "n": n,
        "digital_gold_owners": f"{digital_owners}/{n} ({_pct_str(digital_owners, n)})",
        "sgb_aware_medium_high": f"{sgb_aware}/{n} ({_pct_str(sgb_aware, n)})",
        "avg_gold_portfolio_pct": f"{round(avg_gold_pct)}%" if avg_gold_pct else "â€”",
        "top_gold_role": top_role,
        "total_format_mentions": str(sum(format_counts.values())) if format_counts else "â€”",
        "top_formats": str(dict(list(format_counts.items())[:3])) if format_counts else "â€”",
        "top_investment_platform": top_platform,
        "top_info_source": top_info_src,
        "top_purchase_trigger": max(trigger_counts, key=trigger_counts.get) if trigger_counts else "â€”",
    }

    _section_header(
        "Gold Category Knowledge",
        "Existing familiarity with gold investment formats, platforms, and information sources.",
        n, _C["seg_dg"],
    )

    _insight_banner(
        f"{digital_owners}/{n} respondents own digital gold Â· "
        f"{sgb_aware}/{n} show medium/high SGB awareness Â· "
        f"{'Avg gold allocation: ' + str(round(avg_gold_pct)) + '%' if avg_gold_pct else 'Gold allocation not reported'}",
        "Category familiarity shapes receptivity to a gold-with-yield proposition.",
        color=_C["seg_dg"],
    )

    c1, c2, c3, c4 = st.columns(4)
    _kpi(c1, "Digital Gold Owners", f"{digital_owners}/{n}", _C["seg_dg"])
    _kpi(c2, "SGB Aware (med/high)", f"{sgb_aware}/{n}", _C["pos"])
    _kpi(c3, "Gold Format Mentions", str(sum(format_counts.values())) if format_counts else "â€”", _C["r1"])
    _kpi(c4, "Avg Gold Portfolio %", f"{round(avg_gold_pct)}%" if avg_gold_pct else "â€”", _C["seg_dg"])

    st.markdown("---")

    col1, col2 = st.columns(2)
    if format_counts:
        with col1:
            _chart_header(
                "Gold Formats Currently Owned",
                subtitle="Physical jewellery, digital gold, ETFs, SGB, coins/bars â€” respondents could select multiple.",
                how_to_read="Longer bar = more respondents own that format. Shows how familiar audience is with digital vs physical gold.",
            )
            st.plotly_chart(
                _h_bar(list(format_counts.keys()), list(format_counts.values()),
                       "Gold Formats Owned (mentions)", _C["seg_dg"],
                       resp_groups=list(format_groups.values())),
                use_container_width=True)
            _chart_caption("Multiple formats possible per respondent. High digital gold ownership = primed for yield conversation.")
    if role_counts:
        with col2:
            _chart_header(
                "Gold's Role in Their Portfolio",
                subtitle="How each respondent frames why they hold gold â€” shapes which product angle will resonate.",
                how_to_read="Dominant role = primary messaging hook. Safety-store majority needs capital-protection narrative first.",
            )
            _legend_row([
                ("Safety store",   _C["pos"],     "gold as protection â€” hedge against inflation/crisis"),
                ("Return vehicle", _C["seg_dg"],  "gold as profit â€” buy low sell high"),
                ("Tradition",      _C["amb"],      "gold as cultural obligation â€” not purely financial"),
            ])
            _role_raw = list(role_counts.keys())
            _chart_click_filter(
                _donut(_role_raw, list(role_counts.values()),
                       "Gold Role in Portfolio",
                       resp_groups=list(role_groups.values())),
                key="ctcf_gold_role", lbls_raw=_role_raw,
                field="gold_behavior.gold_role", enabled=True,
            )
            _chart_caption("Slice size = % of respondents who hold gold primarily for that reason. Click a slice to filter every chart on this page.")

    col3, col4 = st.columns(2)
    if platform_counts:
        top_platforms = dict(list(platform_counts.items())[:10])
        top_plat_grps = list(platform_groups.values())[:10]
        with col3:
            _chart_header(
                "Investment Platforms Currently Used",
                subtitle="Apps and platforms where respondents actively invest â€” indicates digital finance comfort level.",
                how_to_read="Longer bar = more users on that platform. CoinDCX visibility vs fintech competitors visible here.",
            )
            st.plotly_chart(
                _h_bar(list(top_platforms.keys()), list(top_platforms.values()),
                       "Investment Platforms Used", _C["r1"],
                       resp_groups=top_plat_grps),
                use_container_width=True)
            _chart_caption("Respondents using multiple platforms. Shows digital investment literacy and competitive context.")
    if info_counts:
        top_info = dict(list(info_counts.items())[:10])
        top_info_grps = list(info_groups.values())[:10]
        with col4:
            _chart_header(
                "Where Respondents Get Investment Information",
                subtitle="Media, social, advisor channels â€” determines where CoinDCX Karat should reach this audience.",
                how_to_read="Top sources = where product education and trust-building must happen. Longer bar = more respondents use it.",
            )
            st.plotly_chart(
                _h_bar(list(top_info.keys()), list(top_info.values()),
                       "Financial Info Sources", _C["r2"],
                       resp_groups=top_info_grps),
                use_container_width=True)
            _chart_caption("High social/YouTube reliance = influencer and video content strategy. High advisor = consultant channel.")

    if sgb_counts:
        _chart_header(
            "SGB (Sovereign Gold Bond) Awareness",
            subtitle="Sovereign Gold Bonds are the government's yield-bearing gold instrument. Awareness sets yield expectation benchmarks.",
            how_to_read="High SGB awareness = respondents already know yield on gold is possible â€” lowers 'too good to be true' barrier.",
            calc_note="Calculated: extracted from gold_behavior.sgb_awareness. Prompt asks the model to classify respondent's familiarity with SGB (Sovereign Gold Bonds â€” government-issued bonds offering 2.5% annual yield on gold value) as high/medium/low based on how they discussed it.",
        )
        _legend_row([
            ("High",   _C["pos"], "knows SGB well â€” benchmarks any yield claim against 2.5% RBI coupon"),
            ("Medium", _C["amb"], "heard of it â€” partial understanding"),
            ("Low",    _C["neg"], "unaware â€” yield concept needs more education"),
        ])
        _sgb_cmap = {"high": _C["pos"], "medium": _C["amb"], "low": _C["neg"]}
        sgb_keys = list(sgb_counts.keys())
        sgb_colors = [_sgb_cmap.get(k.lower(), _C["neu"]) for k in sgb_keys]
        _chart_click_filter(
            _v_bar(sgb_keys, list(sgb_counts.values()), "SGB Awareness Level", colors=sgb_colors,
                   resp_groups=list(sgb_groups.values())),
            key="ctcf_sgb_awareness", lbls_raw=sgb_keys,
            field="gold_behavior.sgb_awareness", enabled=True,
        )
        _chart_caption("Bar height = respondent count. High SGB awareness makes the yield claim credible â€” they have a reference point. Click a bar to filter every chart on this page.")

    if trigger_counts:
        _chart_header(
            "What Triggers a Gold Purchase Decision",
            subtitle="The event or condition that moves a respondent from intent to actual investment.",
            how_to_read="Tactical triggers (price dip, festival) vs strategic triggers (goal, milestone). Tells you when and how to reach them.",
        )
        _trigger_raw = list(trigger_counts.keys())
        _chart_click_filter(
            _h_bar(_trigger_raw, list(trigger_counts.values()),
                   "Purchase Triggers", _C["amb"],
                   resp_groups=list(trigger_groups.values())),
            key="ctcf_purchase_trigger", lbls_raw=_trigger_raw,
            field="gold_behavior.purchase_trigger", enabled=True,
        )
        _chart_caption("Longer bar = more respondents driven by that trigger. Dominant trigger shapes seasonal/event-based marketing strategy. Click a bar to filter every chart on this page.")

    # â”€â”€ LOOPHOLE A: Portfolio Asset Allocation Wallet Share â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _ASSET_FIELDS = [
        ("gold_pct",    "Gold",           _C["seg_dg"]),
        ("equity_pct",  "Equities",       _C["r1"]),
        ("mf_pct",      "Mutual Funds",   _C["r2"]),
        ("fd_pct",      "Fixed Deposits", _C["seg_fd"]),
        ("crypto_pct",  "Crypto",         _C["neu"]),
        ("savings_pct", "Savings / Cash", _C["amb"]),
    ]
    _asset_avgs, _asset_labels, _asset_colors = [], [], []
    for field, label, col in _ASSET_FIELDS:
        vals = [
            float(v) for m in matrices
            for v in [_get(m, f"portfolio_behavior.portfolio_split.{field}")]
            if isinstance(v, (int, float))
        ]
        if vals:
            _asset_avgs.append(round(sum(vals) / len(vals), 1))
            _asset_labels.append(label)
            _asset_colors.append(col)

    # â”€â”€ LOOPHOLE B: Digital Gold View perception card â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _dg_view_counts: dict = {}
    for m in matrices:
        v = _get(m, "gold_behavior.digital_gold_view") or _get(m, "digital_gold_view")
        if v and str(v).strip().lower() not in ("none", "not_mentioned", ""):
            _dg_view_counts[str(v)] = _dg_view_counts.get(str(v), 0) + 1

    if len(_asset_avgs) >= 2 or _dg_view_counts:
        st.markdown("---")
        st.markdown("#### Portfolio Wallet Share & Digital Gold Perception")
        _col_a, _col_b = st.columns(2)

        if len(_asset_avgs) >= 2:
            with _col_a:
                _chart_header(
                    "Portfolio Wallet Share by Asset",
                    subtitle="Average % of investment portfolio allocated to each asset class, self-reported by respondents.",
                    how_to_read="Bar height = average allocation %. Gold vs others shows wallet-share competition â€” higher gold = easier to retain with a gold product.",
                )
                _aa_fig = _v_bar(
                    _asset_labels, _asset_avgs,
                    "Avg Portfolio Allocation by Asset (%)",
                    colors=_asset_colors, h=260,
                )
                _aa_fig.update_layout(yaxis=dict(**_GRID_STYLE, tickfont=dict(size=9)))
                st.plotly_chart(_aa_fig, use_container_width=True)
                _chart_caption(
                    "Average self-reported allocation % (respondents who shared splits). "
                    "Gold bar vs other assets reveals wallet-share competition."
                )

        if _dg_view_counts:
            _DG_VIEW_LABELS = {
                "smart_convenient": ("Smart & Convenient",  _C["pos"]),
                "suspicious":        ("Suspicious / Risky",  _C["neg"]),
                "innovative":        ("Innovative",          _C["r1"]),
                "risky":             ("Risky",               _C["neg"]),
                "equivalent":        ("Like Physical Gold",  _C["seg_dg"]),
            }
            _total_dg = sum(_dg_view_counts.values())
            _dg_html = ""
            for _raw_k, _cnt in sorted(_dg_view_counts.items(), key=lambda x: -x[1]):
                _lbl, _lcol = _DG_VIEW_LABELS.get(_raw_k, (_raw_k.replace("_", " ").title(), _C["neu"]))
                _pct = round(100 * _cnt / _total_dg)
                _dg_html += (
                    f'<div style="display:flex;align-items:center;gap:10px;'
                    f'padding:8px 0;border-bottom:1px solid {_C["border"]};">'
                    f'<div style="width:12px;height:12px;border-radius:3px;'
                    f'background:{_lcol};flex-shrink:0;"></div>'
                    f'<div style="flex:1;font-size:0.8rem;color:{_C["text"]};'
                    f'font-weight:600;">{_esc(_lbl)}</div>'
                    f'<div style="font-size:0.82rem;font-weight:800;color:{_lcol};">'
                    f'{_cnt} <span style="color:{_C["muted"]};font-weight:400;'
                    f'font-size:0.7rem;">({_pct}%)</span></div></div>'
                )
            with _col_b:
                st.markdown(
                    f'<div style="border:1px solid {_C["border"]};'
                    f'border-top:3px solid {_C["seg_dg"]};'
                    f'border-radius:0 0 8px 8px;padding:16px;margin-top:6px;">'
                    f'<div style="font-size:0.62rem;font-weight:900;color:{_C["seg_dg"]};'
                    f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px;">'
                    f'How Respondents Perceive Digital Gold</div>'
                    f'{_dg_html}</div>',
                    unsafe_allow_html=True,
                )
                _chart_caption(
                    "Spontaneous perception of digital gold. "
                    "'Smart & Convenient' signals positive receptivity; "
                    "'Suspicious/Risky' signals a trust barrier to address."
                )

    st.markdown("---")
    _render_auto_charts(matrices, EXCLUDE_AUTO_CHART, include_prefixes=CATEGORY_PREFIXES["gold_category"],
                        key_prefix="goldcat")

    st.markdown("---")
    st.markdown("#### Verbatim Evidence â€” Gold & Portfolio Behaviour")
    _verbatim_wall(
        _get_passages(matrices, topics=[
            "gold_behaviour", "portfolio_behaviour", "info_sources",
        ]),
        "gold_vb",
    )

    st.markdown("---")
    st.markdown("#### AI Research Finding")
    _ai_finding_robust(
        "category_knowledge", findings_dir, call_or, ctx, active_filters,
        "Synthesize gold category knowledge, format familiarity, and platform usage patterns. "
        "What does existing behaviour tell us about readiness for a digital gold + yield product?",
        "gold_regen", proj_name,
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SECTION 3 â€” CONCEPT TESTING
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _render_concept_testing(matrices: list[dict], findings_dir: str, call_or: Callable,
                            active_filters: dict, proj_name: str) -> None:
    n = len(matrices)

    avg_comp   = _avg([_get(m, "concept_understanding.comprehension_score") for m in matrices])
    avg_r1_app = _avg([_get(m, "route1_evaluation.overall_appeal_score") for m in matrices])
    avg_r2_app = _avg([_get(m, "route2_evaluation.overall_appeal_score") for m in matrices])
    full_comp  = sum(1 for m in matrices if (_get(m, "concept_understanding.comprehension_score") or 0) >= 8)
    low_comp   = sum(1 for m in matrices if (_get(m, "concept_understanding.comprehension_score") or 10) <= 5)

    # Claim reaction summary â€” same defensive guard as benchmark_comparisons above: this field's
    # shape isn't schema-enforced, so skip anything that isn't the expected dict shape.
    claim_data: dict[str, Counter] = defaultdict(Counter)
    for m in matrices:
        raw_cr = m.get("key_claim_reactions") or []
        if not isinstance(raw_cr, list):
            continue
        for cr in raw_cr:
            if not isinstance(cr, dict):
                continue
            claim = str(cr.get("claim") or "").replace("_", " ")
            reaction = str(cr.get("reaction") or "unclear")
            if claim:
                claim_data[claim][reaction] += 1
    top_positive_claim = ""
    if claim_data:
        def _claim_pos_score(cd: Counter) -> int:
            return cd.get("strong", 0) + cd.get("yes", 0) + cd.get("full", 0)
        top_positive_claim = max(claim_data, key=lambda c: _claim_pos_score(claim_data[c]))

    tagline_counts = _count_field(matrices, "tagline_reaction.preferred_tagline")
    tagline_groups = _group_field(matrices, "tagline_reaction.preferred_tagline")
    top_tagline = max(tagline_counts, key=tagline_counts.get) if tagline_counts else "â€”"

    # Route-level attribute summary (safe â€” these may not exist for all studies)
    r1_safety_strong = sum(
        1 for m in matrices
        if str(_get(m, "route1_evaluation.safety_proof_resonance") or "").lower() in ("strong", "yes")
    )
    r2_yield_skeptic = sum(
        1 for m in matrices
        if _get(m, "route2_evaluation.yield_too_good_to_be_true") is True
    )

    ctx = {
        "n": n,
        "avg_comprehension_score": f"{avg_comp:.1f}/10" if avg_comp else "â€”",
        "high_comprehension_ge8": f"{full_comp}/{n} ({_pct_str(full_comp, n)})",
        "low_comprehension_le5": f"{low_comp}/{n} ({_pct_str(low_comp, n)})",
        "route1_avg_appeal": f"{avg_r1_app:.1f}/10" if avg_r1_app else "â€”",
        "route2_avg_appeal": f"{avg_r2_app:.1f}/10" if avg_r2_app else "â€”",
        "top_positive_claim_reaction": top_positive_claim if top_positive_claim else "â€”",
        "preferred_tagline": top_tagline,
        "r1_safety_proof_strong": f"{r1_safety_strong}/{n}" if r1_safety_strong else "â€”",
        "r2_yield_too_good_to_be_true": f"{r2_yield_skeptic}/{n}" if r2_yield_skeptic else "â€”",
        "total_claims_tested": str(len(claim_data)) if claim_data else "â€”",
    }

    _section_header(
        "Concept Testing",
        "Comprehension scores, route-level attribute evaluation, claim reactions, and tagline preference.",
        n, _C["r1"],
    )

    if avg_comp:
        _hc_pct   = round(100 * full_comp / n) if n else 0
        _lc_pct   = round(100 * low_comp / n) if n else 0
        _r_diff   = round(abs((avg_r2_app or 0) - (avg_r1_app or 0)), 1) if avg_r1_app and avg_r2_app else None
        _r_winner = "Route 2 (Returns)" if (avg_r2_app or 0) >= (avg_r1_app or 0) else "Route 1 (Safety)"
        _route_line = f"{_r_winner} leads on appeal by {_r_diff} pts" if _r_diff and _r_diff > 0 else "Routes nearly tied on appeal"
        _insight_banner(
            f"Comprehension below target â€” only {_hc_pct}% (â‰¥8/10) fully grasp the concept Â· "
            f"{_route_line}",
            f"{low_comp}/{n} ({_lc_pct}%) score â‰¤5 â€” simplify messaging priority Â· "
            f"R1: {avg_r1_app:.1f}/10 Â· R2: {avg_r2_app:.1f}/10",
            color=_C["r1"],
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    _kpi(c1, "Respondents", str(n), _C["accent"])
    _kpi(c2, "Avg Comprehension", f"{avg_comp:.1f}/10" if avg_comp else "â€”", _C["r1"])
    _kpi(c3, "R1 Avg Appeal", f"{avg_r1_app:.1f}/10" if avg_r1_app else "â€”", _C["r1"], "safety/ownership")
    _kpi(c4, "R2 Avg Appeal", f"{avg_r2_app:.1f}/10" if avg_r2_app else "â€”", _C["r2"], "returns/value")
    _kpi(c5, "High Comp (â‰¥8)", f"{full_comp}/{n}", _C["pos"])

    st.markdown("---")

    # Comprehension gauge + distribution bar
    if avg_comp:
        mid_comp = sum(
            1 for m in matrices
            if 5 <= (_get(m, "concept_understanding.comprehension_score") or 0) <= 7
        )
        gg_col, dist_col = st.columns(2)
        with gg_col:
            _chart_header(
                "Concept Comprehension Score",
                subtitle=f"AI-rated 1â€“10: did the respondent truly understand the Digital Gold + Yield proposition? n={n}.",
                how_to_read="Needle position = average across all interviews. Green zone (â‰¥7.5) = good. Red zone (â‰¤5) = messaging too complex.",
                calc_note="Calculated: AI extraction from transcript â€” the model was prompted to rate 1-10 how accurately the respondent could re-articulate the core proposition (digital gold + yield + safety proof) after it was explained. Not self-reported â€” based on what respondent actually said.",
            )
            st.plotly_chart(
                _gauge(avg_comp, 10, "Avg Comprehension Score", target=7,
                       color=_C["r1"], h=300),
                use_container_width=True)
            _chart_caption("Target = 7/10 (green line). Below 7 = simplify the concept explanation before launch. Score reflects genuine understanding, not just recall.")
        with dist_col:
            _chart_header(
                "Comprehension Quality Distribution",
                subtitle="How many respondents fell into each understanding band based on their comprehension score.",
                how_to_read="High band = ready for full product pitch. Low band = needs simpler language or analogies.",
                calc_note="Bands derived from comprehension_score: High = score â‰¥8, Medium = 5â€“7, Low = â‰¤4. Same AI-extracted score as the gauge â€” grouped for easy segmentation.",
            )
            _legend_row([
                ("High â‰¥8",    _C["pos"], "fully understood â€” can articulate the proposition back"),
                ("Medium 5â€“7", _C["amb"], "partial understanding â€” grasped headline, missed details"),
                ("Low â‰¤4",     _C["neg"], "confused â€” likely to reject or misuse the product"),
            ])
            _band_labels = ["High (â‰¥8)", "Medium (5â€“7)", "Low (â‰¤4)"]
            _band_vals   = [full_comp, mid_comp, low_comp]
            _band_colors = [_C["pos"], _C["amb"], _C["neg"]]
            _comp_band_groups = [
                [m for m in matrices if (_get(m, "concept_understanding.comprehension_score") or 0) >= 8],
                [m for m in matrices if 5 <= (_get(m, "concept_understanding.comprehension_score") or 0) <= 7],
                [m for m in matrices if (_get(m, "concept_understanding.comprehension_score") or 0) <= 4],
            ]
            st.plotly_chart(
                _v_bar(_band_labels, _band_vals, "Comprehension Bands",
                       colors=_band_colors, h=300,
                       resp_groups=_comp_band_groups),
                use_container_width=True)
            _chart_caption("Bar height = number of respondents in that band. High band respondents are your early adopters.")
        st.markdown("")

    # â”€â”€ Route 1 attributes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    r1_attrs = [
        ("route1_evaluation.safety_proof_resonance",          "Safety proof resonance"),
        ("route1_evaluation.fiu_understood",                  "FIU registration understood"),
        ("route1_evaluation.allocated_vault_understood",      "Allocated vault understood"),
        ("route1_evaluation.proof_of_reserves_understood",    "Proof of reserves understood"),
        ("route1_evaluation.yield_acceptance_given_safety_proof", "Yield acceptance (given safety)"),
    ]
    r1_data = [(path, label, _count_field(matrices, path)) for path, label in r1_attrs]
    has_r1 = any(counts for _, _, counts in r1_data)

    if has_r1:
        st.markdown(
            f'<div style="font-size:0.72rem;font-weight:900;color:{_C["r1"]};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin:8px 0 4px;">'
            f'Route 1 â€” Safety & Ownership</div>',
            unsafe_allow_html=True,
        )
        for _, label, counts in r1_data:
            if counts:
                _attr_row(label, counts, n, _C["r1"])
        st.markdown("")

    # â”€â”€ Route 2 attributes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    r2_attrs = [
        ("route2_evaluation.compounding_in_gold_units_understood", "Compounding in gold units understood"),
        ("route2_evaluation.tax_advantage_noticed",                "Tax advantage noticed"),
        ("route2_evaluation.t1_liquidity_understood",              "T+1 liquidity understood"),
        ("route2_evaluation.sgb_comparison_helped",               "SGB comparison helpful"),
    ]
    r2_data = [(path, label, _count_field(matrices, path)) for path, label in r2_attrs]
    has_r2 = any(counts for _, _, counts in r2_data)

    if has_r2:
        st.markdown(
            f'<div style="font-size:0.72rem;font-weight:900;color:{_C["r2"]};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin:8px 0 4px;">'
            f'Route 2 â€” Returns & Value</div>',
            unsafe_allow_html=True,
        )
        for _, label, counts in r2_data:
            if counts:
                _attr_row(label, counts, n, _C["r2"])
        ytg = [m for m in matrices if _get(m, "route2_evaluation.yield_too_good_to_be_true") is True]
        if matrices:
            _attr_row("Yield 'too good to be true'", {"yes": len(ytg), "no": n - len(ytg)}, n, _C["r2"])

    if has_r1 or has_r2:
        _chart_caption(
            "Each row = one attribute. Bar segments show distribution of responses "
            "(green=positive, amber=conditional, red=negative/skeptical). "
            "Dominant value shown right."
        )
        st.markdown("---")

    # â”€â”€ Key claim reactions heatmap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if claim_data:
        _chart_header(
            "Key Claim Reaction Heatmap",
            subtitle="How respondents reacted to each specific product claim â€” tested across all IDI transcripts.",
            how_to_read="Rows = claims. Columns = reaction types. Darker/brighter cell = more respondents with that reaction. Scan rows to find strongest claim.",
        )
        _legend_row([
            ("strong / yes / full", _C["pos"], "clear acceptance â€” claim landed"),
            ("partial / conditional", _C["amb"], "partially accepted â€” needs supporting proof"),
            ("skeptical / no / unclear", _C["neg"], "rejected or not understood"),
        ])
        all_reactions = sorted({r for c in claim_data.values() for r in c.keys()})
        claim_rows = sorted(claim_data.keys())
        z_mat = [[claim_data[cl].get(r, 0) for r in all_reactions] for cl in claim_rows]
        st.plotly_chart(
            _heatmap(claim_rows, all_reactions, z_mat,
                     "Claim Ã— Reaction (respondent count)",
                     h=max(260, len(claim_rows) * 38)),
            use_container_width=True,
        )
        _chart_caption(
            "Cell value = number of respondents. "
            "High 'strong' row = lead claim for product messaging. "
            "High 'skeptical' row = remove or reframe that claim before launch."
        )

    # Tagline preference
    if tagline_counts:
        _chart_header(
            "Preferred Tagline",
            subtitle="Which tagline each respondent found most memorable and accurate after seeing the concept.",
            how_to_read="Dominant slice = winning tagline. Large slice = clear winner â€” use that line as headline. Even split = test further.",
            calc_note="Calculated: extracted from tagline_reaction.preferred_tagline. The extraction prompt asks the model which tagline the respondent explicitly chose or responded most positively to during the concept show card session.",
        )
        _tagline_raw = list(tagline_counts.keys())
        _chart_click_filter(
            _donut(_tagline_raw, list(tagline_counts.values()),
                   "Tagline Preference", [_C["r1"], _C["r2"], _C["seg_dg"]],
                   resp_groups=list(tagline_groups.values())),
            key="ctcf_preferred_tagline", lbls_raw=_tagline_raw,
            field="tagline_reaction.preferred_tagline", enabled=True,
        )
        _chart_caption(
            "Slice size = respondents preferring that tagline. "
            "Dominant choice = use as primary marketing headline. "
            "Close split = tagline needs refinement or audience segmentation. "
            "Click a slice to filter every chart on this page."
        )

    st.markdown("---")
    _render_auto_charts(matrices, EXCLUDE_AUTO_CHART, include_prefixes=CATEGORY_PREFIXES["concept_testing"],
                        key_prefix="concept")

    st.markdown("---")
    st.markdown("#### Verbatim Evidence â€” Concept Reactions")
    _verbatim_wall(
        _get_passages(matrices, topics=[
            "concept_reaction", "claims",
            "yield", "yield_understanding", "yield comprehension",
        ]),
        "concept_vb",
        extra_filter_fields=[("Route Shown", "route_shown")],
    )

    st.markdown("---")
    st.markdown("#### AI Research Finding")
    _ai_finding_robust(
        "concept_testing", findings_dir, call_or, ctx, active_filters,
        "Synthesize comprehension quality, emotional reactions per route, and claim-level acceptance. "
        "What does the data reveal about concept clarity and which route communicates better?",
        "concept_regen", proj_name,
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SECTION 4 â€” ROUTE COMPARISON
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _render_route_comparison(matrices: list[dict], findings_dir: str, call_or: Callable,
                              active_filters: dict, proj_name: str) -> None:
    n = len(matrices)
    segs = sorted({str(_get(m, "respondent.segment") or "") for m in matrices
                   if _get(m, "respondent.segment")})

    pref_counts  = _count_field(matrices, "preferred_route")
    pref_groups  = _group_field(matrices, "preferred_route")
    r1_n      = sum(v for k, v in pref_counts.items() if "1" in str(k).lower() or "safety" in str(k).lower())
    r2_n      = sum(v for k, v in pref_counts.items() if "2" in str(k).lower() or "return" in str(k).lower())
    blended_n = sum(v for k, v in pref_counts.items() if "blend" in str(k).lower() or "both" in str(k).lower())
    neither_n = max(0, n - r1_n - r2_n - blended_n)

    r1_pct = r1_n / n if n else 0
    r2_pct = r2_n / n if n else 0

    # z-test on those who chose a single clear route
    n_pref = r1_n + r2_n
    z, pval = _prop_ztest(n_pref, r1_n / n_pref, n_pref, r2_n / n_pref) if n_pref > 0 else (0, 1.0)
    sig_str = f"p={pval:.3f} {'â˜… significant' if pval < 0.05 else '(not significant)'}"

    avg_r1_overall = _avg([_get(m, "route1_evaluation.overall_appeal_score") for m in matrices])
    avg_r2_overall = _avg([_get(m, "route2_evaluation.overall_appeal_score") for m in matrices])

    # P8: Element-level chi-square for route comparison attributes
    route_attr_sig: list[dict] = []
    r1_attr_paths = [
        ("route1_evaluation.safety_proof_resonance", "R1: Safety proof resonance"),
        ("route1_evaluation.yield_acceptance_given_safety_proof", "R1: Yield acceptance (safety)"),
    ]
    r2_attr_paths = [
        ("route2_evaluation.t1_liquidity_understood", "R2: T+1 liquidity understood"),
        ("route2_evaluation.sgb_comparison_helped", "R2: SGB comparison helpful"),
    ]
    positive_responses = {"strong", "yes", "full"}
    for path, label in r1_attr_paths + r2_attr_paths:
        counts = _count_field(matrices, path)
        if counts:
            pos_n = sum(v for k, v in counts.items() if k.lower() in positive_responses)
            neg_n = n - pos_n
            if n >= 2 and pos_n > 0 and neg_n > 0:
                p0 = 0.5  # test against equal split
                zs, ps = _prop_ztest(n, pos_n / n, n, p0)
                route_attr_sig.append({
                    "Attribute": label,
                    "Positive (n)": pos_n,
                    "Positive %": _pct_str(pos_n, n),
                    "z-stat": round(zs, 3),
                    "p-value": round(ps, 4),
                    "Sig": "â˜…" if ps < 0.05 else "",
                })

    # Real segment Ã— route-preference joint counts â€” without this, asking the AI to compare routes
    # "across segments" (the section's own instruction) gives it a segment NAME LIST and OVERALL
    # totals but no way to know which segment actually prefers which route, so it either invents a
    # number or (correctly, after the fabrication fix) refuses and stays generic. This gives it the
    # real numbers instead of forcing a choice between guessing and staying vague.
    seg_route_crosstab: dict[str, "Counter"] = {}
    for m in matrices:
        seg = str(_get(m, "respondent.segment") or "").strip()
        pref = str(_get(m, "preferred_route") or "").strip().lower()
        if not seg or not pref:
            continue
        label = ("Route1" if pref in ("route1", "1") else
                  "Route2" if pref in ("route2", "2") else
                  "Blended" if "blend" in pref else "Other")
        seg_route_crosstab.setdefault(seg, Counter())[label] += 1
    seg_route_lines = []
    for seg, counts in sorted(seg_route_crosstab.items()):
        total_s = sum(counts.values())
        parts = ", ".join(f"{lbl}: {c}/{total_s}" for lbl, c in counts.most_common())
        seg_route_lines.append(f"{seg} ({total_s} total) â†’ {parts}")

    ctx = {
        "n": n,
        "r1_preference": f"{r1_n}/{n} ({_pct_str(r1_n, n)})",
        "r2_preference": f"{r2_n}/{n} ({_pct_str(r2_n, n)})",
        "blended_both_routes": f"{blended_n}/{n} ({_pct_str(blended_n, n)})",
        "no_clear_preference": f"{neither_n}/{n}",
        "preference_significance": sig_str,
        "r1_avg_overall_appeal": f"{avg_r1_overall:.1f}/10" if avg_r1_overall else "â€”",
        "r2_avg_overall_appeal": f"{avg_r2_overall:.1f}/10" if avg_r2_overall else "â€”",
        "segments_compared": ", ".join(segs) if segs else "â€”",
        "segment_x_route_preference_crosstab": "; ".join(seg_route_lines) if seg_route_lines else "â€”",
        "significant_attribute_advantages": str([r["Attribute"] for r in route_attr_sig if r["Sig"] == "â˜…"]) if route_attr_sig else "â€”",
    }

    _section_header(
        "Route Comparison",
        "Head-to-head analysis of Route 1 (Safety/Ownership) vs Route 2 (Returns/Value) "
        "including preference significance and benchmark comparisons.",
        n, _C["r1"] if r1_n >= r2_n else _C["r2"],
    )

    _r1_pct = round(100 * r1_n / n) if n else 0
    _r2_pct = round(100 * r2_n / n) if n else 0
    _pref_route = "Route 2 (Returns)" if r2_n > r1_n else "Route 1 (Safety)"
    _appeal_line = (
        f"appeal gap: {abs(avg_r1_overall - avg_r2_overall):.1f} pts â€” routes close in perceived value"
        if avg_r1_overall and avg_r2_overall and abs(avg_r1_overall - avg_r2_overall) < 0.5
        else f"R1 appeal {avg_r1_overall:.1f} vs R2 {avg_r2_overall:.1f}"
        if avg_r1_overall and avg_r2_overall else ""
    )
    _blended_note = f" Â· {blended_n} blended" if blended_n > 0 else ""
    _insight_banner(
        f"{_pref_route} edges ahead ({_r2_pct if r2_n > r1_n else _r1_pct}% preference){_blended_note} Â· {sig_str}",
        _appeal_line,
        color=_C["r1"] if r1_n >= r2_n else _C["r2"],
    )

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    _kpi(c1, "R1 Preferred", f"{r1_n}/{n}", _C["r1"], "safety/ownership")
    _kpi(c2, "R2 Preferred", f"{r2_n}/{n}", _C["r2"], "returns/value")
    _kpi(c3, "Blended Both", f"{blended_n}/{n}", _C["amb"], "valued elements of both")
    _kpi(c4, "No Clear Pref", str(neither_n), _C["neu"])
    _kpi(c5, "R1 Avg Appeal", f"{avg_r1_overall:.1f}/10" if avg_r1_overall else "â€”", _C["r1"])
    _kpi(c6, "R2 Avg Appeal", f"{avg_r2_overall:.1f}/10" if avg_r2_overall else "â€”", _C["r2"])

    st.markdown("---")

    col1, col2 = st.columns([1, 1])
    if pref_counts:
        with col1:
            _chart_header(
                "Overall Route Preference",
                subtitle="Which communication route each respondent chose as more compelling after seeing both.",
                how_to_read="Larger slice = more respondents preferred that route. 'Blended' = valued elements of both equally.",
                calc_note="Calculated: extracted from preferred_route field. The extraction prompt asks the model which route the respondent explicitly stated they preferred, or â€” if not explicit â€” infers from language about what they found more convincing. Values: Route1, Route2, Blended, Unclear.",
            )
            _legend_row([
                ("Route 1", _C["r1"],  "Safety/Ownership-led â€” leads with capital protection and allocated vault"),
                ("Route 2", _C["r2"],  "Returns/Value-led â€” leads with yield, SGB comparison, compounding"),
                ("Blended", _C["amb"], "respondent found both routes equally compelling"),
            ])
            _pref_raw = list(pref_counts.keys())
            _chart_click_filter(
                _donut(_pref_raw, list(pref_counts.values()),
                       "Preferred Route",
                       [_C["r1"], _C["r2"], _C["neu"], _C["amb"]],
                       resp_groups=list(pref_groups.values())),
                key="ctcf_preferred_route", lbls_raw=_pref_raw,
                field="preferred_route", enabled=True,
            )
            _chart_caption("Click a slice to isolate respondents across every chart on this page. Hover for exact count. High 'blended' = both narratives are needed â€” neither standalone wins.")

    if segs:
        r1_by_seg = [round(_avg([_get(m, "route1_evaluation.overall_appeal_score")
                                 for m in _seg_matrices(matrices, s)]) or 0, 1)
                     for s in segs]
        r2_by_seg = [round(_avg([_get(m, "route2_evaluation.overall_appeal_score")
                                 for m in _seg_matrices(matrices, s)]) or 0, 1)
                     for s in segs]
        if any(v > 0 for v in r1_by_seg + r2_by_seg):
            _seg_grps = [_seg_matrices(matrices, s) for s in segs]
            with col2:
                _chart_header(
                    "Route Appeal Score by Investor Segment",
                    subtitle="Average appeal rating (1â€“10) for each route, broken out by investor segment.",
                    how_to_read="Taller bar = higher appeal. Segment with biggest R1 vs R2 gap = strongest route preference signal for targeting.",
                )
                st.plotly_chart(
                    _grouped_bar(segs, {"Route 1": r1_by_seg, "Route 2": r2_by_seg},
                                 "Appeal Score by Segment (1-10)", h=300,
                                 series_resp_groups={"Route 1": _seg_grps, "Route 2": _seg_grps}),
                    use_container_width=True)
                _chart_caption(
                    "Score = mean appeal rating out of 10 per segment. "
                    "A segment where R1 and R2 are both high = open to either approach. "
                    "Large gap = that segment has a strong preferred narrative."
                )

    # Route preference significance by segment
    if len(segs) > 1:
        st.markdown("#### Route Preference by Segment")
        rows = []
        for s in segs:
            seg_ms = _seg_matrices(matrices, s)
            sn = len(seg_ms)
            if sn < 2: continue
            s_pref_raw = {str(k): v for k, v in _count_field(seg_ms, "preferred_route").items()}
            s_r1 = sum(v for k, v in s_pref_raw.items() if "1" in k or "safety" in k.lower())
            s_r2 = sum(v for k, v in s_pref_raw.items() if "2" in k or "return" in k.lower())
            s_blended = sum(v for k, v in s_pref_raw.items()
                            if "blend" in k.lower() or "both" in k.lower())
            s_other = max(0, sn - s_r1 - s_r2 - s_blended)
            # z-test only among those expressing a single-route preference
            n_pref = s_r1 + s_r2
            if n_pref >= 4:
                sz, sp = _prop_ztest(n_pref, s_r1 / n_pref, n_pref, s_r2 / n_pref)
            else:
                sz, sp = 0.0, 1.0
            rows.append({
                "Segment":      s,
                "n (total)":    sn,
                "Route 1":      f"{s_r1} ({_pct_str(s_r1, sn)})",
                "Route 2":      f"{s_r2} ({_pct_str(s_r2, sn)})",
                "Blended":      f"{s_blended} ({_pct_str(s_blended, sn)})" if s_blended else "0",
                "No pref":      str(s_other) if s_other else "0",
                "z (R1 vs R2)": round(sz, 3),
                "p-value":      round(sp, 4),
                "Sig":          "â˜…" if sp < 0.05 else "",
            })
        if rows:
            import pandas as pd
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(
                "R1 + R2 + Blended + No pref = n (total). "
                "Blended = respondent valued elements of both routes. "
                "z-test compares R1 vs R2 among those expressing a single clear preference. "
                "â˜… = p < 0.05."
            )

    # Benchmark comparisons â€” this field's shape isn't schema-enforced (no declared sub_fields),
    # so different interviews can legitimately store it as a list of dicts, a list of strings, or
    # even a bare string â€” skip anything that isn't the expected {benchmark, verdict} dict shape
    # instead of crashing the whole tab on one inconsistently-shaped matrix.
    bench_data: dict[str, Counter] = defaultdict(Counter)
    for m in matrices:
        raw_bc = m.get("benchmark_comparisons") or []
        if isinstance(raw_bc, str):
            continue  # free-text summary, not structured comparisons â€” nothing to chart
        if not isinstance(raw_bc, list):
            continue
        for bc in raw_bc:
            if not isinstance(bc, dict):
                continue  # e.g. a list of plain strings â€” no benchmark/verdict to extract
            bench = str(bc.get("benchmark") or "")
            verdict = str(bc.get("verdict") or "unclear")
            if bench:
                bench_data[bench][verdict] += 1
    if bench_data:
        _chart_header(
            "Benchmark Comparison Verdicts",
            subtitle="How respondents judged CoinDCX Karat against familiar benchmarks like SGB, FDs, and physical gold.",
            how_to_read="Rows = benchmarks tested. Columns = verdicts. Bright cell = more respondents gave that verdict. Positive verdicts = product seen as competitive.",
        )
        _legend_row([
            ("positive / better", _C["pos"], "Karat seen as superior to this benchmark"),
            ("similar / unclear",  _C["amb"], "no clear advantage perceived"),
            ("negative / worse",   _C["neg"], "benchmark seen as more appealing"),
        ])
        verdicts = sorted({v for c in bench_data.values() for v in c.keys()})
        benchmarks = sorted(bench_data.keys())
        z_mat = [[bench_data[b].get(v, 0) for v in verdicts] for b in benchmarks]
        st.plotly_chart(
            _heatmap(benchmarks, verdicts, z_mat, "Benchmark Ã— Verdict (count)",
                     h=max(200, len(benchmarks) * 45)),
            use_container_width=True)
        _chart_caption(
            "How respondents compared the product to existing benchmarks (SGB, FDs, etc.). "
            "Positive verdicts indicate competitive superiority perception. "
            "High 'negative' for SGB = yield story needs strengthening."
        )

    st.markdown("---")
    _render_auto_charts(matrices, EXCLUDE_AUTO_CHART, include_prefixes=CATEGORY_PREFIXES["route_comparison"],
                        key_prefix="route")

    st.markdown("---")
    st.markdown("#### Verbatim Evidence â€” Route Reactions")
    _verbatim_wall(
        _get_passages(matrices, topics=[
            "concept_reaction", "claims",
            "liquidity", "liquidity mechanics",
            "benchmark comparison", "benchmark_comparison", "benchmark_comparisons",
        ]),
        "route_vb",
        extra_filter_fields=[
            ("Preferred Route", "pref_route"),
            ("Route Shown",     "route_shown"),
        ],
    )

    st.markdown("---")
    st.markdown("#### AI Research Finding")
    _ai_finding_robust(
        "route_comparison", findings_dir, call_or, ctx, active_filters,
        "Compare Route 1 (safety/ownership) vs Route 2 (returns/value). "
        "What drives preference? Which segment gravitates to which route and why? "
        "What do the benchmark comparisons reveal about competitive positioning?",
        "route_regen", proj_name,
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SECTION 5 â€” BRAND IMAGERY & TRUST
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _render_brand_trust(matrices: list[dict], findings_dir: str, call_or: Callable,
                        active_filters: dict, proj_name: str) -> None:
    n = len(matrices)

    # This project's matrices span two schema generations (see the normalizer functions above
    # _render_brand_trust) â€” legacy matrices nest trust detail under coindcx_trust as an object,
    # newer ones flatten coindcx_trust into a plain trust-level enum. _trust_gap_value/
    # _trust_builders_list/_platform_association_value merge both shapes so every respondent's
    # real data is visible regardless of which schema generation extracted them.
    trust_gap_counts = _count_trust_gap(matrices)
    trust_gap_groups = _group_trust_gap(matrices)

    low_trust_gap  = sum(1 for m in matrices if _trust_gap_value(m) == "low")
    high_trust_gap = sum(1 for m in matrices if _trust_gap_value(m) == "high")
    avg_intent    = _avg([_get(m, "adoption.intent_score") for m in matrices])
    intent_ge7    = sum(1 for m in matrices if (_get(m, "adoption.intent_score") or 0) >= 7)
    top_barrier   = _count_list_field(matrices, "adoption.barriers")
    top_driver    = _count_list_field(matrices, "adoption.drivers")
    top_bar_str   = max(top_barrier, key=top_barrier.get) if top_barrier else "â€”"
    top_driver_str = max(top_driver, key=top_driver.get) if top_driver else "â€”"
    top_builder   = _count_trust_builders(matrices)
    top_bld_str   = max(top_builder, key=top_builder.get) if top_builder else "â€”"
    builder_groups   = _group_trust_builders(matrices)
    driver_grp_map   = _group_list_field(matrices, "adoption.drivers")
    barrier_grp_map  = _group_list_field(matrices, "adoption.barriers")

    # Real segment Ã— trust-gap and trust-gap Ã— high-intent joint counts â€” same reasoning as Route
    # Comparison: without these, the AI can only see marginal totals for segment, trust gap, and
    # intent separately, so asking "which segment has the worst trust gap" or "does trust gap
    # actually suppress intent" has no real numbers to answer from.
    seg_trust_crosstab: dict[str, "Counter"] = {}
    for m in matrices:
        seg = str(_get(m, "respondent.segment") or "").strip()
        gap = _trust_gap_value(m)
        if seg and gap:
            seg_trust_crosstab.setdefault(seg, Counter())[gap] += 1
    seg_trust_lines = []
    for seg, counts in sorted(seg_trust_crosstab.items()):
        total_s = sum(counts.values())
        parts = ", ".join(f"{g}: {c}/{total_s}" for g, c in counts.most_common())
        seg_trust_lines.append(f"{seg} ({total_s} total) â†’ {parts}")

    trust_intent_crosstab: dict[str, int] = {}
    for gap_label in ("low", "medium", "high"):
        ms_in_gap = [m for m in matrices if _trust_gap_value(m) == gap_label]
        if ms_in_gap:
            hi = sum(1 for m in ms_in_gap if (_get(m, "adoption.intent_score") or 0) >= 7)
            trust_intent_crosstab[gap_label] = f"{hi}/{len(ms_in_gap)} high-intent (â‰¥7)"

    ctx = {
        "n": n,
        "low_trust_gap": f"{low_trust_gap}/{n} ({_pct_str(low_trust_gap, n)})" if low_trust_gap or high_trust_gap else "â€”",
        "high_trust_gap": f"{high_trust_gap}/{n} ({_pct_str(high_trust_gap, n)})" if high_trust_gap else "â€”",
        "avg_adoption_intent": f"{avg_intent:.1f}/10" if avg_intent else "â€”",
        "high_intent_ge7": f"{intent_ge7}/{n} ({_pct_str(intent_ge7, n)})",
        "top_trust_builder": top_bld_str,
        "top_adoption_driver": top_driver_str,
        "top_adoption_barrier": top_bar_str,
        "total_trust_builders_mentioned": str(sum(top_builder.values())) if top_builder else "â€”",
        "total_barriers_mentioned": str(sum(top_barrier.values())) if top_barrier else "â€”",
        "segment_x_trust_gap_crosstab": "; ".join(seg_trust_lines) if seg_trust_lines else "â€”",
        "trust_gap_x_high_intent_crosstab": str(trust_intent_crosstab) if trust_intent_crosstab else "â€”",
    }

    _section_header(
        "Trust & Adoption",
        "Platform trust gap, adoption drivers & barriers, and intent score distribution.",
        n, _C["seg_st"],
    )

    _gap_pct  = round(100 * high_trust_gap / n) if n else 0
    _int_pct  = round(100 * intent_ge7 / n) if n else 0
    _insight_banner(
        f"{_gap_pct}% carry HIGH trust gap â€” {_fmt_val(top_bar_str)} is the adoption blocker, "
        f"{_fmt_val(top_bld_str)} is the credibility lever",
        f"Despite trust gap, {_int_pct}% ({intent_ge7}/{n}) score intent â‰¥7/10 â€” "
        f"proof-point messaging can close the gap",
        color=_C["neg"] if high_trust_gap > low_trust_gap else _C["seg_st"],
    )

    c1, c2, c3, c4 = st.columns(4)
    _kpi(c1, "High Trust Gap", f"{high_trust_gap}/{n}", _C["neg"], "adoption risk")
    _kpi(c2, "Avg Adoption Intent", f"{avg_intent:.1f}/10" if avg_intent else "â€”", _C["r1"])
    _kpi(c3, "Top Trust Builder", _fmt_val(top_bld_str)[:30], _C["pos"])
    _kpi(c4, "Top Adoption Barrier", _fmt_val(top_bar_str)[:30], _C["neg"])

    st.markdown("---")

    col1, col2 = st.columns(2)
    if trust_gap_counts:
        with col1:
            _chart_header(
                "CoinDCX Trust Gap Level",
                subtitle="How much each respondent's trust in CoinDCX is undermined by crypto/fintech distrust associations.",
                how_to_read="Low gap = brand trust overcomes crypto stigma, adoption-ready. High gap = crypto association undermines trust â€” a real adoption blocker.",
                calc_note="Calculated: AI-extracted trust gap, merged across two schema generations this project used â€” legacy matrices store it nested under coindcx_trust.crypto_trust_gap, newer ones flatten coindcx_trust into a trust-level enum (inverted to gap semantics here so both are comparable). Classified low/medium/high (high = bad).",
            )
            _legend_row([
                ("Low",    _C["pos"], "brand trust outweighs crypto concern â€” adoption-ready"),
                ("Medium", _C["amb"], "partial confidence â€” needs proof points"),
                ("High",   _C["neg"], "strong distrust of crypto brand = adoption blocker"),
            ])
            _tg_order = {"low": _C["pos"], "medium": _C["amb"], "high": _C["neg"]}
            _tg_raw = list(trust_gap_counts.keys())
            _tg_colors = [_tg_order.get(str(k).lower(), _C["neu"]) for k in _tg_raw]
            _tg_labels = [_fmt_val(k) for k in _tg_raw]
            # No cross-filter wrapper here: the value is a merged/normalized computation across
            # two schema shapes (_trust_gap_value), not a single dot-path field, so it can't be
            # expressed as a {path: value} entry in active_filters the way every other chart's
            # click-filter is (see _render_header's filter application â€” it does _get(m, path),
            # which has no way to re-run this merge logic). Every other chart on this page is
            # click-filterable; this one is view-only until active_filters supports a value-fn.
            st.plotly_chart(
                _donut(_tg_labels, list(trust_gap_counts.values()),
                       "Trust Gap Level", _tg_colors,
                       resp_groups=list(trust_gap_groups.values())),
                use_container_width=True)
            _chart_caption(
                "Slice = share of respondents at each trust gap level. "
                "Large High slice = messaging must lead with trust restoration before product selling."
            )
    if top_builder:
        top_10_builders = dict(list(top_builder.items())[:10])
        top_10_bld_grps = list(builder_groups.values())[:10]
        with col2:
            _chart_header(
                "What Builds Trust in CoinDCX Karat",
                subtitle="Specific factors respondents cited as trust-building signals â€” the proof points that close the trust gap.",
                how_to_read="Longer bar = more respondents cited that factor. Top factors = leading messaging and feature priorities.",
                calc_note="Calculated: extracted trust-builder mentions, merged across both schema generations (coindcx_trust.trust_builders_cited in legacy matrices, top-level trust_builders in newer ones). The prompt asks the model to extract specific things the respondent said would make them trust the product more â€” e.g. 'SEBI regulation', 'audited reserves', 'brand reputation'. Multiple items per respondent possible.",
            )
            _tb_fmt = {_fmt_val(k): v for k, v in top_10_builders.items()}
            st.plotly_chart(
                _h_bar(list(_tb_fmt.keys()), list(_tb_fmt.values()),
                       "Trust Builders Cited", _C["pos"],
                       resp_groups=top_10_bld_grps),
                use_container_width=True)
            _chart_caption("Bar length = number of respondents who cited that trust signal. Use top 3 factors as proof-point headlines in product comms.")

    driver_counts  = _count_list_field(matrices, "adoption.drivers")
    barrier_counts = _count_list_field(matrices, "adoption.barriers")

    if driver_counts or barrier_counts:
        _chart_header(
            "Adoption Drivers vs Barriers",
            subtitle="What pulls respondents toward adoption (drivers) vs what holds them back (barriers) â€” extracted from IDI responses.",
            how_to_read="Green bars extend right = drivers. Red bars extend left = barriers. Items on both sides = ambivalent factors.",
            calc_note="Calculated: extracted from adoption.drivers (list) and adoption.barriers (list) in each matrix. The prompt instructs the model to extract specific factors the respondent mentioned as reasons they would adopt or not adopt â€” verbatim themes, not inferred. Each mention counted once per respondent.",
        )
        _legend_row([
            ("Drivers", _C["pos"], "factors that pull respondents toward adopting Karat"),
            ("Barriers", _C["neg"], "concerns or blockers that prevent adoption"),
        ])
        st.markdown("#### Adoption Drivers vs Barriers")
        # Align on shared label set for diverging bar
        all_labels = sorted(
            set(list(driver_counts.keys())[:10]) | set(list(barrier_counts.keys())[:10])
        )
        d_vals = [driver_counts.get(lb, 0) for lb in all_labels]
        b_vals = [barrier_counts.get(lb, 0) for lb in all_labels]
        # Only keep rows where at least one side > 0
        rows_to_show = [(lb, d, b) for lb, d, b in zip(all_labels, d_vals, b_vals) if d > 0 or b > 0]
        rows_to_show.sort(key=lambda r: -(r[1] + r[2]))
        rows_to_show = rows_to_show[:14]
        # Preserve raw labels for group lookup before formatting
        raw_labels_shown = [lb for lb, _, _ in rows_to_show]
        rows_to_show = [(_fmt_val(lb), d, b) for lb, d, b in rows_to_show]
        if rows_to_show:
            labels_d, d_final, b_final = zip(*rows_to_show)
            _drv_grps_aligned = [driver_grp_map.get(lb, []) for lb in raw_labels_shown]
            _bar_grps_aligned = [barrier_grp_map.get(lb, []) for lb in raw_labels_shown]
            st.plotly_chart(
                _diverging_bar(
                    list(labels_d), list(d_final), list(b_final),
                    "Drivers", "Barriers",
                    "Drivers vs Barriers (mention count)",
                    h=max(280, len(labels_d) * 28),
                    pos_resp_groups=_drv_grps_aligned,
                    neg_resp_groups=_bar_grps_aligned,
                ),
                use_container_width=True)
            _chart_caption(
                "Green (right) = adoption drivers â€” what pulls respondents toward adoption. "
                "Red (left) = barriers â€” what blocks them. "
                "Wider bars = more mentions. "
                "Items that appear on both sides = ambivalent factors."
            )
        else:
            col3, col4 = st.columns(2)
            if driver_counts:
                with col3:
                    st.plotly_chart(_h_bar(list(driver_counts.keys())[:10],
                                           list(driver_counts.values())[:10],
                                           "Adoption Drivers", _C["pos"],
                                           resp_groups=list(driver_grp_map.values())[:10]),
                                    use_container_width=True)
            if barrier_counts:
                with col4:
                    st.plotly_chart(_h_bar(list(barrier_counts.keys())[:10],
                                           list(barrier_counts.values())[:10],
                                           "Adoption Barriers", _C["neg"],
                                           resp_groups=list(barrier_grp_map.values())[:10]),
                                    use_container_width=True)

    intent_scores = [_get(m, "adoption.intent_score") for m in matrices]
    intent_scores = [int(s) for s in intent_scores if s is not None and isinstance(s, (int, float))]
    if intent_scores:
        intent_dist = Counter(intent_scores)
        sorted_scores = sorted(intent_dist.keys())
        colors_intent = [
            _C["neg"] if s <= 4 else _C["amb"] if s <= 6 else _C["pos"]
            for s in sorted_scores
        ]
        intent_ge7 = sum(intent_dist[s] for s in sorted_scores if s >= 7)
        intent_le4 = sum(intent_dist[s] for s in sorted_scores if s <= 4)
        _intent_score_groups = [
            [m for m in matrices if (_get(m, "adoption.intent_score") or -1) == s]
            for s in sorted_scores
        ]
        _chart_header(
            "Adoption Intent Score Distribution",
            subtitle=f"Self-reported likelihood to adopt CoinDCX Karat, rated 1â€“10 by each respondent. n={n}.",
            how_to_read="Green (7â€“10) = high intent â€” likely early adopters. Amber (5â€“6) = conditional. Red (1â€“4) = resistant.",
            calc_note="Calculated: AI extraction from adoption.intent_score field in each matrix. The extraction prompt asked the model to rate how likely the respondent is to adopt within 6 months (1=would not, 10=would definitely). NPS signal is derived from this: promoter=8â€“10, passive=7, detractorâ‰¤6.",
        )
        _legend_row([
            ("7â€“10 High Intent",   _C["pos"], "ready to adopt â€” activation-focused messaging"),
            ("5â€“6 Conditional",    _C["amb"], "on the fence â€” needs a trigger or proof point"),
            ("1â€“4 Low Intent",     _C["neg"], "resistant â€” unlikely without major trust shift"),
        ])
        fig_intent = go.Figure()
        fig_intent.add_trace(go.Bar(
            x=sorted_scores,
            y=[intent_dist[s] for s in sorted_scores],
            marker=dict(color=colors_intent, line=dict(width=0)),
            text=[str(intent_dist[s]) for s in sorted_scores],
            textposition="outside",
            textfont=dict(size=10, color=_C["muted"]),
            customdata=[_resp_tooltip_html(_intent_score_groups[i], f"Score {sorted_scores[i]}")
                        for i in range(len(sorted_scores))],
            hovertemplate="%{customdata}<extra></extra>",
        ))
        # Zone annotations
        for zone_x, zone_label, zone_col in [
            (2.5, "Low Intent", _C["neg"]),
            (5.5, "Conditional", _C["amb"]),
            (8.5, "High Intent", _C["pos"]),
        ]:
            fig_intent.add_annotation(
                x=zone_x, y=0, yref="paper", yanchor="bottom",
                text=zone_label, showarrow=False,
                font=dict(size=8, color=zone_col),
                opacity=0.55,
            )
        fig_intent.update_layout(
            **{**_CHART_BASE,
               "height": 230,
               "margin": dict(l=40, r=20, t=30, b=50),
               "xaxis": dict(tickmode="linear", dtick=1, **_NO_GRID, tickfont=dict(size=10)),
               "yaxis": dict(**_GRID_STYLE, tickfont=dict(size=9)),
               },
        )
        st.plotly_chart(fig_intent, use_container_width=True)
        _chart_caption(
            f"Intent to adopt (1â€“10). "
            f"Green (7â€“10) = {intent_ge7} respondents ({_pct_str(intent_ge7, n)}). "
            f"Red (1â€“4) = {intent_le4} respondents ({_pct_str(intent_le4, n)}). "
            "Amber (5â€“6) = conditional adopters."
        )

    # Spontaneous brand associations â€” merged across both schema generations (see
    # _platform_association_value near _group_list_field).
    assoc_quotes = [
        {
            "content":  _platform_association_value(m),
            "segment":  str(_get(m, "respondent.segment") or ""),
            "city":     str(_get(m, "respondent.city") or ""),
            "doc_id":   str(m.get("doc_id", "")),
            "sentiment": "neutral", "pain_point": False, "decision_signal": False,
        }
        for m in matrices
        if len(_platform_association_value(m)) > 10
    ]
    if assoc_quotes:
        st.markdown("#### Spontaneous Brand Associations")
        for q in assoc_quotes[:6]:
            seg_c = _seg_color(q["segment"])
            st.markdown(
                f'<div style="border-left:3px solid {seg_c};padding:8px 14px;margin:5px 0;'
                f'background:{seg_c}08;border-radius:0 6px 6px 0;">'
                f'<span style="font-size:0.63rem;color:{_C["muted"]};">'
                f'{_esc(q["segment"])} Â· {_esc(q["city"])} Â· {_esc(q["doc_id"])}'
                f'</span></div>',
                unsafe_allow_html=True)
            st.markdown(q["content"])

    st.markdown("---")
    _render_auto_charts(matrices, EXCLUDE_AUTO_CHART, include_prefixes=CATEGORY_PREFIXES["brand_trust"],
                        key_prefix="trust")

    st.markdown("---")
    st.markdown("#### Verbatim Evidence â€” Trust & Adoption")
    _verbatim_wall(
        _get_passages(matrices, topics=[
            "trust", "brand_trust", "CoinDCX crypto association",
            "adoption", "platform", "platform_risk",
            "custody_risk", "custody and audit anxiety",
        ]),
        "trust_vb",
    )

    st.markdown("---")
    st.markdown("#### AI Research Finding")
    _ai_finding_robust(
        "brand_trust", findings_dir, call_or, ctx, active_filters,
        "Synthesize brand trust dynamics, trust gap patterns, and the specific drivers/barriers to adoption. "
        "What is the trust deficit and what proof points would most efficiently close it?",
        "trust_regen", proj_name,
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# PER-RESPONDENT ANALYSIS TAB
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _render_respondent_card(m: dict, idx: int) -> None:
    """Render full detail card for a single respondent inside an expander."""
    rid   = str(_get(m, "doc_id") or _get(m, "respondent.id") or f"R{idx+1:02d}")
    seg   = _fmt_val(_get(m, "respondent.segment") or "â€”")
    city  = _fmt_val(_get(m, "respondent.city") or "â€”")
    age   = _fmt_val(_get(m, "respondent.age_band") or "â€”")
    gen   = _fmt_val(_get(m, "respondent.gender") or "â€”")
    occ   = _fmt_val(_get(m, "respondent.occupation") or "â€”")

    comp_s  = _get(m, "concept_understanding.comprehension_score")
    intent  = _get(m, "adoption.intent_score")
    nps     = _fmt_val(m.get("nps_signal") or "â€”")
    pref_r  = _fmt_val(_get(m, "preferred_route") or "â€”")
    anxiety = _fmt_val(_get(m, "financial_anxiety_level") or "â€”")
    arch    = _fmt_val(_get(m, "investor_archetype") or "â€”")
    stage   = _fmt_val(_get(m, "life_stage") or "â€”")
    emo_res = _fmt_val(m.get("emotional_resolution") or "â€”")
    route_shown = _fmt_val(_get(m, "concept_understanding.route_shown") or "â€”")

    # Header KPIs
    kc = st.columns(6)
    _kpi(kc[0], "Comprehension", f"{comp_s}/10" if comp_s is not None else "â€”", _C["r1"])
    _kpi(kc[1], "Intent Score",  f"{intent}/10" if intent is not None else "â€”",  _C["pos"])
    _kpi(kc[2], "NPS Signal",    nps,   _C["pos"] if nps.lower() == "promoter" else _C["neg"] if nps.lower() == "detractor" else _C["neu"])
    _kpi(kc[3], "Pref Route",    pref_r, _C["r1"])
    _kpi(kc[4], "Fin Anxiety",   anxiety, _C["neg"] if anxiety.lower() == "high" else _C["amb"] if anxiety.lower() == "medium" else _C["pos"])
    _kpi(kc[5], "Emo Resolution",emo_res, _C["pos"] if emo_res.lower() == "positive" else _C["neg"])

    st.markdown("---")

    # Respondent profile
    pc1, pc2, pc3, pc4 = st.columns(4)
    pc1.markdown(f"**City** &nbsp; {_esc(city)}", unsafe_allow_html=True)
    pc2.markdown(f"**Age band** &nbsp; {_esc(age)}", unsafe_allow_html=True)
    pc3.markdown(f"**Occupation** &nbsp; {_esc(occ)}", unsafe_allow_html=True)
    pc4.markdown(f"**Life Stage** &nbsp; {_esc(stage)}", unsafe_allow_html=True)
    ic1, ic2, ic3 = st.columns(3)
    ic1.markdown(f"**Investor Archetype** &nbsp; {_esc(arch)}", unsafe_allow_html=True)
    ic2.markdown(f"**Route Shown** &nbsp; {_esc(route_shown)}", unsafe_allow_html=True)
    ic3.markdown(f"**Gender** &nbsp; {_esc(gen)}", unsafe_allow_html=True)

    st.markdown("---")

    # Route evaluations side-by-side
    r1_eval = _get(m, "route1_evaluation") or {}
    r2_eval = _get(m, "route2_evaluation") or {}
    if r1_eval or r2_eval:
        st.markdown("**Route Evaluations**")
        re1, re2 = st.columns(2)
        if r1_eval and isinstance(r1_eval, dict):
            with re1:
                r1_col = _C["r1"]
                st.markdown(f"<span style='color:{r1_col};font-weight:700;'>Route 1</span>", unsafe_allow_html=True)
                for fk, fv in r1_eval.items():
                    if fv is not None and str(fv).strip() not in ("", "None"):
                        label = fk.replace("_", " ").title()
                        st.markdown(f"- **{label}**: {_fmt_val(fv)}")
        if r2_eval and isinstance(r2_eval, dict):
            with re2:
                r2_col = _C["r2"]
                st.markdown(f"<span style='color:{r2_col};font-weight:700;'>Route 2</span>", unsafe_allow_html=True)
                for fk, fv in r2_eval.items():
                    if fv is not None and str(fv).strip() not in ("", "None"):
                        label = fk.replace("_", " ").title()
                        st.markdown(f"- **{label}**: {_fmt_val(fv)}")
        st.markdown("---")

    # Key claim reactions
    claims = m.get("key_claim_reactions") or []
    if claims:
        st.markdown("**Key Claim Reactions**")
        import pandas as pd
        _claim_rows = []
        for cr in claims:
            if isinstance(cr, dict):
                _claim_rows.append({
                    "Claim":      str(cr.get("claim") or "â€”")[:80],
                    "Reaction":   _fmt_val(cr.get("reaction") or "â€”"),
                    "Understood": "âœ“" if cr.get("understood") else "âœ—",
                    "Verbatim":   str(cr.get("verbatim") or "â€”")[:120],
                })
        if _claim_rows:
            st.dataframe(pd.DataFrame(_claim_rows), use_container_width=True, hide_index=True)
        st.markdown("---")

    # Trust & adoption
    trust = _get(m, "coindcx_trust") or {}
    adopt = _get(m, "adoption") or {}
    if trust or adopt:
        st.markdown("**Trust & Adoption**")
        ta1, ta2 = st.columns(2)
        if trust and isinstance(trust, dict):
            with ta1:
                trust_col = _C["seg_st"]
                st.markdown(f"<span style='color:{trust_col};font-weight:700;'>Trust</span>", unsafe_allow_html=True)
                for fk, fv in trust.items():
                    if fv is not None and str(fv).strip() not in ("", "None", "[]"):
                        label = fk.replace("_", " ").title()
                        val_str = ", ".join(fv) if isinstance(fv, list) else _fmt_val(fv)
                        st.markdown(f"- **{label}**: {_esc(val_str)}", unsafe_allow_html=True)
        if adopt and isinstance(adopt, dict):
            with ta2:
                adopt_col = _C["pos"]
                st.markdown(f"<span style='color:{adopt_col};font-weight:700;'>Adoption</span>", unsafe_allow_html=True)
                for fk, fv in adopt.items():
                    if fv is not None and str(fv).strip() not in ("", "None", "[]"):
                        label = fk.replace("_", " ").title()
                        val_str = ", ".join(fv) if isinstance(fv, list) else _fmt_val(fv)
                        st.markdown(f"- **{label}**: {_esc(val_str)}", unsafe_allow_html=True)
        st.markdown("---")

    # Gold behavior (CoinDCX-specific, graceful skip if absent)
    gold = _get(m, "gold_behavior") or {}
    portfolio = _get(m, "portfolio_behavior") or {}
    if gold or portfolio:
        st.markdown("**Gold & Portfolio Behavior**")
        gb1, gb2 = st.columns(2)
        if gold and isinstance(gold, dict):
            with gb1:
                st.markdown("**Gold Behavior**")
                for fk, fv in gold.items():
                    if fv is not None and str(fv).strip() not in ("", "None", "[]"):
                        label = fk.replace("_", " ").title()
                        val_str = ", ".join(str(x) for x in fv) if isinstance(fv, list) else _fmt_val(fv)
                        st.markdown(f"- **{label}**: {_esc(val_str)}", unsafe_allow_html=True)
        if portfolio and isinstance(portfolio, dict):
            with gb2:
                st.markdown("**Portfolio Behavior**")
                for fk, fv in portfolio.items():
                    if fv is None or str(fv).strip() in ("", "None", "[]"):
                        continue
                    label = fk.replace("_", " ").title()
                    if isinstance(fv, dict):
                        sub = ", ".join(f"{k}={v}" for k, v in fv.items() if v is not None)
                        st.markdown(f"- **{label}**: {_esc(sub)}", unsafe_allow_html=True)
                    elif isinstance(fv, list):
                        st.markdown(f"- **{label}**: {_esc(', '.join(str(x) for x in fv))}", unsafe_allow_html=True)
                    else:
                        st.markdown(f"- **{label}**: {_esc(_fmt_val(fv))}", unsafe_allow_html=True)
        st.markdown("---")

    # Passages / verbatims â€” use _get_passages([m]) so metadata (segment, city, doc_id)
    # is merged in, matching the format _verbatim_wall expects
    passages = _get_passages([m])
    if passages:
        st.markdown("**Verbatim Passages**")
        _verbatim_wall(passages, key_prefix=f"pr_{rid}_vb")

    # Pain points
    pain_pts = m.get("pain_points") or []
    if pain_pts:
        st.markdown("**Pain Points**")
        for pp in pain_pts:
            if isinstance(pp, dict):
                area = _fmt_val(pp.get("product_area") or pp.get("area") or pp.get("category") or "")
                desc = str(pp.get("issue_description") or pp.get("description") or "").strip()
                quote = str(pp.get("verbatim_quote") or pp.get("verbatim") or "").strip()
                sev  = str(pp.get("severity") or "").lower()
            else:
                area, desc, quote, sev = "", str(pp), "", ""
            sev_col = _C["neg"] if sev == "high" else _C["amb"] if sev == "medium" else _C["neu"]
            badge = f'<span style="font-size:0.6rem;color:{sev_col};font-weight:700;">{_esc(sev.upper()) if sev else ""}</span> '
            label = f"<b>{_esc(area)}</b> â€” " if area else ""
            quote_html = (f'<div style="font-size:0.78rem;color:#666;font-style:italic;'
                          f'margin-top:4px;padding-left:8px;border-left:2px solid {sev_col}88;">'
                          f'"{_esc(quote)}"</div>') if quote else ""
            st.markdown(
                f'<div style="border-left:3px solid {sev_col};padding:6px 10px;margin:4px 0;'
                f'background:{sev_col}10;border-radius:0 5px 5px 0;">'
                f'{badge}{label}<span style="font-size:0.82rem;">{_esc(desc)}</span>'
                f'{quote_html}</div>',
                unsafe_allow_html=True,
            )


def _render_per_respondent(matrices: list[dict], all_matrices: list[dict]) -> None:
    """Per-respondent deep-dive tab with broad filters + transcript search."""
    _section_header(
        "Per-Respondent Analysis",
        "Drill down into each individual interview â€” all extracted matrices + verbatims.",
        len(matrices), _C["accent"],
    )

    # â”€â”€ Broad filter row â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    segs   = sorted({str(_get(m, "respondent.segment") or "") for m in all_matrices if _get(m, "respondent.segment")})
    cities = sorted({str(_get(m, "respondent.city") or "") for m in all_matrices if _get(m, "respondent.city")})
    npss   = sorted({str(m.get("nps_signal") or "") for m in all_matrices if m.get("nps_signal")})
    doc_ids = sorted({str(m.get("doc_id") or "") for m in all_matrices if m.get("doc_id")})

    fc1, fc2, fc3, fc4 = st.columns([2, 2, 1, 2])
    f_seg    = fc1.multiselect("Segment", segs, key="pr_seg", placeholder="All segments")
    f_city   = fc2.multiselect("City", cities, key="pr_city", placeholder="All cities")
    f_nps    = fc3.selectbox("NPS", ["All"] + npss, key="pr_nps")
    f_search = fc4.text_input("Search transcript name (doc_id)", key="pr_search",
                               placeholder="Type to filterâ€¦").strip().lower()

    # â”€â”€ Apply filters â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    filtered = matrices  # start from already-filtered (global) set
    if f_seg:
        filtered = [m for m in filtered if str(_get(m, "respondent.segment") or "") in f_seg]
    if f_city:
        filtered = [m for m in filtered if str(_get(m, "respondent.city") or "") in f_city]
    if f_nps != "All":
        filtered = [m for m in filtered if str(m.get("nps_signal") or "").lower() == f_nps.lower()]
    if f_search:
        filtered = [m for m in filtered
                    if f_search in str(m.get("doc_id") or "").lower()
                    or f_search in str(_get(m, "respondent.id") or "").lower()]

    st.caption(f"{len(filtered)} respondent(s) match filters")

    if not filtered:
        st.info("No respondents match current filters.")
        return

    # â”€â”€ Summary quick table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    import pandas as pd
    summary_rows = []
    for i, m in enumerate(filtered):
        rid   = str(m.get("doc_id") or _get(m, "respondent.id") or f"R{i+1:02d}")
        summary_rows.append({
            "Transcript": rid,
            "Segment":    str(_get(m, "respondent.segment") or "â€”"),
            "City":       str(_get(m, "respondent.city") or "â€”"),
            "Age":        str(_get(m, "respondent.age_band") or "â€”"),
            "Gender":     str(_get(m, "respondent.gender") or "â€”"),
            "Comp":       _get(m, "concept_understanding.comprehension_score"),
            "Intent":     _get(m, "adoption.intent_score"),
            "NPS":        str(m.get("nps_signal") or "â€”"),
            "Pref Route": _fmt_val(_get(m, "preferred_route") or "â€”"),
            "Anxiety":    _fmt_val(_get(m, "financial_anxiety_level") or "â€”"),
        })
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)
    st.markdown("---")

    # â”€â”€ Per-respondent expanders â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for i, m in enumerate(filtered):
        rid  = str(m.get("doc_id") or _get(m, "respondent.id") or f"R{i+1:02d}")
        seg  = str(_get(m, "respondent.segment") or "â€”")
        city = str(_get(m, "respondent.city") or "â€”")
        comp_s = _get(m, "concept_understanding.comprehension_score")
        intent = _get(m, "adoption.intent_score")
        nps    = str(m.get("nps_signal") or "â€”")
        nps_icon = "ðŸŸ¢" if nps.lower() == "promoter" else "ðŸ”´" if nps.lower() == "detractor" else "ðŸŸ¡"
        label = (
            f"{nps_icon} **{rid}** &nbsp;Â·&nbsp; {seg} &nbsp;Â·&nbsp; {city}"
            f" &nbsp;Â·&nbsp; Comp: {comp_s}/10" if comp_s is not None else
            f"{nps_icon} **{rid}** &nbsp;Â·&nbsp; {seg} &nbsp;Â·&nbsp; {city}"
        )
        with st.expander(f"{nps_icon} {rid}  Â·  {seg}  Â·  {city}  Â·  intent {intent}/10" if intent is not None else f"{nps_icon} {rid}  Â·  {seg}  Â·  {city}", expanded=False):
            _render_respondent_card(m, i)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# STUDY REPORT TAB
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _render_study_report(matrices: list[dict], findings_dir: str, call_or: Callable,
                         active_filters: dict, proj_name: str) -> None:
    n = len(matrices)
    segs = sorted({str(_get(m, "respondent.segment") or "") for m in matrices
                   if _get(m, "respondent.segment")})
    cities = sorted({str(_get(m, "respondent.city") or "") for m in matrices
                     if _get(m, "respondent.city")})

    # Top-level study stats for report context
    avg_comp   = _avg([_get(m, "concept_understanding.comprehension_score") for m in matrices])
    avg_intent = _avg([_get(m, "adoption.intent_score") for m in matrices])
    promo      = sum(1 for m in matrices if (m.get("nps_signal") or "").lower() == "promoter")
    r1_pref    = sum(1 for m in matrices
                     if "1" in str(_get(m, "preferred_route") or "").lower()
                     or "safety" in str(_get(m, "preferred_route") or "").lower())
    top_barrier = _count_list_field(matrices, "adoption.barriers")
    top_bar_str = max(top_barrier, key=top_barrier.get) if top_barrier else "â€”"
    top_builder = _count_trust_builders(matrices)
    top_bld_str = max(top_builder, key=top_builder.get) if top_builder else "â€”"

    filter_note = _fmt_filter_ctx(active_filters, n)

    _section_header("Study Report", f"Executive synthesis and full verbatim archive. Scope: {filter_note}.",
                    n, _C["accent"])

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"""
**Study:** {proj_name}
**Scope:** {n} depth interviews Â· {len(segs)} segments Â· {len(cities)} cities
**Segments:** {', '.join(segs) if segs else 'â€”'}
**Cities:** {', '.join(cities) if cities else 'â€”'}
**Active filter:** {filter_note}
""")
    with col2:
        kpi_stats = {
            "Avg Comprehension": f"{avg_comp:.1f}/10" if avg_comp else "â€”",
            "Avg Intent": f"{avg_intent:.1f}/10" if avg_intent else "â€”",
            "NPS Promoters": f"{promo}/{n}",
            "R1 Preferred": _pct_str(r1_pref, n),
            "Top Barrier": _fmt_val(top_bar_str)[:25] if top_bar_str != "â€”" else "â€”",
            "Top Trust Builder": _fmt_val(top_bld_str)[:25] if top_bld_str != "â€”" else "â€”",
        }
        for k, v in kpi_stats.items():
            if v and v != "â€”":
                st.markdown(f"**{k}:** {v}")

    st.markdown("---")

    finding = _load_finding(findings_dir, "full_report")
    if finding.get("finding_text"):
        st.markdown(finding["finding_text"])
    else:
        st.info("Full executive report not yet generated. Use the button below.")

    if st.button("â†º Generate Executive Report", key="report_regen", type="primary"):
        with st.spinner("Generating comprehensive reportâ€¦"):
            prompt = (
                f"You are a senior market researcher writing an executive study report.\n\n"
                f"STUDY: {proj_name}\n"
                f"RESPONDENTS: {n} depth interviews\n"
                f"SEGMENTS: {', '.join(segs)}\n"
                f"CITIES: {', '.join(cities)}\n"
                f"ACTIVE FILTER: {filter_note}\n\n"
                f"KEY STUDY DATA:\n"
                f"  â€¢ Avg comprehension score: {f'{avg_comp:.1f}/10' if avg_comp else 'not reported'}\n"
                f"  â€¢ Avg adoption intent: {f'{avg_intent:.1f}/10' if avg_intent else 'not reported'}\n"
                f"  â€¢ NPS promoters: {promo}/{n}\n"
                f"  â€¢ Route 1 preferred: {_pct_str(r1_pref, n)}\n"
                f"  â€¢ Top adoption barrier: {top_bar_str}\n"
                f"  â€¢ Top trust builder: {top_bld_str}\n\n"
                f"Write a professional executive research report (600-900 words) with sections:\n"
                f"1. EXECUTIVE SUMMARY (3-4 sentences)\n"
                f"2. KEY FINDINGS â€” Comprehension & Concept Clarity\n"
                f"3. KEY FINDINGS â€” Route Preference & Emotional Response\n"
                f"4. KEY FINDINGS â€” Trust Dynamics & Adoption Barriers\n"
                f"5. STRATEGIC RECOMMENDATIONS (3-5 actionable bullets)\n\n"
                f"Ground all claims in the data above. Be specific. Do not invent numbers."
            )
            r = call_or(prompt)
            if r:
                st.markdown(r)
                if st.button("Save report", key="report_save"):
                    try:
                        p = Path(findings_dir) / "full_report.json"
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text(json.dumps({"finding_text": r}, ensure_ascii=False),
                                     encoding="utf-8")
                        st.success("Saved.")
                    except Exception as e:
                        st.error(f"Save failed: {e}")
            else:
                st.warning("No response from OpenRouter.")

    st.markdown("---")
    st.markdown("#### Full Verbatim Archive")
    _verbatim_wall(_get_passages(matrices), "report_vb", "All Passages (Full Study)")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CONFIG-DRIVEN SECTION RENDERERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _compute_kpi_value(kpi_spec: dict, matrices: list[dict]) -> str:
    n = len(matrices)
    vtype = kpi_spec.get("value_type", "avg_score")
    field = kpi_spec.get("field", "")
    suffix = kpi_spec.get("suffix", "")
    # `_get(m, field) or ""` (used below) silently turns a legitimate False/0 value into "" â€”
    # found live: route2_evaluation.yield_too_good_to_be_true is a real boolean field, and
    # `eq_val: "False"` could never match because `False or ""` evaluates to "". Use this instead
    # wherever the raw value (not just truthy values) needs to be stringified for comparison.
    def _str_val(m):
        v = _get(m, field)
        return str(v) if v is not None else ""
    if vtype == "total_n":
        return str(n)
    if vtype == "avg_score":
        avg = _avg([_get(m, field) for m in matrices])
        return f"{avg:.1f}{suffix}" if avg is not None else "â€”"
    elif vtype == "count_eq":
        eq_val = str(kpi_spec.get("eq_val", ""))
        cnt = sum(1 for m in matrices if _str_val(m).lower() == eq_val.lower())
        return f"{cnt}/{n} ({_pct_str(cnt, n)})"
    elif vtype == "count_contains":
        cval = str(kpi_spec.get("contains_val", ""))
        cnt = sum(1 for m in matrices if cval.lower() in _str_val(m).lower())
        return f"{cnt}/{n} ({_pct_str(cnt, n)})"
    elif vtype == "count_high":
        cnt = sum(1 for m in matrices if _str_val(m).lower() == "high")
        return f"{cnt}/{n} ({_pct_str(cnt, n)})"
    elif vtype == "count_any":
        cnt = sum(1 for m in matrices
                  if _get(m, field) not in (None, "", [], "null", "none", "â€”"))
        return f"{cnt}/{n} ({_pct_str(cnt, n)})"
    elif vtype == "r1_r2_split":
        r1 = sum(1 for m in matrices
                 if "1" in str(_get(m, field) or "").lower()
                 or "safety" in str(_get(m, field) or "").lower())
        r2 = sum(1 for m in matrices
                 if "2" in str(_get(m, field) or "").lower()
                 or "return" in str(_get(m, field) or "").lower())
        return f"R1:{r1} / R2:{r2}"
    return "â€”"


def _resolve_color(c: str) -> str:
    _MAP = {
        "blue": _C["accent"], "indigo": _C["accent"],
        "green": _C["pos"],   "emerald": _C["pos"],
        "red": _C["neg"],     "pink": _C["neg"],
        "purple": _C["seg_fd"],
        "amber": _C["amb"],   "yellow": _C["amb"],
        "sky": _C["r2"],      "teal": _C["seg_st"],
        "gray": _C["neu"],    "muted": _C["muted"],
        "r1": _C["r1"],       "r2": _C["r2"],
    }
    if c and c.startswith("#"): return c
    return _MAP.get(str(c).lower(), _C["accent"])


def _sec_section_header(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    color = _resolve_color(sec.get("color", "blue"))
    _section_header(sec.get("title", ""), sec.get("description", ""), len(matrices), color)
    banner = sec.get("banner")
    if banner and banner.get("headline"):
        _insight_banner(banner["headline"], banner.get("subtext", ""), color)


def _sec_kpi_row(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    kpis = sec.get("kpis", [])
    if not kpis: return
    cols = st.columns(len(kpis))
    for i, kpi in enumerate(kpis):
        _kpi(cols[i], kpi.get("label", ""), _compute_kpi_value(kpi, matrices),
             _resolve_color(kpi.get("color", "blue")))


def _sec_distribution(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    field    = sec.get("field", "")
    title    = sec.get("label") or sec.get("title") or _fmt_val(field.split(".")[-1])
    color    = _resolve_color(sec.get("color", "blue"))
    chart    = sec.get("chart", "h_bar")
    limit    = sec.get("limit", 20)
    is_list  = sec.get("list_field", False)
    subtitle = sec.get("subtitle", "")
    how_to   = sec.get("how_to_read", "")
    calc     = sec.get("calc_note", "")
    legend   = sec.get("legend", [])
    caption  = sec.get("caption", "")
    counts = _count_list_field(matrices, field) if is_list else _count_field(matrices, field)
    groups = _group_list_field(matrices, field) if is_list else _group_field(matrices, field)
    with st.container(border=True):
        # â”€â”€ 1. Title â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        st.markdown(
            f'<div style="font-size:1.05rem;font-weight:800;color:#111827;'
            f'margin:2px 0 3px 0;letter-spacing:-0.01em;">{_esc(title)}</div>',
            unsafe_allow_html=True,
        )
        if not counts:
            st.caption(f"No data for {title}"); return
        # â”€â”€ 2. Critical one-liner, always visible â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if caption:
            st.markdown(
                f'<div style="font-size:0.82rem;color:#111827;line-height:1.5;'
                f'margin-bottom:8px;">{_esc(caption)}</div>',
                unsafe_allow_html=True,
            )
        # â”€â”€ 3. Dropdown: definitions + how to read â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        has_context = subtitle or how_to or calc or legend
        if has_context:
            exp_key = f"about_dist_{field.replace('.', '_')}"
            exp_label = "ðŸ”Ž Who counts as what â€” definitions & how to read" if legend else "ðŸ”Ž How to read this chart"
            with st.expander(exp_label, expanded=False, key=exp_key):
                if subtitle:
                    st.markdown(f"**What it shows:** {subtitle}")
                if how_to:
                    st.markdown(f"**How to read:** {how_to}")
                if calc:
                    st.markdown(f"**How AI calculates this:** {calc}")
                if legend:
                    st.markdown("**Category guide â€” what each value means:**")
                    for item in legend:
                        name, col_raw, desc = _legend_item_parts(item)
                        col_hex = _resolve_color(col_raw)
                        st.markdown(
                            f'<div style="display:flex;gap:10px;margin:8px 0;'
                            f'padding:8px 10px;background:#f9fafb;border-radius:6px;'
                            f'border-left:3px solid {col_hex};">'
                            f'<div style="min-width:110px;font-size:0.8rem;font-weight:700;'
                            f'color:#111827;padding-top:1px;">{_esc(name)}</div>'
                            f'<div style="font-size:0.78rem;color:#374151;line-height:1.55;">'
                            f'{_esc(desc)}</div></div>',
                            unsafe_allow_html=True,
                        )
        # â”€â”€ 4. Chart â€” internal title left blank, header above already shows it â”€â”€
        lbls_raw = list(counts.keys())[:limit]
        vals     = [counts[l] for l in lbls_raw]
        rgs      = [groups.get(l, []) for l in lbls_raw]
        lbls     = [_fmt_val(l) for l in lbls_raw]
        if chart == "donut":
            fig = _donut(lbls, vals, "", resp_groups=rgs)
        elif chart == "v_bar":
            fig = _v_bar(lbls, vals, "", color, resp_groups=rgs)
        else:
            fig = _h_bar(lbls, vals, "", color, resp_groups=rgs)
        st.plotly_chart(fig, use_container_width=True)


def _legend_item_parts(item) -> tuple[str, str, str]:
    """Legend items come in two shapes depending on source: legacy hand-authored configs use a
    positional [label, color, description] list; the schema-driven generator's Pydantic
    LegendItem serializes as a {"label", "color", "description"} dict â€” found live, this crashed
    the whole section (`item[0]` on a dict raises KeyError) the first time a generated config
    with a legend actually rendered. Handle both without requiring either side to change shape."""
    if isinstance(item, dict):
        return item.get("label", ""), item.get("color", "blue"), item.get("description", "")
    return (item[0], item[1] if len(item) > 1 else "blue", item[2] if len(item) > 2 else "")


def _sec_multi_distribution(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    fields = sec.get("fields", [])
    if not fields: return

    # Optional in-tab theme filter â€” schema_generator assigns each field a short "group" (sub-
    # theme within this tab, e.g. "Demographics" vs "Investment Behavior & Risk" inside
    # Respondent Profiles) so a crowded tab can be narrowed instead of showing every field flat.
    # Fields with no group (e.g. deterministically backstop-appended ones) bucket into "Other"
    # so nothing silently disappears when a specific theme is selected. Only shown when there's
    # actually more than one real bucket to choose between.
    _groups_present = sorted({(f.get("group") or "").strip() for f in fields} - {""})
    _has_ungrouped = any(not (f.get("group") or "").strip() for f in fields)
    _group_options = ["All"] + _groups_present + (["Other"] if _has_ungrouped else [])
    if len(_group_options) > 2:
        import hashlib
        _key_material = "|".join(f.get("field", "") for f in fields)
        _filter_key = "mdflt_" + hashlib.md5(_key_material.encode("utf-8")).hexdigest()[:10]
        _sel_group = st.radio(
            "Filter by theme", _group_options, horizontal=True, key=_filter_key,
            label_visibility="collapsed",
        )
        if _sel_group != "All":
            _target = "" if _sel_group == "Other" else _sel_group
            fields = [f for f in fields if (f.get("group") or "").strip() == _target]
        if not fields:
            st.caption("No fields in this theme."); return

    n_cols = sec.get("columns", len(fields))
    cols = st.columns(n_cols)
    for i, fdef in enumerate(fields):
        field    = fdef.get("field", "")
        # "label" is what this renderer displays; config authors more naturally write "title" â€”
        # found live: the schema-driven generator populated "title" on every field and left
        # "label" empty, so every chart silently fell through to the raw-field-name fallback
        # instead of the real heading. Prefer label -> title -> raw fallback.
        label    = fdef.get("label") or fdef.get("title") or _fmt_val(field.split(".")[-1])
        color    = _resolve_color(fdef.get("color", "blue"))
        chart    = fdef.get("chart", "donut")
        limit    = fdef.get("limit", 20)
        is_list  = fdef.get("list_field", False)
        subtitle = fdef.get("subtitle", "")
        how_to   = fdef.get("how_to_read", "")
        calc     = fdef.get("calc_note", "")
        legend   = fdef.get("legend", [])
        caption  = fdef.get("caption", "")
        counts   = _count_list_field(matrices, field) if is_list else _count_field(matrices, field)
        groups   = _group_list_field(matrices, field) if is_list else _group_field(matrices, field)
        with cols[i % n_cols]:
            with st.container(border=True):
                # â”€â”€ 1. Title â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                st.markdown(
                    f'<div style="border-left:3px solid {color};padding-left:8px;'
                    f'margin:2px 0 6px 0;">'
                    f'<div style="font-size:0.92rem;font-weight:800;color:#111827;'
                    f'letter-spacing:-0.01em;">{_esc(label)}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if not counts:
                    st.caption(f"No data for {label}"); continue
                # â”€â”€ 2. Critical one-liner, always visible â€” not buried in a dropdown â”€â”€
                if caption:
                    st.markdown(
                        f'<div style="font-size:0.8rem;color:#111827;line-height:1.5;'
                        f'margin-bottom:8px;">{_esc(caption)}</div>',
                        unsafe_allow_html=True,
                    )
                # â”€â”€ 3. Dropdown: what each segment/value actually means + how to read â”€â”€
                has_context = subtitle or how_to or calc or legend
                if has_context:
                    exp_key = f"about_{field.replace('.', '_')}_{i}"
                    exp_label = "ðŸ”Ž Who counts as what â€” definitions & how to read" if legend else "ðŸ”Ž How to read this chart"
                    with st.expander(exp_label, expanded=False, key=exp_key):
                        if subtitle:
                            st.markdown(f"**What it shows:** {subtitle}")
                        if how_to:
                            st.markdown(f"**How to read:** {how_to}")
                        if calc:
                            st.markdown(f"**How AI calculates this:** {calc}")
                        if legend:
                            st.markdown("**Category guide â€” what each value means:**")
                            for item in legend:
                                name, col_raw, desc = _legend_item_parts(item)
                                col_hex = _resolve_color(col_raw)
                                st.markdown(
                                    f'<div style="display:flex;gap:10px;margin:8px 0;'
                                    f'padding:8px 10px;background:#f9fafb;border-radius:6px;'
                                    f'border-left:3px solid {col_hex};">'
                                    f'<div style="min-width:110px;font-size:0.8rem;font-weight:700;'
                                    f'color:#111827;padding-top:1px;">{_esc(name)}</div>'
                                    f'<div style="font-size:0.78rem;color:#374151;line-height:1.55;">'
                                    f'{_esc(desc)}</div></div>',
                                    unsafe_allow_html=True,
                                )
                # â”€â”€ 4. Chart â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                lbls_raw = list(counts.keys())[:limit]
                vals = [counts[l] for l in lbls_raw]
                lbls = [_fmt_val(l) for l in lbls_raw]
                rgs  = [groups.get(l, []) for l in lbls_raw]
                # Build color map from legend config so chart colors == pill colors â€”
                # look up by raw value first, then _fmt_val match, then label match.
                leg_color_map: dict[str, str] = {}
                if legend:
                    for item in legend:
                        name, col_raw, _desc = _legend_item_parts(item)
                        col = _resolve_color(col_raw)
                        leg_color_map[name.lower()] = col
                        leg_color_map[name.lower().replace(" ", "_")] = col
                def _pick_color(raw: str) -> str:
                    raw_l = raw.lower()
                    fmt_l = _fmt_val(raw).lower()
                    return (leg_color_map.get(raw_l)
                            or leg_color_map.get(fmt_l)
                            or color)
                chart_colors = [_pick_color(r) for r in lbls_raw] if leg_color_map else None
                # Chart title left blank â€” the header above already shows it; passing `label`
                # here too rendered Plotly's own built-in title on top of it (double title,
                # found live).
                if chart == "h_bar":
                    fig = _h_bar(lbls, vals, "", color, h=300, resp_groups=rgs)
                elif chart == "v_bar":
                    fig = _v_bar(lbls, vals, "", color, h=300, resp_groups=rgs)
                else:
                    fig = _donut(lbls, vals, "", colors=chart_colors, resp_groups=rgs, h=300)
                # Click-to-filter enabled on every tab â€” the previous restriction to just the
                # respondent_profiles tab was a leftover from before the schema-driven engine
                # covered all 5 tabs, and left every other tab's charts non-clickable.
                _chart_click_filter(
                    fig, key=f"ctcf_{field.replace('.', '_')}_{i}", lbls_raw=lbls_raw, field=field,
                    enabled=True,
                )
                # Legend renders once, inside the dropdown above â€” a second compact strip here
                # was pure duplication (found live: "there are two legends every time").


def _sec_score_distribution(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    field    = sec.get("field", "")
    title    = sec.get("title", _fmt_val(field.split(".")[-1]))
    color    = _resolve_color(sec.get("color", "blue"))
    max_v    = sec.get("max", 10)
    bands    = sec.get("bands", [])
    subtitle = sec.get("subtitle", "")
    header_html = (
        f'<div style="font-size:1.05rem;font-weight:800;color:#111827;'
        f'margin:18px 0 3px 0;letter-spacing:-0.01em;">{_esc(title)}</div>'
    )
    if subtitle:
        header_html += (
            f'<div style="font-size:0.8rem;color:#6b7280;margin-bottom:6px;'
            f'line-height:1.45;">{_esc(subtitle)}</div>'
        )
    st.markdown(header_html, unsafe_allow_html=True)
    if sec.get("legend"):
        resolved = [_legend_item_parts(item) for item in sec["legend"]]
        _legend_row(resolved)
    nums = [float(v) for m in matrices
            for v in [_get(m, field)] if v is not None and isinstance(v, (int, float))]
    if not nums:
        st.caption(f"No numeric data for {title}"); return
    avg_v = sum(nums) / len(nums)
    c1, c2 = st.columns([1, 2])
    with c1:
        st.plotly_chart(_gauge(avg_v, max_v, title, color=color, h=300), use_container_width=True)
    with c2:
        if bands:
            for b in bands:
                bmin, bmax = b.get("min", 1), b.get("max", max_v)
                cnt = sum(1 for v in nums if bmin <= v <= bmax)
                pct = round(100 * cnt / len(nums)) if nums else 0
                bc = _resolve_color(b.get("color", "gray"))
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:12px;margin:6px 0;">'
                    f'<div style="width:130px;font-size:0.76rem;">{_esc(b.get("label",""))}</div>'
                    f'<div style="flex:1;background:{bc}22;border-radius:4px;height:20px;">'
                    f'<div style="width:{pct}%;background:{bc};border-radius:4px;height:20px;'
                    f'min-width:4px;"></div></div>'
                    f'<div style="width:60px;font-size:0.76rem;color:{_C["muted"]};text-align:right;">'
                    f'{cnt} ({pct}%)</div></div>', unsafe_allow_html=True)
        else:
            min_v = sec.get("min", 1)
            bkts = {str(i): 0 for i in range(int(min_v), int(max_v)+1)}
            for v in nums:
                k = str(int(round(v)))
                if k in bkts: bkts[k] += 1
            st.plotly_chart(
                _v_bar(list(bkts.keys()), list(bkts.values()), title, color, h=300),
                use_container_width=True)
    # â”€â”€ Context in expander â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    context = []
    if sec.get("how_to_read"): context.append(f"**How to read:** {sec['how_to_read']}")
    if sec.get("calc_note"):   context.append(f"**How calculated:** {sec['calc_note']}")
    if context:
        exp_key = f"about_score_{field.replace('.', '_')}"
        with st.expander("â„¹ About this chart", expanded=False, key=exp_key):
            for c in context: st.markdown(c)


def _sec_list_distribution(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    field = sec.get("field", "")
    title = sec.get("title", _fmt_val(field.split(".")[-1]))
    color = _resolve_color(sec.get("color", "blue"))
    limit = sec.get("limit", 15)
    chart = sec.get("chart", "h_bar")
    counts = _count_list_field(matrices, field)
    groups = _group_list_field(matrices, field)
    if not counts:
        st.caption(f"No data for {title}"); return
    raw  = list(counts.keys())[:limit]
    vals = list(counts.values())[:limit]
    lbls = [_fmt_val(l) for l in raw]
    rgs  = [groups.get(l, []) for l in raw]
    fig = _donut(lbls, vals, title, resp_groups=rgs) if chart == "donut" \
          else _h_bar(lbls, vals, title, color, resp_groups=rgs)
    st.plotly_chart(fig, use_container_width=True)


_ROUTE_PALETTE = [_C["r1"], _C["r2"], _C["seg_st"], _C["seg_dg"], _C["seg_fd"], _C["pos"]]


def _auto_detect_concepts(matrices: list[dict], sec: dict) -> list[dict]:
    """
    Detect concept evaluation objects from matrices.
    Returns list of: {key, label, color, attributes[{path, label}], score_path}

    Uses config `concepts` list if present (explicit), otherwise scans matrices for
    any top-level dict key ending in `_evaluation` and builds attribute list from
    the union of all sub-keys seen across respondents.
    """
    # Explicit config takes priority
    if sec.get("concepts"):
        concepts = []
        for i, c in enumerate(sec["concepts"]):
            key   = c.get("key", "")
            label = c.get("label", _fmt_val(key))
            color = _resolve_color(c.get("color", "")) or _ROUTE_PALETTE[i % len(_ROUTE_PALETTE)]
            attrs = c.get("attributes", [])
            score = c.get("score_path", f"{key}.overall_appeal_score")
            concepts.append({"key": key, "label": label, "color": color,
                             "attributes": attrs, "score_path": score})
        return concepts

    # Auto-detect: scan all matrices for *_evaluation keys
    eval_keys: dict[str, set] = {}
    for m in matrices:
        for k, v in m.items():
            if k.endswith("_evaluation") and isinstance(v, dict):
                eval_keys.setdefault(k, set()).update(v.keys())

    if not eval_keys:
        return []

    # Sort deterministically: route1 < route2 < concept_a < concept_b etc.
    def _sort_key(k):
        import re
        nums = re.findall(r"\d+", k)
        return (int(nums[0]) if nums else 99, k)

    concepts = []
    for i, key in enumerate(sorted(eval_keys.keys(), key=_sort_key)):
        sub_keys = sorted(eval_keys[key])
        # Derive human label: "route1_evaluation" â†’ "Route 1", "concept_a_evaluation" â†’ "Concept A"
        label_raw = key.replace("_evaluation", "").replace("_", " ").title()
        # Fix common acronyms mangled by .title()
        _ACR = {"Fiu": "FIU", "Sgb": "SGB", "Nps": "NPS", "T 1 ": "T+1 ", "Diy": "DIY",
                "Api": "API", "Kyc": "KYC", "Upi": "UPI", "Sebi": "SEBI", "Rbi": "RBI"}
        for wrong, right in _ACR.items():
            label_raw = label_raw.replace(wrong, right)
        color = _ROUTE_PALETTE[i % len(_ROUTE_PALETTE)]
        # Build attribute list from sub-keys (exclude overall_appeal_score, key_verbatim)
        _ACR2 = {"Fiu": "FIU", "Sgb": "SGB", "Nps": "NPS", "T 1 ": "T+1 ",
                 "Diy": "DIY", "Api": "API", "Kyc": "KYC", "Upi": "UPI", "Sebi": "SEBI"}
        def _fix_acronyms(s: str) -> str:
            for wrong, right in _ACR2.items():
                s = s.replace(wrong, right)
            return s
        attrs = [
            {"path": f"{key}.{sk}",
             "label": _fix_acronyms(
                 sk.replace("_", " ").replace("understood", "").replace("noticed", "").title().strip()
             )}
            for sk in sub_keys
            if sk not in ("overall_appeal_score", "key_verbatim")
        ]
        score_path = f"{key}.overall_appeal_score"
        concepts.append({"key": key, "label": label_raw, "color": color,
                         "attributes": attrs, "score_path": score_path})

    # Use config labels if provided (override auto-detected)
    label_overrides = {c.get("key", ""): c.get("label", "") for c in sec.get("concept_labels", [])}
    for c in concepts:
        if label_overrides.get(c["key"]):
            c["label"] = label_overrides[c["key"]]

    return concepts


def _sec_route_attribute_grid(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    n = len(matrices)
    concepts = _auto_detect_concepts(matrices, sec)
    if not concepts:
        st.caption("No concept evaluation data found."); return

    # Appeal score KPI row
    score_cols = st.columns(len(concepts))
    for i, c in enumerate(concepts):
        avg = _avg([_get(m, c["score_path"]) for m in matrices])
        _kpi(score_cols[i], f"{c['label']} Appeal",
             f"{avg:.1f}/10" if avg is not None else "â€”", c["color"])

    st.markdown("---")

    # One column per concept
    grid_cols = st.columns(len(concepts))
    for col_widget, c in zip(grid_cols, concepts):
        with col_widget:
            st.markdown(
                f"**<span style='color:{c['color']};font-size:0.95rem;'>"
                f"{_esc(c['label'])}</span>**",
                unsafe_allow_html=True,
            )
            for attr in c["attributes"]:
                path  = attr.get("path", "")
                label = attr.get("label", _fmt_val(path.split(".")[-1]))
                _attr_row(label, _count_field(matrices, path), n, c["color"])


def _sec_claim_reactions(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    field = sec.get("field", "key_claim_reactions")
    ck = sec.get("claim_key", "claim")
    rk = sec.get("reaction_key", "reaction")
    uk = sec.get("understood_key", "understood")
    agg: dict[str, dict] = {}
    for m in matrices:
        for cr in (m.get(field) or []):
            if not isinstance(cr, dict): continue
            claim = str(cr.get(ck) or "").strip()
            if not claim: continue
            if claim not in agg:
                agg[claim] = {"reactions": Counter(), "understood": 0, "total": 0}
            agg[claim]["reactions"][str(cr.get(rk) or "unknown")] += 1
            if cr.get(uk): agg[claim]["understood"] += 1
            agg[claim]["total"] += 1
    if not agg:
        st.caption("No claim reaction data."); return
    _ORDER = ["strong_acceptance","acceptance","conditional","confused","skeptical","rejection"]
    _RC = {"strong_acceptance":_C["pos"],"acceptance":"#34d399","conditional":_C["amb"],
           "confused":"#8b5cf6","skeptical":"#f97316","rejection":_C["neg"]}
    top_n  = sec.get("top_n", 15)
    sorted_claims = sorted(agg.items(), key=lambda x: -x[1]["total"])
    st.markdown(f"**Key Claim Reactions** â€” {len(agg)} claims tested")

    def _render_claim_card(claim, data):
        total = data["total"]
        react_html = "".join(
            f'<span style="background:{_RC.get(r,_C["neu"])}22;color:{_RC.get(r,_C["neu"])};'
            f'padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;'
            f'margin-right:3px;">{_esc(_fmt_val(r))} {cnt}</span>'
            for r, cnt in sorted(data["reactions"].items(),
                                  key=lambda x: _ORDER.index(x[0]) if x[0] in _ORDER else 99)
        )
        und_pct = round(100 * data["understood"] / total) if total else 0
        st.markdown(
            f'<div style="border:1px solid {_C["border"]};border-radius:6px;'
            f'padding:8px 14px;margin:4px 0;">'
            f'<div style="font-size:0.78rem;font-weight:700;margin-bottom:4px;">{_esc(claim[:120])}</div>'
            f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">'
            f'{react_html}'
            f'<span style="font-size:0.63rem;color:{_C["muted"]};">understood: {und_pct}%</span>'
            f'</div></div>', unsafe_allow_html=True)

    for claim, data in sorted_claims[:top_n]:
        _render_claim_card(claim, data)
    if len(sorted_claims) > top_n:
        remaining = sorted_claims[top_n:]
        with st.expander(f"Show {len(remaining)} more claims"):
            for claim, data in remaining:
                _render_claim_card(claim, data)


def _sec_diverging_bar(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    d_field = sec.get("drivers_field", "adoption.drivers")
    b_field = sec.get("barriers_field", "adoption.barriers")
    d_label = sec.get("drivers_label", "Drivers")
    b_label = sec.get("barriers_label", "Barriers")
    title   = sec.get("title", "Drivers vs Barriers")
    limit   = sec.get("limit", 12)
    _chart_header(
        title,
        subtitle=sec.get("subtitle", "What pulls respondents toward adopting Karat (right) vs what holds them back (left) â€” extracted from interview responses."),
        how_to_read="Green bars extend right = adoption drivers. Red bars extend left = barriers. Items appearing on both sides = ambivalent factors.",
        calc_note="AI-extracted lists from adoption.drivers and adoption.barriers. Model pulls specific factors mentioned by respondent â€” verbatim themes, not inferred. Multi-select per respondent, counted once each.",
    )
    _legend_row([
        (d_label, _C["pos"], "factors that pull the respondent toward adopting"),
        (b_label, _C["neg"], "concerns or blockers that prevent adoption"),
    ])
    d_counts = _count_list_field(matrices, d_field)
    b_counts = _count_list_field(matrices, b_field)
    if not d_counts and not b_counts:
        st.caption("No drivers/barriers data."); return
    all_keys = sorted(
        set(list(d_counts.keys())[:limit]) | set(list(b_counts.keys())[:limit]),
        key=lambda k: -(d_counts.get(k,0)+b_counts.get(k,0))
    )[:limit]
    d_groups = _group_list_field(matrices, d_field)
    b_groups = _group_list_field(matrices, b_field)
    fig = _diverging_bar(
        [_fmt_val(k) for k in all_keys],
        [d_counts.get(k,0) for k in all_keys],
        [b_counts.get(k,0) for k in all_keys],
        d_label, b_label, title,
        pos_resp_groups=[d_groups.get(k,[]) for k in all_keys],
        neg_resp_groups=[b_groups.get(k,[]) for k in all_keys],
    )
    st.plotly_chart(fig, use_container_width=True)
    if sec.get("caption"): _chart_caption(sec["caption"])


def _sec_segment_kpi_table(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    import pandas as pd
    seg_field = sec.get("segment_field", "respondent.segment")
    metrics   = sec.get("metrics", [])
    segs = sorted({str(_get(m, seg_field) or "") for m in matrices if _get(m, seg_field)})
    if not segs:
        st.caption("No segment data."); return
    rows = []
    for seg in segs:
        seg_mats = [m for m in matrices if str(_get(m, seg_field) or "") == seg]
        row = {"Segment": _fmt_val(seg), "n": len(seg_mats)}
        for metric in metrics:
            row[metric.get("label", metric.get("field",""))] = _compute_kpi_value(metric, seg_mats)
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _sec_segment_card_grid(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    """Styled per-segment colored cards (title + n + stat rows) â€” the config-driven equivalent
    of the hand-built "Respondent Profiles by Segment" row. Reuses _seg_card/_seg_color exactly
    as the legacy _render_profiles did, so visual output matches; only the field list is
    config-driven instead of hardcoded."""
    seg_field = sec.get("segment_field", "respondent.segment")
    metrics   = sec.get("metrics", [])
    segs = sorted({str(_get(m, seg_field) or "") for m in matrices if _get(m, seg_field)})
    if not segs:
        st.caption("No segment data."); return
    cols = st.columns(len(segs))
    for col, seg in zip(cols, segs):
        seg_mats = [m for m in matrices if str(_get(m, seg_field) or "") == seg]
        stats = {
            metric.get("label", metric.get("field", "")): _compute_kpi_value(metric, seg_mats)
            for metric in metrics
        }
        with col:
            _seg_card(_fmt_val(seg), _seg_color(seg), len(seg_mats), stats)


def _sec_portfolio_allocation(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    alloc_fields = sec.get("allocation_fields", [])
    if alloc_fields:
        avgs, lbls = [], []
        for af in alloc_fields:
            avg = _avg([_get(m, af["path"]) for m in matrices])
            if avg is not None:
                avgs.append(avg); lbls.append(af.get("label", af["path"].split(".")[-1]))
        if avgs:
            c1, c2 = st.columns(2)
            with c1:
                st.plotly_chart(_donut(lbls, avgs, "Avg Portfolio Allocation"),
                                use_container_width=True)
            with c2:
                for l, v in zip(lbls, avgs): st.markdown(f"- **{l}**: {v:.1f}%")
    pf = sec.get("platforms_field", "portfolio_behavior.platforms_used")
    isf = sec.get("info_sources_field", "portfolio_behavior.info_sources")
    p_c = _count_list_field(matrices, pf)
    i_c = _count_list_field(matrices, isf)
    p_g = _group_list_field(matrices, pf)
    i_g = _group_list_field(matrices, isf)
    if p_c or i_c:
        pc1, pc2 = st.columns(2)
        if p_c:
            with pc1:
                pl = list(p_c.keys())[:10]; pv = list(p_c.values())[:10]
                rg = [p_g.get(k,[]) for k in pl]
                st.plotly_chart(_h_bar([_fmt_val(x) for x in pl], pv, "Platforms Used",
                                       _C["r1"], h=220, resp_groups=rg), use_container_width=True)
        if i_c:
            with pc2:
                il = list(i_c.keys())[:10]; iv = list(i_c.values())[:10]
                rg = [i_g.get(k,[]) for k in il]
                st.plotly_chart(_h_bar([_fmt_val(x) for x in il], iv, "Information Sources",
                                       _C["r2"], h=220, resp_groups=rg), use_container_width=True)


def _sec_verbatim_wall_section(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    topics     = sec.get("topics", [])
    key_prefix = sec.get("key_prefix", "vb_wall")
    title      = sec.get("title", "Verbatim Evidence")
    pain_only  = sec.get("pain_only", False)
    passages = _get_passages(matrices, topics=topics or None, pain_only=pain_only)
    st.markdown(f"#### {title}")
    _verbatim_wall(passages, key_prefix, title)


def _sec_text_quotes(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    field   = sec.get("field", "")
    title   = sec.get("title", _fmt_val(field.split(".")[-1]))
    max_q   = sec.get("max_quotes", 6)
    quotes = [
        {"content": str(_get(m, field)).strip(),
         "segment": str(_get(m,"respondent.segment") or "â€”"),
         "city":    str(_get(m,"respondent.city") or "â€”"),
         "doc_id":  str(m.get("doc_id") or "â€”")}
        for m in matrices
        if _get(m, field) and len(str(_get(m, field)).strip()) > 10
    ]
    if not quotes:
        st.caption(f"No data for {title}"); return
    st.markdown(f"**{_esc(title)}**", unsafe_allow_html=True)
    for q in quotes[:max_q]:
        seg_c = _seg_color(q["segment"])
        content_esc = _esc(q["content"])
        st.markdown(
            f'<div style="border-left:3px solid {seg_c};padding:10px 14px;margin:6px 0;'
            f'background:{seg_c}08;border-radius:0 6px 6px 0;">'
            f'<div style="font-size:0.82rem;color:{_C["text"]};margin-bottom:6px;'
            f'font-style:italic;line-height:1.5;">{content_esc}</div>'
            f'<span style="font-size:0.63rem;color:{_C["muted"]};">'
            f'{_esc(q["segment"])} Â· {_esc(q["city"])} Â· {_esc(q["doc_id"])}'
            f'</span></div>', unsafe_allow_html=True)


def _sec_benchmark_summary(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    field = sec.get("field", "benchmark_comparisons")
    bk = sec.get("benchmark_key", "benchmark")
    vk = sec.get("verdict_key", "verdict")
    agg: dict = {}
    for m in matrices:
        for bc in (m.get(field) or []):
            if not isinstance(bc, dict): continue
            b = str(bc.get(bk) or "").strip()
            if not b: continue
            agg.setdefault(b, Counter())[str(bc.get(vk) or "unclear")] += 1
    if not agg:
        st.caption("No benchmark data."); return
    _VC = {"product_better":_C["pos"],"comparable":_C["amb"],"product_worse":_C["neg"],"unclear":_C["neu"]}
    st.markdown("**Benchmark Comparisons**")
    for bench, v_counts in sorted(agg.items()):
        total = sum(v_counts.values())
        vh = "".join(
            f'<span style="background:{_VC.get(v,_C["neu"])}22;color:{_VC.get(v,_C["neu"])};'
            f'padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;'
            f'margin-right:3px;">{_esc(_fmt_val(v))} {cnt}</span>'
            for v, cnt in v_counts.most_common()
        )
        st.markdown(
            f'<div style="border-left:3px solid {_C["border"]};padding:6px 14px;margin:4px 0;">'
            f'<b>{_esc(bench)}</b> <span style="font-size:0.63rem;color:{_C["muted"]};">(n={total})</span>'
            f' {vh}</div>', unsafe_allow_html=True)


def _sec_ai_insight(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    section_id = sec.get("section_id", "generic")
    title      = sec.get("title", "AI Research Finding")
    prompt     = sec.get("prompt", "Synthesize the key findings from this section.")
    regen_key  = sec.get("regen_key") or f"ai_{section_id}_regen"
    n = len(matrices)
    ctx: dict = {"n": n, "study": proj_name, "filter": _fmt_filter_ctx(active_filters, n)}
    for cf in sec.get("context_fields", []):
        if isinstance(cf, str):
            cf = {"field": cf, "label": cf.split(".")[-1].replace("_", " ").title()}
        if not isinstance(cf, dict):
            continue
        ctx[cf.get("label", cf.get("field",""))] = _compute_kpi_value(
            {"field": cf.get("field",""), "value_type": cf.get("value_type","avg_score"),
             "eq_val": cf.get("eq_val",""), "contains_val": cf.get("contains_val",""),
             "suffix": cf.get("suffix","")}, matrices)
    st.markdown(f"#### {_esc(title)}")
    _ai_finding_robust(section_id, findings_dir, call_or, ctx, active_filters, prompt, regen_key, proj_name)


def _sec_tagline_preferences(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    pref_field = sec.get("field", "tagline_reaction.preferred_tagline")
    verb_field = sec.get("verbatim_field", "tagline_reaction.tagline_verbatim")
    pref_c = _count_field(matrices, pref_field)
    pref_g = _group_field(matrices, pref_field)
    if not pref_c:
        st.caption("No tagline data."); return
    c1, c2 = st.columns(2)
    with c1:
        lbls = [_fmt_val(k) for k in pref_c.keys()]
        rgs  = [pref_g.get(k,[]) for k in pref_c.keys()]
        st.plotly_chart(_donut(lbls, list(pref_c.values()), "Preferred Tagline", resp_groups=rgs),
                        use_container_width=True)
    with c2:
        st.markdown("**Tagline Verbatims**")
        shown = 0
        for m in matrices:
            vb = _get(m, verb_field)
            if vb and len(str(vb).strip()) > 10 and shown < 5:
                seg  = str(_get(m,"respondent.segment") or "â€”")
                city = str(_get(m,"respondent.city") or "â€”")
                pref = _fmt_val(_get(m, pref_field) or "â€”")
                sc = _seg_color(seg)
                st.markdown(
                    f'<div style="border-left:3px solid {sc};padding:6px 12px;margin:4px 0;'
                    f'background:{sc}08;border-radius:0 5px 5px 0;">'
                    f'<span style="font-size:0.6rem;color:{_C["muted"]};">'
                    f'{_esc(seg)} Â· {_esc(city)} Â· prefers: {_esc(pref)}</span><br>'
                    f'<span style="font-size:0.82rem;">{_esc(str(vb).strip())}</span>'
                    f'</div>', unsafe_allow_html=True)
                shown += 1


def _sec_route_preference(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    field     = sec.get("field", "preferred_route")
    seg_field = sec.get("segment_field", "respondent.segment")
    pref_c = _count_field(matrices, field)
    pref_g = _group_field(matrices, field)
    segs = sorted({str(_get(m, seg_field) or "") for m in matrices if _get(m, seg_field)})
    if not pref_c:
        st.caption("No route preference data."); return
    _chart_header(
        "Overall Route Preference",
        subtitle="Which communication route each respondent found more compelling after seeing both Route 1 and Route 2.",
        how_to_read="Larger slice = more respondents preferred that route. Blended = valued elements of both equally.",
        calc_note="AI-extracted from preferred_route field. Model infers explicit or implicit preference from what the respondent said was more convincing. Values: Route1, Route2, Blended, Unclear.",
    )
    _legend_row([
        ("Route 1", _C["r1"],  "Safety / Ownership-led â€” leads with vault allocation, SEBI oversight, capital protection"),
        ("Route 2", _C["r2"],  "Returns / Value-led â€” leads with yield, SGB comparison, compounding in gold units"),
        ("Blended", _C["amb"], "found both routes compelling â€” neither clearly won"),
    ])
    c1, c2 = st.columns([1, 2])
    with c1:
        lbls = [_fmt_val(k) for k in pref_c.keys()]
        rgs  = [pref_g.get(k,[]) for k in pref_c.keys()]
        colors = [_C["r1"] if "1" in k.lower() else _C["r2"] if "2" in k.lower() else _C["neu"]
                  for k in pref_c.keys()]
        st.plotly_chart(_donut(lbls, list(pref_c.values()), "Route Preference",
                                colors=colors, resp_groups=rgs), use_container_width=True)
    with c2:
        if segs:
            routes = sorted(pref_c.keys())
            series = {_fmt_val(r): [
                sum(1 for m in [x for x in matrices if str(_get(x,seg_field) or "")==seg]
                    if str(_get(m,field) or "").lower() == r.lower())
                for seg in segs
            ] for r in routes}
            st.plotly_chart(
                _grouped_bar([_fmt_val(s) for s in segs], series, "By Segment"),
                use_container_width=True)


def _sec_adoption_detail(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    d_field  = sec.get("drivers_field", "adoption.drivers")
    b_field  = sec.get("barriers_field", "adoption.barriers")
    tl_field = sec.get("timeline_field", "adoption.timeline")
    tr_field = sec.get("trial_amount_field", "adoption.trial_amount_inr")
    it_field = sec.get("intent_field", "adoption.intent_score")
    bv_field = sec.get("barrier_verbatim_field", "adoption.barrier_verbatim")
    n = len(matrices)
    avg_int = _avg([_get(m, it_field) for m in matrices])
    trials  = [float(_get(m, tr_field)) for m in matrices
               if _get(m, tr_field) is not None and isinstance(_get(m, tr_field), (int, float))]
    avg_trial = sum(trials)/len(trials) if trials else None
    kc = st.columns(3)
    _kpi(kc[0], "Avg Intent", f"{avg_int:.1f}/10" if avg_int else "â€”", _C["pos"])
    _kpi(kc[1], "w/ Trial Amount", f"{len(trials)}/{n}", _C["r1"])
    _kpi(kc[2], "Avg Trial Amount", f"â‚¹{avg_trial:,.0f}" if avg_trial else "â€”", _C["seg_dg"])
    st.markdown("---")
    d_c = _count_list_field(matrices, d_field)
    b_c = _count_list_field(matrices, b_field)
    t_c = _count_field(matrices, tl_field)
    d_groups = _group_list_field(matrices, d_field)
    b_groups = _group_list_field(matrices, b_field)
    t_groups = _group_field(matrices, tl_field)
    ac1, ac2, ac3 = st.columns(3)
    if d_c:
        with ac1:
            dl = list(d_c.keys())[:8]; dv = list(d_c.values())[:8]
            rg = [d_groups.get(k,[]) for k in dl]
            st.plotly_chart(_h_bar([_fmt_val(x) for x in dl], dv, "Top Drivers",
                                    _C["pos"], h=240, resp_groups=rg), use_container_width=True)
    if b_c:
        with ac2:
            bl = list(b_c.keys())[:8]; bv = list(b_c.values())[:8]
            rg = [b_groups.get(k,[]) for k in bl]
            st.plotly_chart(_h_bar([_fmt_val(x) for x in bl], bv, "Top Barriers",
                                    _C["neg"], h=240, resp_groups=rg), use_container_width=True)
    if t_c:
        with ac3:
            tl_keys = list(t_c.keys())[:8]; tv = list(t_c.values())[:8]
            rg = [t_groups.get(k,[]) for k in tl_keys]
            st.plotly_chart(_donut([_fmt_val(k) for k in tl_keys], tv, "Timeline", h=240,
                                   resp_groups=rg), use_container_width=True)
    bverbs = [{"content": str(_get(m, bv_field)).strip(),
               "segment": str(_get(m,"respondent.segment") or ""),
               "city":    str(_get(m,"respondent.city") or ""),
               "doc_id":  str(m.get("doc_id") or "")}
              for m in matrices
              if _get(m, bv_field) and len(str(_get(m, bv_field)).strip()) > 10]
    if bverbs:
        st.markdown("**Key Barrier Verbatims**")
        for bv in bverbs[:5]:
            sc = _seg_color(bv["segment"])
            st.markdown(
                f'<div style="border-left:3px solid {_C["neg"]};padding:6px 12px;margin:4px 0;'
                f'background:{_C["neg"]}06;border-radius:0 5px 5px 0;">'
                f'<span style="font-size:0.6rem;color:{_C["muted"]};">'
                f'{_esc(bv["segment"])} Â· {_esc(bv["city"])} Â· {_esc(bv["doc_id"])}'
                f'</span><br><span style="font-size:0.82rem;">{_esc(bv["content"])}</span>'
                f'</div>', unsafe_allow_html=True)


def _sec_trust_builders(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    tb_field  = sec.get("field", "trust_builders")
    gap_field = sec.get("trust_gap_field", "crypto_association_effect")
    verb_field = sec.get("trust_gap_verbatim", "crypto_association_effect")
    aw_field  = sec.get("awareness_field", "platform_association")
    n = len(matrices)
    high_gap = sum(1 for m in matrices if str(_get(m, gap_field) or "").lower() == "high")
    aware    = sum(1 for m in matrices
                   if str(_get(m, aw_field) or "").lower() in ("medium","high"))
    tb_c = _count_list_field(matrices, tb_field)
    gap_c = _count_field(matrices, gap_field)
    none_gap = sum(1 for m in matrices if str(_get(m, gap_field) or "").lower() == "low")
    kc = st.columns(4)
    _kpi(kc[0], "High Trust Gap",   f"{high_gap}/{n} ({_pct_str(high_gap,n)})", _C["neg"])
    _kpi(kc[1], "Platform Aware",   f"{aware}/{n} ({_pct_str(aware,n)})", _C["amb"])
    _kpi(kc[2], "Trust Themes",     str(len(tb_c)), _C["pos"])
    _kpi(kc[3], "Low Trust Gap",    f"{none_gap}/{n} ({_pct_str(none_gap,n)})", _C["pos"])
    st.markdown("---")
    tc1, tc2 = st.columns(2)
    tb_groups  = _group_list_field(matrices, tb_field)
    gap_groups = _group_field(matrices, gap_field)
    with tc1:
        if tb_c:
            _chart_header(
                "What Builds Trust in CoinDCX Karat",
                subtitle="Specific proof points respondents said would make them trust the product â€” extracted from what they actually mentioned.",
                how_to_read="Longer bar = more respondents cited that factor. Top 3 = priority messaging elements.",
                calc_note="AI-extracted as list from trust_builders. Model pulls specific trust-building factors mentioned: e.g. SEBI regulation, audited reserves, brand reputation, T+1 liquidity proof. Multi-select per respondent.",
            )
            lbls = [_fmt_val(k) for k in list(tb_c.keys())[:10]]
            vals = list(tb_c.values())[:10]
            rg   = [tb_groups.get(k,[]) for k in list(tb_c.keys())[:10]]
            st.plotly_chart(_h_bar(lbls, vals, "Trust Builders Cited", _C["pos"], h=300,
                                   resp_groups=rg), use_container_width=True)
            _chart_caption("Each trust factor = something the respondent explicitly said would make them feel safe. Use top 3 as proof-point headlines.")
    with tc2:
        if gap_c:
            _chart_header(
                "CoinDCX Crypto Trust Gap",
                subtitle="How much crypto/fintech distrust undermines respondents' trust in CoinDCX as the product host.",
                how_to_read="High gap = crypto stigma is stronger than CoinDCX brand trust. Low gap = brand credibility wins.",
                calc_note="AI-classified from crypto_association_effect. Model judges the directional effect of CoinDCX's crypto identity on trust in this product. Classified: positive / neutral / conditional / negative.",
            )
            _legend_row([
                ("Low",    _C["pos"], "brand trust outweighs crypto concern â€” adoption-ready"),
                ("Medium", _C["amb"], "partial confidence â€” needs proof points to tip over"),
                ("High",   _C["neg"], "crypto stigma dominates â€” trust-first messaging essential"),
            ])
            gl = [_fmt_val(k) for k in gap_c.keys()]; gv = list(gap_c.values())
            colors_g = [_C["neg"] if "high" in k.lower() else _C["amb"] if "medium" in k.lower()
                        else _C["pos"] for k in gap_c.keys()]
            rg_gap = [gap_groups.get(k,[]) for k in gap_c.keys()]
            st.plotly_chart(_donut(gl, gv, "Crypto Trust Gap", colors=colors_g,
                                   resp_groups=rg_gap), use_container_width=True)
            _chart_caption("High trust gap slice = audience who needs trust-building before any product pitch. Address their specific concern: custody, regulation, or CoinDCX's crypto association.")
    verbs = [{"content": str(_get(m, verb_field)).strip(),
              "segment": str(_get(m,"respondent.segment") or ""),
              "city":    str(_get(m,"respondent.city") or ""),
              "sev":     str(_get(m, gap_field) or "")}
             for m in matrices
             if _get(m, verb_field) and len(str(_get(m, verb_field)).strip()) > 10]
    if verbs:
        st.markdown("**Trust Gap Verbatims**")
        for vb in verbs[:5]:
            sc = _C["neg"] if "high" in vb["sev"].lower() else _C["amb"] if "medium" in vb["sev"].lower() else _C["neu"]
            st.markdown(
                f'<div style="border-left:3px solid {sc};padding:6px 12px;margin:4px 0;'
                f'background:{sc}06;border-radius:0 5px 5px 0;">'
                f'<span style="font-size:0.6rem;color:{_C["muted"]};">'
                f'{_esc(vb["segment"])} Â· {_esc(vb["city"])}'
                f'</span><br><span style="font-size:0.82rem;">{_esc(vb["content"])}</span>'
                f'</div>', unsafe_allow_html=True)


def _sec_pain_points_summary(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    field     = sec.get("field", "pain_points")
    sev_key   = sec.get("severity_key", "severity")
    desc_key  = sec.get("desc_key", "issue_description")
    area_key  = sec.get("area_key", "product_area")
    quote_key = sec.get("quote_key", "verbatim_quote")
    area_c: Counter = Counter(); sev_c: Counter = Counter(); all_pps = []
    area_resp: dict = {}
    for m in matrices:
        for pp in (m.get(field) or []):
            if not isinstance(pp, dict): continue
            area  = str(pp.get(area_key) or pp.get("area") or "other").strip()
            sev   = str(pp.get(sev_key) or "").lower()
            desc  = str(pp.get(desc_key) or pp.get("description") or "").strip()
            quote = str(pp.get(quote_key) or pp.get("verbatim") or "").strip()
            area_c[area] += 1
            area_resp.setdefault(area, []).append(m)
            if sev: sev_c[sev] += 1
            if desc:
                all_pps.append({"area":area,"sev":sev,"desc":desc,"quote":quote,
                                 "segment":str(_get(m,"respondent.segment") or ""),
                                 "city":str(_get(m,"respondent.city") or ""),
                                 "doc_id":str(m.get("doc_id") or "")})
    if not all_pps:
        st.caption("No pain point data."); return
    n_pp = len(all_pps)
    high_n = sum(1 for p in all_pps if p["sev"] == "high")
    kc = st.columns(3)
    _kpi(kc[0], "Total Pain Points", str(n_pp), _C["neg"])
    _kpi(kc[1], "High Severity", f"{high_n}/{n_pp} ({_pct_str(high_n,n_pp)})", _C["neg"])
    _kpi(kc[2], "Areas Affected", str(len(area_c)), _C["amb"])
    st.markdown("---")
    pc1, pc2 = st.columns(2)
    with pc1:
        if area_c:
            area_keys = list(area_c.keys())[:10]
            al = [_fmt_val(k) for k in area_keys]
            av = list(area_c.values())[:10]
            rg = [area_resp.get(k,[]) for k in area_keys]
            st.plotly_chart(_h_bar(al, av, "Pain Point Areas", _C["neg"],
                                   resp_groups=rg), use_container_width=True)
    with pc2:
        if sev_c:
            sl = [_fmt_val(k) for k in sev_c.keys()]; sv = list(sev_c.values())
            sc_colors = [_C["neg"] if "high" in k else _C["amb"] if "medium" in k else _C["pos"]
                         for k in sev_c.keys()]
            st.plotly_chart(_donut(sl, sv, "Severity Distribution", colors=sc_colors),
                            use_container_width=True)
    top_pps = sorted(all_pps,
                     key=lambda p: ["high","medium","low"].index(p["sev"])
                     if p["sev"] in ("high","medium","low") else 3)[:10]
    st.markdown("**Top Pain Points**")
    for pp in top_pps:
        sev_col = _C["neg"] if pp["sev"]=="high" else _C["amb"] if pp["sev"]=="medium" else _C["pos"]
        badge = (f'<span style="background:{sev_col}22;color:{sev_col};font-size:0.6rem;'
                 f'font-weight:800;padding:2px 6px;border-radius:4px;text-transform:uppercase;">'
                 f'{_esc(pp["sev"])}</span> ') if pp["sev"] else ""
        area_lbl = f"<b>{_esc(_fmt_val(pp['area']))}</b> â€” " if pp["area"] else ""
        qh = (f'<div style="font-size:0.78rem;color:#666;font-style:italic;margin-top:4px;'
              f'padding-left:8px;border-left:2px solid {sev_col}88;">'
              f'"{_esc(pp["quote"])}"</div>') if pp["quote"] else ""
        st.markdown(
            f'<div style="border-left:3px solid {sev_col};padding:6px 10px;margin:4px 0;'
            f'background:{sev_col}10;border-radius:0 5px 5px 0;">'
            f'{badge}{area_lbl}<span style="font-size:0.82rem;">{_esc(pp["desc"])}</span>'
            f'{qh}<div style="font-size:0.6rem;color:{_C["muted"]};margin-top:3px;">'
            f'{_esc(pp["segment"])} Â· {_esc(pp["city"])} Â· {_esc(pp["doc_id"])}</div>'
            f'</div>', unsafe_allow_html=True)


def _sec_separator(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    st.markdown("---")


def _sec_per_respondent(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    _render_per_respondent(matrices, all_matrices)


def _sec_study_report(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    _render_study_report(matrices, findings_dir, call_or, active_filters, proj_name)


def _sec_theme_clusters(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name):
    title  = sec.get("title", "Cross-Interview Themes")
    top_n  = sec.get("top_n", 10)
    use_llm_labels = sec.get("llm_labels", True)

    try:
        from infoleap.analytics.theme_clustering_engine import (
            compute_theme_clusters, label_clusters_with_llm)
    except ImportError as e:
        st.caption(f"Theme clustering unavailable ({e}).")
        return

    schema_dir = Path(findings_dir).parent / "schema"
    cache_path = schema_dir / "theme_clusters.json"

    with st.spinner("Clustering respondent quotes across interviews..."):
        result = compute_theme_clusters(matrices, min_cluster_size=3, cache_path=cache_path)

    themes = result.get("themes", [])
    if not themes:
        st.caption(result.get("note") or "No cross-interview themes found yet.")
        return

    if use_llm_labels and not result.get("_llm_labeled"):
        # Use the reliable paid client (llm_client.call_llm_safe / deepseek-chat), not `call_or`
        # (the free-tier OpenRouter rotation) â€” found live: that rotation's 400-token cap
        # silently truncated the batched multi-cluster JSON response, breaking parsing and
        # falling back to word-frequency labels with no visible error. This labeling call is
        # cheap (one batched call) and worth doing right rather than free.
        try:
            import sys as _sys
            _skills_dir = str(Path(__file__).resolve().parent.parent / "skills")
            if _skills_dir not in _sys.path:
                _sys.path.insert(0, _skills_dir)
            from llm_client import call_llm_safe as _call_llm_safe

            def _label_call_fn(prompt: str) -> str:
                return _call_llm_safe(
                    [{"role": "user", "content": prompt}], max_tokens=1200, temp=0.2)

            result = label_clusters_with_llm(result, _label_call_fn)
        except Exception as e:
            print(f"  _sec_theme_clusters: LLM labeling unavailable ({e}) â€” using word-frequency labels")
        result["_llm_labeled"] = True
        try:
            cache_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception:
            pass
        themes = result.get("themes", themes)

    st.markdown(f"#### {_esc(title)}")
    st.caption(
        f"{len(themes)} themes repeat across 2+ respondents, out of "
        f"{result.get('total_passages', 0)} verbatim passages analyzed across the whole study "
        f"(clustered locally by semantic similarity â€” {result.get('unclustered_count', 0)} "
        f"passages don't match a repeated pattern; individual variation, not dropped)."
    )

    for t in themes[:top_n]:
        sm   = t.get("sentiment_mix", {})
        neg  = sm.get("negative", 0)
        pos  = sm.get("positive", 0)
        pain = t.get("pain_point_share", 0.0)
        color = _C["neg"] if (pain >= 0.5 or neg > pos) else (_C["pos"] if pos > neg else _C["neu"])
        with st.container(border=True):
            pain_str = f" Â· {int(pain*100)}% pain signal" if pain > 0 else ""
            st.markdown(
                f'<div style="border-left:3px solid {color};padding-left:10px;">'
                f'<b>{_esc(str(t.get("label","")).title())}</b>'
                f'<span style="color:{_C["muted"]};font-size:0.75rem;"> â€” '
                f'{t["respondent_coverage"]} respondents Â· {t["size"]} mentions{pain_str}</span>'
                f'</div>', unsafe_allow_html=True)
            for q in t.get("sample_quotes", [])[:3]:
                st.markdown(
                    f'<div style="font-size:0.8rem;font-style:italic;color:{_C["text"]};'
                    f'margin:4px 0 4px 12px;">&ldquo;{_esc(q["content"])}&rdquo; '
                    f'<span style="color:{_C["muted"]};font-size:0.65rem;">'
                    f'â€” {_esc(q["doc_id"])}</span></div>', unsafe_allow_html=True)


SECTION_RENDERERS: dict[str, Any] = {
    "section_header":      _sec_section_header,
    "kpi_row":             _sec_kpi_row,
    "distribution":        _sec_distribution,
    "multi_distribution":  _sec_multi_distribution,
    "score_distribution":  _sec_score_distribution,
    "list_distribution":   _sec_list_distribution,
    "route_attribute_grid": _sec_route_attribute_grid,
    "claim_reactions":     _sec_claim_reactions,
    "diverging_bar":       _sec_diverging_bar,
    "segment_kpi_table":   _sec_segment_kpi_table,
    "segment_card_grid":   _sec_segment_card_grid,
    "portfolio_allocation": _sec_portfolio_allocation,
    "verbatim_wall_section": _sec_verbatim_wall_section,
    "text_quotes":         _sec_text_quotes,
    "benchmark_summary":   _sec_benchmark_summary,
    "ai_insight":          _sec_ai_insight,
    "tagline_preferences": _sec_tagline_preferences,
    "route_preference":    _sec_route_preference,
    "adoption_detail":     _sec_adoption_detail,
    "trust_builders":      _sec_trust_builders,
    "pain_points_summary": _sec_pain_points_summary,
    "separator":           _sec_separator,
    "per_respondent":      _sec_per_respondent,
    "study_report":        _sec_study_report,
    "theme_clusters":      _sec_theme_clusters,
}


def _render_section(sec: dict, matrices: list[dict], findings_dir: str,
                    call_or: Callable, active_filters: dict,
                    all_matrices: list[dict], proj_name: str) -> None:
    stype = sec.get("type", "")
    renderer = SECTION_RENDERERS.get(stype)
    if renderer is None:
        st.warning(f"Unknown section type: `{stype}` â€” skipping."); return
    try:
        renderer(sec, matrices, findings_dir, call_or, active_filters, all_matrices, proj_name)
    except Exception as e:
        st.error(f"Error in section `{stype}`: {e}")


def _render_from_config(proj: dict, cfg: dict, matrices: list[dict], all_matrices: list[dict],
                        findings_dir: str, call_or: Callable, active_filters: dict) -> None:
    proj_name = proj.get("name", proj.get("id", "Concept Test Study"))
    tabs_cfg  = cfg.get("tabs", [])
    if not tabs_cfg:
        st.warning("No `tabs` array in ui_config.json."); return
    tab_labels = [t.get("label", t.get("id", f"Tab {i+1}")) for i, t in enumerate(tabs_cfg)]
    st_tabs = st.tabs(tab_labels)
    for tab_cfg, st_tab in zip(tabs_cfg, st_tabs):
        with st_tab:
            st.session_state["_ct_active_tab_id"] = tab_cfg.get("id", "")

            # Theme filter â€” narrative_tags is extracted per-respondent for every project
            # (universal Layer 1 field: "themes with STRONG/MIXED evidence + any emerging
            # themes", per schema_generator.py) but this renderer never surfaced or filtered on
            # it before. Selecting a theme here filters the WHOLE tab â€” every kpi, chart, and
            # verbatim section below only sees matching respondents, not just one chart's field.
            _tab_matrices = matrices
            _all_tags = sorted({
                str(t).strip() for m in matrices for t in (m.get("narrative_tags") or [])
                if str(t).strip()
            }, key=str.lower)
            if _all_tags:
                _tag_options = ["All themes"] + [_fmt_val(t) for t in _all_tags]
                _sel_theme = st.selectbox(
                    "ðŸ· Filter by theme", _tag_options,
                    key=f"ct_theme_filter_{tab_cfg.get('id', '')}",
                )
                if _sel_theme != "All themes":
                    _tab_matrices = [
                        m for m in matrices
                        if any(_fmt_val(str(t).strip()) == _sel_theme
                               for t in (m.get("narrative_tags") or []))
                    ]
                    st.caption(f"Showing {len(_tab_matrices)} of {len(matrices)} respondents "
                               f"tagged with this theme â€” every chart and verbatim below is "
                               f"scoped to them.")

            for sec in tab_cfg.get("sections", []):
                _render_section(sec, _tab_matrices, findings_dir, call_or,
                                active_filters, all_matrices, proj_name)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# GLOBAL FILTER HEADER
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _render_header(all_matrices: list[dict], proj: dict) -> dict:
    """Render global study header + filters. Returns active filter dict."""
    n_total  = len(all_matrices)
    proj_name = proj.get("name", proj.get("id", "Concept Test Study"))
    proj_desc = proj.get("description", "")
    segs     = sorted({str(_get(m, "respondent.segment") or "") for m in all_matrices
                       if _get(m, "respondent.segment")})
    cities   = sorted({str(_get(m, "respondent.city") or "") for m in all_matrices
                       if _get(m, "respondent.city")})

    promo    = sum(1 for m in all_matrices if (m.get("nps_signal") or "").lower() == "promoter")
    avg_int  = _avg([_get(m, "adoption_likelihood") for m in all_matrices])
    avg_comp = _avg([_get(m, "concept_understanding.comprehension_score") for m in all_matrices])
    r1_pref  = sum(1 for m in all_matrices
                   if "1" in str(_get(m, "preferred_route") or "").lower()
                   or "safety" in str(_get(m, "preferred_route") or "").lower())

    desc_div = (
        f'<div style="font-size:0.75rem;color:{_C["muted"]};">{_esc(proj_desc)}</div>'
        if proj_desc else ""
    )
    st.markdown(
        f'<div style="background:{_C["surface"]};border:1px solid {_C["border"]};'
        f'border-radius:10px;padding:14px 20px;margin-bottom:16px;">'
        f'<div style="display:flex;gap:32px;flex-wrap:wrap;align-items:flex-start;">'
        f'<div style="flex:1;min-width:180px;">'
        f'<div style="font-size:0.62rem;font-weight:700;color:{_C["muted"]};'
        f'text-transform:uppercase;letter-spacing:0.08em;">Study</div>'
        f'<div style="font-size:1rem;font-weight:800;margin:2px 0;">{_esc(proj_name)}</div>'
        f'{desc_div}'
        f'</div>'
        f'<div><span style="font-size:0.62rem;font-weight:700;color:{_C["muted"]};'
        f'text-transform:uppercase;">Respondents</span>'
        f'<div style="font-size:1.1rem;font-weight:800;">{n_total}</div></div>'
        f'<div><span style="font-size:0.62rem;font-weight:700;color:{_C["muted"]};'
        f'text-transform:uppercase;">NPS Promoters</span>'
        f'<div style="font-size:1.1rem;font-weight:800;">{promo}/{n_total}</div></div>'
        f'<div><span style="font-size:0.62rem;font-weight:700;color:{_C["muted"]};'
        f'text-transform:uppercase;">Avg Intent</span>'
        f'<div style="font-size:1.1rem;font-weight:800;">'
        f'{f"{avg_int:.1f}/10" if avg_int else "â€”"}</div></div>'
        f'<div><span style="font-size:0.62rem;font-weight:700;color:{_C["muted"]};'
        f'text-transform:uppercase;">Avg Comprehension</span>'
        f'<div style="font-size:1.1rem;font-weight:800;">'
        f'{f"{avg_comp:.1f}/10" if avg_comp else "â€”"}</div></div>'
        f'<div><span style="font-size:0.62rem;font-weight:700;color:{_C["muted"]};'
        f'text-transform:uppercase;">R1 Preferred</span>'
        f'<div style="font-size:1.1rem;font-weight:800;">{_pct_str(r1_pref, n_total)}</div></div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Filter row
    fc1, fc2, fc3, fc4 = st.columns(4)
    seg_f   = fc1.selectbox("Segment", ["All"] + segs, key="ct_seg_f")
    city_f  = fc2.selectbox("City", ["All"] + cities, key="ct_city_f")
    route_f = fc3.selectbox("Route Shown", ["All", "Route1", "Route2", "Both", "Unknown"],
                             key="ct_route_f")
    nps_f   = fc4.selectbox("NPS Signal", ["All", "promoter", "neutral", "detractor"],
                             key="ct_nps_f")

    active_filters: dict[str, str] = {}
    if seg_f   != "All": active_filters["respondent.segment"] = seg_f
    if city_f  != "All": active_filters["respondent.city"]    = city_f
    if route_f != "All": active_filters["concept_understanding.route_shown"] = route_f
    if nps_f   != "All": active_filters["nps_signal"] = nps_f

    # Chart-driven cross-filters (set by clicking a bar/slice in Respondent Profiles charts â€”
    # see _chart_click_filter). Merged in alongside the dropdown filters above; dropdowns win
    # on a field-path collision.
    chart_filters = st.session_state.get("ct_chart_filters", {})
    for path, val in chart_filters.items():
        active_filters.setdefault(path, val)

    if active_filters:
        chips = " Â· ".join(
            f'<span style="background:{_C["accent"]}18;color:{_C["accent"]};'
            f'padding:2px 8px;border-radius:20px;font-size:0.72rem;font-weight:700;">'
            f'{_esc(v)}{" âœ•" if p in chart_filters else ""}</span>'
            for p, v in active_filters.items()
        )
        if st.button("âœ• Clear filters", key="ct_clear_f"):
            for k in ["ct_seg_f", "ct_city_f", "ct_route_f", "ct_nps_f"]:
                if k in st.session_state:
                    del st.session_state[k]
            st.session_state["ct_chart_filters"] = {}
            st.rerun()
        st.markdown(f'Filters: {chips}', unsafe_allow_html=True)

    return active_filters


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MAIN ENTRY
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def render_concept_testing(
    proj: dict,
    base_path: Path,
    call_openrouter_fn: Callable,
) -> None:
    proj_id      = proj.get("id", "karat-coindcx")
    proj_name    = proj.get("name", proj.get("id", "Concept Test Study"))
    matrices_dir = base_path / "data" / "projects" / proj_id / "matrices"
    findings_dir = base_path / "data" / "projects" / proj_id / "findings"
    schema_dir   = base_path / "data" / "projects" / proj_id / "schema"

    all_matrices = _load_matrices(str(matrices_dir))
    if not all_matrices:
        st.warning(f"No matrix files found in `{matrices_dir}`. Run extraction first.")
        return

    # Quality gate: "critical"-quality extractions (verbatim fidelity check failed badly) are
    # excluded from every chart's aggregate data, not just flagged â€” this used to be advisory
    # only (a badge in Extraction Studio) while the broken data rendered into charts identically
    # to clean respondents.
    from infoleap.skills.project_extractor import gate_matrices_by_quality
    all_matrices, _excluded_matrices = gate_matrices_by_quality(all_matrices)
    if _excluded_matrices:
        st.warning(
            f"âš  {len(_excluded_matrices)} respondent(s) excluded from every chart below â€” "
            f"critical-quality extraction (verbatim fidelity check failed badly): "
            + ", ".join(m.get("doc_id", "?") for m in _excluded_matrices)
            + ". Re-run extraction for these files in Extraction Studio."
        )
    if not all_matrices:
        st.warning("All matrices for this project are critical-quality â€” nothing to render. "
                   "Re-run extraction in Extraction Studio.")
        return

    # Load ui_config.json
    cfg: dict = {}
    cfg_path = schema_dir / "ui_config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Global header + filters
    active_filters = _render_header(all_matrices, proj)
    matrices = all_matrices
    for path, val in active_filters.items():
        matrices = [m for m in matrices if str(_get(m, path) or "") == val]

    if not matrices:
        st.warning(
            f"No respondents match active filters. "
            f"({len(all_matrices)} total â€” use Clear filters to reset.)"
        )
        return

    fd = str(findings_dir)

    # Config-driven path â€” active when ui_config.json has a "tabs" array
    if cfg.get("tabs"):
        _render_from_config(proj, cfg, matrices, all_matrices, fd,
                            call_openrouter_fn, active_filters)
        return

    # â”€â”€ Fallback: legacy hardcoded render â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    n = len(matrices)
    _has_gold_fields = any(
        _get(m, "gold_behavior.formats_owned") or _get(m, "gold_behavior.gold_role")
        for m in matrices[:5]
    )

    if _has_gold_fields:
        tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
            "ðŸ‘¥ Respondent Profiles",
            "ðŸª™ Category Context",
            "ðŸ§ª Concept Testing",
            "âš–ï¸ Route Comparison",
            "ðŸ¦ Trust & Adoption",
            "ðŸ” Per Respondent",
            "ðŸ“‹ Study Report",
        ])
        with tab2:
            _render_gold_category(matrices, fd, call_openrouter_fn, active_filters, proj_name)
    else:
        tab1, tab3, tab4, tab5, tab6, tab7 = st.tabs([
            "ðŸ‘¥ Respondent Profiles",
            "ðŸ§ª Concept Testing",
            "âš–ï¸ Route Comparison",
            "ðŸ¦ Trust & Adoption",
            "ðŸ” Per Respondent",
            "ðŸ“‹ Study Report",
        ])

    with tab1:
        _render_profiles(matrices, fd, call_openrouter_fn, active_filters, proj_name)
    with tab3:
        _render_concept_testing(matrices, fd, call_openrouter_fn, active_filters, proj_name)
    with tab4:
        _render_route_comparison(matrices, fd, call_openrouter_fn, active_filters, proj_name)
    with tab5:
        _render_brand_trust(matrices, fd, call_openrouter_fn, active_filters, proj_name)
    with tab6:
        _render_per_respondent(matrices, all_matrices)
    with tab7:
        _render_study_report(matrices, fd, call_openrouter_fn, active_filters, proj_name)
