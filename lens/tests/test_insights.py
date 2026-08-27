import pytest
from analytics.insight_engine import InsightEngine

def test_detect_retention_leak():
    # Setup data where Current Use is much lower than Ever Used
    data = {
        "brands": [
            {
                "brand_name": "LeakBrand",
                "ever_used": 100,
                "current_use": 20,
                "aided": 150,
                "tom": 10,
                "spont": 50
            }
        ]
    }
    engine = InsightEngine()
    insights = engine.analyze(data)
    
    # Check if a retention leak was detected
    assert any("Retention Leak" in i["type"] for i in insights)
    assert any("LeakBrand" in i["brand"] for i in insights)

def test_detect_awareness_gap():
    # Setup data where Aided is high but TOM is low
    data = {
        "brands": [
            {
                "brand_name": "HiddenBrand",
                "aided": 100,
                "tom": 2,
                "spont": 10,
                "ever_used": 10,
                "current_use": 5
            }
        ]
    }
    engine = InsightEngine()
    insights = engine.analyze(data)
    
    assert any("Awareness Gap" in i["type"] for i in insights)

from unittest.mock import patch, MagicMock

def test_gemini_polishing():
    engine = InsightEngine()
    raw_insight = "BrandX has a retention leak."
    
    # Mocking the model used in google.generativeai
    with patch("google.generativeai.GenerativeModel") as mock_model:
        mock_instance = mock_model.return_value
        # Mocking the response structure
        mock_response = MagicMock()
        mock_response.text = "Strategic Insight: BrandX faces user churn."
        mock_instance.generate_content.return_value = mock_response
        
        polished = engine.polish_insight(raw_insight, api_key="test_key")
        assert "Strategic Insight" in polished

def test_detect_unmet_needs():
    # Setup data with imagery gaps
    data = {
        "brands": [
            {
                "brand_name": "PoorServiceBrand",
                "ever_used": 50,
                "current_use": 40,
                "aided": 100,
                "tom": 30,
                "imagery": [
                    {
                        "attribute": "After Sales Service",
                        "importance_pct": 85.0,
                        "association_pct": 15.0
                    }
                ]
            }
        ]
    }
    engine = InsightEngine()
    insights = engine.analyze(data)
    
    assert any("Unmet Need" in i["type"] for i in insights)
    assert any("After Sales Service" in i["finding"] for i in insights)
