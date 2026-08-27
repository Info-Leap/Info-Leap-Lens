"""
Capability: nps
===============
Net Promoter Score analysis with robust entity detection.
"""
import re

CAPABILITY_ID = "nps"
DESCRIPTION   = "Net Promoter Score — promoter/passive/detractor analysis"
KEYWORDS      = ["nps", "net promoter", "promoter", "detractor", "recommend", "satisfaction"]

BRANDS = ['Bajaj', 'Crompton', 'Havells', 'Orient', 'Polycab', 'Usha', 'Almonard', 'Dyson', 'Halonix', 'Luminous', 'Panasonic', 'Polar', 'REO', 'Standard', 'Surya', 'Toofan', 'V Guard', 'Venus', 'Philips', 'Syska', 'Ecolink', 'Wipro', 'AO Smith', 'Racold', 'Ferroli', 'Haier', 'Hindware', 'Jaquar', 'Spherehot', 'Maharaja', 'Butterfly', 'Inalsa', 'Jaipan', 'Morphy Richards', 'Piegon', 'Preethi', 'Prestige', 'Sujata', 'Wonderchef', 'Kenstar', 'Symphony', 'Blue Star', 'Khaitan', 'Voltas', 'CRI', 'Kirloskar', 'KBL', 'KSB', 'Lubi', 'Falcon', 'Grundfos', 'Texmo']

def get_sql(instruction: str) -> str:
    """Returns accurate NPS SQL with multi-dimensional support and ROBUST CASE-INSENSITIVE MATCHING."""
    instruction = instruction.lower()
    
    # 1. Detect audiences with Alias Mapping
    segment_map = [
        ("Mixer Grinder Owners", "v_kitchen_ownership", "%Mixer Grinder / Mixie%"),
        ("Water Heater Owners", "v_room_appliances", "%Water Heater / Geyser%"),
        ("Fan Owners", "v_room_appliances", "%Ceiling Fan%"),
        ("Air Conditioner Owners", "v_room_appliances", "%Air Conditioner%")
    ]
    
    segments = []
    for label, table, pattern in segment_map:
        keywords = label.lower().split() + ["mixie", "geyser", "fan", "ac"]
        if any(kw in instruction for kw in keywords if len(kw) > 1):
            segments.append((label, table, pattern))
    
    # 2. Demographic & Entity Filters
    where_clauses = ["1=1"]

    # Zone — detect ALL zones (handles "north vs south", "east and west")
    all_zones = re.findall(r"\b(north|south|east|west|central)\b", instruction)
    all_zones = list(dict.fromkeys(all_zones))  # deduplicate preserving order
    is_zone_comparison = len(all_zones) >= 2

    if len(all_zones) == 1:
        where_clauses.append(f"LOWER(v.zone_name) = '{all_zones[0]}'")
    elif len(all_zones) >= 2:
        zones_str = "', '".join(all_zones)
        where_clauses.append(f"LOWER(v.zone_name) IN ('{zones_str}')")

    # City — detect ALL cities for city comparisons
    cities = ["mumbai", "delhi", "bangalore", "chennai", "kolkata", "hyderabad",
              "pune", "ahmedabad", "lucknow", "jaipur"]
    all_cities = [c for c in cities if c in instruction]
    is_city_comparison = len(all_cities) >= 2

    if len(all_cities) == 1:
        where_clauses.append(f"LOWER(v.city_name) = '{all_cities[0]}'")
    elif len(all_cities) >= 2:
        cities_str = "', '".join(all_cities)
        where_clauses.append(f"LOWER(v.city_name) IN ('{cities_str}')")

    if "female" in instruction: where_clauses.append("LOWER(v.gender) = 'female'")
    elif "male" in instruction: where_clauses.append("LOWER(v.gender) = 'male'")

    age_match = re.search(r"(\d{2}-\d{2})", instruction)
    if age_match: where_clauses.append(f"v.age_band = '{age_match.group(1)}'")

    # Brand detection
    brand_list = []
    for b in BRANDS:
        if b.lower() in instruction:
            brand_list.append(b.lower())

    if brand_list:
        brands_str = "', '".join(brand_list)
        where_clauses.append(f"LOWER(v.brand_name) IN ('{brands_str}')")
    else:
        brand_match = re.search(r"(?:of|for) ['\"]?([\w\s]+)['\"]?", instruction)
        if brand_match:
            brand_candidate = brand_match.group(1).strip().lower()
            _stop = {"north", "south", "east", "west", "central", "male", "female",
                     "nps", "brand", "brands", "all brands", "all", "zone", "zones",
                     "region", "city", "cities", "ranking", "rankings", "comparison"}
            if brand_candidate not in _stop and len(brand_candidate) > 2:
                where_clauses.append(f"LOWER(v.brand_name) LIKE '%{brand_candidate}%'")

    where_str = " AND ".join(where_clauses)

    # 3. Build SQL (PIVOT: Metric First)
    if len(segments) > 1:
        # COMPARISON MODE
        parts = []
        for label, table, pattern in segments:
            parts.append(f"""
            SELECT 
                   ROUND((SUM(CASE WHEN v.nps_score >= 9 THEN 1.0 ELSE 0.0 END) - 
                    SUM(CASE WHEN v.nps_score <= 6 THEN 1.0 ELSE 0.0 END)) * 100.0 / COUNT(*), 1) as nps,
                   '{label}' as segment, v.brand_name, COUNT(*) as base_size
            FROM v_brand_nps v
            JOIN {table} aud ON v.respondent_id = aud.respondent_id AND aud.appliance_name LIKE '{pattern}'
            WHERE {where_str}
            GROUP BY 2, 3
            """)
        return " UNION ALL ".join(parts) + " ORDER BY nps DESC LIMIT 20;"
    
    elif len(segments) == 1:
        # SINGLE SEGMENT MODE
        label, table, pattern = segments[0]
        return f"""
        SELECT 
               ROUND((SUM(CASE WHEN v.nps_score >= 9 THEN 1.0 ELSE 0.0 END) - 
                SUM(CASE WHEN v.nps_score <= 6 THEN 1.0 ELSE 0.0 END)) * 100.0 / COUNT(*), 1) as nps,
               v.brand_name, COUNT(*) as base_size
        FROM v_brand_nps v
        JOIN {table} aud ON v.respondent_id = aud.respondent_id AND aud.appliance_name LIKE '{pattern}'
        WHERE {where_str}
        GROUP BY v.brand_name
        HAVING base_size >= 50
        ORDER BY nps DESC;
        """
    else:
        # GENERIC NPS MODE
        if is_zone_comparison:
            # Multi-zone comparison: GROUP BY brand + zone → one row per zone
            return f"""
            SELECT v.brand_name,
                   r.zone_name,
                   ROUND((SUM(CASE WHEN v.nps_score >= 9 THEN 1.0 ELSE 0.0 END) -
                    SUM(CASE WHEN v.nps_score <= 6 THEN 1.0 ELSE 0.0 END)) * 100.0 / COUNT(*), 1) AS nps_score,
                   COUNT(*) AS base_size,
                   SUM(CASE WHEN v.nps_score >= 9 THEN 1 ELSE 0 END) AS promoters,
                   SUM(CASE WHEN v.nps_score <= 6 THEN 1 ELSE 0 END) AS detractors
            FROM v_brand_nps v
            JOIN v_respondents r ON v.respondent_id = r.respondent_id
            WHERE {where_str}
            GROUP BY v.brand_name, r.zone_name
            HAVING base_size >= 1
            ORDER BY v.brand_name, nps_score DESC;
            """
        elif is_city_comparison:
            # Multi-city comparison: GROUP BY brand + city
            return f"""
            SELECT v.brand_name,
                   r.city_name,
                   ROUND((SUM(CASE WHEN v.nps_score >= 9 THEN 1.0 ELSE 0.0 END) -
                    SUM(CASE WHEN v.nps_score <= 6 THEN 1.0 ELSE 0.0 END)) * 100.0 / COUNT(*), 1) AS nps_score,
                   COUNT(*) AS base_size
            FROM v_brand_nps v
            JOIN v_respondents r ON v.respondent_id = r.respondent_id
            WHERE {where_str}
            GROUP BY v.brand_name, r.city_name
            HAVING base_size >= 1
            ORDER BY v.brand_name, nps_score DESC;
            """
        elif len(brand_list) >= 2:
            # Multi-brand comparison: one row per brand
            return f"""
            SELECT v.brand_name,
                   ROUND((SUM(CASE WHEN v.nps_score >= 9 THEN 1.0 ELSE 0.0 END) -
                    SUM(CASE WHEN v.nps_score <= 6 THEN 1.0 ELSE 0.0 END)) * 100.0 / COUNT(*), 1) AS nps_score,
                   COUNT(*) AS base_size,
                   SUM(CASE WHEN v.nps_score >= 9 THEN 1 ELSE 0 END) AS promoters,
                   SUM(CASE WHEN v.nps_score <= 6 THEN 1 ELSE 0 END) AS detractors
            FROM v_brand_nps v
            WHERE {where_str}
            GROUP BY v.brand_name
            HAVING base_size >= 1
            ORDER BY nps_score DESC;
            """
        else:
            # Single brand / all brands
            return f"""
            SELECT v.brand_name,
                   ROUND((SUM(CASE WHEN v.nps_score >= 9 THEN 1.0 ELSE 0.0 END) -
                    SUM(CASE WHEN v.nps_score <= 6 THEN 1.0 ELSE 0.0 END)) * 100.0 / COUNT(*), 1) AS nps_score,
                   COUNT(*) AS base_size,
                   SUM(CASE WHEN v.nps_score >= 9 THEN 1 ELSE 0 END) AS promoters,
                   SUM(CASE WHEN v.nps_score <= 6 THEN 1 ELSE 0 END) AS detractors
            FROM v_brand_nps v
            WHERE {where_str}
            GROUP BY v.brand_name
            HAVING base_size >= 1
            ORDER BY nps_score DESC;
            """

def format_prompt(binding: dict, shared_cols: str, respondent_table: str) -> str:
    return "NPS Calculation Mode Active."
