"""
LENS Analytics Renderer  Generic, Data-Driven Backend
=======================================================
Converts any analytics tool result dict into a structured display payload
WITHOUT hardcoding tool names in the rendering logic.

Logic:
    1. Inspect the result dict shape to detect what kind of data it contains
    2. Dynamically select the best chart type (bar, pie, funnel, table)
    3. Produce a "RenderPayload" dict consumed by the UI layer

This makes the renderer fully generalised  any new tool added to market_analytics.py
will automatically work without adding UI code.
"""

from __future__ import annotations
from typing import Any

#  Key field recognisers 
# (priority ordered  first match wins)
LIST_KEYS = [
    "brands",
    "top_brands",
    "cities",
    "segments",
    "buckets",
    "top_attributes",
    "critical_pain_points",
    "rankings",
    "appliance_penetrations",
    "gender_breakdown",
]

SCALAR_KEYS = [
    ("overall_mean", "Overall Mean"),
    ("overall_n", "Total Responses"),
    ("total_respondents", "Total Respondents"),
    ("total_responses", "Total Responses"),
    ("total_households", "Total Households"),
    ("overall_top2box_pct", "Top-2-Box (910) %"),
    ("overall_satisfaction_raw", "Overall Satisfaction"),
    ("nps_proxy", "NPS Proxy"),
    ("n_brands", "Brands Analysed"),
]


def _find_primary_list(result: dict) -> tuple[str, list]:
    """Locate the main data list in a result dict."""
    for key in LIST_KEYS:
        if key in result and isinstance(result[key], list) and result[key]:
            return key, result[key]
    # Fallback: first list value found
    for k, v in result.items():
        if isinstance(v, list) and v:
            return k, v
    return "", []


def _infer_columns(rows: list[dict]) -> list[tuple[str, str]]:
    """
    Infer display columns from a row sample.
    Returns list of (field_key, human_readable_label).
    """
    if not rows:
        return []
    sample = rows[0]
    LABELS = {
        "brand":            "Brand Name",
        "brand_code":       "Brand Code",
        "appliance":        "Appliance Category",
        "appliance_code":   "Appliance Code",
        # 10-pt NPS scale
        "nps":              "NPS (T2B  B6B)",
        "nps_proxy":        "NPS (T2B  B6B)",   # backward compat
        "top_box_pct":      "Top Box % (= 10)",
        "t2b_pct":          "T2B % (910 Promoters)",
        "top2_box_pct":     "T2B % (910)",        # backward compat
        "passives_pct":     "Passives % (68)",
        "b6b_pct":          "B6B % (05 Detractors)",
        "detractors_pct":   "B6B % (05)",         # backward compat
        "mean_score":       "Mean Score (010)",
        "median_score":     "Median",
        "n":                "n (Responses)",
        # City / satisfaction
        "city":             "City",
        "mean_satisfaction": "Mean Satisfaction (010)",
        "t2b_pct":          "T2B % (910)",
        "b6b_pct":          "B6B % (05)",
        "top2box_pct":      "T2B %",                # backward compat
        "bottom2box_pct":   "B6B %",                # backward compat
        "vs_average":       "vs City Avg",
        # 7-pt Importance scale
        "attribute":        "Attribute",
        "mean_importance":  "Mean Importance (17)",
        "pct_of_max":       "% of Max Score",
        "tb_pct":           "Top Box % (= 7)",
        "t2b_pct":          "T2B % (67, Very Important)",
        "b2b_pct":          "B2B % (12, Not Important)",
        "feature_bucket":   "Feature Category",
        "is_gap":           "IS Gap",
        "rank":             "Rank",
        # Feature buckets
        "bucket":           "Feature Category",
        "vs_overall":       "vs Overall",
        "n_attributes":     "# Attributes",
        "n_attributes":     "# Attributes",
        # Appliance / penetration
        "penetration_pct":  "Penetration % (Incidence)",
        "mentions":         "Total Mentions",
        # MQ3B ranking
        "rank_1_count":   "Rank 1 Count (1st Purchase)",
        "rank_1_pct":     "Top Rank % (1st Only)",
        "any_rank_count": "Total Mentions (Multiple Response)",
        "any_rank_pct":   "Multiple Response %",
        # Brand funnel
        "tom_mentions":                 "TOM Mentions",
        "awareness_mentions":           "Aided Awareness (Incidence)",
        "awareness_rate_pct":           "Awareness % (Penetration)",
        "consideration_mentions":       "Consideration Mentions",
        "consideration_conversion_pct": "Consideration Conv %",
        "purchase_mentions":            "Recent Purchase Mentions",
        "purchase_conversion_pct":      "Purchase Conv %",
    }
    cols = []
    for k in sample.keys():
        if k.startswith("_") or k == "variable_code":
            continue  # skip internal fields
        label = LABELS.get(k, k.replace("_", " ").title())
        cols.append((k, label))
    return cols


def _pick_chart(tool_name: str, list_key: str, rows: list[dict]) -> dict | None:
    """Choose an appropriate chart descriptor from result data."""
    if not rows:
        return None

    sample = rows[0]

    # NPS brand comparison
    if "nps" in sample or "nps_proxy" in sample:
        label_key = "brand" if "brand" in sample else list(sample.keys())[0]
        nps_field = "nps" if "nps" in sample else "nps_proxy"
        return {
            "type": "bar",
            "title": "NPS (T2B  B6B) by Brand",
            "x": label_key,
            "y": nps_field,
            "color": nps_field,
            "color_scale": "RdYlGn",
            "rows": rows,
        }

    # Penetration  horizontal bar
    if "penetration_pct" in sample:
        y_key = "appliance" if "appliance" in sample else "appliance_code"
        return {
            "type": "bar_h",
            "title": "Appliance Penetration %",
            "x": "penetration_pct",
            "y": y_key,
            "rows": rows[:15],
        }

    # Funnel stages
    if "awareness_mentions" in sample:
        return {
            "type": "funnel",
            "title": "Brand Funnel",
            "rows": rows[:10],
        }

    # Ranked mentions (MQ3B)
    if "any_rank_count" in sample:
        x_key = "appliance" if "appliance" in sample else "appliance_code"
        return {
            "type": "bar",
            "title": "Appliance Purchase Frequency",
            "x": x_key,
            "y": "any_rank_count",
            "rows": rows[:10],
        }

    # Satisfaction / importance  bar
    if "mean_satisfaction" in sample or "mean_importance" in sample:
        y_key = "mean_satisfaction" if "mean_satisfaction" in sample else "mean_importance"
        x_key = next((k for k in ("city", "attribute", "bucket", "brand") if k in sample), list(sample.keys())[0])
        return {
            "type": "bar",
            "title": f"{'Mean Satisfaction' if y_key == 'mean_satisfaction' else 'Mean Importance'} by {x_key.title()}",
            "x": x_key,
            "y": y_key,
            "rows": rows[:20],
        }

    return None


def build_render_payload(tool_name: str, description: str, result: dict) -> dict:
    """
    Build a generic, tool-agnostic render payload.

    Returns:
        {
          "tool": str,
          "description": str,
          "error": str | None,
          "metric": str,
          "category": str,
          "scalars": [{"label": str, "value": any}, ...],
          "table": {"columns": [(key, label)], "rows": [...]},
          "chart": {...} | None,
          "sub_sections": [{"title": str, "content": str}],
        }
    """
    if result.get("error"):
        return {
            "tool": tool_name,
            "description": description,
            "error": result["error"],
        }


    payload: dict[str, Any] = {
        "tool": tool_name,
        "description": description,
        "error": None,
        "metric": result.get("metric", tool_name.replace("_", " ").title()),
        "category": result.get("category", "All"),
        "scalars": [],
        "table": None,
        "chart": None,
        "charts": [],
        "sub_sections": [],
    }

    #  Extract scalar KPIs 
    for field, label in SCALAR_KEYS:
        if field in result and result[field] is not None:
            payload["scalars"].append({"label": label, "value": result[field]})

    # Leadership / laggard pair
    if "leader" in result and result["leader"]:
        l = result["leader"]
        payload["scalars"].append({
            "label": f" Leader  {l.get('brand', l.get('city', '?'))}",
            "value": l.get("nps", l.get("nps_proxy", l.get("mean_satisfaction", "")))
        })
    if "laggard" in result and result["laggard"]:
        lg = result["laggard"]
        payload["scalars"].append({
            "label": f" Laggard  {lg.get('brand', lg.get('city', '?'))}",
            "value": lg.get("nps", lg.get("nps_proxy", lg.get("mean_satisfaction", "")))
        })
    if "gap_leader_to_laggard" in result:
        payload["scalars"].append({"label": "LeaderLaggard Gap", "value": f"{result['gap_leader_to_laggard']} pts"})

    # Ownership tiers
    tiers = result.get("household_ownership_tiers_pct")
    if tiers:
        for tier_label, pct in tiers.items():
            payload["scalars"].append({
                "label": tier_label.replace("_", " "),
                "value": f"{pct}%"
            })

    #  Extract primary data table 
    list_key, rows = _find_primary_list(result)
    if rows and isinstance(rows[0], dict):
        cols = _infer_columns(rows)
        payload["table"] = {"columns": cols, "rows": rows}
        payload["chart"] = _pick_chart(tool_name, list_key, rows)
        if payload["chart"]:
            payload["charts"].append(payload["chart"])

    #  Gender / demographic sub-sections 
    for sub_key in ("gender_breakdown", "sec_breakdown"):
        sub = result.get(sub_key)
        if isinstance(sub, dict) and sub:
            lines = []
            for k, v in sub.items():
                if isinstance(v, dict):
                    parts = [f"Mean {v.get('mean','?')}", f"n={v.get('n','?')}"]
                    if "top2box_pct" in v:
                        parts.append(f"T2B {v['top2box_pct']}%")
                    lines.append(f"**{k}**: {'  '.join(parts)}")
                else:
                    lines.append(f"**{k}**: {v}")
            payload["sub_sections"].append({
                "title": sub_key.replace("_", " ").title(),
                "content": "  |  ".join(lines)
            })

    return payload


def build_all_payloads(analytics_results: list[dict]) -> list[dict]:
    """Convert a full analytics_results list into render payloads."""
    return [
        build_render_payload(
            tool_name=item.get("tool", ""),
            description=item.get("description", ""),
            result=item.get("result", {})
        )
        for item in analytics_results
    ]

