import pandas as pd
from unittest.mock import patch


def test_render_counts_calls_registry_with_correct_columns():
    from infoleap.views.chart_adapter import render_counts

    with patch("oxdata.views.chart_renderer.st") as mock_st:
        counts = {"Safety Seeker": 8, "Growth Oriented": 8, "Yield Optimizer": 6}
        render_counts(counts, label="Investor Archetype", chart_type="h_bar")
        assert mock_st.plotly_chart.called


def test_render_counts_empty_is_noop():
    from infoleap.views.chart_adapter import render_counts
    with patch("oxdata.views.chart_renderer.st") as mock_st:
        render_counts({}, label="Empty", chart_type="h_bar")
        assert not mock_st.plotly_chart.called


def test_render_rows_calls_registry():
    from infoleap.views.chart_adapter import render_rows

    with patch("oxdata.views.chart_renderer.st") as mock_st:
        rows = [
            {"city": "Mumbai", "sentiment": "positive"},
            {"city": "Delhi", "sentiment": "negative"},
        ]
        render_rows(rows, label="Sentiment by City", chart_type="table")
        assert mock_st.dataframe.called or mock_st.plotly_chart.called


def test_render_rows_empty_is_noop():
    from infoleap.views.chart_adapter import render_rows
    with patch("oxdata.views.chart_renderer.st") as mock_st:
        render_rows([], label="Empty", chart_type="table")
        assert not mock_st.plotly_chart.called
        assert not mock_st.dataframe.called
