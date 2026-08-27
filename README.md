# InfoLeap Pulse

Market research intelligence platform. Survey data from Indian consumer research studies — multiple clients, multiple waves — surfaced through a Streamlit analytics app with an AI agent.

---

## What's been built

### Phase 1 — Foundation
SQLite star schema database. 6,631 respondents across 6 electrical appliance categories, 18 Indian cities. Fact tables: `fact_respondents`, `fact_brand_awareness` (8-stage funnel), `fact_brand_imagery` (442K BQ3 rows), `fact_brand_nps`, `fact_satisfaction`, `fact_need_importance`, `fact_price_paid`, `fact_purchase_journey`, `fact_portfolio_awareness`, `fact_attitudes`. Dimension tables: `dim_brand` (56 brands), `dim_city`, `dim_zone`, `dim_date`, `dim_bq3_attribute` (93 attributes). 11 SQL views (`v_*`) that join demographics automatically. Basic Streamlit shell: Dashboard, Chat, Repository pages.

### Phase 2 — Brand Health Dashboard
Full analytics suite for brand tracking studies.

- **Awareness funnel** — 8 stages: TOM → Spontaneous → Aided → Ever Used → Current User → Consideration → Preferred → Last Purchased
- **NPS** — Net Promoter Score gauge, league-rank card, respondent-weighted across brands
- **CSAT** — 0–10 satisfaction score (recent buyers only)
- **CAN MAP** — Correspondence analysis perceptual map (PCA), top 50 attributes, axis poles + quadrant tints
- **BIP** — Brand Image Profile normalized grid across brands
- **Driver Analysis** — Logistic regression via R bridge (`oxdata/r_scripts/logistic_regression.R`), exp() clamped ±15/5 to prevent Inf. OLS + logistic scripts for key drivers.
- **Brand Narrative** — Gemini-powered AI summaries per brand: funnel story, imagery, NPS league, city-level insight, positioning. 9 AI keys, Rater Depth self-normalizing.

### Phase 3 — Deterministic Query Pipeline (LENS 4.0)
Replaced a 6–8 LLM call orchestrator with a 2-call deterministic pipeline.

- `skills/example_store.py` — TF-IDF few-shot retrieval (30+ seeded examples)
- `skills/schema_registry.py` — runtime DB introspection, eliminates hardcoded DATA_DICTIONARY
- `skills/deterministic_pipeline.py` — 2-call structured query handler
- `skills/chart_tools.py` — 8 brand health chart types as fast path
- Hybrid router in `chat.py` — deterministic path for structured queries, full agent fallback for open questions

### Phase 4 — Executive Analytics + Advanced Models
- Executive KPI strip: TOM, NPS, CSAT, Brand Equity Index, funnel conversion rates
- AI-generated funnel headline (session-cached per brand × project)
- **Kano analysis** — derived via PRCA (Penalty-Reward Contrast from CSAT × imagery)
- **MaxDiff-like preference shares** — importance softmax on BQ3a data
- **TURF analysis** — reach optimization on attribute importance
- **ModelLab** — TRUE textbook Kano/MaxDiff/TURF on customizable synthetic data matching XLSTAT output structure
- 5 registered analytics tools in Ask Pulse: `statistical_significance`, `perceptual_map`, `imagery_profile`, `driver_regression`, `segment_difference_test`
- 5 R scripts: `logistic_regression.R`, `driver_regression.R`, `cronbach_alpha.R`, `anova_analysis.R`, `factor_analysis.R`

Brand Health page: 6 tabs — Executive / Awareness / Imagery & Analytics / Competitive / Loyalty / Advanced Models.

### Phase 5 — Dashboard Excellence + C-Suite Views
- `_resp_filter_cte` pattern — consistent filter chips + reset button across all analytics sections
- **Exec Command Briefing** — three C-suite lenses: CEO (brand equity), CMO (brand funnel), Product (attribute importance)
- Readable perceptual map with axis poles labeled and quadrant tints
- **Diverging Likert** — Consumer Attitudes section with diverging bar chart
- Demographic insight callout boxes
- **Concept Testing Dashboard** (`views/concept_testing_renderer.py`) — CoinDCX 5-section study with per-section quote browser and AI regeneration

### Phase 6 — Multi-Project Ingestion Pipeline
Any client survey can be onboarded through the app UI — no hand-written ingestion script per client.

Three codebook shapes supported:
- `xlsform` — codebook column codes match data file column names directly (highest confidence)
- `ap_tabplan` — no shared naming, reconciled by column order (confidence capped at 0.50)
- `doc_prose` — legacy Word questionnaire only; LLM extracts question text and answer options, second pass handles shared brand-code grids, structural fingerprint matching for crosswalk (confidence capped at 0.65)

22 target buckets: TOM / SPONT / AIDED / EVER\_USED / CURRENT\_USER / CONSIDERATION / PREFERRED / LAST\_PURCHASED / CSAT / NPS / BRAND\_IMAGERY / IMPORTANCE / ATTITUDE / PORTFOLIO\_AWARENESS / PRICE\_PAID / PURCHASE\_JOURNEY / GENDER / AGE / CITY / ZONE / DEMOGRAPHIC / SKIP

Pipeline stages: **Classify** (`codebook_parser.py`) → **Review** (`add_project.py` UI with uncertain-first toggle, pre-flight validation) → **Confirm + Write** (`generic_loader.py` writes identical schema for every project) → **Switch** (sidebar project selector + cache clear).

Additional ingestion features:
- `mapping_workbook.py` — Excel audit workbook: MASTER_MAPPING sheet + per-bucket sheets + DataValidation dropdowns + fact samples + dim tables
- Resume-from-checkpoint via `llm_mapping_raw.json`
- Model fallback list — tries next model on LLM classification failure
- Multi-project DB switching with `st.cache_data.clear()` on actual project change

**Akshayakalpa onboarding (sub-phase):** First external client onboarded. Organic dairy brand, 900 respondents, Bengaluru/Mysuru/Hyderabad. 32 pipeline bugs found and fixed during onboarding (documented in `.planning/AKSHAYAKALPA_PIPELINE_FIX_LOG.md`). Brand Health output verified against source PPT.

### Data Layer Rewrite (in planning — Phase 7)
Architecture decided, not yet built. SQLite stays as query engine. `lens/data_layer.py` will expose `load_raw()`, `load_mapping()`, and compute functions that replace direct SQL calls in analytics views. Excel workbook becomes the mapping audit/correction interface, not a runtime database.

---

## Repository structure

```
info-leap/
├── oxdata/              # Streamlit app — entry point: oxdata/app.py
│   ├── app.py
│   ├── db_loader.py     # resolves DB path — always call this, never hardcode
│   ├── researcher_agent.py
│   ├── views/           # 7 pages: brand_health, chat, dashboard, quote_explorer, add_project, manage_projects, ...
│   ├── skills/          # agent skills, R bridge, deterministic pipeline
│   ├── config/          # project_1.py (brands, SQL schemas, NPS benchmarks), registry.py
│   ├── components/
│   └── r_scripts/       # R statistical scripts called via subprocess
│
├── lens/                # analytics + ingestion engines
│   ├── analytics/       # brand_imagery_engine, can_map_engine, bip_engine, driver_analysis_engine, kano_engine, maxdiff_engine, synthetic_models
│   ├── ingestion/       # codebook_parser, generic_loader, schema_ingest, mapping_workbook
│   ├── data_layer.py    # pandas-based compute layer (replaces SQL views — Phase 7)
│   └── tests/
│
├── data/                # local only — not committed
│   └── project_1/oxdata.db
│
├── memory_system/       # codebase knowledge graph + observational memory
└── tests/
```

---

## Tech stack

| Layer | Technology |
|---|---|
| App framework | Streamlit |
| Database | SQLite (per-project, star schema) |
| AI agent | OpenRouter — multi-model (LLaMA 70B, GPT-4.1 mini, GPT-4o mini) |
| Analytics | Python: pandas, scipy, scikit-learn, plotly |
| Statistical models | R via subprocess bridge (logistic regression, ANOVA, factor analysis) |
| Narrative AI | Google Gemini API |
| Ingestion AI | OpenRouter (doc_prose codebook shape) |

---

## Projects active

| Project | Respondents | Domain | Status |
|---|---|---|---|
| project_1 (Electrical Appliances) | 6,631 | Kitchen + room appliances, 18 Indian cities | Full pipeline, all sections verified |
| Akshayakalpa (Organic Dairy) | 900 | Organic milk, 3 cities | Onboarded, Brand Health working |
