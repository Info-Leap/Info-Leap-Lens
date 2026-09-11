def test_cache_key_changes_when_matrices_mtime_changes():
    from infoleap.views.quote_explorer import _insight_cache_key

    key_1 = _insight_cache_key("Crompton", mtime=1000.0)
    key_2 = _insight_cache_key("Crompton", mtime=2000.0)
    assert key_1 != key_2, "cache key must change when underlying data changes"


def test_cache_key_same_for_same_brand_and_mtime():
    from infoleap.views.quote_explorer import _insight_cache_key

    assert _insight_cache_key("Crompton", mtime=1000.0) == _insight_cache_key("Crompton", mtime=1000.0)


def test_cache_key_differs_by_brand():
    from infoleap.views.quote_explorer import _insight_cache_key

    assert _insight_cache_key("Crompton", mtime=1000.0) != _insight_cache_key("Bajaj", mtime=1000.0)


def test_generate_brand_insight_prunes_older_mtime_entries_for_same_brand():
    """Without pruning, every re-extraction adds a new key and the cache file
    grows forever. A fresh write for a brand must drop that brand's stale entries."""
    from unittest.mock import patch
    from infoleap.views import quote_explorer as qe

    stale_cache = {
        "Crompton_v4_1000": "old stale narrative",
        "Bajaj_v4_1000": "unrelated brand, must survive",
    }

    with patch.object(qe, "_load_insight_cache", return_value=dict(stale_cache)), \
         patch.object(qe, "_save_insight_cache") as mock_save, \
         patch.object(qe, "_matrices_mtime", return_value=2000.0), \
         patch.object(qe, "_call_openrouter_free", return_value="fresh narrative"):
        result = qe._generate_brand_transcript_insight("Crompton", {})

    assert result == "fresh narrative"
    saved_cache = mock_save.call_args[0][0]
    assert "Crompton_v4_1000" not in saved_cache, "stale entry for this brand must be pruned"
    assert saved_cache.get("Crompton_v4_2000") == "fresh narrative"
    assert saved_cache.get("Bajaj_v4_1000") == "unrelated brand, must survive"
