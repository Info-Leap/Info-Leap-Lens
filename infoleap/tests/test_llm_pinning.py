from unittest.mock import patch, MagicMock


def test_call_llm_pinned_uses_primary_model_first():
    from infoleap.views import quote_explorer as qe

    calls = []
    def fake_urlopen(req, timeout):
        payload = req.data.decode()
        calls.append(payload)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"choices":[{"message":{"content":"ok"}}]}'
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("oxdata.views.quote_explorer._get_or_key", return_value="fake-key"), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = qe._call_llm_pinned("test prompt")
        assert result == "ok"
        assert qe._PINNED_MODEL in calls[0]


def test_call_llm_pinned_falls_back_on_primary_failure():
    from infoleap.views import quote_explorer as qe

    attempts = {"n": 0}
    def fake_urlopen(req, timeout):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TimeoutError("primary model timed out")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"choices":[{"message":{"content":"fallback ok"}}]}'
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("oxdata.views.quote_explorer._get_or_key", return_value="fake-key"), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = qe._call_llm_pinned("test prompt")
        assert result == "fallback ok"
        assert attempts["n"] == 2


def test_call_llm_pinned_falls_back_through_multiple_models():
    """Primary AND the first several fallbacks all fail â€” must keep walking the
    list, not just retry once. Regression guard for an off-by-one in the loop."""
    from infoleap.views import quote_explorer as qe

    attempts = {"n": 0}
    def fake_urlopen(req, timeout):
        attempts["n"] += 1
        if attempts["n"] <= 4:
            raise TimeoutError(f"model #{attempts['n']} timed out")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"choices":[{"message":{"content":"fifth model ok"}}]}'
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("oxdata.views.quote_explorer._get_or_key", return_value="fake-key"), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = qe._call_llm_pinned("test prompt")
        assert result == "fifth model ok"
        assert attempts["n"] == 5


def test_pinned_model_is_free_tier_not_paid():
    """This function's name (_call_openrouter_free) and _FREE_MODELS' own comment
    ('Strictly free models only') both promise no billing. The primary model must
    actually be one of the free-tier models, not a paid one slipped in as primary."""
    from infoleap.views import quote_explorer as qe

    assert qe._PINNED_MODEL in qe._FREE_MODELS, (
        f"_PINNED_MODEL={qe._PINNED_MODEL!r} is not in _FREE_MODELS â€” "
        "pinning a paid model here would silently start billing every narrative call"
    )


def test_call_openrouter_free_is_backcompat_wrapper_for_pinned():
    """Existing call sites throughout the codebase call _call_openrouter_free â€”
    it must still exist and behave identically to _call_llm_pinned."""
    from infoleap.views import quote_explorer as qe
    import inspect
    assert callable(qe._call_openrouter_free)
    # Must accept the same (prompt, system=...) signature existing callers use.
    sig = inspect.signature(qe._call_openrouter_free)
    assert "prompt" in sig.parameters
    assert "system" in sig.parameters
