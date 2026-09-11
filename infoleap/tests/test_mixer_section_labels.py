def test_mixer_section_labels_exist_in_config():
    from infoleap.config.project_1 import MIXER_SECTION_LABELS
    assert isinstance(MIXER_SECTION_LABELS, dict)
    assert len(MIXER_SECTION_LABELS) == 5


def test_mixer_section_labels_match_expected_keys():
    from infoleap.config.project_1 import MIXER_SECTION_LABELS
    expected_keys = {"consumer_profiles", "brand_landscape", "pain_points", "aspiration_gaps", "purchase_journey"}
    assert set(MIXER_SECTION_LABELS.keys()) == expected_keys


def test_ethnographic_renderer_reads_labels_from_config_not_hardcoded():
    """Editing config/project_1.py's MIXER_SECTION_LABELS must change what
    ethnographic_renderer.py uses â€” proving it's not still hardcoded locally.
    _ETH_SIGNAL_MAP is an alias (same dict object) for MIXER_SECTION_LABELS,
    so mutating the config dict in place is immediately visible."""
    from infoleap.config import project_1
    from infoleap.views import ethnographic_renderer as eth

    assert eth._ETH_SIGNAL_MAP is project_1.MIXER_SECTION_LABELS

    original = dict(project_1.MIXER_SECTION_LABELS)
    try:
        project_1.MIXER_SECTION_LABELS["pain_points"] = "TOTALLY RENAMED LABEL"
        assert eth._ETH_SIGNAL_MAP["pain_points"] == "TOTALLY RENAMED LABEL"
    finally:
        project_1.MIXER_SECTION_LABELS.clear()
        project_1.MIXER_SECTION_LABELS.update(original)
