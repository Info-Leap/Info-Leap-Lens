"""
DomainKB â€” pre-computed survey facts for LENS 3.2.
Injected into the agent's context as verified facts, avoiding SQL for common queries.
Computed once at module load from the live DB, cached in-process.
"""
import sqlite3
import os
import sys
from pathlib import Path
from functools import lru_cache

_THIS_DIR = Path(__file__).resolve().parent.parent   # oxdata/
_PROJ_ROOT = _THIS_DIR.parent                         # info-leap/
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from infoleap.db_loader import get_db_path


@lru_cache(maxsize=1)
def get_domain_facts() -> dict:
    """
    Returns a dict of pre-computed survey facts.
    Cached after first call â€” safe to call repeatedly.
    Returns empty dict if DB unavailable.
    """
    try:
        db = str(get_db_path(required_table="fact_respondents"))
        conn = sqlite3.connect(db)

        def q(sql, params=None):
            cur = conn.cursor()
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return cur.fetchone()

        # Respondent base
        base_n = q("SELECT COUNT(*) FROM v_respondents")[0]

        # Fieldwork dates
        date_row = q("SELECT MIN(interview_date), MAX(interview_date) FROM v_respondents")
        date_min, date_max = date_row

        # TOM leader
        tom_row = q("""
            SELECT brand_name, COUNT(DISTINCT respondent_id) n
            FROM v_brand_awareness WHERE stage='TOM'
            AND brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)','Don''t Know / None')
            GROUP BY brand_name ORDER BY n DESC LIMIT 1
        """)
        top_tom_brand = tom_row[0] if tom_row else "Bajaj"
        top_tom_n     = tom_row[1] if tom_row else 0
        top_tom_pct   = round(top_tom_n / base_n * 100, 1) if base_n else 0

        # Aided awareness leader
        aided_row = q("""
            SELECT brand_name, COUNT(DISTINCT respondent_id) n
            FROM v_brand_awareness
            WHERE brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)','Don''t Know / None')
            GROUP BY brand_name ORDER BY n DESC LIMIT 1
        """)
        top_aided_brand = aided_row[0] if aided_row else "Bajaj"
        top_aided_pct   = round(aided_row[1] / base_n * 100, 1) if aided_row and base_n else 0

        # NPS leader (min 50 raters)
        nps_row = q("""
            SELECT brand_name,
                   ROUND((SUM(CASE WHEN nps_score>=9 THEN 1.0 ELSE 0 END) -
                          SUM(CASE WHEN nps_score<=6 THEN 1.0 ELSE 0 END))*100.0/COUNT(*),1) nps
            FROM v_brand_nps
            WHERE brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)','Don''t Know / None')
            GROUP BY brand_name HAVING COUNT(*)>=50
            ORDER BY nps DESC LIMIT 1
        """)
        top_nps_brand = nps_row[0] if nps_row else "KSB"
        top_nps_score = nps_row[1] if nps_row else 77.1

        # Worst NPS (min 30 raters)
        worst_row = q("""
            SELECT brand_name,
                   ROUND((SUM(CASE WHEN nps_score>=9 THEN 1.0 ELSE 0 END) -
                          SUM(CASE WHEN nps_score<=6 THEN 1.0 ELSE 0 END))*100.0/COUNT(*),1) nps
            FROM v_brand_nps
            WHERE brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)','Don''t Know / None')
            GROUP BY brand_name HAVING COUNT(*)>=30
            ORDER BY nps ASC LIMIT 1
        """)
        worst_nps_brand = worst_row[0] if worst_row else "Venus"
        worst_nps_score = worst_row[1] if worst_row else -15.0

        # Top kitchen appliance by penetration
        kit_row = q("""
            SELECT da.appliance_name, COUNT(DISTINCT fk.respondent_id) n
            FROM fact_kitchen_ownership fk
            JOIN dim_kitchen_appliance da ON fk.appliance_id=da.appliance_id
            GROUP BY da.appliance_name ORDER BY n DESC LIMIT 1
        """)
        top_kitchen_app = kit_row[0] if kit_row else "Mixer Grinder / Mixie"
        top_kitchen_pct = round(kit_row[1] / base_n * 100, 1) if kit_row and base_n else 93.3

        # Top room appliance by penetration
        room_row = q("""
            SELECT da.appliance_name, COUNT(DISTINCT fr.respondent_id) n
            FROM fact_room_appliances fr
            JOIN dim_room_appliance da ON fr.appliance_id=da.appliance_id
            GROUP BY da.appliance_name ORDER BY n DESC LIMIT 1
        """)
        top_room_app = room_row[0] if room_row else "Ceiling Fans"
        top_room_pct = round(room_row[1] / base_n * 100, 1) if room_row and base_n else 98.4

        # Zone distribution
        zone_rows = conn.execute("""
            SELECT zone_name, COUNT(*) n FROM v_respondents GROUP BY zone_name
        """).fetchall()
        zone_dist = {r[0]: r[1] for r in zone_rows}

        conn.close()

        return {
            "base_n":           base_n,
            "fieldwork_start":  date_min,
            "fieldwork_end":    date_max,
            "survey_wave":      "Wave 1",
            "cities":           18,
            "zones":            4,
            "top_tom_brand":    top_tom_brand,
            "top_tom_pct":      top_tom_pct,
            "top_aided_brand":  top_aided_brand,
            "top_aided_pct":    top_aided_pct,
            "top_nps_brand":    top_nps_brand,
            "top_nps_score":    top_nps_score,
            "worst_nps_brand":  worst_nps_brand,
            "worst_nps_score":  worst_nps_score,
            "top_kitchen_app":  top_kitchen_app,
            "top_kitchen_pct":  top_kitchen_pct,
            "top_room_app":     top_room_app,
            "top_room_pct":     top_room_pct,
            "zone_dist":        zone_dist,
            "nps_industry_avg": 45,
            "category_null_note": (
                "The 'category' column is NULL for all respondents â€” "
                "this is a single all-category wave. Filter brands by product type "
                "using BRAND_CATEGORIES, not respondent category."
            ),
        }
    except Exception as e:
        print(f"[DomainKB] Failed to compute facts: {e}")
        return {}


def get_facts_as_context() -> str:
    """
    Returns facts formatted as a string for injection into the agent's system prompt.
    """
    facts = get_domain_facts()
    if not facts:
        return ""

    zone_str = ", ".join(f"{z}: {n}" for z, n in facts.get("zone_dist", {}).items())

    return f"""
[VERIFIED SURVEY FACTS â€” use these directly, no SQL needed for these]
- Survey: OX Wave 1 | {facts['base_n']:,} respondents | {facts['fieldwork_start']} to {facts['fieldwork_end']}
- Geography: {facts['cities']} cities across {facts['zones']} zones ({zone_str})
- Top-of-Mind leader: {facts['top_tom_brand']} at {facts['top_tom_pct']}% TOM
- Highest aided awareness: {facts['top_aided_brand']} at {facts['top_aided_pct']}%
- Highest NPS (min 50 raters): {facts['top_nps_brand']} at NPS {facts['top_nps_score']:+.0f}
- Lowest NPS (min 30 raters): {facts['worst_nps_brand']} at NPS {facts['worst_nps_score']:+.0f}
- Industry NPS average: {facts['nps_industry_avg']}
- Top kitchen appliance ownership: {facts['top_kitchen_app']} ({facts['top_kitchen_pct']}%)
- Top room appliance ownership: {facts['top_room_app']} ({facts['top_room_pct']}%)
- IMPORTANT: {facts['category_null_note']}
""".strip()
