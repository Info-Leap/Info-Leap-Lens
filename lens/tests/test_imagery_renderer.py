import pytest
import sys
import os

# Add root to sys.path
sys.path.append(os.getcwd())

from analytics.brand_imagery_renderer import BrandImageryRenderer

def test_renderer_radar_payload():
    """Step 1: Radar Chart for BQ3b (Brand Association)"""
    renderer = BrandImageryRenderer()
    data = {
        "status": "success",
        "imagery": {
            "attributes": ["Value for Money", "Durability", "Innovation"],
            "brands": [
                {"brand_name": "Brand A", "scores": [80, 70, 90]},
                {"brand_name": "Brand B", "scores": [60, 85, 75]}
            ]
        }
    }
    payload = renderer.render_imagery_radar(data)
    
    assert payload["type"] == "radar"
    assert payload["title"] == "Brand Association (Imagery)"
    assert "Value for Money" in payload["theta"]
    assert len(payload["data"]) == 2
    assert payload["data"][0]["name"] == "Brand A"
    assert payload["data"][0]["r"] == [80, 70, 90]

def test_renderer_leaky_bucket_payload():
    """Step 2: Leaky Bucket Chart (Aided -> Used -> Current)"""
    renderer = BrandImageryRenderer()
    brand_data = {
        "brand_name": "Crompton",
        "aided": 1000,
        "ever_used": 600,
        "current_use": 300
    }
    payload = renderer.render_leaky_bucket(brand_data)
    
    assert payload["type"] == "step_chart"
    assert payload["title"] == "Conversion Funnel: Crompton"
    assert payload["x"] == ["Aided Awareness", "Ever Used", "Currently Using"]
    assert payload["y"] == [1000, 600, 300]

def test_renderer_quadrant_payload():
    """Step 3: Strategic Quadrant (Importance vs Association)"""
    renderer = BrandImageryRenderer()
    data = {
        "status": "success",
        "strategic": [
            {"attribute": "Price", "importance": 6.5, "association": 75},
            {"attribute": "Style", "importance": 4.2, "association": 40},
            {"attribute": "Service", "importance": 5.8, "association": 85}
        ]
    }
    payload = renderer.render_strategic_quadrant(data)
    
    assert payload["type"] == "quadrant"
    assert payload["title"] == "Strategic Priority Quadrant"
    assert payload["x_axis"] == "Importance (1-7)"
    assert payload["y_axis"] == "Brand Association (%)"
    assert len(payload["points"]) == 3
    assert payload["points"][0]["x"] == 6.5
    assert payload["points"][0]["label"] == "Price"

def test_build_imagery_dashboard():
    renderer = BrandImageryRenderer()
    data = {
        "base_n": 1000,
        "brands": [
            {"brand_name": "Crompton", "aided": 500, "ever_used": 300, "current_use": 150}
        ],
        "imagery": {
            "attributes": ["Attr1"],
            "brands": [{"brand_name": "Crompton", "scores": [80]}]
        },
        "strategic": [
            {"attribute": "Attr1", "importance": 6.0, "association": 80}
        ]
    }
    payload = renderer.build_imagery_dashboard(data)
    
    assert payload["tool"] == "brand_imagery"
    assert len(payload["scalars"]) == 2
    assert len(payload["charts"]) == 3
    assert payload["charts"][0]["type"] == "radar"
    assert payload["charts"][1]["type"] == "quadrant"
    assert payload["charts"][2]["type"] == "step_chart"
