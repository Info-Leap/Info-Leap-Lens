"""
BrandHealthSkill â€” pure (non-Streamlit) wrapper around BrandImageryEngine.
Safe to call from researcher_agent.py async tools.

Returns JSON-serialisable dicts only. No st.* calls anywhere in this file.
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).resolve().parent.parent.parent   # info-leap/
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))


def _get_engine():
    from infoleap.analytics.brand_imagery_engine import BrandImageryEngine
    return BrandImageryEngine()


def get_brand_health_data(
    brand_name: str,
    zone: str = "all",
    gender: str = "all",
    age_band: str = "all",
    city: str = "all",
    category: str = "all",
) -> dict:
    """
    Returns brand health metrics as a plain dict (JSON-safe).

    Keys: brand_name, aided_pct, spont_pct, tom_pct, nps, nps_base,
          strat_score, zone_breakdown, city_nps (top 5), rivals (top 3).
    """
    try:
        engine = _get_engine()
        data   = engine.get_brand_health(
            category=category, zone=zone, city=city, gender=gender, age_band=age_band
        )
        brands = data.get("brands", [])
        brand_data = next(
            (b for b in brands if b.get("brand_name", "").lower() == brand_name.lower()),
            None
        )
        if not brand_data:
            # fuzzy match
            brand_data = next(
                (b for b in brands if brand_name.lower() in b.get("brand_name", "").lower()),
                None
            )
        if not brand_data:
            return {"error": f"Brand '{brand_name}' not found. Available: {[b['brand_name'] for b in brands[:10]]}"}

        base_n = data.get("base_n", 0)
        zone_breakdown = engine.get_zone_breakdown(brand_data["brand_name"], base_n)
        city_nps       = engine.get_city_nps(brand_data["brand_name"])[:5]
        rivals         = engine.get_rival_metrics(brand_data["brand_name"], base_n)[:3]

        return {
            "brand_name":  brand_data.get("brand_name"),
            "aided_pct":   round(float(brand_data.get("aided_pct", 0) or 0), 1),
            "spont_pct":   round(float(brand_data.get("spont_pct", 0) or 0), 1),
            "tom_pct":     round(float(brand_data.get("tom_pct", 0) or 0), 1),
            "nps":         brand_data.get("nps"),
            "nps_base":    brand_data.get("nps_base", 0),
            "strat_score": round(float(brand_data.get("strat_score", 0) or 0), 2),
            "base_n":      base_n,
            "filters_applied": {
                "zone": zone, "gender": gender, "age_band": age_band,
                "city": city, "category": category,
            },
            "zone_breakdown": {
                z: {
                    "tom_pct":  round(float(v.get("tom_pct", 0) or 0), 1),
                    "nps":      v.get("nps"),
                    "base":     v.get("base", 0),
                }
                for z, v in (zone_breakdown or {}).items()
            },
            "top_cities": [
                {
                    "city":  c.get("city", ""),
                    "nps":   c.get("nps"),
                    "zone":  c.get("zone", ""),
                    "base":  c.get("raters", 0),
                }
                for c in (city_nps or [])
            ],
            "rivals": [
                {
                    "brand_name": r.get("brand_name", ""),
                    "tom_pct":    round(float(r.get("tom_pct", 0) or 0), 1),
                    "nps":        r.get("nps"),
                }
                for r in (rivals or [])
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def get_multi_brand_comparison(
    brands: list[str],
    zone: str = "all",
    gender: str = "all",
    age_band: str = "all",
    city: str = "all",
) -> dict:
    """
    Returns awareness + NPS metrics for multiple brands in one call.
    Used for comparative questions in Ask Pulse.
    """
    try:
        engine = _get_engine()
        data   = engine.get_brand_health(zone=zone, city=city, gender=gender, age_band=age_band)
        all_brands = data.get("brands", [])
        result = {}
        for name in brands:
            bd = next(
                (b for b in all_brands if b.get("brand_name", "").lower() == name.lower()),
                None
            )
            if bd:
                result[bd["brand_name"]] = {
                    "tom_pct":   round(float(bd.get("tom_pct", 0) or 0), 1),
                    "aided_pct": round(float(bd.get("aided_pct", 0) or 0), 1),
                    "nps":       bd.get("nps"),
                    "strat_score": round(float(bd.get("strat_score", 0) or 0), 2),
                }
            else:
                result[name] = {"error": "Not found"}
        return {"brands": result, "base_n": data.get("base_n", 0)}
    except Exception as e:
        return {"error": str(e)}


def summarise_for_agent(brand_name: str, zone: str = "all", city: str = "all",
                        gender: str = "all", age_band: str = "all") -> str:
    """
    Returns a compact text summary (~200 words) for injection into agent context.
    Safe for use in LLM prompts â€” no DataFrames, no figures.
    """
    d = get_brand_health_data(brand_name, zone=zone, gender=gender,
                               age_band=age_band, city=city)
    if "error" in d:
        return f"Brand health data unavailable for {brand_name}: {d['error']}"

    filters = [f"{k}={v}" for k, v in d["filters_applied"].items() if v != "all"]
    filter_str = f" [{', '.join(filters)}]" if filters else " [all respondents]"

    zone_lines = "\n".join(
        f"  {z}: TOM {v['tom_pct']}%, NPS {v['nps']}" for z, v in d["zone_breakdown"].items()
    ) or "  No zone data"

    rival_lines = ", ".join(
        f"{r['brand_name']} (TOM {r['tom_pct']}%, NPS {r['nps']})"
        for r in d["rivals"]
    ) or "No rival data"

    city_lines = ", ".join(
        f"{c['city']} (NPS {c['nps']})" for c in d["top_cities"]
    ) or "No city data"

    return (
        f"BRAND HEALTH â€” {d['brand_name']}{filter_str} | Base N={d['base_n']:,}\n"
        f"Awareness: TOM {d['tom_pct']}% | Spontaneous {d['spont_pct']}% | Aided {d['aided_pct']}%\n"
        f"NPS: {d['nps']} (base={d['nps_base']})\n"
        f"Strategic Score: {d['strat_score']}\n"
        f"Zone breakdown:\n{zone_lines}\n"
        f"Top cities by NPS: {city_lines}\n"
        f"Key rivals: {rival_lines}"
    )
