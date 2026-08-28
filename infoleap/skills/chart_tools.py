"""
Chart Tools â€” pre-built brand health charts for the Ask Pulse agent.
Exposes `get_brand_chart(chart_type, brand, zone, category)` which
returns a Plotly figure JSON string using the same functions as the
Brand Health dashboard.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CHART_CATALOGUE = {
    "awareness_landscape":  "All-brand stacked awareness bar (TOM / Spont / Aided)",
    "awareness_funnel":     "Single-brand awareness funnel (Aided â†’ Spont â†’ TOM)",
    "nps_rankings":         "NPS league table â€” all brands ranked",
    "zone_comparison":      "Zone-by-zone awareness breakdown for a brand",
    "zone_nps":             "NPS by zone for a brand",
    "city_nps":             "City-level NPS for a brand (top 12 cities)",
    "radar":                "5-axis brand health radar for a brand",
    "positioning":          "Strategic map â€” TOM% vs NPS scatter for all brands",
}


def get_brand_chart(
    chart_type: str,
    brand: str = None,
    zone: str = "all",
    category: str = "all",
) -> tuple[object, str]:
    """
    Returns (plotly_figure, explanation_text).
    Raises ValueError for unknown chart_type.
    """
    from infoleap.analytics.brand_imagery_engine import BrandImageryEngine
    from infoleap.views.brand_health import (
        _awareness_landscape_chart,
        _awareness_funnel_chart,
        _nps_rankings_chart,
        _zone_comparison_chart,
        _zone_nps_chart,
        _city_nps_chart,
        _brand_radar_chart,
        _brand_positioning_chart,
        NPS_INDUSTRY_AVG,
    )

    if chart_type not in CHART_CATALOGUE:
        raise ValueError(
            f"Unknown chart_type '{chart_type}'. Valid: {list(CHART_CATALOGUE)}"
        )

    engine = BrandImageryEngine()
    data = engine.get_brand_health(
        category=category, zone=zone, city="all", months=None
    )

    if data.get("status") == "insufficient_data":
        raise ValueError("Insufficient survey data for the selected filters.")

    brands_list = data["brands"]
    base_n = data["base_n"]

    brand_data = None
    if brand:
        brand_data = next(
            (b for b in brands_list if b["brand_name"].lower() == brand.lower()),
            None,
        )
        if brand_data is None:
            # Try partial match
            brand_data = next(
                (b for b in brands_list if brand.lower() in b["brand_name"].lower()),
                None,
            )

    # â”€â”€ Chart dispatch â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    if chart_type == "awareness_landscape":
        fig = _awareness_landscape_chart(brands_list, top_n=15)
        explanation = (
            f"Awareness landscape across {len(brands_list)} brands "
            f"(base: {base_n:,} respondents). "
            f"Bars stacked: TOM (top-of-mind) + Spontaneous increment + Aided increment. "
            f"Sorted by total aided awareness."
        )

    elif chart_type == "awareness_funnel":
        if brand_data is None:
            raise ValueError(f"Brand '{brand}' not found. Try awareness_landscape instead.")
        fig = _awareness_funnel_chart(brand_data)
        tom = brand_data.get("tom_pct", 0)
        spont = brand_data.get("spont_pct", 0)
        aided = brand_data.get("aided_pct", 0)
        explanation = (
            f"{brand_data['brand_name']} awareness funnel: "
            f"{aided}% aided â†’ {spont}% spontaneous â†’ {tom}% top-of-mind. "
            f"Funnel depth reflects salience: only {round(tom/aided*100) if aided else 0}% "
            f"of those who recognise the brand recall it first unprompted."
        )

    elif chart_type == "nps_rankings":
        highlight = brand_data["brand_name"] if brand_data else None
        fig = _nps_rankings_chart(brands_list, min_raters=30, top_n=15, highlight=highlight)
        if fig is None:
            raise ValueError("Insufficient NPS data (need â‰¥30 raters per brand).")
        explanation = (
            f"NPS league table â€” top 15 brands by score (min 30 raters). "
            f"Industry average: +{NPS_INDUSTRY_AVG}. "
            + (f"{highlight} highlighted in dark green." if highlight else "")
        )

    elif chart_type == "zone_comparison":
        if brand_data is None:
            raise ValueError(f"Brand '{brand}' required for zone comparison.")
        zone_data = engine.get_zone_breakdown(brand_data["brand_name"], base_n)
        if not zone_data:
            raise ValueError(f"No zone data for {brand_data['brand_name']}.")
        fig = _zone_comparison_chart(zone_data, brand_data["brand_name"])
        best_zone = max(zone_data.items(), key=lambda x: x[1].get("tom_pct", 0))[0]
        explanation = (
            f"Awareness by zone for {brand_data['brand_name']} "
            f"(grouped bars: TOM / Spontaneous / Aided). "
            f"Strongest zone: {best_zone}."
        )

    elif chart_type == "zone_nps":
        if brand_data is None:
            raise ValueError(f"Brand '{brand}' required for zone NPS.")
        zone_data = engine.get_zone_breakdown(brand_data["brand_name"], base_n)
        if not zone_data:
            raise ValueError(f"No zone data for {brand_data['brand_name']}.")
        fig = _zone_nps_chart(zone_data, brand_data["brand_name"])
        if fig is None:
            raise ValueError("No NPS data by zone.")
        best_z = max(
            ((z, d["nps"]) for z, d in zone_data.items() if d.get("nps") is not None),
            key=lambda x: x[1], default=(None, None)
        )
        explanation = (
            f"NPS by zone for {brand_data['brand_name']}. "
            + (f"Best zone: {best_z[0]} (NPS {best_z[1]:+.0f})." if best_z[0] else "")
        )

    elif chart_type == "city_nps":
        if brand_data is None:
            raise ValueError(f"Brand '{brand}' required for city NPS.")
        city_nps = engine.get_city_nps(brand_data["brand_name"])
        if not city_nps:
            raise ValueError(f"No city NPS data for {brand_data['brand_name']}.")
        fig = _city_nps_chart(city_nps, brand_data["brand_name"], top_n=12)
        if fig is None:
            raise ValueError("Could not generate city NPS chart.")
        explanation = (
            f"City-level NPS for {brand_data['brand_name']} "
            f"(top 12 cities, min 15 raters). "
            f"Best: {city_nps[0]['city_name']} (NPS {city_nps[0]['nps']:+.0f}). "
            f"Worst: {city_nps[-1]['city_name']} (NPS {city_nps[-1]['nps']:+.0f})."
        )

    elif chart_type == "radar":
        if brand_data is None:
            raise ValueError(f"Brand '{brand}' required for radar chart.")
        fig = _brand_radar_chart(brand_data, brands_list)
        explanation = (
            f"Brand health radar for {brand_data['brand_name']} vs Top-5 category average. "
            f"Five axes: Salience (TOM%), Recall (Spont%), Total Reach (Aided%), "
            f"Loyalty (NPS normalised 0â€“100), Rater Depth. "
            f"Area above the grey dotted line = competitive advantage."
        )

    elif chart_type == "positioning":
        fig = _brand_positioning_chart(brands_list, brand_data["brand_name"] if brand_data else "")
        if fig is None:
            raise ValueError("Insufficient brands with both TOM and NPS data.")
        explanation = (
            f"Strategic market map: TOM% (x-axis) vs NPS (y-axis). "
            f"Bubble size = Aided awareness%. "
            f"Quadrants: Market Leaders (top-right), Loyalty Hidden Gems (top-left), "
            f"Awareness Leaders (bottom-right), Growth Opportunities (bottom-left). "
            + (f"{brand_data['brand_name']} shown as star." if brand_data else "")
        )

    return fig, explanation
