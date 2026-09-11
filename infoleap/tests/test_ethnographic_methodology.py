from unittest.mock import patch, MagicMock


def test_loyalty_depth_pie_routes_through_chart_adapter():
    """The Loyalty Depth / Advocacy Likelihood pies must now call
    chart_renderer's registry (via chart_adapter.render_counts), not build
    their own px.pie figure directly."""
    from infoleap.views.ethnographic_renderer import _render_brand_landscape

    matrices = [
        {"respondent": {"brand_owned": "Crompton"},
         "brand_relationship": {"loyalty_depth": "high", "advocacy_likelihood": "likely"}},
    ] * 5 + [
        {"respondent": {"brand_owned": "Crompton"},
         "brand_relationship": {"loyalty_depth": "low", "advocacy_likelihood": "unlikely"}},
    ] * 3

    with patch("oxdata.views.chart_renderer.st") as mock_chart_st, \
         patch("oxdata.views.ethnographic_renderer.st") as mock_eth_st, \
         patch("oxdata.views.ethnographic_renderer._load_finding", return_value={}), \
         patch("oxdata.views.ethnographic_renderer._finding_block"):
        mock_eth_st.columns.return_value = (MagicMock(), MagicMock())
        _render_brand_landscape(matrices, "/tmp/fake_findings", lambda p, **kw: "")
        assert mock_chart_st.plotly_chart.called, \
            "expected chart_renderer.render_result to have fired via chart_adapter"


def test_nps_league_table_has_methodology_expander():
    from infoleap.views.ethnographic_renderer import _render_consumer_profiles

    matrices = [{"respondent": {"brand_owned": "Crompton", "journey_stage": "recent_buyer"},
                 "nps_signal": "promoter"}] * 5

    with patch("oxdata.views.ethnographic_renderer.st") as mock_st, \
         patch("oxdata.views.ethnographic_renderer._load_finding", return_value={}), \
         patch("oxdata.views.ethnographic_renderer._finding_block"), \
         patch("oxdata.views.ethnographic_renderer._full_verbatim_wall"):
        mock_st.columns.return_value = [MagicMock()]
        _render_consumer_profiles(matrices, "/tmp/fake_findings", lambda p, **kw: "")
        expander_calls = mock_st.expander.call_args_list
        assert any("calculat" in str(c).lower() for c in expander_calls), \
            "expected a 'How is this calculated?' style expander near the NPS chart"


def test_blame_attribution_has_methodology_expander():
    from infoleap.views.ethnographic_renderer import _render_brand_landscape

    matrices = [{"respondent": {"brand_owned": "Crompton"},
                 "self_blame_instances": ["x"], "product_blame_instances": []}] * 5

    with patch("oxdata.views.ethnographic_renderer.st") as mock_st, \
         patch("oxdata.views.ethnographic_renderer._load_finding", return_value={}), \
         patch("oxdata.views.ethnographic_renderer._finding_block"):
        mock_st.columns.return_value = (MagicMock(), MagicMock())
        _render_brand_landscape(matrices, "/tmp/fake_findings", lambda p, **kw: "")
        expander_calls = mock_st.expander.call_args_list
        assert any("calculat" in str(c).lower() for c in expander_calls), \
            "expected a 'How is this calculated?' style expander near Blame Attribution"
