"""
DEAD CODE ARCHIVE — Quote Explorer legacy Mixer tab block
============================================================
Extracted 2026-07-17 from oxdata/views/quote_explorer.py (was lines 3462-7073).
NEVER IMPORTED, NEVER EXECUTED. Confirmed unreachable: every _study_type branch
(concept_testing / ethnographic / generic) calls st.stop() before this code was
reached in the original file. Kept only as reference for the rebuild (e.g. old
signal-score / topic-coverage / sentiment-arc comparison logic some of it may be
worth reusing). Do not import this module — it has no guaranteed working
dependencies (relies on names like _P, _clean_quote, _section, _kpi, intel,
named_brands, _TOPIC_KWORDS, _BRAND_PAL that lived earlier in quote_explorer.py).
"""

@st.cache_data(ttl=1800, show_spinner=False)
def _load_brand_passages(brand: str, _mtime: float = 0.0) -> list[dict]:
    """All passages for one brand from raw matrices, with doc_id for interview grouping."""
    if not _MATRICES_DIR.exists():
        return []
    result = []
    for fpath in sorted(_MATRICES_DIR.glob("*_matrix.json")):
        try:
            m = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        resp = m.get("respondent", {})
        b = resp.get("brand_owned") or ""
        if b.lower() != brand.lower():
            continue
        doc_id  = m.get("doc_id", fpath.stem.replace("_matrix", ""))
        city    = resp.get("city") or "Unknown"
        journey = resp.get("journey_stage", "")
        for p in (m.get("all_passages") or []):
            cnt = (p.get("content") or "").strip()
            if cnt and len(cnt) >= 30:
                result.append({
                    "content":        cnt,
                    "sentiment":      p.get("sentiment", "neutral"),
                    "topic":          p.get("topic", ""),
                    "pain_point":     bool(p.get("pain_point")),
                    "decision_signal":bool(p.get("decision_signal")),
                    "doc_id":         doc_id,
                    "city":           city,
                    "journey":        journey,
                    "brand":          brand,
                })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# MIXER VIEW (ethnographic study)
# ═════════════════════════════════════════════════════════════════════════════
# Load Mixer config — paths resolved from registry, not hardcoded
_mx_proj_cfg   = _pm.get_project("mixer") or {}
_mx_paths      = _mx_proj_cfg.get("abs_paths", {})
_mx_m_dir      = _mx_paths.get("matrices", _MATRICES_DIR)
_mx_src_dir    = _mx_paths.get("source_docs", _BASE / "data" / "projects" / "mixer" / "source_docs")
_mx_schema_dir = _mx_paths.get("schema", _BASE / "data" / "projects" / "mixer" / "schema" / "extraction_schema.json")
_mx_schema_dir = _mx_schema_dir.parent if _mx_schema_dir and not _mx_schema_dir.is_dir() else _mx_schema_dir
_mx_findings_dir_path = _mx_src_dir.parent / "findings" if _mx_src_dir else _BASE / "data" / "projects" / "mixer" / "findings"
_mx_rs_path    = _mx_src_dir / "report_structure.json" if _mx_src_dir else None
_mx_mp_path    = _mx_schema_dir / "master_prompt.txt" if _mx_schema_dir else None
_mx_ui         = _mx_proj_cfg.get("ui_config", {})
_mx_ai_keys    = _mx_ui.get("ai_insight_keys", ["WHAT CONSUMERS LOVE","PAIN POINTS","BRAND EQUITY SIGNAL","STRATEGIC SIGNAL"])
_mx_ai_icons   = _mx_ui.get("ai_insight_icons", {})
_mx_tab_desc   = _mx_ui.get("tab_descriptions", {})
_mx_entity_lbl = _mx_ui.get("entity_label", "Brand")
_mx_study_ctx  = _mx_ui.get("study_context", "")







# TAB 2 — TRANSCRIPT INTELLIGENCE
# ═════════════════════════════════════════════════════════════════════════════


with st.spinner("Loading transcript matrices…"):
    intel = _load_matrix_intel(_matrices_mtime())

# Fallback to old tree-based intel if no matrices yet
_using_matrices = bool(intel)
if not intel:
    with st.spinner("Falling back to legacy transcript index…"):
        intel = _load_transcript_intel(_trees_mtime(), _v=3)
    intel.pop("__null_doc_count__", 0)

def _is_junk_brand(name: str) -> bool:
    """Reject respondent-ID-prefixed tokens that leak in as brands.

    e.g. 'R_8a4faf06 Zodiac' — a raw Qualtrics respondent id ('R_' + base62)
    mis-parsed as a brand_owned value. Real mixer brands never start with 'R_<id>'.
    """
    import re as _re
    if not name or not isinstance(name, str):
        return True
    n = name.strip()
    # 'R_' followed by 6+ alphanumerics (optionally then a stray word)
    if _re.match(r'^R_[0-9A-Za-z]{6,}', n):
        return True
    # bare respondent id with no real brand token
    if _re.fullmatch(r'[0-9A-Fa-f]{6,}', n):
        return True
    return False


named_brands = sorted(
    [b for b in intel if isinstance(intel[b], dict)
     and intel[b].get("n_docs", 0) >= 1 and not _is_junk_brand(b)],
    key=lambda b: -intel[b].get("n_docs", 0),
)
null_count = 0

if not named_brands:
    n_matrices = len(list(_MATRICES_DIR.glob("*_matrix.json"))) if _MATRICES_DIR.exists() else 0
    if n_matrices == 0:
        st.info(
            "**Transcript matrices not generated yet.**\n\n"
            "Run the extractor first:\n"
            "```bash\n"
            "python lens/ingestion/transcript_matrix_builder.py --workers 5\n"
            "```\n"
            "This takes ~70 minutes and generates 233 matrix files."
        )
    else:
        st.warning(
            f"{n_matrices} matrices found but no brands extracted. "
            "Most docs may be non-brand transcripts (smoothie makers, intenders). "
            "Check `oxdata/data/qual_matrices/` for brand_owned field."
        )
    st.stop()

# Accurate unique counts — not per-brand sums (which double-count multi-brand docs)
total_docs = len(list(_TREES_DIR.glob("*.json"))) if _TREES_DIR.exists() else sum(intel[b].get("n_docs", 0) for b in named_brands)
total_passages = _count_tree_passages()
total_pain     = sum(len(intel[b].get("pain_points", [])) for b in named_brands)
total_gaps     = sum(len(intel[b].get("aspiration_gaps", [])) for b in named_brands)

data_source_label = "matrices (new pipeline)" if _using_matrices else "legacy tree index"

# ── Header info strip ─────────────────────────────────────────────────────
st.markdown(
    f'<div style="background:#faf5ff;border:1px solid #e9d5ff;border-radius:10px;'
    f'padding:12px 16px;margin-bottom:16px;font-size:0.87rem;display:flex;gap:24px;flex-wrap:wrap;">'
    f'<span><b style="color:{_P["purple"]};">{total_docs}</b> IDI transcripts</span>'
    f'<span><b style="color:{_P["teal"]};">{total_passages:,}</b> passages</span>'
    f'<span><b style="color:{_P["blue"]};">{len(named_brands)}</b> brands</span>'
    f'<span><b style="color:{_P["red"]};">{total_pain:,}</b> pain points</span>'
    f'<span><b style="color:{_P["amber"]};">{total_gaps:,}</b> aspiration gaps</span>'
    f'<span style="color:#9ca3af;font-size:0.75rem;">source: {data_source_label}</span>'
    f'</div>',
    unsafe_allow_html=True,
)

# ── Project-level KPI strip (matches CoinDCX layout: above tabs) ─────────────
_mx_prom_avg = round(sum(intel[b].get("promoter_pct", 0) for b in named_brands) / max(len(named_brands), 1), 1) if named_brands else 0
_mx_all_rel: dict = {}
for _bx in named_brands:
    for _rk, _rv in intel[_bx].get("relationship_stages", {}).items():
        _mx_all_rel[_rk] = _mx_all_rel.get(_rk, 0) + _rv
_mx_total_rel = max(sum(_mx_all_rel.values()), 1)
_mx_loyal_pct = round((_mx_all_rel.get("honeymoon", 0) + _mx_all_rel.get("settled_satisfied", 0)) / _mx_total_rel * 100, 1)
_mx_risk_pct  = round((_mx_all_rel.get("strained", 0) + _mx_all_rel.get("at_risk", 0)) / _mx_total_rel * 100, 1)
_mx_n_pain   = sum(len(intel[b].get("pain_points", [])) for b in named_brands)
_mxk1, _mxk2, _mxk3, _mxk4, _mxk5, _mxk6 = st.columns(6)
with _mxk1: _kpi(str(total_docs),        "Interviews",      _P["teal"])
with _mxk2: _kpi(str(len(named_brands)), "Brands",          _P["blue"])
with _mxk3: _kpi(f"{_mx_prom_avg:.0f}%", "Avg NPS Promoter",_P["green"])
with _mxk4: _kpi(f"{_mx_loyal_pct:.0f}%","Loyal/Satisfied", _P["emerald"])
with _mxk5: _kpi(f"{_mx_risk_pct:.0f}%", "Strained/At-Risk",_P["red"])
with _mxk6: _kpi(f"{_mx_n_pain:,}",      "Pain Points",     _P["amber"])
st.caption(
    "Interviews: total IDI transcripts extracted  ·  "
    "Avg NPS Promoter: % of interviews per brand where nps_signal='promoter', averaged across brands  ·  "
    "Loyal/Satisfied: % of relationship_stage ∈ {honeymoon, settled_satisfied}  ·  "
    "Strained/At-Risk: % ∈ {strained, at_risk}  ·  "
    "Pain Points: total extracted pain_point entries across all interviews"
)
st.markdown("<div style='margin:6px 0;'></div>", unsafe_allow_html=True)

# ── Extraction quality banner (parallel to CoinDCX quality summary) ──────────
_mx_n_mats = len(list(_MATRICES_DIR.glob("*_matrix.json"))) if _MATRICES_DIR.exists() else 0
if _mx_n_mats > 0 and _using_matrices:
    st.markdown(
        f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:8px 14px;'
        f'margin-bottom:8px;font-size:0.76rem;color:#166534;">'
        f'<b>{_mx_n_mats}</b> interview matrices extracted · '
        f'Quote attribution: LLM-extracted from source transcripts (no automated verbatim verification) · '
        f'Spot-check quotes against source files in <code>projects/mixer/transcripts/</code>'
        f'</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f'<div style="background:#fffbeb;border:1.5px solid #fcd34d;border-radius:8px;padding:8px 14px;'
        f'margin-bottom:8px;font-size:0.76rem;color:#92400e;">'
        f'Using legacy tree-index data (no per-interview matrices) · '
        f'Run matrix pipeline for richer analytics: '
        f'<code>python lens/ingestion/transcript_matrix_builder.py --workers 5</code>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── Signal scores (parallel to CoinDCX Adoption/Trust/Comprehension) ─────────
def _mx_project_signal_scores(intel_dict: dict, brand_list: list) -> dict:
    sat_vals, risk_vals, opp_vals = [], [], []
    for b in brand_list:
        bd = intel_dict.get(b, {})
        n = max(bd.get("n_docs", 0), 1)
        prom_pct   = bd.get("promoter_pct", 0) or 0
        pos_res_pct = bd.get("positive_resolution_pct", 0) or 0
        rel_stages = bd.get("relationship_stages", {})
        loyal_cnt = rel_stages.get("honeymoon", 0) + rel_stages.get("settled_satisfied", 0)
        at_risk_cnt = rel_stages.get("strained", 0) + rel_stages.get("at_risk", 0)
        loyal_pct   = round(loyal_cnt / n * 100, 1)
        at_risk_pct = round(at_risk_cnt / n * 100, 1)
        sat = min(100, round(prom_pct * 0.40 + pos_res_pct * 0.35 + loyal_pct * 0.25))
        pain_pts = bd.get("pain_points", [])
        n_high_pain = sum(1 for p in pain_pts if isinstance(p, dict) and p.get("severity") in ("high", "critical"))
        blame = bd.get("blame", {}) or {}
        pb_ratio = round(blame.get("product_ratio", 0) * 100, 1) if isinstance(blame, dict) else 0
        risk = min(100, round(n_high_pain / n * 30 + pb_ratio * 0.30 + at_risk_pct * 0.40))
        gaps = bd.get("aspiration_gaps", [])
        n_hi_gaps = sum(1 for g in gaps if isinstance(g, dict) and g.get("opportunity") == "high")
        unspk = bd.get("unspoken_needs", [])
        opp = min(100, round(n_hi_gaps * 12 + len(unspk) * 5))
        sat_vals.append(sat); risk_vals.append(risk); opp_vals.append(opp)
    avg = lambda vs: round(sum(vs) / max(len(vs), 1))
    return {"satisfaction": avg(sat_vals), "churn_risk": avg(risk_vals), "opportunity": avg(opp_vals)}

_mx_proj_sigs = _mx_project_signal_scores(intel, named_brands)
_mss1, _mss2, _mss3 = st.columns(3)
for _msc, _mslbl, _mstv, _mshib, _mslo, _mshi, _msformula in [
    (_mss1, "Brand Satisfaction", _mx_proj_sigs["satisfaction"], True,  "Low",  "High",
     "NPS promoter% × 0.4 + positive emotional resolution% × 0.35 + loyal/satisfied% × 0.25"),
    (_mss2, "Churn Risk",         _mx_proj_sigs["churn_risk"],  False, "Safe", "Critical",
     "High-severity pain points per interview × 30 + product blame% × 0.3 + at-risk relationship% × 0.4"),
    (_mss3, "Opportunity Score",  _mx_proj_sigs["opportunity"], True,  "None", "High",
     "High-opportunity aspiration gaps × 12 + unspoken needs × 5, averaged across brands"),
]:
    with _msc:
        _mssv = _mstv
        if _mshib: _mscc = _P["green"] if _mssv >= 65 else (_P["amber"] if _mssv >= 40 else _P["red"])
        else:       _mscc = _P["red"]   if _mssv >= 65 else (_P["amber"] if _mssv >= 40 else _P["green"])
        st.markdown(
            f'<div style="background:{_mscc}07;border:1px solid {_mscc}22;border-radius:10px;'
            f'padding:9px 14px;display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">'
            f'<div><div style="font-size:0.58rem;font-weight:800;color:#9ca3af;text-transform:uppercase;">{_mslbl}</div>'
            f'<div style="font-size:0.62rem;color:#9ca3af;margin-top:1px;">{_mslo} → {_mshi}</div></div>'
            f'<div style="text-align:right;">'
            f'<div style="font-size:1.5rem;font-weight:900;color:{_mscc};line-height:1;">{_mssv}</div>'
            f'<div style="background:#e5e7eb;border-radius:3px;height:4px;width:70px;margin-top:4px;">'
            f'<div style="width:{_mssv}%;background:{_mscc};height:100%;border-radius:3px;"></div></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        st.caption(f"Formula: {_msformula}")

# ── Load raw Mixer matrices (needed for global filters + interview browser) ─
@st.cache_data(ttl=1800, show_spinner=False)
def _load_raw_mx_mats(_mtime: float = 0.0, _search_dir: str = "") -> list[dict]:
    search_path = Path(_search_dir) if _search_dir else _MATRICES_DIR
    if not search_path.exists(): return []
    mats = []
    for fp in sorted(search_path.glob("*_matrix.json")):
        try:
            m = json.loads(fp.read_text(encoding="utf-8"))
            m["_source_file"] = fp.name
            mats.append(m)
        except Exception: pass
    return mats
_raw_mx_mats = _load_raw_mx_mats(_matrices_mtime(), str(_mx_m_dir) if _mx_m_dir else "")

# ── Global filter bar (parallel to CoinDCX Segment/City/Route filters) ───
_mx_all_cities_gf  = sorted({(m.get("respondent") or {}).get("city","") for m in _raw_mx_mats if (m.get("respondent") or {}).get("city")})
_mx_all_stages_gf  = sorted({(m.get("respondent") or {}).get("journey_stage","") for m in _raw_mx_mats if (m.get("respondent") or {}).get("journey_stage")})
_mxgf1, _mxgf2, _mxgf3 = st.columns(3)
with _mxgf1: _mx_brand_gf = st.selectbox("Brand", ["All"] + named_brands, key="mx_brand_gf")
with _mxgf2: _mx_city_gf  = st.selectbox("City",  ["All"] + _mx_all_cities_gf, key="mx_city_gf")
with _mxgf3: _mx_stage_gf = st.selectbox("Journey Stage", ["All"] + _mx_all_stages_gf, key="mx_stage_gf")

def _mx_filter(mats):
    out = mats
    if _mx_brand_gf != "All": out = [m for m in out if (m.get("respondent") or {}).get("brand_owned","") == _mx_brand_gf]
    if _mx_city_gf  != "All": out = [m for m in out if (m.get("respondent") or {}).get("city","") == _mx_city_gf]
    if _mx_stage_gf != "All": out = [m for m in out if (m.get("respondent") or {}).get("journey_stage","") == _mx_stage_gf]
    return out
_mx_filtered = _mx_filter(_raw_mx_mats)
_mx_fn = len(_mx_filtered)

# ── Dataset overview strip ────────────────────────────────────────────────
st.markdown(
    f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;'
    f'padding:10px 16px;margin-bottom:12px;font-size:0.82rem;display:flex;gap:20px;flex-wrap:wrap;">'
    f'<b style="color:{_P["teal"]};">What\'s in this dataset:</b>'
    f'<span><b style="color:{_P["teal"]};">{_mx_fn if _mx_fn != total_docs else total_docs}</b> '
    f'{"of " + str(total_docs) + " " if _mx_fn != total_docs else ""}in-depth interviews (2–3hr home immersions)</span>'
    f'<span><b style="color:{_P["blue"]};">{len(named_brands)}</b> mixer/juicer brands</span>'
    f'<span><b style="color:{_P["red"]};">{_mx_n_pain:,}</b> extracted pain points</span>'
    f'<span><b style="color:{_P["amber"]};">{total_gaps:,}</b> aspiration gaps</span>'
    f'<span style="color:#9ca3af;font-size:0.75rem;">source: {data_source_label}</span>'
    f'</div>',
    unsafe_allow_html=True,
)

# ── Findings Report — PRIMARY DELIVERABLE ────────────────────────────────
# =============================================================================
# MIXER FINDINGS REPORT
# =============================================================================
st.markdown('---')
st.markdown(
    f'<div style="background:linear-gradient(135deg,#0a2e22 0%,#1a5d4d 60%,#0ea5e9 100%);'
    f'border-radius:12px;padding:16px 22px;margin-bottom:12px;">'
    f'<div style="font-size:0.60rem;font-weight:700;color:rgba(255,255,255,0.45);'
    f'text-transform:uppercase;letter-spacing:0.14em;margin-bottom:3px;">Research Findings</div>'
    f'<div style="font-size:1.2rem;font-weight:900;color:white;">Crompton FMCD — Findings Report</div>'
    f'<div style="font-size:0.76rem;color:rgba(255,255,255,0.55);margin-top:3px;">'
    f'AI-generated findings per research section — 233 IDI interviews</div>'
    f'</div>',
    unsafe_allow_html=True,
)

_mx_rs_path = _mx_rs_path if _mx_rs_path else _BASE / "data" / "projects" / "mixer" / "source_docs" / "report_structure.json"
_mx_findings_dir = _mx_findings_dir_path if _mx_findings_dir_path else _BASE / "data" / "projects" / "mixer" / "findings"
_mx_findings_dir.mkdir(parents=True, exist_ok=True)

_mx_report_sections = []
if _mx_rs_path.exists():
    try:
        _mx_rs = json.loads(_mx_rs_path.read_text(encoding="utf-8"))
        _mx_report_sections = _mx_rs.get("sections", [])
    except Exception:
        pass

if not _mx_report_sections:
    st.warning("report_structure.json not found for Mixer. Expected at projects/mixer/source_docs/")
else:
    _mx_fi = _mx_findings_dir / "index.json"
    _mx_generated = {}
    if _mx_fi.exists():
        try:
            _mx_idx = json.loads(_mx_fi.read_text(encoding="utf-8"))
            _mx_generated = {s["id"]: s for s in _mx_idx.get("sections", []) if s.get("status") == "ok"}
        except Exception:
            pass
    _mx_ng = len(_mx_generated); _mx_nt = len(_mx_report_sections)

    _mxfg1, _mxfg2, _mxfg3 = st.columns([3, 1, 1])
    with _mxfg1:
        st.markdown(
            f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 14px;font-size:0.82rem;">'
            f'<b style="color:{_P["teal"]};">{_mx_ng}/{_mx_nt}</b> sections generated · 233 interviews available</div>',
            unsafe_allow_html=True,
        )
    with _mxfg2:
        if st.button("Generate All", key="gen_mx_findings", type="primary", use_container_width=True):
            import subprocess as _sp4
            _sc4 = _BASE / "skills" / "findings_generator.py"
            with st.spinner("Generating Mixer findings (~5 min)..."):
                _gr4 = _sp4.run([sys.executable, str(_sc4), "--project", _active_project],
                                capture_output=True, text=True, timeout=600,
                                cwd=str(_BASE.parent))
            if _gr4.returncode == 0:
                st.success("Done."); st.cache_data.clear(); st.rerun()
            else:
                st.error("Failed — OpenRouter may be unavailable.")
    with _mxfg3:
        if _mx_generated and st.button("Export .md", key="exp_mx_findings", use_container_width=True):
            _mx_md = [f"# Crompton FMCD — Findings Report\n*{datetime.now().strftime('%Y-%m-%d')}*\n\n---\n"]
            for _ms in _mx_report_sections:
                _mp2 = _mx_findings_dir / f"{_ms['id']}.json"
                if _mp2.exists():
                    try:
                        _md2 = json.loads(_mp2.read_text(encoding="utf-8"))
                        _mx_md.append(f"\n## {_md2.get('dg_section','')} — {_md2.get('title','')}\n\n{_md2.get('finding_text','')}\n\n---\n")
                    except Exception:
                        pass
            st.download_button("Download", data="\n".join(_mx_md).encode("utf-8"),
                               file_name=f"mixer_findings_{datetime.now().strftime('%Y%m%d')}.md",
                               mime="text/markdown", key="dl_mx_md")

    st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)
    for _mfs in _mx_report_sections:
        _mfid = _mfs["id"]
        _mfb1, _mfb2 = st.columns([5, 1])
        with _mfb1:
            st.markdown(
                f'<div style="border-left:4px solid {_P["teal"]};padding:6px 12px;margin:6px 0 4px;">'
                f'<div style="font-size:0.60rem;font-weight:700;color:{_P["teal"]};text-transform:uppercase;">{_mfs.get("dg_section","")}</div>'
                f'<div style="font-size:0.88rem;font-weight:800;color:#111827;">{_html_mod.escape(_mfs.get("title",""))}</div>'
                f'</div>', unsafe_allow_html=True)
        with _mfb2:
            _mf_gen = _mfid in _mx_generated
            if st.button("Regen" if _mf_gen else "Generate", key=f"gen_mxsec_{_mfid}", use_container_width=True):
                import subprocess as _sp5
                _sc5 = _BASE / "skills" / "findings_generator.py"
                with st.spinner(f"Generating {_mfs.get('title','')}..."):
                    _gr5 = _sp5.run([sys.executable, str(_sc5), "--project", _active_project, "--section", _mfid, "--force"],
                                    capture_output=True, text=True, timeout=120, cwd=str(_BASE.parent))
                if _gr5.returncode == 0:
                    st.success("Done."); st.cache_data.clear(); st.rerun()
                else:
                    st.error("Failed.")

        _mf_path = _mx_findings_dir / f"{_mfid}.json"
        if _mf_path.exists():
            try:
                _mfd = json.loads(_mf_path.read_text(encoding="utf-8"))
                _mft = _mfd.get("finding_text","")
                if _mft:
                    # Parse into sections — handles both 'FINDING:' and '**FINDING:**'
                    _mf_secs: dict = _parse_ai_sections(_mft, [
                        "FINDING", "SEGMENT DIFFERENCES", "KEY VERBATIMS", "STRATEGIC IMPLICATION",
                    ])

                    _mf_cols = {
                        "FINDING":               (_P["teal"],   "Core Finding"),
                        "SEGMENT DIFFERENCES":   (_P["blue"],   "By Brand / Segment"),
                        "KEY VERBATIMS":         (_P["purple"], "Key Verbatims"),
                        "STRATEGIC IMPLICATION": (_P["orange"], "Strategic Implication"),
                    }
                    for _mk2, (_mc2, _ml2) in _mf_cols.items():
                        _lines2 = _mf_secs.get(_mk2, [])
                        if not _lines2:
                            continue
                        if _mk2 == "KEY VERBATIMS":
                            # Render each verbatim as a styled quote card
                            st.markdown(
                                f'<div style="font-size:0.60rem;font-weight:800;color:{_mc2};'
                                f'text-transform:uppercase;letter-spacing:0.1em;margin:10px 0 6px;">{_ml2}</div>',
                                unsafe_allow_html=True,
                            )
                            for _vline in _lines2:
                                _vline = _vline.lstrip("•·-–— ").strip()
                                if _vline:
                                    st.markdown(
                                        f'<div style="border-left:3px solid {_mc2};padding:8px 14px;'
                                        f'background:{_mc2}08;border-radius:0 8px 8px 0;margin-bottom:6px;">'
                                        f'<span style="font-size:0.88rem;color:#374151;line-height:1.75;'
                                        f'font-family:Georgia,serif;font-style:italic;">'
                                        f'&ldquo;{_html_mod.escape(_vline)}&rdquo;</span></div>',
                                        unsafe_allow_html=True,
                                    )
                        else:
                            body2 = "<br>".join(_md_bold(_html_mod.escape(ln)) for ln in _lines2)
                            st.markdown(
                                f'<div style="border-left:3px solid {_mc2};padding:10px 14px;background:{_mc2}06;'
                                f'border-radius:0 8px 8px 0;margin-bottom:8px;">'
                                f'<div style="font-size:0.60rem;font-weight:800;color:{_mc2};text-transform:uppercase;'
                                f'letter-spacing:0.1em;margin-bottom:6px;">{_ml2}</div>'
                                f'<div style="font-size:0.88rem;color:#374151;line-height:1.80;">'
                                f'{body2}</div></div>',
                                unsafe_allow_html=True,
                            )
                    st.caption(f"Based on {_mfd.get('n_interviews',0)} interviews · {_mfd.get('generated_at','')[:10]}")

                    # Supporting data (mirrors CoinDCX distributions expander)
                    _mx_ctx = _mfd.get("context", {}) or {}
                    _mx_numeric_dists = {
                        k: {dk: dv for dk, dv in v.items() if isinstance(dv, (int, float))}
                        for k, v in (_mx_ctx.get("distributions", {}) or {}).items()
                        if isinstance(v, dict) and any(isinstance(dv, (int, float)) for dv in v.values())
                    }
                    _mx_verbatims_ctx = (_mx_ctx.get("verbatims") or [])
                    if _mx_numeric_dists or _mx_verbatims_ctx:
                        with st.expander("Supporting data — distributions & metrics", expanded=False):
                            _mxd1, _mxd2 = st.columns(2)
                            with _mxd1:
                                for _mxdk, _mxdv in list(_mx_numeric_dists.items())[:6]:
                                    _mxlbl = _mxdk.replace("_MENTION_COUNT","").replace("_"," ").title()
                                    _mxtot = max(sum(_mxdv.values()), 1)
                                    st.markdown(f'<div style="font-size:0.72rem;font-weight:700;color:#374151;margin:8px 0 4px;">{_html_mod.escape(_mxlbl)}</div>', unsafe_allow_html=True)
                                    for _mxvk, _mxvc in sorted(_mxdv.items(), key=lambda x: -x[1])[:8]:
                                        _mxvp = round(_mxvc / _mxtot * 100)
                                        st.markdown(
                                            f'<div style="font-size:0.72rem;display:flex;gap:6px;align-items:center;margin-bottom:3px;">'
                                            f'<span style="width:130px;color:#6b7280;">{_html_mod.escape(str(_mxvk))}</span>'
                                            f'<div style="flex:1;background:#f1f5f9;border-radius:2px;height:8px;">'
                                            f'<div style="width:{_mxvp}%;background:{_P["teal"]};height:100%;border-radius:2px;"></div></div>'
                                            f'<span style="font-size:0.68rem;color:#374151;font-weight:600;">{_mxvc}</span>'
                                            f'<span style="font-size:0.65rem;color:#9ca3af;">({_mxvp}%)</span></div>',
                                            unsafe_allow_html=True,
                                        )
                            with _mxd2:
                                _mx_seg_bkdn = (_mx_ctx.get("segment_breakdown") or {})
                                if _mx_seg_bkdn:
                                    st.markdown('<div style="font-size:0.72rem;font-weight:700;color:#374151;margin-bottom:6px;">By Brand (n)</div>', unsafe_allow_html=True)
                                    for _mxsbk, _mxsbv in list(_mx_seg_bkdn.items())[:10]:
                                        _mxsbn = _mxsbv.get("n", 0) if isinstance(_mxsbv, dict) else _mxsbv
                                        st.markdown(f'<div style="font-size:0.72rem;color:#6b7280;padding:2px 0;">{_html_mod.escape(str(_mxsbk))}: <b style="color:#374151;">{_mxsbn}</b></div>', unsafe_allow_html=True)
                                if _mx_verbatims_ctx:
                                    st.markdown('<div style="font-size:0.72rem;font-weight:700;color:#374151;margin:8px 0 4px;">Verified Verbatims</div>', unsafe_allow_html=True)
                                    for _mxvb in _mx_verbatims_ctx[:4]:
                                        if isinstance(_mxvb, dict) and _mxvb.get("quote"):
                                            _mxq = str(_mxvb["quote"])[:160]
                                            _mxsrc = f"{_mxvb.get('brand','?')} · {_mxvb.get('city','?')}"
                                            st.markdown(f'<div style="border-left:2px solid {_P["purple"]};padding:4px 8px;background:{_P["purple"]}06;margin-bottom:4px;"><div style="font-size:0.74rem;color:#374151;font-style:italic;">&ldquo;{_html_mod.escape(_mxq)}&rdquo;</div><div style="font-size:0.65rem;color:#9ca3af;margin-top:2px;">{_html_mod.escape(_mxsrc)}</div></div>', unsafe_allow_html=True)
                    else:
                        with st.expander("Supporting data — distributions & metrics", expanded=False):
                            st.caption("No structured supporting data available. Regenerate findings to populate.")
            except Exception as _me:
                st.warning(f"Could not load finding: {_me}")
        else:
            st.markdown(
                f'<div style="background:#f8fafc;border:1px dashed #e2e8f0;border-radius:8px;'
                f'padding:10px 14px;margin-bottom:8px;font-size:0.80rem;color:#9ca3af;">'
                f'Not yet generated. Click "Generate" above.</div>', unsafe_allow_html=True)
        st.markdown("<div style='margin:2px 0;'></div>", unsafe_allow_html=True)

st.markdown('---')

# ── Master Prompt viewer/editor ──────────────────────────────────────────
_mx_mp_path = _mx_mp_path if _mx_mp_path else _BASE / "data" / "projects" / "mixer" / "schema" / "master_prompt.txt"
with st.expander("🤖 Extraction Master Prompt — view or edit before re-extracting", expanded=False):
    try:
        _mx_mp_txt = _mx_mp_path.read_text(encoding="utf-8") if _mx_mp_path.exists() else "master_prompt.txt not found."
    except Exception:
        _mx_mp_txt = "Could not read master_prompt.txt"
    _mx_mp_edit = st.text_area("master_prompt.txt", value=_mx_mp_txt, height=400, key="mx_master_prompt_ta")
    _mxmp_c1, _mxmp_c2 = st.columns(2)
    with _mxmp_c1:
        if st.button("💾 Save Prompt", key="mx_save_mp"):
            try:
                _mx_mp_path.parent.mkdir(parents=True, exist_ok=True)
                _mx_mp_path.write_text(_mx_mp_edit, encoding="utf-8")
                st.success("Prompt saved.")
            except Exception as _mpe:
                st.error(f"Save failed: {_mpe}")
    with _mxmp_c2:
        st.caption(f"projects/mixer/schema/master_prompt.txt  ·  {_mx_mp_path.stat().st_size // 1024 if _mx_mp_path.exists() else 0} KB")

# ── Overall quality alert (outside tabs so visible on all tabs) ───────────────
_mx_all_qs_pre = [m.get("_quality_score") for m in _raw_mx_mats if m.get("_quality_score") is not None]
_mx_unver_pre  = [m.get("doc_id", "") for m in _raw_mx_mats if m.get("_quality_score") is None]
_mx_low_pre    = [m for m in _raw_mx_mats if m.get("_quality_score") is not None and m.get("_quality_score") < 60]
_mx_avg_q_pre  = round(sum(_mx_all_qs_pre) / max(len(_mx_all_qs_pre), 1), 1)

if _mx_low_pre or _mx_unver_pre:
    with st.expander(f"Data Quality: {_mx_avg_q_pre}% verbatim accuracy — {len(_mx_low_pre)} low-quality + {len(_mx_unver_pre)} unverified", expanded=False):
        if _mx_unver_pre:
            st.markdown(f"**Unverified:** {', '.join(_mx_unver_pre[:15])}")
        for _lqm in sorted(_mx_low_pre, key=lambda x: x.get("_quality_score", 0)):
            _lq_id = _lqm.get("doc_id", _lqm.get("filename", "?"))
            _lq_s  = _lqm.get("_quality_score", 0)
            st.markdown(f"- `{_lq_id}` — {_lq_s}% verbatim accuracy")

# ── Unified analysis tabs — labels from ui_config ──────────────────────────
_mx_tab4_lbl = _mx_ui.get("tab4_label", "Brand Analysis")
(_mx_tab1, _mx_tab2, _mx_tab3, _mx_tab4, _mx_tab5, _mx_tab6, _mx_tab7) = st.tabs([
    "🔍 Deep Dive",
    "⚠ Pain Points & Barriers",
    "🏷 Themes & Narratives",
    f"📊 {_mx_tab4_lbl}",
    "🔒 Health & Trust",
    "💬 Passage Search",
    "🔬 Inspector",
])

with _mx_tab1:
    # SECTION A: Brand-by-Brand Deep Dive (main feature)
    # ═══════════════════════════════════════════════════════════════════════════
    _section("Brand Deep Dive", "Select a brand to see all extracted intelligence from IDI transcripts")
    st.markdown(
        f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:8px 14px;'
        f'margin-bottom:10px;font-size:0.76rem;color:#166534;">'
        f'<b>Data source:</b> All scores, pain points, aspiration gaps, and verbatims are extracted by AI from '
        f'{total_docs} in-depth interview transcripts (2–3 hour home immersions). '
        f'Scores are computed from extracted signals, not survey questions. '
        f'Source: <code>oxdata/data/qual_matrices/</code>'
        f'</div>',
        unsafe_allow_html=True,
    )

    sel_ti_brand = st.selectbox(
        "Select brand",
        named_brands,
        key="ti_brand",
        label_visibility="collapsed",
    )
    bd = intel[sel_ti_brand]
    bc = _BRAND_PAL[brand_idx_map.get(sel_ti_brand, 0) % len(_BRAND_PAL)]

    # ── KPI strip for selected brand (fixed universal first, then project-specific) ─
    dk1, dk2, dk3, dk4 = st.columns(4)
    with dk1: kpi_card("Interviews",  str(bd.get("n_docs", 0)),                        bc)
    with dk2: kpi_card("Pain Points", str(len(bd.get("pain_points", []))),             _P["red"])
    with dk3: kpi_card("NPS Promoter",f"{bd.get('promoter_pct', 0):.0f}%",            _P["green"])
    with dk4: kpi_card("Positive End",f"{bd.get('positive_resolution_pct',0):.0f}%",  _P["teal"])
    # Project-specific KPIs (brand health study)
    blame = bd.get("blame", {}); sb_ratio = blame.get("self_ratio", 0)
    dk5, dk6, dk7 = st.columns(3)
    with dk5: _kpi(str(len(bd.get("aspiration_gaps", []))),     "Aspiration Gaps", _P["amber"])
    with dk6: _kpi(f"{sb_ratio:.0%}",                           "Self-Blame Ratio", _P["purple"])
    with dk7: _kpi(str(len(bd.get("unspoken_needs", []))),      "Unspoken Needs",  _P["blue"])

    # ── Computed signal scores (aggregated across all interviews for this brand) ─
    def _ti_brand_signals(bd_: dict) -> dict:
        n_       = max(bd_.get("n_docs", 1), 1)
        prom_    = bd_.get("promoter_pct", 0)
        pos_r_   = bd_.get("positive_resolution_pct", 0)
        rel_s_   = bd_.get("relationship_stages", {})
        n_rel_   = max(sum(rel_s_.values()), 1)
        pos_rel_ = (rel_s_.get("honeymoon",0) + rel_s_.get("settled_satisfied",0)) / n_rel_ * 100
        sat_     = min(100, round(prom_*0.40 + pos_r_*0.35 + pos_rel_*0.25))
        pains_   = bd_.get("pain_points", [])
        hi_p_    = sum(1 for p in pains_ if isinstance(p,dict) and p.get("severity")=="high") if pains_ else 0
        blame_   = bd_.get("blame", {})
        pb_      = blame_.get("product_ratio", 0) if blame_ else 0
        str_     = (rel_s_.get("strained",0) + rel_s_.get("at_risk",0)) / n_rel_ * 100
        risk_    = min(100, round(min(hi_p_,5)*8 + pb_*30 + str_*0.6))
        gaps_    = bd_.get("aspiration_gaps", [])
        hi_g_    = sum(1 for g in gaps_ if isinstance(g,dict) and g.get("opportunity")=="high") if gaps_ else 0
        unspk_   = len(bd_.get("unspoken_needs", []))
        opp_     = min(100, hi_g_*8 + unspk_*5)
        return {"satisfaction": sat_, "risk": risk_, "opportunity": opp_}

    _ti_sigs = _ti_brand_signals(bd)
    _ts1, _ts2, _ts3 = st.columns(3)
    with _ts1: signal_score_card("Brand Satisfaction", _ti_sigs["satisfaction"], True,  "At risk", "Loyal",    f"across {bd.get('n_docs',0)} interviews")
    with _ts2: signal_score_card("Brand Risk",         _ti_sigs["risk"],         False, "Safe",    "Critical", f"across {bd.get('n_docs',0)} interviews")
    with _ts3: signal_score_card("Brand Opportunity",  _ti_sigs["opportunity"],  True,  "Low",     "High",     f"across {bd.get('n_docs',0)} interviews")

    st.markdown("<div style='margin:10px 0 4px;'></div>", unsafe_allow_html=True)

    col_left, col_right = st.columns([1.3, 1])

    with col_left:
        # ── Pain Points ───────────────────────────────────────────────────────
        pain_pts = bd.get("pain_points", [])
        if pain_pts:
            _section("All Pain Points", f"{len(pain_pts)} extracted across {bd.get('n_docs',0)} interviews · sorted by severity")
            sev_color = {"critical": _P["red"], "high": _P["red"], "medium": _P["amber"], "low": "#94a3b8"}
            sev_ord_mx = {"critical":4,"high":3,"medium":2,"low":1}

            # ── Severity-by-area distribution chart (top areas × severity mix) ──
            _pp_valid = [p for p in pain_pts if isinstance(p, dict)]
            if _pp_valid:
                _area_sev: dict = {}
                for _p in _pp_valid:
                    _ar = str(_p.get("area", "other") or "other")
                    _sv = str(_p.get("severity", "medium") or "medium")
                    _area_sev.setdefault(_ar, {"critical":0,"high":0,"medium":0,"low":0})
                    _area_sev[_ar][_sv if _sv in ("critical","high","medium","low") else "medium"] += 1
                # top 8 areas by total pain count
                _top_areas = sorted(_area_sev.items(), key=lambda kv: -sum(kv[1].values()))[:8]
                _areas = [a for a, _ in reversed(_top_areas)]
                _sev_stack = {
                    "critical": ("#b91c1c", [ _area_sev[a]["critical"] for a in _areas ]),
                    "high":     (_P["red"], [ _area_sev[a]["high"]     for a in _areas ]),
                    "medium":   (_P["amber"],[ _area_sev[a]["medium"]  for a in _areas ]),
                    "low":      ("#94a3b8", [ _area_sev[a]["low"]      for a in _areas ]),
                }
                _fig_sev = go.Figure()
                for _slbl, (_scol, _svals) in _sev_stack.items():
                    _fig_sev.add_trace(go.Bar(
                        name=_slbl.title(), y=_areas, x=_svals, orientation="h",
                        marker_color=_scol,
                        hovertemplate=f"<b>%{{y}}</b><br>{_slbl.title()}: %{{x}} mentions<extra></extra>",
                    ))
                _fig_sev.update_layout(**_base_layout(
                    barmode="stack", height=max(220, len(_areas) * 34 + 80),
                    margin=dict(l=10, r=20, t=44, b=30),
                    xaxis=dict(title="Number of verbatim mentions", tickfont=dict(size=10, family=_FONT)),
                    yaxis=dict(tickfont=dict(size=10, family=_FONT), autorange="reversed", showgrid=False),
                    legend=dict(orientation="h", x=0, y=1.08, font=dict(size=10, family=_FONT)),
                    title=dict(text="Pain points by area × severity", font=dict(size=12, family=_FONT)),
                ))
                _chart_header(
                    "Where are consumers experiencing friction?",
                    subtitle=f"Each row = a product/experience area. Bar length = total verbatim mentions of a pain in that area ({len(_pp_valid)} pain points across {len(_areas)} areas).",
                    how_to_read="Read left-to-right: longer bar = more mentioned. Colour = how severe: darker red = more urgent. Hover for exact counts.",
                )
                _legend_pills([
                    ("Critical", "#b91c1c", "blocks purchase or trust — needs immediate fix"),
                    ("High",     _P["red"],   "regularly mentioned friction — high priority"),
                    ("Medium",   _P["amber"],  "occasional frustration — monitor"),
                    ("Low",      "#94a3b8",   "minor annoyance — backlog"),
                ])
                st.plotly_chart(_fig_sev, use_container_width=True, config={"displayModeBar": False})
                _total_critical_high = sum(_area_sev[a]["critical"] + _area_sev[a]["high"] for a, _ in _top_areas)
                _chart_footer(
                    f"<b>{_total_critical_high}</b> Critical/High severity pain points out of {len(_pp_valid)} total. "
                    "Focus product and communication fixes on areas with the longest dark-red segments first."
                )

            _pp_html_parts = []
            for pp in sorted(pain_pts, key=lambda x: sev_ord_mx.get(x.get("severity","low"),0), reverse=True):
                sev = pp.get("severity", "medium")
                col_ = sev_color.get(sev, "#94a3b8")
                _pp_html_parts.append(
                    f'<div style="border-left:3px solid {col_};padding:8px 12px;'
                    f'background:{col_}0a;border-radius:0 8px 8px 0;margin-bottom:8px;">'
                    f'<div style="font-size:0.7rem;font-weight:700;color:{col_};text-transform:uppercase;">'
                    f'{sev} · {pp.get("area","other")} · {pp.get("city","—")}</div>'
                    f'<div style="font-size:0.85rem;color:#374151;margin:3px 0;">{_html_mod.escape(str(pp.get("issue",""))[:160])}</div>'
                    + (f'<div style="font-size:0.78rem;color:#6b7280;font-style:italic;">'
                       f'&ldquo;{_html_mod.escape(_clean_quote(pp.get("quote",""))[:180])}&rdquo;</div>'
                       if pp.get("quote") else "")
                    + f'</div>'
                )
            # Show first 5 directly, rest in scrollable expander
            for _ppx in _pp_html_parts[:5]:
                st.markdown(_ppx, unsafe_allow_html=True)
            if len(_pp_html_parts) > 5:
                with st.expander(f"Show remaining {len(_pp_html_parts)-5} pain points", expanded=False):
                    for _ppx in _pp_html_parts[5:]:
                        st.markdown(_ppx, unsafe_allow_html=True)

        # ── Aspiration Gaps ───────────────────────────────────────────────────
        gaps = bd.get("aspiration_gaps", [])
        if gaps:
            _section("All Aspiration-Reality Gaps", f"{len(gaps)} gaps identified · high commercial opportunity first")
            # Sort: high first, then medium, then low
            _opp_ord = {"high":3,"medium":2,"low":1}
            _gaps_sorted = sorted(gaps, key=lambda g: _opp_ord.get(g.get("opportunity","low"),0), reverse=True)
            _gap_html_parts = []
            for ag in _gaps_sorted:
                opp = ag.get("opportunity", "medium")
                opp_color = _P["red"] if opp == "high" else (_P["amber"] if opp == "medium" else "#94a3b8")
                _gap_html_parts.append(
                    f'<div style="border:1px solid {opp_color}30;border-left:3px solid {opp_color};'
                    f'padding:8px 12px;border-radius:0 8px 8px 0;margin-bottom:8px;background:{opp_color}06;">'
                    f'<div style="font-size:0.7rem;font-weight:700;color:{opp_color};text-transform:uppercase;">'
                    f'{opp} opportunity · {ag.get("charge","?")} · {ag.get("city","—")}</div>'
                    f'<div style="font-size:0.85rem;color:#111827;font-weight:600;margin:3px 0;">'
                    f'{_html_mod.escape(str(ag.get("aspiration",""))[:160])}</div>'
                    + (f'<div style="font-size:0.75rem;color:#6b7280;">Current reality: {_html_mod.escape(str(ag.get("current_reality",""))[:100])}</div>' if ag.get("current_reality") else "")
                    + (f'<div style="font-size:0.75rem;color:#6b7280;">Workaround: {_html_mod.escape(str(ag.get("workaround",""))[:100])}</div>' if ag.get("workaround") else "")
                    + (f'<div style="font-size:0.74rem;color:#6b7280;font-style:italic;margin-top:2px;">'
                       f'&ldquo;{_html_mod.escape(_clean_quote(str(ag.get("verbatim_quote","") or ag.get("quote","")))[:180])}&rdquo;</div>'
                       if ag.get("verbatim_quote") or ag.get("quote") else "")
                    + f'</div>'
                )
            for _gx in _gap_html_parts[:5]:
                st.markdown(_gx, unsafe_allow_html=True)
            if len(_gap_html_parts) > 5:
                with st.expander(f"Show remaining {len(_gap_html_parts)-5} aspiration gaps", expanded=False):
                    for _gx in _gap_html_parts[5:]:
                        st.markdown(_gx, unsafe_allow_html=True)

        # ── Unspoken Needs ────────────────────────────────────────────────────
        unspoken = bd.get("unspoken_needs", [])
        if unspoken:
            with st.expander(f"Unspoken Needs — {len(unspoken)} inferred from workarounds", expanded=False):
                st.caption("Never stated directly. Inferred from what consumers do when the appliance fails them.")
                for un in unspoken:  # ALL — no limit
                    st.markdown(
                        f'<div style="padding:6px 12px;background:#fffbeb;border-left:3px solid {_P["amber"]};'
                        f'border-radius:0 6px 6px 0;margin-bottom:6px;font-size:0.84rem;color:#374151;">'
                        f'{_html_mod.escape(str(un.get("need","") if isinstance(un,dict) else str(un))[:180])}'
                        f'<span style="color:#9ca3af;font-size:0.72rem;"> · {_html_mod.escape(un.get("city","") if isinstance(un,dict) else "")}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        # ── Jobs to be done ───────────────────────────────────────────────────
        jtbd = bd.get("jobs_to_be_done", [])
        if jtbd:
            with st.expander(f"Jobs to Be Done — {len(jtbd)} extracted", expanded=False):
                st.caption("Specific tasks the appliance is hired to do — verbatim from interviews")
                for jb in jtbd:  # ALL — no limit
                    _jb_text = str(jb.get("job", jb))[:200] if isinstance(jb, dict) else str(jb)[:200]
                    _jb_city = jb.get("city", "") if isinstance(jb, dict) else ""
                    st.markdown(
                        f'<div style="padding:5px 10px;background:#f0f9ff;border-left:3px solid {_P["blue"]};'
                        f'border-radius:0 6px 6px 0;margin-bottom:5px;font-size:0.82rem;color:#1e40af;">'
                        f'→ {_html_mod.escape(_jb_text)}'
                        + (f'<span style="color:#9ca3af;font-size:0.70rem;"> · {_html_mod.escape(_jb_city)}</span>' if _jb_city else '')
                        + f'</div>', unsafe_allow_html=True)

        # ── Context strip ─────────────────────────────────────────────────────
        cities_str = ", ".join(bd.get("cities", [])) or "—"
        use_cases  = [t[0] for t in bd.get("top_use_cases", [])[:4]]
        uc_pills = "".join(
            f'<span style="background:{bc}12;color:{bc};border:1px solid {bc}30;'
            f'padding:2px 8px;border-radius:20px;font-size:0.71rem;margin:2px;display:inline-block;">'
            f'{_html_mod.escape(t)}</span>'
            for t in use_cases
        ) or "—"
        st.markdown(
            f'<div style="background:#f8fafc;border-radius:8px;padding:10px 14px;'
            f'font-size:0.79rem;line-height:1.8;margin-top:6px;">'
            f'<b style="color:{bc};">Cities:</b> {_html_mod.escape(cities_str)}<br>'
            f'<b style="color:{bc};">Primary use cases:</b><br><div style="margin-top:4px;">{uc_pills}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col_right:
        # ── Relationship & NPS distribution ───────────────────────────────────
        rel_stages = bd.get("relationship_stages", {})
        nps_dist   = bd.get("nps_signals", {})
        if rel_stages:
            _section("Brand Relationship", "How users relate to this brand")
            rel_colors = {
                "honeymoon": _P["green"], "settled_satisfied": _P["teal"],
                "resigned_acceptance": _P["amber"], "strained": _P["red"],
                "at_risk": "#dc2626", "churned_mentally": "#7f1d1d",
            }
            for stage, cnt in sorted(rel_stages.items(), key=lambda x: -x[1]):
                col_  = rel_colors.get(stage, "#94a3b8")
                pct   = round(cnt / max(sum(rel_stages.values()), 1) * 100)
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">'
                    f'<div style="width:80px;font-size:0.72rem;color:{col_};font-weight:600;">'
                    f'{stage.replace("_"," ")}</div>'
                    f'<div style="flex:1;background:#f1f5f9;border-radius:4px;height:14px;overflow:hidden;">'
                    f'<div style="width:{pct}%;background:{col_};height:100%;border-radius:4px;"></div></div>'
                    f'<div style="width:36px;text-align:right;font-size:0.72rem;color:#6b7280;">{cnt}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # ── Blame attribution ─────────────────────────────────────────────────
        blame = bd.get("blame", {})
        if blame:
            st.markdown("<div style='margin:10px 0 4px;'></div>", unsafe_allow_html=True)
            sb_r = blame.get("self_ratio", 0)
            pb_r = blame.get("product_ratio", 0)
            st.markdown(
                f'<div style="background:#f8fafc;border-radius:8px;padding:10px 14px;font-size:0.8rem;">'
                f'<div style="font-weight:700;color:#374151;margin-bottom:6px;">Blame Attribution</div>'
                f'<div style="display:flex;gap:8px;align-items:center;">'
                f'<span style="color:{_P["purple"]};font-weight:700;">{sb_r:.0%} self-blame</span>'
                f'<span style="color:#d1d5db;">|</span>'
                f'<span style="color:{_P["red"]};font-weight:700;">{pb_r:.0%} product blame</span>'
                f'</div>'
                f'<div style="font-size:0.72rem;color:#9ca3af;margin-top:4px;">'
                f'High self-blame = users fault themselves when product fails. '
                f'Brand communication problem.</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Cook identity ──────────────────────────────────────────────────────
        cook_imgs = bd.get("cook_self_images", {})
        if cook_imgs:
            st.markdown("<div style='margin:8px 0 4px;'></div>", unsafe_allow_html=True)
            _section("Cook Identity", "How respondents see themselves as cooks")
            _ci_colors = {"skilled": _P["green"], "competent": _P["teal"], "efficient": _P["blue"],
                           "creative": _P["purple"], "struggling": _P["amber"], "burdened": _P["red"], "mixed": "#94a3b8"}
            _ci_total = max(sum(cook_imgs.values()), 1)
            for _ci_k, _ci_v in sorted(cook_imgs.items(), key=lambda x: -x[1]):
                _ci_pct = round(_ci_v / _ci_total * 100)
                _ci_c = _ci_colors.get(_ci_k, "#94a3b8")
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
                    f'<div style="width:80px;font-size:0.72rem;color:{_ci_c};font-weight:600;">{_ci_k}</div>'
                    f'<div style="flex:1;background:#f1f5f9;border-radius:3px;height:10px;">'
                    f'<div style="width:{_ci_pct}%;background:{_ci_c};height:100%;border-radius:3px;"></div></div>'
                    f'<div style="width:28px;text-align:right;font-size:0.70rem;color:#6b7280;">{_ci_v}</div>'
                    f'</div>', unsafe_allow_html=True)

        # ── Top use cases ──────────────────────────────────────────────────────
        top_uc = bd.get("top_use_cases", [])
        if top_uc:
            st.markdown("<div style='margin:8px 0 4px;'></div>", unsafe_allow_html=True)
            _section("Primary Use Cases", "What they use the appliance for most")
            for _uc_k, _uc_v in top_uc[:6]:
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;padding:3px 0;'
                    f'border-bottom:1px solid #f8fafc;font-size:0.76rem;">'
                    f'<span style="color:#374151;">{_html_mod.escape(str(_uc_k).replace("_"," ").title())}</span>'
                    f'<span style="color:{_P["teal"]};font-weight:700;">{_uc_v}</span></div>',
                    unsafe_allow_html=True)

        # ── Emotional peaks ───────────────────────────────────────────────────
        peaks = bd.get("emotional_peaks", [])
        if peaks:
            with st.expander(f"❤️ Peak Emotional Moments ({len(peaks)})", expanded=False):
                emo_colors = {
                    "pride": _P["green"], "delight": _P["green"], "relief": _P["teal"],
                    "frustration": _P["red"], "resignation": _P["amber"],
                    "anxiety": _P["orange"], "surprise": _P["purple"],
                }
                for pk in peaks[:4]:
                    ec = emo_colors.get(pk.get("emotion",""), "#94a3b8")
                    st.markdown(
                        f'<div style="border-left:3px solid {ec};padding:6px 10px;'
                        f'background:{ec}0a;border-radius:0 6px 6px 0;margin-bottom:6px;">'
                        f'<div style="font-size:0.68rem;font-weight:700;color:{ec};text-transform:uppercase;">'
                        f'{pk.get("emotion","?")} · {pk.get("city","—")}</div>'
                        f'<div style="font-size:0.78rem;color:#374151;margin-top:2px;">'
                        f'{_html_mod.escape(pk.get("trigger","")[:80])}</div>'
                        + (f'<div style="font-size:0.76rem;color:#6b7280;font-style:italic;margin-top:3px;">'
                           f'"{_html_mod.escape(_clean_quote(pk.get("quote",""))[:100])}"</div>'
                           if pk.get("quote") else "")
                        + f'</div>',
                        unsafe_allow_html=True,
                    )

        # ── AI Brand Insight (cached) ─────────────────────────────────────────
        st.markdown("<div style='margin:10px 0 4px;'></div>", unsafe_allow_html=True)
        cached_insight = _load_insight_cache().get(f"{sel_ti_brand}_v4", "")
        if cached_insight:
            _render_transcript_ai(sel_ti_brand, cached_insight, bc,
                                  keys=_mx_ai_keys, icons_cfg=_mx_ai_icons)
        else:
            if st.button(f"Generate AI Insight for {sel_ti_brand}", key=f"ti_ai_{sel_ti_brand}", type="primary"):
                with st.spinner("Calling OpenRouter…"):
                    insight = _generate_brand_transcript_insight(sel_ti_brand, bd)
                if insight:
                    _render_transcript_ai(sel_ti_brand, insight, bc,
                                          keys=_mx_ai_keys, icons_cfg=_mx_ai_icons)
                    st.rerun()
                else:
                    st.warning("OpenRouter unavailable. Check API key.")

    # ── Full verbatim log — filtered by global City + Journey Stage selectors ──
    _mx_brand_vbs_all = _load_brand_passages(sel_ti_brand, _matrices_mtime())
    _mx_brand_vbs = _mx_brand_vbs_all
    if _mx_city_gf != "All":
        _mx_brand_vbs = [v for v in _mx_brand_vbs if v["city"] == _mx_city_gf]
    if _mx_stage_gf != "All":
        _mx_brand_vbs = [v for v in _mx_brand_vbs if v["journey"] == _mx_stage_gf]

    _mx_n_all  = len(_mx_brand_vbs)
    _mx_n_pos  = sum(1 for v in _mx_brand_vbs if v["sentiment"] == "positive")
    _mx_n_neg  = sum(1 for v in _mx_brand_vbs if v["sentiment"] == "negative")
    _mx_n_pain = sum(1 for v in _mx_brand_vbs if v["pain_point"])
    _mx_n_dec  = sum(1 for v in _mx_brand_vbs if v["decision_signal"])

    _vb_filter_note = ""
    if _mx_city_gf != "All" or _mx_stage_gf != "All":
        _active_parts = []
        if _mx_city_gf != "All":   _active_parts.append(f"City: {_mx_city_gf}")
        if _mx_stage_gf != "All":  _active_parts.append(f"Stage: {_mx_stage_gf}")
        _vb_filter_note = f" · Filtered: {' · '.join(_active_parts)} · {len(_mx_brand_vbs_all)} total"

    st.markdown("<div style='margin:14px 0 4px;'></div>", unsafe_allow_html=True)
    _section(
        f"Verbatim Log ({_mx_n_all} passages{_vb_filter_note})",
        f"Transcript extracts for {sel_ti_brand} · "
        f"{_mx_n_pos} positive · {_mx_n_neg} negative · "
        f"{_mx_n_pain} pain signals · {_mx_n_dec} decision signals"
        + (" ⚠ Signal scores above reflect all interviews (pre-computed per brand)" if _vb_filter_note else ""),
    )

    if not _mx_brand_vbs:
        st.info(
            f"No passages loaded for {sel_ti_brand}. "
            "Run the matrix extractor or check that `qual_matrices/` contains entries for this brand."
        )
    else:
        _mx_vb_tab_all, _mx_vb_tab_pos, _mx_vb_tab_neg, _mx_vb_tab_pain, _mx_vb_tab_dec = st.tabs([
            f"All ({_mx_n_all})",
            f"▲ Positive ({_mx_n_pos})",
            f"▼ Negative ({_mx_n_neg})",
            f"⚠ Pain ({_mx_n_pain})",
            f"→ Decision ({_mx_n_dec})",
        ])
        _MX_SENT_C = {
            "positive":   (_P["green"],  "▲"),
            "negative":   (_P["red"],    "▼"),
            "neutral":    ("#94a3b8",    "●"),
            "ambivalent": (_P["amber"],  "◆"),
        }

        def _mx_render_vb_log(vbs: list):
            if not vbs:
                st.caption("No passages in this category.")
                return
            # Group by interview
            _by_di_mx: dict = {}
            for _vb in vbs:
                _by_di_mx.setdefault(_vb["doc_id"], []).append(_vb)
            for _did_mx, _di_vbs_mx in sorted(_by_di_mx.items()):
                _dv_city_mx  = _di_vbs_mx[0]["city"]
                _dv_jour_mx  = _di_vbs_mx[0]["journey"].replace("_"," ") if _di_vbs_mx[0]["journey"] else ""
                _dv_n_mx     = len(_di_vbs_mx)
                with st.expander(
                    f"{_did_mx}  ·  {_dv_city_mx}"
                    + (f"  ·  {_dv_jour_mx}" if _dv_jour_mx else "")
                    + f"  ·  {_dv_n_mx} passage{'s' if _dv_n_mx != 1 else ''}",
                    expanded=True,
                ):
                    for _vb_mx in _di_vbs_mx:
                        _sc_mx, _si_mx = _MX_SENT_C.get(_vb_mx["sentiment"], ("#94a3b8", "●"))
                        _topic_mx = _vb_mx["topic"].replace("_", " ").title() if _vb_mx["topic"] else ""
                        _flags_mx = []
                        if _vb_mx["pain_point"]: _flags_mx.append(
                            f'<span style="background:{_P["red"]}15;color:{_P["red"]};'
                            f'border:1px solid {_P["red"]}30;padding:1px 7px;border-radius:10px;'
                            f'font-size:0.62rem;font-weight:700;">⚠ pain</span>'
                        )
                        if _vb_mx["decision_signal"]: _flags_mx.append(
                            f'<span style="background:{_P["teal"]}15;color:{_P["teal"]};'
                            f'border:1px solid {_P["teal"]}30;padding:1px 7px;border-radius:10px;'
                            f'font-size:0.62rem;font-weight:700;">→ decision</span>'
                        )
                        st.markdown(
                            f'<div style="border-left:3px solid {_sc_mx};padding:9px 13px;'
                            f'background:{_sc_mx}06;border-radius:0 8px 8px 0;margin-bottom:10px;">'
                            f'<div style="font-size:0.88rem;color:#1f2937;font-family:Georgia,serif;line-height:1.80;">'
                            f'&ldquo;{_html_mod.escape(_clean_quote(_vb_mx["content"]))}&rdquo;</div>'
                            f'<div style="font-size:0.66rem;color:#9ca3af;margin-top:6px;'
                            f'display:flex;gap:8px;align-items:center;flex-wrap:wrap;">'
                            f'<span style="color:{_sc_mx};font-weight:700;">{_si_mx} {_vb_mx["sentiment"]}</span>'
                            + (f'<span>{_html_mod.escape(_topic_mx)}</span>' if _topic_mx else "")
                            + f'<span>{_html_mod.escape(_vb_mx["city"])}</span>'
                            + ("".join(_flags_mx) if _flags_mx else "")
                            + f'</div></div>',
                            unsafe_allow_html=True,
                        )

        with _mx_vb_tab_all:  _mx_render_vb_log(_mx_brand_vbs)
        with _mx_vb_tab_pos:  _mx_render_vb_log([v for v in _mx_brand_vbs if v["sentiment"] == "positive"])
        with _mx_vb_tab_neg:  _mx_render_vb_log([v for v in _mx_brand_vbs if v["sentiment"] == "negative"])
        with _mx_vb_tab_pain: _mx_render_vb_log([v for v in _mx_brand_vbs if v["pain_point"]])
        with _mx_vb_tab_dec:  _mx_render_vb_log([v for v in _mx_brand_vbs if v["decision_signal"]])

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION B2: Interview Browser
    # ═══════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    _section(
        "Interview Browser",
        f"IDI transcripts for {sel_ti_brand} — filter by city, click a card to read the full interview",
    )

    # Words that should not be treated as part of a respondent's name
    _TI_SKIP = {
        "bajaj","butterfly","crompton","havells","maharaja","philips","prestige","usha",
        "mixer","juicer","blender","grinder","fan","heater","cooler","wh","rc","mg","mh",
        "recent","intender","user","purchaser","di","r","clean","3","1","2","4","5",
    }

    @st.cache_data(ttl=3600)
    def _get_brand_docs(brand: str) -> list:
        conn2 = sqlite3.connect(str(_IDX_DB))
        rows2 = conn2.execute(
            "SELECT DISTINCT doc_id, city FROM qual_index WHERE UPPER(brand)=UPPER(?)",
            (brand,),
        ).fetchall()
        conn2.close()
        docs = []
        for doc_id, city in rows2:
            fpath = _TREES_DIR / f"{doc_id}_tree.json"
            if not fpath.exists():
                continue
            try:
                tree = json.loads(fpath.read_text(encoding="utf-8"))
            except Exception:
                continue
            secs = tree.get("sections", [])
            total_p = sum(
                1 for s in secs for p in s.get("passages", [])
                if p.get("content","") and not p["content"].lstrip().startswith("---") and len(p["content"]) > 35
            )
            # ── Name extraction: skip brand/product/numeric tokens ──────────
            raw   = doc_id.replace("_clean","").replace("_"," ")
            parts = raw.split()
            # Drop "DI N" prefix
            if len(parts) >= 2 and parts[0].upper() in ("DI","R") and (parts[1].isdigit() or len(parts[1]) <= 2):
                parts = parts[2:]
            name_tokens = []
            for pt in parts:
                pl = pt.lower().strip("-")
                if pl in _TI_SKIP:
                    continue
                if pt.isdigit():
                    continue
                if re.match(r'^[A-Z]{1,3}$', pt):  # abbreviations like MG, WH, RC
                    continue
                if len(pt) < 2:
                    continue
                name_tokens.append(pt.capitalize())
                if len(name_tokens) >= 2:
                    break
            respondent = " ".join(name_tokens) if name_tokens else (parts[0].capitalize() if parts else "Respondent")
            # ── City: prefer index city, fall back to tree ──────────────────
            resolved_city = (city or tree.get("city","") or "").strip() or "—"
            # ── Themes across all sections ──────────────────────────────────
            all_themes = list({t for s in secs for t in s.get("themes",[])})[:4]
            docs.append({
                "doc_id":     doc_id,
                "respondent": respondent,
                "city":       resolved_city,
                "category":   tree.get("category","Unknown"),
                "word_count": tree.get("word_count", 0),
                "n_passages": total_p,
                "n_sections": len(secs),
                "themes":     all_themes,
            })
        return sorted(docs, key=lambda d: (d["city"], d["respondent"]))

    @st.cache_data(ttl=7200)
    def _get_doc_ai_summary(doc_id: str, brand: str) -> str:
        key = _get_or_key()
        if not key:
            return ""

        # Prefer matrix data (richer)
        matrix_path = _MATRICES_DIR / f"{doc_id}_matrix.json"
        passages = []
        peak_emotion = ""
        top_pain = ""
        top_gap  = ""

        if matrix_path.exists():
            try:
                m = json.loads(matrix_path.read_text(encoding="utf-8"))
                for p in m.get("all_passages", []):
                    c = p.get("content", "")
                    if c and len(c) > 40:
                        passages.append(_clean_quote(c[:300]))
                    if len(passages) >= 8: break
                pk = m.get("peak_emotional_moment", {})
                peak_emotion = f'{pk.get("emotion","")} — "{pk.get("verbatim_quote","")[:120]}"'
                pains = m.get("pain_points", [])
                if pains: top_pain = pains[0].get("issue_description","")
                gaps = m.get("aspiration_reality_gaps", [])
                if gaps: top_gap = gaps[0].get("aspiration","")
            except Exception:
                pass

        if not passages:
            fpath = _TREES_DIR / f"{doc_id}_tree.json"
            if fpath.exists():
                try:
                    tree = json.loads(fpath.read_text(encoding="utf-8"))
                    for sec in tree.get("sections", []):
                        for p in sec.get("passages", []):
                            c = p.get("content","")
                            if c and not c.lstrip().startswith("---") and len(c) > 60:
                                passages.append(_clean_quote(c[:350]))
                            if len(passages) >= 8: break
                        if len(passages) >= 8: break
                except Exception:
                    pass

        if not passages:
            return ""

        body = "\n\n".join(f"[{i+1}] {p}" for i, p in enumerate(passages))
        extras = ""
        if top_pain:    extras += f"\nKey pain point: {top_pain}"
        if top_gap:     extras += f"\nAspiration gap: {top_gap}"
        if peak_emotion: extras += f"\nPeak emotional moment: {peak_emotion}"

        prompt = (
            f"IDI transcript — consumer who uses {brand} appliances (Indian FMCD study).\n"
            f"EXCERPTS:\n{body}\n{extras}\n\n"
            f"Write exactly 3 sentences:\n"
            f"1. Which {brand} appliance does this person own and how do they use it?\n"
            f"2. Their strongest emotion about {brand} — quote one phrase.\n"
            f"3. One actionable brand insight from this interview."
        )
        return _call_openrouter_free(
            prompt,
            system="Qualitative research analyst. Specific, grounded, 3 sentences only. No hedging.",
        )

    _browser_docs = _get_brand_matrix_docs(sel_ti_brand) if _using_matrices else _get_brand_docs(sel_ti_brand)

    if not _browser_docs:
        st.info(f"No interviews indexed for {sel_ti_brand}.")
    else:
        # ── Session state ─────────────────────────────────────────────────────
        if "ti_open_doc" not in st.session_state:
            st.session_state["ti_open_doc"] = None
        if "ti_city_filter" not in st.session_state:
            st.session_state["ti_city_filter"] = "All"
        if "ti_page" not in st.session_state:
            st.session_state["ti_page"] = 0

        open_doc_id = st.session_state["ti_open_doc"]

        # ════════════════════════════════════════════════════════════════════════
        # VIEW A — Transcript detail
        # Entire transcript built as ONE HTML string → single st.markdown scrollable div
        # No Streamlit widgets inside the scroll area (avoids all rendering issues)
        # ════════════════════════════════════════════════════════════════════════
        if open_doc_id:
            open_meta = next((d for d in _browser_docs if d["doc_id"] == open_doc_id), None)
            if not open_meta:
                st.session_state["ti_open_doc"] = None
                st.rerun()
            else:
                # Try raw MD first (100% content), then matrix, then tree
                _raw_md_path    = _PROCESSED_DIR / f"{open_doc_id}.md"
                _matrix_path    = _MATRICES_DIR / f"{open_doc_id}_matrix.json"
                _tree_path      = _TREES_DIR / f"{open_doc_id}_tree.json"
                _matrix_data    = {}
                _raw_md_content = ""

                # Load matrix for metadata (pain points, peaks, etc.)
                if _matrix_path.exists():
                    try:
                        _matrix_data = json.loads(_matrix_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass

                # Load raw MD for full transcript display
                if _raw_md_path.exists():
                    try:
                        _raw_md_content = _raw_md_path.read_text(encoding="utf-8")
                        # Strip YAML frontmatter
                        _raw_md_content = re.sub(r"^---[\s\S]*?---\n", "", _raw_md_content).strip()
                    except Exception:
                        pass

                # Build sections from raw MD dialogue (best quality)
                # Fall back to matrix passages, then tree sections
                if _raw_md_content and re.search(r'^\s*[MR]\s*:', _raw_md_content, re.MULTILINE):
                    # Parse raw MD into a single section of M/R dialogue turns
                    _secs_data = [{"title": "Full Interview", "themes": [],
                                   "passages": [{"content": _raw_md_content, "_raw_md": True}]}]
                elif _matrix_data.get("all_passages"):
                    _secs_data = [{"title": "Interview Passages", "themes": [],
                                   "passages": _matrix_data["all_passages"]}]
                else:
                    _open_tree = {}
                    if _tree_path.exists():
                        try:
                            _open_tree = json.loads(_tree_path.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                    _secs_data = _open_tree.get("sections", [])

                def _sec_label(sec, idx):
                    # Raw MD section — use a descriptive label
                    if sec.get("passages") and sec["passages"][0].get("_raw_md"):
                        return "Full Transcript (Raw)"
                    themes = sec.get("themes", [])
                    if not themes:
                        topics = [
                            p.get("topic","") for p in sec.get("passages",[])
                            if p.get("topic","") and p["topic"].lower() not in ("general","unknown")
                        ]
                        themes = list(dict.fromkeys(topics))[:2]
                    return "  ·  ".join(themes[:2]) if themes else f"Part {idx+1}"

                sec_labels = [_sec_label(s, i) for i, s in enumerate(_secs_data)]

                # ── Back button ───────────────────────────────────────────────
                back_col, _ = st.columns([1, 5])
                with back_col:
                    if st.button("← Back to list", key="ti_back"):
                        st.session_state["ti_open_doc"] = None
                        st.rerun()

                # ── Header ────────────────────────────────────────────────────
                st.markdown(
                    f'<div style="background:#f8fafc;border:1.5px solid #e2e8f0;'
                    f'border-radius:12px;padding:16px 20px;margin:10px 0 14px;">'
                    f'<div style="font-size:1.1rem;font-weight:800;color:#111827;">'
                    f'👤 {_html_mod.escape(open_meta["respondent"])}</div>'
                    f'<div style="font-size:0.78rem;color:#6b7280;margin-top:4px;">'
                    f'📍 {_html_mod.escape(open_meta.get("city","—"))} &nbsp;·&nbsp; '
                    + (f'🏷 {_html_mod.escape(open_meta["category"])} &nbsp;·&nbsp; ' if open_meta.get("category") else "")
                    + f'🛣 {open_meta.get("journey","").replace("_"," ")} &nbsp;·&nbsp; '
                    f'📝 {open_meta.get("word_count",0):,} words'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

                # ── AI summary (cached) ───────────────────────────────────────
                ai_cache_key = f"aisumm_{open_doc_id}"
                if ai_cache_key not in st.session_state:
                    with st.spinner("Generating AI summary…"):
                        st.session_state[ai_cache_key] = _get_doc_ai_summary(open_doc_id, sel_ti_brand)
                ai_summ = st.session_state.get(ai_cache_key, "")

                if ai_summ:
                    sec_pills = " ".join(
                        f'<span style="display:inline-block;background:{bc}15;color:{bc};'
                        f'border:1px solid {bc}30;border-radius:16px;padding:2px 10px;'
                        f'font-size:0.7rem;font-weight:600;margin:2px 2px 2px 0;">'
                        f'§{i+1} {_html_mod.escape(sec_labels[i][:28])}</span>'
                        for i in range(len(_secs_data))
                    )
                    st.markdown(
                        f'<div style="background:linear-gradient(135deg,{bc}0c,{bc}04);'
                        f'border:1.5px solid {bc}28;border-radius:12px;'
                        f'padding:18px 22px;margin-bottom:16px;">'
                        f'<div style="font-size:0.58rem;font-weight:800;color:{bc};'
                        f'text-transform:uppercase;letter-spacing:0.14em;margin-bottom:8px;">'
                        f'✦ AI Consumer Intelligence Summary</div>'
                        f'<div style="font-size:0.9rem;color:#1f2937;line-height:1.85;'
                        f'margin-bottom:14px;">{_html_mod.escape(ai_summ)}</div>'
                        f'<div style="border-top:1px solid {bc}18;padding-top:10px;'
                        f'font-size:0.62rem;color:#94a3b8;font-weight:600;'
                        f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
                        f'Sections in this interview</div>'
                        f'<div>{sec_pills}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("AI summary unavailable — check OpenRouter key.")

                # ── Build highlight index from matrix ──────────────────────────
                # Maps 40-char prefix of each quote → (label, color, badge_icon)
                _HIGHLIGHT_IDX: list = []  # (fragment_lower, label, color, icon)
                if _matrix_data:
                    _HL_DEFS = [
                        ("pain_points",             "issue_description",  "verbatim_quote",  "⚠ Pain Point",    "#ef4444"),
                        ("aspiration_reality_gaps",  "aspiration",         "verbatim_quote",  "💡 Aspiration Gap","#f59e0b"),
                        ("self_blame_instances",     None,                 None,              "🔄 Self-Blame",    "#8b5cf6"),
                        ("product_blame_instances",  None,                 None,              "📍 Product Blame", "#ef4444"),
                    ]
                    for field, issue_key, quote_key, label, color in _HL_DEFS:
                        items = _matrix_data.get(field, [])
                        for item in items:
                            # Get the quote text
                            if isinstance(item, str):
                                q = item
                            elif quote_key:
                                q = item.get(quote_key) or item.get("issue_description","")
                            else:
                                q = ""
                            if q and len(q) > 15:
                                fragment = re.sub(r'\s+', ' ', q.strip().lower())[:60]
                                _HIGHLIGHT_IDX.append((fragment, label, color))

                    # Also peak emotional moment
                    peak = _matrix_data.get("peak_emotional_moment", {})
                    if peak.get("verbatim_quote"):
                        fragment = re.sub(r'\s+', ' ', peak["verbatim_quote"].strip().lower())[:60]
                        _HIGHLIGHT_IDX.append((fragment, f"❤ Peak: {peak.get('emotion','')}", "#06b6d4"))

                def _match_highlights(text: str) -> list:
                    """Return list of (label, color) for any highlights matching this turn."""
                    text_low = re.sub(r'\s+', ' ', text.lower())
                    matched = []
                    for fragment, label, color in _HIGHLIGHT_IDX:
                        # Use first 40 chars of fragment to match (tolerates minor transcript differences)
                        if fragment[:40] and fragment[:40] in text_low:
                            matched.append((label, color))
                    return matched

                def _render_r_turn(txt: str, bc_brand: str) -> str:
                    """Render a Respondent turn, with highlight badges if matrix quotes match."""
                    highlights = _match_highlights(txt)
                    esc_txt    = _html_mod.escape(txt)

                    if highlights:
                        # Build badge row
                        badges = "".join(
                            f'<span style="display:inline-block;background:{col};color:white;'
                            f'padding:1px 8px;border-radius:10px;font-size:0.62rem;'
                            f'font-weight:700;margin:0 3px 3px 0;">{_html_mod.escape(lbl)}</span>'
                            for lbl, col in highlights
                        )
                        border_color = highlights[0][1]  # use first match color for border
                        return (
                            f'<div style="margin:0 0 14px 20%;">'
                            f'<div style="font-size:0.58rem;color:{border_color};'
                            f'font-weight:700;text-transform:uppercase;letter-spacing:0.07em;'
                            f'margin-bottom:3px;text-align:right;">Respondent 👤</div>'
                            f'<div style="background:{border_color}10;'
                            f'border:2px solid {border_color};'
                            f'border-radius:12px 4px 12px 12px;'
                            f'padding:12px 16px;font-size:0.9rem;'
                            f'color:#1f2937;line-height:1.8;'
                            f'font-family:Georgia,serif;">{esc_txt}</div>'
                            f'<div style="margin-top:4px;text-align:right;">{badges}</div>'
                            f'</div>'
                        )
                    else:
                        return (
                            f'<div style="margin:0 0 12px 28%;">'
                            f'<div style="font-size:0.58rem;color:{bc_brand};'
                            f'font-weight:700;text-transform:uppercase;letter-spacing:0.07em;'
                            f'margin-bottom:3px;text-align:right;">Respondent 👤</div>'
                            f'<div style="background:{bc_brand}12;'
                            f'border:1.5px solid {bc_brand}25;'
                            f'border-radius:12px 4px 12px 12px;'
                            f'padding:12px 16px;font-size:0.9rem;'
                            f'color:#1f2937;line-height:1.8;'
                            f'font-family:Georgia,serif;">{esc_txt}</div></div>'
                        )

                # ── Highlights legend above transcript ─────────────────────────
                if _HIGHLIGHT_IDX:
                    legend_items = ""
                    seen_labels = set()
                    for _, label, color in _HIGHLIGHT_IDX:
                        if label not in seen_labels:
                            legend_items += (
                                f'<span style="display:inline-flex;align-items:center;gap:5px;'
                                f'background:{color}15;border:1px solid {color}40;'
                                f'border-radius:20px;padding:3px 10px;font-size:0.72rem;'
                                f'font-weight:600;color:{color};margin:2px;">'
                                f'<span style="width:8px;height:8px;border-radius:50%;'
                                f'background:{color};display:inline-block;"></span>'
                                f'{_html_mod.escape(label)}</span>'
                            )
                            seen_labels.add(label)
                    st.markdown(
                        f'<div style="background:#f8fafc;border:1px solid #e2e8f0;'
                        f'border-radius:10px;padding:10px 14px;margin-bottom:10px;">'
                        f'<div style="font-size:0.68rem;font-weight:700;color:#6b7280;'
                        f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">'
                        f'Highlighted moments ({len(_HIGHLIGHT_IDX)} extracted signals)</div>'
                        f'<div style="display:flex;flex-wrap:wrap;gap:3px;">{legend_items}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                # ── Build full transcript as ONE HTML string ───────────────────
                if _secs_data:
                    html = []
                    for i, sec in enumerate(_secs_data):
                        lbl = sec_labels[i]
                        # Section divider header (skip for raw MD single-section)
                        if not (sec.get("passages") and sec["passages"][0].get("_raw_md")):
                            html.append(
                                f'<div style="background:{bc}0a;border-left:3px solid {bc};'
                                f'border-radius:0 6px 6px 0;padding:7px 14px;margin:20px 0 12px;">'
                                f'<span style="font-size:0.6rem;font-weight:800;color:{bc};'
                                f'text-transform:uppercase;letter-spacing:0.1em;">§{i+1}</span>'
                                f'<span style="font-size:0.8rem;color:#374151;margin-left:8px;">'
                                f'{_html_mod.escape(lbl)}</span></div>'
                            )
                        valid = [
                            p for p in sec.get("passages", [])
                            if p.get("content","")
                            and not p["content"].lstrip().startswith("---")
                            and len(p.get("content","")) >= 35
                        ]
                        for pi, p in enumerate(valid):
                            clean_p = _clean_quote(p["content"])
                            if _has_dialogue(clean_p):
                                for spk, txt in _parse_dialogue(clean_p):
                                    if spk == "M":
                                        html.append(
                                            f'<div style="margin:0 28% 10px 0;">'
                                            f'<div style="font-size:0.58rem;color:#94a3b8;'
                                            f'font-weight:700;text-transform:uppercase;'
                                            f'letter-spacing:0.07em;margin-bottom:3px;">'
                                            f'🎙 Moderator</div>'
                                            f'<div style="background:#f1f5f9;'
                                            f'border-radius:4px 12px 12px 12px;'
                                            f'padding:10px 14px;font-size:0.83rem;'
                                            f'color:#475569;font-style:italic;'
                                            f'line-height:1.65;">{_html_mod.escape(txt)}</div></div>'
                                        )
                                    elif spk == "R":
                                        html.append(_render_r_turn(txt, bc))
                                    else:
                                        html.append(
                                            f'<div style="text-align:center;font-size:0.7rem;'
                                            f'color:#9ca3af;padding:2px 0 6px;'
                                            f'font-style:italic;">…{_html_mod.escape(txt)}</div>'
                                        )
                            else:
                                sl2, sc2, _ = _sentiment(clean_p)
                                bg2 = (
                                    "#f0fdf4" if sl2 == "Positive"
                                    else ("#fef2f2" if sl2 == "Negative" else "#f8fafc")
                                )
                                highlights = _match_highlights(clean_p)
                                border_col  = highlights[0][1] if highlights else sc2
                                badge_html  = "".join(
                                    f'<span style="background:{col};color:white;padding:1px 7px;'
                                    f'border-radius:10px;font-size:0.60rem;font-weight:700;'
                                    f'margin-right:4px;">{_html_mod.escape(lbl)}</span>'
                                    for lbl, col in highlights
                                )
                                html.append(
                                    f'<div style="border-left:3px solid {border_col};'
                                    f'padding:12px 16px;background:{border_col}0d;'
                                    f'border-radius:0 8px 8px 0;margin-bottom:10px;">'
                                    f'<div style="font-size:0.9rem;color:#1f2937;'
                                    f'line-height:1.85;font-family:Georgia,serif;">'
                                    f'{_html_mod.escape(clean_p)}</div>'
                                    + (f'<div style="margin-top:5px;">{badge_html}</div>' if badge_html else "")
                                    + f'</div>'
                                )
                            if pi < len(valid) - 1:
                                html.append(
                                    '<div style="border-top:1px dashed #e2e8f0;'
                                    'margin:6px 0 10px;"></div>'
                                )

                    st.markdown(
                        f'<div style="height:660px;overflow-y:auto;padding:16px 20px;'
                        f'border:1.5px solid #e2e8f0;border-radius:12px;background:#ffffff;">'
                        f'{"".join(html)}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.info("No transcript sections found.")

        # ════════════════════════════════════════════════════════════════════════
        # VIEW B — Card grid with city filter + pagination
        # ════════════════════════════════════════════════════════════════════════
        else:
            # ── City filter bar ───────────────────────────────────────────────
            cities = ["All"] + sorted({d["city"] for d in _browser_docs if d["city"] != "—"})
            sel_city = st.session_state["ti_city_filter"]
            if sel_city not in cities:
                sel_city = "All"

            st.markdown(
                '<style>.ti-city-radio [role=radiogroup]{display:flex;flex-wrap:wrap;gap:6px;}'
                '.ti-city-radio [role=radiogroup] label{background:#f1f5f9;border-radius:20px;'
                'padding:4px 14px;font-size:0.76rem;font-weight:600;cursor:pointer;'
                'border:1.5px solid transparent;}'
                '.ti-city-radio [role=radiogroup] label:has(input:checked){'
                f'background:{bc}20;border-color:{bc};color:{bc};}}'
                '</style>',
                unsafe_allow_html=True,
            )
            with st.container():
                st.markdown('<div class="ti-city-radio">', unsafe_allow_html=True)
                chosen_city = st.radio(
                    "Filter by city",
                    cities,
                    index=cities.index(sel_city) if sel_city in cities else 0,
                    horizontal=True,
                    label_visibility="collapsed",
                    key="ti_city_radio",
                )
                st.markdown('</div>', unsafe_allow_html=True)
            if chosen_city != sel_city:
                st.session_state["ti_city_filter"] = chosen_city
                st.session_state["ti_page"] = 0
                st.rerun()

            st.markdown("<div style='margin:4px 0;'></div>", unsafe_allow_html=True)

            # ── Filter + paginate ─────────────────────────────────────────────
            filtered_docs = (
                _browser_docs if sel_city == "All"
                else [d for d in _browser_docs if d["city"] == sel_city]
            )
            PAGE_SIZE = 12
            total_pages = max(1, math.ceil(len(filtered_docs) / PAGE_SIZE))
            cur_page   = min(st.session_state["ti_page"], total_pages - 1)
            page_docs  = filtered_docs[cur_page * PAGE_SIZE : (cur_page + 1) * PAGE_SIZE]

            st.caption(
                f"{len(filtered_docs)} interviews"
                + (f"  ·  page {cur_page+1}/{total_pages}" if total_pages > 1 else "")
                + (f"  ·  city: {sel_city}" if sel_city != "All" else "")
            )

            # ── Cards ─────────────────────────────────────────────────────────
            COLS = 3
            grid_cols = st.columns(COLS)
            rel_badge_colors = {
                "honeymoon": _P["green"], "settled_satisfied": _P["teal"],
                "resigned_acceptance": _P["amber"], "strained": _P["red"],
                "at_risk": "#dc2626",
            }
            for ci3, doc in enumerate(page_docs):
                with grid_cols[ci3 % COLS]:
                    city_disp = doc.get("city", "Unknown")
                    rel_stage = doc.get("relationship_stage", "")
                    rel_color = rel_badge_colors.get(rel_stage, "#94a3b8")
                    nps_color = (_P["green"] if doc.get("nps") == "promoter"
                                 else _P["red"] if doc.get("nps") == "detractor" else _P["amber"])
                    top_pain = doc.get("top_pain", "")
                    peak_emo  = doc.get("peak_emotion", "")
                    depth_label = (
                        "Deep" if doc.get("word_count",0) > 3000 else
                        ("Medium" if doc.get("word_count",0) > 1500 else "Short")
                    )
                    depth_color = _P["green"] if depth_label == "Deep" else (
                        _P["amber"] if depth_label == "Medium" else _P["blue"])
                    st.markdown(
                        f'<div style="border:1.5px solid #e2e8f0;border-left:3px solid {rel_color};'
                        f'border-radius:0 14px 14px 0;padding:12px 14px;margin-bottom:10px;background:#fafafa;">'
                        f'<div style="font-size:0.88rem;font-weight:700;color:#111827;margin-bottom:2px;">'
                        f'👤 {_html_mod.escape(doc["respondent"])}</div>'
                        f'<div style="font-size:0.72rem;color:#6b7280;margin-bottom:5px;">'
                        f'📍 {_html_mod.escape(city_disp)} &nbsp;·&nbsp; {doc.get("journey","").replace("_"," ")}</div>'
                        + (f'<div style="font-size:0.74rem;color:{_P["red"]};margin-bottom:3px;">'
                           f'⚠ {_html_mod.escape(top_pain[:70])}</div>' if top_pain else "")
                        + (f'<div style="font-size:0.72rem;color:{_P["amber"]};margin-bottom:4px;">'
                           f'💡 peak: {_html_mod.escape(peak_emo)}</div>' if peak_emo else "")
                        + f'<div style="font-size:0.65rem;color:#9ca3af;display:flex;gap:8px;flex-wrap:wrap;">'
                        f'<span style="color:{rel_color};font-weight:700;">{rel_stage.replace("_"," ")}</span>'
                        f'<span style="color:{nps_color};font-weight:700;">NPS:{doc.get("nps","?")}</span>'
                        f'<span style="color:{depth_color};">{depth_label}</span>'
                        f'<span>{doc.get("n_pain",0)} pain</span>'
                        f'<span>{doc.get("n_gaps",0)} gaps</span>'
                        f'<span>{doc.get("n_passages",0)} pass</span>'
                        f'</div></div>',
                        unsafe_allow_html=True,
                    )
                    abs_idx = cur_page * PAGE_SIZE + ci3
                    if st.button(
                        "Read full interview →",
                        key=f"ti_card_{abs_idx}_{doc['doc_id']}",
                        use_container_width=True,
                    ):
                        st.session_state["ti_open_doc"] = doc["doc_id"]
                        st.session_state.pop(f"aisumm_{doc['doc_id']}", None)
                        st.rerun()

            # ── Pagination controls ───────────────────────────────────────────
            if total_pages > 1:
                st.markdown("<div style='margin:10px 0;'></div>", unsafe_allow_html=True)
                pg_c1, pg_c2, pg_c3 = st.columns([1, 2, 1])
                with pg_c1:
                    if cur_page > 0:
                        if st.button("← Prev", key="ti_prev", use_container_width=True):
                            st.session_state["ti_page"] = cur_page - 1
                            st.rerun()
                with pg_c2:
                    st.markdown(
                        f'<div style="text-align:center;font-size:0.8rem;color:#6b7280;'
                        f'padding-top:8px;">Page {cur_page+1} of {total_pages}</div>',
                        unsafe_allow_html=True,
                    )
                with pg_c3:
                    if cur_page < total_pages - 1:
                        if st.button("Next →", key="ti_next", use_container_width=True):
                            st.session_state["ti_page"] = cur_page + 1
                            st.rerun()

    # ═══════════════════════════════════════════════════════════════════════════

    # ── Individual Interview Analysis (per-transcript) ─────────────────────
    st.markdown("---")
    _section("📋 Individual Interview Analysis",
              "Per-transcript: all extracted signals + full transcript + re-extraction with editable prompt")

    # Filter matrices by selected brand (and global filters)
    _mx_brand_for_ia = st.session_state.get("mx_brand_gf", "All")
    _ia_mats = [m for m in _raw_mx_mats
                if _mx_brand_for_ia == "All" or
                (m.get("respondent") or {}).get("brand_owned","") == _mx_brand_for_ia]
    if not _ia_mats:
        st.info("No interviews for selected brand. Change filter above.")
    else:
        # Interview grid
        if "mx_open_doc" not in st.session_state:
            st.session_state["mx_open_doc"] = None
        _ia_open = st.session_state["mx_open_doc"]

        if _ia_open:
            # ── Full interview detail view ─────────────────────────────────
            _ia_m = next((m for m in _raw_mx_mats if m.get("_source_file") == _ia_open or m.get("doc_id") == _ia_open), None)
            if not _ia_m:
                st.session_state["mx_open_doc"] = None; st.rerun()
            else:
                _ia_bc, _ = st.columns([1, 5])
                with _ia_bc:
                    if st.button("← Back", key="mx_back_ia"):
                        st.session_state["mx_open_doc"] = None; st.rerun()

                _ia_resp = _ia_m.get("respondent", {}) or {}
                _ia_brand = (_ia_resp.get("brand_owned") or "?"); _ia_city = (_ia_resp.get("city") or "?")
                _ia_stage = (_ia_resp.get("journey_stage") or "?"); _ia_ctx = (_ia_resp.get("usage_context") or "?")
                _ia_bc2 = _BRAND_PAL[named_brands.index(_ia_brand) % len(_BRAND_PAL)] if _ia_brand in named_brands else _P["teal"]
                _ia_nps = _ia_m.get("nps_signal","")
                _ia_br = (_ia_m.get("brand_relationship") or {})
                _ia_rel = _ia_br.get("relationship_stage","")
                _ia_emo = _ia_m.get("emotional_resolution","")
                _ia_fname = _ia_m.get("filename", _ia_m.get("_source_file",""))

                # Header card
                st.markdown(
                    f'<div style="background:#f8fafc;border:1.5px solid #e2e8f0;border-left:4px solid {_ia_bc2};'
                    f'border-radius:0 12px 12px 0;padding:14px 18px;margin-bottom:12px;">'
                    f'<div style="font-size:1rem;font-weight:800;color:#111827;">'
                    f'{_html_mod.escape(_ia_brand)} '
                    f'<span style="font-size:0.74rem;font-weight:600;color:#6b7280;">·  {_html_mod.escape(_ia_city)}</span>'
                    f'</div>'
                    f'<div style="font-size:0.70rem;color:#9ca3af;margin-top:6px;display:flex;gap:14px;flex-wrap:wrap;">'
                    f'<span>Stage: <b style="color:#374151;">{_html_mod.escape(_ia_stage)}</b></span>'
                    f'<span>Context: <b style="color:#374151;">{_html_mod.escape(_ia_ctx)}</b></span>'
                    f'<span>NPS: <b style="color:{_P["green"] if _ia_nps=="promoter" else (_P["red"] if _ia_nps=="detractor" else "#9ca3af")};">{_ia_nps}</b></span>'
                    f'<span>Relationship: <b style="color:#374151;">{_html_mod.escape(_ia_rel)}</b></span>'
                    f'<span>Emotional arc: <b style="color:#374151;">{_html_mod.escape(_ia_emo)}</b></span>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

                # Two-column detail
                _ia_col_l, _ia_col_r = st.columns([1.2, 1])
                with _ia_col_l:
                    # Pain points
                    _ia_pps = [p for p in (_ia_m.get("pain_points") or []) if isinstance(p,dict)]
                    if _ia_pps:
                        _section(f"Pain Points ({len(_ia_pps)})", "sorted by severity")
                        _ia_sev = {"critical":4,"high":3,"medium":2,"low":1}
                        for _iap in sorted(_ia_pps, key=lambda x: _ia_sev.get(x.get("severity","low"),0), reverse=True):
                            _iasvc = _P["red"] if _iap.get("severity") in ("high","critical") else (_P["amber"] if _iap.get("severity")=="medium" else "#94a3b8")
                            _iaissue = str(_iap.get("issue_description",_iap.get("issue",""))[:140])
                            _iaq = str(_iap.get("verbatim_quote",_iap.get("quote",""))[:180])
                            st.markdown(
                                f'<div style="border-left:3px solid {_iasvc};padding:6px 10px;'
                                f'background:{_iasvc}0a;border-radius:0 6px 6px 0;margin-bottom:6px;">'
                                f'<div style="font-size:0.68rem;font-weight:700;color:{_iasvc};text-transform:uppercase;">'
                                f'{_iap.get("severity","")} · {_iap.get("product_area",_iap.get("area",""))}</div>'
                                f'<div style="font-size:0.82rem;color:#374151;">{_html_mod.escape(_iaissue)}</div>'
                                + (f'<div style="font-size:0.74rem;color:#6b7280;font-style:italic;margin-top:2px;">'
                                   f'&ldquo;{_html_mod.escape(_iaq)}&rdquo;</div>' if _iaq else "")
                                + f'</div>',
                                unsafe_allow_html=True,
                            )
                    # Aspiration gaps
                    _ia_gaps = [g for g in (_ia_m.get("aspiration_reality_gaps") or []) if isinstance(g,dict)]
                    if _ia_gaps:
                        _section(f"Aspiration Gaps ({len(_ia_gaps)})", "high opportunity first")
                        for _iag in sorted(_ia_gaps, key=lambda x: {"high":3,"medium":2,"low":1}.get(x.get("commercial_opportunity",x.get("opportunity","low")),0), reverse=True)[:4]:
                            _iaopp = _iag.get("commercial_opportunity", _iag.get("opportunity","medium"))
                            _iaopc = _P["red"] if _iaopp=="high" else (_P["amber"] if _iaopp=="medium" else "#94a3b8")
                            st.markdown(
                                f'<div style="border-left:3px solid {_iaopc};padding:6px 10px;'
                                f'background:{_iaopc}06;border-radius:0 6px 6px 0;margin-bottom:6px;">'
                                f'<div style="font-size:0.68rem;font-weight:700;color:{_iaopc};text-transform:uppercase;">{_iaopp} opportunity</div>'
                                f'<div style="font-size:0.82rem;color:#111827;font-weight:600;">{_html_mod.escape(str(_iag.get("aspiration",""))[:140])}</div>'
                                + (f'<div style="font-size:0.72rem;color:#6b7280;">Workaround: {_html_mod.escape(str(_iag.get("workaround",""))[:100])}</div>' if _iag.get("workaround") else "")
                                + f'</div>',
                                unsafe_allow_html=True,
                            )
                    # Jobs to be done
                    _ia_jtbd = _ia_m.get("jobs_to_be_done") or []
                    if _ia_jtbd:
                        with st.expander(f"Jobs to Be Done ({len(_ia_jtbd)})", expanded=False):
                            for _iajb in _ia_jtbd[:8]:
                                _iajt = str(_iajb.get("job",_iajb))[:160] if isinstance(_iajb,dict) else str(_iajb)[:160]
                                st.markdown(f'<div style="font-size:0.82rem;color:#374151;padding:3px 0;border-bottom:1px dashed #f1f5f9;">→ {_html_mod.escape(_iajt)}</div>', unsafe_allow_html=True)

                with _ia_col_r:
                    # All extracted dimensions as collapsible
                    _ia_dims = {
                        "Brand Relationship":   _ia_m.get("brand_relationship") or {},
                        "Identity Signals":     _ia_m.get("identity_signals") or {},
                        "Cultural Context":     _ia_m.get("cultural_context") or {},
                        "Emotional Arc":        _ia_m.get("emotional_arc") or {},
                        "Decision Drivers":     _ia_m.get("decision_drivers") or [],
                        "Feature Priorities":   _ia_m.get("feature_priorities") or [],
                        "Language Patterns":    _ia_m.get("language_patterns") or {},
                    }
                    for _dname, _dval in _ia_dims.items():
                        if not _dval: continue
                        with st.expander(_dname, expanded=False):
                            if isinstance(_dval, dict):
                                for _dk2, _dv2 in _dval.items():
                                    if _dv2 is not None and _dv2 != []:
                                        st.markdown(f'<div style="display:flex;gap:10px;padding:3px 0;border-bottom:1px solid #f8fafc;"><div style="width:130px;font-size:0.70rem;color:#9ca3af;">{_html_mod.escape(_dk2.replace("_"," "))}</div><div style="font-size:0.76rem;font-weight:600;color:#374151;">{_html_mod.escape(str(_dv2)[:120])}</div></div>', unsafe_allow_html=True)
                            elif isinstance(_dval, list):
                                for _di2 in _dval[:6]:
                                    if isinstance(_di2, dict):
                                        _di2_txt = " · ".join(f"{k}: {str(v)[:60]}" for k,v in _di2.items() if v)
                                        st.markdown(f'<div style="font-size:0.76rem;color:#374151;padding:3px 0;border-bottom:1px dashed #f1f5f9;">{_html_mod.escape(_di2_txt[:200])}</div>', unsafe_allow_html=True)
                                    else:
                                        st.markdown(f'<div style="font-size:0.76rem;color:#374151;padding:3px 0;">· {_html_mod.escape(str(_di2)[:160])}</div>', unsafe_allow_html=True)

                    # Peak emotional moment
                    _ia_peak = _ia_m.get("peak_emotional_moment") or {}
                    if _ia_peak and isinstance(_ia_peak, dict):
                        st.markdown(
                            f'<div style="background:#fef3c7;border-left:3px solid {_P["amber"]};'
                            f'border-radius:0 8px 8px 0;padding:10px 12px;margin-top:8px;">'
                            f'<div style="font-size:0.68rem;font-weight:700;color:{_P["amber"]};text-transform:uppercase;margin-bottom:4px;">Peak Emotional Moment</div>'
                            f'<div style="font-size:0.82rem;color:#374151;">{_html_mod.escape(str(_ia_peak.get("emotion","")))}</div>'
                            f'<div style="font-size:0.74rem;color:#6b7280;margin-top:2px;">{_html_mod.escape(str(_ia_peak.get("trigger",""))[:120])}</div>'
                            + (f'<div style="font-size:0.74rem;color:#78350f;font-style:italic;margin-top:4px;">&ldquo;{_html_mod.escape(str(_ia_peak.get("verbatim_quote",""))[:200])}&rdquo;</div>' if _ia_peak.get("verbatim_quote") else "")
                            + f'</div>',
                            unsafe_allow_html=True,
                        )

                # ── Re-extraction with editable prompt ─────────────────────
                st.markdown("---")
                _section("🔄 Re-extract This Interview", "Edit the master prompt then re-run extraction to regenerate this matrix")
                _ia_mp_path = _mx_mp_path if _mx_mp_path else _BASE / "data" / "projects" / "mixer" / "schema" / "master_prompt.txt"
                _ia_mp_curr = _ia_mp_path.read_text(encoding="utf-8") if _ia_mp_path.exists() else "master_prompt.txt not found."
                _ia_prompt_edit = st.text_area("Extraction prompt ({{TRANSCRIPT}} will be replaced)", value=_ia_mp_curr, height=200, key=f"ia_prompt_{_ia_open}")
                _ia_r1, _ia_r2, _ia_r3 = st.columns([1, 1, 2])
                with _ia_r1:
                    if st.button("💾 Save prompt", key="ia_save_prompt"):
                        try:
                            _ia_mp_path.parent.mkdir(parents=True, exist_ok=True)
                            _ia_mp_path.write_text(_ia_prompt_edit, encoding="utf-8")
                            st.success("Prompt saved.")
                        except Exception as _iape: st.error(str(_iape))
                with _ia_r2:
                    _ia_force_btn = st.button("▶ Re-extract Now", type="primary", key="ia_reextract")
                    if _ia_force_btn:
                        _ia_script = _BASE.parent / "oxdata" / "skills" / "project_extractor.py"
                        if not _ia_script.exists():
                            _ia_script = _BASE / "skills" / "project_extractor.py"
                        if _ia_fname and _ia_script.exists():
                            with st.spinner(f"Re-extracting {_ia_fname}…"):
                                import subprocess as _iasub
                                _ia_res = _iasub.run(
                                    [sys.executable, str(_ia_script), "--project", _active_project,
                                     "--file", _ia_fname, "--force"],
                                    capture_output=True, text=True, timeout=300,
                                    cwd=str(_BASE.parent),
                                )
                            if _ia_res.returncode == 0:
                                st.success("Re-extracted. Refresh page to see updated data.")
                                st.cache_data.clear()
                            else:
                                st.error("Extraction failed.")
                                st.code(_ia_res.stderr[-600:] if _ia_res.stderr else "No stderr")
                        else:
                            st.warning("Extractor script not found or no filename available.")
                with _ia_r3:
                    st.caption(f"Source file: {_ia_fname}")

                # ── Full transcript viewer ─────────────────────────────────
                _ia_tp = None
                _ia_trans_dir = _BASE / "data" / "projects" / "mixer" / "transcripts"
                if _ia_fname:
                    _ia_clean_name = _ia_fname.replace("_matrix.json","").replace("_clean","")
                    for _ext in [".md",".txt"]:
                        _tp_cand = _ia_trans_dir / f"{_ia_clean_name}_clean{_ext}"
                        if not _tp_cand.exists():
                            _tp_cand = _ia_trans_dir / f"{_ia_clean_name}{_ext}"
                        if _tp_cand.exists():
                            _ia_tp = _tp_cand; break
                    if not _ia_tp:
                        for _tf in _ia_trans_dir.glob("*.md"):
                            if _ia_clean_name[:20] in _tf.stem:
                                _ia_tp = _tf; break

                if _ia_tp and _ia_tp.exists():
                    with st.expander("📄 Full Transcript", expanded=False):
                        try:
                            _ia_raw = _ia_tp.read_text(encoding="utf-8", errors="replace")
                            _ia_raw = re.sub(r"^---[\s\S]*?---\n","",_ia_raw).strip()
                            _ia_html_lines = []
                            for _ia_line in _ia_raw.split("\n"):
                                _ia_line = _ia_line.strip()
                                if not _ia_line: continue
                                if _ia_line.startswith("M:") or _ia_line.startswith("MODERATOR:"):
                                    _ia_html_lines.append(f'<div style="margin:0 25% 6px 0;"><div style="font-size:0.57rem;color:#94a3b8;font-weight:700;text-transform:uppercase;margin-bottom:2px;">Moderator</div><div style="background:#f1f5f9;border-radius:4px 12px 12px 12px;padding:8px 12px;font-size:0.82rem;color:#475569;font-style:italic;">{_html_mod.escape(_ia_line[2:].strip())}</div></div>')
                                elif _ia_line.startswith("R:") or _ia_line.startswith("RESPONDENT:"):
                                    _ia_html_lines.append(f'<div style="margin:0 0 8px 20%;"><div style="font-size:0.57rem;color:{_ia_bc2};font-weight:700;text-transform:uppercase;text-align:right;margin-bottom:2px;">Respondent</div><div style="background:{_ia_bc2}10;border:1px solid {_ia_bc2}30;border-radius:12px 4px 12px 12px;padding:9px 13px;font-size:0.86rem;color:#1f2937;line-height:1.75;font-family:Georgia,serif;">{_html_mod.escape(_ia_line[2:].strip())}</div></div>')
                                else:
                                    _ia_html_lines.append(f'<div style="padding:4px 0;font-size:0.84rem;color:#374151;border-bottom:1px dashed #f1f5f9;line-height:1.65;">{_html_mod.escape(_ia_line)}</div>')
                            st.markdown(f'<div style="height:600px;overflow-y:auto;padding:14px;border:1.5px solid #e2e8f0;border-radius:12px;background:#fff;">{"".join(_ia_html_lines)}</div>', unsafe_allow_html=True)
                        except Exception as _iate:
                            st.warning(f"Could not read transcript: {_iate}")
                else:
                    st.caption(f"Transcript file not found in projects/mixer/transcripts/")

        else:
            # ── Interview card grid ────────────────────────────────────────
            _ia_city_f = st.session_state.get("mx_city_gf","All")
            _ia_cols = st.columns(3)
            _ia_shown = 0
            for _ia_ci, _ia_mat in enumerate(sorted(
                _ia_mats,
                key=lambda m: (
                    (m.get("respondent") or {}).get("brand_owned") or "",
                    (m.get("respondent") or {}).get("city") or "",
                )
            )[:60]):
                _ia_r = (_ia_mat.get("respondent") or {})
                _ia_b = (_ia_r.get("brand_owned") or "?"); _ia_c = (_ia_r.get("city") or "?")
                _ia_st = (_ia_r.get("journey_stage") or "?")
                _ia_nps_s = _ia_mat.get("nps_signal","")
                _ia_nps_col = _P["green"] if _ia_nps_s=="promoter" else (_P["red"] if _ia_nps_s=="detractor" else "#9ca3af")
                _ia_ppc = len([p for p in (_ia_mat.get("pain_points") or []) if isinstance(p,dict)])
                _ia_gapc = len([g for g in (_ia_mat.get("aspiration_reality_gaps") or []) if isinstance(g,dict)])
                _ia_rel_s = ((_ia_mat.get("brand_relationship") or {}).get("relationship_stage") or "")
                _ia_bc3 = _BRAND_PAL[named_brands.index(_ia_b) % len(_BRAND_PAL)] if _ia_b in named_brands else _P["teal"]
                _ia_peak_e = ((_ia_mat.get("peak_emotional_moment") or {}).get("emotion") or "")
                _ia_depth = "Deep" if _ia_ppc >= 3 else ("Medium" if _ia_ppc >= 1 else "Light")
                _ia_depth_c = _P["teal"] if _ia_depth=="Deep" else (_P["amber"] if _ia_depth=="Medium" else "#9ca3af")
                _ia_doc_id = _ia_mat.get("doc_id", _ia_mat.get("_source_file",""))

                with _ia_cols[_ia_ci % 3]:
                    st.markdown(
                        f'<div style="border:1.5px solid #e2e8f0;border-left:3px solid {_ia_bc3};'
                        f'border-radius:0 12px 12px 0;padding:10px 12px;margin-bottom:8px;background:#fafafa;">'
                        f'<div style="font-size:0.84rem;font-weight:700;color:#111827;">👤 {_html_mod.escape((_ia_r.get("city") or "?"))}</div>'
                        f'<div style="font-size:0.68rem;color:{_ia_bc3};font-weight:600;margin-top:1px;">📍 {_html_mod.escape(_ia_st.replace("_"," "))}</div>'
                        f'<div style="font-size:0.62rem;color:#9ca3af;margin-top:5px;display:flex;gap:8px;flex-wrap:wrap;">'
                        f'<span style="color:{_ia_nps_col};font-weight:700;">NPS:{_ia_nps_s}</span>'
                        f'<span style="color:{_ia_depth_c};">{_ia_depth}</span>'
                        f'<span>{_ia_ppc} pain</span>'
                        f'<span>{_ia_gapc} gaps</span>'
                        + (f'<span style="color:{_P["amber"]};">peak:{_html_mod.escape(_ia_peak_e)}</span>' if _ia_peak_e else "")
                        + (f'<span style="color:#6b7280;">{_html_mod.escape(_ia_rel_s.replace("_"," "))}</span>' if _ia_rel_s else "")
                        + f'</div></div>',
                        unsafe_allow_html=True,
                    )
                    if st.button("Deep Dive →", key=f"ia_open_{_ia_ci}_{_ia_doc_id}", use_container_width=True):
                        st.session_state["mx_open_doc"] = _ia_doc_id; st.rerun()
                _ia_shown += 1

            if len(_ia_mats) > 60:
                st.caption(f"Showing 60 of {len(_ia_mats)} interviews. Use filters to narrow.")

    # ═══════════════════════════════════════════════════════════════════════════

with _mx_tab2:
    st.caption(_mx_tab_desc.get("Pain Points & Barriers", f"Pain points extracted from {total_docs} IDI interviews · severity: critical/high/medium/low"))
    # ── Aggregated pain points across all brands ────────────────────────────
    _all_pp_mx = [pp for b in named_brands for pp in intel[b].get('pain_points',[]) if isinstance(pp,dict)]
    _section('Pain Points & Barriers', f"{len(_all_pp_mx)} extracted across {total_docs} interviews · all brands")
    if not _all_pp_mx:
        st.info('No pain points extracted yet.')
    else:
        _sv_mx={}; _area_mx={}
        for pp in _all_pp_mx:
            _s=pp.get('severity','medium'); _a=pp.get('area',pp.get('product_area','other'))
            _sv_mx[_s]=_sv_mx.get(_s,0)+1; _area_mx[_a]=_area_mx.get(_a,0)+1
        _sev_c_mx={'critical':_P['red'],'high':_P['red'],'medium':_P['amber'],'low':'#94a3b8'}
        _pmc1,_pmc2=st.columns(2)
        with _pmc1:
            _section('By Severity','')
            for _sk in ['critical','high','medium','low']:
                _sv=_sv_mx.get(_sk,0)
                if _sv==0: continue
                _sp=round(_sv/max(len(_all_pp_mx),1)*100); _sc=_sev_c_mx.get(_sk,'#94a3b8')
                st.markdown(f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"><div style="width:60px;font-size:0.74rem;color:{_sc};font-weight:700;">{_sk}</div><div style="flex:1;background:#f1f5f9;border-radius:4px;height:14px;"><div style="width:{_sp}%;background:{_sc};height:100%;border-radius:4px;"></div></div><div style="width:28px;font-size:0.72rem;color:#6b7280;text-align:right;">{_sv}</div></div>', unsafe_allow_html=True)
        with _pmc2:
            _section('By Product Area','')
            for _ak,_av in sorted(_area_mx.items(),key=lambda x:-x[1])[:8]:
                _ap=round(_av/max(len(_all_pp_mx),1)*100)
                st.markdown(f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;"><div style="flex:1;font-size:0.73rem;color:#374151;">{_html_mod.escape(_ak.replace("_"," ").title())}</div><div style="width:80px;background:#f1f5f9;border-radius:3px;height:10px;"><div style="width:{_ap}%;background:{_P["teal"]};height:100%;border-radius:3px;"></div></div><div style="font-size:0.70rem;color:#6b7280;width:22px;text-align:right;">{_av}</div></div>', unsafe_allow_html=True)
        st.markdown('---')
        _section('All Pain Points', 'Critical and high shown directly — medium/low collapsed')
        _sev_ord_mx = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}
        for _pp_mx in sorted(_all_pp_mx, key=lambda x: -_sev_ord_mx.get(x.get('severity','low'), 0)):
            _sv_px = _pp_mx.get('severity', 'medium'); _sc_px = _sev_c_mx.get(_sv_px, '#94a3b8')
            _issue_mx = str(_pp_mx.get('issue_description', _pp_mx.get('issue', '')))
            _vq_mx = _pp_mx.get('verbatim_quote', _pp_mx.get('quote', ''))
            _area_mx2 = _pp_mx.get('area', _pp_mx.get('product_area', ''))
            _city_mx2 = _pp_mx.get('city', '')
            _pp_html_mx = (
                f'<div style="border-left:3px solid {_sc_px};padding:10px 14px;background:{_sc_px}0a;border-radius:0 10px 10px 0;margin-bottom:8px;">'
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
                f'<span style="font-size:0.6rem;font-weight:900;background:{_sc_px};color:white;padding:2px 8px;border-radius:12px;text-transform:uppercase;">{_sv_px}</span>'
                f'<span style="font-size:0.65rem;color:#9ca3af;">{_html_mod.escape(_area_mx2.replace("_"," "))} · {_html_mod.escape(_city_mx2)}</span></div>'
                f'<div style="font-size:0.88rem;font-weight:700;color:#111827;">{_html_mod.escape(_issue_mx[:200])}</div>'
                + (f'<div style="font-size:0.82rem;color:#374151;font-family:Georgia,serif;font-style:italic;margin-top:6px;border-left:2px solid {_sc_px}40;padding-left:8px;">&ldquo;{_html_mod.escape(str(_vq_mx)[:250])}&rdquo;</div>' if _vq_mx else '')
                + f'</div>'
            )
            if _sv_px in ('critical', 'high'):
                st.markdown(_pp_html_mx, unsafe_allow_html=True)
            else:
                with st.expander(f'{_sv_px.upper()} · {_issue_mx[:70]}', expanded=False):
                    st.markdown(_pp_html_mx, unsafe_allow_html=True)

with _mx_tab3:
    st.caption(_mx_tab_desc.get("Themes & Narratives", f"Themes and narratives from {total_docs} IDI interviews"))
    _section('Themes & Narratives', f'Usage patterns, product needs, cook identity, purchase drivers — {total_docs} interviews')

    # ── Build themes from matrix intel fields (top_themes not available in matrix mode) ──
    _usage_themes: dict = {}
    _feature_themes: dict = {}
    _pain_themes: dict = {}
    _cook_themes: dict = {}
    _decision_themes: dict = {}

    for b in named_brands:
        bd_t = intel[b]
        # Usage occasions
        for uc, cnt in bd_t.get('top_use_cases', []):
            if uc: _usage_themes[uc] = _usage_themes.get(uc, 0) + cnt
        # Feature priorities
        for ft, cnt in bd_t.get('top_features', []):
            if ft: _feature_themes[ft] = _feature_themes.get(ft, 0) + cnt
        # Pain areas (built from universal Layer 1)
        for pp in bd_t.get('pain_points', []):
            if isinstance(pp, dict):
                area = pp.get('area', pp.get('product_area', ''))
                if area: _pain_themes[area] = _pain_themes.get(area, 0) + 1
        # Cook self-images
        for img, cnt in bd_t.get('cook_self_images', {}).items():
            if img and img not in ('unknown', ''): _cook_themes[img] = _cook_themes.get(img, 0) + cnt
        # Decision sources
        for src, cnt in bd_t.get('decision_sources', {}).items():
            if src: _decision_themes[src] = _decision_themes.get(src, 0) + cnt

    # ── Also try top_themes from tree-based intel (if tree mode fallback) ──
    _tree_themes: dict = {}
    for b in named_brands:
        for t, c in intel[b].get('top_themes', []):
            if t: _tree_themes[t] = _tree_themes.get(t, 0) + c

    # Display in sections
    _th_c1, _th_c2 = st.columns(2)

    def _theme_bar(themes_dict, title, color, col, max_show=10):
        if not themes_dict: return
        with col:
            _section(title, '')
            total_t = max(sum(themes_dict.values()), 1)
            for k, v in sorted(themes_dict.items(), key=lambda x: -x[1])[:max_show]:
                pct = round(v / total_t * 100)
                st.markdown(
                    f'<div style="margin-bottom:5px;">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:1px;">'
                    f'<span style="font-size:0.74rem;color:#374151;">{_html_mod.escape(str(k).replace("_"," ").title())}</span>'
                    f'<span style="font-size:0.70rem;color:{color};font-weight:700;">{v}</span></div>'
                    f'<div style="background:#f1f5f9;border-radius:3px;height:5px;">'
                    f'<div style="width:{min(pct*2,100)}%;background:{color};height:100%;border-radius:3px;"></div></div>'
                    f'</div>', unsafe_allow_html=True)

    _theme_bar(_usage_themes, "Usage Occasions", _P["teal"], _th_c1)
    _theme_bar(_feature_themes, "Feature Priorities", _P["blue"], _th_c2)
    _theme_bar(_pain_themes, "Pain Areas", _P["red"], _th_c1)
    _theme_bar(_cook_themes, "Cook Identity", _P["purple"], _th_c2)
    _theme_bar(_decision_themes, "Decision Drivers", _P["orange"], _th_c1)
    if _tree_themes:
        _theme_bar(_tree_themes, "IDI Section Themes", _P["green"], _th_c2, max_show=12)

    if not any([_usage_themes, _feature_themes, _pain_themes]):
        st.info("No theme data in matrices. Matrix pipeline may not be complete.")

with _mx_tab4:
    st.caption(_mx_tab_desc.get(_mx_tab4_lbl, f"Cross-brand analysis from {total_docs} IDI interviews"))

    fig_ts = _chart_transcript_sentiment_bar(intel)
    if fig_ts:
        _chart_header(
            "How did each brand's interview end — positive or negative?",
            subtitle=(
                "Bars show the emotional arc across IDI interviews for each brand. "
                f"Based on AI-extracted 'emotional_resolution' field from {total_docs} interview matrices. "
                "Brands with fewer than 2 interviews excluded."
            ),
            how_to_read=(
                "Green = % of interviews that ended on a positive note (respondent expressed satisfaction, intent, or advocacy). "
                "Red = % that ended negatively (frustration, distrust, or rejection). "
                "Remaining % is neutral/ambivalent. Longer green bar = stronger brand sentiment."
            ),
        )
        _legend_pills([
            ("Positive resolution", _P["green"],  "interview ended with satisfaction, advocacy, or intent to buy"),
            ("Negative / detractor", _P["red"],   "frustration, distrust, or rejection expressed at close"),
        ])
        st.plotly_chart(fig_ts, use_container_width=True, config={"displayModeBar": False})
        _chart_footer(
            "Positive resolution % is derived from the AI's emotional arc extraction — not a self-reported score. "
            "It captures whether the respondent's overall stance shifted positively through the interview."
        )

    st.markdown("<div style='margin:14px 0;'></div>", unsafe_allow_html=True)

    # NPS promoter % bar (matrix) or loyalty_pct (legacy)
    def _get_promoter_pct(b):
        bd = intel[b]
        if "promoter_pct" in bd:
            return bd["promoter_pct"]
        return bd.get("loyalty_pct", 0)

    loyal_brands = sorted(named_brands, key=_get_promoter_pct, reverse=True)
    promoter_vals = [_get_promoter_pct(b) for b in loyal_brands]
    metric_label = "NPS Promoter %" if _using_matrices else "Loyalty Signal %"
    _is_matrix_nps = _using_matrices

    _chart_header(
        f"{'NPS Promoter Rate' if _is_matrix_nps else 'Loyalty Signal'} — Which brands have the strongest advocates?",
        subtitle=(
            "NPS (Net Promoter Score) classifies respondents as Promoters (score 9–10), Passives (7–8), or Detractors (0–6). "
            "This bar shows the % of each brand's interview pool who were classified as Promoters."
            if _is_matrix_nps else
            f"% of verbatims expressing loyalty or repeat-purchase intent per brand across {total_docs} interviews."
        ),
        how_to_read=(
            "Higher % = more respondents strongly advocate for this brand. "
            "Benchmark: >50% Promoter rate = strong loyalty. 30–50% = moderate. <30% = at risk of switching."
            if _is_matrix_nps else
            "Longer bar = stronger loyalty signal in qualitative interviews."
        ),
    )
    fig_loyal = go.Figure(go.Bar(
        x=promoter_vals, y=loyal_brands, orientation="h",
        marker=dict(color=_P["teal"], opacity=0.80, line=dict(width=0)),
        text=[f"{v:.0f}%" for v in promoter_vals],
        textposition="outside",
        textfont=dict(size=10, family=_FONT),
        hovertemplate="<b>%{y}</b>: %{x:.1f}% Promoters<extra></extra>",
    ))
    fig_loyal.update_layout(**_base_layout(
        height=max(180, 32 * len(loyal_brands) + 50),
        margin=dict(t=8, b=8, l=8, r=60),
        xaxis=dict(ticksuffix="%", tickfont=dict(size=9), gridcolor="#f1f5f9",
                   title="% of interviews classified as Promoter (NPS 9–10)"),
        yaxis=dict(autorange="reversed", tickfont=dict(size=10, family=_FONT), showgrid=False),
    ))
    st.plotly_chart(fig_loyal, use_container_width=True, config={"displayModeBar": False})
    if promoter_vals:
        _top_brand = loyal_brands[0]
        _top_val   = promoter_vals[0]
        _chart_footer(
            f"<b>{_top_brand}</b> leads with <b>{_top_val:.0f}%</b> Promoter rate. "
            "NPS Promoter % here is qualitative — extracted from interview emotional tone and explicit recommendation signals, "
            "not a standalone survey question."
        )

    # ═══════════════════════════════════════════════════════════════════════════

with _mx_tab5:
    st.caption(_mx_tab_desc.get("Health & Trust", f"Brand health and NPS from {total_docs} IDI interviews"))
    _section('Brand Health & NPS', 'Relationship stages, NPS signals, emotional resolution across all brands')
    if _using_matrices:
        _t5c1,_t5c2=st.columns(2)
        with _t5c1:
            _chart_header(
                "NPS Promoter % by Brand",
                subtitle="% of each brand's interview pool classified as Promoters (NPS 9–10). Extracted from AI interview matrices.",
                how_to_read="Longer bar = stronger advocacy. >50% = strong loyalty base. <30% = at-risk brand.",
            )
            _nps_mx=sorted([(b,intel[b].get('promoter_pct',0)) for b in named_brands],key=lambda x:-x[1])
            for _b5,_n5 in _nps_mx:
                _bp5=round(_n5)
                _bc5=_BRAND_PAL[brand_idx_map.get(_b5,0)%len(_BRAND_PAL)]
                st.markdown(f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;"><div style="width:80px;font-size:0.73rem;color:{_bc5};font-weight:600;">{_html_mod.escape(_b5)}</div><div style="flex:1;background:#f1f5f9;border-radius:4px;height:10px;"><div style="width:{_bp5}%;background:{_bc5};height:100%;border-radius:4px;"></div></div><div style="font-size:0.70rem;color:#6b7280;width:32px;text-align:right;">{_n5:.0f}%</div></div>', unsafe_allow_html=True)
        with _t5c2:
            _chart_header(
                "Relationship Stage Distribution",
                subtitle="How respondents emotionally classify their current relationship with the brand — across all brands combined. Count = number of interviews assigned each stage.",
                how_to_read="Green stages = healthy. Amber/Red = at-risk. High 'resigned acceptance' or 'at risk' = retention priority.",
            )
            _rel_defs = {
                "honeymoon":           (_P["green"],  "actively excited, early positive phase"),
                "settled_satisfied":   (_P["teal"],   "stable loyalty, consistent positive"),
                "resigned_acceptance": (_P["amber"],  "stays but no enthusiasm — switching risk"),
                "strained":            (_P["red"],    "frustrated, considering alternatives"),
                "at_risk":             ("#dc2626",    "strong negative — likely to switch or churn"),
            }
            _legend_pills([(lbl.replace("_"," ").title(), col, desc) for lbl,(col,desc) in _rel_defs.items()])
            _rel_all_mx={}
            for b in named_brands:
                for stage,cnt in intel[b].get('relationship_stages',{}).items():
                    _rel_all_mx[stage]=_rel_all_mx.get(stage,0)+cnt
            _rel_c_mx={k: v[0] for k,v in _rel_defs.items()}
            _total_rel=max(sum(_rel_all_mx.values()),1)
            for _rk5,_rv5 in sorted(_rel_all_mx.items(),key=lambda x:-x[1]):
                _rp5=round(_rv5/_total_rel*100); _rc5=_rel_c_mx.get(_rk5,'#94a3b8')
                _stage_label = _rk5.replace("_"," ").title()
                _stage_def   = _rel_defs.get(_rk5, ("", ""))[1]
                _stage_def_html = (f'<div style="font-size:0.67rem;color:#9ca3af;padding-left:148px;margin-top:1px;">{_stage_def}</div>'
                                    if _stage_def else "")
                st.markdown(
                    f'<div style="margin-bottom:7px;">'
                    f'<div style="display:flex;align-items:center;gap:8px;">'
                    f'<div style="width:140px;font-size:0.72rem;color:{_rc5};font-weight:600;">{_stage_label}</div>'
                    f'<div style="flex:1;background:#f1f5f9;border-radius:4px;height:10px;">'
                    f'<div style="width:{_rp5}%;background:{_rc5};height:100%;border-radius:4px;"></div></div>'
                    f'<div style="font-size:0.70rem;color:#6b7280;width:28px;text-align:right;">{_rv5}</div></div>'
                    f'{_stage_def_html}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.info('Brand health data available after matrix extraction. Run pipeline first.')

    # ── Data Quality Panel ─────────────────────────────────────────────────────
    st.markdown('---')
    _section('Data Quality', 'Verbatim accuracy — all extracted quotes verified against source transcripts')

    _mx_all_scores = [s for b in named_brands for s in intel[b].get("quality_scores", []) if s is not None]
    _mx_unverified = [d for b in named_brands for d in intel[b].get("unverified_docs", [])]
    _mx_low_q = [d for b in named_brands for d in intel[b].get("low_quality_docs", [])]
    _mx_avg_q = round(sum(_mx_all_scores) / max(len(_mx_all_scores), 1), 1)

    if not _mx_all_scores and not _mx_unverified:
        st.warning("No quality scores found. Run: `python oxdata/skills/verify_verbatims.py --project mixer`")
    else:
        _qc1, _qc2, _qc3 = st.columns(3)
        with _qc1: kpi_card("Avg Verbatim Accuracy", f"{_mx_avg_q}%", _P["green"] if _mx_avg_q >= 80 else _P["amber"])
        with _qc2: kpi_card("Matrices Verified", str(len(_mx_all_scores)), _P["teal"])
        with _qc3: kpi_card("Unverified", str(len(_mx_unverified)), _P["red"] if _mx_unverified else _P["green"])

        if _mx_low_q or _mx_unverified:
            with st.expander(f"Data Quality Issues — {len(_mx_low_q)} low-quality + {len(_mx_unverified)} unverified", expanded=False):
                if _mx_unverified:
                    st.markdown(f"**Unverified ({len(_mx_unverified)}):** " + ", ".join(_mx_unverified[:20]))
                if _mx_low_q:
                    st.markdown(f"**Low quality (<60%) — {len(_mx_low_q)} interviews:**")
                    for _dq in sorted(_mx_low_q, key=lambda x: x["score"]):
                        st.markdown(f"- `{_dq['doc_id']}` — {_dq['score']}%")
        else:
            st.success(f"All {len(_mx_all_scores)} interviews verified — {_mx_avg_q}% average verbatim accuracy")

        # Per-brand quality bar
        _bq_data = [(b, intel[b].get("avg_quality", 0), intel[b].get("n_docs",0)) for b in named_brands if intel[b].get("quality_scores")]
        if _bq_data:
            st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)
            _section("Verbatim Accuracy by Brand", "")
            for _bq_brand, _bq_score, _bq_n in sorted(_bq_data, key=lambda x: -x[1]):
                _bq_col = _P["green"] if _bq_score >= 80 else (_P["amber"] if _bq_score >= 60 else _P["red"])
                _bqc = _BRAND_PAL[brand_idx_map.get(_bq_brand, 0) % len(_BRAND_PAL)]
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">'
                    f'<div style="width:90px;font-size:0.73rem;color:{_bqc};font-weight:600;">{_html_mod.escape(_bq_brand)}</div>'
                    f'<div style="flex:1;background:#f1f5f9;border-radius:4px;height:10px;">'
                    f'<div style="width:{min(_bq_score,100):.0f}%;background:{_bq_col};height:100%;border-radius:4px;"></div></div>'
                    f'<div style="font-size:0.70rem;color:#6b7280;width:50px;text-align:right;">{_bq_score:.1f}% ({_bq_n})</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

with _mx_tab6:
    # SECTION C: Passage Search
    # ═══════════════════════════════════════════════════════════════════════════
    st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)
    _section(
        "Search Transcript Passages",
        "Full-text search across 233 IDI interviews  ·  filter by brand, city, keyword",
    )

    with st.expander("🔍  Search Filters", expanded=True):
        qc1, qc2, qc3 = st.columns(3)
        with qc1:
            _TI_BRANDS = {"Bajaj","Butterfly","Crompton","Havells","Maharaja","Philips","Prestige","Usha"}
            q_brand_list = ["All Brands"] + [b for b in opts["brands"] if b in _TI_BRANDS]
            q_brand = st.selectbox("Brand", q_brand_list, key="q_b")
        with qc2:
            q_city = st.selectbox("City", ["All Cities"] + opts["cities"], key="q_c")
        with qc3:
            q_zone = st.selectbox("Zone", ["All Zones"] + opts["zones"], key="q_z")
        q_kw = st.text_input(
            "Keyword / topic",
            placeholder="noise  ·  warranty  ·  motor quality  ·  price  ·  durable",
            key="q_kw",
        )

    q_brand = st.session_state.get("q_b", "All Brands")
    q_city  = st.session_state.get("q_c", "All Cities")
    q_zone  = st.session_state.get("q_z", "All Zones")
    q_kw    = st.session_state.get("q_kw", "")

    q_query = q_kw.strip() or ""
    q_bf = None if q_brand == "All Brands" else q_brand
    q_cf = None if q_city  == "All Cities"  else q_city

    # Require at least a brand OR keyword filter — otherwise results are meaningless
    if not q_bf and not q_kw.strip():
        st.info("Select a brand or enter a keyword to search transcripts.")
        q_rows = []
    else:
        if q_bf and q_bf not in _TI_BRANDS:
            st.warning(f"No transcript data for **{q_bf}**. Transcripts cover 8 brands only. Use Survey Verbatims tab.")
            q_rows = []
        else:
            with st.spinner("Searching transcripts…"):
                if _using_matrices:
                    q_rows = _search_matrix_passages(q_query, q_bf, q_cf)
                else:
                    q_rows = _qual_search_cached(q_query, q_bf, q_cf)
            if not q_bf:
                q_rows = [r for r in q_rows if r.get("brand") and r["brand"] != "Unknown"]

    if q_zone != "All Zones":
        _zmap = {
            "North": {"Delhi","Lucknow","Bikaner","Patiala"},
            "South": {"Chennai","Cochin","Guntur","Hassan","Hyderabad","Bangalore"},
            "West":  {"Mumbai","Ahmedabad","Kolhapur","Ujjain"},
            "East":  {"Kolkata","Patna","Bhubaneshwar","Nagaon"},
        }
        q_rows = [r for r in q_rows if r.get("city","") in _zmap.get(q_zone, set())]

    qn = len(q_rows)
    qk1, qk2, qk3 = st.columns(3)
    with qk1: _kpi(str(qn),                          "Passages found", _P["purple"])
    with qk2: _kpi(q_brand if q_bf else "All brands", "Brand filter",   _P["teal"])
    with qk3: _kpi(q_city  if q_cf else "All cities", "City filter",    _P["blue"])

    st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)

    if not q_rows:
        st.info("No passages found. Try broader search terms or remove filters.")
    else:
        with st.expander("🤖  AI Theme Synthesis  (optional — OpenRouter free tier)", expanded=False):
            st.caption("Synthesises top 12 passages. Takes ~15 seconds.")
            if st.button("Synthesise these results", key="q_ai", type="primary"):
                with st.spinner("Calling OpenRouter…"):
                    q_ins = synthesize_qual_insights(q_query, q_rows[:12])
                qsumm = q_ins.get("summary", "")
                if qsumm and "failed" not in qsumm.lower():
                    st.markdown(
                        f'<div style="border-left:4px solid {_P["purple"]};border-radius:8px;'
                        f'background:#faf5ff;padding:14px 18px;">'
                        f'<div style="font-size:0.68rem;font-weight:700;color:{_P["purple"]};'
                        f'text-transform:uppercase;margin-bottom:8px;">AI synthesis</div>'
                        f'<div style="color:#1f2937;line-height:1.75;font-size:0.92rem;">'
                        f'{_html_mod.escape(qsumm)}</div></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.warning("Synthesis unavailable.")

        _section("Matching Passages", f"Showing up to 30 of {qn}  ·  expand any row for full quote")

        for i, q in enumerate(q_rows[:30]):
            text   = q.get("content", "")
            if not text:
                continue
            b_name = q.get("brand") or "Unknown"
            b_idx  = brand_idx_map.get(b_name, i)
            bc_q   = _BRAND_PAL[b_idx % len(_BRAND_PAL)]
            sl, sc, si = _sentiment(text)
            city_q = q.get("city", "—")

            preview = text[:110].rstrip() + ("…" if len(text) > 110 else "")
            with st.expander(f'{si} **{b_name}**  ·  {city_q}  —  "{preview}"', expanded=False):
                st.markdown(
                    f'<div style="font-size:0.93rem;line-height:1.85;color:#374151;'
                    f'font-family:Georgia,serif;border-left:3px solid {bc_q};padding-left:14px;">'
                    f'{_hl(text, q_kw)}</div>',
                    unsafe_allow_html=True,
                )
                tag_row = (
                    _brand_pill(b_name, bc_q) + " &nbsp; " +
                    _tag(f"📍 {city_q}", _P["blue"]) + " &nbsp; " +
                    f'<span style="float:right;background:{sc}15;color:{sc};padding:2px 10px;'
                    f'border-radius:20px;font-size:0.70rem;font-weight:700;border:1px solid {sc}28;">'
                    f'{si} {sl}</span>'
                )
                st.markdown(
                    f'<div style="display:flex;flex-wrap:wrap;gap:5px;align-items:center;'
                    f'padding:6px 0 2px;">{tag_row}</div>',
                    unsafe_allow_html=True,
                )

        if qn > 30:
            st.caption(f"Showing 30 of {qn}. Add keyword or brand filter to narrow.")




# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — PIPELINE INSPECTOR  (research workbench redesign)
# ═════════════════════════════════════════════════════════════════════════════

with _mx_tab7:

    # ── Text analytics (pre-extraction, instant) ──────────────────────────────
    def _pi_text_analytics(md: str) -> dict:
        m_turns  = re.findall(r'(?:^|\n)\s*M\s*:[ \t]*(.+)', md)
        r_turns  = re.findall(r'(?:^|\n)\s*R\s*:[ \t]*(.+)', md)
        m_words  = sum(len(t.split()) for t in m_turns)
        r_words  = sum(len(t.split()) for t in r_turns)
        all_tok  = re.findall(r'\b\w+\b', md.lower())
        total_w  = len(all_tok)
        seg_len  = max(len(md) // 5, 1)
        def _ss(chunk):
            ws = set(re.findall(r'\b\w+\b', chunk.lower()))
            p, n = len(ws & _POS), len(ws & _NEG)
            return round((p - n) / (p + n), 2) if p + n else 0.0
        sentiment_curve = [_ss(md[i*seg_len:(i+1)*seg_len]) for i in range(5)]
        text_ws  = set(all_tok)
        topic_hits = {t: len(text_ws & kws) for t, kws in _TOPIC_KWORDS.items() if text_ws & kws}
        topic_hits = dict(sorted(topic_hits.items(), key=lambda x: -x[1]))
        _KNOWN = {"Bajaj","Butterfly","Crompton","Havells","Maharaja","Philips","Prestige","Usha"}
        brand_c = {b: len(re.findall(r'\b' + b + r'\b', md, re.IGNORECASE)) for b in _KNOWN}
        brand_c = dict(sorted({b:c for b,c in brand_c.items() if c}.items(), key=lambda x: -x[1]))
        _HI = {"aur","yeh","hai","nahi","bahut","tha","thi","ka","ki","ke","toh","jo",
               "mera","mere","wala","wali","bhi","accha","acha","kuch","koi","sab"}
        hindi_r = round(sum(1 for w in all_tok if w in _HI) / max(total_w,1) * 100, 1)
        r_lens  = [len(t.split()) for t in r_turns]
        avg_r   = round(sum(r_lens) / max(len(r_lens),1))
        emo_c   = sum(1 for w in all_tok if w in (_POS | _NEG))
        emo_d   = round(emo_c / max(total_w,1) * 100, 1)
        content = [w for w in all_tok if len(w) >= 3 and w not in _STOP]
        ttr     = round(len(set(content)) / max(len(content),1) * 100, 1)
        return dict(m_turns=len(m_turns), r_turns=len(r_turns),
                    m_words=m_words, r_words=r_words, total_words=total_w,
                    r_talk_pct=round(r_words/max(r_words+m_words,1)*100),
                    sentiment_curve=sentiment_curve, topic_hits=topic_hits,
                    brand_counts=brand_c, hindi_ratio=hindi_r,
                    avg_r_words=int(avg_r), emo_density=emo_d, vocab_richness=ttr)

    def _pi_signal_scores(parsed: dict) -> dict:
        nps     = str(parsed.get("nps_signal") or "unclear")
        emo_res = str(parsed.get("emotional_resolution") or "neutral")
        br      = parsed.get("brand_relationship") or {}
        rel_raw = str(br.get("relationship_stage") or "—") if isinstance(br, dict) else "—"
        rel     = rel_raw.replace(" ", "_")
        adv     = str(br.get("advocacy_likelihood") or "") if isinstance(br, dict) else ""
        swt     = br.get("switching_consideration", False) if isinstance(br, dict) else False
        nps_s   = {"promoter":40,"passive":20,"detractor":0,"unclear":15}.get(nps, 15)
        emo_s   = {"positive":35,"neutral":20,"negative":0}.get(emo_res, 15)
        rel_s   = {"honeymoon":25,"settled_satisfied":22,"resigned_acceptance":12,
                   "strained":6,"at_risk":2,"churned_mentally":0}.get(rel, 10)
        satisfaction = min(100, nps_s + emo_s + rel_s)
        pains   = [p for p in (parsed.get("pain_points") or []) if isinstance(p, dict)]
        high_p  = sum(1 for p in pains if p.get("severity") == "high")
        prod_bl = len(parsed.get("product_blame_instances") or [])
        risk    = min(100, high_p*14 + prod_bl*8 + (22 if swt else 0) + (15 if "wont" in adv else 0))
        gaps    = [g for g in (parsed.get("aspiration_reality_gaps") or []) if isinstance(g, dict)]
        hi_g    = sum(1 for g in gaps if g.get("commercial_opportunity") == "high")
        md_g    = sum(1 for g in gaps if g.get("commercial_opportunity") == "medium")
        unspk   = len([u for u in (parsed.get("unspoken_needs") or []) if u])
        opp     = min(100, hi_g*28 + md_g*12 + unspk*7)
        return {"satisfaction": satisfaction, "risk": risk, "opportunity": opp}

    def _pi_score_breakdown(parsed: dict) -> dict:
        """Exact calculation steps for each signal score — for transparency display."""
        nps     = str(parsed.get("nps_signal") or "unclear")
        emo_res = str(parsed.get("emotional_resolution") or "neutral")
        br      = parsed.get("brand_relationship") or {}
        rel_raw = str(br.get("relationship_stage") or "—") if isinstance(br, dict) else "—"
        rel     = rel_raw.replace(" ", "_")
        adv     = str(br.get("advocacy_likelihood") or "") if isinstance(br, dict) else ""
        swt     = br.get("switching_consideration", False) if isinstance(br, dict) else False
        nps_s   = {"promoter":40,"passive":20,"detractor":0,"unclear":15}.get(nps, 15)
        emo_s   = {"positive":35,"neutral":20,"negative":0}.get(emo_res, 15)
        rel_s   = {"honeymoon":25,"settled_satisfied":22,"resigned_acceptance":12,
                   "strained":6,"at_risk":2,"churned_mentally":0}.get(rel, 10)
        pains   = [p for p in (parsed.get("pain_points") or []) if isinstance(p, dict)]
        high_p  = sum(1 for p in pains if p.get("severity") == "high")
        prod_bl = len(parsed.get("product_blame_instances") or [])
        gaps    = [g for g in (parsed.get("aspiration_reality_gaps") or []) if isinstance(g, dict)]
        hi_g    = sum(1 for g in gaps if g.get("commercial_opportunity") == "high")
        md_g    = sum(1 for g in gaps if g.get("commercial_opportunity") == "medium")
        unspk   = len([u for u in (parsed.get("unspoken_needs") or []) if u])
        swt_pts = 22 if swt else 0
        wnt_pts = 15 if "wont" in adv else 0
        return {
            "satisfaction": [
                ("NPS signal",           nps,     nps_s,   "max 40  (promoter=40, passive=20, detractor=0)"),
                ("Emotional resolution", emo_res, emo_s,   "max 35  (positive=35, neutral=20, negative=0)"),
                ("Relationship stage",   rel_raw, rel_s,   "max 25  (honeymoon=25, settled=22, strained=6, at_risk=2)"),
                ("TOTAL",                "",      min(100, nps_s+emo_s+rel_s), "capped at 100"),
            ],
            "risk": [
                ("High-severity pain pts", f"{high_p} × 14", high_p*14,        "max 40  (each high-severity = 14 pts)"),
                ("Product blame instances",f"{prod_bl} × 8",  prod_bl*8,       "max 25  (each instance = 8 pts)"),
                ("Switching intent",       str(swt),           swt_pts,         "binary: yes=22, no=0"),
                ("Won't recommend",        str("wont" in adv), wnt_pts,         "binary: wont_recommend=15, else 0"),
                ("TOTAL",                  "",  min(100,high_p*14+prod_bl*8+swt_pts+wnt_pts), "capped at 100"),
            ],
            "opportunity": [
                ("High-opp gaps",   f"{hi_g} × 28", hi_g*28,           "max 56  (each high-opp gap = 28 pts)"),
                ("Medium-opp gaps", f"{md_g} × 12", md_g*12,           "max 36  (each medium-opp gap = 12 pts)"),
                ("Unspoken needs",  f"{unspk} × 7",  unspk*7,          "max 35  (each unspoken need = 7 pts)"),
                ("TOTAL",           "",  min(100, hi_g*28+md_g*12+unspk*7), "capped at 100"),
            ],
        }

    def _pi_load_corpus() -> dict:
        """Aggregate stats from all qual_matrices/*.json for corpus-wide comparison.
        Cached in session_state — invalidated when matrix dir mtime changes."""
        _mtime = _matrices_mtime()
        if st.session_state.get("_pi_corpus_mtime") == _mtime and st.session_state.get("_pi_corpus"):
            return st.session_state["_pi_corpus"]
        files = list(_MATRICES_DIR.glob("*_matrix.json")) if _MATRICES_DIR.exists() else []
        if not files:
            st.session_state.update({"_pi_corpus": {"n_docs": 0}, "_pi_corpus_mtime": _mtime})
            return {"n_docs": 0}
        pain_c, hi_pain_c, gap_c, hi_gap_c = [], [], [], []
        sb_c, pb_c, unspk_c = [], [], []
        sat_c, risk_c, opp_c = [], [], []
        nps_d: dict = {}; rel_d: dict = {}; emo_d: dict = {}
        cook_d: dict = {}; brand_d: dict = {}
        for fp in files:
            try: m = json.loads(fp.read_text(encoding="utf-8"))
            except: continue
            pns = [p for p in (m.get("pain_points") or []) if isinstance(p, dict)]
            gps = [g for g in (m.get("aspiration_reality_gaps") or []) if isinstance(g, dict)]
            pain_c.append(len(pns))
            hi_pain_c.append(sum(1 for p in pns if p.get("severity") == "high"))
            gap_c.append(len(gps))
            hi_gap_c.append(sum(1 for g in gps if g.get("commercial_opportunity") == "high"))
            sb_c.append(len(m.get("self_blame_instances") or []))
            pb_c.append(len(m.get("product_blame_instances") or []))
            unspk_c.append(len([u for u in (m.get("unspoken_needs") or []) if u]))
            for _dk, _dv in [("nps_signal","nps_d"),("emotional_resolution","emo_d")]:
                _dmap = locals()[_dv]; _k = str(m.get(_dk) or "unknown")
                _dmap[_k] = _dmap.get(_k, 0) + 1
            _br2 = m.get("brand_relationship") or {}
            if isinstance(_br2, dict):
                _rk = str(_br2.get("relationship_stage") or "unknown")
                rel_d[_rk] = rel_d.get(_rk, 0) + 1
            _ids2 = m.get("identity_signals") or {}
            if isinstance(_ids2, dict):
                _ck = str(_ids2.get("cook_self_image") or "unknown")
                cook_d[_ck] = cook_d.get(_ck, 0) + 1
            _resp2 = m.get("respondent") or {}
            if isinstance(_resp2, dict) and _resp2.get("brand_owned"):
                _bk = str(_resp2["brand_owned"])
                brand_d[_bk] = brand_d.get(_bk, 0) + 1
            _sc2 = _pi_signal_scores(m)
            sat_c.append(_sc2["satisfaction"]); risk_c.append(_sc2["risk"]); opp_c.append(_sc2["opportunity"])
        n = len(pain_c)
        if n == 0:
            st.session_state.update({"_pi_corpus": {"n_docs": 0}, "_pi_corpus_mtime": _mtime})
            return {"n_docs": 0}
        def _a(l): return round(sum(l)/max(len(l),1), 1)
        cs = dict(n_docs=n,
                  pain_c=pain_c,    avg_pain=_a(pain_c),
                  hi_pain_c=hi_pain_c, avg_hi_pain=_a(hi_pain_c),
                  gap_c=gap_c,      avg_gaps=_a(gap_c),
                  hi_gap_c=hi_gap_c, avg_hi_gaps=_a(hi_gap_c),
                  sb_c=sb_c,        avg_sb=_a(sb_c),
                  pb_c=pb_c,        avg_pb=_a(pb_c),
                  unspk_c=unspk_c,  avg_unspk=_a(unspk_c),
                  sat_c=sat_c,      avg_sat=_a(sat_c),
                  risk_c=risk_c,    avg_risk=_a(risk_c),
                  opp_c=opp_c,      avg_opp=_a(opp_c),
                  nps_d=nps_d, rel_d=rel_d, emo_d=emo_d,
                  cook_d=cook_d,
                  brand_d=dict(sorted(brand_d.items(), key=lambda x: -x[1])[:10]))
        st.session_state.update({"_pi_corpus": cs, "_pi_corpus_mtime": _mtime})
        return cs

    def _pi_pct(val: float, lst: list) -> int:
        """Percentile rank: what % of corpus scored below this value."""
        if not lst: return 50
        return round(sum(1 for x in lst if x < val) / len(lst) * 100)

    def _pi_delta_badge(val, avg, higher_is_better=True) -> tuple:
        """Returns (delta_str, color) showing deviation from corpus avg."""
        if avg == 0: return ("—", "#9ca3af")
        delta = val - avg
        pct   = round(delta / max(abs(avg), 0.1) * 100)
        if abs(pct) < 10: return ("≈ avg", "#94a3b8")
        up = higher_is_better
        if pct > 0: return (f"+{pct}% vs avg", _P["green"] if up else _P["red"])
        return (f"{pct}% vs avg", _P["red"] if up else _P["green"])

    def _pi_seg_topics(md: str) -> list:
        """Per-segment dominant topics (5 segments, top 3 topics each)."""
        seg_len = max(len(md) // 5, 1)
        result = []
        for i in range(5):
            chunk = md[i*seg_len:(i+1)*seg_len]
            ws = set(re.findall(r'\b\w+\b', chunk.lower()))
            hits = {t: list(ws & kws) for t, kws in _TOPIC_KWORDS.items() if ws & kws}
            top3 = sorted(hits.items(), key=lambda x: -len(x[1]))[:3]
            result.append(top3)
        return result

    # ── Dimension definitions ─────────────────────────────────────────────────
    _PI_DIMS = [
        {"name":"Respondent Profile","key":"respondent","default":True,"schema":
         '"respondent": {\n    "city": "city from transcript context",\n    "brand_owned": "primary brand discussed in depth",\n    "journey_stage": "loyal_user | recent_buyer | intender | lapsed | aware_only",\n    "usage_context": "homemaker | working_professional | business_cook | mixed",\n    "household_size": "small_1_3 | medium_4_6 | large_7_plus"\n  }'},
        {"name":"Pain Points","key":"pain_points","default":True,"schema":
         '"pain_points": [\n    {"severity": "high|medium|low", "issue_description": "specific problem",\n     "product_area": "motor|jar|blade|noise|service|price|dough|texture|cleaning|other",\n     "workaround_used": "what they do instead or null", "verbatim_quote": "direct quote"}\n  ]'},
        {"name":"Brand Relationship","key":"brand_relationship","default":True,"schema":
         '"brand_relationship": {\n    "relationship_stage": "honeymoon|settled_satisfied|resigned_acceptance|strained|at_risk|churned_mentally",\n    "loyalty_depth": "surface|functional|emotional|identity",\n    "switching_consideration": false,\n    "switching_trigger": "what would make them switch or null",\n    "advocacy_likelihood": "will_recommend|might_recommend|wont_recommend",\n    "unresolved_tension": "the one thing still bothering them",\n    "relationship_verbatim": "best single quote capturing their relationship with the brand"\n  }'},
        {"name":"Cultural Context","key":"cultural_context","default":True,"schema":
         '"cultural_context": {\n    "regional_food_culture": "south_indian|north_indian|western_india|east_india|mixed",\n    "primary_use_cases": ["idly_batter","ginger_garlic_paste","chapati_dough","bulk_biryani"],\n    "event_cooking": false,\n    "time_pressure_type": "morning_rush|working_woman_schedule|business_volume|none",\n    "traditional_vs_modern": "traditional_lean|modern_lean|mixed",\n    "family_food_values": "health_first|convenience_first|taste_first|mixed"\n  }'},
        {"name":"Emotional Arc & Peak","key":"emotional_arc","default":True,"schema":
         '"emotional_arc": [\n    {"moment": "early|mid|late", "emotion": "frustration|delight|resignation|pride|anxiety|surprise|ambivalence|relief", "arousal_intensity": 5, "valence": "positive|negative", "trigger": "specific trigger", "verbatim_quote": "quote"}\n  ],\n  "peak_emotional_moment": {"emotion": "emotion name", "trigger": "what caused it", "verbatim_quote": "the quote"},\n  "emotional_resolution": "positive|negative|neutral"'},
        {"name":"Aspiration-Reality Gaps","key":"aspiration_reality_gaps","default":True,"schema":
         '"aspiration_reality_gaps": [\n    {"aspiration": "what they wish for — specific", "current_reality": "what actually happens", "workaround": "invented solution or null", "emotional_charge": "resignation|frustration|acceptance|hope|silent_suffering", "commercial_opportunity": "high|medium|low", "verbatim_quote": "quote revealing the gap"}\n  ]'},
        {"name":"Identity Signals","key":"identity_signals","default":True,"schema":
         '"identity_signals": {\n    "cook_self_image": "skilled|competent|struggling|efficient|creative|burdened|mixed",\n    "kitchen_role": "primary_caregiver|working_professional|business_cook|shared_responsibility",\n    "appliance_as_identity": "high|medium|low",\n    "competence_framing": "quote showing how they see themselves as a cook",\n    "pride_moments": [{"trigger": "what made them proud", "verbatim_quote": "quote"}],\n    "inadequacy_moments": [{"trigger": "what made them feel inadequate", "verbatim_quote": "quote"}]\n  }'},
        {"name":"Social Dynamics","key":"social_dynamics","default":True,"schema":
         '"social_dynamics": {\n    "household_type": "nuclear|joint|extended",\n    "purchase_decision_maker": "self|husband|joint|mother_in_law|other",\n    "influencer_network": [{"relation": "friend|colleague|dealer|YouTube|family|social_media|neighbour", "influence_type": "recommendation|warning|demonstration|gifted|observed", "verbatim_quote": "quote"}],\n    "cooking_labor": "solo|shared|helped_by_family|helped_by_domestic_help",\n    "social_proof_reliance": "high|medium|low"\n  }'},
        {"name":"All Passages","key":"all_passages","default":False,"schema":
         '"all_passages": [\n    {"content": "verbatim quote min 25 words", "sentiment": "positive|negative|neutral", "topic": "grinding_texture|noise|purchase_decision|brand_trust|cooking_habits|service|price|family_dynamics|aspirations|workaround|product_performance", "pain_point": false, "decision_signal": false}\n  ]'},
        {"name":"Jobs to Be Done","key":"jobs_to_be_done","default":False,"schema":
         '"jobs_to_be_done": ["concrete specific job — e.g. grind 3kg ginger-garlic paste for bulk biryani"]'},
        {"name":"Unspoken Needs","key":"unspoken_needs","default":False,"schema":
         '"unspoken_needs": ["need inferred from workarounds — never directly stated"]'},
        {"name":"Decision Drivers","key":"decision_drivers","default":False,"schema":
         '"decision_drivers": [{"trigger": "what caused purchase", "info_source": "YouTube|dealer|friend|family|online_research|TV_ad|other", "key_reason": "why they chose this brand"}]'},
        {"name":"Language Patterns","key":"language_patterns","default":False,"schema":
         '"language_patterns": {"hedging_phrases": ["uncertainty phrases"], "self_blame_phrases": ["exact self-blame"], "aspiration_markers": ["if only, I wish"]}'},
    ]
    _PI_COMPOUND = {"emotional_arc": ["emotional_arc","peak_emotional_moment","emotional_resolution"]}
    _PI_REL_C = {"honeymoon":_P["green"],"settled satisfied":_P["teal"],"resigned acceptance":_P["amber"],"strained":_P["red"],"at risk":"#dc2626","churned mentally":"#7f1d1d"}
    _PI_EMO_C = {"frustration":_P["red"],"delight":_P["green"],"resignation":_P["amber"],"pride":_P["green"],"anxiety":_P["orange"],"surprise":_P["purple"],"ambivalence":"#94a3b8","relief":_P["teal"],"confusion":_P["amber"]}
    _PI_SIC   = {"skilled":_P["green"],"competent":_P["teal"],"efficient":_P["blue"],"creative":_P["purple"],"struggling":_P["amber"],"burdened":_P["red"],"mixed":"#94a3b8"}
    _PI_BASE  = ("You are a qualitative research analyst processing an In-Depth Interview (IDI) "
                 "from an Indian electrical appliances market research study.\n"
                 "Study: Mixer grinders, food processors, ceiling fans, air coolers. "
                 "Respondents are middle-class Indian household decision-makers.\n\n"
                 "Read the complete transcript. Extract ALL dimensions into ONE JSON object.\n"
                 "Return ONLY valid JSON. No markdown fences. No explanation. Raw JSON only.\n\n")
    _PI_RULES = ("\n\nCRITICAL RULES:\n- verbatim_quote MUST be direct quotes from transcript\n"
                 "- Include all entries the data supports\n- city: from conversation context\n"
                 "- Return raw JSON only\n\nTRANSCRIPT:\n{transcript}")

    def _pi_build_prompt(schemas):
        return _PI_BASE + "{\n  " + ",\n  ".join(schemas) + "\n}" + _PI_RULES

    def _pi_api(prompt: str) -> str:
        key = _get_or_key()
        if not key: return ""
        for model in _FREE_MODELS:
            try:
                pl = json.dumps({"model":model,"messages":[{"role":"system","content":"Return ONLY valid JSON."},{"role":"user","content":prompt}],"max_tokens":4096,"temperature":0.1}).encode()
                req = urllib.request.Request(_OR_URL, data=pl, headers={"Authorization":f"Bearer {key}","Content-Type":"application/json","HTTP-Referer":"https://infoleap.ai"}, method="POST")
                with urllib.request.urlopen(req, timeout=60) as r:
                    t = json.loads(r.read())["choices"][0]["message"]["content"].strip()
                    if t: return t
            except Exception: continue
        return ""

    def _pi_parse(text: str):
        text = re.sub(r'^```(?:json)?\s*','',text.strip()); text = re.sub(r'\s*```$','',text.rstrip())
        try: return json.loads(text)
        except Exception: pass
        s = text.find("{")
        if s == -1: return None
        d, e = 0, -1
        for i, ch in enumerate(text[s:], s):
            if ch=="{": d+=1
            elif ch=="}":
                d-=1
                if d==0: e=i; break
        if e == -1: return None
        try: return json.loads(text[s:e+1])
        except Exception: return None

    # session state — batch list + current-selection shortcuts
    for _k,_v in [("pi_batch",[]),("pi_sel",0),
                  ("pi_md",""),("pi_fname",""),("pi_result",None),("pi_analytics",None),("pi_ana_fname","")]:
        if _k not in st.session_state: st.session_state[_k] = _v

    def _pi_docx_to_md(file_bytes: bytes, fname: str) -> str:
        doc_ = _DocxDocument(io.BytesIO(file_bytes))
        lines_ = []
        for p_ in doc_.paragraphs:
            t_ = p_.text.strip()
            if not t_: continue
            lines_.append(f"{'#'*int(p_.style.name[-1])} {t_}" if p_.style.name.startswith("Heading") and p_.style.name[-1].isdigit() else t_)
        return "\n\n".join(lines_)

    def _pi_sync_selection():
        """Sync main shortcuts (pi_md/fname/result/analytics) from pi_batch[pi_sel]."""
        _bat = st.session_state["pi_batch"]
        _idx = st.session_state["pi_sel"]
        if not _bat or _idx >= len(_bat): return
        _cur = _bat[_idx]
        st.session_state["pi_md"]       = _cur.get("md","")
        st.session_state["pi_fname"]     = _cur.get("fname","")
        st.session_state["pi_result"]    = _cur.get("result")
        st.session_state["pi_analytics"] = _cur.get("analytics")

    # ══════════════════════════════════════════════════════════════════════════
    # HEADER
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown(
        '<div style="background:linear-gradient(135deg,#1e1b4b 0%,#4c1d95 55%,#7c3aed 100%);'
        'border-radius:14px;padding:20px 26px 16px;margin-bottom:18px;">'
        '<div style="font-size:0.60rem;font-weight:800;letter-spacing:0.18em;color:rgba(255,255,255,0.4);text-transform:uppercase;margin-bottom:4px;">Research Workbench</div>'
        '<div style="font-size:1.5rem;font-weight:900;color:white;letter-spacing:-0.01em;">🔬 Pipeline Inspector</div>'
        '<div style="margin-top:5px;color:rgba(255,255,255,0.6);font-size:0.82rem;">'
        'Upload DOCX → instant text analytics → dimension extraction → intelligence dashboard</div></div>',
        unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # CONTROLS — multi-file batch (max 5), collapses after first extraction
    # ══════════════════════════════════════════════════════════════════════════
    _batch      = st.session_state["pi_batch"]
    _n_batch    = len(_batch)
    _n_done     = sum(1 for f in _batch if f.get("result"))
    _has_res    = _n_done > 0
    _ctr_label  = f"⚙️  Setup & Controls  ·  {_n_batch} file{'s' if _n_batch!=1 else ''} loaded, {_n_done} extracted" if _n_batch else "⚙️  Setup & Controls"
    with st.expander(_ctr_label, expanded=not _has_res):

        # Step 1: Multi-file upload
        st.markdown('<div style="font-size:0.62rem;font-weight:800;color:#7c3aed;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:5px;">Step 1 — Upload (max 5 transcripts)</div>', unsafe_allow_html=True)
        if not _DOCX_AVAIL:
            st.warning("python-docx not installed. Run: pip install python-docx")
        _pi_ups = st.file_uploader("DOCX transcripts", type=["docx"], key="pi_upload",
                                    label_visibility="collapsed", disabled=not _DOCX_AVAIL,
                                    accept_multiple_files=True)
        if _pi_ups and _DOCX_AVAIL:
            _existing_fnames = {f["fname"] for f in _batch}
            _added = 0
            for _upf in _pi_ups:
                if _upf.name in _existing_fnames: continue
                if len(_batch) >= 5:
                    st.warning("Max 5 files. Remove a file first to add more."); break
                try:
                    _md_new = _pi_docx_to_md(_upf.read(), _upf.name)
                    _batch.append({"fname":_upf.name,"md":_md_new,"analytics":None,"result":None})
                    _added += 1
                except Exception as _ue: st.error(f"Failed {_upf.name}: {_ue}")
            if _added:
                st.session_state["pi_batch"] = _batch
                st.session_state["pi_sel"] = len(_batch) - 1
                _pi_sync_selection()
                st.session_state.pop("pi_prompt", None)
                st.rerun()

        # Batch status: show each file with status + remove button
        if _batch:
            st.markdown('<div style="font-size:0.58rem;font-weight:700;color:#9ca3af;text-transform:uppercase;margin:6px 0 4px;">Loaded files</div>', unsafe_allow_html=True)
            for _fi, _fb in enumerate(_batch):
                _fb_done = bool(_fb.get("result") and _fb["result"].get("parsed"))
                _fb_icon = "✓" if _fb_done else "⏳"
                _fb_c    = _P["green"] if _fb_done else "#9ca3af"
                _fbc1, _fbc2, _fbc3 = st.columns([3, 1, 1])
                with _fbc1:
                    st.markdown(
                        f'<div style="font-size:0.72rem;color:{_fb_c};font-weight:{"700" if _fi==st.session_state["pi_sel"] else "400"};">'
                        f'{_fb_icon} {_html_mod.escape(_fb["fname"])} '
                        f'<span style="color:#9ca3af;">({len(_fb["md"]):,} chars)</span></div>',
                        unsafe_allow_html=True)
                with _fbc2:
                    if st.button("Select", key=f"pi_sel_{_fi}", use_container_width=True):
                        st.session_state["pi_sel"] = _fi; _pi_sync_selection(); st.rerun()
                with _fbc3:
                    if st.button("✕", key=f"pi_rm_{_fi}", use_container_width=True):
                        st.session_state["pi_batch"].pop(_fi)
                        st.session_state["pi_sel"] = max(0, st.session_state["pi_sel"]-1)
                        _pi_sync_selection(); st.rerun()

        # Step 2: Dimensions
        st.markdown('<div style="margin:10px 0 5px;font-size:0.62rem;font-weight:800;color:#7c3aed;text-transform:uppercase;letter-spacing:0.1em;">Step 2 — Dimensions</div>', unsafe_allow_html=True)
        _pi_main  = [d for d in _PI_DIMS if d["default"]]
        _pi_extra = [d for d in _PI_DIMS if not d["default"]]
        _pi_sel_dims = {}
        _dcols = st.columns(4)
        for _i, _d in enumerate(_pi_main):
            with _dcols[_i % 4]:
                _pi_sel_dims[_d["name"]] = st.checkbox(_d["name"], value=True, key=f"pi_d_{_d['key']}")
        with st.expander("➕ Additional", expanded=False):
            _ec = st.columns(3)
            for _i, _d in enumerate(_pi_extra):
                with _ec[_i % 3]:
                    _pi_sel_dims[_d["name"]] = st.checkbox(_d["name"], value=False, key=f"pi_d_{_d['key']}")
        _pi_active  = [d for d in _PI_DIMS if _pi_sel_dims.get(d["name"], False)]
        _pi_built   = _pi_build_prompt([d["schema"] for d in _pi_active])

        # Step 3: Prompt
        st.markdown(f'<div style="margin:10px 0 5px;font-size:0.62rem;font-weight:800;color:#7c3aed;text-transform:uppercase;letter-spacing:0.1em;">Step 3 — Prompt ({len(_pi_active)} dims · editable)</div>', unsafe_allow_html=True)
        if "pi_prompt" not in st.session_state: st.session_state["pi_prompt"] = _pi_built
        _rc1, _rc2 = st.columns([1, 4])
        with _rc1:
            if st.button("↺ Rebuild", key="pi_regen", use_container_width=True):
                st.session_state["pi_prompt"] = _pi_built; st.rerun()
        with _rc2: st.caption("Applies to all files. Editing does not auto-sync to checkboxes.")
        st.text_area("Prompt", key="pi_prompt", height=180, label_visibility="collapsed")

        # Step 4: Extract
        st.markdown('<div style="margin:10px 0 5px;font-size:0.62rem;font-weight:800;color:#7c3aed;text-transform:uppercase;letter-spacing:0.1em;">Step 4 — Extract</div>', unsafe_allow_html=True)
        _pending = [f for f in _batch if not f.get("result")]
        _can_run = bool(_batch) and bool(_get_or_key())
        _rb1, _rb2, _rb3 = st.columns([1, 1, 2])
        with _rb1:
            _run_all = st.button(f"▶ Extract All Pending ({len(_pending)})", key="pi_run_all",
                                  type="primary", use_container_width=True,
                                  disabled=not _can_run or not _pending)
        with _rb2:
            _cur_no_result = not st.session_state.get("pi_result")
            _run_sel = st.button("▶ Extract Selected", key="pi_run_sel",
                                  use_container_width=True,
                                  disabled=not _can_run or not st.session_state["pi_md"])
        with _rb3:
            if not _batch: st.info("Upload DOCX files first.")
            elif not _get_or_key(): st.warning("OPENROUTER_API_KEY missing.")
            else: st.caption(f"{len(_pending)} pending · {len(_pi_active)} dims · ~30s/file · free models")

        def _do_extract(file_entry: dict) -> dict:
            _fp2 = st.session_state.get("pi_prompt","").replace("{transcript}", file_entry["md"][:100_000])
            _raw2 = _pi_api(_fp2)
            if not _raw2: return file_entry
            _parsed2 = _pi_parse(_raw2)
            file_entry["result"] = {"parsed":_parsed2,"raw":_raw2,
                                    "dims":[d["name"] for d in _pi_active],
                                    "fname":file_entry["fname"]}
            return file_entry

        if _run_all and _pending:
            _prog = st.progress(0)
            for _pi_run_i, _pf in enumerate(_pending):
                with st.spinner(f"Extracting {_pf['fname']} ({_pi_run_i+1}/{len(_pending)})…"):
                    _idx2 = next(j for j,f in enumerate(st.session_state["pi_batch"]) if f["fname"]==_pf["fname"])
                    st.session_state["pi_batch"][_idx2] = _do_extract(st.session_state["pi_batch"][_idx2])
                _prog.progress((_pi_run_i+1)/len(_pending))
            _pi_sync_selection(); st.rerun()

        if _run_sel and st.session_state["pi_md"]:
            _sel_idx2 = st.session_state["pi_sel"]
            with st.spinner(f"Extracting {st.session_state['pi_fname']}…"):
                st.session_state["pi_batch"][_sel_idx2] = _do_extract(st.session_state["pi_batch"][_sel_idx2])
            _pi_sync_selection(); st.rerun()

    st.markdown("<div style='margin:10px 0;'></div>", unsafe_allow_html=True)

    # ── File selector bar (shows when batch has >1 file) ─────────────────────
    _batch = st.session_state["pi_batch"]
    if len(_batch) > 1:
        _sel_cur = st.session_state["pi_sel"]
        st.markdown(
            f'<div style="font-size:0.60rem;font-weight:800;color:#9ca3af;text-transform:uppercase;'
            f'letter-spacing:0.1em;margin-bottom:6px;">Viewing file {_sel_cur+1} of {len(_batch)}</div>',
            unsafe_allow_html=True)
        _fs_cols = st.columns(len(_batch))
        for _fi2, _fb2 in enumerate(_batch):
            with _fs_cols[_fi2]:
                _is_sel = _fi2 == _sel_cur
                _fb2_done = bool(_fb2.get("result") and _fb2["result"].get("parsed"))
                _fb2_icon = "✓ " if _fb2_done else "⏳ "
                _btn_type = "primary" if _is_sel else "secondary"
                _short_name = _fb2["fname"].replace(".docx","")[:18]
                if st.button(f"{_fb2_icon}{_short_name}", key=f"pi_fsel_{_fi2}",
                              type=_btn_type, use_container_width=True):
                    st.session_state["pi_sel"] = _fi2; _pi_sync_selection(); st.rerun()
        st.markdown("<div style='margin:8px 0 0;height:1px;background:#e2e8f0;'></div>", unsafe_allow_html=True)
        st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # TEXT ANALYTICS — instant, from raw MD
    # ══════════════════════════════════════════════════════════════════════════
    _pi_md = st.session_state["pi_md"]
    if _pi_md:
        if st.session_state["pi_ana_fname"] != st.session_state["pi_fname"] or st.session_state["pi_analytics"] is None:
            st.session_state["pi_analytics"] = _pi_text_analytics(_pi_md)
            st.session_state["pi_ana_fname"]  = st.session_state["pi_fname"]
        _ana = st.session_state["pi_analytics"]

        st.markdown(
            f'<div style="border-left:4px solid {_P["purple"]};padding:3px 0 3px 14px;margin-bottom:10px;">'
            f'<span style="font-size:0.86rem;font-weight:800;color:{_P["purple"]};">Text Analytics</span>'
            f'<span style="font-size:0.68rem;color:#9ca3af;margin-left:8px;">computed instantly from raw transcript · no API needed</span>'
            f'</div>', unsafe_allow_html=True)

        # KPI strip
        _t1,_t2,_t3,_t4,_t5,_t6 = st.columns(6)
        with _t1: _kpi(f"{_ana['total_words']:,}", "Words", _P["purple"])
        with _t2: _kpi(f"{_ana['r_talk_pct']}%", "R talks", _P["teal"])
        with _t3: _kpi(str(_ana['avg_r_words']), "Avg R turn", _P["blue"])
        with _t4: _kpi(str(len(_ana['topic_hits'])), "Topics hit", _P["green"])
        with _t5: _kpi(f"{_ana['emo_density']}%", "Emotional density", _P["orange"])
        with _t6: _kpi(f"{_ana['hindi_ratio']}%", "Bilingual", _P["amber"])

        st.markdown("<div style='margin:10px 0;'></div>", unsafe_allow_html=True)
        _anL, _anR = st.columns([1.1, 1])

        with _anL:
            # Sentiment arc: 5 colored blocks
            _ARC_LABELS = ["Early","25%","Mid","75%","Late"]
            def _arc_col(s):
                if s >= 0.3: return _P["green"]
                if s >= 0.05: return _P["teal"]
                if s >= -0.05: return "#94a3b8"
                if s >= -0.3: return _P["amber"]
                return _P["red"]
            _arc_html = (
                '<div style="margin-bottom:5px;font-size:0.60rem;font-weight:800;color:#374151;'
                'text-transform:uppercase;letter-spacing:0.08em;">Sentiment Arc — interview trajectory</div>'
                '<div style="display:flex;gap:4px;height:58px;">'
            )
            for _i, _s in enumerate(_ana["sentiment_curve"]):
                _c = _arc_col(_s)
                _arc_html += (
                    f'<div style="flex:1;background:{_c};border-radius:6px;display:flex;flex-direction:column;'
                    f'align-items:center;justify-content:space-between;padding:5px 3px;">'
                    f'<span style="font-size:0.62rem;font-weight:800;color:white;">{_s:+.2f}</span>'
                    f'<span style="font-size:0.55rem;color:rgba(255,255,255,0.7);">{_ARC_LABELS[_i]}</span>'
                    f'</div>'
                )
            _arc_html += '</div><div style="font-size:0.58rem;color:#9ca3af;margin-top:3px;">Green=positive · Red=negative · Grey=neutral</div>'
            st.markdown(f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:11px 13px;">{_arc_html}</div>', unsafe_allow_html=True)

        with _anR:
            # Topic coverage bars
            _mx = max(_ana["topic_hits"].values(), default=1)
            _tc = '<div style="margin-bottom:5px;font-size:0.60rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:0.08em;">Topic Coverage (keyword hits)</div>'
            for _tp, _ht in list(_ana["topic_hits"].items())[:8]:
                _bw = round(_ht / _mx * 100)
                _tc += (f'<div style="margin-bottom:4px;">'
                        f'<div style="display:flex;justify-content:space-between;font-size:0.63rem;margin-bottom:1px;">'
                        f'<span style="color:#374151;font-weight:600;">{_html_mod.escape(_tp)}</span>'
                        f'<span style="color:#9ca3af;">{_ht}</span></div>'
                        f'<div style="background:#e5e7eb;border-radius:3px;height:6px;">'
                        f'<div style="width:{_bw}%;background:{_P["teal"]};height:100%;border-radius:3px;"></div></div></div>')
            if not _ana["topic_hits"]: _tc += '<div style="font-size:0.72rem;color:#9ca3af;">No strong topic keywords.</div>'
            st.markdown(f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:11px 13px;">{_tc}</div>', unsafe_allow_html=True)

        # Brand pills + stats
        _bm_p = "".join(
            f'<span style="background:{_BRAND_PAL[_i%len(_BRAND_PAL)]}18;color:{_BRAND_PAL[_i%len(_BRAND_PAL)]};'
            f'border:1px solid {_BRAND_PAL[_i%len(_BRAND_PAL)]}35;padding:3px 10px;border-radius:12px;'
            f'font-size:0.70rem;font-weight:700;margin:2px;display:inline-block;">{_html_mod.escape(_b)} ×{_c}</span>'
            for _i,(_b,_c) in enumerate(_ana["brand_counts"].items()))
        _stat_p = (f'<span style="background:#f1f5f9;color:#374151;border:1px solid #e2e8f0;padding:3px 10px;border-radius:12px;font-size:0.68rem;font-weight:600;margin:2px;display:inline-block;">Vocab richness {_ana["vocab_richness"]}%</span>'
                   f'<span style="background:#f1f5f9;color:#374151;border:1px solid #e2e8f0;padding:3px 10px;border-radius:12px;font-size:0.68rem;font-weight:600;margin:2px;display:inline-block;">R turns {_ana["r_turns"]} · M turns {_ana["m_turns"]}</span>')
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;align-items:center;gap:2px;margin-top:8px;">'
            f'<span style="font-size:0.60rem;font-weight:700;color:#9ca3af;text-transform:uppercase;margin-right:4px;">Brands:</span>'
            + (_bm_p or f'<span style="font-size:0.70rem;color:#9ca3af;">None detected</span>')
            + f'<span style="flex:1;"></span>{_stat_p}</div>',
            unsafe_allow_html=True)

        st.markdown("<div style='margin:8px 0;height:1px;background:linear-gradient(90deg,#7c3aed30,transparent);'></div>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # EXTRACTION DASHBOARD
    # ══════════════════════════════════════════════════════════════════════════
    _pir = st.session_state["pi_result"]
    if _pir:
        _prs  = _pir.get("parsed")
        _praw = _pir.get("raw","")
        _dims = _pir.get("dims",[])
        _pfn  = _pir.get("fname","")
        _km   = {d["name"]:d["key"] for d in _PI_DIMS}

        if not _prs:
            st.error("Could not parse JSON from API response.")
            with st.expander("Raw response", expanded=True): st.code(_praw[:3000], language="text")
            st.caption("Free models sometimes add prose. Try Rebuild → re-run.")
        else:
            # ── Respondent header ─────────────────────────────────────────────
            _resp   = _prs.get("respondent") or {}
            _brand  = str(_resp.get("brand_owned") or "—")
            _city   = str(_resp.get("city") or "—")
            _jrny   = str(_resp.get("journey_stage") or "—").replace("_"," ")
            _usage  = str(_resp.get("usage_context") or "—").replace("_"," ")
            _hh     = str(_resp.get("household_size") or "—").replace("_"," ")
            _nps    = str(_prs.get("nps_signal") or "unclear")
            _emor   = str(_prs.get("emotional_resolution") or "neutral")
            _br     = (_prs.get("brand_relationship") or {}) if isinstance(_prs.get("brand_relationship"),dict) else {}
            _relr   = str(_br.get("relationship_stage") or "—").replace("_"," ")
            _brc    = _PI_REL_C.get(_relr,"#94a3b8")
            _npsc   = {"promoter":_P["green"],"passive":_P["amber"],"detractor":_P["red"]}.get(_nps,"#94a3b8")
            _emoc   = {"positive":_P["green"],"negative":_P["red"],"neutral":"#94a3b8"}.get(_emor,"#94a3b8")
            _bbc    = _BRAND_PAL[abs(hash(_brand))%len(_BRAND_PAL)] if _brand!="—" else _P["purple"]
            _scores = _pi_signal_scores(_prs)

            st.markdown(
                f'<div style="background:linear-gradient(135deg,{_bbc}10,{_bbc}03);border:1.5px solid {_bbc}25;border-radius:12px;padding:13px 18px;margin-bottom:10px;">'
                f'<div style="font-size:0.56rem;font-weight:800;color:{_bbc};text-transform:uppercase;letter-spacing:0.14em;margin-bottom:4px;">Extraction Complete · {_html_mod.escape(_pfn)}</div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">'
                f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">'
                f'<span style="background:{_bbc};color:white;padding:4px 15px;border-radius:18px;font-size:0.95rem;font-weight:900;">{_html_mod.escape(_brand)}</span>'
                f'<span style="font-size:0.78rem;color:#6b7280;">📍 {_html_mod.escape(_city)}</span>'
                f'<span style="font-size:0.75rem;color:#374151;">{_html_mod.escape(_jrny)}</span>'
                f'<span style="color:#d1d5db;">·</span><span style="font-size:0.75rem;color:#374151;">{_html_mod.escape(_usage)}</span>'
                f'<span style="color:#d1d5db;">·</span><span style="font-size:0.75rem;color:#374151;">HH {_html_mod.escape(_hh)}</span>'
                f'</div>'
                f'<div style="display:flex;gap:5px;">'
                + "".join(f'<div style="background:{_c}10;border:1.5px solid {_c}28;border-radius:8px;padding:5px 11px;text-align:center;min-width:58px;">'
                          f'<div style="font-size:0.72rem;font-weight:800;color:{_c};">{_html_mod.escape(str(_v))}</div>'
                          f'<div style="font-size:0.55rem;color:#9ca3af;text-transform:uppercase;font-weight:600;">{_l}</div></div>'
                          for _v,_c,_l in [(_nps,_npsc,"NPS"),(_relr.split()[0].title() if _relr!="—" else "—",_brc,"Rel."),(_emor.title(),_emoc,"Emotion")])
                + f'</div></div></div>', unsafe_allow_html=True)

            # ── 3 signal scores ───────────────────────────────────────────────
            _bd    = _pi_score_breakdown(_prs)
            _corp  = _pi_load_corpus()
            _has_c = _corp.get("n_docs", 0) > 0
            _s1,_s2,_s3 = st.columns(3)
            for _col,_lbl,_score,_desc,_pal,_lo,_hi,_avg_key,_lst_key,_hib in [
                (_s1,"Satisfaction",_scores["satisfaction"],"NPS + emotional arc + relationship",
                 _P["green"],"At risk","Loyal","avg_sat","sat_c",True),
                (_s2,"Risk",_scores["risk"],"High pain + product blame + switching",
                 _P["red"],"Safe","Critical","avg_risk","risk_c",False),
                (_s3,"Opportunity",_scores["opportunity"],"High-opp gaps + unspoken needs",
                 _P["purple"],"Low","High","avg_opp","opp_c",True),
            ]:
                with _col:
                    _sc2   = _pal if _score>=50 else (_P["amber"] if _score>=25 else "#94a3b8")
                    _c_avg = _corp.get(_avg_key, 0) if _has_c else None
                    _c_lst = _corp.get(_lst_key, []) if _has_c else []
                    _c_pct = _pi_pct(_score, _c_lst) if _c_lst else None
                    _delta, _dc = _pi_delta_badge(_score, _c_avg or 0, _hib) if _c_avg else ("","")
                    _pct_label  = f"{_c_pct}th percentile" if _c_pct is not None else ""

                    st.markdown(
                        f'<div style="background:{_sc2}07;border:1.5px solid {_sc2}25;border-radius:12px;padding:13px;text-align:center;margin-bottom:6px;">'
                        f'<div style="font-size:0.58rem;font-weight:800;color:#9ca3af;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px;">{_lbl}</div>'
                        f'<div style="font-size:2.0rem;font-weight:900;color:{_sc2};line-height:1;">{_score}</div>'
                        f'<div style="font-size:0.58rem;color:#9ca3af;">/100</div>'
                        f'<div style="background:#e5e7eb;border-radius:3px;height:5px;margin:6px 0;">'
                        f'<div style="width:{_score}%;background:{_sc2};height:100%;border-radius:3px;"></div></div>'
                        f'<div style="display:flex;justify-content:space-between;font-size:0.54rem;color:#9ca3af;margin-bottom:4px;"><span>{_lo}</span><span>{_hi}</span></div>'
                        # Corpus comparison line
                        + (f'<div style="display:flex;justify-content:center;gap:8px;flex-wrap:wrap;margin-bottom:4px;">'
                           f'<span style="font-size:0.62rem;color:#6b7280;">corp avg <b>{_c_avg}</b></span>'
                           f'<span style="font-size:0.62rem;font-weight:700;color:{_dc};">{_delta}</span>'
                           f'<span style="font-size:0.60rem;background:{_sc2}18;color:{_sc2};padding:1px 5px;border-radius:5px;">{_pct_label}</span>'
                           f'</div>' if _c_avg else "")
                        + f'<div style="font-size:0.58rem;color:#6b7280;line-height:1.3;">{_desc}</div>'
                        f'</div>', unsafe_allow_html=True)

                    # Calculation breakdown expander
                    with st.expander("≡ How calculated", expanded=False):
                        _bk_rows = _bd.get(_lbl.lower(), [])
                        for _factor, _val2, _pts, _note in _bk_rows:
                            _is_tot = "TOTAL" in _factor
                            st.markdown(
                                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                                f'padding:4px 0;border-bottom:1px solid #f1f5f9;">'
                                f'<div style="font-size:0.68rem;color:{"#111827" if _is_tot else "#6b7280"};font-weight:{"800" if _is_tot else "400"};">'
                                f'{_html_mod.escape(_factor)}'
                                + (f'<br><span style="font-size:0.58rem;color:#9ca3af;">{_html_mod.escape(_val2)}</span>' if _val2 else "")
                                + f'</div>'
                                f'<div style="text-align:right;">'
                                f'<div style="font-size:0.78rem;font-weight:900;color:{_sc2 if _is_tot else "#374151"};">+{_pts}</div>'
                                f'<div style="font-size:0.56rem;color:#9ca3af;">{_html_mod.escape(_note)}</div>'
                                f'</div></div>',
                                unsafe_allow_html=True)

            # ── Emotional story arc ───────────────────────────────────────────
            _eac = [e for e in (_prs.get("emotional_arc") or []) if isinstance(e,dict)]
            if _eac:
                st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:7px;">Emotional Story Arc</div>', unsafe_allow_html=True)
                _arc_s = sorted(_eac[:7], key=lambda x:{"early":0,"mid":1,"late":2}.get(str(x.get("moment","")),1))
                _arc_cols = st.columns(len(_arc_s)) if len(_arc_s)>=2 else st.columns(1)
                for _ai,_em in enumerate(_arc_s):
                    with _arc_cols[_ai]:
                        _en  = str(_em.get("emotion","?"))
                        _val = str(_em.get("valence","neutral"))
                        _mom = str(_em.get("moment","?"))
                        _trg = str(_em.get("trigger",""))[:75]
                        _eq  = str(_em.get("verbatim_quote",""))[:90]
                        _ec  = _PI_EMO_C.get(_en,"#94a3b8")
                        _vc  = {"positive":_P["green"],"negative":_P["red"]}.get(_val,"#94a3b8")
                        try: _bar = min(10,int(_em.get("arousal_intensity",5)))*10
                        except: _bar = 50
                        st.markdown(
                            f'<div style="background:{_ec}0b;border:1.5px solid {_ec}28;border-radius:9px;padding:9px;">'
                            f'<div style="font-size:0.54rem;color:#9ca3af;font-weight:700;text-transform:uppercase;margin-bottom:2px;">{_html_mod.escape(_mom)}</div>'
                            f'<div style="font-size:0.80rem;font-weight:800;color:{_ec};margin-bottom:3px;">{_html_mod.escape(_en)}</div>'
                            f'<div style="display:flex;align-items:center;gap:3px;margin-bottom:3px;">'
                            f'<span style="background:{_vc}18;color:{_vc};padding:1px 4px;border-radius:3px;font-size:0.54rem;font-weight:700;">{_html_mod.escape(_val)}</span>'
                            f'<div style="flex:1;background:#e5e7eb;border-radius:2px;height:4px;">'
                            f'<div style="width:{_bar}%;background:{_ec};height:100%;border-radius:2px;"></div></div></div>'
                            + (f'<div style="font-size:0.65rem;color:#6b7280;line-height:1.3;margin-bottom:2px;">{_html_mod.escape(_trg)}</div>' if _trg else "")
                            + (f'<div style="font-size:0.62rem;color:#9ca3af;font-style:italic;border-top:1px dashed #e2e8f0;padding-top:2px;margin-top:2px;">&ldquo;{_html_mod.escape(_clean_quote(_eq))}&rdquo;</div>' if _eq else "")
                            + f'</div>', unsafe_allow_html=True)
                _peak = _prs.get("peak_emotional_moment") or {}
                if isinstance(_peak,dict) and _peak.get("emotion"):
                    _pkc = _PI_EMO_C.get(_peak.get("emotion",""),_P["purple"])
                    st.markdown(
                        f'<div style="background:{_pkc}0a;border:1.5px solid {_pkc}30;border-radius:9px;'
                        f'padding:9px 12px;margin-top:6px;display:flex;gap:12px;align-items:flex-start;">'
                        f'<span style="font-size:0.60rem;font-weight:800;color:{_pkc};text-transform:uppercase;white-space:nowrap;padding-top:2px;">★ Peak</span>'
                        f'<div style="flex:1;">'
                        f'<span style="font-size:0.78rem;font-weight:700;color:{_pkc};">{_html_mod.escape(str(_peak.get("emotion","")))}</span>'
                        + (f' <span style="font-size:0.70rem;color:#6b7280;"> — {_html_mod.escape(str(_peak.get("trigger",""))[:100])}</span>' if _peak.get("trigger") else "")
                        + (f'<div style="font-size:0.72rem;color:#9ca3af;font-style:italic;margin-top:3px;">&ldquo;{_html_mod.escape(_clean_quote(str(_peak.get("verbatim_quote",""))[:160]))}&rdquo;</div>' if _peak.get("verbatim_quote") else "")
                        + f'</div></div>', unsafe_allow_html=True)

            # ── Evidence tabs ─────────────────────────────────────────────────
            st.markdown("<div style='margin:12px 0 0;'></div>", unsafe_allow_html=True)
            _tab_pain, _tab_opp, _tab_voice, _tab_ident, _tab_qual, _tab_corpus, _tab_text = st.tabs([
                "⚠ Pain & Risk","💡 Opportunities","💬 Voice","🪞 Identity & Culture",
                "✅ Quality","📊 vs Corpus","🔍 Text Breakdown"])

            # — Pain & Risk —
            with _tab_pain:
                _pns  = [p for p in (_prs.get("pain_points") or []) if isinstance(p,dict)]
                _sbl  = _prs.get("self_blame_instances") or []
                _pbl  = _prs.get("product_blame_instances") or []
                _bswt = _br.get("switching_consideration",False)
                _badv = str(_br.get("advocacy_likelihood","")).replace("_"," ")
                _bten = str(_br.get("unresolved_tension") or "")
                _pp1,_pp2 = st.columns([1.3,1])
                with _pp1:
                    st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:{_P["red"]};text-transform:uppercase;margin-bottom:7px;">Pain Points ({len(_pns)})</div>', unsafe_allow_html=True)
                    _svm = {"high":"#dc2626","medium":_P["amber"],"low":"#94a3b8"}
                    for _pp in sorted(_pns, key=lambda x:{"high":3,"medium":2,"low":1}.get(x.get("severity","low"),0), reverse=True)[:6]:
                        _psc = _svm.get(_pp.get("severity","low"),"#94a3b8")
                        _pq  = _clean_quote(str(_pp.get("verbatim_quote",""))[:120])
                        st.markdown(
                            f'<div style="border-left:3px solid {_psc};background:{_psc}07;border-radius:0 8px 8px 0;padding:7px 10px;margin-bottom:6px;">'
                            f'<div style="display:flex;gap:5px;align-items:center;margin-bottom:2px;">'
                            f'<span style="background:{_psc};color:white;padding:1px 6px;border-radius:7px;font-size:0.55rem;font-weight:800;text-transform:uppercase;">{_html_mod.escape(_pp.get("severity","?"))}</span>'
                            f'<span style="font-size:0.60rem;color:#9ca3af;">{_html_mod.escape(str(_pp.get("product_area","?")))}</span></div>'
                            f'<div style="font-size:0.78rem;color:#1f2937;font-weight:600;line-height:1.4;">{_html_mod.escape(str(_pp.get("issue_description",""))[:120])}</div>'
                            + (f'<div style="font-size:0.68rem;color:#6b7280;font-style:italic;margin-top:3px;border-top:1px dashed #e2e8f0;padding-top:2px;">&ldquo;{_html_mod.escape(_pq)}&rdquo;</div>' if _pq else "")
                            + f'</div>', unsafe_allow_html=True)
                    if not _pns: st.caption("Enable 'Pain Points' dimension.")
                with _pp2:
                    st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:{_P["purple"]};text-transform:uppercase;margin-bottom:7px;">Risk Signals</div>', unsafe_allow_html=True)
                    _bt = len(_sbl)+len(_pbl)
                    if _bt:
                        _sbp = round(len(_sbl)/_bt*100)
                        st.markdown(
                            f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:9px;padding:11px 13px;margin-bottom:8px;">'
                            f'<div style="font-size:0.60rem;font-weight:700;color:#374151;margin-bottom:5px;">Blame Attribution ({_bt} instances)</div>'
                            f'<div style="display:flex;height:10px;border-radius:5px;overflow:hidden;margin-bottom:4px;">'
                            f'<div style="width:{_sbp}%;background:{_P["purple"]};"></div>'
                            f'<div style="width:{100-_sbp}%;background:{_P["red"]};"></div></div>'
                            f'<div style="display:flex;justify-content:space-between;font-size:0.65rem;">'
                            f'<span style="color:{_P["purple"]};">Self {_sbp}% ({len(_sbl)})</span>'
                            f'<span style="color:{_P["red"]};">Product {100-_sbp}% ({len(_pbl)})</span></div>'
                            f'<div style="font-size:0.58rem;color:#9ca3af;margin-top:4px;">High self-blame = brand education gap</div>'
                            f'</div>', unsafe_allow_html=True)
                    _swc = _P["red"] if _bswt else _P["green"]
                    _avc = _P["red"] if "wont" in _badv else (_P["amber"] if "might" in _badv else _P["green"])
                    for _rl,_rv,_rc2 in [("Switching risk","YES — churn likely" if _bswt else "No intent",_swc),("Advocacy",_badv.title() or "—",_avc)]:
                        st.markdown(f'<div style="background:{_rc2}07;border:1px solid {_rc2}25;border-radius:8px;padding:8px 11px;margin-bottom:6px;"><div style="font-size:0.58rem;color:#9ca3af;font-weight:700;text-transform:uppercase;">{_rl}</div><div style="font-size:0.80rem;font-weight:800;color:{_rc2};">{_html_mod.escape(_rv)}</div></div>', unsafe_allow_html=True)
                    if _bten:
                        st.markdown(f'<div style="background:#fff7ed;border-left:3px solid {_P["amber"]};border-radius:0 8px 8px 0;padding:8px 11px;"><div style="font-size:0.58rem;color:{_P["amber"]};font-weight:800;text-transform:uppercase;margin-bottom:2px;">Unresolved tension</div><div style="font-size:0.76rem;color:#374151;">{_html_mod.escape(_bten[:140])}</div></div>', unsafe_allow_html=True)

            # — Opportunities —
            with _tab_opp:
                _gps  = [g for g in (_prs.get("aspiration_reality_gaps") or []) if isinstance(g,dict)]
                _unsp = [u for u in (_prs.get("unspoken_needs") or []) if u]
                _jtbd = [j for j in (_prs.get("jobs_to_be_done") or []) if j]
                _opc2 = {"high":_P["red"],"medium":_P["amber"],"low":"#94a3b8"}
                if _gps:
                    st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:{_P["amber"]};text-transform:uppercase;margin-bottom:7px;">Aspiration Gaps ({len(_gps)})</div>', unsafe_allow_html=True)
                    for _ag in sorted(_gps, key=lambda x:{"high":3,"medium":2,"low":1}.get(x.get("commercial_opportunity","low"),0), reverse=True):
                        _oc2 = _opc2.get(_ag.get("commercial_opportunity","low"),"#94a3b8")
                        _aq  = _clean_quote(str(_ag.get("verbatim_quote",""))[:130])
                        _wk  = str(_ag.get("workaround",""))
                        st.markdown(
                            f'<div style="border:1px solid {_oc2}25;border-left:3px solid {_oc2};border-radius:0 9px 9px 0;padding:9px 12px;margin-bottom:7px;background:{_oc2}04;">'
                            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:3px;">'
                            f'<div style="font-size:0.86rem;font-weight:700;color:#111827;line-height:1.3;flex:1;">{_html_mod.escape(str(_ag.get("aspiration",""))[:140])}</div>'
                            f'<span style="background:{_oc2}18;color:{_oc2};padding:2px 7px;border-radius:7px;font-size:0.58rem;font-weight:800;text-transform:uppercase;margin-left:8px;white-space:nowrap;">{_html_mod.escape(_ag.get("commercial_opportunity","?"))} opp</span></div>'
                            f'<div style="font-size:0.70rem;color:#6b7280;margin-bottom:3px;">Reality: {_html_mod.escape(str(_ag.get("current_reality",""))[:100])}</div>'
                            + (f'<div style="font-size:0.68rem;color:{_P["teal"]};">↳ {_html_mod.escape(_wk[:80])}</div>' if _wk else "")
                            + (f'<div style="font-size:0.72rem;color:#9ca3af;font-style:italic;margin-top:3px;">&ldquo;{_html_mod.escape(_aq)}&rdquo;</div>' if _aq else "")
                            + f'</div>', unsafe_allow_html=True)
                else: st.caption("Enable 'Aspiration-Reality Gaps' dimension.")
                if _unsp:
                    st.markdown(
                        f'<div style="background:linear-gradient(90deg,{_P["teal"]}08,transparent);border-left:3px solid {_P["teal"]};border-radius:0 9px 9px 0;padding:9px 13px;margin-top:8px;">'
                        f'<div style="font-size:0.60rem;font-weight:800;color:{_P["teal"]};text-transform:uppercase;margin-bottom:5px;">Unspoken Needs ({len(_unsp)})</div>'
                        f'<div style="display:flex;flex-wrap:wrap;gap:5px;">'
                        + "".join(f'<span style="background:{_P["teal"]}10;color:{_P["teal"]};border:1px solid {_P["teal"]}25;padding:4px 11px;border-radius:13px;font-size:0.73rem;">{_html_mod.escape(str(_u)[:80])}</span>' for _u in _unsp[:8])
                        + f'</div></div>', unsafe_allow_html=True)
                if _jtbd:
                    st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:{_P["blue"]};text-transform:uppercase;margin:10px 0 5px;">Jobs to Be Done ({len(_jtbd)})</div>', unsafe_allow_html=True)
                    for _j in _jtbd[:6]: st.markdown(f'<div style="font-size:0.78rem;color:#374151;padding:4px 0 4px 10px;border-left:2px solid {_P["blue"]}30;">→ {_html_mod.escape(str(_j)[:120])}</div>', unsafe_allow_html=True)

            # — Voice —
            with _tab_voice:
                _pss = [p for p in (_prs.get("all_passages") or []) if isinstance(p,dict) and p.get("content","")]
                if not _pss: st.info("Enable 'All Passages' dimension to see verbatim quotes.")
                else:
                    _posq = sorted([p for p in _pss if p.get("sentiment")=="positive"], key=lambda x:len(x.get("content","")),reverse=True)[:4]
                    _negq = sorted([p for p in _pss if p.get("sentiment")=="negative"], key=lambda x:len(x.get("content","")),reverse=True)[:4]
                    _paiq = sorted([p for p in _pss if p.get("pain_point")], key=lambda x:len(x.get("content","")),reverse=True)[:3]
                    _decq = sorted([p for p in _pss if p.get("decision_signal")], key=lambda x:len(x.get("content","")),reverse=True)[:3]
                    _vc1,_vc2 = st.columns(2)
                    for _ql,_vcol,_lbl,_qc,_bg in [(_posq,_vc1,"▲ Positive",_P["green"],"#f0fdf4"),(_negq,_vc2,"▼ Critical",_P["red"],"#fef2f2")]:
                        with _vcol:
                            st.markdown(f'<div style="font-size:0.60rem;font-weight:800;color:{_qc};text-transform:uppercase;margin-bottom:6px;">{_lbl} ({len(_ql)})</div>', unsafe_allow_html=True)
                            for _q in _ql:
                                _tp = str(_q.get("topic","")).replace("_"," ")
                                st.markdown(
                                    f'<div style="border-left:3px solid {_qc};background:{_bg};border-radius:0 9px 9px 0;padding:9px 12px;margin-bottom:7px;">'
                                    f'<div style="font-family:Georgia,serif;font-size:0.82rem;color:#1f2937;line-height:1.72;">&ldquo;{_html_mod.escape(_clean_quote(str(_q.get("content",""))[:280]))}&rdquo;</div>'
                                    + (f'<div style="font-size:0.58rem;color:#9ca3af;margin-top:3px;">{_html_mod.escape(_tp)}</div>' if _tp else "")
                                    + f'</div>', unsafe_allow_html=True)
                    if _decq or _paiq:
                        _vc3,_vc4 = st.columns(2)
                        for _ql,_vcol,_lbl,_qc,_bg in [(_decq,_vc3,"→ Decision signals",_P["blue"],"#f0f9ff"),(_paiq,_vc4,"⚠ Pain signals",_P["amber"],"#fffbeb")]:
                            with _vcol:
                                if _ql:
                                    st.markdown(f'<div style="font-size:0.60rem;font-weight:800;color:{_qc};text-transform:uppercase;margin-bottom:6px;">{_lbl}</div>', unsafe_allow_html=True)
                                    for _q in _ql:
                                        st.markdown(f'<div style="border-left:3px solid {_qc};background:{_bg};border-radius:0 9px 9px 0;padding:9px 12px;margin-bottom:7px;font-family:Georgia,serif;font-size:0.80rem;color:#1f2937;line-height:1.72;">&ldquo;{_html_mod.escape(_clean_quote(str(_q.get("content",""))[:250]))}&rdquo;</div>', unsafe_allow_html=True)

            # — Identity & Culture —
            with _tab_ident:
                _ids = (_prs.get("identity_signals") or {}) if isinstance(_prs.get("identity_signals"),dict) else {}
                _cc  = (_prs.get("cultural_context") or {}) if isinstance(_prs.get("cultural_context"),dict) else {}
                _sd  = (_prs.get("social_dynamics") or {}) if isinstance(_prs.get("social_dynamics"),dict) else {}
                _ic1,_ic2,_ic3 = st.columns(3)
                with _ic1:
                    if _ids:
                        _si  = str(_ids.get("cook_self_image","—"))
                        _sic = _PI_SIC.get(_si,"#94a3b8")
                        _cf  = str(_ids.get("competence_framing") or "")
                        _aia = str(_ids.get("appliance_as_identity","—"))
                        _ac  = {"high":_P["purple"],"medium":_P["teal"],"low":"#94a3b8"}.get(_aia,"#94a3b8")
                        st.markdown(
                            f'<div style="background:{_sic}07;border:1px solid {_sic}25;border-radius:10px;padding:11px 13px;">'
                            f'<div style="font-size:0.58rem;font-weight:800;color:#9ca3af;text-transform:uppercase;margin-bottom:5px;">Cook Identity</div>'
                            f'<span style="background:{_sic};color:white;padding:3px 11px;border-radius:13px;font-size:0.75rem;font-weight:800;">{_html_mod.escape(_si.upper())}</span>'
                            f'<div style="font-size:0.68rem;color:#374151;line-height:1.7;margin-top:7px;">Role: <b>{_html_mod.escape(str(_ids.get("kitchen_role","—")).replace("_"," "))}</b><br>As identity: <b style="color:{_ac};">{_html_mod.escape(_aia)}</b></div>'
                            + (f'<div style="font-size:0.70rem;color:#6b7280;font-style:italic;border-top:1px dashed #e2e8f0;margin-top:6px;padding-top:5px;">&ldquo;{_html_mod.escape(_clean_quote(_cf[:150]))}&rdquo;</div>' if _cf else "")
                            + f'</div>', unsafe_allow_html=True)
                    else: st.caption("Enable 'Identity Signals' dimension.")
                with _ic2:
                    if _cc:
                        _ucs = _cc.get("primary_use_cases") or []
                        _ucp = "".join(f'<span style="background:{_P["cyan"]}12;color:{_P["cyan"]};border:1px solid {_P["cyan"]}22;padding:2px 7px;border-radius:9px;font-size:0.62rem;font-weight:600;margin:2px;display:inline-block;">{_html_mod.escape(str(_u).replace("_"," "))}</span>' for _u in (_ucs[:5] if isinstance(_ucs,list) else []))
                        st.markdown(
                            f'<div style="background:{_P["cyan"]}04;border:1px solid {_P["cyan"]}16;border-radius:10px;padding:11px 13px;">'
                            f'<div style="font-size:0.58rem;font-weight:800;color:#9ca3af;text-transform:uppercase;margin-bottom:5px;">Cultural Context</div>'
                            f'<div style="font-size:0.68rem;color:#374151;line-height:1.75;">Region: <b>{_html_mod.escape(str(_cc.get("regional_food_culture","—")).replace("_"," "))}</b><br>Tradition: <b>{_html_mod.escape(str(_cc.get("traditional_vs_modern","—")).replace("_"," "))}</b><br>Values: <b>{_html_mod.escape(str(_cc.get("family_food_values","—")).replace("_"," "))}</b><br>Time pressure: <b>{_html_mod.escape(str(_cc.get("time_pressure_type","—")).replace("_"," "))}</b><br>Event cooking: <b>{"Yes" if _cc.get("event_cooking") else "No"}</b></div>'
                            + (f'<div style="margin-top:5px;display:flex;flex-wrap:wrap;">{_ucp}</div>' if _ucp else "")
                            + f'</div>', unsafe_allow_html=True)
                    else: st.caption("Enable 'Cultural Context' dimension.")
                with _ic3:
                    if _sd:
                        _spr = str(_sd.get("social_proof_reliance","—"))
                        _spc = {"high":_P["red"],"medium":_P["amber"],"low":_P["green"]}.get(_spr,"#94a3b8")
                        _inf = _sd.get("influencer_network") or []
                        st.markdown(
                            f'<div style="background:{_P["emerald"]}04;border:1px solid {_P["emerald"]}16;border-radius:10px;padding:11px 13px;">'
                            f'<div style="font-size:0.58rem;font-weight:800;color:#9ca3af;text-transform:uppercase;margin-bottom:5px;">Social Dynamics</div>'
                            f'<div style="font-size:0.68rem;color:#374151;line-height:1.75;">Household: <b>{_html_mod.escape(str(_sd.get("household_type","—")))}</b><br>Decision: <b>{_html_mod.escape(str(_sd.get("purchase_decision_maker","—")).replace("_"," "))}</b><br>Cooking: <b>{_html_mod.escape(str(_sd.get("cooking_labor","—")).replace("_"," "))}</b><br>Social proof: <b style="color:{_spc};">{_html_mod.escape(_spr)}</b></div>'
                            + (f'<div style="margin-top:5px;font-size:0.60rem;color:#9ca3af;display:flex;flex-wrap:wrap;gap:3px;">' + "".join(f'<span style="background:#f1f5f9;border-radius:4px;padding:1px 5px;">{_html_mod.escape(str(_inf2.get("relation","?")))} · {_html_mod.escape(str(_inf2.get("influence_type","?"))[:15])}</span>' for _inf2 in (_inf[:3] if isinstance(_inf,list) else []) if isinstance(_inf2,dict)) + f'</div>' if isinstance(_inf,list) and _inf else "")
                            + f'</div>', unsafe_allow_html=True)
                    else: st.caption("Enable 'Social Dynamics' dimension.")

            # — Quality & Actions —
            with _tab_qual:
                _fd = [d for d in _dims if any(k in _prs for k in _PI_COMPOUND.get(_km.get(d,""),[_km.get(d,"")]))]
                _md = [d for d in _dims if d not in _fd]
                _cp = round(len(_fd)/max(len(_dims),1)*100)
                _cc2 = _P["green"] if _cp>=90 else _P["amber"] if _cp>=65 else _P["red"]
                st.markdown(f'<div style="background:{_cc2}07;border-left:4px solid {_cc2};border-radius:0 9px 9px 0;padding:9px 15px;margin-bottom:10px;display:flex;align-items:center;gap:14px;"><span style="font-size:2rem;font-weight:900;color:{_cc2};">{_cp}%</span><span style="font-size:0.78rem;color:#374151;">{len(_fd)}/{len(_dims)} dimensions extracted</span>' + (f'<span style="font-size:0.70rem;color:{_P["red"]};">Missing: {", ".join(_md)}</span>' if _md else "") + f'</div>', unsafe_allow_html=True)
                _qcols = st.columns(4)
                for _qi,_qd in enumerate(_dims):
                    _qk = _km.get(_qd,""); _qks = _PI_COMPOUND.get(_qk,[_qk])
                    _qv = next((_prs.get(k) for k in _qks if k in _prs), None)
                    if isinstance(_qv,list): _qs,_qdt = min(100,len(_qv)*20) if _qv else 0, f"{len(_qv)} items"
                    elif isinstance(_qv,dict): _f=sum(1 for v in _qv.values() if v and v not in("—",None)); _qs,_qdt=round(_f/max(len(_qv),1)*100),f"{_f}/{len(_qv)} fields"
                    elif _qv: _qs,_qdt = 100, str(_qv)[:20]
                    else: _qs,_qdt = 0, "not extracted"
                    _qc = _P["green"] if _qs>=80 else _P["amber"] if _qs>=40 else _P["red"]
                    with _qcols[_qi%4]:
                        st.markdown(f'<div style="border:1px solid {_qc}22;border-left:3px solid {_qc};border-radius:0 7px 7px 0;padding:6px 9px;margin-bottom:5px;"><div style="font-size:0.58rem;font-weight:700;color:#374151;margin-bottom:3px;">{_html_mod.escape(_qd)}</div><div style="display:flex;align-items:center;gap:4px;"><div style="flex:1;background:#f1f5f9;border-radius:2px;height:4px;"><div style="width:{_qs}%;background:{_qc};height:100%;border-radius:2px;"></div></div><span style="font-size:0.62rem;font-weight:700;color:{_qc};">{_qs}%</span></div><div style="font-size:0.56rem;color:#9ca3af;margin-top:2px;">{_html_mod.escape(_qdt)}</div></div>', unsafe_allow_html=True)
                st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)
                _qa1,_qa2,_qa3,_qa4 = st.columns(4)
                _qfn = st.session_state["pi_fname"].replace(".docx","_matrix.json")
                _qsp = _MATRICES_DIR / _qfn
                with _qa1: st.download_button("⬇ matrix.json", json.dumps(_prs,ensure_ascii=False,indent=2).encode(), file_name=_qfn, mime="application/json", key="pi_dl_json")
                with _qa2: st.download_button("⬇ prompt.txt", st.session_state.get("pi_prompt","").encode(), file_name="extraction_prompt.txt", mime="text/plain", key="pi_dl_prompt")
                with _qa3:
                    if st.button("💾 Save to corpus", key="pi_save", use_container_width=True, help=f"→ {_qsp}"):
                        try: _MATRICES_DIR.mkdir(parents=True,exist_ok=True); _qsp.write_text(json.dumps(_prs,ensure_ascii=False,indent=2),encoding="utf-8"); st.success(f"Saved → `{_qsp.name}`")
                        except Exception as _qe: st.error(str(_qe))
                with _qa4:
                    if st.button("🔄 Re-extract", key="pi_rerun", use_container_width=True):
                        st.session_state["pi_result"]=None; st.rerun()
                st.caption(f"Corpus → oxdata/data/qual_matrices/{_qfn} · Transcript Intelligence picks it up on next load.")

            # — vs Corpus —
            with _tab_corpus:
                _corp2 = _pi_load_corpus()
                _cn    = _corp2.get("n_docs", 0)
                if _cn == 0:
                    st.info("No corpus matrices found. Run `transcript_matrix_builder.py` to generate the 233 matrix files first.")
                else:
                    st.markdown(
                        f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;'
                        f'padding:10px 16px;margin-bottom:12px;font-size:0.78rem;color:#374151;">'
                        f'Corpus: <b>{_cn}</b> transcripts · brand distribution: '
                        + " · ".join(f'<b>{_html_mod.escape(b)}</b> {c}' for b,c in list(_corp2["brand_d"].items())[:6])
                        + f'</div>', unsafe_allow_html=True)

                    # ── Metric comparison table ───────────────────────────────
                    st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px;">Metric Comparison — This Transcript vs Corpus</div>', unsafe_allow_html=True)

                    _pns2   = [p for p in (_prs.get("pain_points") or []) if isinstance(p,dict)]
                    _gps2   = [g for g in (_prs.get("aspiration_reality_gaps") or []) if isinstance(g,dict)]
                    _sb2    = len(_prs.get("self_blame_instances") or [])
                    _pb2    = len(_prs.get("product_blame_instances") or [])
                    _un2    = len([u for u in (_prs.get("unspoken_needs") or []) if u])
                    _hpn2   = sum(1 for p in _pns2 if p.get("severity")=="high")
                    _hgp2   = sum(1 for g in _gps2 if g.get("commercial_opportunity")=="high")

                    _rows = [
                        ("Pain Points",       len(_pns2), _corp2["avg_pain"],    _corp2["pain_c"],    True),
                        ("High-Sev Pain",     _hpn2,      _corp2["avg_hi_pain"], _corp2["hi_pain_c"], True),
                        ("Aspiration Gaps",   len(_gps2), _corp2["avg_gaps"],    _corp2["gap_c"],     True),
                        ("High-Opp Gaps",     _hgp2,      _corp2["avg_hi_gaps"], _corp2["hi_gap_c"],  True),
                        ("Self-Blame Count",  _sb2,       _corp2["avg_sb"],      _corp2["sb_c"],      False),
                        ("Product Blame",     _pb2,       _corp2["avg_pb"],      _corp2["pb_c"],      False),
                        ("Unspoken Needs",    _un2,       _corp2["avg_unspk"],   _corp2["unspk_c"],   True),
                        ("Satisfaction Score",_scores["satisfaction"],_corp2["avg_sat"], _corp2["sat_c"],True),
                        ("Risk Score",        _scores["risk"],_corp2["avg_risk"],_corp2["risk_c"],    False),
                        ("Opportunity Score", _scores["opportunity"],_corp2["avg_opp"],_corp2["opp_c"],True),
                    ]

                    # Table header
                    _th1,_th2,_th3,_th4,_th5 = st.columns([2,1,1,1,2])
                    for _c,_t in zip([_th1,_th2,_th3,_th4,_th5],["Metric","This","Corp Avg","Percentile","Signal"]):
                        _c.markdown(f'<div style="font-size:0.60rem;font-weight:800;color:#9ca3af;text-transform:uppercase;">{_t}</div>', unsafe_allow_html=True)

                    for _metric,_val2,_avg2,_lst2,_hib in _rows:
                        _pct2  = _pi_pct(_val2, _lst2) if _lst2 else None
                        _dlt,_dc2 = _pi_delta_badge(_val2, _avg2, _hib)
                        _pct_c = _P["green"] if (_pct2 or 50)>=70 else (_P["amber"] if (_pct2 or 50)>=40 else _P["red"])
                        _r1,_r2,_r3,_r4,_r5 = st.columns([2,1,1,1,2])
                        with _r1: st.markdown(f'<div style="font-size:0.72rem;color:#374151;padding:3px 0;">{_metric}</div>', unsafe_allow_html=True)
                        with _r2: st.markdown(f'<div style="font-size:0.78rem;font-weight:800;color:#111827;padding:3px 0;">{_val2}</div>', unsafe_allow_html=True)
                        with _r3: st.markdown(f'<div style="font-size:0.72rem;color:#6b7280;padding:3px 0;">{_avg2}</div>', unsafe_allow_html=True)
                        with _r4:
                            if _pct2 is not None:
                                st.markdown(f'<div style="font-size:0.70rem;font-weight:700;color:{_pct_c};padding:3px 0;">{_pct2}th</div>', unsafe_allow_html=True)
                        with _r5:
                            if _dlt:
                                st.markdown(f'<div style="font-size:0.68rem;font-weight:600;color:{_dc2};padding:3px 0;">{_html_mod.escape(_dlt)}</div>', unsafe_allow_html=True)
                        st.markdown('<div style="border-bottom:1px solid #f1f5f9;"></div>', unsafe_allow_html=True)

                    # ── Distribution comparisons ──────────────────────────────
                    st.markdown("<div style='margin:14px 0 6px;'></div>", unsafe_allow_html=True)
                    _dc1, _dc2c, _dc3 = st.columns(3)

                    def _pi_dist_bars(col, title, this_val, dist_dict, color):
                        with col:
                            _total = max(sum(dist_dict.values()), 1)
                            col.markdown(f'<div style="font-size:0.60rem;font-weight:800;color:#374151;text-transform:uppercase;margin-bottom:6px;">{title}</div>', unsafe_allow_html=True)
                            for _k, _cnt in sorted(dist_dict.items(), key=lambda x: -x[1])[:6]:
                                _pct3 = round(_cnt/_total*100)
                                _is_this = _k.replace("_"," ") == this_val.replace("_"," ") or _k == this_val
                                _bc = color if _is_this else "#e5e7eb"
                                _tc = "white" if _is_this else "#9ca3af"
                                col.markdown(
                                    f'<div style="margin-bottom:4px;">'
                                    f'<div style="display:flex;justify-content:space-between;font-size:0.62rem;margin-bottom:1px;">'
                                    f'<span style="color:{"#111827" if _is_this else "#6b7280"};font-weight:{"700" if _is_this else "400"};">'
                                    f'{_html_mod.escape(str(_k).replace("_"," "))}'
                                    + (" ← you" if _is_this else "")
                                    + f'</span><span style="color:#9ca3af;">{_pct3}%</span></div>'
                                    f'<div style="background:#f1f5f9;border-radius:3px;height:6px;">'
                                    f'<div style="width:{_pct3}%;background:{_bc};height:100%;border-radius:3px;"></div></div>'
                                    f'</div>',
                                    unsafe_allow_html=True)

                    _pi_dist_bars(_dc1, "NPS Distribution", _nps, _corp2["nps_d"], _npsc)
                    _pi_dist_bars(_dc2c, "Relationship Stage", _relr.replace(" ","_"), _corp2["rel_d"], _brc)
                    _pi_dist_bars(_dc3, "Emotional Resolution", _emor, _corp2["emo_d"], _emoc)

                    st.markdown("<div style='margin:10px 0 6px;'></div>", unsafe_allow_html=True)
                    _dc4, _dc5 = st.columns(2)
                    _ids3 = _prs.get("identity_signals") or {}
                    _this_cook = str(_ids3.get("cook_self_image","") if isinstance(_ids3,dict) else "")
                    _pi_dist_bars(_dc4, "Cook Self-Image", _this_cook, _corp2.get("cook_d",{}), _PI_SIC.get(_this_cook,"#94a3b8"))

                    with _dc5:
                        # Pain count distribution histogram (inline bar)
                        _pc_lst = _corp2["pain_c"]
                        _this_p = len(_pns2)
                        st.markdown('<div style="font-size:0.60rem;font-weight:800;color:#374151;text-transform:uppercase;margin-bottom:6px;">Pain Count Distribution</div>', unsafe_allow_html=True)
                        _buckets = {}
                        for _v in _pc_lst: _buckets[_v] = _buckets.get(_v,0)+1
                        _bmax = max(_buckets.values(), default=1)
                        for _bv in sorted(_buckets.keys())[:10]:
                            _is_me = _bv == _this_p
                            _bw = round(_buckets[_bv]/_bmax*100)
                            st.markdown(
                                f'<div style="margin-bottom:3px;display:flex;align-items:center;gap:6px;">'
                                f'<span style="font-size:0.62rem;width:18px;text-align:right;color:{"#111827" if _is_me else "#9ca3af"};font-weight:{"800" if _is_me else "400"};">{_bv}</span>'
                                f'<div style="flex:1;background:#f1f5f9;border-radius:3px;height:7px;">'
                                f'<div style="width:{_bw}%;background:{"#dc2626" if _is_me else "#e5e7eb"};height:100%;border-radius:3px;"></div></div>'
                                f'<span style="font-size:0.58rem;color:#9ca3af;">{_buckets[_bv]}</span>'
                                + (" ← you" if _is_me else "")
                                + f'</div>',
                                unsafe_allow_html=True)

            # — Text Breakdown —
            with _tab_text:
                _pi_md2 = st.session_state.get("pi_md","")
                if not _pi_md2:
                    st.info("Upload a DOCX to see text-level breakdown.")
                else:
                    _ana2 = st.session_state.get("pi_analytics") or _pi_text_analytics(_pi_md2)

                    # Segment-by-segment analysis
                    st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px;">Segment Analysis — Topic Dominance Across Interview Timeline</div>', unsafe_allow_html=True)
                    _seg_topics = _pi_seg_topics(_pi_md2)
                    _arc_lbls2  = ["Early (0-20%)", "Early-mid (20-40%)", "Middle (40-60%)", "Late-mid (60-80%)", "Late (80-100%)"]
                    _arc_scores2 = _ana2["sentiment_curve"]
                    for _si2, (_seg_t, _arc_s2) in enumerate(zip(_seg_topics, _arc_scores2)):
                        _sc3 = _P["green"] if _arc_s2>=0.1 else (_P["red"] if _arc_s2<=-0.1 else "#94a3b8")
                        st.markdown(
                            f'<div style="border-left:3px solid {_sc3};background:{_sc3}07;border-radius:0 8px 8px 0;'
                            f'padding:7px 12px;margin-bottom:6px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">'
                            f'<div style="min-width:110px;">'
                            f'<div style="font-size:0.58rem;color:#9ca3af;font-weight:700;text-transform:uppercase;">{_arc_lbls2[_si2]}</div>'
                            f'<div style="font-size:0.75rem;font-weight:800;color:{_sc3};">sentiment {_arc_s2:+.2f}</div>'
                            f'</div>'
                            f'<div style="display:flex;gap:6px;flex-wrap:wrap;flex:1;">'
                            + ("".join(
                                f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:7px;padding:3px 8px;">'
                                f'<span style="font-size:0.62rem;font-weight:700;color:#374151;">{_html_mod.escape(_t)}</span> '
                                f'<span style="font-size:0.60rem;color:#9ca3af;">({len(_kws)} kws: {_html_mod.escape(", ".join(sorted(_kws)[:4]))})</span>'
                                f'</div>'
                                for _t, _kws in _seg_t) if _seg_t else '<span style="font-size:0.68rem;color:#9ca3af;">No strong topic keywords in this segment</span>')
                            + f'</div></div>',
                            unsafe_allow_html=True)

                    # Topic keyword detail
                    st.markdown("<div style='margin:12px 0 6px;'></div>", unsafe_allow_html=True)
                    st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:7px;">Topic Keyword Match Detail — Which Words Triggered Each Topic</div>', unsafe_allow_html=True)
                    _full_ws = set(re.findall(r'\b\w+\b', _pi_md2.lower()))
                    for _tp2, _kws2 in list(_TOPIC_KWORDS.items()):
                        _matched = sorted(_full_ws & _kws2)
                        if not _matched: continue
                        _bw2 = round(len(_matched)/max(len(_kws2),1)*100)
                        st.markdown(
                            f'<div style="border:1px solid #e2e8f0;border-radius:8px;padding:7px 11px;margin-bottom:5px;">'
                            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
                            f'<span style="font-size:0.72rem;font-weight:700;color:#374151;">{_html_mod.escape(_tp2)}</span>'
                            f'<div style="display:flex;align-items:center;gap:8px;">'
                            f'<div style="width:60px;background:#e5e7eb;border-radius:3px;height:5px;">'
                            f'<div style="width:{_bw2}%;background:{_P["teal"]};height:100%;border-radius:3px;"></div></div>'
                            f'<span style="font-size:0.60rem;color:#9ca3af;">{len(_matched)}/{len(_kws2)} matched</span>'
                            f'</div></div>'
                            f'<div style="display:flex;flex-wrap:wrap;gap:3px;">'
                            + "".join(f'<span style="background:{_P["teal"]}12;color:{_P["teal"]};border:1px solid {_P["teal"]}25;padding:1px 7px;border-radius:9px;font-size:0.62rem;">{_html_mod.escape(_w)}</span>' for _w in _matched)
                            + f'</div></div>',
                            unsafe_allow_html=True)

                    # Brand sentiment in text
                    st.markdown("<div style='margin:12px 0 6px;'></div>", unsafe_allow_html=True)
                    st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:7px;">Brand Context — Sentence-Level Sentiment Around Each Brand Mention</div>', unsafe_allow_html=True)
                    _sents = re.split(r'[.!?\n]+', _pi_md2)
                    for _bi2, (_br_name, _br_cnt) in enumerate(list(_ana2["brand_counts"].items())[:5]):
                        _br_sents = [(s.strip(), _sentiment(s)[0]) for s in _sents if _br_name.lower() in s.lower() and 5 < len(s.split()) < 60]
                        if not _br_sents: continue
                        _br_bc2 = _BRAND_PAL[_bi2%len(_BRAND_PAL)]
                        _pos_ct  = sum(1 for _,ss in _br_sents if ss=="Positive")
                        _neg_ct  = sum(1 for _,ss in _br_sents if ss=="Negative")
                        st.markdown(f'<div style="font-size:0.70rem;font-weight:800;color:{_br_bc2};margin-bottom:4px;">{_html_mod.escape(_br_name)} — {_br_cnt} mentions · {_pos_ct} positive · {_neg_ct} negative context sentences</div>', unsafe_allow_html=True)
                        for _sent_txt, _sent_sent in _br_sents[:4]:
                            _ssc = {"Positive":_P["green"],"Negative":_P["red"]}.get(_sent_sent,"#94a3b8")
                            st.markdown(
                                f'<div style="border-left:2px solid {_ssc};background:{_ssc}07;border-radius:0 6px 6px 0;'
                                f'padding:4px 9px;margin-bottom:3px;font-size:0.72rem;color:#374151;">'
                                f'{_html_mod.escape(_sent_txt[:140])}</div>',
                                unsafe_allow_html=True)

                    # Sentiment arc word-level detail
                    st.markdown("<div style='margin:12px 0 6px;'></div>", unsafe_allow_html=True)
                    st.markdown(f'<div style="font-size:0.62rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:7px;">Sentiment Arc Calculation — Positive &amp; Negative Words Per Segment</div>', unsafe_allow_html=True)
                    _seg_len2 = max(len(_pi_md2)//5, 1)
                    for _si3 in range(5):
                        _chunk2 = _pi_md2[_si3*_seg_len2:(_si3+1)*_seg_len2]
                        _cws    = set(re.findall(r'\b\w+\b', _chunk2.lower()))
                        _pos_w  = sorted(_cws & _POS)[:8]
                        _neg_w  = sorted(_cws & _NEG)[:8]
                        _arc_s3 = _arc_scores2[_si3]
                        _sc4    = _P["green"] if _arc_s3>=0.1 else (_P["red"] if _arc_s3<=-0.1 else "#94a3b8")
                        st.markdown(
                            f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:8px 11px;margin-bottom:5px;">'
                            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">'
                            f'<span style="font-size:0.60rem;font-weight:700;color:#9ca3af;min-width:70px;">{_arc_lbls2[_si3]}</span>'
                            f'<span style="font-size:0.72rem;font-weight:800;color:{_sc4};">score {_arc_s3:+.2f}</span>'
                            f'<span style="font-size:0.60rem;color:#9ca3af;">= ({len(_pos_w)} pos − {len(_neg_w)} neg) / ({len(_pos_w)}+{len(_neg_w)})</span>'
                            f'</div>'
                            f'<div style="display:flex;gap:10px;flex-wrap:wrap;">'
                            + (f'<div><span style="font-size:0.58rem;color:{_P["green"]};font-weight:700;">POSITIVE: </span>'
                               + " ".join(f'<span style="font-size:0.62rem;color:{_P["green"]};">{_html_mod.escape(_w)}</span>' for _w in _pos_w)
                               + f'</div>' if _pos_w else "")
                            + (f'<div><span style="font-size:0.58rem;color:{_P["red"]};font-weight:700;">NEGATIVE: </span>'
                               + " ".join(f'<span style="font-size:0.62rem;color:{_P["red"]};">{_html_mod.escape(_w)}</span>' for _w in _neg_w)
                               + f'</div>' if _neg_w else "")
                            + f'</div></div>',
                            unsafe_allow_html=True)

            # ── Raw JSON — single collapsed expander ──────────────────────────
            st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)
            with st.expander("🗃️  Raw extracted JSON  (all data · collapsed by default)", expanded=False):
                _fj = json.dumps(_prs, ensure_ascii=False, indent=2)
                st.markdown(
                    f'<div style="background:#0f0a1e;color:#c4b5fd;border-radius:10px;'
                    f'padding:13px 15px;font-family:monospace;font-size:0.73rem;'
                    f'line-height:1.6;white-space:pre-wrap;max-height:450px;overflow-y:auto;overflow-x:auto;">'
                    f'{_html_mod.escape(_fj[:12000])}'
                    + ("…" if len(_fj)>12000 else "")
                    + f'</div>', unsafe_allow_html=True)
                st.download_button("⬇ raw API response", _praw.encode(), file_name="raw_response.txt", mime="text/plain", key="pi_dl_raw")

    # ══════════════════════════════════════════════════════════════════════════
    # MULTI-FILE COMPARISON — appears automatically when >1 file extracted
    # ══════════════════════════════════════════════════════════════════════════
    _batch_done = [f for f in st.session_state["pi_batch"] if f.get("result") and f["result"].get("parsed")]
    if len(_batch_done) > 1:
        st.markdown("<div style='margin:16px 0 0;height:2px;background:linear-gradient(90deg,#7c3aed,transparent);'></div>", unsafe_allow_html=True)
        st.markdown(
            f'<div style="padding:10px 0 6px;">'
            f'<span style="font-size:0.95rem;font-weight:900;color:{_P["purple"]};">Multi-File Comparison</span>'
            f'<span style="font-size:0.72rem;color:#9ca3af;margin-left:10px;">{len(_batch_done)} files extracted · comparing side by side</span>'
            f'</div>', unsafe_allow_html=True)

        _cmp_tab1, _cmp_tab2, _cmp_tab3 = st.tabs(["📊 Signal Scores", "🗺 Topic Coverage", "🌊 Sentiment Arcs"])

        with _cmp_tab1:
            # Signal scores table + pain/gap counts
            _cmp_fnames = [f["fname"].replace(".docx","")[:20] for f in _batch_done]
            _cmp_scores = [_pi_signal_scores(f["result"]["parsed"]) for f in _batch_done]
            _cmp_pain   = [len([p for p in (f["result"]["parsed"].get("pain_points") or []) if isinstance(p,dict)]) for f in _batch_done]
            _cmp_hi_pain= [sum(1 for p in (f["result"]["parsed"].get("pain_points") or []) if isinstance(p,dict) and p.get("severity")=="high") for f in _batch_done]
            _cmp_gaps   = [len([g for g in (f["result"]["parsed"].get("aspiration_reality_gaps") or []) if isinstance(g,dict)]) for f in _batch_done]
            _cmp_hi_gaps= [sum(1 for g in (f["result"]["parsed"].get("aspiration_reality_gaps") or []) if isinstance(g,dict) and g.get("commercial_opportunity")=="high") for f in _batch_done]
            _cmp_brands = [str((f["result"]["parsed"].get("respondent") or {}).get("brand_owned") or "—") for f in _batch_done]
            _cmp_nps    = [str(f["result"]["parsed"].get("nps_signal") or "unclear") for f in _batch_done]
            _cmp_emor   = [str(f["result"]["parsed"].get("emotional_resolution") or "neutral") for f in _batch_done]
            _cmp_rel    = [str(((f["result"]["parsed"].get("brand_relationship") or {}).get("relationship_stage") or "—")).replace("_"," ") for f in _batch_done]

            # Header row
            _hdr_cols = st.columns([2] + [1]*len(_batch_done))
            _hdr_cols[0].markdown('<div style="font-size:0.60rem;font-weight:800;color:#9ca3af;text-transform:uppercase;">Metric</div>', unsafe_allow_html=True)
            for _ci2, _fn2 in enumerate(_cmp_fnames):
                _hdr_cols[_ci2+1].markdown(f'<div style="font-size:0.62rem;font-weight:800;color:{_BRAND_PAL[_ci2%len(_BRAND_PAL)]};text-align:center;word-break:break-all;">{_html_mod.escape(_fn2)}</div>', unsafe_allow_html=True)

            # Data rows
            def _cmp_row(label, values, colors=None, fmt=str, hib=True):
                _r_cols = st.columns([2] + [1]*len(_batch_done))
                _r_cols[0].markdown(f'<div style="font-size:0.70rem;color:#374151;padding:3px 0;">{label}</div>', unsafe_allow_html=True)
                _mn,_mx = min(values), max(values)
                for _ci3, _v3 in enumerate(values):
                    _is_best = (_v3==_mx if hib else _v3==_mn) and _mn!=_mx
                    _is_worst= (_v3==_mn if hib else _v3==_mx) and _mn!=_mx
                    _vc3 = _P["green"] if _is_best else (_P["red"] if _is_worst else "#374151")
                    _r_cols[_ci3+1].markdown(
                        f'<div style="text-align:center;font-size:0.78rem;font-weight:{"900" if _is_best or _is_worst else "500"};color:{_vc3};padding:3px 0;">'
                        f'{_html_mod.escape(fmt(_v3))}'
                        + (" ★" if _is_best else (" ▼" if _is_worst else ""))
                        + f'</div>', unsafe_allow_html=True)
                st.markdown('<div style="border-bottom:1px solid #f1f5f9;"></div>', unsafe_allow_html=True)

            for _row_lbl, _row_vals, _row_hib in [
                ("Satisfaction /100",  [s["satisfaction"] for s in _cmp_scores], True),
                ("Risk /100",          [s["risk"] for s in _cmp_scores], False),
                ("Opportunity /100",   [s["opportunity"] for s in _cmp_scores], True),
                ("Pain Points (total)",_cmp_pain,    False),
                ("High-Severity Pain", _cmp_hi_pain, False),
                ("Aspiration Gaps",    _cmp_gaps,    True),
                ("High-Opp Gaps",      _cmp_hi_gaps, True),
            ]:
                _cmp_row(_row_lbl, _row_vals, hib=_row_hib)

            # Categorical comparison
            st.markdown("<div style='margin:10px 0 6px;'></div>", unsafe_allow_html=True)
            for _cat_lbl, _cat_vals in [("Brand", _cmp_brands), ("NPS Signal", _cmp_nps),
                                         ("Emotional End", _cmp_emor), ("Relationship Stage", _cmp_rel)]:
                _cat_cols = st.columns([2] + [1]*len(_batch_done))
                _cat_cols[0].markdown(f'<div style="font-size:0.70rem;color:#374151;padding:3px 0;">{_cat_lbl}</div>', unsafe_allow_html=True)
                for _ci4, _cv in enumerate(_cat_vals):
                    _cat_cols[_ci4+1].markdown(f'<div style="text-align:center;font-size:0.68rem;color:#6b7280;padding:3px 0;">{_html_mod.escape(str(_cv))}</div>', unsafe_allow_html=True)
                st.markdown('<div style="border-bottom:1px solid #f1f5f9;"></div>', unsafe_allow_html=True)

        with _cmp_tab2:
            # Topic coverage matrix: topics × files
            _cmp_all_topics = list(_TOPIC_KWORDS.keys())
            _cmp_topic_hits = []
            for _fd3 in _batch_done:
                _fm3 = _fd3.get("md","")
                _fw3 = set(re.findall(r'\b\w+\b', _fm3.lower()))
                _cmp_topic_hits.append({t: len(_fw3 & kws) for t, kws in _TOPIC_KWORDS.items()})

            _th_cols = st.columns([2] + [1]*len(_batch_done))
            _th_cols[0].markdown('<div style="font-size:0.60rem;font-weight:800;color:#9ca3af;text-transform:uppercase;">Topic</div>', unsafe_allow_html=True)
            for _ci5, _fn5 in enumerate(_cmp_fnames):
                _th_cols[_ci5+1].markdown(f'<div style="font-size:0.60rem;font-weight:700;color:{_BRAND_PAL[_ci5%len(_BRAND_PAL)]};text-align:center;">{_html_mod.escape(_fn5[:14])}</div>', unsafe_allow_html=True)

            for _tp5 in _cmp_all_topics:
                _tp_vals = [_cmp_topic_hits[_ci5].get(_tp5, 0) for _ci5 in range(len(_batch_done))]
                _tp_max  = max(_tp_vals, default=1)
                if _tp_max == 0: continue
                _tp_cols = st.columns([2] + [1]*len(_batch_done))
                _tp_cols[0].markdown(f'<div style="font-size:0.68rem;color:#374151;padding:2px 0;">{_html_mod.escape(_tp5)}</div>', unsafe_allow_html=True)
                for _ci5, _tv5 in enumerate(_tp_vals):
                    _bc5 = _BRAND_PAL[_ci5 % len(_BRAND_PAL)]
                    _bw5 = round(_tv5/_tp_max*100) if _tp_max else 0
                    _tp_cols[_ci5+1].markdown(
                        f'<div style="padding:2px 4px;">'
                        f'<div style="background:#f1f5f9;border-radius:2px;height:6px;">'
                        f'<div style="width:{_bw5}%;background:{_bc5};height:100%;border-radius:2px;"></div></div>'
                        f'<div style="font-size:0.58rem;color:#9ca3af;text-align:center;">{_tv5}</div>'
                        f'</div>', unsafe_allow_html=True)
                st.markdown('<div style="border-bottom:1px solid #f8f8f8;"></div>', unsafe_allow_html=True)

        with _cmp_tab3:
            # Sentiment arc comparison: 5 segments per file stacked
            _arc_lbl5 = ["Early","25%","Mid","75%","Late"]
            def _arc_c5(s): return _P["green"] if s>=0.1 else (_P["red"] if s<=-0.1 else "#94a3b8")
            for _fi6, _fd6 in enumerate(_batch_done):
                _md6  = _fd6.get("md","")
                _ana6 = _fd6.get("analytics") or _pi_text_analytics(_md6)
                _arc6 = _ana6["sentiment_curve"]
                _bc6  = _BRAND_PAL[_fi6 % len(_BRAND_PAL)]
                _fname6 = _fd6["fname"].replace(".docx","")[:25]
                st.markdown(f'<div style="font-size:0.68rem;font-weight:700;color:{_bc6};margin-bottom:4px;">{_html_mod.escape(_fname6)}</div>', unsafe_allow_html=True)
                _arc6_cols = st.columns(5)
                for _si6, (_sc6, _al6) in enumerate(zip(_arc6, _arc_lbl5)):
                    with _arc6_cols[_si6]:
                        _cc6 = _arc_c5(_sc6)
                        st.markdown(
                            f'<div style="background:{_cc6};border-radius:5px;height:32px;display:flex;'
                            f'flex-direction:column;align-items:center;justify-content:center;margin-bottom:2px;">'
                            f'<span style="font-size:0.62rem;font-weight:800;color:white;">{_sc6:+.2f}</span>'
                            f'</div>'
                            f'<div style="font-size:0.56rem;color:#9ca3af;text-align:center;">{_al6}</div>',
                            unsafe_allow_html=True)
                # R-talk % and emotional density from text analytics
                st.markdown(
                    f'<div style="font-size:0.62rem;color:#9ca3af;margin-bottom:10px;">'
                    f'R-talk {_ana6["r_talk_pct"]}% · emo density {_ana6["emo_density"]}% · '
                    f'{_ana6["total_words"]:,} words · bilingual {_ana6["hindi_ratio"]}%</div>',
                    unsafe_allow_html=True)
