"""
Project 1 — OX Wave 1
=======================
Consumer survey across 6 electrical appliance categories,
18 cities, 6,631 respondents.

To add a new project, duplicate this file as project_N.py and:
  1. Change PROJECT_ID, PROJECT_NAME, DATA_FILE
  2. Update DIMENSIONS with that survey's code→label mappings
  3. Update COLUMN_MAP if the source Excel uses different column names
  4. Update MULTI_SELECT_FLAGS for that survey's multi-select questions
  5. Register the new project in config/registry.py
"""

from pathlib import Path

# ── identity ──────────────────────────────────────────────────────────────────
PROJECT_ID   = "project_1"
PROJECT_NAME = "OX Wave 1 — Electrical Appliances Consumer Survey"
DATA_FILE    = Path(__file__).parent.parent / "data" / "raw" / "test_data.xlsx"
SHEET_NAME   = "Complete Data "        # trailing space is intentional (source file)

# ── source column names ───────────────────────────────────────────────────────
# Maps semantic role → actual column name in the Excel sheet.
# Change only the right-hand values when a new project uses different names.
COLUMN_MAP = {
    "interview_date":   "int_stime",
    "city_code":        "centre",
    "zone_code":        "zone",
    "gender_code":      "s1",
    "age":              "age",
    "age_band_code":    "age_grid",
    "interviewer":      "int_name",
    "resp_name":        "resp_name",
    "device_id":        "deviceid",
    # brand awareness
    "tom_brand":        "bq1a",       # single-select
    "spont_brands":     "bq1b",       # ordered multi-select string
    # NPS columns follow pattern bq2b_{brand_id}  (1-53)
    "nps_prefix":       "bq2b_",
    "nps_brand_range":  (1, 53),      # inclusive
    # open-ended verbatims
    "verbatims": {
        "bq2a":       "Reason for brand preference / recommendation",
        "mq3a_others":"Other kitchen appliance owned (specify)",
        "mq1a_others":"Other brand name 1 (specify)",
        "mq1b_others":"Other brand name 2 (specify)",
    },
}

# Binary flag column patterns: "{base}/{code}"
# Each entry:  (table_role, base_prefix, valid_codes_iterable)
MULTI_SELECT_FLAGS = {
    "aided_brand":        ("bq1c",  None),          # codes from DIMENSIONS["brands"]
    "kitchen_ownership":  ("mq3a",  None),          # codes from DIMENSIONS["kitchen_appliances"]
    "recent_purchase":    ("mq3b",  None),          # ranked — uses string column
    "room_appliances":    ("mq2a",  None),          # codes from DIMENSIONS["room_appliances"]
}

# ── dimension mappings ────────────────────────────────────────────────────────
# These are the ONLY things that differ survey-to-survey.
DIMENSIONS = {

    "zones": {
        1: "North",
        2: "South",
        3: "West",
        4: "East",
    },

    # city_id → (zone_id, city_name)
    "cities": {
        1:  (1, "Delhi"),
        2:  (1, "Lucknow"),
        3:  (1, "Bikaner"),
        4:  (1, "Patiala"),
        5:  (2, "Chennai"),
        6:  (2, "Cochin"),
        7:  (2, "Guntur"),
        8:  (2, "Hassan"),
        9:  (3, "Mumbai"),
        10: (3, "Ahmedabad"),
        11: (3, "Kolhapur"),
        12: (3, "Ujjain"),
        13: (4, "Kolkata"),
        14: (4, "Patna"),
        15: (4, "Bhubaneshwar"),
        16: (4, "Nagaon"),
        17: (2, "Hyderabad"),
        18: (2, "Bangalore"),
    },

    "gender": {
        1: "Male",
        2: "Female",
    },

    "age_bands": {
        2: "25-35",
        3: "36-50",
    },

    # brand_id → brand_name  (used for bq1a, bq1b, bq1c, bq2b_*)
    "brands": {
        1:  "Bajaj",
        2:  "Crompton",
        3:  "Havells",
        4:  "Orient",
        5:  "Polycab",
        6:  "Usha",
        7:  "Almonard",
        8:  "Atomberg (Gorilla)",
        9:  "Dyson",
        10: "Halonix",
        11: "Luminous",
        12: "Panasonic (Anchor)",
        13: "Polar",
        14: "REO",
        15: "Standard",
        16: "Surya",
        17: "Toofan",
        18: "V Guard",
        19: "Venus",
        20: "Philips",
        21: "Syska",
        22: "Ecolink",
        23: "Panasonic",
        24: "Wipro",
        25: "AO Smith",
        26: "Racold",
        27: "Ferroli",
        28: "Haier",
        29: "Hindware",
        30: "Jaquar",
        31: "Spherehot",
        32: "Maharaja",
        33: "Butterfly",
        34: "Inalsa",
        35: "Jaipan",
        36: "Morphy Richards",
        37: "Piegon",
        38: "Preethi",
        39: "Prestige",
        40: "Sujata",
        41: "Wonderchef",
        42: "Kenstar",
        43: "Symphony",
        44: "Blue Star",
        45: "Khaitan",
        46: "Voltas",
        47: "CRI",
        48: "Kirloskar or KBL",
        49: "KSB",
        50: "Lubi",
        51: "Falcon",
        52: "Grundfos",
        53: "Texmo",
        54: "Others (Specify 1)",
        55: "Others (Specify 2)",
        99: "Don't Know / None",
    },

    # Brand name aliases for user queries (alias -> actual brand name in database)
    # Used by simple_query.py and LLM prompts
    "brand_aliases": {
        # Common abbreviations and variations
        "kbl": "Kirloskar or KBL",
        "kirloskar": "Kirloskar or KBL",
        "ksb": "KSB",
        "maharaja": "Maharaja Whiteline",
        "atomberg": "Atomberg (Gorilla)",
        "ao smith": "AO Smith",
        "panasonic anchor": "Panasonic (Anchor)",
        "v guard": "V Guard",
        "pigeon": "Piegon",  # Note: typo in original data
    },

    # kitchen appliance codes — mq3a (owned) and mq3b (recently purchased)
    "kitchen_appliances": {
        1:  "Mixer Grinder / Mixie",
        2:  "Juicer (Separate standalone)",
        3:  "Food Processor (Kneading & Chopping)",
        4:  "Microwave Oven",
        5:  "Electric Kettle",
        6:  "Sandwich Maker / Toaster",
        7:  "Water Purifiers (Filter)",
        8:  "Induction (Electric) Stove",
        9:  "Air Fryer",
        10: "OTG Oven",
        11: "Coffee Maker",
        12: "Electric Chimney",
        13: "Dishwasher",
        14: "Any Others",
    },

    # room appliance codes — mq2a  (note: code 10 absent in source)
    "room_appliances": {
        1:  "CFL / Incandescent Bulb",
        2:  "Chandeliers / Jhoomer",
        3:  "LED Tube Light / Batten",
        4:  "LED Bulbs",
        5:  "LED Ceiling Lights (Panel/Downlights)",
        6:  "Regular Tube Lights",
        7:  "Indoor Decorative Lights (Wall/Floor)",
        8:  "Ceiling Fans",
        9:  "Air Conditioner",
        11: "Exhaust Fan",
        12: "Room Heater",
        13: "Table Fan",
        14: "Pedestal Fan",
        15: "Wall Fan",
        16: "Air Cooler",
        17: "Air Purifier",
        18: "Water Heater / Geyser",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# SKILL FOUNDRY — Layer 3 Bindings
# ══════════════════════════════════════════════════════════════════════════════
# These are the ONLY project-specific values needed by the skill foundry.
# Layer 1 (base_rules.py) and Layer 2 (capabilities/*.py) are shared across
# all projects. Only this section changes per project.
# See docs/SKILLS.md for full architecture documentation.

# The respondent table used as denominator for all penetration percentages.
RESPONDENT_TABLE = "fact_respondents"

# Demographic / geographic columns shared across ALL views in this project.
# Injected at the end of every capability's VIEW section.
SHARED_VIEW_COLS = (
    "  + shared across all views: gender ('Male'/'Female'), age, "
    "age_band ('25-35'/'36-50'),\n"
    "    city_name, zone_name ('North'/'South'/'West'/'East'),\n"
    "    interview_date (YYYY-MM-DD), interview_year, interview_month,\n"
    "    interview_month_name ('April'/'May'/'June'), interview_quarter"
)

# ── Layer 3: schema bindings per capability ────────────────────────────────────
# Keys must match the required binding keys documented in each capability module.
CAPABILITIES: dict = {

    "awareness": {
        "view":            "v_brand_awareness",
        "view_rows":       "39,842",
        "entity_col":      "brand_name",
        "stage_col":       "stage",
        "tom_value":       "TOM",
        "spont_value":     "SPONT",
        "aided_value":     "AIDED",
        "exclude_filter":  "brand_name != 'Don''t Know / None'",
        "tom_desc":        "top of mind — single brand first recalled (bq1a). rank = 1.",
        "spont_desc":      "spontaneous multi-select, ordered (bq1b). rank = mention position.",
        "aided_desc":      "aided recall from shown list (bq1c). rank = NULL (no order).",
    },

    "nps": {
        "view":            "v_brand_nps",
        "view_rows":       "10,200",
        "entity_col":      "brand_name",
        "score_col":       "nps_score",
        "category_col":    "nps_category",
        "promoter_min":    9,
        "passive_min":     7,
        "passive_max":     8,
        "detractor_max":   6,
        "min_raters":      50,
        "sparse_note": (
            "Sparse — respondents only rate brands they have personally used/owned. "
            "Average ~1.5 brands rated per person."
        ),
    },

    "ownership": {
        "views": [
            {"name": "v_kitchen_ownership", "desc": "kitchen appliances owned", "rows": "13,955"},
            {"name": "v_room_appliances", "desc": "room/electrical appliances owned", "rows": "30,534"},
            {"name": "v_recent_purchase", "desc": "recently purchased appliances (kitchen only)", "rows": "8,110"}
        ],
        "entity_col":  "appliance_name",
        "entity_list_kitchen": (
            "Mixer Grinder / Mixie, Juicer (Separate standalone), "
            "Food Processor (Kneading & Chopping), Microwave Oven, "
            "Electric Kettle, Sandwich Maker / Toaster, Water Purifiers (Filter), "
            "Induction (Electric) Stove, Air Fryer, OTG Oven, Coffee Maker, "
            "Electric Chimney, Dishwasher"
        ),
        "entity_list_room": (
            "CFL / Incandescent Bulb, Chandeliers / Jhoomer, LED Tube Light / Batten, "
            "LED Bulbs, LED Ceiling Lights, Regular Tube Lights, "
            "Indoor Decorative Lights, Ceiling Fans, Air Conditioner, "
            "Exhaust Fan, Room Heater, Table Fan, Pedestal Fan, Wall Fan, "
            "Air Cooler, Air Purifier, Water Heater / Geyser"
        ),
    },

    "purchase": {
        "view":        "v_recent_purchase",
        "view_rows":   "8,110",
        "entity_col":  "appliance_name",
        "rank_col":    "purchase_rank",
        "max_rank":    3,
        "rank_desc":   "1 = most recently purchased / primary, 2–3 = secondary",
        "entity_list": (
            "Mixer Grinder / Mixie, Juicer (Separate standalone), "
            "Food Processor (Kneading & Chopping), Microwave Oven, "
            "Electric Kettle, Sandwich Maker / Toaster, Water Purifiers (Filter), "
            "Induction (Electric) Stove, Air Fryer, OTG Oven, Coffee Maker, "
            "Electric Chimney, Dishwasher, Any Others"
        ),
    },

    "demographic": {
        "view":          "v_respondents",
        "view_rows":     "6,631",
        "id_col":        "respondent_id",
        "name_col":      "resp_name",
        "gender_col":    "gender",
        "gender_values": "'Male' / 'Female'",
        "age_col":       "age",
        "age_band_col":  "age_band",
        "age_bands":     "'25-35' or '36-50'",
        "city_col":      "city_name",
        "zone_col":      "zone_name",
        "zone_values":   "'North' / 'South' / 'West' / 'East'",
        "date_col":      "interview_date",
        "cities_north":  "Delhi, Lucknow, Bikaner, Patiala",
        "cities_south":  "Chennai, Cochin, Guntur, Hassan, Hyderabad, Bangalore",
        "cities_west":   "Mumbai, Ahmedabad, Kolhapur, Ujjain",
        "cities_east":   "Kolkata, Patna, Bhubaneshwar, Nagaon",
    },

    # "general" has no binding — foundry builds it from all above capabilities
}

# ── routing configuration ──────────────────────────────────────────────────────

# Priority order for keyword matching (first match wins).
# Put more specific / unambiguous skills first.
# "demographic" moved up to catch "compare crompton by gender" before brand entity triggers awareness.
SKILL_PRIORITY = ["nps", "purchase", "ownership", "awareness", "demographic"]

# Keywords that trigger each skill. Checked in SKILL_PRIORITY order.
KEYWORDS: dict[str, list[str]] = {
    "nps": [
        "nps", "net promoter", "promoter", "detractor", "passive",
        "recommend", "loyalty", "satisfaction",
    ],
    "purchase": [
        "purchase", "purchased", "bought", "recently bought", "recent buy",
        "last bought", "buying", " buy", "did buy", "have bought",
    ],
    "ownership": [
        "kitchen", "mixer", "juicer", "food processor", "microwave",
        "kettle", "sandwich", "water purifier", "induction stove", "air fryer",
        "otg", "coffee maker", "chimney", "dishwasher",
        "room", "ceiling fan", "air conditioner", "ac ", " ac,", "bulb",
        "led ", "tube light", "geyser", "heater", "water heater", "air cooler",
        "exhaust fan", "table fan", "pedestal fan", "wall fan", "air purifier",
        "room heater", "chandelier",
        "own", "owned", "ownership", "penetration", "saturation",
        "emerging", "tier", "core", "premium", "over-index", "over index",
        "low", "medium", "high", "periphery", "secondary",
        # Phrases for "most owned" queries
        "top product", "top appliance", "most popular", "best selling",
        "leading product", "most owned", "#1 product", "number one product",
        "highest ownership", "most common",
    ],
    "awareness": [
        "tom", "top of mind", "spontaneous", "spont", "aided", "recall",
        "awareness", "brand funnel", "funnel", "mention", "remember",
        "heard of", "first brand", "think of", "conversion", "trial",
        "currently using", "consideration", "preference", "shortlist",
        "ever used", "market share", "share",
    ],
    "demographic": [
        "respondent", "how many", "city", "zone", "gender", "male", "female",
        "age", "demographic", "fieldwork", "interview", "month", "date",
        "north", "south", "east", "west",
        "delhi", "mumbai", "kolkata", "bangalore", "chennai", "hyderabad",
        "lucknow", "patna", "ahmedabad", "cochin", "guntur", "hassan",
        "bikaner", "patiala", "kolhapur", "ujjain", "bhubaneshwar", "nagaon",
        "count", "distribution", "breakdown", "split", "detail", "who",
        # "compare", "comparison",  # Removed - too generic, causes wrong routing "by gender", "by age", "by city", "by zone",
        "demographics", "profile", "split by", "breakdown by", "segment",
        "cohort", "age group", "25-35", "36-50",
    ],
}

# Brand / entity names that map to a specific skill when mentioned.
ENTITY_KEYWORDS: list[str] = [
    # top brands from dim_brand
    "bajaj", "crompton", "havells", "philips", "usha", "orient", "syska",
    "panasonic", "wipro", "surya", "luminous", "polycab", "anchor", "finolex",
    "v guard", "vguard", "butterfly", "preethi", "prestige", "symphony",
    "voltas", "hitachi", "daikin", "godrej", "whirlpool", "maharaja",
    "inalsa", "jaipan", "morphy", "pigeon", "sujata", "wonderchef",
    "kenstar", "blue star", "khaitan", "almonard", "atomberg", "gorilla",
    "halonix", "polar", "standard", "toofan", "venus", "ecolink",
    "ao smith", "racold", "ferroli", "haier", "hindware", "jaquar", "spherehot",
]
ENTITY_SKILL = "awareness"  # entity keywords → route to awareness

# ── UI metadata (label + icon for each skill) ──────────────────────────────────
SKILL_META: dict[str, dict] = {
    "awareness":   {"label": "Brand Awareness",          "icon": "📢"},
    "nps":         {"label": "NPS / Brand Ratings",      "icon": "⭐"},
    "ownership":   {"label": "Kitchen Appliances",       "icon": "🍳"},
    "purchase":    {"label": "Recent Purchases",         "icon": "🛒"},
    "room":        {"label": "Room Appliances",          "icon": "🏠"},
    "demographic": {"label": "Respondents / Demographics","icon": "👤"},
    "general":     {"label": "General / Cross-domain",   "icon": "🔍"},
}

# ── industry benchmarks (used by insights layer — 0 API calls) ─────────────────
BENCHMARKS = {
    # NPS benchmarks (consumer durables industry — India)
    "nps_industry_avg": 45,
    "nps_good": 50,
    "nps_excellent": 70,

    # Awareness benchmarks
    "tom_strong": 20,           # 20%+ TOM is strong in a multi-brand category
    "tom_dominant": 30,         # 30%+ TOM is dominant
    "spont_strong": 50,         # 50%+ spontaneous awareness = well-established
    "total_awareness_good": 70, # 70%+ total awareness = mainstream brand

    # Ownership benchmarks
    "penetration_high": 50,     # 50%+ ownership = mass category
    "penetration_niche": 10,    # <10% = niche/emerging

    # Sample size thresholds
    "sample_min_reliable": 50,
    "sample_low_warning": 30,
}

# ── competitor groupings (used by comparison engine) ───────────────────────────
# Maps a brand to its primary competitive set for auto-comparison context.
COMPETITORS = {
    "Crompton":    ["Bajaj", "Havells", "Orient", "Usha"],
    "Bajaj":       ["Crompton", "Havells", "Orient", "Usha"],
    "Havells":     ["Crompton", "Bajaj", "Orient", "Polycab"],
    "Orient":      ["Crompton", "Bajaj", "Havells", "Usha"],
    "Usha":        ["Crompton", "Bajaj", "Havells", "Orient"],
    "Philips":     ["Syska", "Havells", "Wipro", "Panasonic"],
    "Syska":       ["Philips", "Havells", "Wipro", "Ecolink"],
    "Prestige":    ["Butterfly", "Preethi", "Maharaja", "Morphy Richards"],
    "Butterfly":   ["Prestige", "Preethi", "Maharaja", "Pigeon"],
    "Voltas":      ["Blue Star", "Havells", "Symphony", "Crompton"],
}

# ── data dictionary (injected into universal rules) ────────────────────────────
# LLM will be instructed to ONLY use these exact column names and values,
# avoiding hallucinated strings that cause SQL errors.
DATA_DICTIONARY = """\
VIEWS AVAILABLE:
  v_respondents — respondent demographics (respondent_id, resp_name, gender, age, age_band, city_name, zone_name, interview_date, interview_month, interview_month_name, interview_quarter)
  v_brand_awareness — brand recall data (respondent_id, stage, rank, brand_id, brand_name, + demographics)
  v_brand_nps — NPS ratings (respondent_id, brand_id, brand_name, nps_score, nps_category, + demographics)
  v_kitchen_ownership — kitchen appliances owned (respondent_id, appliance_id, appliance_name, + demographics)
  v_recent_purchase — recently purchased appliances (respondent_id, purchase_rank, appliance_id, appliance_name, + demographics)
  v_room_appliances — room/electrical appliances owned (respondent_id, appliance_id, appliance_name, + demographics)

QUALITATIVE & BRIDGE TABLES (Use these for Qualitative/Mixed queries):
  dim_qual_quant_bridge — link between quantitative surveys and qualitative interviews (respondent_id, transcript_id, match_confidence, match_reason)
    - If respondent_id IS NOT NULL, the respondent has BOTH quantitative and qualitative data.
  fact_verbatims — raw text responses from the quantitative survey (respondent_id, verbatim_text, question_code)
  fact_transcript_segments — processed segments from qualitative interview transcripts (id, doc_id, content, brand, city)

RESEARCH BASE DEFINITIONS:
  - "Qualitative Respondents": Count unique doc_id from fact_transcript_segments OR unique transcript_id from dim_qual_quant_bridge.
  - "Quantitative Respondents": Count unique respondent_id from v_respondents (Total: 6,631).
  - "Overlap Respondents": Count unique respondent_id from dim_qual_quant_bridge WHERE respondent_id IS NOT NULL.

COLUMN VALUES (use these EXACT strings):
  stage (v_brand_awareness): 'TOM', 'SPONT', 'AIDED'
    - TOM = Top of Mind (first brand recalled, unaided)
    - SPONT = Spontaneous (all brands recalled without prompting)
    - AIDED = Aided awareness (recognized from a list)
  nps_category (v_brand_nps): 'Promoter', 'Passive', 'Detractor'
  nps_score (v_brand_nps): integer 0-10. NPS = % Promoters - % Detractors
  gender: 'Male', 'Female'
  age_band: '25-35', '36-50'
  zone_name: 'East', 'North', 'South', 'West'
  interview_month_name: 'April', 'May', 'June'
  city_name: 'Ahmedabad', 'Bangalore', 'Bhubaneshwar', 'Bikaner', 'Chennai', 'Cochin', 'Delhi', 'Guntur', 'Hassan', 'Hyderabad', 'Kolhapur', 'Kolkata', 'Lucknow', 'Mumbai', 'Nagaon', 'Patiala', 'Patna', 'Ujjain'
  brand_name (major brands): 'Bajaj', 'Crompton', 'Havells', 'Philips', 'Orient', 'Usha', 'Panasonic', 'Syska', 'Wipro', 'Prestige', 'Butterfly', 'Preethi', 'Maharaja Whiteline', 'Atomberg (Gorilla)', 'Pigeon', 'Bosch', 'V-Guard', 'Morphy Richards', 'LG', 'Samsung'
  appliance_name (kitchen): 'Mixer Grinder / Mixie', 'Electric Kettle', 'Microwave Oven', 'Water Purifiers (Filter)', 'Induction (Electric) Stove', 'Sandwich Maker / Toaster', 'OTG Oven', 'Food Processor (Kneading & Chopping)', 'Juicer (Separate standalone)', 'Coffee Maker', 'Air Fryer', 'Electric Chimney', 'Dishwasher'
  appliance_name (room): 'Ceiling Fans', 'LED Bulbs', 'LED Tube Light / Batten', 'Table Fan', 'Exhaust Fan', 'Pedestal Fan', 'Air Conditioner', 'Water Heater / Geyser', 'LED Ceiling Lights (Panel/Downlights)', 'Room Heater', 'Wall Fan', 'Air Cooler', 'CFL / Incandescent Bulb', 'Air Purifier'

FOREIGN KEY SEMANTICS (Business Relationships):
  - v_brand_awareness.respondent_id = v_respondents.respondent_id
    (Means: "The specific persona/user who recalled a brand in the survey")
  - v_brand_nps.respondent_id = v_respondents.respondent_id
    (Means: "The specific persona/user who gave an NPS rating to a brand")
  - v_kitchen_ownership.respondent_id = v_respondents.respondent_id
    (Means: "The specific persona/user who owns this appliance")
  - Implicit Joins: If the user asks "NPS for female respondents", you MUST explicitly JOIN v_brand_nps to v_respondents ON respondent_id.
"""

# Structured relationships for programmatic use
RELATIONSHIPS = [
    {
        "from": "v_brand_awareness.respondent_id",
        "to": "v_respondents.respondent_id",
        "type": "many-to-one",
        "semantics": "respondent who provided brand recall feedback",
    },
    {
        "from": "v_brand_nps.respondent_id",
        "to": "v_respondents.respondent_id",
        "type": "many-to-one",
        "semantics": "respondent who rated the brand for NPS",
    },
    {
        "from": "v_kitchen_ownership.respondent_id",
        "to": "v_respondents.respondent_id",
        "type": "many-to-one",
        "semantics": "respondent who owns the kitchen appliance",
    },
    {
        "from": "v_recent_purchase.respondent_id",
        "to": "v_respondents.respondent_id",
        "type": "many-to-one",
        "semantics": "respondent who recently purchased the appliance",
    },
    {
        "from": "v_room_appliances.respondent_id",
        "to": "v_respondents.respondent_id",
        "type": "many-to-one",
        "semantics": "respondent who owns the room appliance",
    },
]

# User-editable display labels for Mixer ethnographic study tabs/sections.
# Change values here (not in ethnographic_renderer.py) to rename what users see
# in the Quote Explorer options-menu and tab bar — no code change needed.
MIXER_SECTION_LABELS: dict[str, str] = {
    "consumer_profiles": "Consumer Profiles",
    "brand_landscape":   "Brand Landscape",
    "pain_points":       "Pain Points",
    "aspiration_gaps":   "Aspiration & Need",
    "purchase_journey":  "Purchase Journey",
}


# ── Brand → product-category groupings (used by Brand Health category filter) ──
# Maps product category label → list of exact brand_name strings from dim_brand.
# A brand can appear in multiple categories (e.g., Havells: Fans + Lighting).
# "All" is the implicit default and requires no entry here.
BRAND_CATEGORIES: dict[str, list[str]] = {
    "Ceiling Fans": [
        "Atomberg (Gorilla)", "Bajaj", "Crompton", "Halonix", "Havells", 
        "Luminous", "Orient", "Panasonic (Anchor)", "Polar", "Polycab", 
        "Standard", "Surya", "Toofan", "Usha", "V Guard", "Venus"
    ],
    "LED Batten": [
        "Bajaj", "Crompton", "Halonix", "Havells", "Orient", 
        "Philips", "Polycab", "Surya", "Syska", "Wipro"
    ],
    "Mixer Grinder": [
        "Bajaj", "Butterfly", "Crompton", "Havells", "Inalsa", 
        "Jaipan", "Maharaja", "Morphy Richards", "Panasonic", "Philips",
        "Pigeon", "Preethi", "Prestige", "Sujata", "Usha", "Wonderchef"
    ],
    "Water Heater": [
        "AO Smith", "Bajaj", "Crompton", "Haier", "Havells", 
        "Hindware", "Jaquar", "Orient", "Racold", "Usha", "V Guard", "Venus"
    ],
    "Air Coolers": [
        "Bajaj", "Blue Star", "Crompton", "Hindware", "Kenstar", 
        "Khaitan", "Maharaja", "Orient", "Symphony", "Usha", "Voltas"
    ],
    "Water Pumps": [
        "CRI", "Crompton", "Falcon", "Grundfos", "Havells", 
        "KSB", "Kirloskar or KBL", "Lubi", "Texmo", "Usha", "V Guard"
    ],
}

TERM_MAPPINGS = """\
COMMON TERM MAPPINGS (user says → use this):
  "top of mind" / "TOM" / "first recall" → stage = 'TOM'
  "spontaneous" / "unaided" → stage = 'SPONT'
  "aided" / "prompted" / "recognition" → stage = 'AIDED'
  "promoters" → nps_category = 'Promoter'
  "detractors" / "critics" → nps_category = 'Detractor'
  "passives" / "neutrals" → nps_category = 'Passive'
  "NPS" / "net promoter" → compute from nps_score: ROUND((SUM(CASE WHEN nps_score >= 9 THEN 1.0 ELSE 0 END) - SUM(CASE WHEN nps_score <= 6 THEN 1.0 ELSE 0 END)) * 100.0 / COUNT(*), 1)
  "mixer" / "mixie" → appliance_name = 'Mixer Grinder / Mixie'
  "kettle" → appliance_name = 'Electric Kettle'
  "fan" / "ceiling fan" → appliance_name = 'Ceiling Fans'
  "AC" / "air conditioner" → appliance_name = 'Air Conditioner'
  "geyser" / "water heater" → appliance_name = 'Water Heater / Geyser'
  "LED" / "bulb" → appliance_name = 'LED Bulbs'
  "Bangalore" / "Bengaluru" → city_name = 'Bangalore'
  "Calcutta" → city_name = 'Kolkata'
  "Bombay" → city_name = 'Mumbai'
  """


# ── Analysis presets (config-driven regression + CAN MAP pipeline) ─────────────
try:
    from lens.analytics.analysis_spec import AnalysisSpec  # noqa: E402
    REGRESSION_CONFIGS = {
        "nps_drivers":    AnalysisSpec.nps_drivers(),
        "trial_drivers":  AnalysisSpec.trial_drivers(),
        "csat_drivers":   AnalysisSpec.csat_drivers(),
        "perception_map": AnalysisSpec.perception_map_only(top_n=30),
    }
except ImportError:
    REGRESSION_CONFIGS = {}
