"""
LENS Generic Loader — Phase 2 of multi-project ingestion (2026-07-27, extended 2026-07-28)
========================================================================
Takes a HUMAN-CONFIRMED bucket assignment (from oxdata/views/add_project.py's "Assign to
Buckets" step — see lens/ingestion/codebook_parser.py for how that gets built) and the raw data
file, and writes real rows into a fresh SQLite database using the SAME fixed target schema every
project uses (fact_brand_awareness, fact_satisfaction, fact_brand_nps, fact_brand_imagery,
fact_need_importance, fact_attitudes, fact_portfolio_awareness, fact_price_paid,
fact_purchase_journey, dim_brand, dim_category). Nothing here guesses — every question->bucket
assignment must already be confirmed; this module only knows how to WRITE, not how to CLASSIFY
(that's codebook_parser.py's job).

Scope: TOM / SPONT / AIDED / EVER_USED / CURRENT_USER / CONSIDERATION / PREFERRED /
LAST_PURCHASED -> fact_brand_awareness, CSAT -> fact_satisfaction, NPS -> fact_brand_nps,
BRAND_IMAGERY -> fact_brand_imagery, GENDER/AGE/CITY/ZONE -> fact_respondents columns,
IMPORTANCE -> fact_need_importance, ATTITUDE -> fact_attitudes, PORTFOLIO_AWARENESS ->
fact_portfolio_awareness, PRICE_PAID -> fact_price_paid, PURCHASE_JOURNEY -> fact_purchase_journey.

2026-07-28 honesty notes (read before trusting these five newer buckets as fully equivalent to
project_1's hand-built schema — see CLAUDE.md):
  - ATTITUDE and PRICE_PAID here are NOT tagged to a specific product category per-row unless the
    confirmed question text itself names one — project_1's real fact_attitudes/fact_price_paid
    are category-scoped (one row per respondent x CATEGORY), but the bucket-assignment UI only
    captures question_code/question_text/data_columns, not a per-item category tag. Each
    ATTITUDE/PRICE_PAID item is written with category_id = NULL unless disambiguation text names
    a category (rare). This is a real simplification, not a bug being hidden — flagged in the
    returned summary's "warnings" list whenever it happens.
  - PURCHASE_JOURNEY is written as ONE generic long-format table (respondent_id, question_code,
    question_text, answer) rather than project_1's specific wide pq1-5 columns (why bought, where
    researched, channel, who decided) — there is no generic way to know which of a new project's
    journey questions corresponds to which of those 5 specific slots without per-project mapping,
    so this stores whatever was confirmed, generically, and leaves reshaping into pq1-5-equivalent
    columns as a per-project follow-up if the dashboard ever needs that specific shape for a new
    project (Brand Health's Purchase Journey section would need updating to read the generic shape
    too — not done as part of this pass).

Requires per-question VALUE LABELS (brand code -> brand name) to resolve which respondent picked
which brand — i.e. only works for XLSForm-shaped codebooks today, not AP-tabplan-shaped ones
(load_ap_tabplan_datamap() supplies no value labels at all — see the Akshayakalpa entries in
.planning/MULTIPROJECT_INGESTION_LOG_2026-07-27.md for why that's a genuine, not-yet-solved gap,
not an oversight here). GENDER/AGE/CITY/ZONE/IMPORTANCE/ATTITUDE/PRICE_PAID/PURCHASE_JOURNEY that
are single-value (not brand-coded) still work without value labels when the raw cell IS already
the answer (e.g. a literal age in years) — only the brand/category-crosswalk buckets need labels.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional


# TOM/SPONT/AIDED are mutually EXCLUSIVE tiers in the target schema (each respondent×brand pair
# lands in exactly ONE, the deepest they reached) — not independent questions like the other
# buckets. This mirrors fact_brand_awareness's real semantics (see CLAUDE.md: "TOM/SPONT/AIDED
# are mutually exclusive tiers... not cumulative counts"). Order = priority, deepest first.
EXCLUSIVE_STAGE_PRIORITY = ["TOM", "SPONT", "AIDED"]

# Buckets this loader knows how to write, and which fact table each becomes.
BRAND_AWARENESS_BUCKETS = {"TOM", "SPONT", "AIDED", "EVER_USED", "CURRENT_USER",
                            "CONSIDERATION", "PREFERRED", "LAST_PURCHASED"}

# 2026-07-28: bucket -> fact_respondents column it's written to directly (no separate fact table).
# GENDER always goes to the `gender` TEXT column. AGE is special-cased at write time (not a fixed
# single column): a LABELED age band (e.g. codebook value 1="18-25") writes to `age_band` TEXT;
# an unlabeled raw numeric age (e.g. a literal "34") writes to the `age` INTEGER column instead —
# see the AGE branch in load_confirmed_assignment(). Found by testing: writing every AGE value to
# age_band unconditionally put raw ages like "25" in what should be a banded-category column.
DEMOGRAPHIC_COLUMN_BUCKETS = {"GENDER": "gender"}
# CITY/ZONE go through their own dim tables (dim_city/dim_zone), handled separately below.


@dataclass
class BrandHit:
    respondent_id: int
    brand_name: str


def create_minimal_schema(conn: sqlite3.Connection) -> None:
    """Fixed, deterministic schema — NO AI involved, deliberately. The tables/views here are the
    same for every project by construction (that's the whole point of the "fixed target schema,
    N thin per-project mappings" architecture — see the 2026-07-27 session log). Classification
    (which source column means what) is where structural heuristics + human confirmation earn
    their keep; the schema itself should never be probabilistically generated — a database
    structure that could come out different on every run would make the app's queries unreliable
    in a way no amount of confidence-scoring fixes.

    2026-07-27 fix: previously this only created FACT tables. The app's actual query layer reads
    from VIEWS (v_respondents, v_brand_awareness, v_brand_nps, v_satisfaction) that join those
    facts against dimension tables — found live when switching to a Phase-2-loaded project threw
    `no such table: v_brand_awareness`. The view SQL below is copied verbatim from project_1's
    real `sqlite_master` definitions (not reinvented) so a Phase-2-loaded project's views behave
    identically to the canonical project's, not just structurally similar.

    2026-07-28: added dim_category + 5 new fact tables (fact_need_importance, fact_attitudes,
    fact_portfolio_awareness, fact_price_paid, fact_purchase_journey) so a confirmed IMPORTANCE/
    ATTITUDE/PORTFOLIO_AWARENESS/PRICE_PAID/PURCHASE_JOURNEY bucket has somewhere to land — these
    were previously "accepted but not written" (see the bucket audit in this session's chat).
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dim_date (
            date_id INTEGER PRIMARY KEY, date_label TEXT,
            full_date TEXT, year INTEGER, month_num INTEGER, month_name TEXT, quarter INTEGER
        );
        INSERT OR IGNORE INTO dim_date (date_id, date_label) VALUES (1, 'Wave 1');

        CREATE TABLE IF NOT EXISTS dim_brand (
            brand_id INTEGER PRIMARY KEY AUTOINCREMENT, brand_name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dim_city (
            city_id INTEGER PRIMARY KEY AUTOINCREMENT, city_name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dim_zone (
            zone_id INTEGER PRIMARY KEY AUTOINCREMENT, zone_name TEXT UNIQUE NOT NULL
        );

        -- 2026-07-28: minimal version of project_1's dim_bq3_attribute (attr_id, attr_label only
        -- — the applies_fans/applies_led/... columns are project_1's own multi-category-survey
        -- nuance, not required for the imagery chart itself, so left out here). Reused as-is for
        -- ATTITUDE statement labels too (same shape: a label a rating battery is tagged against).
        CREATE TABLE IF NOT EXISTS dim_bq3_attribute (
            attr_id INTEGER PRIMARY KEY AUTOINCREMENT, attr_label TEXT UNIQUE NOT NULL,
            broad_feature TEXT
        );

        -- 2026-07-28: top-level product/service category dimension — needed by
        -- PORTFOLIO_AWARENESS (which categories a brand is associated with), and optionally by
        -- ATTITUDE/PRICE_PAID when a category can be inferred from the question text.
        CREATE TABLE IF NOT EXISTS dim_category (
            category_id INTEGER PRIMARY KEY AUTOINCREMENT, category_name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fact_brand_imagery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, attr_id INTEGER NOT NULL, brand_id INTEGER NOT NULL,
            value INTEGER NOT NULL DEFAULT 1, category_code TEXT,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id),
            FOREIGN KEY (attr_id) REFERENCES dim_bq3_attribute(attr_id),
            FOREIGN KEY (brand_id) REFERENCES dim_brand(brand_id)
        );
        CREATE INDEX IF NOT EXISTS idx_fbi_resp_brand ON fact_brand_imagery(respondent_id, brand_id);

        -- Demographic columns are nullable; GENDER/AGE (via age_band) are now populated when
        -- confirmed (2026-07-28) — CITY/ZONE resolve through city_id/zone_id below. Other
        -- DEMOGRAPHIC-bucket fields (income, occupation, NCCS, ...) still aren't written — no
        -- generic column to hold arbitrary demographic fields without knowing their shape ahead
        -- of time; flagged in the load summary when a project confirms one, not silently dropped.
        CREATE TABLE IF NOT EXISTS fact_respondents (
            respondent_id INTEGER PRIMARY KEY, date_id INTEGER DEFAULT 1,
            resp_name TEXT, gender TEXT, age INTEGER, age_band TEXT,
            interviewer TEXT, category TEXT, city_id INTEGER, zone_id INTEGER,
            FOREIGN KEY (city_id) REFERENCES dim_city(city_id),
            FOREIGN KEY (zone_id) REFERENCES dim_zone(zone_id)
        );

        CREATE TABLE IF NOT EXISTS fact_brand_awareness (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, date_id INTEGER NOT NULL DEFAULT 1,
            brand_id INTEGER NOT NULL, stage TEXT NOT NULL, rank INTEGER,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id),
            FOREIGN KEY (brand_id) REFERENCES dim_brand(brand_id),
            UNIQUE(respondent_id, brand_id, stage)
        );
        CREATE INDEX IF NOT EXISTS idx_fba_brand_stage ON fact_brand_awareness(brand_id, stage);
        CREATE INDEX IF NOT EXISTS idx_fba_resp_brand_stage ON fact_brand_awareness(respondent_id, brand_id, stage);

        CREATE TABLE IF NOT EXISTS project_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fact_satisfaction (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, date_id INTEGER NOT NULL DEFAULT 1,
            brand_id INTEGER NOT NULL, score REAL,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id),
            FOREIGN KEY (brand_id) REFERENCES dim_brand(brand_id),
            UNIQUE(respondent_id, brand_id)
        );

        CREATE TABLE IF NOT EXISTS fact_brand_nps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, date_id INTEGER NOT NULL DEFAULT 1,
            brand_id INTEGER NOT NULL, nps_score REAL,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id),
            FOREIGN KEY (brand_id) REFERENCES dim_brand(brand_id)
        );

        CREATE TABLE IF NOT EXISTS fact_brand_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, date_id INTEGER NOT NULL DEFAULT 1,
            brand_id INTEGER NOT NULL, rating_type TEXT NOT NULL, score REAL,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id),
            FOREIGN KEY (brand_id) REFERENCES dim_brand(brand_id)
        );

        -- 2026-07-28: new tables for the 5 previously-unwritten buckets.
        CREATE TABLE IF NOT EXISTS fact_need_importance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, attr_id INTEGER NOT NULL, importance_score REAL,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id),
            FOREIGN KEY (attr_id) REFERENCES dim_bq3_attribute(attr_id)
        );

        CREATE TABLE IF NOT EXISTS fact_attitudes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, category_id INTEGER, attr_id INTEGER NOT NULL,
            rating REAL,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id),
            FOREIGN KEY (category_id) REFERENCES dim_category(category_id),
            FOREIGN KEY (attr_id) REFERENCES dim_bq3_attribute(attr_id)
        );

        CREATE TABLE IF NOT EXISTS fact_portfolio_awareness (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, brand_id INTEGER NOT NULL, category_id INTEGER NOT NULL,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id),
            FOREIGN KEY (brand_id) REFERENCES dim_brand(brand_id),
            FOREIGN KEY (category_id) REFERENCES dim_category(category_id)
        );

        CREATE TABLE IF NOT EXISTS fact_price_paid (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, category_id INTEGER, price_tier TEXT,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id),
            FOREIGN KEY (category_id) REFERENCES dim_category(category_id)
        );

        -- Generic long format (question_code/question_text/answer) — see module docstring for
        -- why this isn't project_1's specific wide pq1-5 shape.
        CREATE TABLE IF NOT EXISTS fact_purchase_journey (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id INTEGER NOT NULL, question_code TEXT, question_text TEXT, answer TEXT,
            FOREIGN KEY (respondent_id) REFERENCES fact_respondents(respondent_id)
        );

        -- ── Views — verbatim copies of project_1's real definitions (existing 4) ───────────
        DROP VIEW IF EXISTS v_respondents;
        CREATE VIEW v_respondents AS
            SELECT r.respondent_id, r.resp_name, r.gender, r.age, r.age_band,
                   r.interviewer, r.category,
                   c.city_name, z.zone_name,
                   d.full_date AS interview_date,
                   d.year      AS interview_year,
                   d.month_num AS interview_month,
                   d.month_name AS interview_month_name,
                   d.quarter   AS interview_quarter
            FROM fact_respondents r
            LEFT JOIN dim_city c ON r.city_id = c.city_id
            LEFT JOIN dim_zone z ON r.zone_id = z.zone_id
            LEFT JOIN dim_date d ON r.date_id = d.date_id;

        DROP VIEW IF EXISTS v_brand_awareness;
        CREATE VIEW v_brand_awareness AS
            SELECT ba.id, ba.respondent_id, ba.stage, ba.rank,
                   b.brand_id, b.brand_name,
                   r.gender, r.age, r.age_band, r.category,
                   c.city_name, z.zone_name,
                   d.full_date AS interview_date,
                   d.year AS interview_year, d.month_num AS interview_month,
                   d.month_name AS interview_month_name, d.quarter AS interview_quarter
            FROM fact_brand_awareness ba
            JOIN dim_brand b         ON ba.brand_id     = b.brand_id
            JOIN fact_respondents r  ON ba.respondent_id = r.respondent_id
            LEFT JOIN dim_city c     ON r.city_id        = c.city_id
            LEFT JOIN dim_zone z     ON r.zone_id        = z.zone_id
            LEFT JOIN dim_date d     ON ba.date_id       = d.date_id;

        DROP VIEW IF EXISTS v_brand_nps;
        CREATE VIEW v_brand_nps AS
            SELECT n.id, n.respondent_id, b.brand_id, b.brand_name,
                   n.nps_score,
                   CASE WHEN n.nps_score>=9 THEN 'Promoter'
                        WHEN n.nps_score>=7 THEN 'Passive'
                        ELSE 'Detractor' END AS nps_category,
                   r.gender, r.age, r.age_band, r.category,
                   c.city_name, z.zone_name,
                   d.full_date AS interview_date,
                   d.year AS interview_year, d.month_num AS interview_month,
                   d.month_name AS interview_month_name, d.quarter AS interview_quarter
            FROM fact_brand_nps n
            JOIN dim_brand b         ON n.brand_id      = b.brand_id
            JOIN fact_respondents r  ON n.respondent_id = r.respondent_id
            LEFT JOIN dim_city c     ON r.city_id       = c.city_id
            LEFT JOIN dim_zone z     ON r.zone_id       = z.zone_id
            LEFT JOIN dim_date d     ON n.date_id       = d.date_id;

        DROP VIEW IF EXISTS v_satisfaction;
        CREATE VIEW v_satisfaction AS
            SELECT
                s.id,
                s.respondent_id,
                b.brand_id,
                b.brand_name,
                s.score,
                r.gender, r.age, r.age_band, r.category,
                c.city_name, z.zone_name,
                d.full_date AS interview_date,
                d.year AS interview_year, d.month_num AS interview_month,
                d.month_name AS interview_month_name, d.quarter AS interview_quarter
            FROM fact_satisfaction s
            JOIN dim_brand b         ON s.brand_id      = b.brand_id
            JOIN fact_respondents r  ON s.respondent_id = r.respondent_id
            LEFT JOIN dim_city c     ON r.city_id       = c.city_id
            LEFT JOIN dim_zone z     ON r.zone_id       = z.zone_id
            LEFT JOIN dim_date d     ON s.date_id       = d.date_id;

        DROP VIEW IF EXISTS v_brand_ratings;
        CREATE VIEW v_brand_ratings AS
            SELECT
                br.id, br.respondent_id, br.rating_type, br.score,
                b.brand_id, b.brand_name,
                r.gender, r.age, r.age_band, r.category,
                c.city_name, z.zone_name,
                d.full_date AS interview_date,
                d.year AS interview_year, d.month_num AS interview_month,
                d.month_name AS interview_month_name, d.quarter AS interview_quarter
            FROM fact_brand_ratings br
            JOIN dim_brand b         ON br.brand_id      = b.brand_id
            JOIN fact_respondents r  ON br.respondent_id = r.respondent_id
            LEFT JOIN dim_city c     ON r.city_id       = c.city_id
            LEFT JOIN dim_zone z     ON r.zone_id       = z.zone_id
            LEFT JOIN dim_date d     ON br.date_id      = d.date_id;

        DROP VIEW IF EXISTS v_brand_imagery;
        CREATE VIEW v_brand_imagery AS
            SELECT
                bi.id,
                bi.respondent_id,
                r.city_id,
                r.zone_id,
                r.gender,
                r.age_band,
                bi.attr_id,
                a.attr_label,
                a.broad_feature,
                bi.brand_id,
                db.brand_name,
                bi.value,
                bi.category_code
            FROM fact_brand_imagery bi
            JOIN dim_bq3_attribute a   ON a.attr_id  = bi.attr_id
            JOIN dim_brand db          ON db.brand_id = bi.brand_id
            JOIN fact_respondents r    ON r.respondent_id = bi.respondent_id;

        -- ── Views — new 2026-07-28, same join pattern as the 4 above ────────────────────────
        DROP VIEW IF EXISTS v_need_importance;
        CREATE VIEW v_need_importance AS
            SELECT ni.id, ni.respondent_id, r.city_id, r.zone_id, r.gender, r.age_band,
                   ni.attr_id, a.attr_label, a.broad_feature, ni.importance_score
            FROM fact_need_importance ni
            JOIN dim_bq3_attribute a ON a.attr_id = ni.attr_id
            JOIN fact_respondents r  ON r.respondent_id = ni.respondent_id;

        DROP VIEW IF EXISTS v_attitudes;
        CREATE VIEW v_attitudes AS
            SELECT att.id, att.respondent_id, r.city_id, r.zone_id, r.gender, r.age_band,
                   att.category_id, cat.category_name, att.attr_id, a.attr_label, att.rating
            FROM fact_attitudes att
            JOIN dim_bq3_attribute a  ON a.attr_id = att.attr_id
            LEFT JOIN dim_category cat ON cat.category_id = att.category_id
            JOIN fact_respondents r   ON r.respondent_id = att.respondent_id;

        DROP VIEW IF EXISTS v_portfolio_awareness;
        CREATE VIEW v_portfolio_awareness AS
            SELECT pa.id, pa.respondent_id, r.city_id, r.zone_id, r.gender, r.age_band,
                   pa.brand_id, b.brand_name, pa.category_id, cat.category_name
            FROM fact_portfolio_awareness pa
            JOIN dim_brand b          ON b.brand_id = pa.brand_id
            JOIN dim_category cat     ON cat.category_id = pa.category_id
            JOIN fact_respondents r   ON r.respondent_id = pa.respondent_id;

        DROP VIEW IF EXISTS v_price_paid;
        CREATE VIEW v_price_paid AS
            SELECT pp.id, pp.respondent_id, r.city_id, r.zone_id, r.gender, r.age_band,
                   pp.category_id, cat.category_name, pp.price_tier
            FROM fact_price_paid pp
            LEFT JOIN dim_category cat ON cat.category_id = pp.category_id
            JOIN fact_respondents r    ON r.respondent_id = pp.respondent_id;

        DROP VIEW IF EXISTS v_purchase_journey;
        CREATE VIEW v_purchase_journey AS
            SELECT pj.id, pj.respondent_id, r.city_id, r.zone_id, r.gender, r.age_band,
                   pj.question_code, pj.question_text, pj.answer
            FROM fact_purchase_journey pj
            JOIN fact_respondents r ON r.respondent_id = pj.respondent_id;
    """)
    conn.commit()


REFUSAL_BRAND_PATTERNS = [
    r"^none\s*of", r"^noneof", r"^\$\{.*\}$", r"^q\d+_\d+_\d+$", r"^overall$",
    r"^other$", r"^don'?t\s*know", r"^not\s*applicable", r"^n/a$", r"^refused$",
    r"^none$", r"^[\d\s]+$",  # pure digit-code strings, e.g. a raw multi-code cell
                              # ("2 3 4 6 7 8 9 10 11") that never resolved to a real brand name —
                              # see .planning/AKSHAYAKALPA_PIPELINE_FIX_LOG.md Round 12.
    r"^yes$", r"^no$",        # binary Yes/No answer values from imagery questions (code_to_label
                              # {1:'Yes', 0:'No'}) must never be written as brand names.
]

def is_junk_brand_name(name: str) -> bool:
    if not name:
        return True
    clean = str(name).strip().casefold()
    return any(re.search(pat, clean) for pat in REFUSAL_BRAND_PATTERNS)

def get_or_create_brand(conn: sqlite3.Connection, brand_name: str, _cache: dict,
                        brand_aliases: Optional[dict[str, str]] = None) -> Optional[int]:
    brand_name = str(brand_name).strip()
    key = brand_name.casefold()
    if brand_aliases and key in brand_aliases:
        brand_name = brand_aliases[key].strip()
        key = brand_name.casefold()
    if is_junk_brand_name(brand_name):
        return None
    if key in _cache:
        return _cache[key]
    row = conn.execute(
        "SELECT brand_id, brand_name FROM dim_brand WHERE TRIM(brand_name) = ? COLLATE NOCASE",
        (brand_name,)
    ).fetchone()
    if row:
        _cache[key] = row[0]
        return row[0]
    cur = conn.execute("INSERT INTO dim_brand (brand_name) VALUES (?)", (brand_name,))
    _cache[key] = cur.lastrowid
    return cur.lastrowid


_JUNK_ATTR_PATTERNS = [
    r"^q\d+[\._]",       # raw question code leaked as attr label (e.g. "Q66. Please select...")
    r"^please\s+select", # bare instruction text
    r"^select\s+all",
    r"^reasons\s+for",
    r"^not\s+tried",
]

def is_junk_attr_label(label: str, known_brand_names: Optional[list[str]] = None) -> bool:
    """Return True for labels that are not real imagery attributes:
    - Brand names accidentally mapped as attributes
    - Raw question texts (Q66. ...)
    - Instruction strings ("Please select all reasons...")
    """
    if not label:
        return True
    clean = label.strip().casefold()
    if known_brand_names:
        for bn in known_brand_names:
            if clean == bn.strip().casefold():
                return True
    return any(re.search(pat, clean) for pat in _JUNK_ATTR_PATTERNS)


def get_or_create_attr(conn: sqlite3.Connection, attr_label: str, _cache: dict,
                       known_brand_names: Optional[list[str]] = None) -> Optional[int]:
    """Same pattern as get_or_create_brand — an imagery attribute not already in
    dim_bq3_attribute is a real, new attribute (this project asks about something project_1's
    survey didn't), not an error; add it rather than drop the data.

    Returns None for junk labels (brand names appearing as attrs, raw question texts)."""
    attr_label = str(attr_label).strip()
    if is_junk_attr_label(attr_label, known_brand_names):
        return None
    key = attr_label.casefold()
    if key in _cache:
        return _cache[key]
    row = conn.execute(
        "SELECT attr_id, attr_label FROM dim_bq3_attribute WHERE TRIM(attr_label) = ? COLLATE NOCASE",
        (attr_label,)
    ).fetchone()
    if row:
        _cache[key] = row[0]
        return row[0]
    cur = conn.execute("INSERT INTO dim_bq3_attribute (attr_label) VALUES (?)", (attr_label,))
    _cache[key] = cur.lastrowid
    return cur.lastrowid


def get_or_create_category(conn: sqlite3.Connection, category_name: str, _cache: dict) -> int:
    """Same pattern as get_or_create_attr, for dim_category (2026-07-28)."""
    category_name = str(category_name).strip()
    key = category_name.casefold()
    if key in _cache:
        return _cache[key]
    row = conn.execute(
        "SELECT category_id, category_name FROM dim_category WHERE TRIM(category_name) = ? COLLATE NOCASE",
        (category_name,)
    ).fetchone()
    if row:
        _cache[key] = row[0]
        return row[0]
    cur = conn.execute("INSERT INTO dim_category (category_name) VALUES (?)", (category_name,))
    _cache[category_name] = cur.lastrowid
    return cur.lastrowid


def build_value_crosswalk(value_labels: dict, known_names: list[str],
                           threshold: float = 0.75) -> dict:
    """code -> name, via fuzzy match of the codebook's value label against a reference name list
    (brand names OR category names — same function serves both, see get_or_create_brand/
    get_or_create_category call sites). Names not already known get added fresh via the relevant
    get_or_create_* helper, this only decides which EXISTING name a code's label should be treated
    as, when one matches closely enough)."""
    def _norm(s):
        return "".join(c for c in str(s).lower() if c.isalnum())

    crosswalk = {}
    for code, label in value_labels.items():
        best_name, best_score = None, 0.0
        for name in known_names:
            score = SequenceMatcher(None, _norm(label), _norm(name)).ratio()
            if score > best_score:
                best_name, best_score = name, score
        # Below threshold: keep the label AS-IS as its own name rather than dropping it —
        # an unmatched brand/category is still real (just new to this schema), not an error.
        crosswalk[code] = best_name if best_score >= threshold else str(label)
    return crosswalk


def _canonicalize_text_brand(val, known_brands: list, threshold: float = 0.75) -> Optional[str]:
    """Fuzzy-matches a raw free-text cell value against the known brand list, returning the
    CANONICAL spelling on a good match or None otherwise (see extract_brand_hits' 'single' shape,
    empty-value_labels branch for why this exists — open-ended brand-recall text is messy)."""
    if not isinstance(val, str) or not val.strip():
        return None
    text = val.strip()

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(s).lower())

    t_norm = _norm(text)
    if not t_norm:
        return None
    best_name, best_ratio = None, 0.0
    for b in known_brands:
        ratio = SequenceMatcher(None, t_norm, _norm(b)).ratio()
        if ratio > best_ratio:
            best_name, best_ratio = b, ratio
    return best_name if best_ratio >= threshold else None


def extract_brand_hits(data_df, data_columns: list, value_labels: dict, shape: str,
                        known_brands: Optional[list] = None,
                        delimiter: Optional[str] = None) -> list[BrandHit]:
    """Read the raw respondent rows for one question's data column(s) and return which brand
    each respondent named/selected. `shape` and `value_labels` come straight from the
    codebook_parser ColumnFamily/QuestionDef for this question — this function trusts that
    classification (bucket assignment is what a human already confirmed), it doesn't re-derive it.

    - shape='single': cell value IS the brand code directly (e.g. bq1a: 1=Bajaj chosen).
    - shape='multi_select': column family is 'code/N' dummy-coded — for each checked (==1) cell,
      N (the column's numeric suffix) is the brand code.

    2026-07-28: also reused unchanged for PORTFOLIO_AWARENESS, where the "brand" being resolved is
    actually a category name decoded through a category crosswalk — the BrandHit.brand_name field
    just holds whatever name this call was asked to resolve, brand or category; structurally
    identical (a slash-family whose numeric suffix is the other dimension's code).
    """
    hits = []

    if shape == "single":
        col = data_columns[0]
        if col not in data_df.columns:
            return hits
        for resp_id, val in zip(data_df.index, data_df[col]):
            if val is None or (isinstance(val, float) and val != val):  # NaN
                continue
            if value_labels:
                label = value_labels.get(val, value_labels.get(int(val) if isinstance(val, float) else val))
                # Code map present but lookup failed: val is probably free-text brand name
                # (e.g. TOM open-end "Nandini") — fall through to fuzzy match rather than
                # dropping the row when known_brands is available.
                if label is None and known_brands and isinstance(val, str) and val.strip():
                    label = _canonicalize_text_brand(val, known_brands)
            else:
                # 2026-07-30: some raw exports store the brand name as a literal string directly
                # in the cell (no numeric code, no value_labels dict at all) — commonly an OPEN-
                # ENDED spontaneous-recall question (TOM/SPONT), where respondents free-type a
                # brand and the raw values are full of typos, case variants, and non-brand junk
                # ("Sid Farm"/"Sid's Farm", "Thirumala"/"Tirunala"/"thirumila", stray numeric-code
                # strings from a misaligned column). Writing each unique spelling as its own
                # dim_brand row would flood the brand list with near-duplicates and garbage.
                # Fuzzy-match against the known brand list instead and use the CANONICAL name;
                # drop (don't write a hit for) anything that doesn't match well enough — an
                # unmatched free-text answer is a real coding gap, not something to silently
                # invent a new "brand" for.
                label = _canonicalize_text_brand(val, known_brands) if known_brands else (
                    str(val).strip() if isinstance(val, str) and val.strip() else None)
            if label is not None and not is_junk_brand_name(label):
                hits.append(BrandHit(respondent_id=int(resp_id) + 1, brand_name=str(label)))

    elif shape == "multi_select":
        for col in data_columns:
            if col not in data_df.columns:
                continue
            m = re.search(r"[/_](\d+)$", str(col))
            if not m:
                continue
            code = int(m.group(1))
            label = value_labels.get(code)
            if label is None or is_junk_brand_name(label):
                continue
            try:
                checked = data_df[col].fillna(0).astype(float) == 1
            except (ValueError, TypeError):
                # Column contains text (e.g. open-end TOM "Nandini") — treat as brand name
                # string directly; the numeric suffix code is irrelevant in this case.
                for resp_id, val in zip(data_df.index, data_df[col]):
                    if not isinstance(val, str) or not val.strip():
                        continue
                    brand = _canonicalize_text_brand(val, known_brands) if known_brands else (
                        val.strip() if not is_junk_brand_name(val.strip()) else None
                    )
                    if brand:
                        hits.append(BrandHit(respondent_id=int(resp_id) + 1, brand_name=brand))
                continue
            for resp_id in data_df.index[checked]:
                hits.append(BrandHit(respondent_id=int(resp_id) + 1, brand_name=str(label)))

    elif shape == "delimited_multi_select":
        # raw cell holds multiple option codes crammed into one string, delimiter varies
        # (space/comma/pipe/inconsistent — codebook_parser.detect_delimited_multiselect found
        # this shape from real data). Default: pull every digit run out of the cell (delimiter-
        # agnostic, works regardless of separator). If the human reviewer picked an explicit
        # delimiter in the UI (e.g. the file uses "," but a cell also contains a code like
        # "12" that isn't meant to be split), split on that character instead so a wrong
        # digit-run guess can be corrected without a code change.
        col = data_columns[0]
        if col in data_df.columns:
            for resp_id, val in zip(data_df.index, data_df[col]):
                if val is None or (isinstance(val, float) and val != val):
                    continue
                if isinstance(delimiter, str) and delimiter:
                    toks = [t.strip() for t in str(val).split(delimiter) if t.strip()]
                else:
                    toks = re.findall(r"\d+", str(val))
                for tok in toks:
                    try:
                        code = int(tok)
                    except ValueError:
                        continue
                    label = value_labels.get(code)
                    if label is not None and not is_junk_brand_name(label):
                        hits.append(BrandHit(respondent_id=int(resp_id) + 1, brand_name=str(label)))

    return hits


def extract_single_value_hits(data_df, data_columns: list, value_labels: dict) -> list:
    """2026-07-28 (DEMOGRAPHIC buckets — GENDER/AGE/CITY/ZONE): one value per respondent, not a
    brand. Resolves through value_labels when the codebook has them (a coded field, e.g.
    1=Male/2=Female); otherwise keeps the raw cell value as-is (an open numeric field, e.g. a
    literal age in years). Returns [(respondent_id, value)] — value is str for labeled fields,
    the original scalar for unlabeled ones (so AGE can stay numeric when it's genuinely numeric).

    Also reused for PRICE_PAID and single-shape PURCHASE_JOURNEY items — same "one coded or raw
    value per respondent" structure, just a different destination table.
    """
    if not data_columns or data_columns[0] not in data_df.columns:
        return []
    col = data_columns[0]
    out = []
    for resp_id, val in zip(data_df.index, data_df[col]):
        if val is None or (isinstance(val, float) and val != val):
            continue
        if value_labels:
            label = value_labels.get(val, value_labels.get(int(val) if isinstance(val, float) else val))
            out.append((int(resp_id) + 1, str(label) if label is not None else str(val)))
        else:
            out.append((int(resp_id) + 1, val))
    return out


def extract_numeric_score_hits(data_df, data_columns: list, value_labels: dict) -> list:
    """2026-07-28 (IMPORTANCE/ATTITUDE rating batteries): like extract_single_value_hits, but the
    destination column is REAL (a numeric score, e.g. 1-7 importance or 1-5 attitude rating).

    2026-07-28, found and fixed via a live browser test against this project's own real bq3a data
    (a 1-7 importance scale): the raw data CELL already IS the numeric score (e.g. `7.0`) — it is
    NOT a code that needs decoding through value_labels the way a brand/category question's cell
    is. But many Likert-style codebooks only attach descriptive TEXT to the scale's endpoints and
    midpoint (this project's own bq3a: 1="Not at all important", 4="Neither important Nor
    unimportant", 7="Very Important", while 2/3/5/6 pass through as bare numbers with no text).
    The original version of this function looked the value up in value_labels FIRST — so a
    genuinely valid response of 7 got replaced with the string "Very Important", which then failed
    to parse as a float and was silently dropped as "unparseable", while 6 (no text label) sailed
    through fine. On a 150-row live test this dropped ~30-50% of real, valid importance scores
    (46-49 out of ~90-100 non-blank cells per attribute) — found only by actually running the
    ingest through the browser and reading the warning counts, not by reasoning about the code.
    Fixed: try the raw cell value as a float FIRST (that's the real score whenever the codebook
    numerically codes the scale, regardless of whether some codes also carry a text label); only
    fall back to resolving through value_labels when the raw cell itself isn't already numeric
    (covers the rarer case of a codebook that stores the label text directly in the data, not a
    code). Returns [(respondent_id, float_score)]; cells that are numeric under neither path are
    skipped, not zero-filled — a missing rating is not the same claim as "rated it 0".
    """
    if not data_columns or data_columns[0] not in data_df.columns:
        return []
    col = data_columns[0]
    out = []
    for resp_id, val in zip(data_df.index, data_df[col]):
        if val is None or (isinstance(val, float) and val != val):
            continue
        try:
            out.append((int(resp_id) + 1, float(val)))
            continue
        except (TypeError, ValueError):
            pass
        if value_labels:
            raw = value_labels.get(val, value_labels.get(int(val) if isinstance(val, float) else val, val))
            try:
                out.append((int(resp_id) + 1, float(raw)))
            except (TypeError, ValueError):
                continue
    return out


def get_or_create_dim(conn: sqlite3.Connection, table: str, id_col: str, name_col: str,
                       name: str, _cache: dict) -> int:
    """Generic get-or-create for a small lookup dim (dim_city, dim_zone) — same pattern as
    get_or_create_brand/get_or_create_attr, parameterized over table/column names instead of
    duplicating the function three times."""
    cache_key = (table, name)
    if cache_key in _cache:
        return _cache[cache_key]
    row = conn.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} = ?", (name,)).fetchone()
    if row:
        _cache[cache_key] = row[0]
        return row[0]
    cur = conn.execute(f"INSERT INTO {table} ({name_col}) VALUES (?)", (name,))
    _cache[cache_key] = cur.lastrowid
    return cur.lastrowid


_SCHEMA_SHAPE_TO_LOADER_SHAPE = {
    # schema_ingest.py's LLM-facing shape enum -> the real shape strings
    # load_confirmed_assignment's extract_*_hits functions branch on (see
    # extract_brand_hits's shape== checks around line 526-590): 'single',
    # 'multi_select', 'delimited_multi_select', 'battery_member'.
    "single_value": "single",
    "numeric_score": "single",
    "multivalent_source": "delimited_multi_select",
    # multi_select_dummies: pure one-hot dummy battery, no delimited base column at all —
    # extract_brand_hits's shape='multi_select' branch reads each dummy column's own numeric
    # suffix as the code and checks cell==1, which is exactly this case (see the live
    # Akshayakalpa q67 gap: 13 dummy columns, no combined delimited column to point to).
    "multi_select_dummies": "multi_select",
}


def assignment_from_schema(schema_doc: dict) -> dict:
    """Adapt a schema_ingest.py-produced mapping doc (validated against
    ingestion_mapping.schema.json) into the 4 separate structures
    load_confirmed_assignment actually takes as positional args: assignment,
    value_labels_by_code, shape_by_code, delimiter_by_code. Returns them bundled
    in a dict for the caller to unpack into that call.

    Questions with bucket == "SKIP" are dropped entirely — they appear in none
    of the 4 returned structures. `data_columns` is [source_column] (a one-element
    list) for every shape EXCEPT multi_select_dummies, where source_column is only
    a nominal/representative stem name (informational — see schema_ingest.py's
    _SYSTEM_PROMPT) and the real columns to read are the question's dummy_columns
    list instead."""
    assignment: dict = {}
    value_labels_by_code: dict = {}
    shape_by_code: dict = {}
    delimiter_by_code: dict = {}

    for q in schema_doc.get("questions", []):
        if q.get("bucket") == "SKIP":
            continue
        code = q["question_code"]
        bucket = q["bucket"]
        _has_dummies = bool(q.get("dummy_columns"))
        if q["shape"] == "multi_select_dummies":
            data_columns = list(q.get("dummy_columns") or [])
        elif q["shape"] in ("multivalent_source", "single_value") and _has_dummies:
            # Prefer binary dummy columns when available — more reliable than parsing the
            # space-coded source column. Applies to both multivalent_source AND single_value
            # shapes (e.g. AK AIDED/CONSIDERATION tagged single_value but have dummy cols).
            data_columns = list(q["dummy_columns"])
        else:
            data_columns = [q["source_column"]]
        assignment.setdefault(bucket, []).append({
            "question_code": code,
            "question_text": q.get("question_text"),
            "source_column": q.get("source_column"),
            "data_columns": data_columns,
        })
        raw_labels = q.get("code_to_label") or {}
        # Normalize all keys to int so extract_brand_hits int-keyed lookups match regardless
        # of whether the source (JSON, Excel parse) produced string keys ("1") or int keys (1).
        normalized_labels: dict = {}
        for k, v in raw_labels.items():
            try:
                normalized_labels[int(float(k))] = v
            except (ValueError, TypeError):
                normalized_labels[k] = v
        value_labels_by_code[code] = normalized_labels
        if q["shape"] in ("multivalent_source", "single_value") and _has_dummies:
            shape_by_code[code] = "multi_select"  # use dummy-column path, not delimited
        else:
            shape_by_code[code] = _SCHEMA_SHAPE_TO_LOADER_SHAPE.get(q["shape"], "single")
        if q["shape"] == "multivalent_source" and q.get("delimiter") and not q.get("dummy_columns"):
            delimiter_by_code[code] = q["delimiter"]

    # Propagate brand code→name mapping to brand-awareness questions whose code_to_label
    # is empty. The LLM classifier often populates code_to_label for only one question in
    # a family (e.g. PREFERRED q25: {1: "Akshayakalpa", 2: "Country Delight", ...}) and
    # leaves the rest empty. CONSIDERATION/EVER_USED/CURRENT_USER/TOM/SPONT/AIDED all share
    # the same brand code numbering — propagate the richest mapping to every empty peer.
    _brand_code_maps: list[dict] = []
    for bucket in BRAND_AWARENESS_BUCKETS:
        for item in assignment.get(bucket, []):
            vlabels = value_labels_by_code.get(item["question_code"], {})
            if vlabels:
                _brand_code_maps.append(vlabels)
    if _brand_code_maps:
        # Pick the map with the most entries as the authoritative source, then normalize
        # keys to int so extract_brand_hits's `value_labels.get(int_code)` lookups match.
        # Raw code_to_label keys may be '1.0' (float-stringified) or '1' (str) or 1 (int).
        _raw_best = max(_brand_code_maps, key=len)
        _best_map: dict = {}
        for k, v in _raw_best.items():
            try:
                _best_map[int(float(k))] = v
            except (ValueError, TypeError):
                pass
        for bucket in BRAND_AWARENESS_BUCKETS:
            for item in assignment.get(bucket, []):
                code = item["question_code"]
                if not value_labels_by_code.get(code):
                    value_labels_by_code[code] = _best_map

        # For NPS/CSAT/rating buckets with per-brand battery columns (e.g. q65_1, q65_2, ...):
        # 1. Infer the brand for already-classified questions from the suffix code.
        # 2. Auto-expand sibling columns that the LLM missed (only classified _1 but _2.._N exist
        #    in the schema's question list, or weren't classified at all). This is future-proof:
        #    any new project whose NPS/CSAT is a per-brand battery gets all brands populated for
        #    free without needing the LLM to enumerate every suffix column.
        # BRAND_IMAGERY is excluded here — it uses a 3-part grid pattern ({attr}_{brand}) handled
        # by a separate expansion block in load_confirmed_assignment() where data_df is available.
        _BATTERY_RATING_BUCKETS = ("NPS", "CSAT", "FAMILIARITY", "BRAND_LOVE", "BRAND_TRUST",
                                   "BRAND_QUALITY", "BRAND_RATING")

        # Collect all classified question codes (to avoid duplicates when expanding)
        _classified_codes: set = {
            item["question_code"]
            for items in assignment.values()
            for item in items
        }
        for bucket in _BATTERY_RATING_BUCKETS:
            _new_items: list = []
            for item in assignment.get(bucket, []):
                if item.get("brand"):
                    continue
                code = item["question_code"]
                src = (item.get("data_columns") or [code])[0]
                m = re.search(r"^(.+?)_(\d+)$", str(src))
                if not m:
                    continue
                stem, suffix_str = m.group(1), m.group(2)
                brand_code = int(suffix_str)
                inferred = _best_map.get(brand_code)
                if inferred and not is_junk_brand_name(inferred):
                    item["brand"] = inferred
                # Expand sibling columns for all other brand codes in _best_map
                for sibling_code_int, sibling_brand in _best_map.items():
                    if is_junk_brand_name(sibling_brand):
                        continue
                    sibling_col = f"{stem}_{sibling_code_int}"
                    if sibling_col in _classified_codes or sibling_col == src:
                        continue
                    _new_items.append({
                        "question_code": sibling_col,
                        "question_text": sibling_brand,
                        "data_columns": [sibling_col],
                        "brand": sibling_brand,
                        "_auto_expanded": True,
                    })
                    _classified_codes.add(sibling_col)
                    value_labels_by_code[sibling_col] = {}
                    shape_by_code[sibling_col] = "single"
            if _new_items:
                assignment.setdefault(bucket, []).extend(_new_items)

    return {
        "assignment": assignment,
        "value_labels_by_code": value_labels_by_code,
        "shape_by_code": shape_by_code,
        "delimiter_by_code": delimiter_by_code,
    }


def load_confirmed_assignment(
    assignment: dict,          # {bucket: [{question_code, question_text, data_columns}, ...]}
    value_labels_by_code: dict,  # {question_code: {value_code: label}} — from the confirmed QuestionDefs
    shape_by_code: dict,         # {question_code: 'single'|'multi_select'|...}
    data_df,                     # full raw data DataFrame (0-based index -> respondent_id = index+1)
    out_db_path: str,
    category_names: Optional[list] = None,  # 2026-07-28: known category names for the
                                             # PORTFOLIO_AWARENESS brand-vs-category crosswalk
    brand_names: Optional[list] = None,     # 2026-07-30: known brand names — used to canonicalize
                                             # open-ended free-text brand columns (see
                                             # _canonicalize_text_brand) instead of writing every
                                             # unique typo/case-variant as its own dim_brand row
    delimiter_by_code: Optional[dict] = None,  # {question_code: delimiter_char} — human-picked
                                                # override for delimited_multi_select questions,
                                                # from the Add Project review table; falls back to
                                                # the digit-regex auto-split when a code has no entry
    brand_aliases: Optional[dict[str, str]] = None, # {alias_key: canonical_brand_name}
) -> dict:
    """End-to-end: for every bucket in the confirmed assignment, extract values from the raw
    data and write them into the fixed target schema at out_db_path. Returns a summary dict for
    reporting (rows written per bucket, brands seen) — nothing here is silent.
    """
    delimiter_by_code = delimiter_by_code or {}
    # 2026-08-03: write to a temp file and only replace out_db_path on full success. A crash or
    # interrupted run (Streamlit rerun killing the script mid-ingest, exception partway through)
    # used to leave a half-written oxdata.db behind — same filename, valid schema, but silently
    # missing most bucket rows (e.g. SPONT=0/CSAT=0 while TOM/AIDED looked populated) with no error
    # surfaced anywhere, indistinguishable from a real completed ingest until someone diffed row
    # counts against a known-good run. See .planning/AKSHAYAKALPA_PIPELINE_FIX_LOG.md Round 11.
    tmp_db_path = out_db_path + ".tmp"
    if os.path.exists(tmp_db_path):
        os.remove(tmp_db_path)
    conn = sqlite3.connect(tmp_db_path)
    create_minimal_schema(conn)
    brand_id_cache: dict = {}
    attr_id_cache: dict = {}
    category_id_cache: dict = {}
    summary = {"buckets_written": {}, "skipped_buckets": [], "accepted_not_written": [],
               "respondents_seen": set(), "warnings": []}

    exclusive_hits: dict[str, set[tuple]] = {s: set() for s in EXCLUSIVE_STAGE_PRIORITY}
    independent_rows = []  # (stage, respondent_id, brand_name)
    csat_rows = []          # (respondent_id, score)
    nps_rows = []            # (respondent_id, brand_name, score)
    imagery_rows = []        # (attr_label, respondent_id, brand_name)
    importance_rows = []     # (attr_label, respondent_id, score)
    attitude_rows = []       # (attr_label, respondent_id, rating)
    portfolio_rows = []      # (category_label, respondent_id, brand_name)
    price_rows = []          # (category_label, respondent_id, tier)
    journey_rows = []        # (question_code, question_text, respondent_id, answer)
    rating_rows = []         # (respondent_id, brand, rating_type, score)
    demographic_col_updates: dict[str, list] = {c: [] for c in DEMOGRAPHIC_COLUMN_BUCKETS.values()}
    age_band_updates: list = []  # (respondent_id, label) — labeled age band, e.g. "18-25"
    age_numeric_updates: list = []  # (respondent_id, int_age) — raw numeric age
    city_updates: list = []   # (respondent_id, city_name)
    zone_updates: list = []   # (respondent_id, zone_name)

    known_categories = list(category_names) if category_names else []
    if brand_names:
        for b in brand_names:
            get_or_create_brand(conn, b, brand_id_cache, brand_aliases)

    # ── BRAND_IMAGERY grid auto-expansion ─────────────────────────────────────
    # Projects with a brand×attribute binary grid (e.g. q34_{attr}_{brand}) will have
    # their BRAND_IMAGERY items classified at the attribute level only (q34_1, q34_2…)
    # — the per-brand sub-columns are never individually listed in the codebook. Expand
    # each attribute item's data_columns here using the actual data columns and the brand
    # code map derived from value_labels_by_code (the richest entry — same source used
    # by assignment_from_schema for NPS/CSAT expansion). Future-proof: any project with
    # this 3-part column pattern gets full brand coverage without any manual JSON edits.
    _img_brand_map: dict[int, str] = {}
    for _vlc in value_labels_by_code.values():
        if _vlc and len(_vlc) > len(_img_brand_map):
            try:
                _img_brand_map = {int(float(k)) if not isinstance(k, int) else k: v
                                  for k, v in _vlc.items()}
            except (ValueError, TypeError):
                pass
    # Fallback: if _img_brand_map is empty OR only contains non-brand values like "Yes"/"No"
    # (imagery questions with binary 0/1 code_to_label inflate the map with garbage), use
    # dim_brand instead. This handles projects where the LLM left code_to_label empty for
    # multi_select_dummies awareness questions but brand IDs are already in the DB.
    _known_brand_set = {b.casefold() for b in (brand_names or [])}
    _map_has_brands = bool(_img_brand_map) and any(
        str(v).casefold() in _known_brand_set for v in _img_brand_map.values()
    )
    if not _map_has_brands:
        _dim_brand_rows = conn.execute(
            "SELECT brand_id, brand_name FROM dim_brand ORDER BY brand_id"
        ).fetchall()
        if _dim_brand_rows:
            _img_brand_map = {bid: bname for bid, bname in _dim_brand_rows}
            # Propagate dim_brand code map to question codes that:
            # (a) have no code_to_label yet, OR
            # (b) have a binary Yes/No code_to_label that is not a real brand map
            #     (imagery grid questions like "Is AK a premium brand? Yes/No" —
            #      their real brand map is {1:'AK', 2:'CD', ...} via the per-brand
            #      dummy sub-columns q34_1_1, q34_1_2, ...).
            # Guard: only inject into awareness-funnel questions whose text mentions
            # "brand" or an actual brand name — prevents non-brand multi-selects
            # (e.g. "which dairy products did you buy?") from getting false brand hits.
            _known_brand_casefolded = {bn.casefold() for bn in (brand_names or [])}
            _is_yn_map = lambda c2l: bool(c2l) and set(
                str(v).strip().casefold() for v in c2l.values()
            ) <= {"yes", "no"}
            _aware_buckets_all = BRAND_AWARENESS_BUCKETS | {"BRAND_IMAGERY", "PORTFOLIO_AWARENESS"}
            for _bkt in _aware_buckets_all:
                for _item in assignment.get(_bkt, []):
                    _qcode = _item["question_code"]
                    _existing_c2l = value_labels_by_code.get(_qcode)
                    # Skip if already has a real brand map (not Yes/No).
                    if _existing_c2l and not _is_yn_map(_existing_c2l):
                        continue
                    # For imagery/portfolio, always inject.
                    # For awareness funnel, only inject if question mentions brands.
                    if _bkt not in {"BRAND_IMAGERY", "PORTFOLIO_AWARENESS"}:
                        # For awareness-funnel buckets, only inject if question text
                        # references brands (word "brand" or a known brand name appears).
                        _q_text = (_item.get("question_text") or "").casefold()
                        _mentions_brand = (
                            "brand" in _q_text
                            or any(bn in _q_text for bn in _known_brand_casefolded)
                        )
                        if not _mentions_brand:
                            continue
                    value_labels_by_code[_qcode] = _img_brand_map
    _data_col_set = set(data_df.columns)
    for _img_item in assignment.get("BRAND_IMAGERY", []):
        _icode = _img_item["question_code"]
        _idcols = _img_item.get("data_columns") or [_icode]
        # Skip expansion only when multiple columns already exist (genuinely expanded).
        # A single column that differs from the question_code (e.g. model set source_column
        # to q34_1_1 for question_code q34_1) still needs expansion — the model picked the
        # first dummy but didn't list all siblings; expand from _icode prefix as normal.
        if len(_idcols) > 1:
            continue
        if not _img_brand_map:
            continue
        # Detect sub-columns: {question_code}_{brand_code} in actual data
        _sub = [f"{_icode}_{bc}" for bc in sorted(_img_brand_map)
                if f"{_icode}_{bc}" in _data_col_set
                and not is_junk_brand_name(_img_brand_map[bc])]
        if not _sub:
            continue
        # Detect whether candidate sub-columns are 0/1 dummies or delimited multi-code
        # strings. When q34_1/q34_2/... are themselves attribute-level PARENT columns
        # (containing "8 11") rather than brand-level dummies, treat as delimited not
        # multi_select — otherwise fillna(0).astype(float)==1 crashes on string values.
        _sample_vals = []
        for _sc in _sub[:2]:
            _sample_vals.extend(data_df[_sc].dropna().head(3).tolist())
        _is_delimited = any(
            isinstance(v, str) and (" " in v or ";" in v or "," in v) for v in _sample_vals
        )
        _clean_map = {bc: nm for bc, nm in _img_brand_map.items() if not is_junk_brand_name(nm)}
        if _is_delimited:
            # Sub-columns are attribute parents (delimited), not brand dummies.
            # Use the first sub-column as source for delimited extraction.
            _img_item["data_columns"] = [_sub[0]]
            shape_by_code[_icode] = "delimited_multi_select"
        else:
            _img_item["data_columns"] = _sub
            shape_by_code[_icode] = "multi_select"
        value_labels_by_code[_icode] = _clean_map

    def _strip_option_tail(text: str) -> str:
        """2026-07-30: MA-battery members from codebook_parser's tail-brand-match path (see
        _extract_battery_option_text in codebook_parser.py) keep their FULL original text
        ("<b>Is a premium brand</b>: Akshayakalpa Organic?") for audit purposes — but as an
        imagery/portfolio attribute LABEL that trailing "': <brand>?'" segment would make every
        brand's row for the same real attribute look like a different attribute. Strip it back
        off here, purely textually, no state needed from the classifier. No-op if there's no
        colon or the prefix would be empty (keeps original text as a safe fallback)."""
        # Strip a leading AP-table-title cross-reference marker (see codebook_parser.py's
        # enrich_question_text_from_ap_tabplan) before anything else — it has no colon, so it
        # would otherwise survive the colon-split below and dilute both the label text and any
        # fuzzy brand-name check run against it.
        text = re.sub(r"^\[AP_TABLE_TITLE=[^\]]*\]\s*", "", text)
        prefix = text.rsplit(":", 1)[0].strip() if ":" in text else text
        # SPSS-style datamap labels often carry rich-text markup (e.g. "<b>Is a premium
        # brand</b>") meant for on-screen rendering, not as a real attribute name — strip it so
        # dim_bq3_attribute/dim_category rows show clean text regardless of source formatting.
        prefix = re.sub(r"<[^>]+>", "", prefix).strip()
        return prefix if prefix else text

    def _disambiguate(items: list, key: str = "question_text") -> dict:
        """Shared guard (originally BRAND_IMAGERY-only, generalised 2026-07-28): if a bucket's
        confirmed questions reuse the same label for multiple distinct sub-questions (a codebook
        that labels every attribute/category identically), appending the question_code keeps them
        from collapsing into one dim row via the UNIQUE constraint. Returns {question_code: label}."""
        counts: dict = {}
        for it in items:
            text = _strip_option_tail(it[key] or it["question_code"])
            counts[text] = counts.get(text, 0) + 1
        # NOT ambiguous here: many brands genuinely sharing one real attribute prefix after
        # stripping is the WHOLE POINT (one imagery attribute, many brand rows) — only flag as
        # ambiguous if the SAME final code appears with a different stripped label than expected
        # would be a real collision; plain repeats of the same attribute across brands are fine
        # and expected, so don't warn on those.
        out = {}
        for it in items:
            text = _strip_option_tail(it[key] or it["question_code"])
            out[it["question_code"]] = text
        return out

    for bucket, items in assignment.items():
        known_buckets = (BRAND_AWARENESS_BUCKETS | {"CSAT", "NPS", "BRAND_IMAGERY"} |
                          set(DEMOGRAPHIC_COLUMN_BUCKETS) | {"AGE", "CITY", "ZONE", "IMPORTANCE",
                          "ATTITUDE", "PORTFOLIO_AWARENESS", "PRICE_PAID", "PURCHASE_JOURNEY"})
        # DEMOGRAPHIC is a real, intentional bucket (see TARGET_BUCKETS docstring: income,
        # occupation, NCCS, ...) — this loader just has no generic catch-all table for it yet.
        # That's a documented, accepted gap, not an "unknown bucket key" — track separately so
        # the UI doesn't show two contradictory messages for the same thing.
        if bucket == "DEMOGRAPHIC":
            summary["accepted_not_written"].append(bucket)
            continue
        if bucket not in known_buckets:
            summary["skipped_buckets"].append(bucket)
            continue

        if bucket == "BRAND_IMAGERY":
            # 2026-07-28: each confirmed imagery question is ONE attribute (e.g. "Durable",
            # "Trusted brand") asked as a multi-select-of-brands — structurally identical to
            # SPONT/AIDED (a slash-family whose numeric suffix is the brand code), just written
            # to fact_brand_imagery instead of fact_brand_awareness. Reuses extract_brand_hits
            # unchanged; only the destination table differs.
            #
            # NOTE: do NOT use _disambiguate(items) here — it keys by question_code, which
            # collapses all attrs that share one question_code (e.g. all q34_* attrs in AK)
            # to the LAST label only. Instead take question_text directly from each item.
            for item in items:
                code = item["question_code"]
                src_col = item.get("source_column") or code
                cols = item["data_columns"]
                shape = shape_by_code.get(src_col, shape_by_code.get(code, "single"))
                vlabels = value_labels_by_code.get(src_col, value_labels_by_code.get(code, {}))
                attr_label = _strip_option_tail(item.get("question_text") or code)
                # 2026-07-30: BRAND_IMAGERY assumes "one member = one ATTRIBUTE, decode WHICH
                # BRAND from the cell value" (e.g. q34's "Is a premium brand: Amul?" tail-matched
                # batteries). Some clients instead have "one member = one BRAND, cell value is a
                # RATING SCORE for it" (e.g. Akshayakalpa's q26/q27/q30 "Love Meter"/"Relatable"/
                # "Unique" batteries — each column's text IS just the brand name, no attribute
                # text at all, routed here only via an AP table title keyword match). Extracting
                # those with the attribute-decode logic would treat rating-scale LABELS ("8",
                # "Absolutely Love it a lot") as if they were brand names — silent garbage in
                # dim_brand. Detect that inversion (the attribute label itself IS a known brand
                # name) and skip with a clear warning instead of writing wrong data; this schema
                # doesn't have a per-brand-rating-score bucket to route these to correctly yet.
                if brand_names and _canonicalize_text_brand(attr_label, brand_names, threshold=0.75):
                    summary["warnings"].append(
                        f"BRAND_IMAGERY '{code}': looks like a per-brand RATING column (its "
                        f"attribute label '{attr_label}' is itself a brand name), not an "
                        f"attribute-coded imagery question — 0 rows written. This schema has no "
                        f"per-brand rating-score bucket yet; needs manual handling."
                    )
                    continue
                hits = extract_brand_hits(data_df, cols, vlabels, shape, known_brands=brand_names,
                                           delimiter=delimiter_by_code.get(code))
                for h in hits:
                    summary["respondents_seen"].add(h.respondent_id)
                    imagery_rows.append((attr_label, h.respondent_id, h.brand_name))
            continue

        if bucket == "PORTFOLIO_AWARENESS":
            # 2026-07-28: symmetric to BRAND_IMAGERY — here each confirmed question is ONE
            # CATEGORY ("which brands make Ceiling Fans?") asked as a multi-select-of-brands, so
            # the same extract_brand_hits() call works unchanged; only the label meaning (category
            # instead of attribute) and destination table differ.
            labels = _disambiguate(items)
            for item in items:
                code = item["question_code"]
                cols = item["data_columns"]
                shape = shape_by_code.get(code, "single")
                vlabels = value_labels_by_code.get(code, {})
                hits = extract_brand_hits(data_df, cols, vlabels, shape, known_brands=brand_names,
                                           delimiter=delimiter_by_code.get(code))
                for h in hits:
                    summary["respondents_seen"].add(h.respondent_id)
                    portfolio_rows.append((labels[code], h.respondent_id, h.brand_name))
            continue

        if bucket in ("IMPORTANCE", "ATTITUDE"):
            # 2026-07-28: each confirmed question is one attribute/statement rated on a numeric
            # scale (1-7 importance, 1-5 attitude) — one value per respondent, not brand-coded.
            labels = _disambiguate(items)
            for item in items:
                code = item["question_code"]
                cols = item["data_columns"]
                vlabels = value_labels_by_code.get(code, {})
                scores = extract_numeric_score_hits(data_df, cols, vlabels)
                dropped = 0
                if cols and cols[0] in data_df.columns:
                    n_nonnull = data_df[cols[0]].notna().sum()
                    dropped = int(n_nonnull) - len(scores)
                if dropped > 0:
                    summary["warnings"].append(
                        f"{bucket} '{code}': {dropped} non-blank cell(s) could not be parsed as a "
                        f"numeric score and were skipped (not zero-filled)."
                    )
                for rid, score in scores:
                    summary["respondents_seen"].add(rid)
                    if bucket == "IMPORTANCE":
                        importance_rows.append((labels[code], rid, score))
                    else:
                        attitude_rows.append((labels[code], rid, score))
            continue

        if bucket == "PRICE_PAID":
            labels = _disambiguate(items)
            for item in items:
                code = item["question_code"]
                cols = item["data_columns"]
                vlabels = value_labels_by_code.get(code, {})
                for rid, val in extract_single_value_hits(data_df, cols, vlabels):
                    summary["respondents_seen"].add(rid)
                    price_rows.append((labels[code], rid, str(val)))
            continue

        if bucket == "PURCHASE_JOURNEY":
            # 2026-07-28: generic long-format write — see module docstring for why this isn't
            # project_1's specific pq1-5 wide shape. Handles both single-value and multi_select
            # (comma-joined selected labels) questions.
            for item in items:
                code = item["question_code"]
                cols = item["data_columns"]
                text = item["question_text"] or code
                shape = shape_by_code.get(code, "single")
                vlabels = value_labels_by_code.get(code, {})
                if shape == "multi_select":
                    hits = extract_brand_hits(data_df, cols, vlabels, shape, known_brands=brand_names,
                                               delimiter=delimiter_by_code.get(code))
                    by_resp: dict = {}
                    for h in hits:
                        by_resp.setdefault(h.respondent_id, []).append(h.brand_name)
                    for rid, vals in by_resp.items():
                        summary["respondents_seen"].add(rid)
                        journey_rows.append((code, text, rid, ", ".join(vals)))
                else:
                    for rid, val in extract_single_value_hits(data_df, cols, vlabels):
                        summary["respondents_seen"].add(rid)
                        journey_rows.append((code, text, rid, str(val)))
            continue

        if bucket in DEMOGRAPHIC_COLUMN_BUCKETS:
            col_name = DEMOGRAPHIC_COLUMN_BUCKETS[bucket]
            for item in items:
                code = item["question_code"]
                cols = item["data_columns"]
                vlabels = value_labels_by_code.get(code, {})
                for rid, val in extract_single_value_hits(data_df, cols, vlabels):
                    summary["respondents_seen"].add(rid)
                    demographic_col_updates[col_name].append((rid, val))
            continue

        if bucket == "AGE":
            # See DEMOGRAPHIC_COLUMN_BUCKETS comment: labeled -> age_band TEXT, raw numeric -> age
            # INTEGER. A codebook can label AGE as bands ("1"="18-25") or leave it open numeric
            # ("34" typed directly) — extract_single_value_hits already tells them apart (returns
            # a str only when value_labels resolved something, the raw scalar otherwise).
            for item in items:
                code = item["question_code"]
                cols = item["data_columns"]
                vlabels = value_labels_by_code.get(code, {})
                for rid, val in extract_single_value_hits(data_df, cols, vlabels):
                    summary["respondents_seen"].add(rid)
                    if isinstance(val, str):
                        age_band_updates.append((rid, val))
                    else:
                        try:
                            age_numeric_updates.append((rid, int(val)))
                        except (TypeError, ValueError):
                            continue
            continue

        if bucket in ("CITY", "ZONE"):
            target = city_updates if bucket == "CITY" else zone_updates
            for item in items:
                code = item["question_code"]
                cols = item["data_columns"]
                vlabels = value_labels_by_code.get(code, {})
                for rid, val in extract_single_value_hits(data_df, cols, vlabels):
                    summary["respondents_seen"].add(rid)
                    target.append((rid, str(val)))
            continue

        for item in items:
            code = item["question_code"]
            cols = item["data_columns"]
            shape = shape_by_code.get(code, "single")
            vlabels = value_labels_by_code.get(code, {})

            if bucket in ("CSAT", "NPS", "FAMILIARITY", "BRAND_LOVE", "BRAND_TRUST", "BRAND_QUALITY", "BRAND_RATING"):
                if not cols or cols[0] not in data_df.columns:
                    continue
                col = cols[0]
                n_dropped = 0
                for resp_id, val in zip(data_df.index, data_df[col]):
                    if val is None or (isinstance(val, float) and val != val):
                        continue
                    rid = int(resp_id) + 1
                    try:
                        score = float(val)
                    except (TypeError, ValueError):
                        n_dropped += 1
                        continue
                    summary["respondents_seen"].add(rid)
                    # Brand resolution priority: explicit item["brand"] (set by suffix-code
                    # inference in assignment_from_schema) > question_text match > skip row
                    bname = item.get("brand") or None
                    if not bname:
                        brand_item_text = item.get("question_text", "")
                        clean_t = re.sub(r"<[^>]+>", "", re.sub(r"^\[AP_TABLE_TITLE=[^\]]+\]\s*", "", str(brand_item_text))).strip()
                        bname = _canonicalize_text_brand(clean_t, brand_names) if brand_names else None
                        # Fallback: question text may be long ("Recommendation: BrandName?") causing
                        # fuzzy ratio to fall below threshold — try exact substring match instead.
                        if not bname and brand_names:
                            clean_lower = clean_t.casefold()
                            for _b in brand_names:
                                if _b.casefold() in clean_lower:
                                    bname = _b
                                    break
                    if not bname:
                        continue  # no brand resolved — skip rather than writing as "Overall"
                    if bucket == "CSAT":
                        csat_rows.append((rid, bname, score))
                    elif bucket == "NPS":
                        nps_rows.append((rid, bname, score))
                    else:
                        rating_rows.append((rid, bname, bucket, score))
                if n_dropped:
                    summary["warnings"].append(
                        f"{bucket} '{code}': {n_dropped} non-blank cell(s) could not be parsed as "
                        f"a numeric score and were skipped (not zero-filled)."
                    )
                continue

            hits = extract_brand_hits(data_df, cols, vlabels, shape, known_brands=brand_names,
                                       delimiter=delimiter_by_code.get(code))
            if not hits and shape == "battery_member" and cols:
                raw_text = str(item.get("question_text", "")).casefold()
                if not is_junk_brand_name(raw_text) and not any(code.endswith(sfx) for sfx in ("_12", "_13", "_99", "_oth", "_other")):
                    summary["warnings"].append(
                        f"{bucket} '{code}': battery member ({len(cols)} sub-column(s)) has no "
                        f"resolved brand/option identity — 0 rows written. Needs manual brand "
                        f"assignment or a resolvable sibling-sheet source; this is a real data gap, "
                        f"not extraction failure."
                    )
            for h in hits:
                summary["respondents_seen"].add(h.respondent_id)
                if bucket in EXCLUSIVE_STAGE_PRIORITY:
                    exclusive_hits[bucket].add((h.respondent_id, h.brand_name))
                else:
                    independent_rows.append((bucket, h.respondent_id, h.brand_name))

    # Resolve TOM/SPONT/AIDED to one exclusive stage per (respondent, brand) — deepest wins.
    resolved: dict[tuple, str] = {}
    for stage in EXCLUSIVE_STAGE_PRIORITY:
        for pair in exclusive_hits[stage]:
            resolved.setdefault(pair, stage)

    all_awareness_rows = [(stage, rid, brand) for (rid, brand), stage in resolved.items()]
    # Dedup non-exclusive stages before writing (two question codes → same stage → same pair)
    _seen_indep: set = set()
    _deduped_indep = []
    for row in independent_rows:
        _key = (row[0], row[1], row[2])  # (stage, respondent_id, brand_name)
        if _key not in _seen_indep:
            _seen_indep.add(_key)
            _deduped_indep.append(row)
    all_awareness_rows += _deduped_indep

    conn.executemany("INSERT OR IGNORE INTO fact_respondents (respondent_id) VALUES (?)",
                      [(rid,) for rid in summary["respondents_seen"]])

    for stage, rid, brand in all_awareness_rows:
        bid = get_or_create_brand(conn, brand, brand_id_cache, brand_aliases)
        if bid is not None:
            conn.execute(
                "INSERT OR IGNORE INTO fact_brand_awareness (respondent_id, brand_id, stage) VALUES (?,?,?)",
                (rid, bid, stage),
            )
            summary["buckets_written"][stage] = summary["buckets_written"].get(stage, 0) + 1

    for rid, brand, score in csat_rows:
        bid = get_or_create_brand(conn, brand, brand_id_cache, brand_aliases)
        if bid is not None:
            conn.execute("INSERT OR IGNORE INTO fact_satisfaction (respondent_id, brand_id, score) VALUES (?,?,?)",
                          (rid, bid, score))
    if csat_rows:
        summary["buckets_written"]["CSAT"] = len(csat_rows)

    for rid, brand, score in nps_rows:
        bid = get_or_create_brand(conn, brand, brand_id_cache, brand_aliases)
        if bid is not None:
            conn.execute("INSERT INTO fact_brand_nps (respondent_id, brand_id, nps_score) VALUES (?,?,?)",
                          (rid, bid, score))
    if nps_rows:
        summary["buckets_written"]["NPS"] = len(nps_rows)

    for rid, brand, rtype, score in rating_rows:
        bid = get_or_create_brand(conn, brand, brand_id_cache, brand_aliases)
        if bid is not None:
            conn.execute(
                "INSERT INTO fact_brand_ratings (respondent_id, brand_id, rating_type, score) VALUES (?,?,?,?)",
                (rid, bid, rtype, score),
            )
    if rating_rows:
        summary["buckets_written"]["BRAND_RATINGS"] = len(rating_rows)

    for attr_label, rid, brand in imagery_rows:
        bid = get_or_create_brand(conn, brand, brand_id_cache, brand_aliases)
        if bid is not None:
            aid = get_or_create_attr(conn, attr_label, attr_id_cache, known_brand_names=brand_names)
            if aid is not None:
                conn.execute(
                    "INSERT INTO fact_brand_imagery (respondent_id, attr_id, brand_id, value) VALUES (?,?,?,1)",
                    (rid, aid, bid),
                )
    if imagery_rows:
        summary["buckets_written"]["BRAND_IMAGERY"] = len(imagery_rows)

    for attr_label, rid, score in importance_rows:
        aid = get_or_create_attr(conn, attr_label, attr_id_cache, known_brand_names=brand_names)
        if aid is not None:
            conn.execute(
                "INSERT INTO fact_need_importance (respondent_id, attr_id, importance_score) VALUES (?,?,?)",
                (rid, aid, score),
            )
    if importance_rows:
        summary["buckets_written"]["IMPORTANCE"] = len(importance_rows)

    for attr_label, rid, rating in attitude_rows:
        aid = get_or_create_attr(conn, attr_label, attr_id_cache, known_brand_names=brand_names)
        if aid is not None:
            conn.execute(
                "INSERT INTO fact_attitudes (respondent_id, category_id, attr_id, rating) VALUES (?,NULL,?,?)",
                (rid, aid, rating),
            )
    if attitude_rows:
        summary["buckets_written"]["ATTITUDE"] = len(attitude_rows)

    for category_label, rid, brand in portfolio_rows:
        # category_label came off the confirmed question text/code, not the value-labels crosswalk
        # — build_value_crosswalk is for decoding CODED cells (the brand side here), category
        # names are taken as literally confirmed, matching the BRAND_IMAGERY attr_label pattern.
        bid = get_or_create_brand(conn, brand, brand_id_cache)
        cid = get_or_create_category(conn, category_label, category_id_cache)
        conn.execute(
            "INSERT INTO fact_portfolio_awareness (respondent_id, brand_id, category_id) VALUES (?,?,?)",
            (rid, bid, cid),
        )
    if portfolio_rows:
        summary["buckets_written"]["PORTFOLIO_AWARENESS"] = len(portfolio_rows)

    for category_label, rid, tier in price_rows:
        cid = get_or_create_category(conn, category_label, category_id_cache)
        conn.execute(
            "INSERT INTO fact_price_paid (respondent_id, category_id, price_tier) VALUES (?,?,?)",
            (rid, cid, tier),
        )
    if price_rows:
        summary["buckets_written"]["PRICE_PAID"] = len(price_rows)

    for code, text, rid, answer in journey_rows:
        conn.execute(
            "INSERT INTO fact_purchase_journey (respondent_id, question_code, question_text, answer) "
            "VALUES (?,?,?,?)",
            (rid, code, text, answer),
        )
    if journey_rows:
        summary["buckets_written"]["PURCHASE_JOURNEY"] = len(journey_rows)

    for col_name, updates in demographic_col_updates.items():
        for rid, val in updates:
            conn.execute(f"UPDATE fact_respondents SET {col_name} = ? WHERE respondent_id = ?",
                         (val, rid))
        if updates:
            summary["buckets_written"][f"DEMOGRAPHIC:{col_name}"] = len(updates)

    for rid, label in age_band_updates:
        conn.execute("UPDATE fact_respondents SET age_band = ? WHERE respondent_id = ?", (label, rid))
    for rid, age_val in age_numeric_updates:
        conn.execute("UPDATE fact_respondents SET age = ? WHERE respondent_id = ?", (age_val, rid))
    if age_band_updates or age_numeric_updates:
        summary["buckets_written"]["AGE"] = len(age_band_updates) + len(age_numeric_updates)

    city_id_cache: dict = {}
    for rid, city_name in city_updates:
        cid = get_or_create_dim(conn, "dim_city", "city_id", "city_name", city_name, city_id_cache)
        conn.execute("UPDATE fact_respondents SET city_id = ? WHERE respondent_id = ?", (cid, rid))
    if city_updates:
        summary["buckets_written"]["CITY"] = len(city_updates)

    zone_id_cache: dict = {}
    for rid, zone_name in zone_updates:
        zid = get_or_create_dim(conn, "dim_zone", "zone_id", "zone_name", zone_name, zone_id_cache)
        conn.execute("UPDATE fact_respondents SET zone_id = ? WHERE respondent_id = ?", (zid, rid))
    if zone_updates:
        summary["buckets_written"]["ZONE"] = len(zone_updates)

    # 2026-08-03: quality gate BEFORE the atomic swap — a missing/incorrect brand list doesn't
    # error anywhere above, it just quietly fills dim_brand with raw digit-code strings that never
    # resolved to a real brand name (is_junk_brand_name patterns). Catching this here, before the
    # new DB replaces the old one, turns a silent data-quality failure into a hard, unmissable one
    # instead of relying on someone noticing a warning. See
    # .planning/AKSHAYAKALPA_PIPELINE_FIX_LOG.md Round 12.
    brand_rows = conn.execute("SELECT brand_name FROM dim_brand").fetchall()
    total_brands = len(brand_rows)
    junk_brands = sum(1 for (name,) in brand_rows if is_junk_brand_name(name))
    if total_brands and (junk_brands > 5 or (junk_brands > 1 and junk_brands / total_brands > 0.12)):
        conn.close()
        os.remove(tmp_db_path)
        raise ValueError(
            f"Ingest refused: {junk_brands}/{total_brands} dim_brand rows look like unresolved "
            f"junk (raw digit-code strings, e.g. \"{next((n for n, in brand_rows if is_junk_brand_name(n)), '')}\""
            f") instead of real brand names. This almost always means the 'Known brand names for "
            f"this client' field was empty or wrong when you clicked Process — fill it in and "
            f"re-run. No database was written; the previous one (if any) is untouched."
        )

    # Write default analysis config so analytics engines can read it without hardcoding
    _project_id = os.path.basename(os.path.dirname(out_db_path))
    _brand_names_str = ",".join(brand_names) if brand_names else ""
    _default_config = {
        "dv_source": "nps",
        "dv_topbox_threshold": "9",
        "iv_source": "imagery",
        "awareness_gate_stages": "TOM,SPONT,AIDED,EVER_USED,CONSIDERATION",
        "exclude_brand_ids": "",
        "project_name": _project_id,
        "brand_names_confirmed": _brand_names_str,
    }
    for _key, _val in _default_config.items():
        conn.execute(
            "INSERT OR REPLACE INTO project_config (key, value) VALUES (?,?)",
            (_key, str(_val)),
        )

    conn.commit()
    conn.close()
    # Only now, after a clean commit+close, does the real target file get touched — any exception
    # above leaves out_db_path exactly as it was (old good DB, or nothing) and only the .tmp file
    # is left behind for inspection.
    if os.path.exists(out_db_path):
        os.remove(out_db_path)
    os.replace(tmp_db_path, out_db_path)
    summary["respondents_seen"] = len(summary["respondents_seen"])

    # Post-ingest row count sanity check — catch near-zero counts that indicate bad CTL
    _n_resp = summary["respondents_seen"] or 1
    _low_threshold = max(10, int(_n_resp * 0.10))  # < 10% of respondents = suspicious
    for _bucket, _n_rows in summary["buckets_written"].items():
        if _bucket in BRAND_AWARENESS_BUCKETS and _n_rows < _low_threshold:
            summary["warnings"].append(
                f"⚠ {_bucket}: only {_n_rows} rows written ({_n_rows/_n_resp*100:.1f}% of respondents) "
                f"— possible empty code_to_label or missing brand codes."
            )

    return summary
