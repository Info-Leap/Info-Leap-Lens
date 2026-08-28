# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Planning Docs â€” Read These First

All key planning documents live in `.planning/`:

| Doc | What it covers |
|-----|---------------|
| `.planning/PRD.md` | What we're building, target users, confirmed features, open gaps |
| `.planning/ARCHITECTURE.md` | App flow, folder structure, tech stack, DB schema, multi-project design |
| `.planning/RULES.md` | Coding rules, libraries, error handling, AI boundaries, what to avoid |
| `.planning/PHASOR.md` | Phase history (1â€“7), current status, what's done vs open |
| `.planning/DESIGN.md` | All UI sections, chart types, data shown, color standards, layout decisions |
| `.planning/SESSION_MEMORY.md` | Current focus, recently completed, open problems, DB row counts, gotchas |

**Before starting any task:** Read `SESSION_MEMORY.md` for current state, then the relevant section of `ARCHITECTURE.md` or `DESIGN.md`.  
**After completing a task:** Update `SESSION_MEMORY.md` (recently completed + open problems) and `PHASOR.md` if a phase changed.

## Project Overview

InfoLeap Pulse â€” a Streamlit-based market research intelligence platform. Survey data from 6,631 respondents across 6 electrical appliance categories, 18 Indian cities. The AI agent (`infoleap/`) answers natural language questions over the survey database.

**All application code lives in `infoleap/`. Subfolders: `views/`, `analytics/`, `ingestion/`, `skills/`, `utils/`, `auth/`, `gdrive/`.**

## Running the App

```bash
# From project root
streamlit run infoleap/app.py

# Frontend (React, separate process â€” optional)
cd infoleap/frontend && npm run dev
```

No build step for the Streamlit app. Python dependencies:
```bash
pip install -r infoleap/requirements.txt
pip install -r infoleap/requirements.txt
```

## Universal Memory System (Graphify & Claude-Mem)

This codebase features an integrated dual-memory architecture accessible across all agentic CLIs and IDEs (Claude Code, Gemini CLI, Antigravity, Cursor, Windsurf, Copilot):

1. **Graphify (Structural Knowledge Graph)**: AST parses python files and database views (`v_*`) into `.memory/graph.json` (5,000+ nodes, 10,000+ edges).
2. **Claude-Mem (Persistent Observational Memory)**: SQLite database `.memory/claude_mem.sqlite` storing session observations and architectural rules.

### Memory CLI Commands
```bash
# Rebuild structural knowledge graph
py -3.12 -m memory_system.cli build

# Query codebase relationships (modules, classes, views)
py -3.12 -m memory_system.cli query-graph <term>

# Search persistent memory & architectural rules
py -3.12 -m memory_system.cli search-mem <query>

# Record new learning/observation
py -3.12 -m memory_system.cli add-obs <category> <content>
```

## Architecture

### Entry Point & Navigation

`infoleap/app.py` â€” sets page config, injects styles, initializes `ContextEngine`, and wires Streamlit multi-page navigation. Pages live in `infoleap/views/`.

### Pages (`infoleap/views/`)

| Page | File | What it does |
|------|------|--------------|
| Home | `dashboard.py` | Summary metrics |
| Ask Pulse | `chat.py` | NL Q&A over survey data via AI agent |
| Brand Health | `brand_health.py` | Awareness funnel, NPS, imagery radar |
| Investigations | `investigation.py` | Deep-dive analysis |
| Signals | `signals.py` | Alert / anomaly detection |
| Quote Explorer | `quote_explorer.py` | Verbatim search |
| Repository | `repository.py` | Raw data browser |

### AI Agent (`infoleap/researcher_agent.py`)

Multi-model orchestration via OpenRouter. Entry: `run_autonomous_research_async()`. Routes queries through:
- `skills/semantic_router.py` â†’ intent classification
- `skills/thinker.py` â†’ SQL plan generation
- `skills/bq3_engine.py` â†’ brand perception analytics
- `skills/qual_retriever.py` â†’ qualitative synthesis

**Analytics tools (registered as @orchestrator.tool, ~line 887):**
- `statistical_significance` â€” pooled two-proportion z-test
- `perceptual_map` â€” correspondence analysis plot
- `imagery_profile` â€” top-N BQ3 brand associations
- `driver_regression` â€” logistic key-driver via R bridge
- `segment_difference_test` â€” ANOVA/t-test across segments

All 5 registered in Â§9 TOOL GUIDE prompt. Source: `infoleap/skills/analytics_tools.py`.

### Database

SQLite at `infoleap/data/project_1/oxdata.db` (~28MB). Star schema:

**Fact tables (original):** `fact_respondents`, `fact_brand_awareness`, `fact_brand_nps`, `fact_kitchen_ownership`, `fact_recent_purchase`, `fact_room_appliances`, `fact_verbatims`, `fact_brand_imagery` (442K BQ3b rows)

**Fact tables (new â€” ingested via `infoleap/ingestion/ingest_missing_vars.py`):**
- `fact_brand_awareness` also contains stages `EVER_USED`, `CURRENT_USER`, `CONSIDERATION`, `PREFERRED`, `LAST_PURCHASED` (full bq1d-h funnel, 34K rows)
- `fact_satisfaction` â€” CSAT score 0-10 (bq5), 4,704 respondents (recent buyers only)
- `fact_need_importance` â€” 258K rows, attr_id Ã— respondent Ã— importance score 1-7 (bq3a)
- `fact_price_paid` â€” price tier per respondent Ã— category (bq0b)
- `fact_purchase_journey` â€” 26K rows, pq1-5 (why bought, where researched, channel, who decided)
- `fact_portfolio_awareness` â€” 73K rows, which categories consumer associates with each brand (bq6)
- `fact_attitudes` â€” 35K rows, category attitude statements 1-5 rating (aq4)

**Dimension tables:** `dim_brand`, `dim_city`, `dim_zone`, `dim_date`, `dim_kitchen_appliance`, `dim_room_appliance`, `dim_bq3_attribute`

**Views (use these in queries):** `v_respondents`, `v_brand_awareness`, `v_brand_nps`, `v_kitchen_ownership`, `v_recent_purchase`, `v_room_appliances`, `v_brand_imagery`, `v_satisfaction`, `v_need_importance`, `v_purchase_journey`, `v_portfolio_awareness`, `v_key_drivers`

**`v_key_drivers`** â€” key analytical view: joins preaggregated importance (bq3a) + brand association % (bq3b) per attribute Ã— brand. Use for Importance-Performance quadrant analysis. Query takes ~3.5s; filter by `brand_id` first.

`get_db_path()` in `infoleap/db_loader.py` resolves the DB path â€” always call this, never hardcode.

**Critical:** The `category` column in `fact_respondents` is `NULL` for all rows in the current dataset. Category filtering via this column has no effect. Brand awareness data does NOT have a category dimension in the current wave.

### Analytics Engine (`lens/analytics/`)

- `brand_imagery_engine.py` â€” awareness funnel + NPS metrics for Brand Health page
- `insight_engine.py` â€” textual insights + Gemini-powered narratives
- `brand_imagery_renderer.py` â€” chart spec generation
- `can_map_engine.py` â€” correspondence analysis (CAN MAP), top_attrs=50, zero-row guard
- `bip_engine.py` â€” BIP normalization, top_brands param, NaN denom guard
- `driver_analysis_engine.py` â€” key driver regression (OLS + logistic via R bridge)
- `brand_narrative.py` â€” AI brand narrative; Rater Depth self-normalizing; NPS respondent-weighted
- `kano_engine.py` â€” derived Kano via PRCA (Penalty-Reward Contrast from CSATÃ—imagery)
- `maxdiff_engine.py` â€” MaxDiff-like preference shares via importance softmax (bq3a); has `caveat` field
- `synthetic_models.py` â€” TRUE textbook Kano/MaxDiff/TURF on customizable synthetic data (XLSTAT output structure)

**R bridge:** `infoleap/skills/r_bridge.py` â†’ calls Rscript subprocess. Scripts in `infoleap/r_scripts/`:
- `logistic_regression.R` â€” binary key-driver (imagery â†’ top-box NPS); exp() clamped Â±15/5 to prevent Inf
- `driver_regression.R`, `cronbach_alpha.R`, `anova_analysis.R`, `factor_analysis.R`

**Note:** BQ3 imagery data (442K rows) in `fact_brand_imagery`. BQ3a importance (258K rows) in `fact_need_importance`. `v_key_drivers` joins them for Importance-Performance analysis. `fact_brand_imagery.value` is ALWAYS 1 â€” assoc% = COUNT(DISTINCT respondent)/base, never AVG(value).

### Multi-Project Ingestion Pipeline (`infoleap/ingestion/`, `infoleap/views/add_project.py`)

Lets a new client's raw survey data be onboarded through the app itself â€” no hand-written ingest
script per client. Three phases, all documented session-by-session in
`.planning/MULTIPROJECT_INGESTION_LOG_2026-07-27.md` (read this before touching any of these files
â€” it has the actual bugs found/fixed and why each design choice was made):

1. **Classify** (`infoleap/ingestion/codebook_parser.py`) â€” turns a client's codebook + data file into
   a per-question mapping report (guessed bucket + confidence + evidence), never writes anything.
   Three codebook shapes, three reconciliation strategies:
   - `xlsform` â€” codebook question code matches the data file's column name directly
     (`reconcile_columns`). Default, highest trust.
   - `ap_tabplan` â€” no shared naming at all, but codebook row order â‰ˆ data column order
     (`reconcile_columns_positional`). Confidence hard-capped at 0.5.
   - `doc_prose` â€” only a legacy `.doc` questionnaire exists (real text, no machine-readable
     structure at all). `prose_questionnaire_parser.py` uses an LLM to extract question text/answer
     options from the flattened prose (plus a dedicated second pass for the shared brand-code grid
     these questionnaires almost always have â€” see that module's docstring), then
     `ai_crosswalk_match()` matches each question to a real data column/family by structural
     fingerprint (does the family's dummy-code suffix set or a column's observed values match the
     question's option codes?) with an LLM arbitrating among the structurally-plausible candidates
     only. Confidence capped at 0.65. Requires an OpenRouter key + MS Word (win32com) â€” no non-AI
     fallback exists for this shape.
   Value labels get fuzzy-matched against `dim_brand`/`dim_bq3_attribute`/a caller-supplied category
   list to guess the bucket â€” this is why a brand-new client's OWN brand names (not yet in any
   `dim_brand`) will legitimately show low confidence until a human confirms them once.

2. **Confirm + write** (`infoleap/ingestion/generic_loader.py`) â€” human reviews/corrects the bucket
   assignment in `add_project.py`, then `load_confirmed_assignment()` writes real rows into a FRESH
   `infoleap/data/<project id>/oxdata.db` (same fixed schema/views as project_1, never appended into
   project_1 itself â€” that write path is hard-blocked in the UI). Buckets written: the full
   awareness funnel (TOM/SPONT/AIDED/EVER_USED/CURRENT_USER/CONSIDERATION/PREFERRED/
   LAST_PURCHASED), CSAT, NPS, BRAND_IMAGERY, IMPORTANCE, ATTITUDE, PORTFOLIO_AWARENESS,
   PRICE_PAID, PURCHASE_JOURNEY, GENDER, AGE, CITY, ZONE. Not yet written: the generic
   DEMOGRAPHIC catch-all (income/occupation/NCCS/...).

3. **Switch** (`infoleap/db_loader.py::get_db_path(project_id=...)`, sidebar "Active Project"
   selector in `app.py`) â€” every `@st.cache_data`-wrapped page function resolves its DB path
   internally, so switching projects requires `st.cache_data.clear()` on the actual change (not
   every rerun) â€” see `app.py`'s project-switcher block for why the "previous project" comparison
   can't default to the just-updated session-state value.

### Project Config (`infoleap/config/project_1.py`)

Single source of truth for: brand IDâ†’name mappings, dimension codes, SQL view schemas, skill routing keywords, NPS/awareness benchmarks, and competitor groupings. All analytics and LLM prompts reference this file.

### Session State

`ContextEngine` (`infoleap/utils/context.py`) initializes three session-state keys on boot: `active_category`, `active_brand`, `active_zone`. Brand Health page reads `active_category` from session state â€” there is no in-page category selector; context must be set from another page or programmatically.

## Key Patterns

**Adding a new page:** Create `infoleap/views/mypage.py` and register it in the `pg = st.navigation({...})` dict in `infoleap/app.py`.

**Querying the DB:** Always use the `v_*` views, not raw fact tables. The views join demographics automatically. Use `get_db_path()` to resolve the path.

**LLM routing:** Query intent â†’ `semantic_router.py` (keyword match) â†’ capability-specific SQL generation â†’ result rendering via `chart_renderer.py`. Skill config (keywords, view names) lives in `config/project_1.py::CAPABILITIES` and `KEYWORDS`.

**Chart rendering:** `infoleap/views/chart_renderer.py` takes a `chart_spec` dict from the agent and renders Plotly charts. Supported types match the spec keys in `bq3_metadata.json`.

## Environment Variables

Stored in `infoleap/.env`:
```
OPENROUTER_API_KEY=...
OPENROUTER_MODEL_PRO=meta-llama/llama-3.3-70b-instruct
OPENROUTER_MODEL_ALT=openai/gpt-4.1-mini
OPENROUTER_MODEL_MINI=openai/gpt-4o-mini
GOOGLE_API_KEY=...          # Gemini (used in InsightEngine narratives)
SHEETS_LOG_ID=...           # Google Sheets audit log
GOOGLE_SHEETS_CREDS=...     # Path to service account JSON
```

## Related Project

KARMA-OS agent system lives at `D:\antigravity project\karma - zeroclaw\`. The `.mindos/SOUL.md` there defines agent identities and permissions. `infoleap/` is Antigravity's primary coding domain.
