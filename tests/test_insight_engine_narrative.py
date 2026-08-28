from unittest.mock import patch, MagicMock
import pytest
from lens.analytics.insight_engine import InsightEngine

def test_generate_balanced_narrative_raw():
    engine = InsightEngine()
    # Using the keys expected by InsightEngine's current logic
    mock_data = {
        "brands": [
            {
                "brand_name": "TestBrand", 
                "aided": 80, 
                "current_use": 10, 
                "ever_used": 60, 
                "tom": 2,
                "imagery": [
                    {
                        "attribute": "Quality",
                        "importance_pct": 90,
                        "association_pct": 10
                    }
                ]
            }
        ],
        "base_n": 100
    }
    # Force no client to test raw fallback
    with patch.object(InsightEngine, "_get_client", return_value=None):
        narrative = engine.generate_executive_narrative(mock_data, "TestBrand")
        assert "TestBrand" in narrative
        assert "retention leak" in narrative.lower()
        assert "awareness gap" in narrative.lower()
        assert "unmet need" in narrative.lower()

def test_generate_balanced_narrative_mocked():
    engine = InsightEngine()
    mock_data = {
        "brands": [
            {
                "brand_name": "TestBrand", 
                "aided": 80, 
                "current_use": 10, 
                "ever_used": 60, 
                "tom": 2,
                "imagery": [
                    {
                        "attribute": "Quality",
                        "importance_pct": 90,
                        "association_pct": 10
                    }
                ]
            }
        ],
        "base_n": 100
    }
    
    with patch("google.genai.Client") as mock_client_class:
        mock_client = mock_client_class.return_value
        mock_response = MagicMock()
        mock_response.text = "TestBrand shows a significant retention leak and an unmet need for Quality despite high awareness."
        mock_client.models.generate_content.return_value = mock_response
        
        narrative = engine.generate_executive_narrative(mock_data, "TestBrand", api_key="test_key")
        assert "TestBrand" in narrative
        assert len(narrative) > 50
        assert "retention leak" in narrative.lower()

def test_generate_balanced_narrative_pct_keys():
    engine = InsightEngine()
    # Using _pct keys from user example
    mock_data = {
        "brands": [
            {
                "brand_name": "TestBrand", 
                "aided_pct": 80, 
                "current_use_pct": 10, 
                "ever_used_pct": 60, 
                "tom_pct": 2,
                "imagery": []
            }
        ],
        "base_n": 100
    }
    # Force no client to test raw fallback
    with patch.object(InsightEngine, "_get_client", return_value=None):
        narrative = engine.generate_executive_narrative(mock_data, "TestBrand")
        assert "TestBrand" in narrative
        assert "retention leak" in narrative.lower()
        assert "awareness gap" in narrative.lower()

def test_generate_balanced_narrative_from_plan():
    engine = InsightEngine()
    mock_data = {
        "brands": [{"brand_name": "TestBrand", "aided_pct": 80, "current_use_pct": 30, "ever_used_pct": 60, "imagery": []}],
        "base_n": 100
    }
    # Force no client to test raw fallback
    with patch.object(InsightEngine, "_get_client", return_value=None):
        narrative = engine.generate_executive_narrative(mock_data, "TestBrand")
        assert "TestBrand" in narrative
        # 30/60 = 0.5 (Retention Rate), threshold is 0.4. No Retention leak.
        # TOM is 0, Aided is 80. 0/80 = 0 < 0.05. Awareness Gap.
        assert "awareness gap" in narrative.lower()
        assert len(narrative) > 50
