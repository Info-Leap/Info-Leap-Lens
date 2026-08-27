"""
Mapping utilities for InfoLeap Pulse attributes.
Provides thematic categorization for the 93 survey attributes.
"""

from typing import Dict, List

# Thematic Mapping of 93 Attributes
# Categories:
#   - Performance & Quality: Technical excellence, durability, core functional results.
#   - Value & Pricing: Cost-effectiveness, energy savings, price perception.
#   - Emotional & Lifestyle: Design, aesthetics, brand image, innovative feel.
#   - Service & Network: Maintenance, availability, trust, recommendations.

ATTRIBUTE_THEMES = {
    # Performance & Quality
    1: "Performance & Quality", 2: "Performance & Quality", 3: "Performance & Quality",
    6: "Performance & Quality", 7: "Performance & Quality", 8: "Performance & Quality",
    9: "Performance & Quality", 14: "Performance & Quality", 15: "Performance & Quality",
    16: "Performance & Quality", 20: "Performance & Quality", 21: "Performance & Quality",
    22: "Performance & Quality", 23: "Performance & Quality", 32: "Performance & Quality",
    33: "Performance & Quality", 34: "Performance & Quality", 35: "Performance & Quality",
    36: "Performance & Quality", 37: "Performance & Quality", 38: "Performance & Quality",
    39: "Performance & Quality", 40: "Performance & Quality", 41: "Performance & Quality",
    42: "Performance & Quality", 53: "Performance & Quality", 56: "Performance & Quality",
    64: "Performance & Quality", 65: "Performance & Quality", 71: "Performance & Quality",
    73: "Performance & Quality", 75: "Performance & Quality", 76: "Performance & Quality",
    77: "Performance & Quality",

    # Value & Pricing
    4: "Value & Pricing", 5: "Value & Pricing", 91: "Value & Pricing",
    92: "Value & Pricing", 93: "Value & Pricing",

    # Emotional & Lifestyle
    10: "Emotional & Lifestyle", 11: "Emotional & Lifestyle", 12: "Emotional & Lifestyle",
    13: "Emotional & Lifestyle", 17: "Emotional & Lifestyle", 18: "Emotional & Lifestyle",
    19: "Emotional & Lifestyle", 24: "Emotional & Lifestyle", 25: "Emotional & Lifestyle",
    26: "Emotional & Lifestyle", 27: "Emotional & Lifestyle", 28: "Emotional & Lifestyle",
    29: "Emotional & Lifestyle", 30: "Emotional & Lifestyle", 31: "Emotional & Lifestyle",
    43: "Emotional & Lifestyle", 44: "Emotional & Lifestyle", 45: "Emotional & Lifestyle",
    46: "Emotional & Lifestyle", 47: "Emotional & Lifestyle", 48: "Emotional & Lifestyle",
    49: "Emotional & Lifestyle", 50: "Emotional & Lifestyle", 51: "Emotional & Lifestyle",
    52: "Emotional & Lifestyle", 54: "Emotional & Lifestyle", 55: "Emotional & Lifestyle",
    67: "Emotional & Lifestyle", 68: "Emotional & Lifestyle", 69: "Emotional & Lifestyle",
    70: "Emotional & Lifestyle", 72: "Emotional & Lifestyle", 74: "Emotional & Lifestyle",
    78: "Emotional & Lifestyle", 79: "Emotional & Lifestyle", 80: "Emotional & Lifestyle",
    83: "Emotional & Lifestyle", 88: "Emotional & Lifestyle", 89: "Emotional & Lifestyle",
    90: "Emotional & Lifestyle",

    # Service & Network
    57: "Service & Network", 58: "Service & Network", 59: "Service & Network",
    60: "Service & Network", 61: "Service & Network", 62: "Service & Network",
    63: "Service & Network", 66: "Service & Network", 81: "Service & Network",
    82: "Service & Network", 84: "Service & Network", 85: "Service & Network",
    86: "Service & Network", 87: "Service & Network",
}

def get_attribute_themes(attr_list: List[int]) -> Dict[int, str]:
    """
    Returns a mapping of attribute IDs to their thematic categories.
    """
    return {attr_id: ATTRIBUTE_THEMES.get(attr_id, "Other") for attr_id in attr_list}

def _get_attr_themes(attr_list: List[int]) -> Dict[int, str]:
    """
    Alias for get_attribute_themes that returns shorter theme names 
    (Performance, Value, Emotional, Service) per the executive spec.
    """
    long_to_short = {
        "Performance & Quality": "Performance",
        "Value & Pricing":       "Value",
        "Emotional & Lifestyle": "Emotional",
        "Service & Network":     "Service",
    }
    themes = get_attribute_themes(attr_list)
    return {aid: long_to_short.get(theme, theme) for aid, theme in themes.items()}

def get_theme_for_label(label: str, meta_df) -> str:
    """
    Helper to get theme from label using metadata dataframe.
    """
    if meta_df.empty:
        return "Other"
    match = meta_df[meta_df["attr_label"] == label]
    if match.empty:
        return "Other"
    attr_id = int(match.iloc[0]["attr_id"])
    return ATTRIBUTE_THEMES.get(attr_id, "Other")
