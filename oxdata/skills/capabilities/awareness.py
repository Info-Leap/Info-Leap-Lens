"""
Capability: awareness
=====================
Brand / product awareness funnel.
Covers top-of-mind (TOM), spontaneous (SPONT), and aided recall (AIDED).
"""
import re

CAPABILITY_ID = "awareness"
DESCRIPTION   = "Brand/product awareness funnel — TOM, spontaneous, aided recall"
KEYWORDS      = ["awareness", "popular", "top of mind", "tom", "recall", "heard of", "mention", "competition", "competitor", "market share", "ranking", "top", "leader", "most", "conversion", "convert", "funnel conversion", "awareness conversion"]

def get_sql(instruction: str) -> str:
    """
    Returns cumulative awareness funnel SQL.

    Stages in DB are MUTUALLY EXCLUSIVE (each respondent has exactly one stage
    per brand). Industry-standard funnel is CUMULATIVE:
      TOM%   = TOM respondents / total
      SPONT% = (TOM + SPONT) respondents / total
      AIDED% = (TOM + SPONT + AIDED) respondents / total  ← total recognition

    A TOM respondent IS counted in SPONT and AIDED — they know the brand.
    """
    instruction = instruction.lower()

    # Detect single-stage queries (user wants only one level)
    only_tom   = ("top of mind" in instruction or " tom " in instruction
                  or instruction.startswith("tom ") or instruction.endswith(" tom"))
    only_spont = "spontaneous" in instruction and "tom" not in instruction

    # Demographic / geographic filters
    demo_clauses = ["1=1"]

    # All zones (handles "north vs south", "east and west")
    all_zones = re.findall(r"\b(north|south|east|west|central)\b", instruction)
    all_zones = list(dict.fromkeys(all_zones))
    is_zone_comparison = len(all_zones) >= 2

    if len(all_zones) == 1:
        demo_clauses.append(f"LOWER(r.zone_name) = '{all_zones[0]}'")
    elif len(all_zones) >= 2:
        zones_str = "', '".join(all_zones)
        demo_clauses.append(f"LOWER(r.zone_name) IN ('{zones_str}')")

    cities = ["mumbai", "delhi", "bangalore", "chennai", "kolkata",
              "hyderabad", "pune", "ahmedabad", "lucknow", "jaipur"]
    all_cities = [c for c in cities if c in instruction]
    is_city_comparison = len(all_cities) >= 2

    if len(all_cities) == 1:
        demo_clauses.append(f"LOWER(r.city_name) = '{all_cities[0]}'")
    elif len(all_cities) >= 2:
        cities_str = "', '".join(all_cities)
        demo_clauses.append(f"LOWER(r.city_name) IN ('{cities_str}')")

    if "female" in instruction:
        demo_clauses.append("LOWER(r.gender) = 'female'")
    elif "male" in instruction:
        demo_clauses.append("LOWER(r.gender) = 'male'")

    age_match = re.search(r"(\d{2}-\d{2})", instruction)
    if age_match:
        demo_clauses.append(f"r.age_band = '{age_match.group(1)}'")

    demo_str = " AND ".join(demo_clauses)

    # Brand filter
    brands = [
        'bajaj', 'crompton', 'havells', 'orient', 'polycab', 'usha', 'philips',
        'syska', 'luminous', 'panasonic', 'voltas', 'blue star', 'khaitan',
        'preethi', 'prestige', 'butterfly', 'inalsa', 'morphy richards',
        'sujata', 'wonderchef', 'kenstar', 'symphony', 'maharaja',
        'ao smith', 'racold', 'hindware', 'v guard', 'surya', 'halonix',
    ]
    brand_filter = ""
    for b in brands:
        if b in instruction:
            brand_filter = f"AND LOWER(v.brand_name) = '{b}'"
            break

    # ── Cumulative funnel SQL ─────────────────────────────────────────────
    if only_tom:
        # Single-stage: TOM only
        return f"""
        WITH base AS (
            SELECT COUNT(DISTINCT respondent_id) AS total
            FROM v_respondents r WHERE {demo_str}
        )
        SELECT v.brand_name, 'TOM' AS stage,
            ROUND(COUNT(DISTINCT v.respondent_id)*100.0/(SELECT total FROM base), 1) AS awareness_pct,
            COUNT(DISTINCT v.respondent_id) AS awareness_count
        FROM v_brand_awareness v
        JOIN v_respondents r ON v.respondent_id = r.respondent_id
        WHERE {demo_str} AND LOWER(v.stage) = 'tom' {brand_filter}
        GROUP BY v.brand_name
        ORDER BY awareness_pct DESC LIMIT 20;
        """

    if only_spont:
        # Cumulative spontaneous = TOM + SPONT
        return f"""
        WITH base AS (
            SELECT COUNT(DISTINCT respondent_id) AS total
            FROM v_respondents r WHERE {demo_str}
        )
        SELECT v.brand_name, 'SPONT (cumulative)' AS stage,
            ROUND(COUNT(DISTINCT v.respondent_id)*100.0/(SELECT total FROM base), 1) AS awareness_pct,
            COUNT(DISTINCT v.respondent_id) AS awareness_count
        FROM v_brand_awareness v
        JOIN v_respondents r ON v.respondent_id = r.respondent_id
        WHERE {demo_str} AND LOWER(v.stage) IN ('tom','spont') {brand_filter}
        GROUP BY v.brand_name
        ORDER BY awareness_pct DESC LIMIT 20;
        """

    # Multi-zone comparison: cumulative funnel per zone
    if is_zone_comparison:
        return f"""
        WITH zone_base AS (
            SELECT r.zone_name, COUNT(DISTINCT r.respondent_id) AS total
            FROM v_respondents r WHERE {demo_str}
            GROUP BY r.zone_name
        ),
        brand_stages AS (
            SELECT r.zone_name, v.brand_name,
                SUM(CASE WHEN LOWER(v.stage)='tom'  THEN 1 ELSE 0 END) AS tom_n,
                SUM(CASE WHEN LOWER(v.stage) IN ('tom','spont') THEN 1 ELSE 0 END) AS spont_n,
                COUNT(v.respondent_id) AS aided_n
            FROM v_brand_awareness v
            JOIN v_respondents r ON v.respondent_id = r.respondent_id
            WHERE {demo_str} {brand_filter}
            GROUP BY r.zone_name, v.brand_name
        )
        SELECT bs.zone_name, bs.brand_name,
            ROUND(tom_n  *100.0/zb.total, 1) AS tom_pct,
            ROUND(spont_n*100.0/zb.total, 1) AS spont_pct,
            ROUND(aided_n*100.0/zb.total, 1) AS aided_pct,
            tom_n, spont_n, aided_n, zb.total AS base_respondents
        FROM brand_stages bs
        JOIN zone_base zb ON bs.zone_name = zb.zone_name
        ORDER BY bs.zone_name, aided_pct DESC
        LIMIT 20;
        """

    # Default: full cumulative funnel for single zone/national
    return f"""
    -- Cumulative awareness funnel: each level includes all higher-salience respondents
    -- AIDED = total recognition (TOM+SPONT+AIDED), SPONT = TOM+SPONT, TOM = TOM only
    WITH base AS (
        SELECT COUNT(DISTINCT respondent_id) AS total
        FROM v_respondents r WHERE {demo_str}
    ),
    brand_stages AS (
        SELECT v.brand_name,
            SUM(CASE WHEN LOWER(v.stage)='tom'  THEN 1 ELSE 0 END) AS tom_n,
            SUM(CASE WHEN LOWER(v.stage) IN ('tom','spont') THEN 1 ELSE 0 END) AS spont_n,
            COUNT(v.respondent_id) AS aided_n
        FROM v_brand_awareness v
        JOIN v_respondents r ON v.respondent_id = r.respondent_id
        WHERE {demo_str} {brand_filter}
        GROUP BY v.brand_name
    )
    SELECT brand_name,
        ROUND(tom_n  *100.0/(SELECT total FROM base), 1) AS tom_pct,
        ROUND(spont_n*100.0/(SELECT total FROM base), 1) AS spont_pct,
        ROUND(aided_n*100.0/(SELECT total FROM base), 1) AS aided_pct,
        tom_n, spont_n, aided_n,
        (SELECT total FROM base) AS base_respondents
    FROM brand_stages
    ORDER BY aided_pct DESC
    LIMIT 20;
    """

# One-liner summary used when building the GENERAL skill's views section
KEY_COLUMNS_SUMMARY = (
    "respondent_id, stage, rank, brand_name"
)
