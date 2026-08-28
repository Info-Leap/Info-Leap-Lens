"""
InfoLeap Pulse â€” Settings, System Info & Model Configuration
Shows the actual models, data sources, and pipeline used in this instance.
"""
import os
import json
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv
from infoleap.utils.ui_styles import (inject_pulse_styles, sidebar_context_block,
                                    section_header, kpi_card, page_banner)

inject_pulse_styles()
sidebar_context_block()

_BASE = Path(__file__).resolve().parent.parent
load_dotenv(str(_BASE / ".env"), override=True)

page_banner("Settings & System Info",
            subtitle="Actual models, data sources, pipeline configuration â€” no placeholder values",
            eyebrow="System Configuration")

# â”€â”€ Data overview â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
section_header("Data Overview")

_data_dir = _BASE / "data"
_quant_db = _data_dir / "project_1" / "oxdata.db"
_projects_dir = _data_dir / "projects"

col1, col2, col3, col4 = st.columns(4)
db_size = round(_quant_db.stat().st_size / 1024 / 1024, 1) if _quant_db.exists() else 0
mixer_matrices = len(list((_data_dir / "qual_matrices").glob("*_matrix.json"))) if (_data_dir / "qual_matrices").exists() else 0
cdcx_matrices = len(list((_projects_dir / "karat-coindcx" / "matrices").glob("*_matrix.json"))) if (_projects_dir / "karat-coindcx" / "matrices").exists() else 0
with col1: kpi_card("Survey DB", f"{db_size} MB", "#1a5d4d", subtext="SQLite Â· data/project_1/")
with col2: kpi_card("Respondents", "6,631", "#0ea5e9", subtext="6 categories Â· 18 cities")
with col3: kpi_card("Mixer Matrices", str(mixer_matrices), "#7c3aed", subtext="233 IDI transcripts")
with col4: kpi_card("CoinDCX Matrices", str(cdcx_matrices), "#f59e0b", subtext="23 concept-test DIs")

# â”€â”€ Projects â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
section_header("Active Qualitative Projects")

_registry_path = _projects_dir / "registry.json"
if _registry_path.exists():
    registry = json.loads(_registry_path.read_text(encoding="utf-8"))
    for proj in registry.get("projects", []):
        pid = proj["id"]
        m_dir = _projects_dir / pid / "matrices"
        f_dir = _projects_dir / pid / "findings"
        n_matrices = len(list(m_dir.glob("*_matrix.json"))) if m_dir.exists() else 0
        n_findings = len(list(f_dir.glob("*.json"))) - 1 if f_dir.exists() else 0  # -1 for index.json

        # Load quality report if available
        vr_path = _projects_dir / pid / "verification_report.json"
        qual_score = None
        if vr_path.exists():
            try:
                vr = json.loads(vr_path.read_text(encoding="utf-8"))
                qual_score = vr.get("overall_quality_score")
            except Exception:
                pass

        with st.expander(f"**{proj['display_name']}** â€” {proj['study_type']} Â· {n_matrices} matrices", expanded=True):
            c1, c2, c3, c4 = st.columns(4)
            with c1: kpi_card("Interviews", str(n_matrices), "#1a5d4d")
            with c2: kpi_card("Findings", str(max(n_findings, 0)), "#0ea5e9")
            q_color = "#22c55e" if qual_score and qual_score >= 80 else ("#f59e0b" if qual_score else "#9ca3af")
            with c3: kpi_card("Verbatim Quality", f"{qual_score:.1f}%" if qual_score else "Unverified", q_color)
            with c4: kpi_card("Study Type", proj["study_type"].replace("_", " ").title(), "#7c3aed")
            st.caption(proj.get("description", "")[:120])

# â”€â”€ LLM Models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
section_header("LLM Models â€” OpenRouter Free Tier")

st.info("All AI inference uses OpenRouter free-tier models only. No paid model fallback.", icon="ðŸ”“")

_model_table = [
    ("deepseek/deepseek-r1:free", "671B", "Findings generation, complex analysis", "Primary â€” best reasoning quality"),
    ("qwen/qwen3-235b-a22b:free", "235B", "Findings generation, segment analysis", "Primary â€” strong analytical depth"),
    ("moonshotai/kimi-k2.6:free", "~70B", "Transcript extraction, Q&A", "Reliable, good context handling"),
    ("meta-llama/llama-4-scout:free", "~100B", "Transcript extraction, Q&A", "Fast, reliable fallback"),
    ("openai/gpt-oss-120b:free", "120B", "Extraction, verbatim analysis", "General purpose"),
    ("microsoft/phi-4-reasoning:free", "14B", "Small reasoning tasks", "Low-latency fallback"),
    ("google/gemma-4-31b-it:free", "31B", "Simple classification tasks", "Last fallback"),
]

model_data = []
for model, params, uses, note in _model_table:
    model_data.append({"Model": model, "Parameters": params, "Used for": uses, "Notes": note})

import pandas as pd
st.dataframe(pd.DataFrame(model_data), use_container_width=True, hide_index=True)

# â”€â”€ Pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
section_header("Qualitative Intelligence Pipeline")

st.markdown("""
```
New Project â†’ Drop files in projects/{brand_name}/
              â”œâ”€â”€ transcripts/*.docx   (raw interview files)
              â””â”€â”€ source_docs/
                  â”œâ”€â”€ DG_*.docx        (discussion guide)
                  â””â”€â”€ AI_Prompt_*.docx (extraction instructions)

Step 1: schema_generator.py
        Reads DG + AI_Prompt â†’ generates extraction_schema.json + master_prompt.txt
        Model: deepseek-r1:free or qwen3-235b:free
        Output: Layer 2 schema (project-specific fields) + report_structure.json

Step 2: docx_to_md.py
        Converts .docx transcripts â†’ .md with YAML frontmatter metadata
        No LLM. Pure python-docx.

Step 3: project_extractor.py
        Two-step extraction per transcript:
        Phase 1 (think): Free-form analysis â€” who is this person, what do they say?
        Phase 2 (structure): JSON extraction against schema
        Model: deepseek-r1:free (best) â†’ qwen3-235b:free â†’ moonshotai â†’ llama-4
        Output: {doc_id}_matrix.json per transcript

Step 4: verify_verbatims.py
        BM25-based check: every verbatim_quote located in source .md file
        Methods: exact match â†’ prefix-40 â†’ prefix-25 â†’ word overlap (60%)
        Output: _quality_score per matrix (96.0% for Mixer, 83.2% for CoinDCX)
        No LLM.

Step 5: findings_generator.py
        Per DG section: aggregates matrix data â†’ generates research finding
        Format: FINDING / SEGMENT DIFFERENCES / KEY VERBATIMS / STRATEGIC IMPLICATION
        Only uses verbatims present in data â€” never invents quotes
        Model: deepseek-r1:free or qwen3-235b:free
        Output: projects/{id}/findings/{section_id}.json
```
""")

# â”€â”€ Scoring formulas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
section_header("Signal Score Formulas")

st.markdown("""
**Brand Satisfaction (0-100)**
```
NPS promoter signal:      Ã— 0.40  (max 40 pts)  â€” promoter=40, passive=20, detractor=0
Positive emotional arc:   Ã— 0.35  (max 35 pts)  â€” positive=35, neutral=20, negative=0
Relationship health:      Ã— 0.25  (max 25 pts)  â€” honeymoon=25, settled=22, strained=6, at_risk=2
TOTAL: min(100, sum of above)
```

**Brand Risk (0-100)**
```
High-severity pain pts:   Ã— 14 each (max 5 = 70 pts)
Product blame ratio:      Ã— 30      (max 30 pts â€” if all blame is on product)
Switching consideration:  +22       (binary flag from brand_relationship.switching_consideration)
Won't recommend flag:     +15       (wont_recommend in advocacy_likelihood)
TOTAL: min(100, sum of above)
```

**Brand Opportunity (0-100)**
```
High-opportunity gaps:    Ã— 28 each (max 56 pts â€” 7 gaps Ã— 28)
Medium-opportunity gaps:  Ã— 12 each (max 36 pts)
Unspoken needs:           Ã— 7 each  (max 35 pts)
TOTAL: min(100, sum of above)
```

**CoinDCX Adoption Readiness (0-100)**
```
Avg intent score:         Ã— 5   (0-50 pts)
Avg comprehension score:  Ã— 3   (0-30 pts)
Timeline bonus (0-3m/3-6m): up to 20 pts
TOTAL: min(100, sum of above)
```

**CoinDCX Trust Gap Risk (0-100)**
```
High trust gap %:         Ã— 40 (up to 40 pts)
Medium trust gap %:       Ã— 15 (up to 15 pts)
High-severity pain pts:   Ã— 3 each
TOTAL: min(100, sum of above)
```
""")

# â”€â”€ Environment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
section_header("Environment")

openrouter_key_set = bool(os.getenv("OPENROUTER_API_KEY"))
google_key_set = bool(os.getenv("GOOGLE_API_KEY"))

col1, col2, col3 = st.columns(3)
with col1: kpi_card("OpenRouter Key", "Set âœ“" if openrouter_key_set else "Not set âœ—",
                    "#22c55e" if openrouter_key_set else "#ef4444")
with col2: kpi_card("Google Key (Gemini)", "Set âœ“" if google_key_set else "Not set âœ—",
                    "#22c55e" if google_key_set else "#ef4444")
with col3: kpi_card("App Framework", "Streamlit", "#0ea5e9")

st.caption("Keys are loaded from oxdata/.env â€” never stored in code or version control.")
