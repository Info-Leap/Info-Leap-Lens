"""
OxData — Schema Explorer (Power BI style data model view)
No API calls on this page — 100% local, reads directly from SQLite DB.
Shows: Interactive ER diagram, table cards with live row counts, column details, context explanation.
"""

import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from infoleap.utils.ui_styles import (inject_pulse_styles, sidebar_context_block,
                                    scoreboard_card, section_header, empty_state,
                                    page_banner, COLORS)

BASE_DIR = Path(__file__).parent.parent

# Use db_loader to get database - downloads on each session if needed
import os
import sys

# Ensure we can find packages from project root (parent of oxdata)
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir) # This is 'oxdata'
repo_root = os.path.dirname(project_root)

if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from infoleap.db_loader import get_db_path
DB_PATH = get_db_path()

if not DB_PATH or not DB_PATH.exists():
    empty_state(f"Database not available — {DB_PATH}", icon="✗",
                action_hint="Check oxdata/data/project_1/oxdata.db exists.")
    st.stop()

# ── live row counts from DB ────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def get_row_counts(db_path: str) -> dict:
    """Row counts for EVERY real table/view in the active DB, discovered from sqlite_master —
    not a hardcoded list. A project ingested via Add Project (generic_loader.create_minimal_schema)
    has a DIFFERENT table set than project_1's original schema (no fact_kitchen_ownership/
    fact_room_appliances/dim_kitchen_appliance, but DOES have fact_satisfaction/
    fact_need_importance/fact_attitudes/etc) — a fixed list would show false '—' for tables a
    new project was never meant to have, while silently hiding the ones it actually does have
    real data in. db_path is a cache-key param (not read from the DB_PATH global) so switching
    the Active Project selector busts this cache correctly instead of showing a stale project's
    counts for up to the 300s TTL.
    """
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    names = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()]
    counts = {}
    for t in names:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            counts[t] = cur.fetchone()[0]
        except Exception:
            counts[t] = "—"
    con.close()
    return counts

@st.cache_data(ttl=300)
def get_table_columns(table: str) -> pd.DataFrame:
    con = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(f"PRAGMA table_info({table})", con)
    con.close()
    return df[["name", "type", "notnull", "pk"]].rename(
        columns={"name": "Column", "type": "Type", "notnull": "Not Null", "pk": "PK"}
    )

@st.cache_data(ttl=300)
def get_sample(table: str, n: int = 5) -> pd.DataFrame:
    if table.startswith("fact_") or table == "v_respondents" or table == "v_verbatims":
        return pd.DataFrame({"Protected": ["Data masked for privacy"]})
    con = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(f"SELECT * FROM {table} LIMIT {n}", con)
    con.close()
    return df


COUNTS = get_row_counts(str(DB_PATH))

# Apply Pulse Styles
inject_pulse_styles()


def _fmt_count(value, fallback: int) -> str:
    """counts[t] is '—' (a string) when the table/view query failed inside get_row_counts —
    .get()'s default never fires for that case since the key IS present, just with a
    placeholder value. Guard the ',' format spec here instead of assuming an int."""
    v = value if isinstance(value, (int, float)) else fallback
    return f"{v:,}"


def _as_int(value) -> int:
    """counts[t] can be '—' (string) when a table exists but its COUNT query failed — never
    trust it's already an int before arithmetic."""
    return value if isinstance(value, (int, float)) else 0


live_total = _as_int(COUNTS.get("fact_respondents", 6631)) or 6631
live_total_fmt = _fmt_count(live_total, 6631)
sidebar_context_block(brand="Survey Database", respondents=live_total)

section_header("Repository", "What Pulse reads from and whether you can trust it")

st.markdown(f"""
    <div style="background-color:#f0fdf4;padding:15px;border-radius:8px;
                border-left:5px solid #30a76a;margin-bottom:20px;">
        <span style="font-weight:bold;color:#166534;">Wave 1 data loaded and indexed</span><br>
        <span style="font-size:0.85rem;color:#166534;">
            {live_total_fmt} respondents &middot; 18 cities &middot; 4 zones &middot;
            April&ndash;June 2021 &middot;
            {_fmt_count(COUNTS.get("fact_brand_awareness"), 39842)} brand awareness events &middot; {_fmt_count(COUNTS.get("fact_brand_nps"), 10200)} NPS ratings &middot; {_fmt_count(COUNTS.get("fact_verbatims"), 6982)} verbatims
        </span>
    </div>
""", unsafe_allow_html=True)

col_sources, col_health = st.columns([1, 1])

with col_sources:
    section_header("Data Sources")
    sources = [
        ("Wave 1 Quantitative Survey",
         f"{live_total_fmt} respondents &middot; SQLite star schema"),
        ("Qualitative Transcripts",
         "233 depth interview segments &middot; fact_transcript_segments"),
        ("Verbatim Responses",
         f"{_fmt_count(COUNTS.get('fact_verbatims'), 6982)} open-ended responses &middot; fact_verbatims"),
    ]
    for name, desc in sources:
        st.markdown(
            f'<div class="pulse-card" style="padding:10px;margin-bottom:8px;">'
            f"<b>{name}</b><br>"
            f'<span style="font-size:0.75rem;color:#6b7280;">{desc}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )

with col_health:
    section_header("Coverage by Domain")
    health_data = {
        "Brand Awareness (TOM/SPONT/AIDED)": 100,
        "NPS / Brand Loyalty":               100,
        "Kitchen Appliance Ownership":        100,
        "Room Appliance Ownership":           100,
        "Brand Imagery (BQ3 attributes)":      0,
        "Qualitative Transcripts":            100,
    }
    for theme, pct in health_data.items():
        color = "#22c55e" if pct == 100 else "#ef4444"
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;'
            f'align-items:center;padding:5px 0;border-bottom:1px solid #f3f4f6;">'
            f'<span style="font-size:0.82rem;">{theme}</span>'
            f'<span style="font-weight:700;color:{color};">{pct}%</span>'
            f"</div>",
            unsafe_allow_html=True,
        )
    st.markdown("""
        <div style="font-size:0.72rem;color:#9ca3af;margin-top:8px;">
        BQ3 imagery data not yet ingested — radar/quadrant charts pending.
        </div>
    """, unsafe_allow_html=True)


# ── Interactive ER diagram (Cytoscape.js) ─────────────────────────────────────
# Each entry: (technical_id, human_label, color, description, x_px, y_px)
# Positions designed as star schema: core fact at centre, outer facts mid-ring,
# dimensions on the periphery.

_ER_NODES = [
    ("fact_respondents",       "Respondents",       "#2563EB", "Core respondent demographics & geography",         420, 185),
    ("dim_date",               "Date",              "#16A34A", "39 unique interview dates (Apr–Jun 2021)",          215,  55),
    ("dim_city",               "City",              "#16A34A", "18 Indian cities across 4 zones",                  645,  70),
    ("dim_zone",               "Zone",              "#16A34A", "North / South / West / East",                      130, 115),
    ("fact_brand_awareness",   "Brand Awareness",   "#7C3AED", "TOM / SPONT / AIDED brand recall events",          110, 285),
    ("fact_brand_nps",         "Brand NPS",         "#7C3AED", "NPS scores 0-10 per respondent x brand rated",     215, 400),
    ("fact_kitchen_ownership", "Kitchen Ownership", "#7C3AED", "Kitchen appliances owned (binary flags as rows)",  420, 445),
    ("fact_recent_purchase",   "Recent Purchase",   "#7C3AED", "Recent purchases (ranked 1 = most recent)",        625, 400),
    ("fact_room_appliances",   "Room Appliances",   "#7C3AED", "Room appliances owned (fans, AC, bulbs, etc.)",    720, 285),
    ("dim_brand",              "Brand",             "#16A34A", "56 brands (codes 1-55 + 99 = Don't Know)",          50, 415),
    ("dim_kitchen_appliance",  "Kitchen Appliance", "#16A34A", "14 kitchen appliance types",                       445, 540),
    ("dim_room_appliance",     "Room Appliance",    "#16A34A", "17 room appliance types",                          810, 410),
]

_ER_EDGES = [
    ("fact_respondents",       "dim_date"),
    ("fact_respondents",       "dim_city"),
    ("fact_respondents",       "dim_zone"),
    ("fact_brand_awareness",   "fact_respondents"),
    ("fact_brand_awareness",   "dim_brand"),
    ("fact_brand_nps",         "fact_respondents"),
    ("fact_brand_nps",         "dim_brand"),
    ("fact_kitchen_ownership", "fact_respondents"),
    ("fact_kitchen_ownership", "dim_kitchen_appliance"),
    ("fact_recent_purchase",   "fact_respondents"),
    ("fact_recent_purchase",   "dim_kitchen_appliance"),
    ("fact_room_appliances",   "fact_respondents"),
    ("fact_room_appliances",   "dim_room_appliance"),
]


def render_er_diagram(counts: dict) -> None:
    """Render a fully interactive Cytoscape.js ER diagram inside an HTML component.

    Features:
    - Drag individual nodes to rearrange
    - Scroll to zoom in/out
    - Click-drag on empty canvas to pan
    - Hover over any node for tooltip with technical name, description, row count
    - Connected edges highlight on hover
    """
    nodes_js = json.dumps([
        {
            "id":    nid,
            "human": human,
            "color": color,
            "desc":  desc,
            "x":     x,
            "y":     y,
            "rows":  f"{counts[nid]:,}" if isinstance(counts.get(nid), int) else "—",
        }
        for nid, human, color, desc, x, y in _ER_NODES
    ])

    edges_js = json.dumps([
        {"id": f"e{i}", "source": src, "target": dst}
        for i, (src, dst) in enumerate(_ER_EDGES)
    ])

    html = f"""<!DOCTYPE html>
<html>
<head>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: transparent; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
#cy {{
  width: 100%;
  height: 560px;
  background: #0F172A;
  border-radius: 8px;
  cursor: grab;
}}
#cy:active {{ cursor: grabbing; }}
#tooltip {{
  position: fixed;
  background: #1E293B;
  color: #E2E8F0;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.7;
  pointer-events: none;
  display: none;
  border: 1px solid #334155;
  max-width: 270px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.6);
  z-index: 9999;
}}
#wrapper {{ position: relative; }}
#legend {{
  position: absolute;
  bottom: 14px;
  left: 14px;
  display: flex;
  gap: 18px;
  font-size: 11px;
  color: #94A3B8;
  background: rgba(15,23,42,0.88);
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid #1E293B;
  pointer-events: none;
}}
#legend span {{ display: flex; align-items: center; gap: 6px; }}
.dot {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; flex-shrink: 0; }}
#hint {{
  position: absolute;
  top: 10px;
  right: 10px;
  font-size: 10px;
  color: #64748B;
  background: rgba(15,23,42,0.88);
  padding: 4px 10px;
  border-radius: 4px;
  pointer-events: none;
}}
</style>
</head>
<body>
<div id="wrapper">
  <div id="cy"></div>
  <div id="legend">
    <span><span class="dot" style="background:#2563EB"></span>Core fact</span>
    <span><span class="dot" style="background:#7C3AED"></span>Fact table</span>
    <span><span class="dot" style="background:#16A34A"></span>Dimension</span>
  </div>
  <div id="hint">Drag nodes &nbsp;&middot;&nbsp; Scroll to zoom &nbsp;&middot;&nbsp; Drag canvas to pan</div>
</div>
<div id="tooltip"></div>

<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.26.0/dist/cytoscape.min.js"></script>
<script>
const nodesData = {nodes_js};
const edgesData = {edges_js};

const elements = [];

nodesData.forEach(n => {{
  elements.push({{
    data: {{
      id:         n.id,
      label:      n.human + '\\n' + n.id,
      humanLabel: n.human,
      techName:   n.id,
      color:      n.color,
      desc:       n.desc,
      rows:       n.rows,
    }},
    position: {{ x: n.x, y: n.y }},
  }});
}});

edgesData.forEach(e => {{
  elements.push({{ data: {{ id: e.id, source: e.source, target: e.target }} }});
}});

const cy = cytoscape({{
  container: document.getElementById('cy'),
  elements,
  layout: {{ name: 'preset' }},
  style: [
    {{
      selector: 'node',
      style: {{
        'background-color':   'data(color)',
        'label':              'data(label)',
        'color':              '#FFFFFF',
        'text-valign':        'center',
        'text-halign':        'center',
        'font-size':          '9.5px',
        'font-family':        'system-ui, sans-serif',
        'width':              '92px',
        'height':             '46px',
        'shape':              'round-rectangle',
        'text-wrap':          'wrap',
        'text-max-width':     '86px',
        'border-width':       1.5,
        'border-color':       'rgba(255,255,255,0.15)',
        'transition-property':'border-color, border-width, background-color',
        'transition-duration':'80ms',
      }},
    }},
    {{
      selector: '#fact_respondents',
      style: {{
        'width':        '104px',
        'height':       '50px',
        'font-size':    '10px',
        'border-width': 2.5,
        'border-color': 'rgba(255,255,255,0.3)',
        'font-weight':  'bold',
      }},
    }},
    {{
      selector: 'node.hover',
      style: {{
        'border-color': '#F59E0B',
        'border-width': 3,
      }},
    }},
    {{
      selector: 'node:selected',
      style: {{
        'border-color': '#F59E0B',
        'border-width': 3,
      }},
    }},
    {{
      selector: 'edge',
      style: {{
        'width':               1.5,
        'line-color':          '#334155',
        'curve-style':         'bezier',
        'target-arrow-shape':  'vee',
        'target-arrow-color':  '#475569',
        'arrow-scale':         0.85,
        'opacity':             0.6,
        'transition-property': 'opacity, line-color, width',
        'transition-duration': '80ms',
      }},
    }},
    {{
      selector: 'edge.highlighted',
      style: {{
        'line-color':          '#60A5FA',
        'target-arrow-color':  '#60A5FA',
        'opacity':             1,
        'width':               2.5,
      }},
    }},
  ],
  userZoomingEnabled:  true,
  userPanningEnabled:  true,
  autoungrabify:       false,
  minZoom:             0.25,
  maxZoom:             4,
  wheelSensitivity:    0.25,
}});

// ── Tooltip ───────────────────────────────────────────────────────────────────
const tooltip = document.getElementById('tooltip');

cy.on('mouseover', 'node', function(e) {{
  const d = e.target.data();
  tooltip.innerHTML =
    '<div style="font-weight:700;font-size:13px;margin-bottom:2px">' + d.humanLabel + '</div>' +
    '<div style="color:#94A3B8;font-size:10px;font-family:monospace;letter-spacing:0.3px;margin-bottom:6px">' + d.techName + '</div>' +
    '<div style="margin-bottom:6px;color:#CBD5E1">' + d.desc + '</div>' +
    '<div style="color:#60A5FA;font-weight:600">' + d.rows + ' rows</div>';
  tooltip.style.display = 'block';
  e.target.addClass('hover');
  e.target.connectedEdges().addClass('highlighted');
}});

cy.on('mouseout', 'node', function(e) {{
  tooltip.style.display = 'none';
  e.target.removeClass('hover');
  e.target.connectedEdges().removeClass('highlighted');
}});

document.getElementById('cy').addEventListener('mousemove', function(e) {{
  if (tooltip.style.display !== 'none') {{
    tooltip.style.left = (e.clientX + 18) + 'px';
    tooltip.style.top  = (e.clientY - 10) + 'px';
  }}
}});

document.getElementById('cy').addEventListener('mouseleave', function() {{
  tooltip.style.display = 'none';
}});
</script>
</body>
</html>"""

    components.html(html, height=580, scrolling=False)


# ── view cards data ────────────────────────────────────────────────────────────
VIEW_CARDS = [
    {
        "name": "v_respondents",
        "icon": "👤",
        "purpose": "One row per respondent with all demographics & geography resolved.",
        "key_cols": "respondent_id, gender, age, age_band, city_name, zone_name, interview_date",
        "use_for": "Filter by city, zone, gender, date. Base for all percentages.",
    },
    {
        "name": "v_brand_awareness",
        "icon": "📢",
        "purpose": "One row per respondent × brand × awareness stage.",
        "key_cols": "respondent_id, stage (TOM/SPONT/AIDED), rank, brand_name",
        "use_for": "Brand funnel analysis — TOM%, spontaneous%, total awareness%.",
    },
    {
        "name": "v_brand_nps",
        "icon": "⭐",
        "purpose": "One row per respondent × brand NPS rating.",
        "key_cols": "respondent_id, brand_name, nps_score (0-10), nps_category",
        "use_for": "NPS scores, promoter/detractor breakdowns, brand loyalty.",
    },
    {
        "name": "v_kitchen_ownership",
        "icon": "🍳",
        "purpose": "One row per respondent × kitchen appliance owned.",
        "key_cols": "respondent_id, appliance_name",
        "use_for": "Appliance penetration rates, ownership by demographic.",
    },
    {
        "name": "v_recent_purchase",
        "icon": "🛒",
        "purpose": "One row per respondent × recently purchased appliance (ranked).",
        "key_cols": "respondent_id, purchase_rank (1–3), appliance_name",
        "use_for": "Which appliances were bought most recently. Rank 1 = most recent.",
    },
    {
        "name": "v_room_appliances",
        "icon": "🏠",
        "purpose": "One row per respondent × room appliance owned.",
        "key_cols": "respondent_id, appliance_name",
        "use_for": "Fan/AC/bulb/geyser ownership rates by city or zone.",
    },
]

TABLE_CARDS = {
    "Fact Tables": {
        "color": "#7C3AED",
        "tables": [
            {"name": "fact_respondents",       "desc": "Core respondent row. All 6,631 interviews."},
            {"name": "fact_brand_awareness",   "desc": "TOM / SPONT / AIDED recall events."},
            {"name": "fact_brand_nps",         "desc": "Per-brand NPS ratings (sparse — only rated brands)."},
            {"name": "fact_kitchen_ownership", "desc": "Kitchen appliance binary flags expanded to rows."},
            {"name": "fact_recent_purchase",   "desc": "Recent purchase selections with rank order."},
            {"name": "fact_room_appliances",   "desc": "Room appliance binary flags expanded to rows."},
            {"name": "fact_verbatims",         "desc": "Open-ended text responses (bq2a, others)."},
        ]
    },
    "Dimension Tables": {
        "color": "#16A34A",
        "tables": [
            {"name": "dim_brand",             "desc": "56 brand codes → names"},
            {"name": "dim_city",              "desc": "18 cities → zone mapping"},
            {"name": "dim_zone",              "desc": "4 zones (North/South/West/East)"},
            {"name": "dim_kitchen_appliance", "desc": "14 kitchen appliance types"},
            {"name": "dim_room_appliance",    "desc": "17 room appliance types"},
            {"name": "dim_date",              "desc": "39 interview dates with year/month/quarter"},
        ]
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

section_header("Schema Explorer", "Data model for Project 1 — OX Wave 1. No API calls on this page.")

# ── top stats bar ──────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
from infoleap.utils.ui_styles import kpi_card as _repo_kpi
with c1: _repo_kpi("Respondents", _fmt_count(COUNTS.get('fact_respondents'), 0), "#1a5d4d")
with c2: _repo_kpi("Brand Events", _fmt_count(COUNTS.get('fact_brand_awareness'), 0), "#0ea5e9")
with c3: _repo_kpi("NPS Ratings", _fmt_count(COUNTS.get('fact_brand_nps'), 0), "#7c3aed")
with c4: _repo_kpi("Appliance Rows",
                   f"{_as_int(COUNTS.get('fact_kitchen_ownership')) + _as_int(COUNTS.get('fact_room_appliances')):,}",
                   "#f59e0b")
with c5: _repo_kpi("Views Available", "6", "#10b981")

st.divider()

import os

# ── tabs ───────────────────────────────────────────────────────────────────────
if os.environ.get("ST_PROD_MODE") == "true":
    tab_er, tab_views, tab_tables, tab_live = st.tabs([
        "ER Diagram",
        "Views (query these)",
        "Raw Tables",
        "🔍 This Project's Data (live)",
    ])
    tab_context = None
else:
    tab_er, tab_views, tab_tables, tab_live, tab_context = st.tabs([
        "ER Diagram",
        "Views (query these)",
        "Raw Tables",
        "🔍 This Project's Data (live)",
        "How Context Works",
    ])

# ── TAB 1: ER DIAGRAM ─────────────────────────────────────────────────────────
with tab_er:
    section_header("Entity Relationship Diagram — Star Schema")
    st.caption(
        "Drag any node to reposition it. Scroll to zoom. Drag the background to pan. "
        "Hover over a node for details and row count."
    )
    render_er_diagram(COUNTS)

    st.markdown("---")
    st.markdown(
        """
        **Data Model Summary (Schema Fallback):**
        
        The database follows a **Star Schema** architecture optimized for fast retrieval of market research metrics:
        
        | Table / View | Type | Purpose | Key Joins |
        |:---|:---|:---|:---|
        | **v_respondents** | View | Core respondent hub | respondent_id |
        | **v_brand_awareness** | View | TOM / Spont / Aided metrics | respondent_id, brand_name |
        | **v_brand_nps** | View | NPS ratings & categories | respondent_id, brand_name |
        | **v_kitchen_ownership**| View | Appliance penetration | respondent_id, appliance_name |
        | **v_room_appliances** | View | Fan/AC/Geyser ownership | respondent_id, appliance_name |
        | **v_verbatims** | View | Open-ended text responses | respondent_id |
        | **dim_brand** | Table | Brand name lookup (56 brands) | brand_id |
        | **dim_city** | Table | 18 cities across 4 zones | city_id |
        | **dim_date** | Table | Fieldwork timeline (Apr-Jun 21) | date_id |
        
        **Reading the diagram:**
        - **Blue (centre):** `fact_respondents` — the hub every other fact joins to.
        - **Purple:** Fact tables — one row per event (a brand mention, an NPS rating, an appliance owned).
        - **Green:** Dimension tables — lookup tables for codes → labels (brand names, city names, etc.)
        - **Arrows:** Foreign key direction. All views pre-join these so the LLM never needs to write JOINs.
        """
    )

# ── TAB 2: VIEWS ──────────────────────────────────────────────────────────────
with tab_views:
    section_header("6 Pre-joined Views — Always query these in the chat")
    st.info(
        "Views join all dimension labels into the fact data. When you chat, the LLM is told "
        "to query views only — this means it writes simpler SQL and is less likely to hallucinate column names.",
        icon="ℹ️",
    )

    for card in VIEW_CARDS:
        cnt = COUNTS.get(card["name"], "—")
        badge = f"{cnt:,} rows" if isinstance(cnt, int) else cnt
        with st.expander(f"{card['icon']}  **{card['name']}** — {badge}", expanded=False):
            col_l, col_r = st.columns([2, 1])
            with col_l:
                st.markdown(f"**Purpose:** {card['purpose']}")
                st.markdown(f"**Key columns:** `{card['key_cols']}`")
                st.markdown(f"**Use for:** {card['use_for']}")
            with col_r:
                st.markdown("**Column list:**")
                try:
                    cols_df = get_table_columns(card["name"])
                    st.dataframe(cols_df, use_container_width=True, hide_index=True, height=220)
                except Exception:
                    st.caption("(Run ETL to populate DB)")

            st.markdown("**Sample rows:**")
            try:
                sample = get_sample(card["name"], 3)
                st.dataframe(sample, use_container_width=True, hide_index=True)
            except Exception:
                st.caption("(No data yet)")

# ── TAB 3: RAW TABLES ─────────────────────────────────────────────────────────
with tab_tables:
    section_header("Raw Tables — For reference only (chat queries the views)")

    for category, info in TABLE_CARDS.items():
        st.markdown(f"##### {category}")
        col_groups = st.columns(3)
        for i, table in enumerate(info["tables"]):
            cnt = COUNTS.get(table["name"], "—")
            badge = f"{cnt:,}" if isinstance(cnt, int) else cnt
            with col_groups[i % 3]:
                with st.container(border=True):
                    st.markdown(
                        f"**{table['name']}**  \n"
                        f"<span style='color:#94A3B8;font-size:12px'>{badge} rows</span>",
                        unsafe_allow_html=True,
                    )
                    st.caption(table["desc"])
                    with st.expander("Columns", expanded=False):
                        try:
                            cols_df = get_table_columns(table["name"])
                            st.dataframe(
                                cols_df[["Column", "Type", "PK"]],
                                use_container_width=True, hide_index=True, height=180,
                            )
                        except Exception:
                            st.caption("—")
        st.markdown("")

# ── TAB: THIS PROJECT'S DATA (live, schema-agnostic) ──────────────────────────
# 2026-07-30: the ER diagram + VIEW_CARDS/TABLE_CARDS above are hand-curated for project_1's
# ORIGINAL schema (fact_kitchen_ownership, dim_kitchen_appliance, etc) — a project ingested
# through Add Project (generic_loader.create_minimal_schema) has a DIFFERENT table set (no
# kitchen/room-appliance tables, but real data in fact_satisfaction/fact_need_importance/
# fact_attitudes/fact_portfolio_awareness/fact_price_paid/fact_purchase_journey instead). Switching
# Active Project to one of those would make the tabs above show mostly "—"/empty for tables that
# were never expected to exist, while silently never showing the tables that DO have real data —
# no way to verify the backend actually got written correctly. This tab is schema-agnostic: it
# lists EVERY real table in whatever DB is currently active (via get_row_counts' sqlite_master
# introspection), so it's correct for project_1 AND for any newly ingested project alike.
with tab_live:
    section_header("Every table in the currently active database",
                    f"Active project DB: {DB_PATH}")
    st.caption(
        "Unlike the tabs above (hand-curated for project_1's original schema), this list is "
        "generated live from the actual database file — correct for any project, including one "
        "just ingested through Add Project with a different table set."
    )
    _live_names = sorted(COUNTS.keys())
    if not _live_names:
        st.info("No tables found in this database.")
    else:
        _cols = st.columns(3)
        for i, name in enumerate(_live_names):
            cnt = COUNTS.get(name, "—")
            badge = f"{cnt:,} rows" if isinstance(cnt, (int, float)) else str(cnt)
            with _cols[i % 3]:
                with st.container(border=True):
                    st.markdown(
                        f"**{name}**  \n"
                        f"<span style='color:#94A3B8;font-size:12px'>{badge}</span>",
                        unsafe_allow_html=True,
                    )
                    with st.expander("Columns + sample rows", expanded=False):
                        try:
                            cols_df = get_table_columns(name)
                            st.dataframe(cols_df, use_container_width=True, hide_index=True,
                                         height=180)
                        except Exception as e:
                            st.caption(f"Columns unavailable: {e}")
                        try:
                            sample = get_sample(name, 5)
                            st.markdown("**Sample rows:**")
                            st.dataframe(sample, use_container_width=True, hide_index=True)
                        except Exception as e:
                            st.caption(f"Sample unavailable: {e}")

# ── TAB 4: HOW CONTEXT WORKS ──────────────────────────────────────────────────
with tab_context:
    section_header("How the LLM Understands the Database")

    st.markdown(
        "When you type a question in the chat, here is exactly what gets sent to the Groq API:"
    )

    c_left, c_right = st.columns(2)

    with c_left:
        st.markdown("##### 1. System Prompt (skill-specific, 350–900 tokens)")
        st.code(
            """[system]
You are a SQL analyst for a SQLite survey database.

=== RULES ===
1. Return ONLY raw SQL. No fences. No comments.
2. Query VIEWS only (never raw fact_ tables).
3. Penetration %: ROUND(count*100.0 /
   (SELECT COUNT(*) FROM fact_respondents), 1)
4. Always include count + pct columns.
5. Carry forward prior filters on follow-up.
6. Never invent column names.

=== VIEW: v_brand_nps  (10,200 rows) ===
  respondent_id, brand_name
  nps_score (0-10), nps_category
  + gender, age, city_name, zone_name, ...

NPS = ROUND((%promoters - %detractors)*100, 1)
Use HAVING COUNT(*) >= 50 to filter sparse brands.

=== EXAMPLES ===
-- NPS by brand
SELECT brand_name, COUNT(*) raters,
  ROUND((SUM(CASE WHEN nps_score>=9 THEN 1.0 ELSE 0 END)
       - SUM(CASE WHEN nps_score<=6 THEN 1.0 ELSE 0 END))
    * 100.0 / COUNT(*), 1) nps
FROM v_brand_nps
GROUP BY brand_name HAVING COUNT(*) >= 50
ORDER BY nps DESC;
""",
            language="text",
        )

    with c_right:
        st.markdown("##### 2. Prior Conversation (last 4 turns, Q+SQL only)")
        st.code(
            """[user]   How many respondents are from Patna?
[asst]   SELECT city_name, COUNT(*) ...
         WHERE city_name = 'Patna'

[user]   Tell me their details
         ^ "their" resolved because prior SQL
           had WHERE city_name = 'Patna'.
           LLM carries this filter forward.
""",
            language="text",
        )

        st.markdown("##### 3. Your question")
        st.code("[user]   Tell me their details", language="text")

        st.markdown("##### Token budget per call (with Skill Foundry)")
        st.dataframe(
            pd.DataFrame([
                ["Skill system prompt",           "350–900",   "Varies by skill routed to"],
                ["Prior turns (Q+SQL, 4 turns)",  "~400 max",  "Grows with conversation"],
                ["Your question",                 "~15",       "Variable"],
                ["Total input",                   "~800–1,300","Was 6,500 before BUG-008"],
                ["SQL output",                    "~80",       "Short — SQL is concise"],
            ], columns=["Component", "Tokens (est.)", "Notes"]),
            hide_index=True, use_container_width=True,
        )

    st.divider()
    st.markdown(
        """
        ##### How the Skill Foundry reduces tokens

        Instead of loading the full schema for every question, the router classifies your
        question by keyword (zero API calls) and only injects the relevant skill's schema slice.

        | Skill routed | Schema loaded | Est. tokens |
        |---|---|---|
        | NPS / Brand Ratings | `v_brand_nps` only | ~500 |
        | Brand Awareness | `v_brand_awareness` only | ~550 |
        | Kitchen Ownership | `v_kitchen_ownership` only | ~380 |
        | Room Appliances | `v_room_appliances` only | ~380 |
        | Recent Purchases | `v_recent_purchase` only | ~400 |
        | Respondents | `v_respondents` only | ~450 |
        | General (fallback) | Overview of all 6 views | ~900 |

        ##### Why pre-joined views matter for the LLM

        Without views, the LLM would need to write:
        ```sql
        SELECT db.brand_name, COUNT(*) FROM fact_brand_awareness fba
        JOIN dim_brand db ON fba.brand_id = db.brand_id
        JOIN fact_respondents fr ON fba.respondent_id = fr.respondent_id
        JOIN dim_city dc ON fr.city_id = dc.city_id
        WHERE dc.city_name = 'Mumbai'
        GROUP BY db.brand_name
        ```

        With views, it just writes:
        ```sql
        SELECT brand_name, COUNT(*) FROM v_brand_awareness
        WHERE city_name = 'Mumbai' GROUP BY brand_name
        ```

        Simpler SQL = fewer hallucinations, fewer tokens in the system prompt
        (no need to explain all the join keys).
        """
    )
