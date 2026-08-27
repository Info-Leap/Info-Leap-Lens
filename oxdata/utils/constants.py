# Global Mappings for InfoLeap Pulse

CATEGORY_MAPPING = {
    "Mixer Grinder": "Mixer Grinder / Mixie",
    "Ceiling Fans": "Ceiling Fans",
    "Air Cooler": "Air Cooler",
    "Water Heater": "Water Heater",
    "Water Pumps": "Water Pumps",
    "LED Tube Light": "LED Tube Light"
}

REVERSE_CATEGORY_MAPPING = {v: k for k, v in CATEGORY_MAPPING.items()}

def resolve_category(cat: str) -> str:
    """Maps a user-friendly category name to the database appliance_name."""
    if not cat: return "All"
    # Basic fuzzy check
    for friendly, db_name in CATEGORY_MAPPING.items():
        if cat.lower() in friendly.lower() or friendly.lower() in cat.lower():
            return db_name
    return cat

def get_friendly_category(cat: str) -> str:
    """Maps a database appliance_name to a user-friendly category name."""
    return REVERSE_CATEGORY_MAPPING.get(cat, cat)
