"""
Chart Adapter — bridges qual-analysis data shapes (Counter/dict/list-of-dict)
to chart_renderer.py's DataFrame-based render_result(), so qual_generic_renderer.py
and ethnographic_renderer.py stop hand-building Plotly figures and use the same
registry Ask Pulse uses.
"""
from __future__ import annotations

import pandas as pd

from oxdata.views.chart_renderer import render_result


def render_counts(counts: dict[str, int], label: str, chart_type: str = "h_bar",
                   question: str = "") -> None:
    """Render a label->count dict (e.g. from collections.Counter) as a chart.

    Args:
        counts: {category_label: count}.
        label: axis/series title, also used as the metric column name.
        chart_type: a _CHART_REGISTRY key, or "auto" to let the LLM pick.
    """
    if not counts:
        return
    df = pd.DataFrame(
        {"category": list(counts.keys()), label: list(counts.values())}
    )
    if chart_type == "auto":
        from oxdata.views.chart_renderer import select_chart_type_llm
        chart_type = select_chart_type_llm(df, question or label)
    render_result(df, question or label, chart_spec={"type": chart_type, "x": "category", "y": label, "title": label})


def render_rows(rows: list[dict], label: str, chart_type: str = "h_bar",
                 question: str = "") -> None:
    """Render a list of flat dicts (e.g. matrix-derived records) as a chart.

    Args:
        rows: list of flat dicts, each becoming one DataFrame row.
        label: chart title.
        chart_type: a _CHART_REGISTRY key, or "auto" to let the LLM pick.
    """
    if not rows:
        return
    df = pd.DataFrame(rows)
    if chart_type == "auto":
        from oxdata.views.chart_renderer import select_chart_type_llm
        chart_type = select_chart_type_llm(df, question or label)
    render_result(df, question or label, chart_spec={"type": chart_type, "title": label})
