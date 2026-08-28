"""
Capability: demographic
=======================
Respondent-level analysis: counts, breakdowns, detail queries, geography, time.

Covers: city/zone distribution, gender/age splits, monthly fieldwork trends,
and row-level respondent detail (name, interviewer, date).

Required binding keys (from config/project_N.CAPABILITIES["demographic"]):
  view            — main respondent view (one row per respondent)
  view_rows       — approximate row count
  id_col          — primary key column (e.g., respondent_id)
  name_col        — respondent name column
  gender_col      — gender column + allowed values
  gender_values   — string describing allowed values (e.g., "'Male' / 'Female'")
  age_col         — exact age column
  age_band_col    — banded age column
  age_bands       — string describing allowed values
  city_col        — city name column
  zone_col        — zone name column
  zone_values     — string describing zone values
  date_col        — interview date column (ISO format)
  cities_north    — comma-separated city names for North zone
  cities_south    — comma-separated city names for South zone
  cities_west     — comma-separated city names for West zone
  cities_east     — comma-separated city names for East zone
"""

CAPABILITY_ID = "demographic"
DESCRIPTION   = "Respondent counts, demographics, geography, fieldwork dates"

KEY_COLUMNS_SUMMARY = (
    "{id_col}, {gender_col}, {age_col}, {age_band_col}, {city_col}, {zone_col}, {date_col}"
)


def format_prompt(binding: dict, shared_cols: str, respondent_table: str) -> str:
    b = binding
    return f"""
VIEW: {b['view']}
Columns: {b['id_col']}, {b['name_col']}, {b['gender_col']} ({b['gender_values']}), {b['age_col']}, {b['age_band_col']}, {b['city_col']}, {b['zone_col']}, {b['date_col']}
{shared_cols}

- One row per respondent.
- Use subqueries to v_brand_awareness for aware-respondent breakdowns.

EXAMPLES:
-- Respondents by city
SELECT {b['city_col']}, {b['zone_col']}, COUNT(*) n FROM {b['view']} GROUP BY {b['city_col']} ORDER BY n DESC;

-- specific brand aware respondents by gender
SELECT {b['gender_col']}, COUNT(*) n FROM {b['view']} 
WHERE {b['id_col']} IN (SELECT respondent_id FROM v_brand_awareness WHERE brand_name='Crompton')
GROUP BY {b['gender_col']};
"""
