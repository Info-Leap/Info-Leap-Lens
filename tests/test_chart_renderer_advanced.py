import pandas as pd
from oxdata.views.chart_renderer import _CHART_REGISTRY, parse_chart_spec

def test_radar_and_quadrant_in_registry():
    assert "radar" in _CHART_REGISTRY, "Radar renderer missing"
    assert "quadrant" in _CHART_REGISTRY, "Quadrant renderer missing"

def test_parse_radar_spec():
    raw = 'SELECT * FROM test\nCHART: {"type": "radar", "theta": ["A"], "data": [{"name": "B", "r": [1]}]}'
    sql, spec = parse_chart_spec(raw)
    assert spec["type"] == "radar"

def test_render_result_calls_radar():
    from unittest.mock import patch
    from oxdata.views.chart_renderer import render_result
    
    with patch("oxdata.views.chart_renderer.st") as mock_st:
        df = pd.DataFrame({"dummy": [1]})
        spec = {
            "type": "radar",
            "theta": ["Brand A", "Brand B"],
            "data": [{"name": "User 1", "r": [10, 20]}]
        }
        
        # This should not raise "Missing theta or data" info if kwargs are passed correctly
        render_result(df, "test question", chart_spec=spec)
        
        # Check that st.plotly_chart was called (indicating renderer was called with data)
        assert mock_st.plotly_chart.called

def test_render_result_calls_quadrant():
    from unittest.mock import patch
    from oxdata.views.chart_renderer import render_result
    
    with patch("oxdata.views.chart_renderer.st") as mock_st:
        df = pd.DataFrame({"dummy": [1]})
        spec = {
            "type": "quadrant",
            "points": [{"x": 10, "y": 20, "label": "Point A"}],
            "layout": {"x_mid": 15, "y_mid": 25}
        }
        
        render_result(df, "test question", chart_spec=spec)
        assert mock_st.plotly_chart.called
