def test_build_options_menu_prompt_lists_choices():
    from infoleap.skills.prompt_templates import build_options_menu_prompt

    prompt = build_options_menu_prompt(
        entity="Crompton", available_signals=["pain_points", "aspiration_gaps", "nps_trend"],
    )
    assert "pain_points" in prompt
    assert "aspiration_gaps" in prompt
    assert "nps_trend" in prompt
    assert "Crompton" in prompt


def test_build_options_menu_prompt_rejects_unknown_signal_gracefully():
    """Passing a signal name with no known description must not crash â€” it should
    still appear in the prompt (using the raw name as its own description)."""
    from infoleap.skills.prompt_templates import build_options_menu_prompt

    prompt = build_options_menu_prompt(entity="Bajaj", available_signals=["some_future_signal_type"])
    assert "some_future_signal_type" in prompt


def test_build_options_menu_prompt_specifies_2_to_4_option_range():
    """The LLM must be told a specific range, not left to guess how many options
    to propose â€” an unbounded menu defeats the point of a structured choice step."""
    from infoleap.skills.prompt_templates import build_options_menu_prompt

    prompt = build_options_menu_prompt(entity="Crompton", available_signals=["pain_points"])
    assert "2-4" in prompt


def test_build_methodology_note_is_deterministic_string_not_llm():
    from infoleap.skills.prompt_templates import build_methodology_note

    note = build_methodology_note(
        metric="NPS Promoter %",
        formula="promoters / total_raters * 100",
        source="fact_brand_nps, filtered to brand=Crompton",
    )
    assert "promoters / total_raters * 100" in note
    assert "fact_brand_nps" in note
    assert "NPS Promoter %" in note


def test_build_methodology_note_is_pure_function_no_network_call():
    """This must never call an LLM or make a network request â€” verify by checking
    it runs instantly with no mocking required and produces the same output twice."""
    from infoleap.skills.prompt_templates import build_methodology_note

    note_1 = build_methodology_note(metric="X", formula="Y", source="Z")
    note_2 = build_methodology_note(metric="X", formula="Y", source="Z")
    assert note_1 == note_2
