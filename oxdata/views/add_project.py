"""
Add Project — multi-project ingestion: classify (Phase 1) + confirm + write (Phase 2)
====================================================================================
Upload a new client's raw data file + data map + AP (analysis plan). A single LLM call
(lens/ingestion/schema_ingest.py::classify_all_questions) reads the full AP text, full
datamap text, and a row-sample of the raw data, and returns one schema-validated mapping
document (one row per real survey question — bucket, shape, source column, code->label).
Nothing is written to any database until a human reviews/corrects the bucket assignment in
the table below and clicks "Confirm Bucket Assignment && Ingest", which adapts that document
via lens/ingestion/generic_loader.py::assignment_from_schema and calls
load_confirmed_assignment() to write rows into a NEW project database at
oxdata/data/<project id>/oxdata.db.

2026-08-03: replaced the old 6-stage heuristic classifier (build_mapping_report /
classify_with_ai_fallback / reconcile_columns / group_cross_question_batteries / ...) with
this single-call schema-driven pipeline — see .planning/AKSHAYAKALPA_PIPELINE_FIX_LOG.md for
the 32-bug history of the old pipeline this replaces, and
.planning/MULTIPROJECT_INGESTION_LOG_2026-07-27.md for the overall ingestion design.

2026-08-05: Added UI improvements:
  - Mode toggle: AI Classify | Manual Template Ingest
  - Model dropdown in Advanced settings (OpenRouter model selector, overrides .env default)
  - Elapsed-time timer shown during Process and Ingest
  - Animated completion banner with time elapsed
  - Manual ingest mode: download template Excel -> fill with AI -> upload -> instant write
"""

import io
import json
import re
import sys
import os
import tempfile
import time
from pathlib import Path

import pandas as pd
import streamlit as st

current_dir = os.path.dirname(os.path.abspath(__file__))
oxdata_dir = os.path.dirname(current_dir)
repo_root = os.path.dirname(oxdata_dir)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from oxdata.utils.ui_styles import inject_pulse_styles, section_header
from oxdata.db_loader import get_db_path
from lens.ingestion.codebook_parser import (TARGET_BUCKETS, MappingGuess,
                                             export_column_map_excel, export_column_map_json)
from lens.ingestion.schema_ingest import (build_context_packet, flatten_datamap_to_text,
                                           classify_all_questions)
from lens.ingestion.generic_loader import load_confirmed_assignment, assignment_from_schema
from lens.ingestion.mapping_workbook import (build_mapping_workbook, read_master_mapping,
                                              apply_overrides_to_schema)


# ── OpenRouter model catalogue (used for model dropdown) ──────────────────────
# Curated list of models available on OpenRouter that work well for structured
# JSON classification. User can override via dropdown; .env defaults still apply
# when no override is set.
_OPENROUTER_MODEL_OPTIONS = [
    ("(use .env default)", None),
    ("GPT-4o mini  [fast, cheap]", "openai/gpt-4o-mini"),
    ("GPT-4.1 mini  [better reasoning]", "openai/gpt-4.1-mini"),
    ("GPT-4o  [high quality]", "openai/gpt-4o"),
    ("Claude Sonnet 4.5  [Anthropic, strong]", "anthropic/claude-sonnet-4-5"),
    ("Claude Haiku 4.5  [Anthropic, fast]", "anthropic/claude-haiku-4-5"),
    ("Llama 3.3 70B  [Meta, free tier]", "meta-llama/llama-3.3-70b-instruct"),
    ("Gemini 2.0 Flash  [Google, fast]", "google/gemini-2.0-flash-001"),
    ("Gemini 2.5 Flash  [Google, reasoning]", "google/gemini-2.5-flash-preview"),
    ("Deepseek R1  [reasoning, cheap]", "deepseek/deepseek-r1"),
    ("Qwen 2.5 72B  [multilingual]", "qwen/qwen-2.5-72b-instruct"),
]


def _build_fallback_model_list(override_model: str = None) -> list:
    """2026-08-03: classify_all_questions() now accepts a LIST of model-ids to try in order.
    2026-08-05: accepts an optional override_model (from UI dropdown) that gets inserted at
    the front of the fallback list, ensuring it is tried first even if it differs from .env.
    """
    candidates = [
        os.getenv("OPENROUTER_MODEL_PRO"),
        os.getenv("OPENROUTER_MODEL_ALT"),
        os.getenv("OPENROUTER_MODEL_LLAMA"),
        os.getenv("OPENROUTER_MODEL_MINI"),
        os.getenv("OPENROUTER_MODEL_NANO"),
    ]
    seen = set()
    ordered = []
    if override_model:
        seen.add(override_model)
        ordered.append(override_model)
    for m in candidates:
        if m and m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


def _questions_to_mapping_guesses(qlist):
    """Adapt schema_ingest's plain-dict question rows into the MappingGuess dataclass that
    codebook_parser.py's export_column_map_excel/export_column_map_json still expect — those
    two exporters are otherwise untouched (old pipeline's shape), so this is a display-only
    adapter, not a real classifier. matched_dimension/raw_data_sample have no equivalent in the
    new schema and are only ever displayed/joined as text by the exporters, so empty defaults
    are safe."""
    out = []
    for q in qlist:
        src_col = q.get("source_column")
        out.append(MappingGuess(
            question_code=q.get("question_code") or "",
            question_text=q.get("question_text") or "",
            shape=q.get("shape") or "",
            guessed_role=q.get("bucket") or "SKIP",
            confidence=float(q.get("confidence") or 0.0),
            matched_dimension=None,
            evidence=q.get("reasoning") or "",
            n_data_columns=1 if src_col else 0,
            data_columns=[src_col] if src_col else [],
            value_labels=q.get("code_to_label") or {},
            raw_data_sample="",
        ))
    return out


def _generate_manual_template() -> bytes:
    """Generate the Manual Ingest Template Excel workbook.

    Sheet 1 — MAPPING: one example row per bucket type (generic, not client-specific).
    Sheet 2 — INSTRUCTIONS: bucket reference + shape definitions + AI prompt to fill it.
    Sheet 3 — BUCKET_REFERENCE: all 22 buckets with descriptions.

    The user downloads this, fills it in (ideally with an AI assistant), then uploads it
    alongside the raw data file for instant ingestion — no AI classification call needed.
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Sheet 1: MAPPING — one generic example per shape/bucket combination
        example_rows = [
            # Demographics
            ("dem1",  "Please select the city where you currently reside.",
             "CITY",          "single_value",          "dem1",   "",  "",
             '{"1":"Mumbai","2":"Delhi","3":"Bangalore"}'),
            ("dem2",  "Please select your gender.",
             "GENDER",        "single_value",          "dem2",   "",  "",
             '{"1":"Male","2":"Female","3":"Other"}'),
            ("dem3",  "What is your age?",
             "AGE",           "single_value",          "dem3",   "",  "",
             ""),
            # Brand funnel
            ("q_tom", "First brand that comes to mind unprompted (open-end).",
             "TOM",           "single_value",          "q_tom",  "",  "",
             ""),
            ("q_aid", "Which of these brands have you heard of? (aided list shown)",
             "AIDED",         "multivalent_source",    "q_aid",  " ", "q_aid_1 q_aid_2 q_aid_3",
             '{"1":"BrandA","2":"BrandB","3":"BrandC"}'),
            ("q_cur", "Which brands are you currently using?",
             "CURRENT_USER",  "multivalent_source",    "q_cur",  " ", "q_cur_1 q_cur_2 q_cur_3",
             '{"1":"BrandA","2":"BrandB","3":"BrandC"}'),
            ("q_prf", "Which brand do you use most often? (most preferred)",
             "PREFERRED",     "single_value",          "q_prf",  "",  "",
             '{"1":"BrandA","2":"BrandB","3":"BrandC"}'),
            # NPS / CSAT
            ("q_nps", "How likely are you to recommend [Brand] to others? (0-10 scale)",
             "NPS",           "numeric_score",         "q_nps_1","",  "",
             ""),
            ("q_sat", "Overall, how satisfied are you with [Brand]? (1-5 scale)",
             "CSAT",          "numeric_score",         "q_sat_1","",  "",
             ""),
            # Brand imagery — multi-brand (multivalent per attribute)
            ("bq_img1","Select brands you feel are trustworthy.",
             "BRAND_IMAGERY", "multivalent_source",    "bq_img1"," ", "bq_img1_1 bq_img1_2 bq_img1_3",
             '{"1":"BrandA","2":"BrandB","3":"BrandC"}'),
            # Brand imagery — dummy-only (no combined column)
            ("bq_img2","Select brands you feel offer good value for money.",
             "BRAND_IMAGERY", "multi_select_dummies",  "bq_img2","",  "bq_img2_1 bq_img2_2 bq_img2_3",
             '{"1":"BrandA","2":"BrandB","3":"BrandC"}'),
            # Importance battery
            ("imp1",  "How important is freshness to you when choosing a brand? (1-7 scale)",
             "IMPORTANCE",    "numeric_score",         "imp1",   "",  "",
             ""),
            # Attitude / brand rating
            ("att1",  "How much do you love [Brand]? (1-5 scale)",
             "ATTITUDE",      "numeric_score",         "att1_1", "",  "",
             ""),
            # Price
            ("q_pri", "What price do you pay per unit for this product?",
             "PRICE_PAID",    "single_value",          "q_pri",  "",  "",
             '{"1":"< Rs 50","2":"Rs 50-100","3":"> Rs 100"}'),
        ]
        mapping_df = pd.DataFrame(
            example_rows,
            columns=["question_code","question_text","bucket","shape",
                     "source_column","delimiter","dummy_columns","code_to_label"]
        )
        mapping_df.to_excel(writer, sheet_name="MAPPING", index=False)

        # Sheet 2: INSTRUCTIONS
        ai_prompt = (
            "I am filling a survey ingestion mapping template for InfoLeap Pulse. "
            "I will paste my datamap below. For EACH question in the datamap, add ONE row to the "
            "MAPPING sheet using these rules:\n"
            "- question_code: the column stem (strip trailing _1/_2/... suffix)\n"
            "- bucket: one of 22 values — see BUCKET_REFERENCE sheet for full list\n"
            "- shape: multivalent_source (one cell holds '1 3 5'), single_value, numeric_score, "
            "  or multi_select_dummies (only 0/1 columns, no combined column)\n"
            "- source_column: exact column name to read from the data file\n"
            "- delimiter: only for multivalent_source — space, comma, or semicolon\n"
            "- dummy_columns: space-separated list of 0/1 column names (multi_select_dummies only)\n"
            "- code_to_label: JSON dict {\"raw_code\": \"brand_or_label_name\"} for brand questions\n\n"
            "Delete the example rows when done. Keep only real questions.\n\n"
            "[PASTE YOUR DATAMAP HERE]"
        )
        instr_rows = [
            ["MANUAL INGEST TEMPLATE — InfoLeap Pulse", ""],
            ["", ""],
            ["HOW TO USE THIS TEMPLATE", ""],
            ["1. Fill the MAPPING sheet — one row per survey question to ingest.", ""],
            ["2. Delete the 14 example rows (rows 2-15) and add your own.", ""],
            ["3. Use BUCKET_REFERENCE sheet to pick the right bucket for each question.", ""],
            ["4. Upload this filled template + raw data file in the Manual Ingest tab.", ""],
            ["", ""],
            ["FASTEST WAY — USE AN AI (Claude, Gemini, ChatGPT):", ""],
            ["Copy the prompt below, paste your datamap after [PASTE YOUR DATAMAP HERE], send to AI.", ""],
            ["AI PROMPT:", ""],
            [ai_prompt, ""],
            ["", ""],
            ["COLUMN GUIDE", ""],
            ["question_code", "The column stem in your raw data file (e.g. q17, not q17_1)"],
            ["question_text", "Human-readable label (only for display — not used in DB write)"],
            ["bucket", "One of the 22 buckets in BUCKET_REFERENCE sheet (e.g. AIDED, NPS)"],
            ["shape", "multivalent_source | single_value | numeric_score | multi_select_dummies"],
            ["source_column", "The actual column name in your raw data file to read from"],
            ["delimiter", "For multivalent_source: the delimiter between codes (space, ;, ,)"],
            ["dummy_columns", "For multi_select_dummies: space-separated list of dummy col names"],
            ["code_to_label", 'JSON dict mapping raw code -> brand/attribute name e.g. {"1":"Amul"}'],
            ["", ""],
            ["SHAPE DEFINITIONS", ""],
            ["multivalent_source", "One column holds all selections as delimited codes: '1 3 5'"],
            ["single_value", "One column, one value per respondent"],
            ["numeric_score", "Numeric rating (NPS 0-10, CSAT 1-5, importance 1-7)"],
            ["multi_select_dummies", "No combined column — only one-hot dummy columns (0/1 per option)"],
        ]
        pd.DataFrame(instr_rows, columns=["Field", "Description"]).to_excel(
            writer, sheet_name="INSTRUCTIONS", index=False)

        # Sheet 3: BUCKET_REFERENCE
        bucket_rows = [(b, d) for b, d in TARGET_BUCKETS.items()]
        pd.DataFrame(bucket_rows, columns=["Bucket", "Description"]).to_excel(
            writer, sheet_name="BUCKET_REFERENCE", index=False)

    return buf.getvalue()


def _render_manage_project():
    """Project Edit / Update / Delete section."""
    import glob
    import shutil
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    oxdata_data_dir = _Path(oxdata_dir) / "data"
    db_paths = sorted(glob.glob(str(oxdata_data_dir / "*" / "oxdata.db")))
    project_ids = [_Path(p).parent.name for p in db_paths]

    if not project_ids:
        st.info("No projects found. Add one first.")
        return

    selected = st.selectbox("Select project", project_ids, key="manage_project_sel")
    if not selected:
        return

    db_path = str(oxdata_data_dir / selected / "oxdata.db")
    db_file = _Path(db_path)

    # ── Project info ──────────────────────────────────────────────────────────
    st.markdown(f"**DB path:** `{db_path}`")
    if db_file.exists():
        size_mb = db_file.stat().st_size / (1024 * 1024)
        import datetime
        mtime = datetime.datetime.fromtimestamp(db_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        st.caption(f"Size: {size_mb:.1f} MB | Last modified: {mtime}")
        try:
            _conn = _sqlite3.connect(db_path)
            _tables = [r[0] for r in _conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            _counts = {}
            for _t in ["fact_respondents", "dim_brand", "fact_brand_awareness", "fact_brand_imagery", "fact_brand_nps", "fact_satisfaction"]:
                if _t in _tables:
                    _n = _conn.execute(f"SELECT COUNT(*) FROM {_t}").fetchone()[0]
                    _counts[_t] = _n
            _conn.close()
            cols = st.columns(len(_counts))
            for i, (tbl, n) in enumerate(_counts.items()):
                cols[i].metric(tbl.replace("fact_", "").replace("dim_", ""), f"{n:,}")
        except Exception as e:
            st.warning(f"Could not read DB stats: {e}")
    else:
        st.error("DB file not found.")
        return

    st.divider()

    # ── Edit Analysis Config ──────────────────────────────────────────────────
    st.subheader("⚙️ Analysis Configuration")
    try:
        _conn2 = _sqlite3.connect(db_path)
        _tables2 = [r[0] for r in _conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "project_config" in _tables2:
            _cfg_rows = _conn2.execute("SELECT key, value FROM project_config").fetchall()
            _cfg = {k: v for k, v in _cfg_rows}
        else:
            _cfg = {}
        _conn2.close()
    except Exception:
        _cfg = {}

    with st.form("edit_analysis_config"):
        _all_stages = ["TOM", "SPONT", "AIDED", "EVER_USED", "CONSIDERATION", "CURRENT_USER", "PREFERRED", "LAST_PURCHASED"]
        _cur_stages_raw = _cfg.get("awareness_gate_stages", "TOM,SPONT,AIDED,EVER_USED,CONSIDERATION")
        _cur_stages = [s.strip() for s in _cur_stages_raw.split(",") if s.strip()]

        _proj_name = st.text_input("Project display name", value=_cfg.get("project_name", selected))
        _dv_source = st.selectbox("DV source", ["nps", "csat", "ever_tried"],
                                   index=["nps", "csat", "ever_tried"].index(_cfg.get("dv_source", "nps"))
                                   if _cfg.get("dv_source", "nps") in ["nps", "csat", "ever_tried"] else 0)
        _topbox = st.number_input("Top-box threshold (0 = continuous OLS)", min_value=0, max_value=10,
                                   value=int(_cfg.get("dv_topbox_threshold", 9) or 0))
        _iv_source = st.selectbox("IV source", ["imagery", "importance", "both"],
                                   index=["imagery", "importance", "both"].index(_cfg.get("iv_source", "imagery"))
                                   if _cfg.get("iv_source", "imagery") in ["imagery", "importance", "both"] else 0)
        _gate_stages = st.multiselect("Awareness gate stages", _all_stages, default=_cur_stages)
        _excl_brands = st.text_input("Exclude brand IDs (comma-separated ints)", value=_cfg.get("exclude_brand_ids", ""))

        if st.form_submit_button("💾 Save Analysis Config"):
            try:
                _conn3 = _sqlite3.connect(db_path)
                # Create table if this is an older project without it
                _conn3.execute("CREATE TABLE IF NOT EXISTS project_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                _new_cfg = {
                    "project_name": _proj_name,
                    "dv_source": _dv_source,
                    "dv_topbox_threshold": str(_topbox),
                    "iv_source": _iv_source,
                    "awareness_gate_stages": ",".join(_gate_stages),
                    "exclude_brand_ids": _excl_brands.strip(),
                }
                for _k, _v in _new_cfg.items():
                    _conn3.execute("INSERT OR REPLACE INTO project_config (key, value) VALUES (?,?)", (_k, _v))
                _conn3.commit()
                _conn3.close()
                st.success("Analysis config saved.")
            except Exception as e:
                st.error(f"Save failed: {e}")

    st.divider()

    # ── Edit Brand Names ──────────────────────────────────────────────────────
    st.subheader("🏷️ Brand Names")
    try:
        _conn4 = _sqlite3.connect(db_path)
        _brands_df = pd.read_sql("SELECT brand_id, brand_name FROM dim_brand ORDER BY brand_id", _conn4)
        _conn4.close()
    except Exception as e:
        st.warning(f"Could not load brands: {e}")
        _brands_df = pd.DataFrame()

    if not _brands_df.empty:
        _edited_brands = st.data_editor(
            _brands_df, key=f"brand_editor_{selected}", num_rows="fixed",
            column_config={"brand_id": st.column_config.NumberColumn("ID", disabled=True)},
            use_container_width=True,
        )
        if st.button("💾 Save Brand Names", key="save_brands"):
            try:
                _conn5 = _sqlite3.connect(db_path)
                for _, row in _edited_brands.iterrows():
                    _conn5.execute("UPDATE dim_brand SET brand_name=? WHERE brand_id=?",
                                   (row["brand_name"], int(row["brand_id"])))
                _conn5.commit()
                _conn5.close()
                st.success("Brand names updated.")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Update failed: {e}")

    st.divider()

    # ── Re-run Indexes ────────────────────────────────────────────────────────
    if st.button("🔧 Re-run Performance Indexes", key="reindex"):
        try:
            _conn6 = _sqlite3.connect(db_path)
            _conn6.execute("CREATE INDEX IF NOT EXISTS idx_fba_resp_brand_stage ON fact_brand_awareness(respondent_id, brand_id, stage)")
            _conn6.execute("CREATE INDEX IF NOT EXISTS idx_fbi_resp_brand ON fact_brand_imagery(respondent_id, brand_id)")
            _conn6.commit()
            _conn6.close()
            st.success("Indexes created/verified.")
        except Exception as e:
            st.error(f"Index creation failed: {e}")

    st.divider()

    # ── Delete Project ────────────────────────────────────────────────────────
    st.subheader("🗑️ Delete Project")
    st.error(
        f"**This cannot be undone.** Deleting project `{selected}` will permanently remove "
        f"the entire `oxdata/data/{selected}/` directory and all its data."
    )
    _confirm_input = st.text_input(
        f"Type `{selected}` to confirm deletion", key="delete_confirm_input"
    )
    if st.button("🗑️ Delete Project Permanently", key="delete_project_btn", type="primary"):
        if _confirm_input.strip() == selected:
            try:
                _del_path = str(oxdata_data_dir / selected)
                shutil.rmtree(_del_path)
                st.success(f"Project `{selected}` deleted.")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Delete failed: {e}")
        else:
            st.warning(f"Project ID does not match. Type exactly: `{selected}`")


inject_pulse_styles()
st.title("🧬 Add Project — Ingestion Pipeline")

# ── Mode toggle ───────────────────────────────────────────────────────────────
mode = st.radio(
    "Ingestion mode",
    ["🤖 AI Classify (recommended)", "📋 Manual Template Ingest"],
    horizontal=True,
    help="AI Classify: upload 3 files, AI maps every column automatically. "
         "Manual Template: download template, fill it (with AI help), upload for instant write.",
)
_manual_mode = mode.startswith("📋")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# MANUAL TEMPLATE INGEST MODE
# ══════════════════════════════════════════════════════════════════════════════
if _manual_mode:
    st.markdown("### 📋 Manual Template Ingest")
    st.info(
        "**How it works:** Download the template below → open it → fill in the MAPPING sheet "
        "(use Claude or another AI with your datamap to do this fast) → upload the filled "
        "template + your raw data file → instant ingest, no AI classification call needed."
    )

    # Download template
    tmpl_bytes = _generate_manual_template()
    st.download_button(
        "📥 Download Ingest Template (Excel)",
        tmpl_bytes,
        "infoleap_ingest_template.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=False,
    )

    with st.expander("📖 How to fill the template with Claude / another AI"):
        st.markdown("""
**Prompt to give Claude:**

> I have a market research survey. I'll give you the data map (Variable/Label list) and the
> template Excel file. Fill in the MAPPING sheet — one row per survey question.
> Rules:
> - `bucket`: pick from the BUCKET_REFERENCE sheet (e.g. AIDED, NPS, BRAND_IMAGERY)
> - `shape`: multivalent_source if one column holds "1 3 5" style codes; multi_select_dummies
>   if only q34_1_1, q34_1_2 dummy columns exist; numeric_score for ratings; single_value otherwise
> - `source_column`: the column name exactly as it appears in the data file
> - `code_to_label`: JSON dict with code→brand/attribute name for brand funnel + imagery questions
> - Skip screener questions, household ownership questions, and media channel questions
>
> Here is my data map: [paste your datamap text or attach file]

Then paste the filled MAPPING sheet back here, or upload the saved Excel.
        """)

    st.markdown("---")
    st.markdown("**Upload filled template + raw data:**")
    m_col1, m_col2 = st.columns(2)
    with m_col1:
        st.markdown("**1. Filled template**")
        manual_template_file = st.file_uploader(
            "Filled template", type=["xlsx"], label_visibility="collapsed",
            key="manual_template_upload",
            help="The infoleap_ingest_template.xlsx you filled in.",
        )
    with m_col2:
        st.markdown("**2. Raw data file**")
        manual_data_file = st.file_uploader(
            "Raw data", type=["xlsx", "xls", "csv"], label_visibility="collapsed",
            key="manual_data_upload",
            help="The respondent-level survey export — one row per respondent.",
        )

    manual_project_id = st.text_input(
        "Project ID",
        placeholder="e.g. akshayakalpa",
        key="manual_project_id",
        help="Destination: oxdata/data/<this id>/oxdata.db",
    )

    with st.expander("⚙️ Brand names (required for brand-identity questions)"):
        manual_brand_text = st.text_input(
            "Known brand names (comma-separated)",
            placeholder="e.g. Akshayakalpa Organic, Country Delight, Amul",
            key="manual_brand_text",
        )

    manual_run = st.button(
        "⚡ Ingest Now",
        type="primary",
        disabled=(manual_template_file is None or manual_data_file is None
                  or not manual_project_id.strip()),
    )

    if manual_run:
        _t0 = time.time()
        _timer_ph = st.empty()

        def _tick(label="Ingesting"):
            elapsed = time.time() - _t0
            _timer_ph.caption(f"⏱ {label}… {elapsed:.1f}s elapsed")

        with st.spinner("Reading template and writing to database…"):
            try:
                _tick("Reading template")
                manual_template_file.seek(0)
                mapping_df = pd.read_excel(manual_template_file, sheet_name="MAPPING")

                # Build questions list from template rows
                manual_questions = []
                for _, row in mapping_df.iterrows():
                    qcode = str(row.get("question_code") or "").strip()
                    bucket = str(row.get("bucket") or "").strip().upper()
                    if not qcode or not bucket or bucket == "SKIP" or qcode.startswith("("):
                        continue

                    shape = str(row.get("shape") or "single_value").strip()
                    source_col = str(row.get("source_column") or qcode).strip()
                    delimiter_val = str(row.get("delimiter") or "").strip() or None

                    dummy_raw = str(row.get("dummy_columns") or "").strip()
                    dummy_cols = [c.strip() for c in dummy_raw.split() if c.strip()] if dummy_raw else []

                    code_to_label_raw = str(row.get("code_to_label") or "").strip()
                    try:
                        code_to_label = json.loads(code_to_label_raw) if code_to_label_raw else {}
                    except Exception:
                        code_to_label = {}

                    manual_questions.append({
                        "question_code": qcode,
                        "question_text": str(row.get("question_text") or qcode),
                        "bucket": bucket,
                        "shape": shape,
                        "source_column": source_col,
                        "delimiter": delimiter_val,
                        "dummy_columns": dummy_cols,
                        "code_to_label": code_to_label,
                        "confidence": 1.0,
                        "reasoning": "manual template ingest",
                    })

                if not manual_questions:
                    st.error("No valid rows found in MAPPING sheet — check the template.")
                elif not manual_project_id.strip():
                    st.error("Project ID required.")
                elif manual_project_id.strip() == "project_1":
                    st.error("Cannot write to 'project_1' — pick a different project ID.")
                else:
                    _tick("Parsing data file")
                    manual_data_file.seek(0)
                    if manual_data_file.name.lower().endswith((".xlsx", ".xls")):
                        ingest_df = pd.read_excel(manual_data_file)
                    else:
                        ingest_df = pd.read_csv(manual_data_file)

                    manual_schema_doc = {
                        "project_id": manual_project_id.strip(),
                        "questions": manual_questions,
                    }
                    pieces = assignment_from_schema(manual_schema_doc)
                    assignment = pieces["assignment"]

                    _tick("Writing to database")
                    ingest_brand_names = (
                        [b.strip() for b in manual_brand_text.split(",") if b.strip()]
                        if manual_brand_text else None
                    )
                    out_dir = Path(oxdata_dir) / "data" / manual_project_id.strip()
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_db_path = str(out_dir / "oxdata.db")

                    result = load_confirmed_assignment(
                        assignment, pieces["value_labels_by_code"], pieces["shape_by_code"],
                        ingest_df, out_db_path,
                        brand_names=ingest_brand_names,
                        delimiter_by_code=pieces["delimiter_by_code"],
                    )

                    elapsed = time.time() - _t0
                    _timer_ph.empty()

                    if result is not None:
                        st.balloons()
                        st.success(
                            f"✅ Done in **{elapsed:.1f}s** — wrote {result['respondents_seen']:,} "
                            f"respondent(s) to `oxdata/data/{manual_project_id.strip()}/oxdata.db`."
                        )
                        st.markdown("**Rows written per bucket:**")
                        for bucket_name, n in result["buckets_written"].items():
                            st.markdown(f"- `{bucket_name}`: {n:,} rows")
                        if result.get("warnings"):
                            with st.expander(f"⚠️ {len(result['warnings'])} warning(s)"):
                                for w in result["warnings"]:
                                    st.markdown(f"- {w}")
                        try:
                            from lens.ingestion.master_excel import write_master_excel as _write_master
                            _master_out = str(out_dir / "master_mapping.xlsx")
                            _write_master(
                                _master_out, schema_doc,
                                str(out_dir / "raw_data.xlsx"), manual_project_id.strip(),
                                raw_df=ingest_df,
                            )
                            st.info(f"📊 `master_mapping.xlsx` written with RAW_DATA sheet "
                                    f"({len(ingest_df):,} rows). Edit & re-ingest to apply changes.")
                            with open(_master_out, "rb") as _fh:
                                st.download_button(
                                    "⬇️ Download master_mapping.xlsx",
                                    _fh.read(),
                                    file_name=f"{manual_project_id.strip()}_master_mapping.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    key="dl_master_mapping_manual",
                                )
                        except Exception as _xl_err:
                            st.warning(f"master_mapping.xlsx not written: {_xl_err}")

            except Exception as e:
                elapsed = time.time() - _t0
                _timer_ph.empty()
                st.error(f"Manual ingest failed after {elapsed:.1f}s: {e}")
                st.exception(e)

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# AI CLASSIFY MODE (original flow)
# ══════════════════════════════════════════════════════════════════════════════
st.caption(
    "Give it three files. It reads the Data map to know every question's answer options, reads "
    "the AP (analysis plan) to know which funnel stage/bucket each question belongs to and how "
    "it should be calculated, then maps every raw data column onto the fixed database schema in "
    "ONE pass. Nothing is written until you review the mapping below and confirm."
)

with st.expander("📋 What files do I need? (collect from your research agency)", expanded=False):
    st.markdown("""
| File | What it is | Who provides it | Typical filename |
|------|-----------|-----------------|-----------------|
| **Raw data** | One row per respondent, all survey answers as codes | Research agency | `Data_900_Jan2026.xlsx` |
| **Data map** | Column name → description + answer code labels | Research agency | `DATAMAP.xls` or `Codebook.xlsx` |
| **Analysis plan (AP)** | Question number → title + type (TOM/SPONT/Imagery…) | Research agency / PM | `AP_v3.xlsx` |
| **Questionnaire** *(optional)* | The actual question text, for ambiguous columns | Research agency | `.doc` / `.pdf` |

**Minimum to ingest:** Raw data + Data map + AP.
**If AP is unavailable:** Use the Data map as the AP input — classification will work but with lower confidence on funnel stage columns.
**If you have a `.doc` questionnaire:** Upload it as the Data map — the pipeline extracts question text automatically.
    """)

# ── Resume from checkpoint ───────────────────────────────────────────────────
with st.expander("⏩ Resume from saved checkpoint (skip re-classify)", expanded=False):
    st.markdown(
        "If you already ran classification for this project, restore the session from disk — "
        "no re-upload or AI call needed. Requires `llm_mapping_raw.json` and `raw_data.xlsx` "
        "saved in `oxdata/data/<project_id>/`."
    )
    _resume_pid = st.text_input(
        "Project ID to resume",
        placeholder="e.g. akshayakalpa",
        key="resume_checkpoint_pid",
    )
    if _resume_pid.strip() and st.button("Load checkpoint", key="resume_checkpoint_btn"):
        _r_pid = _resume_pid.strip()
        _r_json = Path(oxdata_dir) / "data" / _r_pid / "llm_mapping_raw.json"
        _r_xlsx = Path(oxdata_dir) / "data" / _r_pid / "raw_data.xlsx"
        _r_errors = []
        if not _r_json.exists():
            _r_errors.append(f"`llm_mapping_raw.json` not found in oxdata/data/{_r_pid}/")
        if not _r_xlsx.exists():
            _r_errors.append(f"`raw_data.xlsx` not found in oxdata/data/{_r_pid}/ — re-upload raw file above first")
        if _r_errors:
            for _e in _r_errors:
                st.error(_e)
        else:
            try:
                _r_raw_loaded = json.loads(_r_json.read_text(encoding="utf-8"))
                _r_schema_doc = _r_raw_loaded.get("schema_doc", _r_raw_loaded)
                _r_raw_df = pd.read_excel(_r_xlsx)
                st.session_state["ap_schema_doc"] = _r_schema_doc
                st.session_state["ap_raw_df"] = _r_raw_df
                st.session_state["ap_project_id"] = _r_pid
                n_q = len(_r_schema_doc.get("questions", []))
                st.success(
                    f"Checkpoint loaded — {n_q} questions, {len(_r_raw_df)} respondents. "
                    "Scroll down to review mapping or download the workbook."
                )
            except Exception as _r_err:
                st.error(f"Failed to load checkpoint: {_r_err}")

# ── Step 1: three inputs ─────────────────────────────────────────────────────
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("**1. Raw data**")
    data_file = st.file_uploader(
        "Raw data file", type=["xlsx", "xls", "csv"], label_visibility="collapsed",
        help="The respondent-level survey export — one row per respondent.",
    )
with c2:
    st.markdown("**2. Data map**")
    codebook_file = st.file_uploader(
        "Data map", type=["xlsx", "xls", "doc"], label_visibility="collapsed",
        help="Tells the pipeline what every column's answer options/filters are and how raw "
             "codes map to real labels (brand names, ratings, etc).",
    )
with c3:
    st.markdown("**3. AP (analysis plan)**")
    ap_tabplan_file = st.file_uploader(
        "AP", type=["xlsx", "xls"], label_visibility="collapsed",
        help="Tells the pipeline how each bucket (TOM, SPONT, AIDED, ...) is actually "
             "calculated and which columns feed it — the AP's own table titles are the most "
             "reliable signal for which funnel stage a question is.",
    )

# Auto-detect data map sheet names
data_sheet_name = None
if data_file is not None and data_file.name.lower().endswith((".xlsx", ".xls")):
    try:
        data_file.seek(0)
        xls = pd.ExcelFile(data_file)
        data_sheet_name = xls.sheet_names[-1]
    except Exception as e:
        st.error(f"Couldn't read raw data file's sheet names: {e}")

variables_sheet, values_sheet = "Variables", "Values"
codebook_is_doc = False
if codebook_file is not None:
    if codebook_file.name.lower().endswith(".doc"):
        codebook_is_doc = True
    else:
        try:
            codebook_file.seek(0)
            codebook_sheets = pd.ExcelFile(codebook_file).sheet_names
            lower = {s.lower(): s for s in codebook_sheets}

            def _find(*keywords):
                return next((orig for low, orig in lower.items()
                             if any(kw in low for kw in keywords)), None)

            variables_sheet = _find("variable") or codebook_sheets[0]
            values_sheet = _find("value") or (codebook_sheets[1] if len(codebook_sheets) > 1
                                                else codebook_sheets[0])
        except Exception as e:
            st.error(f"Couldn't read data map's sheet names: {e}")

with st.expander("⚙️ Advanced settings"):
    # ── Model dropdown ────────────────────────────────────────────────────────
    st.markdown("**AI model for classification**")
    model_labels = [label for label, _ in _OPENROUTER_MODEL_OPTIONS]
    selected_model_label = st.selectbox(
        "OpenRouter model",
        model_labels,
        index=0,
        label_visibility="collapsed",
        help="Override the model used for classification. Default uses OPENROUTER_MODEL_PRO "
             "from .env. Stronger models (GPT-4o, Claude Sonnet) give better bucket accuracy "
             "at higher cost; fast/cheap models (GPT-4o mini, Haiku) are good enough for "
             "well-structured datamaps.",
    )
    _model_override = dict(_OPENROUTER_MODEL_OPTIONS)[selected_model_label]
    if _model_override:
        st.caption(f"Will try `{_model_override}` first, then fall back to .env models.")
    else:
        st.caption("Using model order from `.env` (OPENROUTER_MODEL_PRO → ALT → LLAMA → MINI → NANO).")

    st.markdown("---")

    # ── Regression matrix upload ──────────────────────────────────────────────
    st.markdown("**Regression matrix (optional — for XLSTAT-matched driver analysis)**")
    st.caption(
        "Upload a pre-built wide-format CSV: columns `respondent_id`, `brand_id`, `dv_ever_used`, "
        "then one column per imagery attribute (binary 0/1). "
        "When present, pooled driver regression uses this file directly — "
        "guarantees n and variable values match XLSTAT exactly."
    )
    _reg_proj_id = st.text_input(
        "Project ID to save matrix for",
        value="",
        placeholder="e.g. akshayakalpa",
        key="reg_matrix_project_id",
        help="Must match an existing project folder under oxdata/data/",
    )
    _reg_matrix_file = st.file_uploader(
        "Regression matrix CSV",
        type=["csv"],
        key="reg_matrix_upload",
        help="Wide-format: respondent_id, brand_id, dv_ever_used, [attr cols...]",
    )
    if _reg_matrix_file and _reg_proj_id.strip():
        _reg_out_dir = Path(oxdata_dir) / "data" / _reg_proj_id.strip()
        if not _reg_out_dir.exists():
            st.error(f"Project folder `oxdata/data/{_reg_proj_id.strip()}` does not exist. Ingest project first.")
        elif st.button("Save regression matrix", key="save_reg_matrix"):
            _reg_out_path = _reg_out_dir / "regression_matrix.csv"
            _reg_out_path.write_bytes(_reg_matrix_file.getvalue())
            st.success(f"Saved to `{_reg_out_path}`. Pooled driver regression will use this file automatically.")

    st.markdown("---")

    # ── Brand names ──────────────────────────────────────────────────────────
    st.markdown("**Brand list**")
    if "ap_brand_text" not in st.session_state:
        st.session_state["ap_brand_text"] = ""
    if not st.session_state["ap_brand_text"] and st.session_state.get("ap_schema_doc"):
        _brand_buckets = {"AIDED", "TOM", "SPONT", "EVER_USED", "CURRENT_USER",
                          "CONSIDERATION", "PREFERRED", "BRAND_IMAGERY"}
        _SKIP_LABELS = {
            "noneofthese", "none of these", "none of the above", "nota",
            "other", "others", "any other", "other brands",
            "don't know", "dont know", "dk", "not applicable", "na", "n/a",
            "refused", "can't say", "cant say", "not sure",
        }
        _seen: dict[str, str] = {}
        # Pull from code_to_label in schema_doc questions
        for _q in st.session_state["ap_schema_doc"].get("questions", []):
            if _q.get("bucket") in _brand_buckets:
                for _label in (_q.get("code_to_label") or {}).values():
                    if _label and _label.strip():
                        _key = _label.strip().lower()
                        if _key not in _seen and _key not in _SKIP_LABELS:
                            _seen[_key] = _label.strip()
        # Also pull from BRANDS sheet in master_mapping.xlsx if available
        _proj_id = (st.session_state.get("ap_project_id")
                    or st.session_state.get("_prefill_project_id", "")).strip()
        if _proj_id:
            _master_xl_path = Path(oxdata_dir) / "data" / _proj_id / "master_mapping.xlsx"
            if _master_xl_path.exists():
                try:
                    import pandas as _pd
                    _brands_df = _pd.read_excel(str(_master_xl_path), sheet_name="BRANDS")
                    for _, _brow in _brands_df.iterrows():
                        _bname = str(_brow.get("brand_name", "")).strip()
                        _is_junk = bool(_brow.get("is_junk", False))
                        _in_ap = _brow.get("in_ap_universe")
                        # Only include brands in the AP universe to avoid demographic labels
                        if _in_ap is not None and not bool(_in_ap):
                            continue
                        if _bname and not _is_junk:
                            _bkey = _bname.lower()
                            if _bkey not in _seen and _bkey not in _SKIP_LABELS:
                                _seen[_bkey] = _bname
                except Exception:
                    pass
        if _seen:
            st.session_state["ap_brand_text"] = ", ".join(_seen.values())

    brand_text = st.text_input(
        "Known brand names for this client (comma-separated)",
        placeholder="e.g. Akshayakalpa Organic, Country Delight, Milky Mist, Sid's Farm, Amul",
        help="Auto-filled from AIDED/TOM/SPONT answer labels after classification. "
             "Edit as needed before ingesting.",
        key="ap_brand_text",
    )

project_id_for_classify = st.text_input(
    "Project ID (used during classification and as the destination folder — "
    "folder-safe: letters, numbers, underscore)",
    placeholder="e.g. akshayakalpa",
    help="Destination: oxdata/data/<this id>/oxdata.db. Set this before Process — the "
         "classification call tags the mapping with it, and it pre-fills the ingest step below.",
)

run = st.button("⚙️ Process", type="primary",
                 disabled=(data_file is None or codebook_file is None
                           or ap_tabplan_file is None or not project_id_for_classify.strip()))


_AP_TRIM_SHEET_MIN_COLS = 6
_AP_TRIM_SHEET_MAX_COLS = 20
_AP_TRIM_MIN_ROW_CELLS = 4


def _workbook_to_text(file_obj) -> str:
    """Dump every sheet in an Excel workbook to plain text for the AP file."""
    file_obj.seek(0)
    xls = pd.ExcelFile(file_obj)
    lines = []
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name)
        trim_this_sheet = _AP_TRIM_SHEET_MIN_COLS <= len(df.columns) <= _AP_TRIM_SHEET_MAX_COLS
        lines.append(f"=== SHEET: {sheet_name} ===")
        lines.append(" | ".join(str(c) for c in df.columns))
        for _, row in df.iterrows():
            cells = [str(v) for v in row.tolist() if pd.notna(v)]
            if trim_this_sheet and len(cells) > _AP_TRIM_MIN_ROW_CELLS:
                cells = cells[:20]  # keep Table No | Q.No | Title | Side Breaks | SA/MA/OE + brand code columns
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


if run:
    _t0_classify = time.time()

    def _render_classify_progress(stage: str, substage: str = "", elapsed: float = 0.0, model_name: str = ""):
        """Render a styled progress card during classification."""
        stages = [
            ("📂", "Parse files", "Reading data & codebook"),
            ("🧠", "Build context", "Packaging columns + value labels"),
            ("🤖", "AI classify", "LLM maps every question to schema bucket"),
            ("✅", "Done", ""),
        ]
        stage_keys = ["parse", "context", "ai", "done"]
        current_idx = stage_keys.index(stage) if stage in stage_keys else 0

        bars = []
        for i, (icon, label, _) in enumerate(stages):
            if i < current_idx:
                dot = "🟢"
                style = "color:#4CAF50;font-weight:600"
            elif i == current_idx:
                dot = "🔵"
                style = "color:#2196F3;font-weight:700"
            else:
                dot = "⚪"
                style = "color:#888;font-weight:400"
            bars.append(f'<span style="{style}">{dot} {icon} {label}</span>')

        model_line = f'<br><span style="color:#aaa;font-size:0.8em">Model: <code>{model_name}</code></span>' if model_name else ""
        substage_line = f'<br><span style="color:#ccc;font-size:0.85em">↳ {substage}</span>' if substage else ""
        elapsed_line = f'<br><span style="color:#999;font-size:0.8em">⏱ {elapsed:.1f}s elapsed</span>' if elapsed > 0 else ""

        html = f"""
        <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);border:1px solid #2d3561;
                    border-radius:12px;padding:20px 24px;margin:12px 0;font-family:monospace">
          <div style="font-size:1.05em;color:#e0e0e0;margin-bottom:12px;letter-spacing:0.5px">
            🔬 <b>AI Classification Pipeline</b>
          </div>
          <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:10px">
            {"&nbsp;&nbsp;→&nbsp;&nbsp;".join(bars)}
          </div>
          {substage_line}{model_line}{elapsed_line}
        </div>
        """
        return html

    _progress_slot = st.empty()
    _progress_slot.markdown(
        _render_classify_progress("parse", "Loading data file…", 0.0),
        unsafe_allow_html=True)

    schema_doc = None
    _ai_model = None
    column_samples = {}
    try:
        data_file.seek(0)
        if data_file.name.lower().endswith((".xlsx", ".xls")):
            data_df_full = pd.read_excel(data_file, sheet_name=data_sheet_name)
        else:
            data_df_full = pd.read_csv(data_file)
        data_cols = list(data_df_full.columns)
        st.session_state["ap_raw_df"] = data_df_full  # stored for mapping workbook download
        _progress_slot.markdown(
            _render_classify_progress("parse", f"Data loaded — {len(data_cols)} columns", time.time() - _t0_classify),
            unsafe_allow_html=True)

        codebook_file.seek(0)
        if codebook_is_doc:
            _tmp_dir = tempfile.gettempdir()
            codebook_path = os.path.join(_tmp_dir, f"_addproject_codebook_{id(codebook_file)}.doc")
            with open(codebook_path, "wb") as _f:
                _f.write(codebook_file.read())
            from lens.ingestion.prose_questionnaire_parser import extract_doc_text
            datamap_text = extract_doc_text(codebook_path)
        else:
            _tmp_dir = tempfile.gettempdir()
            codebook_path = os.path.join(_tmp_dir, f"_addproject_datamap_{id(codebook_file)}.xlsx")
            with open(codebook_path, "wb") as _f:
                _f.write(codebook_file.read())
            datamap_text = flatten_datamap_to_text(
                codebook_path, variables_sheet=variables_sheet, values_sheet=values_sheet)

        ap_tabplan_file.seek(0)
        ap_text = _workbook_to_text(ap_tabplan_file)

        # ── Fast path: AP file IS a master_mapping.xlsx (META + MAPPING sheets) ──
        # No LLM needed — the mapping is already fully specified by the human.
        ap_tabplan_file.seek(0)
        _tmp_ap_path = os.path.join(tempfile.gettempdir(), f"_addproject_ap_{id(ap_tabplan_file)}.xlsx")
        with open(_tmp_ap_path, "wb") as _f:
            _f.write(ap_tabplan_file.read())
        try:
            _ap_xl_sheets = pd.ExcelFile(_tmp_ap_path).sheet_names
        except Exception:
            _ap_xl_sheets = []
        _is_master_mapping = {"META", "MAPPING"}.issubset(set(_ap_xl_sheets))

        if _is_master_mapping:
            _progress_slot.markdown(
                _render_classify_progress("ai", "Master mapping detected — skipping AI (instant)", time.time() - _t0_classify, "master_mapping.xlsx"),
                unsafe_allow_html=True)
            from lens.ingestion.master_excel import read_master_excel
            schema_doc = read_master_excel(_tmp_ap_path)
            schema_doc["project_id"] = project_id_for_classify.strip() or schema_doc.get("project_id", "unknown")
            schema_doc["_model_used"] = "master_mapping.xlsx (no AI)"
            _ai_model = "master_mapping.xlsx (no AI)"
        else:
            _progress_slot.markdown(
                _render_classify_progress("context", "Building context packet…", time.time() - _t0_classify),
                unsafe_allow_html=True)
            packet = build_context_packet(ap_text, datamap_text, data_df_full)

            try:
                from dotenv import load_dotenv
                load_dotenv(os.path.join(oxdata_dir, ".env"), override=True)
            except Exception:
                pass
            _ai_key = os.getenv("OPENROUTER_API_KEY")
            _model_fallback_list = _build_fallback_model_list(_model_override)
            _ai_model = _model_fallback_list[0] if _model_fallback_list else None
            if not _ai_key:
                raise RuntimeError(
                    "OPENROUTER_API_KEY missing from oxdata/.env — the schema-driven pipeline is "
                    "a single AI call with no rules-only fallback; set the key before Process.")
            if not _model_fallback_list:
                raise RuntimeError(
                    "No OPENROUTER_MODEL_* variable set in oxdata/.env (checked PRO/ALT/LLAMA/"
                    "MINI/NANO) — set at least one before Process.")

            _progress_slot.markdown(
                _render_classify_progress("ai", f"Sending to {_ai_model}… (may take 30–90s)", time.time() - _t0_classify, _ai_model),
                unsafe_allow_html=True)
            schema_doc = classify_all_questions(
                packet, project_id=project_id_for_classify.strip(),
                api_key=_ai_key, model=_model_fallback_list, timeout=180)
            _ai_model = schema_doc.get("_model_used", _ai_model)

        column_samples = {}
        for col in data_cols:
            vals = data_df_full[col].dropna().astype(str).str.strip()
            vals = [v for v in vals if v][:3]
            if vals:
                column_samples[col] = vals

    except Exception as e:
        elapsed_err = time.time() - _t0_classify
        _progress_slot.empty()
        st.error(f"Classification failed after {elapsed_err:.1f}s: {e}")
        st.exception(e)
        schema_doc = None

    if schema_doc is not None:
        elapsed_classify = time.time() - _t0_classify
        _progress_slot.markdown(
            _render_classify_progress("done", f"Classified successfully!", elapsed_classify, _ai_model),
            unsafe_allow_html=True)

        try:
            import datetime as _dt
            _raw_pid = project_id_for_classify.strip() or "unknown_project"
            _raw_dir = Path(oxdata_dir) / "data" / _raw_pid
            _raw_dir.mkdir(parents=True, exist_ok=True)
            _raw_ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
            _raw_payload = {
                "generated_at": _dt.datetime.now().isoformat(),
                "project_id": _raw_pid,
                "model": _ai_model,
                "schema_doc": schema_doc,
            }
            _raw_json = json.dumps(_raw_payload, indent=2, default=str)
            (_raw_dir / "llm_mapping_raw.json").write_text(_raw_json, encoding="utf-8")
            (_raw_dir / f"llm_mapping_raw_{_raw_ts}.json").write_text(_raw_json, encoding="utf-8")
            # Save raw_data.xlsx so resume-from-checkpoint doesn't need re-upload
            _raw_xlsx_path = _raw_dir / "raw_data.xlsx"
            if not _raw_xlsx_path.exists():
                data_df_full.to_excel(_raw_xlsx_path, index=False)
        except Exception as _raw_err:
            st.warning(f"Could not save raw LLM mapping audit file: {_raw_err}")

        st.session_state["ap_schema_doc"] = schema_doc
        st.session_state["ap_data_cols"] = data_cols
        st.session_state["ap_column_samples"] = column_samples
        st.session_state["_prefill_project_id"] = project_id_for_classify.strip()
        st.session_state["_classify_elapsed"] = elapsed_classify
        st.session_state["ap_question_edits"] = {}
        st.session_state["ap_manual_rows"] = []

if st.session_state.get("ap_schema_doc") is not None:
    schema_doc = st.session_state["ap_schema_doc"]
    questions = schema_doc.get("questions", [])
    _model_used = schema_doc.get("_model_used")
    _classify_elapsed = st.session_state.get("_classify_elapsed")

    # Completion banner
    _elapsed_str = f" in {_classify_elapsed:.1f}s" if _classify_elapsed else ""
    if _model_used:
        st.success(f"✅ Classification complete{_elapsed_str} — model: `{_model_used}`")

    column_samples = st.session_state.get("ap_column_samples", {})
    all_raw_cols = st.session_state.get("ap_data_cols", [])
    question_edits = st.session_state.setdefault("ap_question_edits", {})
    manual_rows = st.session_state.setdefault("ap_manual_rows", [])

    bucket_options = ["SKIP"] + [b for b in TARGET_BUCKETS.keys() if b != "SKIP"]

    n_high = sum(1 for q in questions if (q.get("confidence") or 0) >= 0.7)
    n_med = sum(1 for q in questions if 0.4 <= (q.get("confidence") or 0) < 0.7)
    n_low = sum(1 for q in questions if (q.get("confidence") or 0) < 0.4)

    def _clean_qtext(t):
        import re as _re2
        t = _re2.sub(r"<[^>]+>", "", str(t or ""))
        return " ".join(t.split())[:120]

    def _sample_for_col(col, n=3, width=35):
        vals = column_samples.get(col, [])
        if not vals:
            return "—"
        return " · ".join(repr(v[:width]) for v in vals[:n])

    def _conf_badge(c):
        c = float(c or 0)
        if c >= 0.8: return "🟢"
        elif c >= 0.5: return "🟡"
        return "🔴"

    editor_rows = []
    for i, q in enumerate(questions):
        cur_bucket = question_edits.get(i, {}).get("bucket", q.get("bucket") or "SKIP")
        cur_source = question_edits.get(i, {}).get("source_column", q.get("source_column"))
        editor_rows.append({
            "_idx": i,
            "question_code": q.get("question_code"),
            "question_text": q.get("question_text"),
            "bucket": cur_bucket,
            "shape": q.get("shape"),
            "source_column": cur_source,
            "confidence": q.get("confidence"),
            "reasoning": q.get("reasoning"),
        })

    # ── BUCKET SUMMARY PILLS ─────────────────────────────────────────────
    st.markdown("### 🗺️ Mapping — how the pipeline read your file")

    _BUCKET_META = {
        "TOM": ("🥇", "#1565C0"), "SPONT": ("💬", "#1976D2"), "AIDED": ("👁", "#0288D1"),
        "CONSIDERATION": ("🤔", "#0097A7"), "EVER_USED": ("📦", "#00796B"),
        "CURRENT_USER": ("✅", "#388E3C"), "PREFERRED": ("⭐", "#558B2F"),
        "LAST_PURCHASED": ("🛒", "#827717"),
        "NPS": ("📊", "#E65100"), "CSAT": ("😊", "#BF360C"),
        "BRAND_IMAGERY": ("🖼", "#6A1B9A"), "IMPORTANCE": ("⚖", "#4527A0"),
        "ATTITUDE": ("💭", "#283593"), "PURCHASE_JOURNEY": ("🗺", "#1A237E"),
        "PRICE_PAID": ("💰", "#880E4F"), "PORTFOLIO_AWARENESS": ("📋", "#4E342E"),
        "GENDER": ("👤", "#37474F"), "AGE": ("🎂", "#37474F"),
        "CITY": ("🏙", "#37474F"), "ZONE": ("🗾", "#37474F"),
        "DEMOGRAPHIC": ("📝", "#455A64"), "SKIP": ("⏭", "#616161"),
    }

    from collections import defaultdict as _defdict
    _by_bucket = _defdict(list)
    for row in editor_rows:
        _by_bucket[row["bucket"]].append(row)

    _non_skip = [(b, rows) for b, rows in sorted(_by_bucket.items()) if b != "SKIP"]
    _skip_rows = _by_bucket.get("SKIP", [])

    _pill_html = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 16px">'
    for b, rows in _non_skip:
        icon, color = _BUCKET_META.get(b, ("•", "#555"))
        lc = sum(1 for r in rows if float(r.get("confidence") or 0) < 0.5)
        warn = " ⚠️" if lc else ""
        _pill_html += (
            f'<span style="background:{color}22;border:1px solid {color}66;'
            f'border-radius:20px;padding:4px 12px;font-size:0.82em;color:{color};font-weight:600">'
            f'{icon} {b} ({len(rows)}){warn}</span>'
        )
    if _skip_rows:
        _pill_html += (
            f'<span style="background:#33333322;border:1px solid #66666644;'
            f'border-radius:20px;padding:4px 12px;font-size:0.82em;color:#888">'
            f'⏭ SKIP ({len(_skip_rows)})</span>'
        )
    _pill_html += '</div>'
    st.markdown(_pill_html, unsafe_allow_html=True)
    _conf_note = (
        f"{n_high} 🟢 auto-confirmed · {n_med} 🟡 medium · {n_low} 🔴 need review "
        f"({len(questions)} total)"
    )
    st.caption(_conf_note)

    # ── FOCUS MODE: show only uncertain rows ──────────────────────────────
    _focus_uncertain = st.toggle(
        "🎯 Focus: show only uncertain classifications (< 0.7 confidence)",
        value=(n_low + n_med) > 0 and (n_low + n_med) < len(questions),
        help="Hide high-confidence rows so you only review what actually needs attention. "
             "High-confidence rows are still ingested — this is display-only.",
        key="ap_focus_uncertain",
    )
    if _focus_uncertain and (n_low + n_med) == 0:
        st.success("All questions classified with high confidence — nothing to review!")
    elif _focus_uncertain:
        st.info(
            f"Showing {n_low + n_med} uncertain rows. "
            f"{n_high} high-confidence rows hidden — they'll still be ingested."
        )

    def _rows_to_show(rows):
        if not _focus_uncertain:
            return rows
        return [r for r in rows if float(r.get("confidence") or 0) < 0.7]

    # ── GROUPED EDITORS per bucket ────────────────────────────────────────
    _all_edits_this_render = {}

    for b, rows in _non_skip + ([("SKIP", _skip_rows)] if _skip_rows else []):
        if not rows:
            continue
        icon, color = _BUCKET_META.get(b, ("•", "#555"))
        low_conf = [r for r in rows if float(r.get("confidence") or 0) < 0.5]
        visible_rows = _rows_to_show(rows)
        if not visible_rows:
            continue  # all high-confidence; skip this bucket in focus mode
        warn_str = f" — ⚠️ {len(low_conf)} need review" if low_conf else ""
        hidden_note = f" · {len(rows) - len(visible_rows)} hidden" if len(visible_rows) < len(rows) else ""
        with st.expander(
            f"{icon} **{b}** &nbsp; {len(visible_rows)} shown{hidden_note}{warn_str}",
            expanded=(b != "SKIP" and len(visible_rows) <= 12),
        ):
            _CTL_WARN_BUCKETS = {
                "TOM", "SPONT", "AIDED", "EVER_USED", "CURRENT_USER",
                "CONSIDERATION", "PREFERRED", "LAST_PURCHASED", "BRAND_IMAGERY",
            }
            _CTL_WARN_SHAPES = {"multivalent_source", "multi_select_dummies", "multi_select"}
            def _ctl_status(row):
                ctl = row.get("code_to_label") or {}
                shape = row.get("shape") or ""
                bucket = row.get("bucket") or ""
                if bucket in _CTL_WARN_BUCKETS and shape in _CTL_WARN_SHAPES:
                    if not ctl:
                        return "⚠ empty"
                    return f"{len(ctl)} brands"
                return ""

            group_df = pd.DataFrame([{
                "_idx": r["_idx"],
                "": _conf_badge(r["confidence"]),
                "Code": r["question_code"] or "",
                "Question": _clean_qtext(r["question_text"]),
                "Bucket": r["bucket"],
                "Source column": r["source_column"] or "",
                "CTL": _ctl_status(r),
                "Sample values": _sample_for_col(r["source_column"] or ""),
                "AI reasoning": (r["reasoning"] or "")[:100],
            } for r in visible_rows])

            edited_group = st.data_editor(
                group_df.drop(columns=["_idx"]),
                column_config={
                    "": st.column_config.TextColumn("", disabled=True, width=28),
                    "Code": st.column_config.TextColumn("Code", disabled=True, width="small"),
                    "Question": st.column_config.TextColumn("Question", disabled=True, width="large"),
                    "Bucket": st.column_config.SelectboxColumn(
                        "Bucket", options=bucket_options, required=True, width="medium"),
                    "Source column": st.column_config.SelectboxColumn(
                        "Source column", options=all_raw_cols, required=True, width="medium"),
                    "CTL": st.column_config.TextColumn(
                        "CTL brands", disabled=True, width="small",
                        help="code→brand mapping entries. '⚠ empty' = brands won't be counted."),
                    "Sample values": st.column_config.TextColumn(
                        "Sample values", disabled=True, width="medium"),
                    "AI reasoning": st.column_config.TextColumn(
                        "AI reasoning", disabled=True, width="large"),
                },
                hide_index=True, use_container_width=True,
                height=min(80 + 38 * len(visible_rows), 420),
                key=f"qeditor_{b}",
                num_rows="fixed",
            )
            for j, (_, erow) in enumerate(edited_group.iterrows()):
                orig_idx = visible_rows[j]["_idx"]
                _all_edits_this_render[orig_idx] = {
                    "bucket": erow["Bucket"],
                    "source_column": erow["Source column"],
                }

    # Reconcile edits into question_edits session state
    for i, orig in enumerate(editor_rows):
        edit = _all_edits_this_render.get(i)
        if edit is None:
            continue
        if edit["bucket"] != orig["bucket"] or edit["source_column"] != orig["source_column"]:
            question_edits[i] = edit
        elif i in question_edits:
            del question_edits[i]

    with st.expander("🔎 code → label detail (per question)"):
        for i, q in enumerate(questions):
            labels = q.get("code_to_label") or {}
            if not labels:
                continue
            with st.expander(f"`{q.get('question_code')}` — {(q.get('question_text') or '')[:70]}"):
                st.json(labels)

    st.divider()
    st.markdown("### ✋ Manual assignment")
    st.info(
        "For raw columns the classifier missed or skipped entirely. Add a row, pick the real "
        "column name from the dropdown (no typing needed), and choose its bucket — it gets "
        "merged into the confirmed assignment below alongside the AI-classified rows above."
    )
    manual_editor_df = st.data_editor(
        pd.DataFrame(manual_rows) if manual_rows else pd.DataFrame({
            "question_code": pd.Series(dtype="str"),
            "source_column": pd.Series(dtype="str"),
            "bucket": pd.Series(dtype="str"),
        }),
        column_config={
            "question_code": st.column_config.TextColumn(
                "Question code (label only)", width="medium",
                help="Free text — used only as a display label, doesn't need to match anything."),
            "source_column": st.column_config.SelectboxColumn(
                "Column name", options=all_raw_cols, required=True, width="large",
                help="Pick the exact column from your uploaded data file."),
            "bucket": st.column_config.SelectboxColumn(
                "Bucket", options=bucket_options, required=True, width="medium"),
        },
        num_rows="dynamic", hide_index=True, use_container_width=True,
        key="manual_assignment_editor",
    )
    st.session_state["ap_manual_rows"] = manual_editor_df.to_dict(orient="records")

    def _build_live_questions():
        live = []
        for i, q in enumerate(questions):
            qq = dict(q)
            override = question_edits.get(i)
            if override:
                qq["bucket"] = override["bucket"]
                qq["source_column"] = override["source_column"]
            live.append(qq)
        for mrow in st.session_state.get("ap_manual_rows", []):
            col = str(mrow.get("source_column") or "").strip()
            bucket = mrow.get("bucket")
            if not col or not bucket or bucket == "SKIP":
                continue
            live.append({
                "question_code": (mrow.get("question_code") or col),
                "question_text": "(manual assignment)",
                "bucket": bucket,
                "shape": "single_value",
                "source_column": col,
                "delimiter": None,
                "dummy_columns": [],
                "code_to_label": {},
                "confidence": 1.0,
                "reasoning": "manually assigned",
            })
        return live

    with st.expander("🔧 Advanced / technical details (only if you need to dig deeper)"):
        st.caption(
            "Everything here is optional — the simplified table above is enough to confirm "
            "and ingest. Use this section to export the current mapping (as edited above) for "
            "client signoff or an audit trail."
        )
        _export_guesses = _questions_to_mapping_guesses(_build_live_questions())
        _export_value_labels_by_code = {g.question_code: g.value_labels for g in _export_guesses}
        c_dl1, c_dl2 = st.columns(2)
        with c_dl1:
            excel_bytes = export_column_map_excel(
                _export_guesses, value_labels_by_code=_export_value_labels_by_code)
            st.download_button("📊 Export Column Map (Excel)", excel_bytes,
                                "column_map_audit.xlsx",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True)
        with c_dl2:
            json_export_str = export_column_map_json(
                _export_guesses, value_labels_by_code=_export_value_labels_by_code)
            st.download_button("📄 Export Column Map (JSON)", json_export_str.encode("utf-8"),
                                "column_map_audit.json", "application/json", use_container_width=True)

        st.markdown("---")
        st.markdown("**📥 Full Mapping Workbook** — richly formatted Excel with AI mapping + dropdown overrides + raw data sheet")
        st.caption(
            "Download this workbook, correct any bucket mappings in the OVERRIDE_bucket column "
            "(dropdown validated), then upload the corrected file below to re-ingest without re-running the AI."
        )
        _wb_pid = (st.session_state.get("_prefill_project_id") or "").strip()
        _wb_db_path = str(Path(oxdata_dir) / "data" / _wb_pid / "oxdata.db") if _wb_pid else None
        _wb_df_raw = st.session_state.get("ap_raw_df")  # stored at upload time
        _wb_live_schema = {"project_id": _wb_pid, "questions": _build_live_questions()}
        try:
            _wb_bytes = build_mapping_workbook(
                schema_doc=_wb_live_schema,
                df_raw=_wb_df_raw if isinstance(_wb_df_raw, __import__("pandas").DataFrame) else None,
                db_path=_wb_db_path if _wb_db_path and Path(_wb_db_path).exists() else None,
                project_id=_wb_pid,
            )
            _wb_fname = f"{_wb_pid or 'mapping'}_workbook.xlsx"
            st.download_button(
                "📥 Download Full Mapping Workbook (.xlsx)",
                _wb_bytes,
                _wb_fname,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dl_full_mapping_wb",
            )
        except Exception as _wb_err:
            st.warning(f"Workbook generation failed: {_wb_err}")

        st.markdown("---")
        st.caption(
            "**Raw LLM classification (audit)** — the unedited mapping the LLM returned for "
            "this project id, saved to disk the moment classification finished."
        )
        _raw_pid_for_view = (st.session_state.get("_prefill_project_id") or "").strip()
        _raw_path_for_view = (
            Path(oxdata_dir) / "data" / _raw_pid_for_view / "llm_mapping_raw.json"
            if _raw_pid_for_view else None
        )
        if _raw_path_for_view and _raw_path_for_view.exists():
            _raw_bytes_for_view = _raw_path_for_view.read_bytes()
            st.download_button(
                "🧾 Download raw LLM mapping (llm_mapping_raw.json)",
                _raw_bytes_for_view, "llm_mapping_raw.json", "application/json",
                use_container_width=True,
            )
            with st.expander("View raw LLM mapping JSON"):
                st.json(json.loads(_raw_bytes_for_view))
        else:
            st.caption("(no saved raw mapping file found for this project id yet)")

    # ── PRE-INGEST VALIDATION ─────────────────────────────────────────────────
    st.divider()
    st.markdown("### 🔍 Pre-ingest validation")
    st.caption("Automated checks run before writing to disk. Fix blockers before ingesting.")

    def _run_preflight(live_questions, raw_cols):
        """Return list of (severity, check_name, message). severity: 'error'|'warning'|'ok'."""
        results = []
        bucket_counts = {}
        for q in live_questions:
            b = q.get("bucket") or "SKIP"
            bucket_counts[b] = bucket_counts.get(b, 0) + 1

        non_skip = {b: n for b, n in bucket_counts.items() if b != "SKIP"}

        # Critical: must have at least one awareness bucket
        awareness = {"TOM", "SPONT", "AIDED"}
        if not any(b in non_skip for b in awareness):
            results.append(("error", "Awareness funnel missing",
                            "No TOM/SPONT/AIDED bucket assigned — funnel chart will be empty."))
        else:
            results.append(("ok", "Awareness funnel",
                            f"{sum(non_skip.get(b,0) for b in awareness)} question(s) mapped."))

        # Critical: NPS or CSAT must be present for loyalty section
        if "NPS" not in non_skip and "CSAT" not in non_skip:
            results.append(("warning", "Loyalty metrics missing",
                            "No NPS or CSAT bucket — Loyalty & Market section will be empty."))
        else:
            results.append(("ok", "Loyalty metrics", "NPS/CSAT present."))

        # Critical: BRAND_IMAGERY must be present for imagery sections
        if "BRAND_IMAGERY" not in non_skip:
            results.append(("error", "Brand imagery missing",
                            "No BRAND_IMAGERY bucket — Imagery & Analytics will be empty."))
        else:
            results.append(("ok", "Brand imagery",
                            f"{non_skip['BRAND_IMAGERY']} attribute(s) mapped."))

        # Warning: demographics
        demo_buckets = {"GENDER", "AGE", "CITY", "ZONE"}
        mapped_demo = [b for b in demo_buckets if b in non_skip]
        if len(mapped_demo) < 2:
            results.append(("warning", "Demographics sparse",
                            f"Only {mapped_demo or 'none'} demographic buckets — filters may be limited."))
        else:
            results.append(("ok", "Demographics", f"Mapped: {', '.join(sorted(mapped_demo))}."))

        # Column existence check
        raw_col_set = set(str(c) for c in raw_cols)
        missing_cols = []
        for q in live_questions:
            src = q.get("source_column") or ""
            if src and src not in raw_col_set:
                missing_cols.append(f"{q.get('question_code')} → '{src}'")
            for dc in (q.get("dummy_columns") or []):
                if dc and dc not in raw_col_set:
                    missing_cols.append(f"{q.get('question_code')} dummy → '{dc}'")
        if missing_cols:
            results.append(("error", "Missing source columns",
                            f"{len(missing_cols)} column(s) not found in raw data: "
                            + ", ".join(missing_cols[:5])
                            + ("…" if len(missing_cols) > 5 else "")))
        else:
            results.append(("ok", "Source columns", "All mapped columns exist in raw data."))

        # CTL-empty check: awareness/imagery questions with no code_to_label → 0 brand rows
        _CTL_REQUIRED_BUCKETS = {
            "TOM", "SPONT", "AIDED", "EVER_USED", "CURRENT_USER",
            "CONSIDERATION", "PREFERRED", "LAST_PURCHASED", "BRAND_IMAGERY",
        }
        _CTL_REQUIRED_SHAPES = {"multivalent_source", "multi_select_dummies", "multi_select"}
        empty_ctl_qs = []
        for q in live_questions:
            bucket = q.get("bucket") or "SKIP"
            shape = q.get("shape") or ""
            ctl = q.get("code_to_label") or {}
            if bucket in _CTL_REQUIRED_BUCKETS and shape in _CTL_REQUIRED_SHAPES and not ctl:
                empty_ctl_qs.append(f"{q.get('question_code')} [{bucket}]")
        if empty_ctl_qs:
            results.append(("warning", "Empty code_to_label",
                            f"{len(empty_ctl_qs)} brand question(s) have no code→brand mapping — "
                            "brands will NOT be counted: " + ", ".join(empty_ctl_qs[:5])
                            + ("…" if len(empty_ctl_qs) > 5 else "")))

        # Low-confidence rows still on real buckets
        low_conf_real = [q for q in live_questions
                         if float(q.get("confidence") or 0) < 0.4
                         and (q.get("bucket") or "SKIP") != "SKIP"]
        if low_conf_real:
            results.append(("warning", "Low-confidence mappings",
                            f"{len(low_conf_real)} question(s) have <40% confidence but are assigned "
                            "to real buckets — verify these are correct before ingesting."))

        return results

    _live_qs_for_preflight = _build_live_questions()
    _raw_cols_for_pf = st.session_state.get("ap_data_cols", [])
    _pf_results = _run_preflight(_live_qs_for_preflight, _raw_cols_for_pf)

    _pf_errors = [r for r in _pf_results if r[0] == "error"]
    _pf_warnings = [r for r in _pf_results if r[0] == "warning"]
    _pf_oks = [r for r in _pf_results if r[0] == "ok"]

    _pf_cols = st.columns(3)
    _pf_cols[0].metric("✅ Passed", len(_pf_oks))
    _pf_cols[1].metric("⚠️ Warnings", len(_pf_warnings))
    _pf_cols[2].metric("❌ Blockers", len(_pf_errors))

    for sev, name, msg in _pf_results:
        if sev == "error":
            st.error(f"**{name}:** {msg}")
        elif sev == "warning":
            st.warning(f"**{name}:** {msg}")
        else:
            st.success(f"**{name}:** {msg}")

    st.divider()
    st.markdown("### 🚀 Ingest — write the confirmed assignment to a new project database")
    st.caption(
        "Every project gets its OWN database file at oxdata/data/<project id>/oxdata.db — "
        "never appended into project_1's real production database, which is hard-blocked below."
    )
    _prefill_id = st.session_state.get("_prefill_project_id", "")
    new_project_id = st.text_input(
        "New project ID (folder-safe: letters, numbers, underscore only)",
        value=_prefill_id,
        placeholder="e.g. akshayakalpa",
        help="Destination: oxdata/data/<this id>/oxdata.db (created fresh, or appended to if "
             "it already exists from a previous confirmed batch for the same project).",
    )

    if st.button("✅ Confirm Bucket Assignment && Ingest", type="primary"):
        live_questions = _build_live_questions()
        live_schema_doc = {"project_id": schema_doc.get("project_id"), "questions": live_questions}
        pieces = assignment_from_schema(live_schema_doc)
        assignment = pieces["assignment"]

        if not assignment:
            st.warning("Nothing assigned — every row is still SKIP.")
        elif not new_project_id or not new_project_id.strip():
            st.error("Enter a new project ID before ingesting.")
        elif not re.match(r'^[a-zA-Z0-9_]+$', new_project_id.strip()):
            st.error("Project ID must be letters, numbers, and underscores only (no spaces or special characters).")
        elif new_project_id.strip() == "project_1":
            st.error("Refusing to write to 'project_1' — that's the real production database.")
        elif data_file is None and "ap_raw_df" not in st.session_state:
            st.error("Raw data file upload is gone — re-upload it and click Process again.")
        elif (not brand_text.strip()
              and any(bucket in assignment for bucket in (
                  "TOM", "SPONT", "AIDED", "EVER_USED", "CURRENT_USER", "CONSIDERATION",
                  "PREFERRED", "BRAND_IMAGERY", "PORTFOLIO_AWARENESS"))):
            st.error(
                "🛑 No brand names entered, but this project has brand-identity questions. "
                "Expand 'Advanced settings' and fill in 'Known brand names', then click ingest again."
            )
        else:
            for bucket in TARGET_BUCKETS:
                items = assignment.get(bucket)
                if not items:
                    continue
                with st.expander(f"{bucket} — {len(items)} question(s)", expanded=False):
                    for it in items:
                        st.markdown(
                            f"`{it['question_code']}` — {it['question_text']} "
                            f"→ column: `{', '.join(str(c) for c in it['data_columns'])}`"
                        )

            _t0_ingest = time.time()
            _ingest_timer = st.empty()

            with st.spinner("Writing to the new project database…"):
                try:
                    _ingest_timer.caption(f"⏱ Writing… 0.0s elapsed")
                    # Use uploaded file or fall back to checkpoint-loaded DataFrame
                    if data_file is not None:
                        data_file.seek(0)
                        if data_file.name.lower().endswith((".xlsx", ".xls")):
                            ingest_df = pd.read_excel(data_file, sheet_name=data_sheet_name)
                        else:
                            ingest_df = pd.read_csv(data_file)
                    else:
                        ingest_df = st.session_state["ap_raw_df"]

                    # Strip ghost columns (in schema but not in raw data) — warn, don't crash
                    missing_cols = sorted({
                        c for items in assignment.values() for it in items
                        for c in it["data_columns"] if c not in ingest_df.columns
                    })
                    if missing_cols:
                        st.warning(
                            f"⚠ {len(missing_cols)} schema column(s) not found in raw data — skipped: "
                            f"{', '.join(missing_cols[:10])}{'…' if len(missing_cols) > 10 else ''}. "
                            "This is normal if the survey skipped some dummy columns."
                        )
                        # Remove ghost columns from assignment
                        for bucket, items in assignment.items():
                            for it in items:
                                it["data_columns"] = [c for c in it["data_columns"] if c in ingest_df.columns]

                    ingest_brand_names = ([b.strip() for b in brand_text.split(",") if b.strip()]
                                           if brand_text else None)
                    # Fallback: if brand_text empty, load non-junk AP brands from master_mapping.xlsx
                    if not ingest_brand_names:
                        _mx_path = Path(oxdata_dir) / "data" / new_project_id.strip() / "master_mapping.xlsx"
                        if _mx_path.exists():
                            try:
                                _bdf = pd.read_excel(str(_mx_path), sheet_name="BRANDS")
                                ingest_brand_names = [
                                    str(r["brand_name"]).strip() for _, r in _bdf.iterrows()
                                    if not bool(r.get("is_junk", False))
                                    and bool(r.get("in_ap_universe", True))
                                    and str(r.get("brand_name", "")).strip()
                                ]
                            except Exception:
                                pass
                    out_dir = Path(oxdata_dir) / "data" / new_project_id.strip()
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_db_path = str(out_dir / "oxdata.db")

                    result = load_confirmed_assignment(
                        assignment, pieces["value_labels_by_code"], pieces["shape_by_code"],
                        ingest_df, out_db_path,
                        brand_names=ingest_brand_names,
                        delimiter_by_code=pieces["delimiter_by_code"],
                    )
                except Exception as e:
                    elapsed_err = time.time() - _t0_ingest
                    _ingest_timer.empty()
                    st.error(f"Ingest failed after {elapsed_err:.1f}s: {e}")
                    st.exception(e)
                    result = None

            if result is not None:
                elapsed_ingest = time.time() - _t0_ingest
                _ingest_timer.empty()
                st.balloons()
                st.success(
                    f"✅ Done in **{elapsed_ingest:.1f}s** — wrote {result['respondents_seen']:,} "
                    f"respondent(s) to `oxdata/data/{new_project_id.strip()}/oxdata.db`. "
                    f"Switch to it via the sidebar 'Active Project' selector."
                )
                st.markdown("**Rows written per bucket:**")
                for bucket, n in result["buckets_written"].items():
                    st.markdown(f"- `{bucket}`: {n:,} rows")
                if result.get("skipped_buckets"):
                    st.warning(f"Skipped (unknown bucket key): {result['skipped_buckets']}")
                if result.get("accepted_not_written"):
                    st.info(f"Bucket(s) confirmed but not written yet: "
                            f"{result['accepted_not_written']}")
                if result.get("warnings"):
                    with st.expander(f"⚠️ {len(result['warnings'])} warning(s) from the ingest",
                                      expanded=True):
                        for w in result["warnings"]:
                            st.markdown(f"- {w}")
                # Write master_mapping.xlsx with embedded raw data
                try:
                    from lens.ingestion.master_excel import write_master_excel as _write_master
                    _master_out = str(out_dir / "master_mapping.xlsx")
                    _schema_for_excel = st.session_state.get("ap_schema_doc", {})
                    _raw_for_excel = ingest_df
                    _write_master(
                        _master_out, _schema_for_excel,
                        str(out_dir / "raw_data.xlsx"), new_project_id.strip(),
                        raw_df=_raw_for_excel,
                    )
                    st.info(f"📊 `master_mapping.xlsx` written to `oxdata/data/{new_project_id.strip()}/` "
                            f"— includes RAW_DATA sheet ({len(_raw_for_excel):,} rows). "
                            f"Edit mapping/brands in Excel; re-ingest to apply changes.")
                    with open(_master_out, "rb") as _fh:
                        st.download_button(
                            "⬇️ Download master_mapping.xlsx",
                            _fh.read(),
                            file_name=f"{new_project_id.strip()}_master_mapping.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="dl_master_mapping_new",
                        )
                except Exception as _xl_err:
                    st.warning(f"master_mapping.xlsx not written: {_xl_err}")


# ══════════════════════════════════════════════════════════════════════════════
# WORKBOOK RE-INGEST SECTION
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
with st.expander("📋 Re-ingest from Corrected Mapping Workbook", expanded=False):
    st.markdown(
        "**If you corrected the OVERRIDE columns in the downloaded mapping workbook**, upload it here "
        "to re-run ingestion with your corrections — without re-uploading raw data or re-running the AI."
    )
    st.caption(
        "Only the OVERRIDE_bucket and OVERRIDE_source_column columns are read. "
        "Leave them blank for questions you want to keep as-is from the original AI mapping."
    )
    _rb_wb_file = st.file_uploader(
        "Upload corrected mapping workbook (.xlsx)",
        type=["xlsx"],
        key="reingest_workbook_file",
    )
    _rb_project_id = st.text_input(
        "Project ID to re-ingest into",
        placeholder="e.g. akshayakalpa",
        key="reingest_project_id",
        help="Must match an existing project with a saved llm_mapping_raw.json checkpoint.",
    )
    if _rb_wb_file and _rb_project_id.strip():
        _rb_pid = _rb_project_id.strip()
        _rb_json_path = Path(oxdata_dir) / "data" / _rb_pid / "llm_mapping_raw.json"
        if not _rb_json_path.exists():
            st.error(
                f"No saved mapping checkpoint found at oxdata/data/{_rb_pid}/llm_mapping_raw.json. "
                "Run the classify step first for this project, then download and correct the workbook."
            )
        elif _rb_pid == "project_1":
            st.error("Refusing to re-ingest into 'project_1' — that's the real production database.")
        else:
            try:
                _rb_overrides = read_master_mapping(_rb_wb_file.getvalue())
                st.success(f"Workbook parsed — {len(_rb_overrides)} override(s) found.")
                if _rb_overrides:
                    st.dataframe(
                        pd.DataFrame([
                            {"question_code": k, **v}
                            for k, v in _rb_overrides.items()
                        ]),
                        hide_index=True, use_container_width=True,
                    )
                else:
                    st.info("No override columns filled — workbook matches original AI mapping. Nothing to change.")
            except Exception as _rb_err:
                st.error(f"Failed to parse workbook: {_rb_err}")
                _rb_overrides = {}

            if _rb_overrides and st.button(
                "✅ Apply Overrides && Re-ingest", type="primary", key="reingest_apply_btn"
            ):
                try:
                    _rb_raw_loaded = json.loads(_rb_json_path.read_text(encoding="utf-8"))
                    # Checkpoint may be wrapped {generated_at, schema_doc, ...} or bare schema_doc
                    _rb_base_schema = _rb_raw_loaded.get("schema_doc", _rb_raw_loaded)
                    _rb_corrected_schema = apply_overrides_to_schema(_rb_base_schema, _rb_overrides)
                    _rb_pieces = assignment_from_schema(_rb_corrected_schema)
                    _rb_assignment = _rb_pieces["assignment"]
                    _rb_data_path = Path(oxdata_dir) / "data" / _rb_pid
                    # Try session state first; fall back to saved raw_data.xlsx on disk
                    _rb_raw_df = st.session_state.get("ap_raw_df")
                    _rb_raw_xlsx = _rb_data_path / "raw_data.xlsx"
                    if (_rb_raw_df is None or not isinstance(_rb_raw_df, pd.DataFrame)) and _rb_raw_xlsx.exists():
                        _rb_raw_df = pd.read_excel(_rb_raw_xlsx)
                    if _rb_raw_df is None or not isinstance(_rb_raw_df, pd.DataFrame):
                        st.error(
                            "Raw data file not in session and no raw_data.xlsx saved on disk. "
                            "Use the '⏩ Resume from saved checkpoint' section above to restore session, "
                            "or re-upload the raw file in the classify section."
                        )
                    else:
                        with st.spinner("Re-ingesting with corrected mappings…"):
                            _rb_brand_names = [
                                b.strip() for b in
                                st.session_state.get("ap_brand_text", "").split(",")
                                if b.strip()
                            ]
                            _rb_out_dir = Path(oxdata_dir) / "data" / _rb_pid
                            _rb_out_dir.mkdir(parents=True, exist_ok=True)
                            _rb_out_db = str(_rb_out_dir / "oxdata.db")
                            _rb_result = load_confirmed_assignment(
                                _rb_pieces["assignment"],
                                _rb_pieces.get("value_labels_by_code", {}),
                                _rb_pieces.get("shape_by_code", {}),
                                _rb_raw_df,
                                _rb_out_db,
                                brand_names=_rb_brand_names,
                                delimiter_by_code=_rb_pieces.get("delimiter_by_code", {}),
                            )
                        st.success(f"✅ Re-ingested into oxdata/data/{_rb_pid}/oxdata.db with your corrections.")
                        if _rb_result.get("warnings"):
                            for _w in _rb_result["warnings"]:
                                st.warning(_w)
                except Exception as _rb_ingest_err:
                    st.error(f"Re-ingest failed: {_rb_ingest_err}")

# ══════════════════════════════════════════════════════════════════════════════
# MANAGE PROJECT SECTION
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
with st.expander("⚙️ Manage Existing Project", expanded=False):
    _render_manage_project()
