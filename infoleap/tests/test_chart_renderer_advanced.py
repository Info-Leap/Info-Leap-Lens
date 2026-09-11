from unittest.mock import patch

import pandas as pd

from infoleap.views.chart_renderer import _CHART_REGISTRY, parse_chart_spec


def test_radar_and_quadrant_in_registry():
    assert "radar" in _CHART_REGISTRY, "Radar renderer missing"
    assert "quadrant" in _CHART_REGISTRY, "Quadrant renderer missing"


def test_parse_radar_spec():
    raw = 'SELECT * FROM test\nCHART: {"type": "radar", "theta": ["A"], "data": [{"name": "B", "r": [1]}]}'
    sql, spec = parse_chart_spec(raw)
    assert spec["type"] == "radar"


def test_h_bar_v_bar_donut_aliases_registered():
    assert "h_bar" in _CHART_REGISTRY, "h_bar alias missing"
    assert "v_bar" in _CHART_REGISTRY, "v_bar alias missing"
    assert "donut" in _CHART_REGISTRY, "donut alias missing"


def test_v_bar_renders_with_valid_data():
    with patch("oxdata.views.chart_renderer.st") as mock_st:
        df = pd.DataFrame({"category": ["A", "B", "C"], "value": [10, 20, 30]})
        _CHART_REGISTRY["v_bar"](df, x="category", y="value", title="test")
        assert mock_st.plotly_chart.called


def test_donut_has_hole_distinct_from_pie():
    with patch("oxdata.views.chart_renderer.st") as mock_st:
        df = pd.DataFrame({"category": ["A", "B"], "value": [40, 60]})
        _CHART_REGISTRY["donut"](df, x="category", y="value", title="test")
        assert mock_st.plotly_chart.called
        fig = mock_st.plotly_chart.call_args[0][0]
        assert fig.data[0].hole == 0.5


def test_v_bar_empty_data_falls_back_to_table():
    with patch("oxdata.views.chart_renderer.render_table") as mock_table, \
         patch("oxdata.views.chart_renderer.st"):
        df = pd.DataFrame({"only_numeric": [1, 2, 3]})  # no string/categorical column
        _CHART_REGISTRY["v_bar"](df)
        assert mock_table.called


def test_h_bar_preserves_caller_order_top_to_bottom():
    """First category in the input stays visually on top â€” Plotly's horizontal-bar
    default puts it on the bottom, which would silently invert caller-intended order."""
    with patch("oxdata.views.chart_renderer.st") as mock_st:
        df = pd.DataFrame({"category": ["First", "Second", "Third"], "count": [30, 20, 10]})
        _CHART_REGISTRY["h_bar"](df, x="category", y="count")
        fig = mock_st.plotly_chart.call_args[0][0]
        assert fig.layout.yaxis.autorange == "reversed"


def test_select_chart_type_llm_falls_back_to_heuristic_on_empty_response():
    import pandas as pd
    from infoleap.views.chart_renderer import select_chart_type_llm, _auto_select_chart

    df = pd.DataFrame({"category": ["A", "B"], "count": [5, 9]})

    def fake_llm(prompt, system=""):
        return ""  # simulate LLM unavailable

    result = select_chart_type_llm(df, "distribution of X", call_llm_fn=fake_llm)
    assert result == _auto_select_chart(df, "distribution of X")


def test_select_chart_type_llm_uses_valid_llm_choice():
    import pandas as pd
    from infoleap.views.chart_renderer import select_chart_type_llm, _CHART_REGISTRY

    df = pd.DataFrame({"category": ["A", "B"], "count": [5, 9]})

    def fake_llm(prompt, system=""):
        return "donut"

    result = select_chart_type_llm(df, "share of X", call_llm_fn=fake_llm)
    assert result == "donut"
    assert result in _CHART_REGISTRY


def test_select_chart_type_llm_rejects_hallucinated_type():
    import pandas as pd
    from infoleap.views.chart_renderer import select_chart_type_llm, _auto_select_chart

    df = pd.DataFrame({"category": ["A", "B"], "count": [5, 9]})

    def fake_llm(prompt, system=""):
        return "3d_exploding_pie"  # not a real registry key

    result = select_chart_type_llm(df, "share of X", call_llm_fn=fake_llm)
    assert result == _auto_select_chart(df, "share of X")


def test_select_chart_type_llm_falls_back_on_call_exception():
    import pandas as pd
    from infoleap.views.chart_renderer import select_chart_type_llm, _auto_select_chart

    df = pd.DataFrame({"category": ["A", "B"], "count": [5, 9]})

    def flaky_llm(prompt, system=""):
        raise TimeoutError("simulated network timeout")

    result = select_chart_type_llm(df, "share of X", call_llm_fn=flaky_llm)
    assert result == _auto_select_chart(df, "share of X")
