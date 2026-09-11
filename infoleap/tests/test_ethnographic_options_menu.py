from unittest.mock import patch, MagicMock


def test_render_options_menu_shows_llm_proposed_recommendations():
    from infoleap.views.ethnographic_renderer import _render_options_menu

    def fake_llm(prompt, system=""):
        return "1. pain_points â€” most complaints this wave\n2. brand_landscape â€” new entrant detected"

    with patch("oxdata.views.ethnographic_renderer.st") as mock_st:
        mock_st.session_state = {}
        recommended = _render_options_menu("mixer", fake_llm)
        assert mock_st.markdown.called or mock_st.info.called
        assert isinstance(recommended, list)


def test_render_options_menu_falls_back_gracefully_when_llm_unavailable():
    """If the LLM call fails/returns empty, must not crash â€” return an empty or
    default recommendation list so the page still renders all 6 tabs normally."""
    from infoleap.views.ethnographic_renderer import _render_options_menu

    def dead_llm(prompt, system=""):
        return ""

    with patch("oxdata.views.ethnographic_renderer.st") as mock_st:
        mock_st.session_state = {}
        recommended = _render_options_menu("mixer", dead_llm)
        assert isinstance(recommended, list)  # empty list is fine, must not raise


def test_render_options_menu_caches_in_session_state_no_repeat_llm_call():
    """Re-rendering (e.g. a Streamlit rerun from an unrelated widget interaction)
    must not re-call the LLM every time â€” cache the menu per project id."""
    from infoleap.views.ethnographic_renderer import _render_options_menu

    call_count = {"n": 0}
    def counting_llm(prompt, system=""):
        call_count["n"] += 1
        return "1. pain_points â€” reason"

    with patch("oxdata.views.ethnographic_renderer.st") as mock_st:
        mock_st.session_state = {}
        _render_options_menu("mixer", counting_llm)
        _render_options_menu("mixer", counting_llm)
        assert call_count["n"] == 1, "second call must hit the session_state cache, not re-call the LLM"


def test_render_options_menu_no_keys_match_returns_empty_list():
    """Raw LLM text with content but no recognizable signal key must not crash
    or accidentally recommend something â€” just return []."""
    from infoleap.views.ethnographic_renderer import _render_options_menu

    def off_topic_llm(prompt, system=""):
        return "1. something unrelated to any known signal\n2. also unrelated"

    with patch("oxdata.views.ethnographic_renderer.st") as mock_st:
        mock_st.session_state = {}
        recommended = _render_options_menu("mixer", off_topic_llm)
        assert recommended == []


def test_reorder_tabs_zero_recommended_keeps_all_six_unmarked():
    from infoleap.views.ethnographic_renderer import _reorder_tab_labels_by_recommendation

    labels = ["Consumer Profiles", "Brand Landscape", "Pain Points", "Aspiration & Need", "Purchase Journey", "Study Report"]
    ordered = _reorder_tab_labels_by_recommendation(labels, [])
    assert len(ordered) == 6
    assert ordered == labels, "no recommendations means no reordering/marking"


def test_reorder_tabs_all_recommended_keeps_all_six_marked():
    from infoleap.views.ethnographic_renderer import _reorder_tab_labels_by_recommendation

    labels = ["Consumer Profiles", "Brand Landscape", "Pain Points", "Aspiration & Need", "Purchase Journey", "Study Report"]
    all_keys = ["consumer_profiles", "brand_landscape", "pain_points", "aspiration_gaps", "purchase_journey"]
    ordered = _reorder_tab_labels_by_recommendation(labels, all_keys)
    assert len(ordered) == 6, "must not drop the un-recommendable 'Study Report' tab"
    assert "Study Report" in ordered


def test_reorder_tabs_by_recommendation_puts_recommended_first():
    from infoleap.views.ethnographic_renderer import _reorder_tab_labels_by_recommendation

    labels = ["Consumer Profiles", "Brand Landscape", "Pain Points", "Aspiration & Need", "Purchase Journey", "Study Report"]
    recommended_keys = ["pain_points", "brand_landscape"]
    ordered = _reorder_tab_labels_by_recommendation(labels, recommended_keys)
    assert len(ordered) == 6, "must not drop any tab"
    assert set(ordered) >= {"Consumer Profiles", "Brand Landscape", "Pain Points", "Aspiration & Need", "Purchase Journey", "Study Report"} or \
           all(any(base in lbl for base in labels) for lbl in ordered), "all original tabs must still be present, possibly with a marker prefix/suffix"
    # The two recommended ones should be the first two entries (order among them doesn't matter)
    first_two_base_names = [lbl.replace("â­ ", "").replace(" (Recommended)", "") for lbl in ordered[:2]]
    assert "Pain Points" in first_two_base_names
    assert "Brand Landscape" in first_two_base_names
