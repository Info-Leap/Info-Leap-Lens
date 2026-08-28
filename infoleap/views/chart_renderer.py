"""
Chart Renderer — All Visualisation Logic (Extracted from chat.py)
=================================================================
Provides two modes:
  1. **chart_spec-driven** — LLM returns a JSON spec alongside SQL; we render exactly
     what was requested (bar, funnel, pie, grouped_bar, nps_waterfall, etc.)
  2. **heuristic fallback** — if chart_spec is absent or ``{"type": "auto"}``, the
     original column-name heuristic logic selects the best chart.

Public API:
  parse_chart_spec(raw_output)  → (sql, chart_spec_dict)
  render_result(df, question, chart_spec=None)  → None  (renders via st.*)
  render_table(df)              → None  (formatted st.dataframe)
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ═══════════════════════════════════════════════════════════════════════════════
# CHART SPEC PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_chart_spec(raw_output: str) -> tuple[str, dict]:
    """
    Split LLM output into SQL and chart_spec.

    The LLM is instructed to append a ``CHART: {...}`` line after the SQL.
    If missing or malformed, falls back to ``{"type": "auto"}``.

    Args:
        raw_output: Raw LLM response (SQL + optional CHART line).

    Returns:
        (sql_string, chart_spec_dict)
    """
    if "CHART:" in raw_output:
        parts = raw_output.split("CHART:", 1)
        sql = parts[0].strip()
        try:
            chart_spec = json.loads(parts[1].strip())
        except (json.JSONDecodeError, IndexError):
            chart_spec = {"type": "auto"}
    else:
        sql = raw_output.strip()
        chart_spec = {"type": "auto"}
    return sql, chart_spec


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

DETAIL_MARKERS = {"respondent_id", "resp_name", "device_id", "interviewer", "response_text"}

SKIP_COLS = {
    "respondent_id", "brand_id", "appliance_id", "date_id",
    "city_id", "zone_id", "id", "purchase_rank",
}

# Unified palette — matches brand_health.py exactly
COLORS = {
    "primary":   "#1a5d4d",   # dark green  (TOM / main bars)
    "secondary": "#30a76a",   # mid green   (SPONT / secondary)
    "tertiary":  "#86efac",   # light green (AIDED / tertiary)
    "success":   "#22c55e",
    "warning":   "#fbbf24",
    "danger":    "#ef4444",
    "info":      "#0ea5e9",
    # Multi-series palette — greens first, then supporting colours
    "palette": [
        "#1a5d4d", "#30a76a", "#86efac", "#0ea5e9",
        "#fbbf24", "#ef4444", "#8b5cf6", "#f97316",
        "#06b6d4", "#ec4899",
    ],
    # NPS-specific
    "nps_promoter":  "#22c55e",
    "nps_passive":   "#fbbf24",
    "nps_detractor": "#ef4444",
}

# Shared layout defaults applied to every chart
_LAYOUT = dict(
    template="plotly_white",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, Arial, sans-serif", size=13),
    margin=dict(l=10, r=80, t=44, b=20),
)


def extract_key_metrics(df: pd.DataFrame) -> list[tuple[str, str]]:
    """Extract top 3-5 key metrics from the DataFrame without nonsensical summing."""
    mcols = metric_cols(df)[:5]
    results = []
    
    # If it's a ranking/long table, summing percentages is misleading.
    for c in mcols:
        cl = c.lower()
        # SKIP summing for percentage/share/score/rank columns
        if any(x in cl for x in ["pct", "percent", "share", "score", "rank", "nps", "avg", "mean"]):
            if len(df) == 1:
                v = df.iloc[0][c]
            else:
                # For long tables, we don't show column-wise sums of scores
                continue 
        else:
            # It's a count/total - summing is logical for aggregate base
            v = df[c].sum() if len(df) > 1 else df.iloc[0][c]

        if isinstance(v, (int, float, np.integer, np.floating)):
            if isinstance(v, (float, np.floating)):
                results.append((c, f"{v:.1f}"))
            else:
                results.append((c, f"{v:,}"))
        else:
            results.append((c, str(v)))
            
    return results[:4]


def has_col(df: pd.DataFrame, *frags: str) -> str | None:
    """Return the first column whose name contains any of the given fragments."""
    for col in df.columns:
        if any(f.lower() in col.lower() for f in frags):
            return col
    return None


def metric_cols(df: pd.DataFrame) -> list[str]:
    """Return all numeric columns that are likely metrics (not IDs)."""
    seen: set[str] = set()
    result: list[str] = []
    for c, dt in zip(df.columns, df.dtypes):
        if c in seen:
            continue
        seen.add(c)
        if (pd.api.types.is_numeric_dtype(dt)
                and c.lower() not in SKIP_COLS
                and not c.lower().endswith("_id")):
            result.append(c)
    return result


def string_cols(df: pd.DataFrame) -> list[str]:
    """Return all string/object/categorical columns."""
    seen: set[str] = set()
    result: list[str] = []
    for c, dt in zip(df.columns, df.dtypes):
        if c in seen:
            continue
        seen.add(c)
        if (dt == object
                or pd.api.types.is_string_dtype(dt)
                or isinstance(dt, pd.CategoricalDtype)):
            result.append(c)
    return result


def _col_label(col_name: str) -> str:
    """Convert column_name to 'Column Name' for display."""
    return col_name.replace("_", " ").title()


# ═══════════════════════════════════════════════════════════════════════════════
# INDIVIDUAL CHART RENDERERS
# ═══════════════════════════════════════════════════════════════════════════════

def _render_metric_cards(df: pd.DataFrame, **_kw) -> None:
    """Single-row scalar results as metric cards."""
    cols_ui = st.columns(min(len(df.columns), 4))
    for i, col in enumerate(df.columns):
        v = df.iloc[0, i]
        with cols_ui[i % 4]:
            if isinstance(v, float):
                st.metric(_col_label(col), f"{v:.1f}")
            elif isinstance(v, int):
                st.metric(_col_label(col), f"{v:,}")
            else:
                st.metric(_col_label(col), str(v))


def _render_bar(df: pd.DataFrame, x: str | None = None, y: str | None = None,
                title: str | None = None, orientation: str = "h", **_kw) -> None:
    """Bar chart — horizontal by default for category × metric data.

    Pass ``orientation="v"`` for a vertical bar chart (see ``_render_v_bar``).
    """
    scols = string_cols(df)
    mcols = metric_cols(df)
    if not scols or not mcols:
        render_table(df)
        return

    # BETTER HEURISTIC: Prefer descriptive labels over generic ones
    label_col = x if x and x in df.columns else (
        has_col(df, "brand", "appliance", "city", "zone", "gender", "age") or scols[0]
    )

    value_col = y if y and y in df.columns else (
        has_col(df, "pct", "percent", "share", "score", "nps")
        or has_col(df, "count", "n", "total", "mentions")
        or mcols[0]
    )

    if not title:
        title = f"{_col_label(value_col)} by {_col_label(label_col)}"

    # Ensure consistent order with table
    df_s = df.copy()

    fmt = "%.1f" if df[value_col].dtype == float else "%d"
    is_vertical = orientation == "v"
    bar_x, bar_y = (label_col, value_col) if is_vertical else (value_col, label_col)
    fig = px.bar(
        df_s, x=bar_x, y=bar_y, orientation=orientation, text=value_col,
        color_discrete_sequence=[COLORS["primary"]],
        title=title,
        category_orders={label_col: df_s[label_col].tolist()}
    )
    fig.update_traces(texttemplate=f"%{{text:{fmt}}}", textposition="outside",
                      marker_line_width=0)
    layout_kwargs = dict(
        height=450 if is_vertical else max(350, len(df_s) * 36),
        showlegend=False,
    )
    if is_vertical:
        layout_kwargs.update(xaxis_title="", yaxis_title=_col_label(value_col))
    else:
        # First category in df_s stays on top (matches pre-adapter behavior) —
        # Plotly's default for horizontal bars puts the first category at the
        # bottom, which silently inverts caller-intended ordering otherwise.
        layout_kwargs.update(xaxis_title=_col_label(value_col), yaxis_title="",
                              yaxis=dict(autorange="reversed"))
    fig.update_layout(**_LAYOUT, **layout_kwargs)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, use_container_width=True)
    with st.expander("Data table", expanded=False):
        render_table(df)


def _render_v_bar(df: pd.DataFrame, x: str | None = None, y: str | None = None,
                title: str | None = None, **_kw) -> None:
    """Vertical bar chart — thin wrapper around ``_render_bar`` with orientation="v"."""
    _render_bar(df, x=x, y=y, title=title, orientation="v", **_kw)


def _render_grouped_bar(df: pd.DataFrame, x: str | None = None,
                        y: str | None = None, color: str | None = None,
                        title: str | None = None, **_kw) -> None:
    """Side-by-side grouped bar chart for brand/entity comparisons."""
    scols = string_cols(df)
    mcols = metric_cols(df)
    if not scols or not mcols:
        render_table(df)
        return

    x_col = x if x and x in df.columns else (
        has_col(df, "brand", "appliance", "city", "zone") or scols[0]
    )

    # For awareness funnel: prefer pct columns over count columns
    pct_mcols = [c for c in mcols if any(t in c.lower() for t in ["pct", "percent"])]
    display_mcols = pct_mcols[:4] if pct_mcols else mcols[:4]

    # If we have multiple metrics and no color grouping, melt for multi-metric view
    if len(display_mcols) >= 2 and not color:
        df_melted = df.melt(id_vars=[x_col], value_vars=display_mcols,
                            var_name="Metric", value_name="Value")
        fig = px.bar(
            df_melted, x=x_col, y="Value", color="Metric",
            barmode="group", text="Value",
            color_discrete_sequence=COLORS["palette"],
            title=title or f"Comparison by {_col_label(x_col)}",
            category_orders={x_col: df[x_col].tolist()} # FORCE ORDER
        )
    else:
        y_col = y if y and y in df.columns else mcols[0]
        color_col = color if color and color in df.columns else (
            scols[1] if len(scols) > 1 else None
        )
        fig = px.bar(
            df, x=x_col, y=y_col, color=color_col,
            barmode="group", text=y_col,
            color_discrete_sequence=COLORS["palette"],
            title=title or f"{_col_label(y_col)} by {_col_label(x_col)}",
            category_orders={x_col: df[x_col].tolist()} # FORCE ORDER
        )

    fmt = "%.1f" if any(df[c].dtype == float for c in mcols) else "%d"
    fig.update_traces(texttemplate=f"%{{text:{fmt}}}", textposition="outside",
                      marker_line_width=0)
    fig.update_layout(
        **_LAYOUT,
        height=max(400, len(df) * 44),
        legend=dict(orientation="h", y=1.12),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, use_container_width=True)
    with st.expander("Data table", expanded=False):
        render_table(df)


def _render_stacked_bar(df: pd.DataFrame, x: str | None = None,
                        title: str | None = None, **_kw) -> None:
    """Stacked horizontal bar — useful for composition breakdowns."""
    scols = string_cols(df)
    mcols = metric_cols(df)
    if not scols or not mcols:
        render_table(df)
        return

    x_col = x if x and x in df.columns else scols[0]
    fig = go.Figure()
    for i, mc in enumerate(mcols[:5]):
        fig.add_bar(
            name=_col_label(mc), x=df[mc], y=df[x_col],
            orientation="h",
            marker_color=COLORS["palette"][i % len(COLORS["palette"])],
        )
    fig.update_layout(
        **_LAYOUT,
        barmode="stack",
        title=title or f"Breakdown by {_col_label(x_col)}",
        height=max(350, len(df) * 38),
        legend=dict(orientation="h", y=1.1),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, use_container_width=True)
    with st.expander("Data table", expanded=False):
        render_table(df)


def _render_funnel(df: pd.DataFrame, x: str | None = None, y: str | None = None,
                   title: str | None = None, **_kw) -> None:
    """Funnel chart — TOM → SPONT → AIDED awareness progression."""
    scols = string_cols(df)
    mcols = metric_cols(df)
    if not scols or not mcols:
        render_table(df)
        return

    stage_col = x if x and x in df.columns else (has_col(df, "stage") or scols[0])
    value_col = y if y and y in df.columns else (
        has_col(df, "pct", "percent", "count") or mcols[0]
    )

    # Sort funnel stages in logical order if "stage" column detected
    stage_order = {"TOM": 0, "SPONT": 1, "AIDED": 2, "TOTAL": 3}
    if stage_col and has_col(df, "stage"):
        df = df.copy()
        df["_sort"] = df[stage_col].str.upper().map(stage_order).fillna(99)
        df = df.sort_values("_sort").drop(columns=["_sort"])

    fig = px.funnel(
        df, x=value_col, y=stage_col,
        color_discrete_sequence=[COLORS["primary"], COLORS["secondary"], COLORS["tertiary"]],
        title=title or "Awareness Funnel",
    )
    fig.update_layout(
        **_LAYOUT,
        height=max(320, len(df) * 90),
    )
    st.plotly_chart(fig, use_container_width=True)
    with st.expander("Data table", expanded=False):
        render_table(df)


def _render_pie(df: pd.DataFrame, x: str | None = None, y: str | None = None,
                title: str | None = None, hole: float = 0.35, **_kw) -> None:
    """Pie / donut chart for share/percentage distributions."""
    scols = string_cols(df)
    mcols = metric_cols(df)
    if not scols or not mcols:
        render_table(df)
        return

    label_col = x if x and x in df.columns else (
        has_col(df, "brand", "appliance", "city", "zone") or scols[0]
    )
    value_col = y if y and y in df.columns else (
        has_col(df, "pct", "percent", "share") or mcols[0]
    )

    fig = px.pie(
        df, names=label_col, values=value_col,
        color_discrete_sequence=COLORS["palette"],
        title=title or f"{_col_label(value_col)} Distribution",
        hole=hole,
        category_orders={label_col: df[label_col].tolist()} # FORCE ORDER
    )
    fig.update_traces(textinfo="label+percent", textposition="outside",
                      marker=dict(line=dict(color="white", width=2)))
    fig.update_layout(**_LAYOUT, height=460)
    st.plotly_chart(fig, use_container_width=True)
    with st.expander("Data table", expanded=False):
        render_table(df)


def _render_donut(df: pd.DataFrame, **kwargs) -> None:
    """Donut chart — thin wrapper around _render_pie forcing a larger hole."""
    # 0.5 (vs. _render_pie's default 0.35 slight-inset pie) carves a hole wide
    # enough to read as a distinct "donut" shape rather than a pie with a dimple.
    kwargs["hole"] = 0.5
    _render_pie(df, **kwargs)


def _render_nps_waterfall(df: pd.DataFrame, title: str | None = None, **_kw) -> None:
    """NPS stacked horizontal bar — Detractors | Passives | Promoters."""
    p = has_col(df, "promoter")
    d = has_col(df, "detractor")
    pas = has_col(df, "passive")
    n_col = has_col(df, "brand_name", "brand")
    nps_c = has_col(df, "nps", " nps")

    if not (p and d and n_col):
        render_table(df)
        return

    rows = len(df)
    df_s = df.sort_values(nps_c if nps_c else p, ascending=True)
    fig = go.Figure()
    fig.add_bar(name="Detractors", x=df_s[d], y=df_s[n_col],
                orientation="h", marker_color=COLORS["nps_detractor"])
    if pas:
        fig.add_bar(name="Passives", x=df_s[pas], y=df_s[n_col],
                    orientation="h", marker_color=COLORS["nps_passive"])
    fig.add_bar(name="Promoters", x=df_s[p], y=df_s[n_col],
                orientation="h", marker_color=COLORS["nps_promoter"])
    fig.update_layout(
        **_LAYOUT,
        barmode="stack", title=title or "NPS Breakdown",
        height=max(320, rows * 38),
        legend=dict(orientation="h", y=1.1),
    )
    st.plotly_chart(fig, use_container_width=True)
    with st.expander("Data table", expanded=False):
        render_table(df)


def _render_line(df: pd.DataFrame, x: str | None = None, y: str | None = None,
                 color: str | None = None, title: str | None = None, **_kw) -> None:
    """Time-series line chart with markers."""
    mcols = metric_cols(df)
    time_col = x if x and x in df.columns else (
        has_col(df, "month_name", "month_num", "interview_month", "quarter")
    )
    y_col = y if y and y in df.columns else (
        has_col(df, "pct", "percent", "share")
        or has_col(df, "count", "n", "mentions", "aware", "owners")
        or (mcols[0] if mcols else None)
    )
    color_col = color if color and color in df.columns else (
        has_col(df, "brand_name", "brand", "appliance_name",
                "city_name", "zone_name", "gender")
    )
    if color_col == time_col:
        color_col = None

    if not time_col or not y_col:
        render_table(df)
        return

    fig = px.line(
        df, x=time_col, y=y_col, color=color_col, markers=True,
        color_discrete_sequence=COLORS["palette"],
        title=title or f"{_col_label(y_col)} over Time",
    )
    fig.update_layout(**_LAYOUT, height=430)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0fdf4")
    st.plotly_chart(fig, use_container_width=True)
    with st.expander("Data table", expanded=False):
        render_table(df)


def _render_radar(df: pd.DataFrame, theta: list | None = None, data: list | None = None,
                  title: str | None = None, **_kw) -> None:
    """Radar chart for imagery profiles."""
    if theta is None or data is None:
        st.info("Missing theta or data for radar chart.")
        return
    colors = [COLORS["primary"], COLORS["secondary"], COLORS["info"]]
    fig = go.Figure()
    for i, d in enumerate(data):
        fig.add_trace(go.Scatterpolar(
            r=d["r"], theta=theta, fill="toself", name=d["name"],
            line_color=colors[i % len(colors)],
            fillcolor=colors[i % len(colors)].replace("#", "rgba(") + ",0.15)" if "#" in colors[i % len(colors)] else colors[i % len(colors)],
        ))
    fig.update_layout(
        **_LAYOUT,
        polar=dict(radialaxis=dict(visible=True, gridcolor="#dcfce7")),
        showlegend=True,
        title=title or "Brand Signature Comparison",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_quadrant(df: pd.DataFrame, points: list | None = None,
                     layout: dict | None = None, title: str | None = None, **_kw) -> None:
    """Strategic quadrant chart."""
    if points is None:
        st.info("Missing points for quadrant chart.")
        return
    pts = pd.DataFrame(points)
    fig = px.scatter(
        pts, x="x", y="y", text="label",
        color_discrete_sequence=[COLORS["primary"]],
        title=title or "Strategic Map",
    )
    fig.update_traces(textposition="top center", marker_size=10)
    l_x_mid = layout.get("x_mid", 4.0) if layout else 4.0
    l_y_mid = layout.get("y_mid", 50.0) if layout else 50.0
    fig.add_vline(x=l_x_mid, line_dash="dash", line_color="#9ca3af")
    fig.add_hline(y=l_y_mid, line_dash="dash", line_color="#9ca3af")
    fig.update_layout(**_LAYOUT, height=460)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TABLE RENDERER
# ═══════════════════════════════════════════════════════════════════════════════

def render_table(df: pd.DataFrame) -> None:
    """Formatted st.dataframe with high-readability column config."""
    cfg: dict[str, Any] = {}
    for col in df.columns:
        cl, lbl = col.lower(), _col_label(col)
        if any(x in cl for x in ["pct", "percent", "share", " nps", "avg", "mean"]):
            cfg[col] = st.column_config.NumberColumn(lbl, format="%.1f%%" if "pct" in cl or "percent" in cl else "%.1f")
        elif any(x in cl for x in ["count", "_n", "total", "mentions", "owners",
                                     "aware", "female", "male", "raters"]):
            cfg[col] = st.column_config.NumberColumn(lbl, format="%d")
        elif any(x in cl for x in ["score", "rank"]):
            cfg[col] = st.column_config.NumberColumn(lbl, format="%.1f")
        else:
            cfg[col] = st.column_config.TextColumn(lbl)
            
    st.dataframe(df, use_container_width=True, column_config=cfg, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# CHART TYPE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

_CHART_REGISTRY: dict[str, Any] = {
    "bar":           _render_bar,
    "stacked_bar":   _render_stacked_bar,
    "grouped_bar":   _render_grouped_bar,
    "funnel":        _render_funnel,
    "pie":           _render_pie,
    "nps_waterfall": _render_nps_waterfall,
    "line":          _render_line,
    "metric_cards":  _render_metric_cards,
    "table":         render_table,
    "radar":         _render_radar,
    "quadrant":      _render_quadrant,
    "h_bar":         _render_bar,   # alias — _render_bar is already horizontal orientation
    "v_bar":         _render_v_bar,
    "donut":         _render_donut,
}


# ═══════════════════════════════════════════════════════════════════════════════
# HEURISTIC CHART SELECTION (fallback when chart_spec is "auto")
# ═══════════════════════════════════════════════════════════════════════════════

def _has_awareness_funnel_cols(df: pd.DataFrame) -> bool:
    """Detect full awareness funnel output (has tom + spont + total columns)."""
    cols_lower = [c.lower() for c in df.columns]
    has_tom = any("tom" in c for c in cols_lower)
    has_spont = any("spont" in c for c in cols_lower)
    has_total = any("total" in c for c in cols_lower)
    return has_tom and has_spont and has_total


def _auto_select_chart(df: pd.DataFrame, question: str) -> str:
    """Pick the best chart type using column-name heuristics."""
    rows = len(df)
    mcols = metric_cols(df)
    scols = string_cols(df)
    q = question.lower()

    # Detect detail/row-level data (always table)
    if any(c in df.columns for c in DETAIL_MARKERS):
        return "table"

    # NPS waterfall (check BEFORE metric_cards — a single-row NPS is still a waterfall)
    if has_col(df, "promoter") and has_col(df, "detractor") and rows <= 40:
        return "nps_waterfall"

    # Single scalar
    if rows == 1 and len(df.columns) == 1:
        return "metric_cards"

    # Single-row with multiple columns
    if rows == 1 and len(df.columns) <= 8:
        return "metric_cards"

    # Awareness funnel data (tom + spont + total cols) — always grouped_bar for brand comparison
    if _has_awareness_funnel_cols(df) and scols and rows >= 2:
        return "grouped_bar"

    # Funnel detection (stage column + funnel keywords)
    stage_col = has_col(df, "stage")
    if stage_col and any(w in q for w in ["funnel", "progression", "stages"]):
        return "funnel"
    # Also auto-detect funnel when result has a 'stage' column with TOM/SPONT/AIDED values
    if stage_col and rows <= 10:
        stage_vals = df[stage_col].astype(str).str.upper().unique()
        if any(s in stage_vals for s in ["TOM", "SPONT", "AIDED"]):
            return "funnel"

    # Time-series
    time_col = has_col(df, "month_name", "month_num", "interview_month", "quarter")
    if time_col and mcols and rows <= 50 and "interview_date" not in df.columns:
        return "line"

    # Multi-metric comparison (grouped bar): compare/vs keywords OR multi-metric + multi-row
    if scols and len(mcols) >= 2 and rows >= 2:
        if any(w in q for w in ["compare", "vs", "side by side", "versus", "across zone",
                                  "across city", "by zone", "by city", "by brand"]):
            return "grouped_bar"
        # Multi-metric with few rows and multiple pct columns → grouped bar for clarity
        pct_mcols = [c for c in mcols if any(x in c.lower() for x in ["pct", "percent"])]
        if len(pct_mcols) >= 2 and rows <= 20:
            return "grouped_bar"

    # Pie for percentage distributions
    if any(w in q for w in ["distribution", "share", "proportion", "pie"]):
        if scols and mcols and 2 <= rows <= 10:
            return "pie"

    # Default: horizontal bar for category × metric
    if scols and mcols and (rows <= 30 or any(
        w in q for w in ["chart", "graph", "plot", "visual", "bar", "show me",
                          "rank", "ranking", "top", "bottom"]
    )):
        return "bar"

    return "table"


def select_chart_type_llm(df: pd.DataFrame, question: str, call_llm_fn=None) -> str:
    """Ask the LLM to pick a chart type from the real registry, given the data shape.

    Never trusts the LLM blindly: falls back to the existing column-heuristic
    (_auto_select_chart) if the LLM is unavailable, returns empty, or names a
    type that isn't actually in _CHART_REGISTRY. This keeps chart selection
    dynamic without letting a bad LLM answer break the page.
    """
    if call_llm_fn is None:
        try:
            from infoleap.views.quote_explorer import _call_llm_pinned as call_llm_fn
        except Exception:
            # Broad on purpose: quote_explorer.py does real work at import time
            # (Streamlit session state, DB path resolution, ProjectManager()) that
            # can fail outside an active script context for reasons that have
            # nothing to do with whether a chart-type-selector callable exists.
            # Any failure here just means "no LLM available" — fall back.
            return _auto_select_chart(df, question)

    scols, mcols = string_cols(df), metric_cols(df)
    prompt = (
        f"Data columns: categorical={scols}, numeric={mcols}, rows={len(df)}.\n"
        f"Question/context: {question}\n"
        f"Available chart types: {', '.join(sorted(_CHART_REGISTRY.keys()))}\n"
        f"Reply with exactly one chart type name from that list, nothing else."
    )
    try:
        raw = (call_llm_fn(prompt, system="Chart type selector. Reply with one word only.") or "").strip().lower()
    except Exception:
        # Network/timeout/API errors must never break the render path — fall back.
        return _auto_select_chart(df, question)
    if raw in _CHART_REGISTRY:
        return raw
    return _auto_select_chart(df, question)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

import re as _re  # needed for chart_code cleaning

def render_result(
    df: pd.DataFrame,
    question: str,
    chart_spec: dict | None = None,
    chart_code: str | None = None,
) -> None:
    """
    Render a query result.
    Priority: chart_code (agent Python) → chart_spec (JSON) → heuristic auto-select.

    Args:
        df:          The query result DataFrame.
        question:    The original user question (for heuristic chart selection).
        chart_spec:  Optional JSON chart spec from the LLM.
        chart_code:  Optional Python Plotly code from the agent (run_python tool).
    """
    if df is None or df.empty:
        st.info("Query returned no rows.")
        return

    # Path 1: Execute agent-generated Python Plotly code
    if chart_code and df is not None and not df.empty:
        try:
            clean = chart_code.strip()
            clean = clean.replace("```python", "").replace("```", "")
            clean = _re.sub(r"\.show\(\s*\)", "", clean)
            scope = {
                "df": df, "pd": pd, "px": px, "np": np, "go": go,
            }
            exec(clean, {}, scope)
            if "fig" in scope and scope["fig"] is not None:
                fig = scope["fig"]
                fig.update_layout(**_LAYOUT)
                fig.update_xaxes(showgrid=False)
                fig.update_yaxes(showgrid=False)
                st.plotly_chart(fig, use_container_width=True)
                return
        except Exception as e:
            import logging as _log
            _log.getLogger("LENS_3.2").warning(f"chart_code exec failed: {e}")
            # Fall through to chart_spec path

    # FORCED VALIDATION: If LLM suggests a chart that doesn't fit the columns, override it.
    actual_chart_type = _auto_select_chart(df, question)
    if chart_spec and chart_spec.get("type"):
        # Basic check: if columns are categorical but LLM wants a line chart
        if "line" in chart_spec["type"].lower():
             time_col = has_col(df, "month_name", "month_num", "interview_month", "quarter")
             if not time_col:
                 chart_spec["type"] = "bar" # Force bar for categorical comparisons

    # Render key metrics panel (if multi-row data with metrics)
    mcols = metric_cols(df)
    if len(df) > 1 and mcols:
        metrics = extract_key_metrics(df)
        if len(metrics) >= 2:
            cols = st.columns(min(len(metrics), 4))
            for i, (label, value) in enumerate(metrics[:4]):
                with cols[i]:
                    st.metric(_col_label(label), value)
            st.divider()

    # Determine chart type
    if chart_spec and chart_spec.get("type", "auto") != "auto":
        chart_type = chart_spec["type"]
    else:
        chart_type = _auto_select_chart(df, question)

    # Get renderer
    renderer = _CHART_REGISTRY.get(chart_type)
    if renderer is None:
        renderer = _CHART_REGISTRY.get(_auto_select_chart(df, question), render_table)

    # Build kwargs from chart_spec: pass everything except 'type'
    kwargs: dict[str, Any] = {}
    if chart_spec:
        kwargs = {k: v for k, v in chart_spec.items() if k != "type" and v is not None}

    # Render
    try:
        renderer(df, **kwargs)
    except Exception as e:
        st.warning(f"Chart rendering failed ({e}). Falling back to table.")
        render_table(df)
