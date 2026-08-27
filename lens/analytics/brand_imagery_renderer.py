from __future__ import annotations
from typing import Any

class BrandImageryRenderer:
    """
    Renderer for Brand Imagery & Health visualizations.
    Converts raw analytics data into Plotly-ready payloads for the UI.
    """

    def render_imagery_radar(self, data: dict) -> dict:
        """
        Step 1: Create Radar Chart payload for BQ3b (Brand Association).
        """
        imagery = data.get("imagery", {})
        attributes = imagery.get("attributes", [])
        brands = imagery.get("brands", [])

        return {
            "type": "radar",
            "title": "Brand Association (Imagery)",
            "theta": attributes,
            "data": [
                {
                    "name": b["brand_name"],
                    "r": b["scores"]
                }
                for b in brands
            ]
        }

    def render_leaky_bucket(self, brand_data: dict) -> dict:
        """
        Step 2: Create "Leaky Bucket" step-chart payload (Aided -> Used -> Current).
        """
        brand_name = brand_data.get("brand_name", "Unknown")
        
        return {
            "type": "step_chart",
            "title": f"Conversion Funnel: {brand_name}",
            "x": ["Aided Awareness", "Ever Used", "Currently Using"],
            "y": [
                brand_data.get("aided", 0),
                brand_data.get("ever_used", 0),
                brand_data.get("current_use", 0)
            ]
        }

    def render_strategic_quadrant(self, data: dict) -> dict:
        """
        Step 3: Create Strategic Quadrant payload for Importance vs Association.
        """
        strategic = data.get("strategic", [])
        
        return {
            "type": "quadrant",
            "title": "Strategic Priority Quadrant",
            "x_axis": "Importance (1-7)",
            "y_axis": "Brand Association (%)",
            "points": [
                {
                    "label": item["attribute"],
                    "x": item["importance"],
                    "y": item["association"]
                }
                for item in strategic
            ],
            "layout": {
                "show_quadrants": True,
                "x_mid": 4.0,
                "y_mid": 50.0,
                "labels": {
                    "top_right": "Differentiating Strengths",
                    "top_left": "Niche/Hidden Gems",
                    "bottom_right": "Table Stakes",
                    "bottom_left": "Low Priority"
                }
            }
        }

    def build_imagery_dashboard(self, brand_health_result: dict) -> dict:
        """
        Assembles all imagery-related visualizations into a single structured payload.
        """
        brands = brand_health_result.get("brands", [])
        top_brand = brands[0] if brands else None
        
        payload = {
            "tool": "brand_imagery",
            "description": "Comprehensive Brand Health & Imagery Analysis",
            "metric": "Brand Health Index",
            "scalars": [
                {"label": "Total Respondents", "value": brand_health_result.get("base_n", 0)},
                {"label": "Top Brand (Aided)", "value": top_brand["brand_name"] if top_brand else "N/A"}
            ],
            "charts": []
        }

        # 1. Radar Chart (if imagery data exists)
        if "imagery" in brand_health_result:
            payload["charts"].append(self.render_imagery_radar(brand_health_result))

        # 2. Strategic Quadrant (if strategic data exists)
        if "strategic" in brand_health_result:
            payload["charts"].append(self.render_strategic_quadrant(brand_health_result))

        # 3. Leaky Bucket for Top Brand
        if top_brand:
            payload["charts"].append(self.render_leaky_bucket(top_brand))

        return payload
