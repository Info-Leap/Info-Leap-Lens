from unittest.mock import patch


def test_render_sec_distribution_uses_configured_chart_type():
    from infoleap.views.qual_generic_renderer import _render_sec_distribution

    matrices = [{"respondent": {"investor_archetype": "Safety Seeker"}}] * 8 + \
               [{"respondent": {"investor_archetype": "Growth Oriented"}}] * 8
    sec = {"fields": [{"path": "respondent.investor_archetype", "label": "Investor Archetype", "chart": "donut"}]}

    with patch("oxdata.views.chart_renderer.st") as mock_st:
        _render_sec_distribution(sec, matrices)
        assert mock_st.plotly_chart.called
        fig = mock_st.plotly_chart.call_args[0][0]
        assert fig.data[0].hole == 0.5, (
            "chart_type='donut' from ui_config must actually reach the renderer, "
            "not silently default to h_bar"
        )


def test_render_sec_distribution_defaults_to_h_bar_when_chart_key_missing():
    from infoleap.views.qual_generic_renderer import _render_sec_distribution

    matrices = [{"respondent": {"life_stage": "Young Professional"}}] * 5
    sec = {"fields": [{"path": "respondent.life_stage", "label": "Life Stage"}]}  # no "chart" key

    with patch("oxdata.views.chart_renderer.st") as mock_st:
        _render_sec_distribution(sec, matrices)
        assert mock_st.plotly_chart.called


def test_render_sec_distribution_empty_matrices_is_noop():
    from infoleap.views.qual_generic_renderer import _render_sec_distribution

    sec = {"fields": [{"path": "respondent.nonexistent_field", "label": "Nothing", "chart": "h_bar"}]}

    with patch("oxdata.views.chart_renderer.st") as mock_st:
        _render_sec_distribution(sec, [])
        assert not mock_st.plotly_chart.called
