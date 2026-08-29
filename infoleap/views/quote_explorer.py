"""
Quote Explorer — InfoLeap Pulse
==================================
Tab 1  Survey Verbatims  — 6,623 bq2a responses with full demographics
Tab 2  Transcript Intelligence — 233 IDI transcripts, 8 named brands

Data rules (strict):
 - Min 15 verbatims per brand to appear in any chart
 - Category column is NULL this wave — filter hidden
 - Charts compute on full query set; quote browser paginates at 20/page
 - Transcript NULL-brand docs: acknowledged, not hidden
 - Keyword matching uses regex word boundaries (no substring false positives)
 - SQL ORDER BY fv.id — stable pagination across filter changes
 - NPS computed as (promoters% - detractors%) using nps_score thresholds
 - Transcript sentiment uses keyword-based analysis (pre-tagged data broken for some brands)
"""
import streamlit as st
import sqlite3
from datetime import datetime
import hashlib
import re
import html as _html_mod
import math
import asyncio
import json
import io
import os
import requests
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional
from collections import Counter, defaultdict
import pandas as pd
import plotly.graph_objects as go

try:
    from docx import Document as _DocxDocument
    _DOCX_AVAIL = True
except ImportError:
    _DOCX_AVAIL = False

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from infoleap.utils.ui_styles import (inject_pulse_styles, sidebar_context_block,
                                    COLORS, BRAND_COLORS, ZONE_COLORS, CHART_LAYOUT,
                                    signal_score_card, kpi_card)
from infoleap.db_loader import get_db_path
from infoleap.skills.qual_retriever import search_qual_trees, synthesize_qual_insights
from infoleap.skills.project_manager import ProjectManager

inject_pulse_styles()


def _load_json_safe(fpath, error_log: list) -> Optional[dict]:
    """2026-07-27: shared replacement for the ~14 sites in this file that did
    `try: json.loads(...); except Exception: pass/continue` — a malformed matrix (partial write,
    truncated LLM extraction output) used to vanish with zero trace anywhere. Now the caller
    collects (filename, error) pairs into its own `error_log` list and is responsible for
    surfacing them (st.warning near wherever the resulting count/list is shown) — this function
    only centralizes the try/except so every site behaves the same way, not what happens after.
    """
    try:
        return json.loads(fpath.read_text(encoding="utf-8"))
    except Exception as e:
        error_log.append((fpath.name, str(e)))
        return None


def _warn_json_errors(error_log: list, context: str = "matrix file(s)") -> None:
    """Consistent warning rendering for _load_json_safe's error_log — call once after a loop."""
    if not error_log:
        return
    st.warning(
        f"⚠️ {len(error_log)} {context} failed to parse and were skipped — counts below may be "
        f"undercounted. " +
        ", ".join(f"`{name}`" for name, _ in error_log[:5]) +
        (f" (+{len(error_log) - 5} more)" if len(error_log) > 5 else "")
    )


active_brand = st.session_state.get("active_brand", "All Brands")
sidebar_context_block(brand=active_brand)

# ── Project resolution ─────────────────────────────────────────────────────────
_pm = ProjectManager()
_all_projects = _pm.list_projects()
if "active_project" not in st.session_state:
    _default_proj = _all_projects[0]["id"] if _all_projects else "mixer"
    st.session_state["active_project"] = _default_proj
_active_project = st.session_state["active_project"]
_active_proj_meta = _pm.get_project(_active_project) or {}
_study_type = _active_proj_meta.get("study_type", "ethnographic")

# ── Paths ─────────────────────────────────────────────────────────────────────
try:
    _found_db = get_db_path(required_table="fact_respondents")
    DB = str(_found_db) if _found_db else ""
except (FileNotFoundError, Exception):
    DB = ""
_BASE         = Path(__file__).resolve().parent.parent
_TREES_DIR    = _BASE / "data" / "pageindex_trees"
_IDX_DB       = _BASE / "data" / "qual_index.db"
_MATRICES_DIR = _BASE / "data" / "qual_matrices"

@st.cache_data(ttl=3600, show_spinner=False)
def _count_tree_passages() -> int:
    """Count actual unique passages across all tree JSON files."""
    total = 0
    if not _TREES_DIR.exists():
        return 0
    for f in _TREES_DIR.glob("*.json"):
        try:
            tree = json.loads(f.read_text(encoding="utf-8"))
            for s in tree.get("sections", []):
                total += len(s.get("passages", []))
        except Exception:
            pass
    return total
_PROCESSED_DIR = _BASE / "data" / "qualitative" / "processed"
_TI_CACHE     = _BASE / "data" / "transcript_insights_cache.json"

# ── OpenRouter (free models for transcript AI) ─────────────────────────────────
try:
    from dotenv import load_dotenv, find_dotenv
    _env_path = str(_BASE / ".env")
    load_dotenv(_env_path, override=True)
except Exception:
    pass

def _get_or_key() -> str:
    """Lazy key loader — re-reads env each call so hot-reloads pick it up."""
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        try:
            from dotenv import load_dotenv
            load_dotenv(str(_BASE / ".env"), override=True)
            key = os.getenv("OPENROUTER_API_KEY", "")
        except Exception:
            pass
    return key

_OR_KEY = _get_or_key()
_OR_URL     = "https://openrouter.ai/api/v1/chat/completions"

# Primary model for all narrative/insight generation. Rotation across the rest of
# _FREE_MODELS below is fallback-ONLY (primary timeout or hard error) — never the
# default path. Was: silently picking whichever of 8 wildly different free models
# answered first, causing visibly inconsistent tone/quality between runs.
#
# Deliberately a free-tier model, not a paid one — this function is still named
# _call_openrouter_free (via its back-compat wrapper) and every call site expects
# free-tier-only cost behavior. Picking a paid model here would silently start
# billing on every AI narrative generated by Quote Explorer. DeepSeek R1 is
# chosen as the strongest reasoning model in the free list below (also matches
# the project's existing primary-model convention in skills/llm_client.py,
# which independently settled on a DeepSeek model after a documented comparison).
_PINNED_MODEL = "deepseek/deepseek-r1:free"

_FREE_MODELS = [
    "moonshotai/kimi-k2.6:free",
    "deepseek/deepseek-r1:free",
    "qwen/qwen3-235b-a22b:free",
    "meta-llama/llama-4-scout:free",
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b-it:free",
    "microsoft/phi-4-reasoning:free",
]  # Strictly free models only — no paid fallback


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_openrouter_catalog() -> list[dict]:
    """Raw live OpenRouter model catalog, cached once/day. Shared by every model-picker
    dropdown so we hit the API once, not once per dropdown."""
    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=8)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception:
        return []


def _clean_openrouter_models(models: list[dict]) -> tuple[list[tuple], list[tuple]]:
    """Filters the raw catalog down to real, usable, honestly-priced chat models.
    Returns (free, paid) where paid items are (name, slug, p_in, p_out, ctx_len) and
    free items are (name, slug, ctx_len). Drops: meta-routers (openrouter/auto, fusion,
    pareto-code — pricing '-1' placeholder, not a pinned model), non-text-output models
    (audio/image generators), and any model with negative/placeholder pricing."""
    free, paid = [], []
    for m in models:
        slug = m.get("id", "")
        name = m.get("name", slug)
        if not slug or slug.startswith("openrouter/"):
            continue
        out_modes = ((m.get("architecture") or {}).get("output_modalities") or [])
        if out_modes and "text" not in out_modes:
            continue
        pricing = m.get("pricing", {}) or {}
        try:
            p_in = float(pricing.get("prompt", "0") or "0") * 1_000_000
            p_out = float(pricing.get("completion", "0") or "0") * 1_000_000
        except (TypeError, ValueError):
            continue
        if p_in < 0 or p_out < 0:
            continue
        ctx_len = m.get("context_length") or (m.get("top_provider") or {}).get("context_length") or 0
        if slug.endswith(":free") or (p_in == 0 and p_out == 0):
            free.append((name, slug, ctx_len))
        else:
            paid.append((name, slug, p_in, p_out, ctx_len))
    return free, paid


@st.cache_data(ttl=86400, show_spinner=False)
def _get_openrouter_model_options() -> dict:
    """Live full OpenRouter model catalog (hundreds of models), not a hand-picked shortlist.
    Returns an ordered label -> slug dict: Default, then Paid (cheapest first), then Free
    (alphabetical). Falls back to a small known-good list if the API is unreachable, so the
    dropdown never ends up empty."""
    options: dict = {"Default (pipeline fallback chain)": None}
    models = _fetch_openrouter_catalog()

    if not models:
        options["── Paid ($/M tokens) ──────────"] = "__divider__"
        for label, slug in [
            ("DeepSeek Chat — $0.20 in / $0.80 out", "deepseek/deepseek-chat"),
            ("Gemini 2.5 Flash — $0.30 in / $2.50 out", "google/gemini-2.5-flash"),
            ("Qwen 2.5 72B Instruct — $0.35 in / $0.40 out", "qwen/qwen-2.5-72b-instruct"),
            ("Llama 3.3 70B Instruct — $0.35 in / $0.40 out", "meta-llama/llama-3.3-70b-instruct"),
        ]:
            options[label] = slug
        options["── Free ─────────────────────"] = "__divider__"
        for slug in _FREE_MODELS:
            options[f"{slug} — Free"] = slug
        return options

    free, paid = _clean_openrouter_models(models)
    free.sort(key=lambda t: t[0].lower())
    paid.sort(key=lambda t: t[2])

    options["── Paid ($/M tokens, cheapest first) ──"] = "__divider__"
    for name, slug, p_in, p_out, _ctx in paid:
        options[f"{name} — ${p_in:.2f} in / ${p_out:.2f} out"] = slug
    options["── Free ─────────────────────"] = "__divider__"
    for name, slug, _ctx in free:
        options[f"{name} — Free"] = slug

    return options


@st.cache_data(ttl=86400, show_spinner=False)
def _get_top_extraction_models(n: int = 20) -> dict:
    """Curated top-N model picker for the extraction pipeline (Step 1 / Step 2 / Reconcile).
    The full 300+ live catalog includes free-tier junk unsuited to structured JSON extraction
    on long transcripts (moderation models, tiny/experimental providers, roleplay finetunes) —
    scanning it directly for "cheapest 20" surfaces that junk before any real paid model shows
    up. Instead: use this project's own already-tested free-model shortlist (_FREE_MODELS,
    proven on this pipeline) as the free tier, then fill the rest with live-priced real paid
    models ascending by cost — genuinely cheapest-to-priciest, but curated not noisy."""
    options: dict = {"Default (pipeline fallback chain)": None}
    models = _fetch_openrouter_catalog()
    _, paid = _clean_openrouter_models(models)

    MIN_CTX = 16000
    paid = [t for t in paid if t[4] >= MIN_CTX]
    paid.sort(key=lambda t: t[2])  # ascending by input price

    if not paid and not _FREE_MODELS:
        from infoleap.skills.llm_client import MODEL as _default_model, FALLBACK_MODELS as _fallback_models
        for slug in [_default_model] + [m for m in _fallback_models if m != _default_model]:
            options[f"{slug} (pipeline default/fallback)"] = slug
        return options

    for slug in _FREE_MODELS:
        options[f"{slug} — Free"] = slug

    n_paid = max(0, n - len(_FREE_MODELS))
    for name, slug, p_in, p_out, ctx in paid[:n_paid]:
        ctx_label = f"{ctx // 1000}K ctx" if ctx else "ctx n/a"
        options[f"{name} — ${p_in:.2f} in / ${p_out:.2f} out — {ctx_label}"] = slug

    return options


# ── Design tokens — sourced from shared ui_styles ─────────────────────────────
_P         = COLORS          # shared palette dict
_BRAND_PAL = BRAND_COLORS    # shared ordered palette
_ZONE_C    = ZONE_COLORS     # shared zone colors
_FONT      = "Inter, Arial, sans-serif"
_MIN_N     = 15

# ── Attribute keyword sets ─────────────────────────────────────────────────────
_ATTRS: dict[str, set] = {
    "Price / Value":    {"price","cost","affordable","cheap","expensive","value","worth",
                         "daam","mehnga","sasta","budget","costly","money","rupee","saving",
                         "economical","pocket","finance","reasonable"},
    "Quality / Build":  {"quality","build","material","finish","sturdy","strong","solid",
                         "guna","tikau","construction","standard","grade","genuine","robust"},
    "Performance":      {"performance","power","efficient","fast","effective","output",
                         "capacity","motor","speed","smooth","powerful","kaam","effective"},
    "Durability":       {"durable","lasting","decade","lifetime","tuta","broke",
                         "repair","maintenance","tikau","purana","reliable","long-lasting"},
    "Service / Support":{"service","warranty","support","technician","replacement","mechanic",
                         "seva","customer","helpline","response","care","centre","amc"},
    "Design / Look":    {"design","look","style","color","colour","aesthetic","beautiful",
                         "attractive","dikhna","sundar","appearance","sleek","elegant","finish"},
}

# ── Sentiment lexicon ─────────────────────────────────────────────────────────
_POS = {"good","great","best","excellent","acha","badhiya","pasand","quality","durable",
        "reliable","trusted","fast","strong","affordable","value","love","happy","recommend",
        "famous","popular","superior","efficient","nice","prefer","satisfied","perfect",
        "smooth","quiet","powerful","effective","worth","genuine","standard","purana"}
_NEG = {"bad","poor","worst","kharab","tuta","broke","problem","issue","expensive",
        "costly","noise","loud","slow","heavy","waste","disappoint","faulty","repair",
        "return","defect","weak","broken","cheap","useless","overpriced","unreliable",
        "complaint","bakwas","chup","bura","ganda"}

_STOP = {
    "hai","h","ka","ki","ke","ko","aur","yeh","ye","iska","uska","ek","se","bhi","nhi",
    "ho","hota","hoti","nahi","wala","wali","bahut","bhut","mera","mere","meri","jo",
    "jab","tab","koi","kuch","toh","tha","thi","the","is","it","and","or","of","in",
    "to","a","an","for","this","that","with","are","has","have","was","not","on","at",
    "by","be","as","from","they","we","very","brand","their","its","which","so","but",
    "also","more","like","can","my","our","us","me","him","her","his","hers","use",
    "used","using","get","got","buy","bought","one","two","year","years","ago","would",
    "will","just","only","always","never","every","all","some","any","since","when",
    "then","than","too","do","did","does","had","been","being","because","if","else",
}

# ═════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ═════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600)
def _load_opts() -> dict:
    try:
        conn = sqlite3.connect(DB)
        brands = [r[0] for r in conn.execute("""
            SELECT DISTINCT db.brand_name
            FROM dim_brand db
            JOIN (SELECT brand_id FROM fact_brand_awareness WHERE stage='TOM' AND rank=1
                  GROUP BY brand_id) t ON t.brand_id = db.brand_id
            WHERE db.brand_name NOT IN ('Others (Specify 1)','Others (Specify 2)','Don''t Know / None')
            ORDER BY db.brand_name
        """).fetchall()]
        cities   = [r[0] for r in conn.execute(
            "SELECT DISTINCT city_name FROM dim_city ORDER BY city_name").fetchall()]
        zones    = [r[0] for r in conn.execute(
            "SELECT DISTINCT zone_name FROM dim_zone ORDER BY zone_name").fetchall()]
        genders  = [r[0] for r in conn.execute(
            "SELECT DISTINCT gender FROM v_respondents WHERE gender IS NOT NULL ORDER BY gender").fetchall()]
        age_bands = [r[0] for r in conn.execute(
            "SELECT DISTINCT age_band FROM v_respondents WHERE age_band IS NOT NULL ORDER BY age_band").fetchall()]
        conn.close()
        return dict(brands=brands, cities=cities, zones=zones,
                    genders=genders, age_bands=age_bands)
    except Exception as e:
        import logging
        logging.error(f"[QE] _load_opts failed: {e}")
        return dict(brands=[], cities=[], zones=[], genders=[], age_bands=[])


@st.cache_data(ttl=1800, show_spinner=False)
def _query_verbatims(brands, cities, zones, genders, age_bands, keyword):
    conn = sqlite3.connect(DB)
    where, params = ["fv.question_code = 'bq2a'"], []

    def _add(col, vals):
        if vals:
            where.append(f"{col} IN ({','.join('?'*len(vals))})")
            params.extend(vals)

    _add("db_tom.brand_name", brands)
    _add("dc.city_name",      cities)
    _add("dz.zone_name",      zones)
    _add("vr.gender",         genders)
    _add("vr.age_band",       age_bands)

    if keyword.strip():
        kw = keyword.strip().lower()
        kw = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("LOWER(fv.response_text) LIKE ? ESCAPE '\\'")
        params.append(f"%{kw}%")

    rows = conn.execute(f"""
        SELECT fv.id,
               fv.response_text,
               COALESCE(db_tom.brand_name,'—') brand,
               COALESCE(vr.gender,'—')          gender,
               COALESCE(vr.age_band,'—')         age_band,
               dc.city_name,
               dz.zone_name
        FROM fact_verbatims fv
        JOIN v_respondents vr ON fv.respondent_id = vr.respondent_id
        JOIN dim_city  dc ON dc.city_name = vr.city_name
        JOIN dim_zone  dz ON dz.zone_name = vr.zone_name
        LEFT JOIN (SELECT respondent_id, brand_id FROM fact_brand_awareness
                   WHERE stage='TOM' AND rank=1 GROUP BY respondent_id) tom
            ON tom.respondent_id = fv.respondent_id
        LEFT JOIN dim_brand db_tom ON db_tom.brand_id = tom.brand_id
        WHERE {' AND '.join(where)}
        ORDER BY fv.id ASC
    """, params).fetchall()
    conn.close()
    return [dict(id=r[0], text=r[1], brand=r[2], gender=r[3],
                 age_band=r[4], city=r[5], zone=r[6]) for r in rows]


@st.cache_data(ttl=3600)
def _load_brand_quant_context() -> dict:
    """TOM awareness% + proper NPS (promoters% - detractors%) per brand."""
    conn = sqlite3.connect(DB)
    base_n = conn.execute(
        "SELECT COUNT(DISTINCT respondent_id) FROM fact_brand_awareness"
    ).fetchone()[0]

    tom_rows = conn.execute("""
        SELECT db.brand_name,
               ROUND(COUNT(DISTINCT ba.respondent_id) * 100.0 / ?, 1)
        FROM fact_brand_awareness ba
        JOIN dim_brand db ON db.brand_id = ba.brand_id
        WHERE ba.stage = 'TOM'
        GROUP BY db.brand_name
    """, (base_n,)).fetchall()

    # Correct NPS formula: (promoters - detractors) / total × 100
    nps_rows = conn.execute("""
        SELECT db.brand_name,
               ROUND(
                   (SUM(CASE WHEN bn.nps_score >= 9 THEN 1.0 ELSE 0 END)
                  - SUM(CASE WHEN bn.nps_score <= 6 THEN 1.0 ELSE 0 END))
                   * 100.0 / COUNT(*), 1
               ) AS nps
        FROM fact_brand_nps bn
        JOIN dim_brand db ON db.brand_id = bn.brand_id
        GROUP BY db.brand_name
        HAVING COUNT(*) >= 30
    """).fetchall()

    conn.close()
    tom = {r[0]: r[1] for r in tom_rows}
    nps = {r[0]: r[1] for r in nps_rows}
    return {b: {"tom_pct": tom.get(b), "nps": nps.get(b)} for b in set(list(tom) + list(nps))}


def _matrices_mtime() -> float:
    if not _MATRICES_DIR.exists():
        return 0.0
    try:
        return max((f.stat().st_mtime for f in _MATRICES_DIR.glob("*_matrix.json")), default=0.0)
    except OSError:
        return 0.0

def _trees_mtime() -> float:
    if not _TREES_DIR.exists():
        return 0.0
    try:
        return max((f.stat().st_mtime for f in _TREES_DIR.glob("*.json")), default=0.0)
    except OSError:
        return 0.0


@st.cache_data(ttl=1800, show_spinner=False)
def _load_matrix_intel(_mtime: float = 0.0) -> dict:
    """
    Load brand intelligence from qual_matrices/*.json (new pipeline).
    Returns per-brand aggregated data including pain points, gaps, emotions.
    Falls back to empty dict if no matrices exist yet.
    """
    if not _MATRICES_DIR.exists():
        return {}

    files = list(_MATRICES_DIR.glob("*_matrix.json"))
    if not files:
        return {}

    brand_data: dict = defaultdict(lambda: {
        "n_docs": 0,
        "cities": set(),
        "pain_points": [],
        "aspiration_gaps": [],
        "unspoken_needs": [],
        "jobs_to_be_done": [],
        "emotional_peaks": [],
        "self_blame_total": 0,
        "product_blame_total": 0,
        "relationship_stages": Counter(),
        "nps_signals": Counter(),
        "emotional_resolutions": Counter(),
        "cook_self_images": Counter(),
        "use_cases": Counter(),
        "pos_passages": [],
        "neg_passages": [],
        "all_passages": [],
        "feature_priorities": Counter(),
        "decision_sources": Counter(),
        # quality tracking
        "quality_scores": [],
        "quality_labels": Counter(),
        "unverified_docs": [],
        "low_quality_docs": [],
    })

    for fpath in files:
        try:
            m = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as _e:
            print(f"[quote_explorer] WARN: failed to parse {fpath.name} in _load_matrix_intel: {_e}")
            continue

        resp  = m.get("respondent", {})
        brand = resp.get("brand_owned") or ""
        if not brand:
            continue
        city  = resp.get("city") or "Unknown"

        bd = brand_data[brand]
        bd["n_docs"] += 1
        bd["cities"].add(city)

        # quality tracking per matrix
        qs = m.get("_quality_score")
        doc_id = m.get("doc_id", fpath.stem)
        if qs is None:
            bd["unverified_docs"].append(doc_id)
        else:
            bd["quality_scores"].append(qs)
            _verif = m.get("_verification", {})
            _qlabel = _verif.get("quality_label", "unknown") if isinstance(_verif, dict) else "unknown"
            bd["quality_labels"][_qlabel] += 1
            if qs < 60:
                bd["low_quality_docs"].append({"doc_id": doc_id, "score": qs})

        bd["relationship_stages"][m.get("brand_relationship", {}).get("relationship_stage", "unknown")] += 1
        bd["nps_signals"][m.get("nps_signal", "unclear")] += 1
        bd["emotional_resolutions"][m.get("emotional_resolution", "neutral")] += 1

        ids = m.get("identity_signals", {})
        if ids.get("cook_self_image"):
            bd["cook_self_images"][ids["cook_self_image"]] += 1

        cc = m.get("cultural_context", {})
        for uc in cc.get("primary_use_cases", []):
            if uc: bd["use_cases"][uc] += 1

        for pp in m.get("pain_points", []):
            if pp.get("issue_description"):
                bd["pain_points"].append({
                    "issue": pp["issue_description"],
                    "severity": pp.get("severity", "medium"),
                    "area": pp.get("product_area", "other"),
                    "quote": pp.get("verbatim_quote", ""),
                    "city": city,
                })

        for ag in m.get("aspiration_reality_gaps", []):
            if ag.get("aspiration"):
                bd["aspiration_gaps"].append({
                    "aspiration": ag["aspiration"],
                    "workaround": ag.get("workaround"),
                    "opportunity": ag.get("commercial_opportunity", "medium"),
                    "charge": ag.get("emotional_charge", "unknown"),
                    "quote": ag.get("verbatim_quote", ""),
                    "city": city,
                })

        for un in m.get("unspoken_needs", []):
            if un:
                bd["unspoken_needs"].append({"need": str(un), "city": city})

        for jt in m.get("jobs_to_be_done", []):
            if jt: bd["jobs_to_be_done"].append({"job": str(jt), "city": city})

        peak = m.get("peak_emotional_moment", {})
        if peak.get("emotion"):
            bd["emotional_peaks"].append({
                "emotion": peak["emotion"],
                "trigger": peak.get("trigger", ""),
                "quote": peak.get("verbatim_quote", ""),
                "city": city,
            })

        bd["self_blame_total"]    += len(m.get("self_blame_instances", []))
        bd["product_blame_total"] += len(m.get("product_blame_instances", []))

        for fp in m.get("feature_priorities", []):
            if fp.get("feature") and fp.get("importance") == "high":
                bd["feature_priorities"][fp["feature"]] += 1

        for dd in m.get("decision_drivers", []):
            src = dd.get("info_source", "other")
            if src: bd["decision_sources"][src] += 1

        for p in m.get("all_passages", []):
            if not p.get("content") or len(p["content"]) < 30:
                continue
            entry = {
                "content": p["content"],
                "sentiment": p.get("sentiment", "neutral"),
                "topic": p.get("topic", "unknown"),
                "pain_point": p.get("pain_point", False),
                "decision_signal": p.get("decision_signal", False),
                "city": city,
                "brand": brand,
            }
            bd["all_passages"].append(entry)
            if p.get("sentiment") == "positive" and len(bd["pos_passages"]) < 8:
                bd["pos_passages"].append(entry)
            elif p.get("sentiment") == "negative" and len(bd["neg_passages"]) < 8:
                bd["neg_passages"].append(entry)

    result = {}
    for brand, bd in brand_data.items():
        if bd["n_docs"] < 1:
            continue
        n_docs = bd["n_docs"]
        sb = bd["self_blame_total"]
        pb = bd["product_blame_total"]
        result[brand] = {
            "n_docs":     n_docs,
            "n_passages": len(bd["all_passages"]),
            "cities":     sorted(bd["cities"]),
            "pain_points":      bd["pain_points"],
            "aspiration_gaps":  bd["aspiration_gaps"],
            "unspoken_needs":   bd["unspoken_needs"],
            "jobs_to_be_done":  bd["jobs_to_be_done"],
            "emotional_peaks":  bd["emotional_peaks"],
            "blame": {
                "self": sb, "product": pb,
                "self_ratio":    round(sb / max(sb + pb, 1), 2),
                "product_ratio": round(pb / max(sb + pb, 1), 2),
            },
            "relationship_stages": dict(bd["relationship_stages"].most_common()),
            "nps_signals":         dict(bd["nps_signals"].most_common()),
            "emotional_resolutions": dict(bd["emotional_resolutions"].most_common()),
            "cook_self_images":    dict(bd["cook_self_images"].most_common()),
            "top_use_cases":       bd["use_cases"].most_common(6),
            "top_features":        bd["feature_priorities"].most_common(6),
            "decision_sources":    dict(bd["decision_sources"].most_common()),
            "pos_passages":        bd["pos_passages"][:5],
            "neg_passages":        bd["neg_passages"][:5],
            "all_passages":        bd["all_passages"],
            "positive_resolution_pct": round(
                bd["emotional_resolutions"].get("positive", 0) / max(n_docs, 1) * 100, 1),
            "promoter_pct": round(
                bd["nps_signals"].get("promoter", 0) / max(n_docs, 1) * 100, 1),
            # quality stats
            "avg_quality": round(sum(bd["quality_scores"]) / max(len(bd["quality_scores"]), 1), 1),
            "unverified_docs": bd["unverified_docs"],
            "low_quality_docs": bd["low_quality_docs"],
            "quality_labels": dict(bd["quality_labels"].most_common()),
        }

    return result


def _bm25_score(query_terms: list[str], doc_tokens: list[str], avg_dl: float, k1: float = 1.5, b: float = 0.75) -> float:
    """BM25 relevance score — better than simple keyword match, no external deps."""
    dl = len(doc_tokens)
    doc_tf: dict = {}
    for t in doc_tokens:
        doc_tf[t] = doc_tf.get(t, 0) + 1
    score = 0.0
    for term in query_terms:
        tf = doc_tf.get(term, 0)
        if tf == 0:
            # try prefix match (handles Hindi transliteration variations)
            tf = sum(v for k, v in doc_tf.items() if k.startswith(term[:4]) and len(term) >= 4)
        if tf > 0:
            score += (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / max(avg_dl, 1)))
    return score


def _search_matrix_passages(query: str, brand_filter: str = None, city_filter: str = None,
                             matrices_dir: Path = None) -> list:
    """
    BM25-ranked passage search across matrix all_passages.
    Works for both Mixer (brand_owned) and CoinDCX (segment) projects.
    Falls back to project-level search if matrices_dir provided.
    """
    search_dir = matrices_dir or (_MATRICES_DIR if _MATRICES_DIR.exists() else None)
    if not search_dir or not search_dir.exists():
        return []

    q_low = (query or "").lower().strip()
    # Tokenise query: words >=3 chars + strip Hindi stop words
    _Q_STOP = {"hai","ka","ki","ke","ko","se","bhi","aur","yeh","jo","tha","thi","the",
               "nahi","wala","wali","bahut","mera","mere","toh","kuch","koi","sab"}
    query_terms = [w for w in re.findall(r'\b\w{3,}\b', q_low) if w not in _Q_STOP] if q_low else []

    # Collect all docs for avg_dl calculation (BM25 needs corpus stats)
    all_passages_flat: list[str] = []
    matrix_cache: list[tuple] = []  # (path, matrix, brand, city, segment)

    for fpath in sorted(search_dir.glob("*_matrix.json")):
        try:
            m = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as _e:
            print(f"[quote_explorer] WARN: failed to parse {fpath.name} during search: {_e}")
            continue
        resp   = m.get("respondent", {})
        brand  = resp.get("brand_owned") or resp.get("segment") or "Unknown"
        city   = resp.get("city") or "Unknown"
        seg    = resp.get("segment") or resp.get("brand_owned") or "Unknown"

        if brand_filter and brand.lower() != brand_filter.lower() and seg.lower() != brand_filter.lower():
            continue
        if city_filter and city_filter.lower() not in city.lower():
            continue

        for p in m.get("all_passages", []):
            c = p.get("content", "")
            if c and len(c) >= 25:
                all_passages_flat.append(c)
        matrix_cache.append((fpath, m, brand, city, seg))

    avg_dl = sum(len(c.split()) for c in all_passages_flat) / max(len(all_passages_flat), 1)
    results = []

    for fpath, m, brand, city, seg in matrix_cache:
        qs = m.get("_quality_score") if m.get("_quality_score") is not None else 100
        doc_id = m.get("doc_id", "")
        journey = m.get("respondent", {}).get("journey_stage", "")

        for pi, p in enumerate(m.get("all_passages", [])):
            content = p.get("content", "")
            if not content or len(content) < 25:
                continue

            content_low = content.lower()
            tokens = re.findall(r'\b\w{3,}\b', content_low)

            # BM25 relevance score
            bm25 = _bm25_score(query_terms, tokens, avg_dl) if query_terms else 0

            # Boost signals
            boost = 0
            if q_low and q_low in content_low:
                boost += 30          # exact phrase match
            if p.get("pain_point"):
                boost += 4
            if p.get("decision_signal"):
                boost += 3
            if qs >= 80:
                boost += 2           # high quality matrix
            if brand_filter:
                boost += 2

            total = bm25 * 10 + boost

            if not q_low or total > 0:
                results.append({
                    "content":         content,
                    "brand":           brand,
                    "segment":         seg,
                    "city":            city,
                    "doc_id":          doc_id,
                    "journey":         journey,
                    "sentiment":       p.get("sentiment", "neutral"),
                    "topic":           p.get("topic", ""),
                    "pain_point":      p.get("pain_point", False),
                    "decision_signal": p.get("decision_signal", False),
                    "quality_score":   qs,
                    "score":           round(total, 2),
                })

    results.sort(key=lambda x: -x["score"])
    return results[:150]


# ─── save reference before any reassignment ──────────────────────────────────
_search_matrix_passages_bm25 = _search_matrix_passages


def _cdcx_search_passages(query: str, segment_filter: str = None, city_filter: str = None,
                           topic_filter: str = None, sentiment_filter: str = None,
                           matrices_dir: Path = None) -> list:
    """Enhanced passage search for CoinDCX — supports segment/topic/sentiment filters."""
    raw = _search_matrix_passages_bm25(query, brand_filter=segment_filter,
                                       city_filter=city_filter, matrices_dir=matrices_dir)
    if topic_filter and topic_filter != "All":
        raw = [r for r in raw if r.get("topic", "") == topic_filter]
    if sentiment_filter and sentiment_filter != "All":
        raw = [r for r in raw if r.get("sentiment", "") == sentiment_filter]
    return raw


# ─── legacy compat: old code does m.get("respondent",...) then appends brand/city ──
# New BM25 function returns those keys already — no changes needed in callers.

# keep old for-loop version stub (referenced below in Mixer passage search)
def _search_matrix_passages_old(query: str, brand_filter: str = None, city_filter: str = None) -> list:
    """Thin wrapper kept for backward compat — delegates to BM25 implementation."""
    return _search_matrix_passages_bm25(query, brand_filter=brand_filter, city_filter=city_filter)


# Mixer passage search uses _search_matrix_passages — point to BM25
_search_matrix_passages = _search_matrix_passages_bm25  # noqa: F811 — intentional rebind


# ─── old for-loop stub (never called — kept to avoid NameError in old code paths)
def _search_matrix_passages_for_legacy(query: str, brand_filter: str = None, city_filter: str = None) -> list:
    results = []
    if not _MATRICES_DIR.exists():
        return results
    for fpath in _MATRICES_DIR.glob("*_matrix.json"):
        try:
            m = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        resp = m.get("respondent", {})
        brand = resp.get("brand_owned") or "Unknown"
        city  = resp.get("city") or "Unknown"
        if brand_filter and brand.lower() != brand_filter.lower():
            continue
        if city_filter and city_filter.lower() not in city.lower():
            continue
        for p in m.get("all_passages", []):
            content = p.get("content", "")
            if not content or len(content) < 25:
                continue
            results.append({
                "content": content,
                "brand": brand,
                    "city": city,
                    "sentiment": p.get("sentiment", "neutral"),
                    "topic": p.get("topic", "unknown"),
                    "pain_point": p.get("pain_point", False),
                    "score": score,
                })

    results.sort(key=lambda x: -x["score"])
    return results[:100]


def _get_brand_matrix_docs(brand: str) -> list:
    """Load per-doc summaries for a brand from matrices. Replaces _get_brand_docs."""
    if not _MATRICES_DIR.exists():
        return []

    _TI_SKIP = {
        "bajaj","butterfly","crompton","havells","maharaja","philips","prestige","usha",
        "sujata","preethi","mixer","juicer","blender","grinder","fan","heater","cooler",
        "wh","rc","mg","mh","fp","recent","intender","user","purchaser","di","r","clean",
        "3","1","2","4","5",
    }

    docs = []
    for fpath in sorted(_MATRICES_DIR.glob("*_matrix.json")):
        try:
            m = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as _e:
            print(f"[quote_explorer] WARN: failed to parse {fpath.name} in _get_brand_matrix_docs: {_e}")
            continue

        resp = m.get("respondent", {})
        b    = resp.get("brand_owned") or ""
        if b.lower() != brand.lower():
            continue

        city     = resp.get("city") or "Unknown"
        doc_id   = m.get("doc_id", fpath.stem.replace("_matrix", ""))
        wc       = m.get("word_count", 0)
        passages = m.get("all_passages", [])
        pain_pts = m.get("pain_points", [])
        gaps     = m.get("aspiration_reality_gaps", [])
        peak     = m.get("peak_emotional_moment", {})
        rel_stage = m.get("brand_relationship", {}).get("relationship_stage", "unknown")
        nps       = m.get("nps_signal", "unclear")
        journey   = resp.get("journey_stage", "unknown")

        # Respondent name from filename
        raw = doc_id.replace("_clean", "").replace("_matrix", "").replace("_", " ")
        parts = raw.split()
        if len(parts) >= 2 and parts[0].upper() in ("DI", "R") and (parts[1].isdigit() or len(parts[1]) <= 2):
            parts = parts[2:]
        name_tokens = []
        for pt in parts:
            pl = pt.lower().strip("-")
            if pl in _TI_SKIP or pt.isdigit() or re.match(r'^[A-Z]{1,3}$', pt) or len(pt) < 2:
                continue
            name_tokens.append(pt.capitalize())
            if len(name_tokens) >= 2:
                break
        respondent = " ".join(name_tokens) if name_tokens else (parts[0].capitalize() if parts else "Respondent")

        top_pain = pain_pts[0]["issue_description"][:60] if pain_pts else ""
        top_gap  = gaps[0]["aspiration"][:60] if gaps else ""

        docs.append({
            "doc_id":    doc_id,
            "respondent": respondent,
            "city":       city,
            "journey":    journey,
            "word_count": wc,
            "n_passages": len(passages),
            "n_pain":     len(pain_pts),
            "n_gaps":     len(gaps),
            "peak_emotion": peak.get("emotion", ""),
            "peak_quote":   peak.get("verbatim_quote", "")[:100],
            "relationship_stage": rel_stage,
            "nps":         nps,
            "top_pain":    top_pain,
            "top_gap":     top_gap,
        })

    return sorted(docs, key=lambda d: (d["city"], d["respondent"]))


_LOYAL_PHRASES = {
    "recommend", "suggest", "tell friends", "advise", "refer",
    "next time", "will buy", "same brand", "buy again",
    "loyal", "always buy", "only brand", "lifelong", "life long",
    "many years", "long time", "keep buying",
    "switched to", "changed to", "replaced with", "now using", "shifted to",
    "dobara", "phir se", "salo se",
}

# Broader topic keyword map for transcript content
_TOPIC_KWORDS: dict[str, set] = {
    "Price & Value":      {"price","cost","affordable","cheap","expensive","value","worth","budget","costly","money","saving","economical","reasonable"},
    "Quality & Build":    {"quality","build","material","sturdy","strong","solid","construction","standard","robust","genuine","poor quality","cheap quality"},
    "Performance":        {"performance","power","efficient","fast","effective","output","capacity","motor","speed","smooth","powerful","kaam","grinding","blend"},
    "Durability":         {"durable","lasting","decade","lifetime","broke","tuta","repair","maintenance","reliable","long lasting","old","purana"},
    "Service & Warranty": {"service","warranty","support","technician","replacement","mechanic","customer care","helpline","response","amc","centre","seva"},
    "Design & Look":      {"design","look","style","color","colour","attractive","beautiful","aesthetic","appearance","sleek","finish","dikhna"},
    "Noise & Vibration":  {"noise","noisy","vibration","loud","sound","quiet","shakes","rattles","chup","avaaz"},
    "Ease of Use":        {"easy","simple","convenient","user friendly","handle","operate","difficult","hard","complex","straightforward","smooth to use"},
    "Brand Trust":        {"trust","brand","reliable","reputed","famous","popular","known","old brand","heritage","original","fake","duplicate","genuine"},
    "Purchase Decision":  {"bought","purchase","buy","decided","choose","chose","recommend","switch","changed","compare","better than","preferred"},
}


@st.cache_data(ttl=3600)
def _load_transcript_intel(_mtime: float = 0.0, _v: int = 3) -> dict:
    """
    Loads and analyses all 233 IDI transcript trees.
    Sentiment uses content-based keyword analysis (not broken pre-tags).
    Extracts real quote samples per brand for display.
    """
    if not _IDX_DB.exists() or not _TREES_DIR.exists():
        return {}

    conn = sqlite3.connect(str(_IDX_DB))
    idx = conn.execute(
        "SELECT doc_id, brand, city FROM qual_index WHERE brand IS NOT NULL AND brand != ''"
    ).fetchall()
    null_count = conn.execute(
        "SELECT COUNT(DISTINCT doc_id) FROM qual_index WHERE brand IS NULL OR brand = ''"
    ).fetchone()[0]
    conn.close()

    brand_data: dict = defaultdict(lambda: {
        "pos": 0, "neu": 0, "neg": 0,
        "attrs": defaultdict(int),
        "topics": defaultdict(int),
        "themes": Counter(),
        "loyalty_hits": 0,
        "n_passages": 0,
        "n_docs": 0,
        "cities": set(),
        "categories": Counter(),
        "pos_quotes": [],   # best positive quotes for display
        "neg_quotes": [],   # strongest negative quotes for display
        "notable_quotes": [], # interesting general quotes
    })

    for doc_id, brand, city in idx:
        fpath = _TREES_DIR / f"{doc_id}_tree.json"
        if not fpath.exists():
            continue
        try:
            tree = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as _e:
            print(f"[quote_explorer] WARN: failed to parse {fpath.name} (tree): {_e}")
            continue

        bd = brand_data[brand]
        bd["n_docs"] += 1
        if city:
            bd["cities"].add(city)

        category = tree.get("category", "")
        if category and category.lower() not in ("unknown", ""):
            bd["categories"][category] += 1

        for theme in tree.get("all_themes", []):
            if theme:
                bd["themes"][theme] += 1

        for section in tree.get("sections", []):
            for passage in section.get("passages", []):
                content = passage.get("content", "")
                if not content or len(content) < 35:
                    continue
                if content.lstrip().startswith("---"):
                    continue
                # skip header/profile blocks (interviewer intros)
                if content.count("M:") > 2 and len(content) < 200:
                    continue

                content_low = content.lower()
                words = set(re.findall(r'\b\w+\b', content_low))

                # ── CONTENT-BASED sentiment (fixes broken pre-tags) ──────────
                pos_hits = len(words & _POS)
                neg_hits = len(words & _NEG)
                if pos_hits > neg_hits:
                    sent_label = "positive"
                    bd["pos"] += 1
                elif neg_hits > pos_hits:
                    sent_label = "negative"
                    bd["neg"] += 1
                else:
                    sent_label = "neutral"
                    bd["neu"] += 1

                bd["n_passages"] += 1

                # ── Topic classification ─────────────────────────────────────
                matched_topics = []
                for topic_name, kws in _TOPIC_KWORDS.items():
                    if words & kws:
                        bd["topics"][topic_name] += 1
                        matched_topics.append(topic_name)

                # ── Attr keyword matching ────────────────────────────────────
                for attr_name, kws in _ATTRS.items():
                    if words & kws:
                        bd["attrs"][attr_name] += 1

                # ── Loyalty phrases ──────────────────────────────────────────
                if any(phrase in content_low for phrase in _LOYAL_PHRASES):
                    bd["loyalty_hits"] += 1

                # ── Quote sampling (keep best examples) ──────────────────────
                # Prefer quotes 60-350 chars, substantive (not just interviewer Q)
                clean = re.sub(r'\s+', ' ', content.strip())
                quote_score = pos_hits + neg_hits  # how sentiment-charged
                if 60 <= len(clean) <= 400 and ":" not in clean[:15]:
                    if sent_label == "positive" and len(bd["pos_quotes"]) < 5:
                        bd["pos_quotes"].append({
                            "text": clean, "city": city or "—",
                            "score": quote_score, "topics": matched_topics[:2],
                        })
                    elif sent_label == "negative" and len(bd["neg_quotes"]) < 5:
                        bd["neg_quotes"].append({
                            "text": clean, "city": city or "—",
                            "score": quote_score, "topics": matched_topics[:2],
                        })
                    elif sent_label == "neutral" and quote_score >= 2 and len(bd["notable_quotes"]) < 4:
                        bd["notable_quotes"].append({
                            "text": clean, "city": city or "—",
                            "score": quote_score, "topics": matched_topics[:2],
                        })

    result: dict = {}
    for brand, bd in brand_data.items():
        n = bd["n_passages"]
        if n < 5:
            continue
        result[brand] = {
            "n_passages":    n,
            "n_docs":        bd["n_docs"],
            "cities":        sorted(bd["cities"]),
            "categories":    dict(bd["categories"].most_common(3)),
            "top_themes":    bd["themes"].most_common(8),
            "sentiment": {
                "positive": bd["pos"],
                "neutral":  bd["neu"],
                "negative": bd["neg"],
                "pos_pct":  round(bd["pos"] / n * 100, 1),
                "neg_pct":  round(bd["neg"] / n * 100, 1),
                "neu_pct":  round(bd["neu"] / n * 100, 1),
            },
            "attrs": {
                a: round(cnt / n * 100, 1)
                for a, cnt in bd["attrs"].items()
            },
            "top_topics":    sorted(bd["topics"].items(), key=lambda x: -x[1])[:8],
            "loyalty_pct":   round(bd["loyalty_hits"] / n * 100, 1),
            "pos_quotes":    sorted(bd["pos_quotes"], key=lambda x: -x["score"])[:3],
            "neg_quotes":    sorted(bd["neg_quotes"], key=lambda x: -x["score"])[:3],
            "notable_quotes": bd["notable_quotes"][:3],
        }

    result["__null_doc_count__"] = null_count
    return result


def _call_llm_pinned(prompt: str, system: str = "You are a qualitative research analyst.") -> str:
    """Call the pinned primary model (free-tier — see _PINNED_MODEL comment); fall
    back through the rest of _FREE_MODELS only on failure/timeout, never by default.
    Total wall-clock budget: 45s across primary + fallbacks to prevent long page freezes."""
    key = _get_or_key()
    if not key:
        return ""
    _deadline = time.monotonic() + 45
    _models_to_try = [_PINNED_MODEL] + [m for m in _FREE_MODELS if m != _PINNED_MODEL]
    for model in _models_to_try:
        if time.monotonic() >= _deadline:
            break
        try:
            _remaining = max(5, int(_deadline - time.monotonic()))
            payload = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
                "max_tokens": 400,
                "temperature": 0.2,
            }).encode("utf-8")
            req = urllib.request.Request(
                _OR_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://infoleap.ai",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=min(20, _remaining)) as resp:
                data = json.loads(resp.read())
                text = data["choices"][0]["message"]["content"].strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def _call_openrouter_free(prompt: str, system: str = "You are a qualitative research analyst.") -> str:
    """Back-compat wrapper — all existing call sites now get the pinned model first,
    falling back through _FREE_MODELS only on failure."""
    return _call_llm_pinned(prompt, system)


def _load_insight_cache() -> dict:
    if _TI_CACHE.exists():
        try:
            return json.loads(_TI_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_insight_cache(cache: dict):
    try:
        _TI_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _insight_cache_key(brand: str, mtime: float = 0.0) -> str:
    """Cache key tied to data mtime so re-extracted matrices bust stale AI narratives.
    Was: hardcoded '{brand}_v4' suffix with no relationship to underlying data —
    re-extraction left stale cached narratives showing indefinitely.

    mtime is truncated to whole seconds — acceptable since matrix extraction is a
    slow, manual/offline batch job, not something that re-runs sub-second."""
    return f"{brand}_v4_{int(mtime)}"


def _generate_brand_transcript_insight(brand: str, bd: dict) -> str:
    """Generate AI insight for one brand from matrix data. Cached to disk."""
    cache = _load_insight_cache()
    cache_key = _insight_cache_key(brand, _matrices_mtime())
    if cache_key in cache:
        return cache[cache_key]

    # Works with both new matrix bd and old tree-based bd
    cities      = bd.get("cities", [])
    n_docs      = bd.get("n_docs", bd.get("n_passages", 0))
    promoter_pct = bd.get("promoter_pct", 0)
    pos_res_pct  = bd.get("positive_resolution_pct", 0)

    # Pain points — from matrix
    pain_pts = bd.get("pain_points", [])
    if isinstance(pain_pts, dict):
        pain_pts = pain_pts.get("all", []) or pain_pts.get("top_5", [])
    top_pains = [
        f'[{p.get("area","?")}·{p.get("severity","?")}] {p.get("issue",p.get("issue_description",""))[:80]}'
        + (f' — "{_clean_quote(p.get("quote",p.get("verbatim_quote",""))[:150])}"' if p.get("quote") or p.get("verbatim_quote") else "")
        for p in (pain_pts[:4] if pain_pts else [])
    ]

    # Aspiration gaps — from matrix
    gaps = bd.get("aspiration_gaps", [])
    if isinstance(gaps, dict):
        gaps = gaps.get("high_opportunity", []) or gaps.get("all", [])
    top_gaps = [
        f'[{g.get("opportunity","?")} opp] {g.get("aspiration","")[:80]}'
        + (f' (workaround: {g.get("workaround","")[:60]})' if g.get("workaround") else "")
        for g in (gaps[:3] if gaps else [])
    ]

    # Positive/negative verbatims
    pos_passages = bd.get("pos_passages", bd.get("pos_quotes", []))
    neg_passages = bd.get("neg_passages", bd.get("neg_quotes", []))
    pos_qs = [_clean_quote((q.get("content") or q.get("text",""))[:200]) for q in pos_passages[:3]]
    neg_qs = [_clean_quote((q.get("content") or q.get("text",""))[:200]) for q in neg_passages[:3]]

    # Unspoken needs
    unspoken = [str(u.get("need",u))[:100] for u in bd.get("unspoken_needs",[])[:3]]

    # Blame attribution — keys from _load_matrix_intel() are self_ratio / product_ratio
    blame = bd.get("blame", {})
    sb_ratio = blame.get("self_ratio", blame.get("self_blame_ratio", 0))
    pb_ratio = blame.get("product_ratio", blame.get("product_blame_ratio", 0))

    # Relationship health
    rel_stages = bd.get("relationship_stages", {})
    strained_pct = round(rel_stages.get("strained",0) / max(sum(rel_stages.values()),1) * 100)
    at_risk_pct  = round(rel_stages.get("at_risk",0) / max(sum(rel_stages.values()),1) * 100)

    _study_ctx_for_prompt = _mx_study_ctx if '_mx_study_ctx' in dir() and _mx_study_ctx else \
        "FMCD category (mixer-grinders, food processors). Middle-class Indian consumers. Purchase cycles 5-10 years."

    prompt = f"""BRAND TRANSCRIPT INTELLIGENCE BRIEF — {brand.upper()}

STUDY: {n_docs} In-depth interviews, {len(cities)} cities: {', '.join(cities[:6])}
Context: {_study_ctx_for_prompt}

PERFORMANCE SIGNALS:
  NPS Promoter: {promoter_pct:.0f}% | Positive resolution: {pos_res_pct:.0f}%
  Strained relationship: {strained_pct}% | At-risk: {at_risk_pct}%
  Self-blame ratio: {sb_ratio:.0%} | Product-blame ratio: {pb_ratio:.0%}
  (High self-blame = consumers fault themselves when product fails. Brand education problem.)

TOP PAIN POINTS (extracted from interviews):
{chr(10).join(f'  {i+1}. {p}' for i,p in enumerate(top_pains)) if top_pains else '  [None extracted]'}

ASPIRATION GAPS (what consumers wish existed):
{chr(10).join(f'  - {g}' for g in top_gaps) if top_gaps else '  [None extracted]'}

UNSPOKEN NEEDS (inferred from workarounds):
{chr(10).join(f'  * {u}' for u in unspoken) if unspoken else '  [None inferred]'}

POSITIVE VERBATIMS:
{chr(10).join(f'  + "{q}"' for q in pos_qs) if pos_qs else '  [None]'}

CRITICAL VERBATIMS:
{chr(10).join(f'  - "{q}"' for q in neg_qs) if neg_qs else '  [None]'}

Write a sharp 4-part brief. Every claim must trace to specific evidence above. Be {brand}-specific. No hedging.

WHAT CONSUMERS LOVE: [2 sentences — the emotional/functional core of {brand}'s appeal. What specific language reveals WHY they choose it?]

PAIN POINTS: [2 sentences — root complaint. Product failure, service failure, or expectation mismatch? Quote the most damaging criticism. What does this cost in loyalty?]

BRAND EQUITY SIGNAL: [1 sentence — STRONG/MODERATE/WEAK consumer equity for {brand}. What evidence drives this verdict? Consider: {promoter_pct:.0f}% promoter, {strained_pct}% strained, {sb_ratio:.0%} self-blame.]

STRATEGIC SIGNAL: [1-2 sentences — single most urgent action for the {brand} team. Which pain to fix or positive theme to amplify. Name specific city/segment if data supports it.]

Output only the 4 sections above."""

    result = _call_openrouter_free(
        prompt,
        system=(
            "You are a senior qualitative research analyst specialising in Indian consumer behaviour and FMCD brand strategy. "
            "You interpret IDI transcripts to extract strategic insight — not summaries. "
            "Write for brand managers who need to act, not academics who need to reflect. "
            "Be specific, evidence-grounded, and opinionated. If data is thin, say so — don't pad."
        ),
    )
    if result:
        # Drop this brand's older mtime-keyed entries — otherwise every
        # re-extraction adds a new key and the cache file grows forever.
        cache = {k: v for k, v in cache.items() if not k.startswith(f"{brand}_v4_")}
        cache[cache_key] = result
        _save_insight_cache(cache)
    return result


@st.cache_data(ttl=1800, show_spinner=False)
def _qual_search_cached(query, brand, city):
    filters = {k: v for k, v in {"brand": brand, "city": city}.items() if v}
    try:
        return asyncio.run(search_qual_trees(query, filters=filters))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(search_qual_trees(query, filters=filters))
        finally:
            loop.close()
    except Exception:
        return []


# ═════════════════════════════════════════════════════════════════════════════
# ANALYTICS (in-memory)
# ═════════════════════════════════════════════════════════════════════════════

_ANON_ID_RE      = re.compile(r'\bR_[0-9a-f]{6,10}\b', re.IGNORECASE)
_DIALOGUE_RE     = re.compile(r'(?<!\w)([MR]):\s+')   # IDI transcript turn marker

def _clean_quote(text: str) -> str:
    """Strip anonymized brand UUIDs (R_7f9e665f style) from verbatim quotes."""
    return _ANON_ID_RE.sub("[brand]", text).strip()

def _has_dialogue(text: str) -> bool:
    """True if passage contains raw IDI M:/R: dialogue markers."""
    return bool(_DIALOGUE_RE.search(text))

def _parse_dialogue(text: str) -> list:
    """Split IDI transcript text on M:/R: markers → [(speaker, content), ...]."""
    parts = _DIALOGUE_RE.split(text)
    result = []
    if parts[0].strip():
        result.append(("X", parts[0].strip()))
    for i in range(1, len(parts) - 1, 2):
        speaker = parts[i].upper()
        body    = parts[i + 1].strip() if (i + 1) < len(parts) else ""
        if body:
            result.append((speaker, body))
    return result



def _sentiment(text: str) -> tuple:
    words = set(re.findall(r'\b\w+\b', text.lower()))
    p, n = len(words & _POS), len(words & _NEG)
    if p > n:   return "Positive", _P["green"],  "▲"
    if n > p:   return "Negative", _P["red"],    "▼"
    return "Neutral", "#94a3b8", "●"


def _compute_brand_attributes(rows: list) -> dict:
    brand_buckets: dict = defaultdict(list)
    for r in rows:
        if r["brand"] != "—":
            brand_buckets[r["brand"]].append(r["text"].lower())

    result = {}
    for brand, texts in brand_buckets.items():
        n = len(texts)
        if n < _MIN_N:
            continue
        attr_counts = {}
        for attr_name, kws in _ATTRS.items():
            pat = re.compile(
                r'\b(' + '|'.join(re.escape(k) for k in kws) + r')\b',
                re.IGNORECASE,
            )
            hits = sum(1 for t in texts if pat.search(t))
            attr_counts[attr_name] = round(hits / n * 100, 1)
        attr_counts["_n"] = n
        result[brand] = attr_counts

    return result


def _compute_sentiment_by_brand(rows: list) -> list:
    brand_sent: dict = defaultdict(lambda: {"pos": 0, "neu": 0, "neg": 0, "n": 0})
    for r in rows:
        if r["brand"] == "—":
            continue
        s = _sentiment(r["text"])[0]
        bd = brand_sent[r["brand"]]
        bd["n"] += 1
        if s == "Positive":   bd["pos"] += 1
        elif s == "Negative": bd["neg"] += 1
        else:                 bd["neu"] += 1

    out = []
    for brand, bd in brand_sent.items():
        n = bd["n"]
        if n < _MIN_N:
            continue
        pos_p = round(bd["pos"] / n * 100, 1)
        neg_p = round(bd["neg"] / n * 100, 1)
        out.append(dict(brand=brand, pos_pct=pos_p, neg_pct=neg_p,
                        net=pos_p - neg_p, n=n))
    return sorted(out, key=lambda x: x["net"], reverse=True)


def _compute_zone_voice(rows: list) -> dict:
    global_count: Counter = Counter()
    zone_counts: dict = defaultdict(Counter)
    global_total = 0

    for r in rows:
        for w in re.findall(r'\b[a-zA-Z]{3,}\b', r["text"].lower()):
            if w not in _STOP:
                global_count[w] += 1
                zone_counts[r["zone"]][w] += 1
                global_total += 1

    if global_total == 0:
        return {}

    _FREQ_FLOOR = 0.001
    g_freq = {w: max(c / global_total, _FREQ_FLOOR)
              for w, c in global_count.items() if c >= 5}

    result = {}
    for zone, zc in zone_counts.items():
        z_total = sum(zc.values())
        if z_total < 50:
            continue
        scored = []
        for w, cnt in zc.items():
            if cnt < 5 or w not in g_freq:
                continue
            ratio = (cnt / z_total) / g_freq[w]
            if ratio >= 1.4:
                scored.append((w, round(ratio, 2), cnt))
        scored.sort(key=lambda x: -x[1])
        result[zone] = scored[:5]

    return result


def _compute_distinctive_words(rows: list) -> dict:
    global_count: Counter = Counter()
    brand_counts: dict = defaultdict(Counter)
    global_total = 0

    for r in rows:
        for w in re.findall(r'\b[a-zA-Z]{3,}\b', r["text"].lower()):
            if w not in _STOP:
                global_count[w] += 1
                if r["brand"] != "—":
                    brand_counts[r["brand"]][w] += 1
                global_total += 1

    _FREQ_FLOOR = 0.001
    g_freq = {w: max(c / global_total, _FREQ_FLOOR)
              for w, c in global_count.items() if c >= 3}
    brand_total = {b: sum(bc.values()) for b, bc in brand_counts.items()}

    result = {}
    for brand, bc in brand_counts.items():
        if brand_total[brand] < _MIN_N * 3:
            continue
        scored = []
        for w, cnt in bc.items():
            if cnt < 4 or w not in g_freq:
                continue
            ratio = (cnt / brand_total[brand]) / g_freq[w]
            if ratio >= 1.5:
                scored.append((w, round(ratio, 2), cnt))
        scored.sort(key=lambda x: -x[1])
        result[brand] = scored[:5]

    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHARTS — smaller, cleaner, one insight per chart
# ═════════════════════════════════════════════════════════════════════════════

def _chart_header(title: str, subtitle: str = "", how_to_read: str = ""):
    """Renders a styled chart title block + optional reading guide above the chart."""
    subtitle_html = f'<div style="font-size:0.78rem;color:#6b7280;margin-bottom:4px;">{subtitle}</div>' if subtitle else ""
    how_to_read_html = f'<div style="font-size:0.75rem;color:#9ca3af;font-style:italic;">{how_to_read}</div>' if how_to_read else ""
    st.markdown(
        f'<div style="margin:18px 0 4px 0;">'
        f'<div style="font-size:0.92rem;font-weight:700;color:#111827;margin-bottom:2px;">{title}</div>'
        f'{subtitle_html}'
        f'{how_to_read_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _chart_footer(text: str):
    """Renders a styled insight callout below a chart."""
    st.markdown(
        f'<div style="font-size:0.76rem;color:#6b7280;padding:5px 0 10px 0;border-top:1px solid #f1f5f9;margin-top:2px;">'
        f'{text}</div>',
        unsafe_allow_html=True,
    )


def _legend_pills(items: list):
    """Renders coloured pill legend below a chart. items = [(label, hex_color, description)]"""
    def _desc_html(desc):
        return f'<span style="color:#9ca3af;"> — {desc}</span>' if desc else ""

    pills_html = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:4px;margin:0 8px 4px 0;">'
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:{col};flex-shrink:0;"></span>'
        f'<span style="font-size:0.74rem;color:#374151;"><b>{lbl}</b>'
        f'{_desc_html(desc)}'
        f'</span></span>'
        for lbl, col, desc in items
    )
    st.markdown(
        f'<div style="display:flex;flex-wrap:wrap;margin:4px 0 10px 0;">{pills_html}</div>',
        unsafe_allow_html=True,
    )


def _base_layout(**kw) -> dict:
    base = dict(CHART_LAYOUT)  # white bg, Inter font, standard grid
    base.update(
        font=dict(family=_FONT, size=11, color="#374151"),
        margin=dict(t=48, b=32, l=12, r=50),
        hoverlabel=dict(bgcolor="white", bordercolor="#e5e7eb",
                        font=dict(family=_FONT, size=11)),
    )
    base.update(kw)
    return base


def _chart_sentiment_diverging(sent_data: list):
    """Diverging bar — positive right, negative left. One clear message per brand."""
    if not sent_data:
        return None
    # Cap at 12 brands so chart stays readable
    sent_data = sent_data[:12]
    brands   = [d["brand"] for d in sent_data]
    pos_vals = [d["pos_pct"] for d in sent_data]
    neg_vals = [-d["neg_pct"] for d in sent_data]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Positive", x=pos_vals, y=brands, orientation="h",
        marker=dict(color=_P["green"], opacity=0.88, line=dict(width=0)),
        text=[f"+{v:.0f}%" for v in pos_vals],
        textposition="inside",
        textfont=dict(size=10, color="white", family=_FONT),
        hovertemplate="<b>%{y}</b> — Positive: %{x:.1f}%<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Negative", x=neg_vals, y=brands, orientation="h",
        marker=dict(color=_P["red"], opacity=0.82, line=dict(width=0)),
        text=[f"{d['neg_pct']:.0f}%" for d in sent_data],
        textposition="inside",
        textfont=dict(size=10, color="white", family=_FONT),
        hovertemplate="<b>%{y}</b> — Negative: %{customdata:.1f}%<extra></extra>",
        customdata=[d["neg_pct"] for d in sent_data],
    ))
    x_max = max(pos_vals, default=0)
    for d in sent_data:
        color = _P["green"] if d["net"] >= 0 else _P["red"]
        sign  = "+" if d["net"] >= 0 else ""
        fig.add_annotation(
            x=x_max + 3, y=d["brand"],
            text=f"<b>{sign}{d['net']:.0f}</b>",
            font=dict(size=9, color=color, family=_FONT),
            showarrow=False, xanchor="left",
        )
    fig.update_layout(**_base_layout(
        barmode="relative",
        height=max(260, 34 * len(brands) + 70),
        margin=dict(t=44, b=28, l=8, r=70),
        yaxis=dict(tickfont=dict(size=10, family=_FONT), showgrid=False, autorange="reversed"),
        xaxis=dict(ticksuffix="%", tickfont=dict(size=9, color="#9ca3af"),
                   gridcolor="#f1f5f9", zeroline=True,
                   zerolinecolor="#d1d5db", zerolinewidth=2),
        legend=dict(orientation="h", x=0, y=1.05, font=dict(size=10, family=_FONT)),
    ))
    return fig


def _chart_attribute_heatmap(brand_attrs: dict):
    """Brand × attribute heatmap — % of verbatims mentioning each topic."""
    if not brand_attrs:
        return None
    attrs  = list(_ATTRS.keys())
    brands = sorted(brand_attrs.keys(), key=lambda b: brand_attrs[b]["_n"], reverse=True)[:10]

    z   = [[brand_attrs[b].get(a, 0) for a in attrs] for b in brands]
    txt = [[f"{v:.0f}%" for v in row] for row in z]

    fig = go.Figure(go.Heatmap(
        z=z,
        x=[a.replace(" / ", "\n") for a in attrs],
        y=[f"{b}  (n={brand_attrs[b]['_n']:,})" for b in brands],
        text=txt,
        texttemplate="%{text}",
        textfont=dict(size=9, color="white", family=_FONT),
        colorscale=[[0, "#f0fdf4"], [0.3, "#86efac"], [0.6, "#30a76a"], [1, "#1a5d4d"]],
        zmin=0, zmax=60,
        showscale=True,
        colorbar=dict(title=dict(text="%", font=dict(size=9)),
                      ticksuffix="%", thickness=10, len=0.7,
                      tickfont=dict(size=8)),
        hovertemplate="<b>%{y}</b> — %{x}<br>%{z:.1f}% of verbatims<extra></extra>",
    ))
    fig.update_layout(**_base_layout(
        height=max(220, 38 * len(brands) + 90),
        margin=dict(t=44, b=50, l=140, r=60),
        xaxis=dict(side="top", tickfont=dict(size=9, family=_FONT), tickangle=0),
        yaxis=dict(tickfont=dict(size=9, family=_FONT), autorange="reversed"),
    ))
    return fig


def _chart_zone_word_bars(zone_words: dict):
    """One simple bar per zone showing top over-indexing words. Clear, not cramped."""
    zones = [z for z in ["North", "South", "East", "West"] if z in zone_words and zone_words[z]]
    if not zones:
        return None

    # Show as 2×2 grid of individual charts for readability
    figs = {}
    for zone in zones:
        words_data = zone_words[zone][:5]
        labels = [wd[0].capitalize() for wd in words_data]
        ratios = [wd[1] for wd in words_data]
        zc = _ZONE_C.get(zone, "#94a3b8")

        fig = go.Figure(go.Bar(
            x=ratios, y=labels, orientation="h",
            marker=dict(color=zc, opacity=0.80, line=dict(width=0)),
            text=[f"{r:.1f}×" for r in ratios],
            textposition="outside",
            textfont=dict(size=10, color="#374151", family=_FONT),
            hovertemplate="%{y}: %{x:.2f}× national avg<extra></extra>",
        ))
        fig.update_layout(**_base_layout(
            height=220,
            margin=dict(t=36, b=20, l=8, r=50),
            title=dict(text=f"<b>{zone}</b>", font=dict(size=12, color=zc), x=0),
            xaxis=dict(title="over-index vs national", tickfont=dict(size=8),
                       gridcolor="#f1f5f9", range=[0, max(ratios) * 1.4]),
            yaxis=dict(tickfont=dict(size=10, family=_FONT), autorange="reversed", showgrid=False),
        ))
        figs[zone] = fig

    return figs


def _chart_transcript_sentiment_bar(intel: dict):
    """Simple stacked bar: positive / neutral / negative per brand. Max 8 brands.
    Works with both old tree-based intel (has 'sentiment' key) and new matrix intel
    (has 'promoter_pct' / 'positive_resolution_pct' instead)."""

    def _pos_pct(b):
        bd = intel[b]
        if "sentiment" in bd:
            return bd["sentiment"].get("pos_pct", 0)
        return bd.get("positive_resolution_pct", 0)

    def _neg_pct(b):
        bd = intel[b]
        if "sentiment" in bd:
            return bd["sentiment"].get("neg_pct", 0)
        # Approximate from NPS/relationship data
        nps = bd.get("nps_signals", {})
        n   = max(bd.get("n_docs", 1), 1)
        return round(nps.get("detractor", 0) / n * 100, 1)

    brands = sorted(
        [b for b in intel if b != "__null_doc_count__" and isinstance(intel[b], dict)
         and intel[b].get("n_docs", intel[b].get("n_passages", 0)) >= 1],
        key=_pos_pct, reverse=True,
    )[:8]
    if not brands:
        return None

    pos_vals = [_pos_pct(b) for b in brands]
    neg_vals = [_neg_pct(b) for b in brands]
    neu_vals = [max(0, 100 - pos_vals[i] - neg_vals[i]) for i in range(len(brands))]
    ns = [intel[b].get("n_docs", intel[b].get("n_passages", 0)) for b in brands]

    fig = go.Figure()
    for name, vals, color in [
        ("Positive", pos_vals, _P["green"]),
        ("Neutral",  neu_vals, "#cbd5e1"),
        ("Negative", neg_vals, _P["red"]),
    ]:
        fig.add_trace(go.Bar(
            name=name, x=vals,
            y=[f"{b} (n={n})" for b, n in zip(brands, ns)],
            orientation="h",
            marker=dict(color=color, line=dict(width=0), opacity=0.88),
            text=[f"{v:.0f}%" if v >= 8 else "" for v in vals],
            textposition="inside",
            textfont=dict(size=10, color="white" if color != "#cbd5e1" else "#374151", family=_FONT),
            hovertemplate=f"<b>%{{y}}</b> — {name}: %{{x:.1f}}%<extra></extra>",
        ))

    fig.update_layout(**_base_layout(
        barmode="stack",
        height=max(240, 38 * len(brands) + 80),
        margin=dict(t=40, b=28, l=8, r=16),
        yaxis=dict(autorange="reversed", tickfont=dict(size=10, family=_FONT), showgrid=False),
        xaxis=dict(ticksuffix="%", range=[0, 105], tickfont=dict(size=9, color="#9ca3af"),
                   gridcolor="#f1f5f9", zeroline=False),
        legend=dict(orientation="h", x=0, y=1.06, font=dict(size=10, family=_FONT)),
    ))
    return fig


# ═════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _kpi(val, label, color=None):
    color = color or _P["teal"]
    st.markdown(
        f'<div style="border:1px solid {color}28;border-radius:10px;padding:12px 14px;'
        f'background:{color}08;text-align:center;">'
        f'<div style="font-size:1.6rem;font-weight:900;color:{color};line-height:1.1;">{val}</div>'
        f'<div style="font-size:0.66rem;font-weight:700;color:#6b7280;margin-top:3px;'
        f'text-transform:uppercase;letter-spacing:0.05em;">{label}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _hl(text: str, kw: str) -> str:
    esc = _html_mod.escape(text)
    if not kw.strip():
        return esc
    pat = re.compile(re.escape(_html_mod.escape(kw.strip())), re.IGNORECASE)
    return pat.sub(
        lambda m: (f"<mark style='background:#fef08a;padding:1px 4px;border-radius:3px;"
                   f"font-weight:700;color:#92400e;'>{m.group()}</mark>"),
        esc,
    )


def _brand_pill(brand: str, color: str) -> str:
    return (f'<span style="background:{color};color: #e5e7eb;padding:2px 10px;border-radius:20px;'
            f'font-size:0.72rem;font-weight:700;">{_html_mod.escape(brand)}</span>')


def _tag(text: str, color: str = "#6b7280") -> str:
    return (f'<span style="background:{color}15;color:{color};padding:2px 8px;border-radius:6px;'
            f'font-size:0.71rem;font-weight:600;border:1px solid {color}28;">'
            f'{_html_mod.escape(str(text))}</span>')


import re as _re_qe

def _md_bold(text: str) -> str:
    """Convert **bold** markdown to <b>bold</b> HTML."""
    return _re_qe.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

def _parse_ai_sections(text: str, keys: list) -> dict:
    """Parse AI text into sections. Handles both KEY: and **KEY** formats."""
    sections = {k: [] for k in keys}
    current = None
    # Build regex: matches '**KEY**:?', '**KEY:**', 'KEY:', case-insensitive
    escaped_keys = '|'.join(_re_qe.escape(k) for k in keys)
    pattern = _re_qe.compile(
        r'^\*{0,2}(' + escaped_keys + r')\*{0,2}:?\*{0,2}\s*$|'
        r'^\*{0,2}(' + escaped_keys + r')\*{0,2}:(.*)$',
        _re_qe.IGNORECASE,
    )
    for line in text.strip().splitlines():
        ls = line.strip()
        m = pattern.match(ls)
        if m:
            key = (m.group(1) or m.group(2) or "").upper()
            rest = (m.group(3) or "").strip().strip('*').strip()
            if key in sections:
                current = key
                if rest:
                    sections[current].append(rest)
        elif current and ls:
            sections[current].append(ls.strip('*').strip() if ls.startswith('**') and ls.endswith('**') else ls)
    return sections

def _render_transcript_ai(brand: str, text: str, accent: str,
                          keys: list = None, icons_cfg: dict = None):
    """Render AI insight. Keys + icons are project-specific (from ui_config)."""
    _KEYS = keys or ["WHAT CONSUMERS LOVE", "PAIN POINTS", "BRAND EQUITY SIGNAL", "STRATEGIC SIGNAL"]
    sections = _parse_ai_sections(text, _KEYS)

    _DEFAULT_ICONS = {
        "WHAT CONSUMERS LOVE":  ("▲", _P["green"]),
        "PAIN POINTS":          ("▼", _P["red"]),
        "BRAND EQUITY SIGNAL":  ("◆", _P["purple"]),
        "STRATEGIC SIGNAL":     ("→", _P["teal"]),
        "WHAT THIS SEGMENT WANTS": ("▲", _P["green"]),
        "TRUST SIGNALS":        ("◆", _P["purple"]),
    }
    if icons_cfg:
        _DEFAULT_ICONS.update({
            k: tuple(v) if isinstance(v, (list, tuple)) and len(v) >= 2 else ("→", accent)
            for k, v in icons_cfg.items()
        })
    icons = {k: _DEFAULT_ICONS.get(k, ("→", accent)) for k in _KEYS}
    has_structured = any(sections.values())
    if has_structured:
        parts = [
            f'<div style="border-left:4px solid {accent};border-radius:6px;'
            f'background:#f8fafc;padding:14px 18px;">'
            f'<div style="font-size:0.68rem;font-weight:800;letter-spacing:0.08em;'
            f'color:{accent};text-transform:uppercase;margin-bottom:12px;">AI Insight · {_html_mod.escape(brand)}</div>'
        ]
        for key, (icon, col_) in icons.items():
            lines = sections.get(key, [])
            if lines:
                # _md_bold converts **text** → <b>text</b> before escaping individual segments
                body = "<br>".join(_md_bold(_html_mod.escape(ln)) for ln in lines)
                parts.append(
                    f'<div style="margin-bottom:12px;padding:10px 12px;background:{col_}08;'
                    f'border-radius:6px;border-left:3px solid {col_}40;">'
                    f'<div style="font-size:0.68rem;font-weight:700;color:{col_};text-transform:uppercase;'
                    f'letter-spacing:0.06em;margin-bottom:6px;">{icon} {key}</div>'
                    f'<div style="font-size:0.85rem;color:#374151;line-height:1.75;">{body}</div>'
                    f'</div>'
                )
        parts.append('</div>')
        st.markdown(''.join(parts), unsafe_allow_html=True)
    else:
        # Fallback: if text looks like markdown (##, |, **), render via st.markdown
        if any(ln.strip().startswith(('##', '|', '**')) for ln in text.splitlines()[:8]):
            st.markdown(
                f'<div style="border-left:4px solid {accent};border-radius:6px;'
                f'padding:14px 18px;">',
                unsafe_allow_html=True,
            )
            st.markdown(text)
        else:
            body = "<br>".join(_md_bold(_html_mod.escape(ln)) if ln.strip() else "&nbsp;"
                               for ln in text.strip().splitlines())
            st.markdown(
                f'<div style="border-left:4px solid {accent};background:#f8fafc;'
                f'border-radius:6px;padding:14px 18px;font-size:0.85rem;color:#374151;line-height:1.75;">'
                f'{body}</div>',
                unsafe_allow_html=True,
            )


def _section(title: str, sub: str = "", accent: str = None, icon: str = ""):
    col = accent or _P["teal"]
    sub_html = f'<span style="font-size:0.72rem;color:#9ca3af;margin-left:8px;">{_html_mod.escape(sub)}</span>' if sub else ""
    icon_html = f'<span style="margin-right:6px;">{icon}</span>' if icon else ""
    st.markdown(
        f'<div style="background:linear-gradient(90deg,{col}12,transparent);'
        f'border-left:3px solid {col};border-radius:0 8px 8px 0;'
        f'padding:8px 14px;margin:20px 0 12px;">'
        f'<span style="font-size:0.88rem;font-weight:800;color:{col};">{icon_html}{_html_mod.escape(title)}</span>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ═════════════════════════════════════════════════════════════════════════════
# PAGE HEADER
# ═════════════════════════════════════════════════════════════════════════════

_STATUS_COLORS = {
    "processed": _P["green"], "extracted": _P["teal"],
    "converting": _P["amber"], "raw": "#94a3b8", "error": _P["red"],
}
_STATUS_ICONS = {
    "processed": "✓", "extracted": "●", "converting": "⋯", "raw": "○", "error": "✗",
}

# ── Combined header + project selector ────────────────────────────────────────
_aproj_meta = _active_proj_meta
_aproj_stat = _pm.get_status(_active_project)
# Live disk scan for active project — registry values go stale after ZIP install
_aproj_t_dir = _BASE / "data" / "projects" / _active_project / "transcripts"
_aproj_m_dir = _BASE / "data" / "projects" / _active_project / "matrices"
_aproj_t_fmt = _aproj_meta.get("transcript_format", "md")
_aproj_t_ext = "*.docx" if _aproj_t_fmt == "docx" else "*.md"
_aproj_n  = (len(list(_aproj_t_dir.glob(_aproj_t_ext))) if _aproj_t_dir.exists() else 0) or _aproj_meta.get("transcript_count", 0)
_aproj_mn = (len(list(_aproj_m_dir.glob("*_matrix.json"))) if _aproj_m_dir.exists() else 0) or _aproj_meta.get("matrix_count", 0)
_aproj_sc   = _STATUS_COLORS.get(_aproj_stat, "#94a3b8")
_aproj_dn   = _aproj_meta.get("display_name", _active_project)
_aproj_desc = _aproj_meta.get("description","")[:90]

# Build project pill HTML for each project
_proj_pills_html = ""
for _pp in _all_projects:
    _pp_id    = _pp["id"]
    _pp_stat  = _pm.get_status(_pp_id)
    _pp_sc    = _STATUS_COLORS.get(_pp_stat, "#94a3b8")
    _pp_si    = _STATUS_ICONS.get(_pp_stat, "○")
    _pp_t_dir = _BASE / "data" / "projects" / _pp_id / "transcripts"
    _pp_m_dir = _BASE / "data" / "projects" / _pp_id / "matrices"
    _pp_t_fmt = _pp.get("transcript_format", "md")
    _pp_t_ext = "*.docx" if _pp_t_fmt == "docx" else "*.md"
    _pp_n     = (len(list(_pp_t_dir.glob(_pp_t_ext))) if _pp_t_dir.exists() else 0) or _pp.get("transcript_count", 0)
    _pp_mn    = (len(list(_pp_m_dir.glob("*_matrix.json"))) if _pp_m_dir.exists() else 0) or _pp.get("matrix_count", 0)
    _pp_sn    = _pp["display_name"].split("—")[0].strip() if "—" in _pp["display_name"] else _pp["display_name"][:22]
    _is_a     = (_pp_id == _active_project)
    _pill_bg  = "white" if _is_a else "rgba(255,255,255,0.12)"
    _pill_col = _P["teal"] if _is_a else "rgba(255,255,255,0.7)"
    _pill_bdr = f"2px solid {_P['teal']}" if _is_a else "1px solid rgba(255,255,255,0.18)"
    _proj_pills_html += (
        f'<span style="display:inline-flex;align-items:center;gap:6px;'
        f'background:{_pill_bg};border:{_pill_bdr};border-radius:20px;'
        f'padding:5px 14px;font-size:0.76rem;font-weight:{"800" if _is_a else "600"};'
        f'color:{_pill_col};margin-right:6px;white-space:nowrap;">'
        f'<span style="color:{_pp_sc};font-size:0.70rem;">{_pp_si}</span>'
        f'{_html_mod.escape(_pp_sn)}'
        f'<span style="opacity:0.6;font-weight:500;font-size:0.68rem;">&nbsp;{_pp_mn}/{_pp_n}</span>'
        f'</span>'
    )

st.markdown(
    f'<div style="background:linear-gradient(135deg,#0a2e22 0%,#1a5d4d 60%,#0d4a6e 100%);'
    f'border-radius:14px;padding:18px 22px 14px;margin-bottom:12px;color: #e5e7eb;">'
    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
    f'<div>'
    f'<div style="font-size:0.60rem;font-weight:700;letter-spacing:0.14em;'
    f'color:rgba(255,255,255,0.45);text-transform:uppercase;margin-bottom:3px;">'
    f'Qualitative Intelligence</div>'
    f'<div style="font-size:1.45rem;font-weight:900;color: #e5e7eb;letter-spacing:-0.01em;'
    f'margin-bottom:4px;">{_html_mod.escape(_aproj_dn)}</div>'
    f'<div style="font-size:0.76rem;color:rgba(255,255,255,0.55);margin-bottom:10px;">'
    f'{_html_mod.escape(_aproj_desc)}</div>'
    f'</div>'
    f'<div style="text-align:right;flex-shrink:0;padding-left:16px;">'
    f'<div style="font-size:0.60rem;color:rgba(255,255,255,0.4);text-transform:uppercase;'
    f'letter-spacing:0.1em;margin-bottom:3px;">Status</div>'
    f'<div style="font-size:0.8rem;font-weight:700;color:{_aproj_sc};">'
    f'{_STATUS_ICONS.get(_aproj_stat,"○")} {_aproj_stat}</div>'
    f'<div style="font-size:0.68rem;color:rgba(255,255,255,0.4);margin-top:2px;">'
    f'{_aproj_mn} extracted / {_aproj_n} total</div>'
    f'</div>'
    f'</div>'
    f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:0;">'
    f'{_proj_pills_html}'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)

# Project switcher buttons (invisible, click triggers rerun)
_sw_cols = st.columns(len(_all_projects) + 1)
for _swi, _swp in enumerate(_all_projects):
    if _swp["id"] != _active_project:
        with _sw_cols[_swi]:
            _sw_short = _swp["display_name"].split("—")[0].strip()[:18]
            if st.button(f"Switch to {_sw_short}", key=f"sw_{_swp['id']}", use_container_width=True):
                st.session_state["active_project"] = _swp["id"]
                for _k in ["ti_open_doc","ti_city_filter","ti_page","cdcx_open_doc"]:
                    st.session_state.pop(_k, None)
                st.rerun()
with _sw_cols[-1]:
    if st.button("↻ Refresh Projects", key="refresh_projects", use_container_width=True):
        new_ids = _pm.scan_for_new_projects()
        if new_ids:
            st.info(f"New projects found: {', '.join(new_ids)} — switch to them and run extraction from their Inspector tab.")
        else:
            st.toast("No new projects found.")
        st.cache_data.clear()
        st.rerun()

with st.expander("➕ Upload New Project (ZIP)", expanded=False):

    with st.expander("📖 How to structure your ZIP — full spec", expanded=False):
        st.markdown("""
**ZIP structure (all paths relative to ZIP root or one top-level folder):**

```
my-project.zip
├── transcripts/                 ← REQUIRED — your interview files
│   ├── Interview_01.docx        ← .docx or .md, any filename
│   ├── Interview_02.docx
│   └── ...
│
├── source_docs/                 ← RECOMMENDED — used to auto-generate schema
│   ├── DG_MyStudy.docx          ← Discussion Guide (any name matching *DG*.docx)
│   └── AI_Prompt_MyStudy.docx  ← Analysis brief (any name matching *prompt*.docx)
│
├── schema/                      ← OPTIONAL — pre-built schema (skip schema_generator)
│   ├── master_prompt.txt        ← If present, used directly for extraction
│   └── extraction_schema.json  ← If present, skips schema_generator entirely
│
└── project.json                 ← OPTIONAL — auto-created from ZIP filename if missing
```

---

**Path 1 — Auto-generate schema (recommended for new projects)**

Put your Discussion Guide + Analysis Brief in `source_docs/`. After upload, click
**Run Schema Generator** (or the full pipeline runs it automatically). The app calls
`schema_generator.py` which reads both docs via LLM and produces:
- `schema/extraction_schema.json` — JSON extraction schema
- `schema/master_prompt.txt` — system prompt for extraction

File naming conventions for auto-detection:
- Discussion Guide: `DG_*.docx`, `*DG*.docx`, `*discussion*guide*.docx`
- Analysis Brief: `AI_Prompt*.docx`, `*prompt*.docx`, `*analysis*.docx`, `*brief*.docx`

---

**Path 2 — Pre-built schema (fastest, full control)**

Write `schema/master_prompt.txt` yourself and include it in the ZIP. Format:

```
You are a Senior Qualitative Research Consultant...

STUDY: Your Study Name
TYPE: ethnographic / concept_testing / usability / other
RESPONDENT SEGMENTS: key1, key2, key3
SUMMARY: One paragraph describing the study objective, sample, cities.

---

PHASE 1 — FREE-FORM REASONING (no schema constraints)

Read the COMPLETE transcript before writing anything. Think through:
A. WHO IS THIS RESPONDENT? (profile, demographics, mindset)
B. CORE BEHAVIOUR AND CONTEXT
C. REACTIONS AND RESPONSES
D. PAIN POINTS AND BARRIERS
E. SIGNALS AND MOTIVATIONS

---

PHASE 2 — STRUCTURED JSON EXTRACTION

Extract into this exact schema:
{
  "respondent": {
    "city": "string",
    "brand_owned": "string",    ← or "segment" for concept tests
    "gender": "string",
    "age_band": "string",
    "occupation": "string"
  },
  "pain_points": [
    {
      "issue_description": "string",
      "severity": "critical|high|medium|low",
      "product_area": "string",
      "verbatim_quote": "string"
    }
  ],
  "all_passages": [
    {
      "content": "verbatim quote ≥30 chars",
      "sentiment": "positive|negative|neutral|ambivalent",
      "topic": "snake_case_topic",
      "pain_point": true/false,
      "decision_signal": true/false
    }
  ],
  "nps_signal": "promoter|passive|detractor|unclear",
  "emotional_resolution": "positive|neutral|negative",
  "narrative_tags": ["tag1", "tag2"]
}

TRANSCRIPT:
{{TRANSCRIPT_CONTENT}}
```

> `{{TRANSCRIPT_CONTENT}}` is replaced automatically by the extractor. Do not remove it.

---

**`project.json` — optional, auto-created if missing**

```json
{
  "id": "my-project",
  "display_name": "My Study — Brand X Consumer Research",
  "study_type": "ethnographic",
  "transcript_format": "docx",
  "description": "50-word study description",
  "segment_key": "brand_owned",
  "filter_keys": ["brand_owned", "city", "journey_stage"],
  "data_paths": {
    "transcripts": "projects/my-project/transcripts",
    "matrices":    "projects/my-project/matrices",
    "source_docs": "projects/my-project/source_docs",
    "schema":      "projects/my-project/schema/extraction_schema.json"
  },
  "ui_config": {
    "entity_label": "Brand",
    "ai_insight_keys": ["WHAT CONSUMERS LOVE", "PAIN POINTS", "BRAND EQUITY SIGNAL", "STRATEGIC SIGNAL"],
    "ai_insight_icons": {
      "WHAT CONSUMERS LOVE":  ["▲", "#22c55e"],
      "PAIN POINTS":          ["▼", "#ef4444"],
      "BRAND EQUITY SIGNAL":  ["◆", "#a855f7"],
      "STRATEGIC SIGNAL":     ["→", "#14b8a6"]
    },
    "tab_descriptions": {
      "Deep Dive": "Per-brand deep dive",
      "Pain Points & Barriers": "Extracted pain points by severity",
      "Themes & Narratives": "Narrative tag frequency",
      "Brand Analysis": "Brand health and signals",
      "Health & Trust": "Trust and NPS signals",
      "Passage Search": "Full-text verbatim search",
      "Inspector": "Per-interview deep read"
    }
  }
}
```

**`segment_key`** — the respondent field used for grouping in Deep Dive tab.
Use `"brand_owned"` for brand studies, `"segment"` for concept tests.

**`filter_keys`** — fields shown as filter dropdowns. Must match keys in `respondent` JSON.

---

**Transcript file naming — best practice**

```
Brand_City_Gender_Age.docx          ← e.g. Crompton_Delhi_F_35.docx
DI_01_SegmentName_City.docx         ← e.g. DI_01_StockInvestor_Delhi.docx
```
Naming is flexible — the extractor reads content, not filename.
Metadata (city, brand, segment) should appear in the transcript body or filename
so the LLM can extract them into `respondent.*` fields.

---

**Minimum viable ZIP (fastest path):**

```
my-project.zip
└── transcripts/
    ├── interview_01.md   ← paste transcript as markdown
    └── interview_02.md
```
Upload → switch to project → paste master_prompt.txt in the editor → extract.
""")

    _up_zip = st.file_uploader(
        "Project ZIP file", type=["zip"], key="new_proj_zip",
        label_visibility="collapsed",
    )

    _up_id_override = st.text_input(
        "Project ID (optional — leave blank to derive from filename)",
        placeholder="e.g. my-study-2026",
        key="new_proj_id_override",
    ).strip().lower()

    if _up_zip and st.button("📦 Install Project", key="install_proj_btn", type="primary"):
        import zipfile, io, re as _re2

        # Derive project ID
        _stem = Path(_up_zip.name).stem
        _raw_id = _up_id_override if _up_id_override else _stem
        _proj_id_new = _re2.sub(r"-+", "-", _re2.sub(r"[^a-z0-9-]", "-", _raw_id.lower())).strip("-")

        _proj_dir = _BASE / "data" / "projects" / _proj_id_new

        if _proj_dir.exists():
            st.error(f"Project '{_proj_id_new}' already exists. Choose a different ID or delete the existing folder.")
        else:
            try:
                with zipfile.ZipFile(io.BytesIO(_up_zip.getvalue())) as _zf:
                    _names = _zf.namelist()

                    # Detect top-level folder prefix (e.g. my-project/ wrapping everything)
                    _top = _names[0].split("/")[0] + "/" if _names else ""
                    _all_under_top = _top and all(n.startswith(_top) or n == _top.rstrip("/") for n in _names)
                    _prefix = _top if _all_under_top else ""

                    # Validate: must have transcripts/
                    _has_t = any(
                        (n[len(_prefix):] if _prefix else n).startswith("transcripts/")
                        for n in _names
                    )
                    if not _has_t:
                        st.error("ZIP must contain a `transcripts/` folder at its root (or inside a top-level folder).")
                    else:
                        _proj_dir.mkdir(parents=True, exist_ok=True)

                        for _zi in _zf.infolist():
                            _rel = _zi.filename[len(_prefix):] if _prefix else _zi.filename
                            if not _rel or _rel.endswith("/"):
                                continue
                            _dest = _proj_dir / _rel
                            _dest.parent.mkdir(parents=True, exist_ok=True)
                            with _zf.open(_zi) as _src, open(_dest, "wb") as _dst:
                                _dst.write(_src.read())

                        # Auto-create project.json if missing
                        _pj = _proj_dir / "project.json"
                        if not _pj.exists():
                            _t_dir_new = _proj_dir / "transcripts"
                            _has_docx = any(_t_dir_new.glob("*.docx")) if _t_dir_new.exists() else False
                            _t_fmt_new = "docx" if _has_docx else "md"
                            _display_new = _stem.replace("-", " ").replace("_", " ").title()
                            _minimal = {
                                "id":               _proj_id_new,
                                "display_name":     _display_new,
                                "transcript_format": _t_fmt_new,
                                "status":           "raw",
                                "description":      "",
                                "segment_key":      "brand_owned",
                                "filter_keys":      ["city"],
                                "data_paths": {
                                    "transcripts": f"projects/{_proj_id_new}/transcripts",
                                    "matrices":    f"projects/{_proj_id_new}/matrices",
                                    "source_docs": f"projects/{_proj_id_new}/source_docs",
                                    "schema":      f"projects/{_proj_id_new}/schema/extraction_schema.json",
                                },
                                "created_at": str(__import__("datetime").date.today()),
                            }
                            _pj.write_text(json.dumps(_minimal, indent=2), encoding="utf-8")

                        # Register in registry.json
                        _reg_path = _BASE / "data" / "projects" / "registry.json"
                        _reg = json.loads(_reg_path.read_text(encoding="utf-8")) if _reg_path.exists() else {"projects": []}
                        _known = {p["id"] for p in _reg.get("projects", [])}
                        if _proj_id_new not in _known:
                            _reg.setdefault("projects", []).append(json.loads(_pj.read_text(encoding="utf-8")))
                            _reg_path.write_text(json.dumps(_reg, indent=2, ensure_ascii=False), encoding="utf-8")

                        # Switch to new project
                        st.session_state["active_project"] = _proj_id_new
                        st.cache_data.clear()
                        st.success(f"Project '{_proj_id_new}' installed. Switching…")
                        st.rerun()

            except zipfile.BadZipFile:
                st.error("Not a valid ZIP file.")
            except Exception as _uex:
                st.error(f"Install failed: {_uex}")

st.markdown("<div style='margin:4px 0;'></div>", unsafe_allow_html=True)

opts = _load_opts()
brand_idx_map = {b: i for i, b in enumerate(opts["brands"])}

# ─────────────────────────────────────────────────────────────────────────────
# Generic project setup UI — works for any project registered in registry.json.
# Renders file structure + master prompt editor + extraction trigger.
# Called from the generic project block AND can be reused by project-specific views.
# ─────────────────────────────────────────────────────────────────────────────
def _render_project_setup(proj_id: str, proj: dict):
    paths        = proj.get("abs_paths", {})
    display_name = proj.get("display_name", proj_id)
    t_dir        = paths.get("transcripts")
    m_dir        = paths.get("matrices")
    schema_path  = paths.get("schema")
    src_dir      = paths.get("source_docs")
    t_fmt        = proj.get("transcript_format", "md")
    mp_path      = (schema_path.parent / "master_prompt.txt") if schema_path else None
    status       = _pm.get_status(proj_id)
    _sc_h        = _STATUS_COLORS.get(status, "#94a3b8")
    m_count      = len(list(m_dir.glob("*_matrix.json"))) if m_dir and m_dir.exists() else 0

    # Project header
    st.markdown(
        f'<div style="background:#faf5ff;border:1.5px solid #e9d5ff;border-radius:12px;'
        f'padding:14px 18px;margin-bottom:16px;">'
        f'<div style="font-size:0.68rem;font-weight:700;color:{_P["purple"]};'
        f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px;">Active Project</div>'
        f'<div style="font-size:1.1rem;font-weight:900;color:#111827;">'
        f'{_html_mod.escape(display_name)}</div>'
        f'<div style="font-size:0.78rem;color:#6b7280;margin-top:4px;">'
        f'<span style="color:{_sc_h};font-weight:700;">Status: {status}</span>'
        f'&nbsp;·&nbsp;{m_count} matrices extracted</div></div>',
        unsafe_allow_html=True,
    )

    if m_count > 0:
        # Matrices exist — caller (generic renderer) handles display. Just return silently.
        return

    # No matrices yet — show setup / confirm flow
    st.markdown(
        f'<div style="background:#fffbeb;border:1.5px solid #fcd34d;border-radius:12px;'
        f'padding:16px 20px;margin-bottom:16px;">'
        f'<div style="font-size:0.88rem;font-weight:700;color:#92400e;margin-bottom:6px;">'
        f'⚠ No extracted matrices yet — review prompt then run extraction</div>'
        f'<div style="font-size:0.82rem;color:#78350f;line-height:1.7;">'
        f'Uses OpenRouter free-tier. Each transcript ≈ 2 LLM calls. Takes 5–30 min total.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Project structure
    with st.expander("📁 Project Structure — review before extraction", expanded=True):
        _ps_c1, _ps_c2 = st.columns(2)
        with _ps_c1:
            st.markdown(
                f'<div style="font-size:0.72rem;font-weight:700;color:{_P["teal"]};'
                f'text-transform:uppercase;letter-spacing:0.07em;margin-bottom:8px;">Source Docs</div>',
                unsafe_allow_html=True,
            )
            if src_dir and src_dir.exists():
                for _sf in sorted(src_dir.iterdir()):
                    _sz = round(_sf.stat().st_size / 1024, 1)
                    _ic = "📄" if _sf.suffix == ".docx" else ("📊" if _sf.suffix == ".pptx" else "📁")
                    st.markdown(
                        f'<div style="font-size:0.77rem;padding:4px 0;border-bottom:1px solid #f1f5f9;'
                        f'display:flex;justify-content:space-between;">'
                        f'<span>{_ic} {_html_mod.escape(_sf.name)}</span>'
                        f'<span style="color:#9ca3af;">{_sz} KB</span></div>',
                        unsafe_allow_html=True,
                    )
            if schema_path and schema_path.exists():
                _ss = round(schema_path.stat().st_size / 1024, 1)
                st.markdown(
                    f'<div style="font-size:0.77rem;padding:4px 0;margin-top:4px;">'
                    f'📋 {schema_path.name} &nbsp;<span style="color:#9ca3af;">{_ss} KB</span></div>',
                    unsafe_allow_html=True,
                )
        with _ps_c2:
            st.markdown(
                f'<div style="font-size:0.72rem;font-weight:700;color:{_P["teal"]};'
                f'text-transform:uppercase;letter-spacing:0.07em;margin-bottom:8px;">Transcripts</div>',
                unsafe_allow_html=True,
            )
            t_ext = "*.docx" if t_fmt == "docx" else "*.md"
            _trans_files = []
            if t_dir and t_dir.exists():
                _trans_files = sorted(t_dir.glob(t_ext))
                if not _trans_files and t_fmt == "docx":
                    _proc = t_dir / "processed"
                    _trans_files = sorted(_proc.glob("*.md")) if _proc.exists() else []
            for _tf in _trans_files[:30]:
                _tsz = round(_tf.stat().st_size / 1024, 1)
                st.markdown(
                    f'<div style="font-size:0.72rem;padding:3px 0;border-bottom:1px solid #f1f5f9;'
                    f'display:flex;justify-content:space-between;">'
                    f'<span>📝 {_html_mod.escape(_tf.name)}</span>'
                    f'<span style="color:#9ca3af;">{_tsz} KB</span></div>',
                    unsafe_allow_html=True,
                )
            if len(_trans_files) > 30:
                st.caption(f"… and {len(_trans_files) - 30} more")
            if not _trans_files:
                st.warning(f"No {t_ext} files found in transcripts folder.")

    # Master prompt
    _master_txt = ""
    if mp_path and mp_path.exists():
        try:
            _master_txt = mp_path.read_text(encoding="utf-8")
        except Exception:
            pass

    _section("🤖 Extraction Master Prompt", "Review · edit · confirm before running extraction")

    if _master_txt:
        _json_start       = _master_txt.find("\n{")
        _rules_start      = _master_txt.find("\nCRITICAL RULES:")
        _transcript_start = _master_txt.find("\nTRANSCRIPT:")
        _role_section     = _master_txt[:_json_start].strip() if _json_start != -1 else _master_txt[:600]
        _schema_section   = (
            _master_txt[_json_start:_rules_start].strip()
            if _json_start != -1 and _rules_start != -1 else ""
        )
        _rules_section    = (
            _master_txt[_rules_start:_transcript_start].strip()
            if _rules_start != -1 and _transcript_start != -1 else ""
        )

        st.markdown(
            f'<div style="background:#f0fdf4;border-left:4px solid {_P["teal"]};'
            f'border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:8px;">'
            f'<div style="font-size:0.68rem;font-weight:800;color:{_P["teal"]};'
            f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">'
            f'Role · Context · Pre-extraction Reasoning</div>'
            f'<pre style="font-size:0.76rem;color:#1f2937;white-space:pre-wrap;'
            f'line-height:1.7;margin:0;font-family:monospace;">'
            f'{_html_mod.escape(_role_section)}</pre></div>',
            unsafe_allow_html=True,
        )
        if _schema_section:
            with st.expander("{ } JSON Schema — click to expand", expanded=False):
                st.code(_schema_section, language="json")
        if _rules_section:
            st.markdown(
                f'<div style="background:#faf5ff;border-left:4px solid {_P["purple"]};'
                f'border-radius:0 8px 8px 0;padding:12px 16px;margin-top:6px;">'
                f'<div style="font-size:0.68rem;font-weight:800;color:{_P["purple"]};'
                f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">'
                f'Critical Rules · Scoring Definitions</div>'
                f'<pre style="font-size:0.76rem;color:#1f2937;white-space:pre-wrap;'
                f'line-height:1.7;margin:0;font-family:monospace;">'
                f'{_html_mod.escape(_rules_section)}</pre></div>',
                unsafe_allow_html=True,
            )
        if st.toggle("✏️ Edit master prompt before extraction", key=f"{proj_id}_edit_prompt"):
            _edit_txt = st.text_area(
                "master_prompt.txt",
                value=_master_txt,
                height=500,
                key=f"{proj_id}_prompt_edit_area",
            )
            if st.button("💾 Save prompt", key=f"{proj_id}_save_prompt"):
                try:
                    mp_path.write_text(_edit_txt, encoding="utf-8")
                    st.success("Prompt saved.")
                    st.rerun()
                except Exception as _pe:
                    st.error(f"Save failed: {_pe}")
    else:
        st.warning("master_prompt.txt not found at expected path.")
        st.caption(f"Expected: {mp_path}")
        _new_prompt = st.text_area(
            "Create master_prompt.txt",
            placeholder="Paste your extraction system prompt here…",
            height=400,
            key=f"{proj_id}_create_prompt",
        )
        if st.button("💾 Save new prompt", key=f"{proj_id}_create_prompt_save",
                     disabled=not _new_prompt.strip() if "_new_prompt" in dir() else True):
            try:
                mp_path.parent.mkdir(parents=True, exist_ok=True)
                mp_path.write_text(_new_prompt, encoding="utf-8")
                st.success("Prompt saved. Now run extraction.")
                st.rerun()
            except Exception as _cpe:
                st.error(f"Save failed: {_cpe}")

    # Extraction trigger
    st.markdown("<div style='margin:10px 0;'></div>", unsafe_allow_html=True)
    _ex_c1, _ex_c2 = st.columns([2, 1])
    with _ex_c1:
        st.info(
            f"**Full pipeline:** schema → docx→md → extraction → verification → findings.\n"
            f"Saves matrices to `projects/{proj_id}/matrices/`.\n"
            f"Manual: `python infoleap/skills/project_extractor.py --project {proj_id}`"
        )
    with _ex_c2:
        _confirm_key = f"_{proj_id}_extraction_confirmed"
        if st.button(
            "▶ Run Extraction Now", type="primary",
            use_container_width=True, key=f"{proj_id}_trigger_extraction",
            disabled=(not _master_txt),
        ):
            if st.session_state.get(_confirm_key):
                t_count = proj.get("transcript_count", "?")
                with st.spinner(f"Running full pipeline for {t_count} transcripts… keep this tab open."):
                    try:
                        _results = _pm.trigger_processing(proj_id)
                        if _results.get("ok"):
                            st.success("Pipeline complete! Refresh page to see results.")
                        else:
                            st.error("Pipeline finished with errors.")
                        for _step in _results.get("steps", []):
                            _icon = "✓" if _step["ok"] else "✗"
                            st.markdown(f"`{_icon} {_step['step']}`")
                            if not _step["ok"] and _step.get("error"):
                                st.code(_step["error"][-400:])
                    except Exception as _xe:
                        st.error(f"Error: {_xe}")
                st.session_state.pop(_confirm_key, None)
            else:
                st.session_state[_confirm_key] = True
                t_count = proj.get("transcript_count", "?")
                st.warning(
                    f"⚠ This will call OpenRouter for ~{t_count} transcripts. "
                    f"Click **Run Extraction Now** again to confirm."
                )


# ═════════════════════════════════════════════════════════════════════════════
# EXTRACTION STUDIO — human-in-the-loop redo of the extraction pipeline.
# Files -> Run Step 1 (initial findings) -> Review & select -> Finalize (Step 2 + write matrices)
# -> optional Reconcile. Works even when matrices already exist (unlike _render_project_setup,
# which silently no-ops once m_count > 0) — this is how you redo a project that's already been
# through the pipeline once, per file, with a human checkpoint before anything is committed.
# ═════════════════════════════════════════════════════════════════════════════
def _auto_select_criteria(entries: list, matrices_dir, doc_id_fn, cap: int = 10) -> set:
    """
    Default file selection by criteria, not just 'everything unprocessed': stratify by
    (segment, city) metadata so the default set spreads across the study's natural groups
    instead of whatever happens to be alphabetically/chronologically first, prioritizing
    unprocessed files within each group. Round-robin across groups until cap is reached.
    """
    buckets: dict = {}
    for fn, e in entries:
        meta = e.get("metadata", {})
        key = (meta.get("segment", ""), meta.get("city", ""))
        buckets.setdefault(key, []).append(fn)

    def _is_processed(fn: str) -> bool:
        doc_id = doc_id_fn(fn)
        return bool(matrices_dir and (matrices_dir / f"{doc_id}_matrix.json").exists())

    for v in buckets.values():
        v.sort(key=_is_processed)  # unprocessed (False) first

    picked: set = set()
    bucket_iters = {k: iter(v) for k, v in buckets.items() if v}
    while len(picked) < cap and bucket_iters:
        for k in list(bucket_iters.keys()):
            try:
                fn = next(bucket_iters[k])
            except StopIteration:
                del bucket_iters[k]
                continue
            picked.add(fn)
            if len(picked) >= cap:
                break
    return picked


def _infer_field_def_from_matrices(fname: str, matrices_dir) -> dict:
    """Infer a real type/shape for a field being restored in Step 2b, instead of blindly
    stubbing it as an untyped string. Found live on CoinDCX: route1_evaluation/route2_evaluation
    are real objects with sub_fields (appeal_rating, appeal_reasons, credibility_gaps) in every
    one of 23 matrices, but a prior schema regeneration dropped their object/sub_fields
    definition and the old restore path put them back as plain `{"type": "string"}` stubs —
    silently losing per-sub-field narrowing in Step 2b (the field just showed a bare checkbox,
    no way to review/narrow appeal_rating etc.) and the correct object shape in
    master_prompt.txt's DIMENSIONS block (extraction kept working only because the model
    ignored the wrong schema type, not because anything here was correct)."""
    _desc = ("Restored — was in existing matrices but dropped by a schema regeneration; "
             "type/shape inferred from real matrix data.")
    if matrices_dir and matrices_dir.exists():
        for mf in matrices_dir.glob("*_matrix.json"):
            try:
                v = json.loads(mf.read_text(encoding="utf-8")).get(fname)
            except Exception as _e:
                print(f"[quote_explorer] WARN: failed to parse {mf.name} (field sample): {_e}")
                continue
            if v in (None, "", [], {}):
                continue
            if isinstance(v, dict):
                sub_fields = []
                for k, sv in v.items():
                    sf_type = ("boolean" if isinstance(sv, bool) else
                                "integer" if isinstance(sv, int) else
                                "array" if isinstance(sv, list) else "string")
                    sub_fields.append({"name": k, "type": sf_type, "description": ""})
                return {"type": "object", "description": _desc, "sub_fields": sub_fields}
            if isinstance(v, list):
                return {"type": "array", "description": _desc}
            if isinstance(v, bool):
                return {"type": "boolean", "description": _desc}
            if isinstance(v, int):
                return {"type": "integer", "description": _desc}
            return {"type": "string", "description": _desc}
    return {"type": "string", "description": _desc}


def _doc_id_for_project(proj: dict, filename: str) -> str:
    """Project-aware doc_id, matching whatever pipeline actually extracted this
    project's matrices — the two pipelines disagree and neither is universal:

    - concept_testing projects (e.g. CoinDCX): docx transcripts named "DI N_...",
      extracted via project_extractor.py's CLI, which derives doc_id via
      _doc_id_from_filename()'s "DI\\s*(\\d+)" regex → "DI_N".
    - ethnographic projects (e.g. Mixer): pre-processed .md transcripts with no
      such naming convention, extracted via lens/ingestion/transcript_matrix_builder.py,
      which uses doc_id = Path(filename).stem verbatim (see that file's `md_path.stem`).

    Using the wrong one for a project silently breaks every doc-id-keyed lookup in
    Extraction Studio (existing-matrix detection, quality badges, staleness, and —
    critically — which filename a re-extraction writes to, which could otherwise
    create an orphaned duplicate matrix the dashboard never reads).
    """
    from infoleap.skills import project_extractor as _pex
    if proj.get("transcript_format") == "md":
        from pathlib import Path as _Path
        return _Path(filename).stem
    return _pex._doc_id_from_filename(filename)


def _render_pipeline_sync_banner(proj_id: str):
    """Glance-level warning when schema/matrices/ui_config.json/reconciliation have
    drifted out of sync — today the only place this was visible was buried inside
    Extraction Studio's per-file staleness list. Shown once at the top of every
    project view (concept_testing, ethnographic, generic) regardless of renderer."""
    from infoleap.skills.project_extractor import pipeline_sync_status
    try:
        _sync = pipeline_sync_status(proj_id)
    except Exception:
        return
    if _sync.get("warnings"):
        st.warning(
            "⚠ **Pipeline out of sync** — " + "  \n".join(f"- {w}" for w in _sync["warnings"])
        )


def _render_extraction_studio(proj_id: str, proj: dict):
    from infoleap.skills import project_extractor as _pex

    paths = proj.get("abs_paths", {})
    t_dir = paths.get("transcripts")
    schema_path = paths.get("schema")
    mp_path = (schema_path.parent / "master_prompt.txt") if schema_path else None
    project_dir = schema_path.parent.parent if schema_path else None
    index_path = (t_dir / "processed_index.json") if t_dir else None
    matrices_dir = paths.get("matrices")

    if not (schema_path and schema_path.exists() and mp_path and mp_path.exists()
            and index_path and index_path.exists()):
        _missing = []
        if not (schema_path and schema_path.exists()):
            _missing.append("`extraction_schema.json` (run schema generation)")
        if not (mp_path and mp_path.exists()):
            _missing.append("`master_prompt.txt` (produced alongside the schema)")
        if not (index_path and index_path.exists()):
            _missing.append("`processed_index.json` (created on first extraction run)")
        st.caption(
            "Extraction Studio can't run yet — missing: " + "; ".join(_missing) +
            ". Use the project's Setup section (or ▶ Run Extraction Now for a first pass) before "
            "coming back here to review a sample."
        )
        return

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    master_prompt = mp_path.read_text(encoding="utf-8")
    step1_section, step2_section = _pex.split_master_prompt(master_prompt)
    index = json.loads(index_path.read_text(encoding="utf-8"))

    mismatches = (schema.get("discovery") or {}).get("brief_dg_mismatch") or []

    _sel_key   = f"es_{proj_id}_selected"
    _s1_key    = f"es_{proj_id}_step1"
    _ack_key   = f"es_{proj_id}_ack_mismatch"
    _disc_key  = f"es_{proj_id}_last_discovery"
    st.session_state.setdefault(_sel_key, {})
    st.session_state.setdefault(_s1_key, {})

    entries = [(fn, e) for fn, e in index.items() if e.get("status") in ("ok", "skipped")]
    entries.sort(key=lambda x: x[0])

    # Sample size — was a hardcoded cap=10 with no UI control. Keyed into _auto_key so
    # changing it recomputes the selection instead of serving a stale cached set.
    _cap_key = f"es_{proj_id}_cap"
    _cap = st.number_input(
        "Sample size for review", min_value=1, max_value=max(len(entries), 1),
        value=min(st.session_state.get(_cap_key, 10), max(len(entries), 1)),
        key=_cap_key, help="How many transcripts Extraction Studio auto-selects for the "
                            "sample-and-review pass, stratified by segment/city.",
    )

    # Auto-selection computed up front (not just inside Step 1's block) so the status strip
    # below reflects the real effective selection on the very first render — mirrors exactly
    # what each file checkbox will default to, without needing the checkboxes to have executed
    # yet this pass (session_state for a checkbox key only exists after that widget has run).
    _auto_key = f"es_{proj_id}_auto_selected_{_cap}"
    if _auto_key not in st.session_state:
        st.session_state[_auto_key] = _auto_select_criteria(
            entries, matrices_dir, lambda f: _doc_id_for_project(proj, f), cap=_cap)
    auto_selected = st.session_state[_auto_key]
    selected = [fn for fn, _e in entries
                if st.session_state[_sel_key].get(fn, fn in auto_selected)]

    n_extracted = sum(1 for fn, _ in entries
                       if matrices_dir and (matrices_dir / f"{_doc_id_for_project(proj, fn)}_matrix.json").exists())
    n_pending_review = sum(1 for r in st.session_state[_s1_key].values() if r.get("status") == "pending_review")
    _has_discovery = bool(st.session_state.get(_disc_key) or schema.get("discovery"))

    # ── How this works — plain-language explainer, expanded by default (was collapsed and
    # showed a hardcoded CoinDCX example regardless of which project you were looking at —
    # confusing/misleading for any other study). Now open by default and the example is built
    # from THIS project's real matrix (if one exists yet) or its real schema field names —
    # never another project's field vocabulary.
    def _build_example_snippet(proj_id: str, schema: dict, matrices_dir) -> str:
        if matrices_dir and matrices_dir.exists():
            _mf = next(iter(sorted(matrices_dir.glob("*_matrix.json"))), None)
            if _mf:
                try:
                    _real = json.loads(_mf.read_text(encoding="utf-8"))
                    _shown = {
                        k: v for k, v in _real.items()
                        if not k.startswith("_") and k not in ("all_passages", "word_count")
                    }
                    _keys = list(_shown.keys())[:4]
                    _snippet = {k: _shown[k] for k in _keys}
                    _snippet["_quality_score"] = _real.get("_quality_score")
                    _snippet["_quality_label"] = _real.get("_quality_label")
                    return json.dumps(_snippet, indent=2, ensure_ascii=False, default=str)
                except Exception:
                    pass
        # No matrices yet — build a schema-shaped placeholder from this project's own fields.
        _fields = (schema.get("layer2", {}) or {}).get("fields", {}) or {}
        _example: dict = {"doc_id": "<respondent_id>"}
        for _fname, _fdef in list(_fields.items())[:4]:
            _vals = _fdef.get("values") or []
            _example[_fname] = _vals[0] if _vals else f"<{_fdef.get('type', 'value')}>"
        _example["_quality_score"] = "<0-100, verified against source transcript>"
        _example["_quality_label"] = "<poor / fair / good / excellent>"
        return json.dumps(_example, indent=2, ensure_ascii=False)

    with st.expander("ℹ️ How this works — read this first", expanded=True):
        st.markdown(
            "**This turns raw interview transcripts into structured, chartable data in 5 stages "
            "— run them top to bottom, in order:**\n\n"
            "1. **Files** — pick which transcripts to work with (a smart default is pre-selected for you).\n"
            "2. **Scope & thinking guidance** *(optional)* — tell the AI what to focus on in plain English.\n"
            "3. **Discover & refresh prompt** — the AI reads your selected transcripts once and decides "
            "what fields/questions THIS study needs — this is a one-time setup step, not run per-interview.\n"
            "4. **Run extraction** — for every selected file, the AI reads that ONE transcript and fills in "
            "the fields decided in step 3, producing one JSON file per respondent (a \"matrix\").\n"
            "5. **Review & finalize** — you can edit or reject the AI's findings before they're locked in, "
            "then the final structured JSON is written to disk and the dashboard picks it up automatically."
        )
        st.markdown(
            f"**Example of the final output for *{proj.get('display_name', proj_id)}*** — "
            + ("one real respondent from this project:" if (matrices_dir and matrices_dir.exists()
               and any(matrices_dir.glob('*_matrix.json')))
               else "shaped from this project's own schema fields (no respondents extracted yet):")
        )
        st.code(_build_example_snippet(proj_id, schema, matrices_dir), language="json")
        st.caption("This exact file is what the dashboard's charts and KPIs read from — nothing is "
                   "computed separately. If a chart looks wrong, the fix is always in this file.")

    # ── Status strip ───────────────────────────────────────────────────────────
    def _pill(label: str, value, ok: bool):
        col = _P["green"] if ok else "#94a3b8"
        st.markdown(
            f'<div style="flex:1;min-width:120px;background:{col}10;border:1px solid {col}40;'
            f'border-radius:8px;padding:8px 12px;">'
            f'<div style="font-size:0.62rem;font-weight:700;color:{col};text-transform:uppercase;'
            f'letter-spacing:0.06em;">{_html_mod.escape(label)}</div>'
            f'<div style="font-size:1rem;font-weight:800;color:#111827;">{value}</div></div>',
            unsafe_allow_html=True,
        )
    _strip_cols = st.columns(5)
    with _strip_cols[0]: _pill("Files", f"{len(entries)} total", True)
    with _strip_cols[1]: _pill("Selected", len(selected), len(selected) > 0)
    with _strip_cols[2]: _pill("Discovery", "done" if _has_discovery else "not run", _has_discovery)
    with _strip_cols[3]: _pill("Pending review", n_pending_review, n_pending_review > 0)
    with _strip_cols[4]: _pill("Matrices", f"{n_extracted}/{len(entries)}", n_extracted > 0)
    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)

    _uncovered_obj = schema.get("_uncovered_objectives") or []
    _not_chartable_obj = schema.get("_not_chartable_objectives") or []
    _scope_fields_missing = schema.get("_scope_fields_missing") or []
    _scope_fields_confirmed_absent = schema.get("_scope_fields_confirmed_absent") or []

    # All 4 gap/warning checks below used to render as always-open st.warning() blocks stacked
    # one after another before the user ever reached Phase 1 — 4 walls of text with no priority
    # order, competing with the Phase 1/2 badges below that already summarize the same thing.
    # Collapsed into one expander, badge-counted, auto-expanded only when something here needs
    # action (an unacknowledged mismatch or a genuinely missing scope field) — not for the
    # purely informational "confirmed absent" case.
    _needs_action = bool(mismatches) or bool(_scope_fields_missing)
    _n_issues = (len(mismatches) + len(_uncovered_obj) + len(_not_chartable_obj)
                 + len(_scope_fields_missing) + len(_scope_fields_confirmed_absent))
    acknowledged = True
    # Hidden for now, per explicit request — scope-named fields now get auto-applied right after
    # Discovery (see the 🔎 Run discovery handler above), so this panel's main previously-manual
    # job (Search/Add stub per missing field) mostly happens automatically now. Kept behind this
    # flag rather than deleted so it's a one-line flip to bring back, e.g. for a project where
    # auto-resolution didn't find evidence and a human needs to look at _scope_fields_confirmed_absent.
    _SHOW_GAPS_WARNINGS_PANEL = False
    if _n_issues and _SHOW_GAPS_WARNINGS_PANEL:
        with st.expander(f"⚠ {_n_issues} gap/warning item(s) from Discovery & scope checks",
                          expanded=_needs_action):
            if mismatches:
                st.warning(
                    "⚠ **Discovery found assumptions in the brief/DG that the transcripts don't "
                    "actually support:**\n\n" + "\n".join(f"- {m}" for m in mismatches)
                )
                acknowledged = st.checkbox("I've reviewed these mismatches", key=_ack_key)

            if _uncovered_obj or _not_chartable_obj:
                _msg = "⚠ **Schema completeness gaps found by the last discovery run:**\n\n"
                if _uncovered_obj:
                    _msg += "Not mapped to any field at all:\n" + "\n".join(f"- {o}" for o in _uncovered_obj) + "\n\n"
                if _not_chartable_obj:
                    _msg += ("Covered only narratively (no chartable field, so no chart will show this):\n"
                              + "\n".join(f"- {o}" for o in _not_chartable_obj))
                st.warning(_msg)

            # Deterministic cross-check: fields your own Scope guidance ("1b" below) explicitly
            # names (e.g. "Schema fields: `tax_advantage_noticed`, ...") but that never got created
            # by any discovery run — doesn't depend on Discovery's transcript sample happening to
            # surface the topic, unlike _uncovered_objectives above. Fuzzy-matched against every
            # field AND sub-field name so a renamed/restructured field isn't flagged as missing.
            if _scope_fields_missing:
                with st.container(border=True):
                    st.warning(
                        "⚠ **Fields your scope guidance named explicitly, but no discovery run ever "
                        "created:**\n\nThese come from the \"Schema fields:\" lines in your Section 1b "
                        "guidance — grounded in what YOU said this study needs, not dependent on "
                        "Discovery's transcript sample happening to surface the topic."
                    )
                    st.caption(
                        "🔍 Search = re-reads real transcripts for genuine evidence before creating the "
                        "field (never fabricates — reports back honestly if nothing's there). "
                        "+ Add stub = force-create an empty placeholder yourself, no evidence required."
                    )
                    if st.button("🔍 Search all missing fields for evidence",
                                 key=f"{proj_id}_es_search_all_scope"):
                        from infoleap.skills import schema_generator as _sg
                        with st.spinner(f"Searching transcripts for {len(_scope_fields_missing)} "
                                         f"field(s)…"):
                            _result = _sg.resolve_missing_scope_fields(proj_id, _scope_fields_missing)
                        if _result["resolved"]:
                            st.success(f"Found real evidence for {len(_result['resolved'])} field(s): "
                                       f"{list(_result['resolved'].keys())}")
                        if _result["confirmed_absent"]:
                            st.info(f"Searched {len(_result['transcripts_checked'])} transcript(s), no "
                                    f"evidence for {len(_result['confirmed_absent'])} field(s): "
                                    f"{_result['confirmed_absent']}")
                        st.cache_data.clear()
                        st.rerun()
                    for _mf in _scope_fields_missing:
                        _c1, _c2, _c3 = st.columns([3, 1.4, 1])
                        _c1.markdown(f"- `{_mf}`")
                        if _c2.button("🔍 Search for evidence", key=f"{proj_id}_es_search_{_mf}"):
                            from infoleap.skills import schema_generator as _sg
                            with st.spinner(f"Searching transcripts for `{_mf}`…"):
                                _result = _sg.resolve_missing_scope_fields(proj_id, [_mf])
                            if _mf in _result["resolved"]:
                                _fd = _result["resolved"][_mf]
                                st.success(
                                    f"Found real evidence in `{_fd['_discovery_source_doc']}`: "
                                    f"\"{_fd['_discovery_source_quote']}\""
                                )
                            else:
                                st.info(f"Searched {len(_result['transcripts_checked'])} transcript(s) "
                                        f"— genuinely no evidence for `{_mf}`.")
                            st.cache_data.clear()
                            st.rerun()
                        if _c3.button("+ Add stub", key=f"{proj_id}_es_addstub_{_mf}"):
                            schema.setdefault("layer2", {}).setdefault("fields", {})[_mf] = {
                                "type": "string",
                                "description": (
                                    f"Auto-added stub — named in scope guidance's \"Schema fields:\" "
                                    f"list but never created by any discovery run. Refine the type/"
                                    f"values/rule before relying on it in analysis."
                                ),
                            }
                            # The field now genuinely exists in schema — but _scope_fields_missing
                            # is a separate static list (only rewritten by a full generate_schema
                            # run), so without removing _mf here it kept showing up in this exact
                            # "no discovery run ever created" section forever after Add stub was
                            # clicked, making the button look like it did nothing. Same class of
                            # bug the 🔍 Search path already avoids (schema_generator.py:1912-1913).
                            schema["_scope_fields_missing"] = [
                                f for f in schema.get("_scope_fields_missing", []) if f != _mf]
                            schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False),
                                                    encoding="utf-8")
                            from infoleap.skills import schema_generator as _sg
                            _sg._resync_master_prompt_from_schema(proj_id)
                            st.success(f"Added `{_mf}` as a stub field and resynced master_prompt.txt.")
                            st.cache_data.clear()
                            st.rerun()
            if _scope_fields_confirmed_absent:
                with st.container(border=True):
                    st.info(
                        "ℹ **Searched real transcripts for these — genuinely no evidence found:**\n\n"
                        + "\n".join(f"- `{f}`" for f in _scope_fields_confirmed_absent)
                        + "\n\nNot fabricated to close the gap. Widen the transcript selection in Step 1 "
                        "and re-search, or accept these objectives aren't covered by this dataset."
                    )

    # ── Phase 1: Setup — files & guidance ────────────────────────────────────
    _p1_badge = "✅ Done"
    _p1_verdict = f"{len(entries)} transcript(s) available"
    with st.expander(
        f"{_p1_badge}  ·  PHASE 1 — Setup: files & guidance  ·  {_p1_verdict}",
        expanded=False,
    ):
        st.caption(
            "Pick which transcripts to work with, and tell the AI what this study is about and "
            "how to reason about it. Feeds directly into Phase 2's schema design."
        )
        # ── Step 1: Files ──────────────────────────────────────────────────────────
        _section("1 · Files", "Pick which transcripts to work with below", accent=_P["teal"], icon="📁")
        with st.container(border=True):
            st.caption(
                f"When you first open this, {len(auto_selected)} of {len(entries)} files are "
                f"pre-checked (stratified across segment × city, prioritizing not-yet-extracted "
                f"transcripts) — check/uncheck any file below to change that. Your current selection "
                f"is shown in the status strip above and the counter to the right of the buttons below."
            )

            # ── quality lookup (cheap — only reads matrices for files that already have one) ──
            def _matrix_quality(doc_id: str):
                if not matrices_dir:
                    return None
                p = matrices_dir / f"{doc_id}_matrix.json"
                if not p.exists():
                    return None
                try:
                    return json.loads(p.read_text(encoding="utf-8")).get("_quality_label")
                except Exception as _e:
                    print(f"[quote_explorer] WARN: failed to parse {p.name} (quality label): {_e}")
                    return None

            _quality_color = {"excellent": _P["green"], "good": "#3b82f6",
                               "poor": "#f59e0b", "critical": "#dc2626"}

            # ── bulk actions row ─────────────────────────────────────────────────────
            bc1, bc2, bc3, bc4 = st.columns([1, 1, 1, 2])
            with bc1:
                if st.button("☑ Select all", key=f"{proj_id}_es_sel_all", use_container_width=True):
                    for fn, _e in entries:
                        st.session_state[_sel_key][fn] = True
                        st.session_state[f"{proj_id}_es_file_{fn}"] = True
                    st.rerun()
            with bc2:
                if st.button("☐ Select none", key=f"{proj_id}_es_sel_none", use_container_width=True):
                    for fn, _e in entries:
                        st.session_state[_sel_key][fn] = False
                        st.session_state[f"{proj_id}_es_file_{fn}"] = False
                    st.rerun()
            with bc3:
                if st.button("↺ Reset to default", key=f"{proj_id}_es_sel_reset", use_container_width=True):
                    for fn, _e in entries:
                        _v = fn in auto_selected
                        st.session_state[_sel_key][fn] = _v
                        st.session_state[f"{proj_id}_es_file_{fn}"] = _v
                    st.rerun()
            with bc4:
                st.markdown(
                    f"<div style='text-align:right;padding-top:6px;font-size:0.82rem;color:#6b7280;'>"
                    f"<b style='color:#111827;'>{len(selected)}</b> of {len(entries)} selected · "
                    f"🔁 already extracted · 🆕 not yet extracted</div>",
                    unsafe_allow_html=True,
                )

            st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

            # Which matrices predate the current schema (extracted before some of today's fields
            # existed) — computed once per render, not per file, since it reads every matrix.
            _stale_map = _pex.matrix_staleness(proj_id, schema)

            # ── file grid — always visible (not buried in a collapsed expander), 2 columns,
            # each row shows status icon, filename, word count, and quality badge if extracted ──
            with st.container(height=360, border=True):
                grid_cols = st.columns(2)
                for i, (fn, e) in enumerate(entries):
                    doc_id = _doc_id_for_project(proj, fn)
                    existing = bool(matrices_dir and (matrices_dir / f"{doc_id}_matrix.json").exists())
                    q_label = _matrix_quality(doc_id) if existing else None
                    _missing_fields = _stale_map.get(doc_id)
                    default_checked = st.session_state[_sel_key].get(fn, fn in auto_selected)
                    with grid_cols[i % 2]:
                        checked = st.checkbox(
                            f"{'🔁' if existing else '🆕'} {fn}", value=default_checked,
                            key=f"{proj_id}_es_file_{fn}",
                        )
                        st.session_state[_sel_key][fn] = checked
                        wc = e.get("word_count")
                        meta_bits = []
                        if wc:
                            meta_bits.append(f"{wc:,} words")
                        if q_label:
                            qc = _quality_color.get(q_label, "#9ca3af")
                            meta_bits.append(f"<span style='color:{qc};font-weight:700;'>{q_label}</span>")
                        if _missing_fields:
                            meta_bits.append(
                                f"<span style='color:#f59e0b;font-weight:700;' title='{_html_mod.escape(', '.join(_missing_fields))}'>"
                                f"⚠ stale — missing {len(_missing_fields)} field(s)</span>")
                        if meta_bits:
                            st.markdown(
                                f"<div style='margin:-8px 0 6px 26px;font-size:0.72rem;color:#6b7280;'>"
                                f"{' · '.join(meta_bits)}</div>", unsafe_allow_html=True)
            selected = [fn for fn, v in st.session_state[_sel_key].items() if v]

        def _resolve_md_path(fn: str):
            entry = index[fn]
            md_rel = entry.get("output_md")
            return (project_dir / md_rel) if md_rel else None

        # ── Step 1b: Scope & thinking guidance ─────────────────────────────────────
        _scope_path = schema_path.parent / "scope_notes.txt"
        _scope_key = f"{proj_id}_es_scope_edit"
        _section("1b · Scope & thinking guidance",
                 "Tell the LLM what to look for and how to reason — feeds discovery, schema, and extraction",
                 accent=_P["amber"], icon="🧭")
        with st.container(border=True):
            st.caption(
                "This is NOT a predefined field list — it's direction (\"focus on X\") the AI uses to "
                "decide what fields to populate itself. It flows straight into ↓ Step 2's discovery "
                "call and gets baked into the master prompt shown in Step 3."
            )

            # Show InfoLeap's actual source brief right here — the whole point of this box is to
            # reinforce/extend what's already in that document, not replace it, and the user
            # explicitly asked to see them side by side instead of guessing what's in the docx.
            source_docs_dir = project_dir / "source_docs" if project_dir else None
            _brief_path = None
            if source_docs_dir and source_docs_dir.exists():
                for pattern in ["AI_Prompt*.docx", "*prompt*.docx", "*analysis*.docx", "*brief*.docx"]:
                    matches = list(source_docs_dir.glob(pattern))
                    if matches:
                        _brief_path = matches[0]
                        break
            if _brief_path:
                with st.expander(f"📄 View InfoLeap's actual brief — {_brief_path.name}", expanded=False):
                    from infoleap.skills import schema_generator as _sg
                    _brief_text = _sg._read_docx(_brief_path)
                    st.text_area("brief_text", value=_brief_text, height=300,
                                  key=f"{proj_id}_es_brief_view", label_visibility="collapsed", disabled=True)
                    st.caption("Read-only — this is the source document, not editable here. Use the box "
                               "below to add direction on TOP of what's already asked for here.")
            else:
                st.caption("No AI Analysis Brief found in source_docs/ for this project.")

            _existing_scope = _scope_path.read_text(encoding="utf-8") if _scope_path.exists() else ""

            _DRAFT_MODEL_OPTIONS = _get_openrouter_model_options()

            _draft_col1, _draft_col2, _draft_col3 = st.columns([1.3, 1.3, 1.6])
            with _draft_col1:
                _draft_clicked = st.button("📝 Draft detailed guidance from brief",
                                            key=f"{proj_id}_es_draft_scope",
                                            disabled=not _brief_path)
            with _draft_col2:
                _draft_model_label = st.selectbox(
                    "Model", options=list(_DRAFT_MODEL_OPTIONS.keys()),
                    key=f"{proj_id}_es_draft_model", label_visibility="collapsed",
                )
            with _draft_col3:
                st.caption("Grounds the scope box in the actual brief above instead of a generic "
                           "placeholder — pulls out research objectives, respondent segments, and key "
                           "things to track, written as direction the AI can act on. Review before saving.")

            _draft_model = _DRAFT_MODEL_OPTIONS.get(_draft_model_label)
            if _draft_model == "__divider__":
                _draft_model = None
                st.warning("That's a section label, not a model — pick an actual model above.")

            if _draft_clicked and _brief_path:
                with st.spinner("Reading the brief and drafting detailed guidance…"):
                    from infoleap.skills.llm_client import call_llm_safe
                    _dg_text_for_draft = ""
                    if source_docs_dir:
                        for pattern in ["DG_*.docx", "dg_*.docx", "*DG*.docx", "*discussion*guide*.docx"]:
                            _m = list(source_docs_dir.glob(pattern))
                            if _m:
                                _dg_text_for_draft = _sg._read_docx(_m[0])
                                break
                    _draft_prompt = f"""You are a senior qualitative research methodologist writing a STRICT
    EXTRACTION DIRECTIVE for a junior AI analyst that is about to read real transcripts and fill a structured
    schema. This is not a briefing memo and not a summary of the brief (the AI already has the brief in every
    prompt). Every line you write must be an instruction the AI can directly act on while coding a transcript —
    if a sentence doesn't change what the AI extracts, flags, or rejects, cut it.

    AI ANALYSIS BRIEF (full source document, untruncated):
    {_brief_text}

    DISCUSSION GUIDE (full source document, untruncated): {_dg_text_for_draft if _dg_text_for_draft else '[not available]'}

    Output a STRUCTURED directive using markdown headers (##) and bullet points — NOT continuous prose. Use
    short, imperative, testable statements ("Extract:", "Accept:", "Reject:", "Decision rule:"), not narrative
    reasoning. Cover EVERY objective, named element, and segment actually present in the brief and discussion
    guide above — do not compress or drop items to hit a short length; a longer, complete directive is
    correct, a shorter incomplete one is not. Produce exactly these sections, in this order:

    ## STUDY FRAMING
    2-4 bullets. State the core tension/hypothesis this study resolves and the business decision it feeds.
    No throat-clearing — state it as a fact the AI must keep in mind while coding every transcript.

    ## OBJECTIVE → EVIDENCE RULES
    One bullet block per major research objective in the brief. For each: what counts as STRONG evidence
    (concrete example phrasing), what counts as WEAK/superficial evidence to reject or flag as thin, and
    which schema field(s) this maps to if inferable from context.

    ## SEGMENT DIFFERENTIATION RULES
    Bulleted decision rules only. State how named respondent segments are expected to diverge and the
    specific confound to control for before comparing them across segments (e.g. "Do not compare route
    preference across segments without first checking prior product familiarity — X may only prefer route
    Y because they misunderstood Z").

    ## NAMED-ELEMENT TRACKING CHECKLIST
    One bullet per specific claim/tagline/certification/concept the brief names for individual reaction
    tracking. Each bullet: element name — what a GENUINE reaction looks like (specific trust/comprehension
    inference) vs. a DEFLECTED/generic reaction (e.g. "sounds official" with no real inference).

    ## AMBIGUITY DECISION RULES
    Bulleted if/then rules only, one per known ambiguity type (stated intent vs. revealed behaviour, mixed/
    contradictory trust signals, segment-specific vocabulary gaps). Format: "IF [signal pattern] THEN
    [exact coding action]." No open-ended "flag it and think about it" — give the concrete rule.

    ## EVIDENCE DISCIPLINE (NON-NEGOTIABLE)
    3-5 bullets, imperative voice ("Never infer X from Y", "If evidence is absent, write DATA NOT AVAILABLE
    — do not estimate"), each tied to a concrete example from this brief of what fabrication would look like
    and why it would mislead the business decision.

    Use markdown headers and bullets exactly as specified above. Bold (**) specific field names, brand
    names, and concrete decision triggers so they scan quickly. Do not soften directives into prose —
    every bullet should read like a rule, not a reflection."""
                    _drafted = call_llm_safe(
                        [{"role": "user", "content": _draft_prompt}], max_tokens=8000, temp=0.2,
                        model=_draft_model)
                    if _drafted:
                        st.session_state[_scope_key] = _drafted.strip()
                        _existing_scope = _drafted.strip()
                        st.success("Drafted — review below, edit if needed, then Save.")
                    else:
                        st.error("Drafting failed — LLM call returned nothing. Try again or write manually.")

            _scope_text = st.text_area(
                "Project scope & thinking guidance", value=_existing_scope, height=220,
                key=_scope_key, label_visibility="collapsed",
                placeholder="e.g. Focus on portfolio-allocation behaviour and trust barriers to digital "
                            "gold. Think like a researcher weighing stated intent against actual habits, "
                            "not just what respondents say they'd do. Or click 'Draft detailed guidance "
                            "from brief' above to auto-fill this from the real source document.",
            )
            st.caption(f"{len(_scope_text.split())} words" if _scope_text else "Empty — either write "
                       "your own or draft from the brief above.")
            if st.button("💾 Save scope", key=f"{proj_id}_es_save_scope"):
                _scope_path.write_text(_scope_text, encoding="utf-8")
                st.success("Saved — next discovery run (Step 2 below) will use this.")

            # Staleness check — the ONLY thing that bakes scope_notes.txt into master_prompt.txt is
            # a full Discovery run (Step 2 below); Step 2b's Apply / stub-add only resync the
            # DIMENSIONS block, never this one. So a saved-but-not-rediscovered scope edit is
            # otherwise invisible: master_prompt.txt keeps sending the OLD guidance to every
            # extraction call with no signal anywhere in the UI. Compare current on-disk
            # scope_notes.txt against the hash of whatever scope text the last Discovery run
            # actually embedded (schema["_scope_synced_hash"], written in schema_generator.py).
            _on_disk_scope = _scope_path.read_text(encoding="utf-8") if _scope_path.exists() else ""
            _current_scope_hash = hashlib.sha256(_on_disk_scope.strip().encode("utf-8")).hexdigest()
            _synced_scope_hash = schema.get("_scope_synced_hash")
            if _on_disk_scope.strip() and _synced_scope_hash is not None and _current_scope_hash != _synced_scope_hash:
                st.warning(
                    "🔴 **Scope guidance changed since the last Discovery run.** "
                    "`master_prompt.txt` still sends the OLD guidance to every extraction call — "
                    "re-run **2 · Discover & refresh prompt** below to pick up your edit."
                )
            elif _on_disk_scope.strip() and _synced_scope_hash is not None:
                st.caption("✅ In sync — this is the scope guidance the last Discovery run baked into master_prompt.txt.")

    # ── Phase 2: Design the schema (one-time) ────────────────────────────────
    _p2_gap_count = len(_uncovered_obj) + len(_not_chartable_obj) + len(_scope_fields_missing)
    _p2_field_count = len(schema.get("layer2", {}).get("fields", {})) if schema else 0
    if _p2_field_count == 0:
        _p2_badge = "⏳ Not started"
    elif _p2_gap_count > 0:
        _p2_badge = "⚠ Needs attention"
    else:
        _p2_badge = "✅ Done"
    _p2_verdict = (
        f"{_p2_field_count} fields discovered"
        + (f" · {_p2_gap_count} coverage gap(s) flagged" if _p2_gap_count else " · no known coverage gaps")
    )
    with st.expander(
        f"{_p2_badge}  ·  PHASE 2 — Design the schema (one-time, on a small sample)  ·  {_p2_verdict}",
        expanded=(_p2_field_count == 0 or _p2_gap_count > 0),
    ):
        st.caption(
            "Reads 3-5 sample transcripts to propose which fields the AI should extract from "
            "every respondent, checks that against your Scope guidance and the brief, and lets "
            "you review/reconcile before running the full dataset. You normally do this once per "
            "project, then re-open it only if you change the brief or scope guidance."
        )
        # ── Step 2: Discover & refresh prompt ─────────────────────────────────────
        _section("2 · Discover & refresh prompt",
                 "Free-form discovery on selected transcripts → regenerates schema + prompt",
                 accent=_P["purple"], icon="🔎")
        with st.container(border=True):
            st.caption("Runs on exactly the files selected in Step 1. Recommended before the first "
                        "real run of a project, or after changing the file selection meaningfully. "
                        "3-5 transcripts is enough — discovery reads them individually and quote-verifies "
                        "everything before it's allowed to influence the schema.")
            if st.button("🔎 Run discovery on selected & refresh prompt",
                         disabled=(not selected), key=f"{proj_id}_es_discover", type="primary"):
                sample_paths = [p for p in (_resolve_md_path(fn) for fn in selected) if p and p.exists()]
                if not sample_paths:
                    st.error("No resolvable .md paths for the selected files.")
                else:
                    _scope_now = _scope_path.read_text(encoding="utf-8") if _scope_path.exists() else None
                    with st.spinner(f"Running discovery on {len(sample_paths)} transcripts, "
                                     f"regenerating schema + master prompt…"):
                        from infoleap.skills import schema_generator as _sg
                        try:
                            _sg.generate_schema(
                                proj_id, force=True, transcripts_dir=t_dir,
                                n_samples=len(sample_paths), skip_discovery=False,
                                sample_paths=sample_paths, user_scope=_scope_now,
                            )
                            fresh_schema = json.loads(schema_path.read_text(encoding="utf-8"))

                            # Auto-apply scope-named fields dynamically instead of requiring a
                            # manual Search/Add-stub click per field — resolve_missing_scope_fields
                            # already does real transcript evidence search + merges found fields
                            # into the schema + resyncs master_prompt.txt on its own; this just
                            # calls it automatically right after the schema that produced the gap
                            # list, instead of waiting for the user to open the (now hidden) gaps
                            # panel and click through each one by hand.
                            _auto_missing = fresh_schema.get("_scope_fields_missing") or []
                            _auto_resolved_names: list[str] = []
                            if _auto_missing:
                                _auto_result = _sg.resolve_missing_scope_fields(proj_id, _auto_missing)
                                _auto_resolved_names = list(_auto_result.get("resolved", {}).keys())
                                if _auto_resolved_names:
                                    fresh_schema = json.loads(schema_path.read_text(encoding="utf-8"))

                            # Ground enum fields in real quote examples so every one of the N
                            # per-transcript extraction calls after this judges values (e.g.
                            # coindcx_trust: high vs medium) against the same concrete evidence
                            # instead of each call inventing its own boundary from scratch — see
                            # generate_field_rubrics() for why this is the actual mechanism of
                            # cross-interview inconsistency. Reuses Discovery's already-verified
                            # quote pool, no extra transcript read needed.
                            _rubric_result = _sg.generate_field_rubrics(proj_id)
                            _n_anchored = len(_rubric_result.get("anchored", []))
                            if _n_anchored:
                                fresh_schema = json.loads(schema_path.read_text(encoding="utf-8"))

                            st.session_state[_disc_key] = fresh_schema.get("discovery")
                            st.session_state[f"{proj_id}_es_field_review_pending"] = True
                            st.cache_data.clear()
                            _n_new_fields = len(fresh_schema.get("layer2", {}).get("fields", {}))
                            _auto_note = (f" · auto-applied {len(_auto_resolved_names)} scope-named "
                                          f"field(s) from real transcript evidence: {_auto_resolved_names}"
                                          if _auto_resolved_names else "")
                            _anchor_note = (f" · grounded {_n_anchored} enum field(s) with example "
                                            f"quotes for judging consistency" if _n_anchored else "")
                            st.success(
                                f"Schema updated ({_n_new_fields} fields) → master_prompt.txt "
                                f"regenerated to match{_auto_note}{_anchor_note} → review the "
                                f"fields in Step 2b below, or jump straight to Step 3 to see the "
                                f"exact prompt text this produced."
                            )
                            # Show the causal link directly, right here — not buried three steps down —
                            # this is what was missing: seeing discovery's output become the prompt text.
                            if mp_path.exists():
                                _fresh_mp = mp_path.read_text(encoding="utf-8")
                                _dims_start = _fresh_mp.find("DIMENSIONS TO EXTRACT:")
                                _dims_end = _fresh_mp.find("ENUM CONSTRAINTS:")
                                if _dims_start != -1 and _dims_end != -1:
                                    with st.expander("→ See what changed in master_prompt.txt (Step 3)", expanded=True):
                                        st.caption("This is the exact block Step 3's master prompt now contains, "
                                                   "generated directly from the fields above:")
                                        st.code(_fresh_mp[_dims_start:_dims_end].strip(), language="text")
                        except Exception as e:
                            st.error(f"Discovery/refresh failed: {e}")

            _last_disc = st.session_state.get(_disc_key) or schema.get("discovery")
            if _last_disc:
                st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)
                with st.expander("🔍 Discovered from transcripts — what the refreshed prompt is grounded in",
                                  expanded=bool(st.session_state.get(_disc_key))):
                    st.markdown(f"**Study domain observed:** {_last_disc.get('study_domain_observed', '')}")
                    rtypes = _last_disc.get("respondent_types", [])
                    if rtypes:
                        st.markdown("**Respondent types:**")
                        for t in rtypes:
                            flag = " ⚠ unverified" if t.get("_unverified") else ""
                            st.markdown(f"- `{t.get('type_name')}`{flag} — {t.get('definition','')}  \n"
                                        f"  > *\"{t.get('distinguishing_quote','')}\"*")
                    etopics = _last_disc.get("emergent_topics", [])
                    if etopics:
                        st.markdown("**Emergent topics (not in the brief):**")
                        for t in etopics:
                            flag = " ⚠ unverified" if t.get("_unverified") else ""
                            st.markdown(f"- `{t.get('topic')}`{flag} — *\"{t.get('example_quote','')}\"*")
                    if _last_disc.get("brief_dg_mismatch"):
                        st.markdown("**Brief/DG assumptions not supported by transcripts:**")
                        for m in _last_disc["brief_dg_mismatch"]:
                            st.markdown(f"- {m}")
            else:
                st.caption("No discovery has been run yet for this project.")

        # Reload schema + master_prompt in case discovery just refreshed them above
        if schema_path.exists():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if mp_path.exists():
            master_prompt = mp_path.read_text(encoding="utf-8")
            step1_section, step2_section = _pex.split_master_prompt(master_prompt)

        # ── Step 2b: Review discovered fields ──────────────────────────────────────
        # The LLM self-populates this list from the transcripts (Step 0/1 of schema_generator) —
        # nothing here is a predefined field the user typed. This panel is the human checkpoint
        # before those self-discovered fields lock into the schema every extraction run after this
        # will be scored against — uncheck anything that looks invented or off-scope.
        _layer2_fields = schema.get("layer2", {}).get("fields", {})
        _matrix_field_counts = _pex.fields_populated_in_matrices(proj_id)
        _missing_from_schema = {k: c for k, c in _matrix_field_counts.items() if k not in _layer2_fields}
        if _layer2_fields or _missing_from_schema:
            _section("2b · Review discovered fields",
                     "Fields the LLM proposed from the transcripts — uncheck anything to drop before locking",
                     accent=_P["purple"], icon="🧩")
            with st.container(border=True):
                st.caption(
                    "These are the fields Step 2's discovery just decided on. Clicking Apply below "
                    "rewrites extraction_schema.json directly — Step 3's master prompt already reflects "
                    "the full set (it was generated in Step 2), so unchecking a field here only affects "
                    "what gets validated/scored, not the prompt text itself."
                )
                _fr_pending = st.session_state.pop(f"{proj_id}_es_field_review_pending", False)
                with st.expander(f"🧩 {len(_layer2_fields)} discovered field(s)", expanded=_fr_pending):
                    _fr_sel_key = f"{proj_id}_es_field_sel"
                    _fr_val_key = f"{proj_id}_es_field_val_sel"
                    _fr_rename_key = f"{proj_id}_es_field_rename"
                    st.session_state.setdefault(_fr_sel_key, {})
                    st.session_state.setdefault(_fr_val_key, {})
                    st.session_state.setdefault(_fr_rename_key, {})
                    for fname, fdef in _layer2_fields.items():
                        _default = st.session_state[_fr_sel_key].get(fname, True)
                        _mcount = _matrix_field_counts.get(fname, 0)
                        _label = f"`{fname}` ({fdef.get('type','string')}) — {fdef.get('description','')}"
                        if _mcount:
                            _label += f"  \n  ⚠ already populated in {_mcount} existing matrix/matrices — unchecking stops future extractions from filling it, existing data stays"
                        st.session_state[_fr_sel_key][fname] = st.checkbox(
                            _label, value=_default, key=f"{proj_id}_es_fchk_{fname}")

                        # Provenance — why this field exists, and which brief/DG objective it
                        # answers (schema_generator.py's structure_prompt now asks for both). This
                        # is the actual review signal: a field with a real, specific rationale is
                        # probably grounded; a missing or generic one is a red flag to drop or
                        # rewrite. Read-only — the reasoning itself isn't meant to be hand-edited,
                        # only judged.
                        if fdef.get("_source_objective") or fdef.get("_rationale"):
                            with st.container(border=True):
                                if fdef.get("_source_objective"):
                                    st.caption(f"📎 **From brief/DG:** {fdef['_source_objective']}")
                                if fdef.get("_rationale"):
                                    st.caption(f"💭 **Why this shape:** {fdef['_rationale']}")
                        elif st.session_state[_fr_sel_key][fname]:
                            st.caption("⚠ No source objective/rationale recorded — this field predates "
                                       "provenance tracking, or came from an emergent Discovery topic "
                                       "not tied to a specific brief objective.")

                        # Rename — the field name is fixed forever at Discovery time otherwise.
                        # Renaming here only relabels the schema key (and cascades through
                        # master_prompt.txt on Apply); existing matrices keep whatever key name
                        # they were extracted under, same staleness model as everywhere else in
                        # this panel (narrowing, restore) — a rename is a going-forward change.
                        _rename_default = st.session_state[_fr_rename_key].get(fname, fname)
                        st.session_state[_fr_rename_key][fname] = st.text_input(
                            "Field name", value=_rename_default, key=f"{proj_id}_es_frename_{fname}",
                            label_visibility="collapsed",
                        )
                        if st.session_state[_fr_rename_key][fname] != fname:
                            st.caption(f"↳ will rename to `{st.session_state[_fr_rename_key][fname]}` on Apply")

                        # Per-value narrowing — e.g. narrow a discovered "life_stage" field from
                        # 4 LLM-proposed values down to the 2 that actually matter for this study.
                        # The kept subset is written back as this field's enum constraint, so every
                        # extraction after Apply only classifies into the values checked here.
                        _values = fdef.get("values")
                        if _values and st.session_state[_fr_sel_key][fname]:
                            st.session_state[_fr_val_key].setdefault(fname, {})
                            with st.container(border=True):
                                st.caption(
                                    f"`{fname}` values — uncheck any to narrow the categories every "
                                    f"future extraction classifies into (existing matrices keep "
                                    f"whatever value they already have; this only changes what NEW "
                                    f"extractions are allowed to pick)."
                                )
                                _val_cols = st.columns(min(len(_values), 4) or 1)
                                for _vi, _val in enumerate(_values):
                                    _val_default = st.session_state[_fr_val_key][fname].get(_val, True)
                                    with _val_cols[_vi % len(_val_cols)]:
                                        st.session_state[_fr_val_key][fname][_val] = st.checkbox(
                                            str(_val), value=_val_default,
                                            key=f"{proj_id}_es_fvalchk_{fname}_{_val}")

                        # Same narrowing, but for enum sub-fields nested inside an object-type field
                        # (e.g. route1_evaluation.appeal_score, .comprehension_score) — these carry
                        # their OWN "values" list, separate from the parent field's, and were
                        # previously invisible here entirely: only the parent's own top-level
                        # "values" got checkboxes, so any object-shaped field's enum sub-fields
                        # (a very common shape — most multi-part evaluations) had no narrowing UI
                        # at all, even though the parent checkbox implied full control over it.
                        _sub_fields = fdef.get("sub_fields") or []
                        if _sub_fields and st.session_state[_fr_sel_key][fname]:
                            for _sf in _sub_fields:
                                _sf_name = _sf.get("name")
                                _sf_values = _sf.get("values")
                                if not _sf_name or not _sf_values:
                                    continue
                                _sf_key = f"{fname}.{_sf_name}"
                                st.session_state[_fr_val_key].setdefault(_sf_key, {})
                                with st.container(border=True):
                                    st.caption(
                                        f"`{fname}.{_sf_name}` values — uncheck any to narrow the "
                                        f"categories every future extraction classifies this "
                                        f"sub-field into (existing matrices unaffected)."
                                    )
                                    _sf_val_cols = st.columns(min(len(_sf_values), 4) or 1)
                                    for _vi, _val in enumerate(_sf_values):
                                        _val_default = st.session_state[_fr_val_key][_sf_key].get(_val, True)
                                        with _sf_val_cols[_vi % len(_sf_val_cols)]:
                                            st.session_state[_fr_val_key][_sf_key][_val] = st.checkbox(
                                                str(_val), value=_val_default,
                                                key=f"{proj_id}_es_fvalchk_{fname}_{_sf_name}_{_val}")

                    if _missing_from_schema:
                        st.markdown("---")
                        st.warning(
                            f"⚠ **{len(_missing_from_schema)} field(s) have real data in existing matrices "
                            f"but AREN'T in the current schema** — a schema regeneration dropped these. "
                            f"Check any you want restored:")
                        _restore_key = f"{proj_id}_es_field_restore"
                        st.session_state.setdefault(_restore_key, {})
                        for fname, cnt in sorted(_missing_from_schema.items(), key=lambda x: -x[1]):
                            _rdefault = st.session_state[_restore_key].get(fname, True)
                            st.session_state[_restore_key][fname] = st.checkbox(
                                f"`{fname}` — populated in {cnt} existing matrix/matrices, not in schema",
                                value=_rdefault, key=f"{proj_id}_es_frestore_{fname}")

                    _n_kept = sum(1 for v in st.session_state[_fr_sel_key].values() if v)
                    _n_restore = sum(1 for v in st.session_state.get(f"{proj_id}_es_field_restore", {}).values() if v)
                    _btn_label = f"✅ Apply — keep {_n_kept} of {len(_layer2_fields)}"
                    if _n_restore:
                        _btn_label += f", restore {_n_restore}"
                    if st.button(_btn_label, key=f"{proj_id}_es_apply_fields"):
                        kept = {k: v for k, v in _layer2_fields.items()
                                if st.session_state[_fr_sel_key].get(k, True)}
                        dropped = [k for k in _layer2_fields if k not in kept]
                        restored = []
                        for fname in _missing_from_schema:
                            if st.session_state.get(f"{proj_id}_es_field_restore", {}).get(fname, True):
                                kept[fname] = _infer_field_def_from_matrices(fname, matrices_dir)
                                restored.append(fname)
                        # Narrow enum values for fields where any value checkbox was unchecked —
                        # this becomes the new enum constraint for every future extraction.
                        narrowed = []
                        for fname, fdef in kept.items():
                            _orig_values = fdef.get("values")
                            if _orig_values:
                                _val_choices = st.session_state.get(_fr_val_key, {}).get(fname, {})
                                _kept_values = [v for v in _orig_values if _val_choices.get(v, True)]
                                if _kept_values and len(_kept_values) < len(_orig_values):
                                    fdef["values"] = _kept_values
                                    narrowed.append(f"{fname} → {', '.join(_kept_values)}")
                            # Same narrowing for enum sub-fields nested inside object-type fields —
                            # mirrors the checkbox UI above, keyed the same way ("field.subfield").
                            for _sf in (fdef.get("sub_fields") or []):
                                _sf_name = _sf.get("name")
                                _sf_orig_values = _sf.get("values")
                                if not _sf_name or not _sf_orig_values:
                                    continue
                                _sf_key = f"{fname}.{_sf_name}"
                                _sf_choices = st.session_state.get(_fr_val_key, {}).get(_sf_key, {})
                                _sf_kept = [v for v in _sf_orig_values if _sf_choices.get(v, True)]
                                if _sf_kept and len(_sf_kept) < len(_sf_orig_values):
                                    _sf["values"] = _sf_kept
                                    narrowed.append(f"{fname}.{_sf_name} → {', '.join(_sf_kept)}")
                        # Apply renames — move each renamed field to its new key. Validated
                        # (snake_case-ish, non-empty, not colliding with a field kept under its
                        # original name) so a typo can't silently corrupt the schema; invalid or
                        # unchanged entries are just left under their original name.
                        renamed = []
                        _rename_choices = st.session_state.get(_fr_rename_key, {})
                        for _old_name in list(kept.keys()):
                            _new_name = re.sub(r"[^a-z0-9_]", "_",
                                                _rename_choices.get(_old_name, _old_name).strip().lower()).strip("_")
                            if _new_name and _new_name != _old_name and _new_name not in kept:
                                kept[_new_name] = kept.pop(_old_name)
                                renamed.append(f"{_old_name} → {_new_name}")
                        schema["layer2"]["fields"] = kept
                        # Marks this exact review as done — Phase 3 extraction stays disabled until
                        # this has been clicked at least once for the current schema. A fresh
                        # discovery run overwrites the whole schema file, so this flag naturally
                        # resets (the regenerated file won't carry it forward) — no separate
                        # invalidation code needed.
                        schema["_field_review_applied"] = True
                        schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
                        # Resync master_prompt.txt too — editing schema['layer2']['fields'] here without
                        # this left the actual prompt sent to every extraction call still listing fields
                        # that were just dropped (found live, this session — the schema said N fields,
                        # the prompt still asked for the old set).
                        from infoleap.skills import schema_generator as _sg2
                        _sg2._resync_master_prompt_from_schema(proj_id)
                        st.cache_data.clear()
                        _msg = f"Applied — {len(kept)} field(s) kept"
                        if dropped: _msg += f", dropped: {', '.join(dropped)}"
                        if restored: _msg += f", restored: {', '.join(restored)}"
                        if narrowed: _msg += f". Narrowed: {'; '.join(narrowed)}"
                        if renamed: _msg += f". Renamed: {'; '.join(renamed)}"
                        st.success(_msg + ". Extraction (Step 4+) is now unlocked and will only be "
                                          "scored against the kept fields/values.")
                        st.rerun()

        # ── Step 2c: Combine fields into a named composite ─────────────────────────
        # After reviewing field quality above, let the researcher pick 2+ fields that really
        # belong together (e.g. two near-duplicate fields, or several narrow sub-questions that
        # read better as one grouped answer) and merge them under one name they choose. Additive
        # only: the new composite is written alongside the originals (schema for future
        # extractions, matrices for existing data) — nothing is deleted, so this can't destroy
        # data the way the accidental --force regeneration in §2 of the 2026-07-13 audit did.
        if _layer2_fields:
            _section("2c · Combine fields", "Merge 2+ reviewed fields into one named composite",
                     accent=_P["blue"], icon="🧬")
            with st.container(border=True):
                st.caption(
                    "Pick fields that overlap or belong together and give the combined field a "
                    "name. This adds a new field (type: object, one sub-field per original) to the "
                    "schema for future extractions, and backfills it into existing matrices from "
                    "whatever values those matrices already have. Originals are kept untouched."
                )
                _cf_opts = sorted(_layer2_fields.keys())
                _cf_labels = {
                    f: f"`{f}` ({_layer2_fields[f].get('type','string')}, "
                       f"{_matrix_field_counts.get(f, 0)} matrices) — {_layer2_fields[f].get('description','')}"
                    for f in _cf_opts
                }
                _cf_sel = st.multiselect(
                    "Fields to combine", options=_cf_opts,
                    format_func=lambda f: _cf_labels.get(f, f),
                    key=f"{proj_id}_es_combine_sel",
                )
                _cf_name_raw = st.text_input(
                    "New composite field name", key=f"{proj_id}_es_combine_name",
                    placeholder="e.g. trust_and_barriers",
                )
                _cf_name = re.sub(r"[^a-z0-9_]", "_", _cf_name_raw.strip().lower()).strip("_")
                if st.button("🧬 Combine into new field", key=f"{proj_id}_es_combine_apply",
                             disabled=len(_cf_sel) < 2 or not _cf_name):
                    if _cf_name in _layer2_fields:
                        st.error(f"`{_cf_name}` already exists — pick a different name.")
                    else:
                        _sub_fields = []
                        for _sf_src in _cf_sel:
                            _sdef = _layer2_fields[_sf_src]
                            _sub_fields.append({
                                "name": _sf_src,
                                "type": _sdef.get("type", "string"),
                                "description": _sdef.get("description", ""),
                                "values": _sdef.get("values"),
                                "verbatim_field": _sdef.get("verbatim_field", False),
                            })
                        schema.setdefault("layer2", {}).setdefault("fields", {})[_cf_name] = {
                            "type": "object",
                            "description": f"Composite of: {', '.join(_cf_sel)}",
                            "sub_fields": _sub_fields,
                            "_source_objective": "Manually combined by researcher via Extraction Studio field-combine picker.",
                            "_rationale": f"User-directed consolidation of {len(_cf_sel)} reviewed fields.",
                            "_combined_from": _cf_sel,
                        }
                        schema.setdefault("_combined_fields", []).append({
                            "new_field": _cf_name, "source_fields": _cf_sel,
                            "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                        })
                        schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
                        from infoleap.skills import schema_generator as _sg3
                        _sg3._resync_master_prompt_from_schema(proj_id)
                        # Backfill existing matrices non-destructively — copy whatever values the
                        # source fields already have into the new nested key, skip matrices missing
                        # all source fields so an empty composite isn't written everywhere.
                        _n_backfilled = 0
                        if matrices_dir and matrices_dir.exists():
                            for _mf in matrices_dir.glob("*_matrix.json"):
                                try:
                                    _m = json.loads(_mf.read_text(encoding="utf-8"))
                                except Exception as _e:
                                    print(f"[quote_explorer] WARN: failed to parse {_mf.name} "
                                          f"during backfill: {_e}")
                                    continue
                                _nested = {k: _m[k] for k in _cf_sel if k in _m}
                                if _nested:
                                    _m[_cf_name] = _nested
                                    _mf.write_text(json.dumps(_m, indent=2, ensure_ascii=False), encoding="utf-8")
                                    _n_backfilled += 1
                        st.cache_data.clear()
                        st.success(f"Created `{_cf_name}` from {', '.join(_cf_sel)}. "
                                   f"Backfilled into {_n_backfilled} existing matrix/matrices. "
                                   f"Future extractions will populate it directly.")
                        st.rerun()

        # ── Step 2d: Verbatim groups ────────────────────────────────────────────────
        # Dynamic, per-project sentiment x theme groups proposed from real extracted quotes —
        # not a fixed universal taxonomy (studies vary too much in domain for one list to fit
        # all of them), grounded the same way schema_generator.py's discovery pass is (cite the
        # real quote, never invent one). Fully user-editable: rename/delete any proposed group,
        # and reassign any individual quote by hand.
        _vg_verbatim_fields = _pex._collect_hard_verbatim_fields(schema) if hasattr(_pex, "_collect_hard_verbatim_fields") else set()
        if matrices_dir and matrices_dir.exists() and any(matrices_dir.glob("*_matrix.json")):
            _section("2e · Verbatim groups", "Cluster extracted quotes into sentiment/theme groups",
                     accent=_P.get("pink", _P["purple"]), icon="🗂️")
            with st.container(border=True):
                from infoleap.skills import verbatim_grouping as _vg
                _vg_data = _vg.load_groups(project_dir)
                st.caption(
                    "Groups are AI-suggested from this project's own extracted verbatims, grounded "
                    "in real quotes only (cited by exact match, never invented) — then fully "
                    "editable by hand: rename, delete, or reassign any single quote below."
                )
                _vg_col1, _vg_col2 = st.columns([1, 1])
                with _vg_col1:
                    if st.button("🧠 Suggest groups (AI)", key=f"{proj_id}_vg_suggest"):
                        _quotes = _vg.collect_verbatims(matrices_dir, _vg_verbatim_fields)
                        if not _quotes:
                            st.warning("No verbatim-flagged quotes found in this project's matrices yet.")
                        else:
                            with st.spinner(f"Grouping {len(_quotes)} quotes..."):
                                _proposed = _vg.propose_groups(_quotes, proj.get("display_name", proj_id),
                                                                proj.get("study_type", "other"))
                            if not _proposed:
                                st.error("Grouping failed or returned nothing usable — try again.")
                            else:
                                _vg_data["groups"] = _proposed
                                _vg_data["generated_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
                                _vg.save_groups(project_dir, _vg_data)
                                st.success(f"Proposed {len(_proposed)} group(s) from {len(_quotes)} quotes.")
                                st.rerun()
                with _vg_col2:
                    if st.button("➕ Add empty group", key=f"{proj_id}_vg_add"):
                        _vg_data["groups"].append({
                            "group_id": f"manual_{len(_vg_data['groups'])}", "name": "New group",
                            "sentiment": "neutral", "theme": "", "definition": "", "members": [],
                        })
                        _vg.save_groups(project_dir, _vg_data)
                        st.rerun()

                if not _vg_data["groups"]:
                    st.caption("No groups yet — click **Suggest groups (AI)** or add one manually.")
                else:
                    _all_group_ids = [g["group_id"] for g in _vg_data["groups"]]
                    _all_group_names = {g["group_id"]: g["name"] for g in _vg_data["groups"]}
                    for _gi, _g in enumerate(list(_vg_data["groups"])):
                        with st.expander(f"🗂️ {_g['name']} — {len(_g.get('members', []))} quote(s) "
                                          f"[{_g.get('sentiment','neutral')}]", expanded=False):
                            _new_name = st.text_input("Group name", value=_g["name"],
                                                       key=f"{proj_id}_vg_name_{_g['group_id']}")
                            _new_sent = st.selectbox(
                                "Sentiment", ["positive", "negative", "mixed", "neutral"],
                                index=["positive", "negative", "mixed", "neutral"].index(_g.get("sentiment", "neutral"))
                                if _g.get("sentiment") in ("positive", "negative", "mixed", "neutral") else 3,
                                key=f"{proj_id}_vg_sent_{_g['group_id']}")
                            if _new_name != _g["name"] or _new_sent != _g.get("sentiment"):
                                _g["name"] = _new_name
                                _g["sentiment"] = _new_sent
                                _vg.save_groups(project_dir, _vg_data)
                            if _g.get("definition"):
                                st.caption(_g["definition"])
                            for _m in _g.get("members", []):
                                st.markdown(f"- *\"{_m['quote'][:200]}\"* — `{_m['doc_id']}` ({_m['field']})")
                            if st.button("🗑️ Delete group", key=f"{proj_id}_vg_del_{_g['group_id']}"):
                                _vg_data["groups"] = [gg for gg in _vg_data["groups"] if gg["group_id"] != _g["group_id"]]
                                _vg.save_groups(project_dir, _vg_data)
                                st.rerun()

                    st.markdown("---")
                    st.caption("Reassign an individual quote to a different group:")
                    _all_quotes = _vg.collect_verbatims(matrices_dir, _vg_verbatim_fields, cap=100)
                    if _all_quotes:
                        _qsel = st.selectbox(
                            "Quote", options=list(range(len(_all_quotes))),
                            format_func=lambda i: f"{_all_quotes[i]['doc_id']} ({_all_quotes[i]['field']}): "
                                                   f"\"{_all_quotes[i]['quote'][:80]}\"",
                            key=f"{proj_id}_vg_reassign_sel",
                        )
                        _target_gid = st.selectbox(
                            "Move to group", options=_all_group_ids,
                            format_func=lambda gid: _all_group_names.get(gid, gid),
                            key=f"{proj_id}_vg_reassign_target",
                        )
                        if st.button("↳ Move quote", key=f"{proj_id}_vg_reassign_apply"):
                            _q = _all_quotes[_qsel]
                            for _g in _vg_data["groups"]:
                                _g["members"] = [m for m in _g.get("members", [])
                                                  if not (m["doc_id"] == _q["doc_id"] and m["field"] == _q["field"]
                                                          and m["quote"] == _q["quote"])]
                                if _g["group_id"] == _target_gid:
                                    _g["members"].append(_q)
                            _vg_data["assignments"][_vg.quote_key(_q["doc_id"], _q["field"], _q["quote"])] = _target_gid
                            _vg.save_groups(project_dir, _vg_data)
                            st.success("Moved.")
                            st.rerun()

        # ── Step 3: View / edit the master prompt ─────────────────────────────────
        _section("3 · Master prompt", "Exactly what every selected transcript's Step 1 call receives",
                 accent=_P["amber"], icon="📄")
        with st.container(border=True):
            with st.expander("📄 View / edit master_prompt.txt", expanded=False):
                _prompt_edit_key = f"{proj_id}_es_prompt_edit"
                _edited_prompt = st.text_area(
                    "master_prompt.txt", value=master_prompt, height=420, key=_prompt_edit_key,
                    label_visibility="collapsed",
                )
                if st.button("💾 Save prompt", key=f"{proj_id}_es_save_prompt"):
                    mp_path.write_text(_edited_prompt, encoding="utf-8")
                    st.success("Saved — Step 1 below will use this edited prompt.")
                    st.rerun()

    # ── Phase 3: Extract & store all data ──────────────────────────────────
    _p3_total = len(entries)
    _p3_done = len(list(matrices_dir.glob("*_matrix.json"))) if matrices_dir and matrices_dir.exists() else 0
    _p3_quals = []
    _p3_needs_review = 0
    if matrices_dir and matrices_dir.exists():
        for _mf in matrices_dir.glob("*_matrix.json"):
            try:
                _md = json.loads(_mf.read_text(encoding="utf-8"))
            except Exception as _e:
                print(f"[quote_explorer] WARN: failed to parse {_mf.name} during Phase 3 scan: {_e}")
                continue
            if _md.get("_quality_score") is not None:
                _p3_quals.append(_md["_quality_score"])
            if _md.get("_needs_review"):
                _p3_needs_review += 1
    _p3_avg_q = round(sum(_p3_quals) / len(_p3_quals)) if _p3_quals else None
    if _p3_done == 0:
        _p3_badge = "⏳ Not started"
    elif _p3_done < _p3_total or _p3_needs_review > 0:
        _p3_badge = "⚠ Needs attention"
    else:
        _p3_badge = "✅ Done"
    _p3_verdict = (
        f"{_p3_done}/{_p3_total} interviews extracted"
        + (f" · avg quality {_p3_avg_q}/100" if _p3_avg_q is not None else " · quality not yet scored")
        + (f" · {_p3_needs_review} need review" if _p3_needs_review else "")
    )
    with st.expander(
        f"{_p3_badge}  ·  PHASE 3 — Extract & store all data  ·  {_p3_verdict}",
        expanded=(_p3_done < _p3_total or _p3_needs_review > 0 or _p3_done == 0),
    ):
        st.caption(
            "Runs the real per-respondent extraction (Step 1 → Step 2 → gate → retry) and writes "
            "the structured records the dashboard reads. Do this after Phase 2's schema is settled."
        )
        _p3_review_applied = bool(schema.get("_field_review_applied"))
        if not _p3_review_applied:
            st.warning(
                "🔒 **Extraction is locked.** Apply Step 2b — Review discovered fields (above) at "
                "least once for this schema before running extraction. Every run after a fresh "
                "discovery starts locked again, so a human always reviews what changed before it "
                "reaches every transcript."
            )
        # ── Step 4: Run extraction (Step 1) ───────────────────────────────────────
        _section("4 · Run extraction — Step 1", "Initial findings, free-form reasoning per transcript",
                 accent=_P["teal"], icon="▶")
        with st.container(border=True):
            _extraction_model_options = _get_top_extraction_models(20)
            _ex_col1, _ex_col2 = st.columns([1.3, 1.3])
            with _ex_col2:
                _extraction_model_label = st.selectbox(
                    "Model (used for Step 1, Step 2, and Reconcile below)",
                    options=list(_extraction_model_options.keys()),
                    key=f"{proj_id}_es_extraction_model",
                )
            _extraction_model = _extraction_model_options.get(_extraction_model_label)
            with _ex_col1:
                if st.button("▶ Run Step 1 — initial findings",
                             disabled=(not selected or not acknowledged or not _p3_review_applied),
                             key=f"{proj_id}_es_run_step1", type="primary"):
                    prog = st.progress(0.0)
                    for i, fn in enumerate(selected):
                        entry = index[fn]
                        md_rel = entry.get("output_md")
                        md_path = (project_dir / md_rel) if md_rel else None
                        if not md_path or not md_path.exists():
                            st.session_state[_s1_key][fn] = {"status": "error", "text": f"md not found: {md_path}"}
                            prog.progress((i + 1) / len(selected))
                            continue
                        try:
                            md_text = md_path.read_text(encoding="utf-8")
                            _meta, body = _pex._parse_frontmatter(md_text)
                            text = _pex.run_step1(step1_section, body, model=_extraction_model,
                                                   project_id=proj_id,
                                                   doc_id=_doc_id_for_project(proj, fn))
                            st.session_state[_s1_key][fn] = {
                                "status": "pending_review", "text": text, "edited": text,
                                "meta": _meta, "body": body, "approved": True,
                            }
                        except Exception as e:
                            st.session_state[_s1_key][fn] = {"status": "error", "text": str(e)}
                        prog.progress((i + 1) / len(selected))
                    st.rerun()
                if not selected:
                    st.caption("Select at least one file in Step 1 above to enable this.")
                elif not acknowledged:
                    st.caption("Acknowledge the brief/DG mismatches above to enable this.")
                elif not _p3_review_applied:
                    st.caption("Apply Step 2b — Review discovered fields (above) to enable this.")

        # ── Step 5: Review & select ───────────────────────────────────────────────
        reviewable = {fn: r for fn, r in st.session_state[_s1_key].items()
                      if r.get("status") == "pending_review"}
        if reviewable:
            _section("5 · Review & select", f"{len(reviewable)} transcript(s) awaiting review",
                     accent=_P["purple"], icon="📝")
            with st.container(border=True):
                for fn, r in reviewable.items():
                    doc_id = _doc_id_for_project(proj, fn)
                    _text = r.get("edited", r.get("text", ""))
                    _meta = r.get("meta", {}) or {}
                    _wc = len(_text.split())
                    # Step 1's prompt asks the model to fill sections A-H (see master_prompt.txt
                    # PHASE 1) and it reliably does so with real markdown (### headers, **bold**
                    # labels) — the raw output was being dumped into a plain st.text_area, which
                    # shows "###"/"**" as literal characters instead of rendering them. Pull the
                    # single most powerful verbatim (section H) out as a highlighted quote so the
                    # strongest evidence is visible without reading the whole thing.
                    _quote_m = re.search(
                        r"H\.\s*MOST POWERFUL VERBATIM.*?\n(.+?)\n",
                        _text, re.IGNORECASE,
                    )
                    _pull_quote = _quote_m.group(1).strip(" *_\n") if _quote_m else ""
                    _badge_bits = [f"{_wc} words"]
                    if _meta.get("city"):    _badge_bits.append(str(_meta["city"]))
                    if _meta.get("segment"): _badge_bits.append(str(_meta["segment"]))
                    if _meta.get("gender"):  _badge_bits.append(str(_meta["gender"]))
                    _header_label = f"📝 {doc_id} — {fn}  ·  {' · '.join(_badge_bits)}"
                    with st.expander(_header_label, expanded=False):
                        r["approved"] = st.checkbox("Approve", value=r.get("approved", True),
                                                     key=f"{proj_id}_es_approve_{fn}")
                        if _pull_quote:
                            st.markdown(
                                f'<div style="border-left:3px solid #7c3aed;padding:8px 14px;'
                                f'margin:6px 0 12px;background:#7c3aed0d;border-radius:0 6px 6px 0;'
                                f'font-style:italic;font-size:0.88rem;">"{_html_mod.escape(_pull_quote)}"'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                        st.markdown("**At a glance** (rendered — edit below to change):")
                        with st.container(border=True):
                            st.markdown(_text if _text else "*(empty)*")
                        r["edited"] = st.text_area(
                            "✏️ Edit raw findings text (Step 1 — editable before Step 2 extraction)",
                            value=r.get("edited", r.get("text", "")), height=220,
                            key=f"{proj_id}_es_edit_{fn}",
                        )

                n_approved = sum(1 for r in reviewable.values() if r.get("approved"))
                if st.button(f"✅ Finalize {n_approved} approved (Step 2 + write matrices)",
                             disabled=(n_approved == 0), key=f"{proj_id}_es_finalize", type="primary"):
                    matrices_dir.mkdir(parents=True, exist_ok=True)
                    prog = st.progress(0.0)
                    results = []
                    done = 0
                    approved_items = [(fn, r) for fn, r in reviewable.items() if r.get("approved")]
                    for fn, r in approved_items:
                        doc_id = _doc_id_for_project(proj, fn)
                        try:
                            respondent_hint = {
                                "doc_id": doc_id, "filename": fn,
                                "word_count": len(r.get("body", "").split()),
                                "respondent": {
                                    "city": r.get("meta", {}).get("city"),
                                    "segment": r.get("meta", {}).get("segment"),
                                    "gender": r.get("meta", {}).get("gender"),
                                    "age_band": None, "occupation": None,
                                },
                            }
                            step2_text = _pex.run_step2(step2_section, r["edited"], respondent_hint,
                                                         model=_extraction_model,
                                                         project_id=proj_id, doc_id=doc_id)
                            parsed = _pex._extract_json(step2_text)
                            if parsed is None:
                                results.append((fn, "PARSE_ERROR")); done += 1
                                prog.progress(done / len(approved_items)); continue
                            parsed.setdefault("doc_id", doc_id)
                            parsed.setdefault("filename", fn)
                            parsed.setdefault("word_count", respondent_hint["word_count"])
                            if "respondent" not in parsed:
                                parsed["respondent"] = respondent_hint["respondent"]
                            parsed = _pex._coerce_field_types(parsed, schema)
                            parsed = _pex._normalise_matrix(parsed, schema)
                            parsed = _pex.gate_matrix(parsed, schema, r.get("body", ""))
                            parsed = _pex.run_reflection_retry(
                                parsed, schema, r.get("body", ""), model=_extraction_model,
                                project_id=proj_id, doc_id=doc_id,
                            )
                            parsed["_step1_text"] = r["edited"]
                            parsed["_extracted_at"] = datetime.now().isoformat()
                            parsed["_schema_fields_at_extraction"] = sorted(schema.get("layer2", {}).get("fields", {}).keys())
                            (matrices_dir / f"{doc_id}_matrix.json").write_text(
                                json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
                            st.session_state[_s1_key][fn]["status"] = "done"
                            q_label = parsed.get("_quality_label", "n/a")
                            status = "OK"
                            if parsed.get("_needs_review"):
                                status = f"OK — ⚠ needs review (quality: {q_label})"
                            results.append((fn, status))
                        except Exception as e:
                            results.append((fn, f"ERROR: {e}"))
                        done += 1
                        prog.progress(done / len(approved_items))
                    for fn, status in results:
                        (st.warning if "needs review" in status else
                         st.success if status == "OK" else st.error)(f"{fn}: {status}")
                    st.cache_data.clear()

        # ── Step 6: Reconcile ──────────────────────────────────────────────────────
        _section("6 · Reconcile", "Optional — merges near-duplicate category values across matrices",
                 accent=_P["green"], icon="🔄")
        with st.container(border=True):
            st.caption("Clusters near-duplicate category values used across this project's matrices "
                        "(e.g. re-running discovery producing slightly different archetype names for the "
                        "same thing) into one canonical label — logged, never silent.")
            if st.button("🔄 Reconcile project", key=f"{proj_id}_es_reconcile"):
                with st.spinner("Reconciling…"):
                    report = _pex.reconcile_project(proj_id, model=_extraction_model)
                if report.get("merged_fields"):
                    st.success(f"Merged {len(report['merged_fields'])} field(s):")
                    st.json(report["merged_fields"])
                else:
                    st.info("No fragmentation found — nothing to merge.")
                st.cache_data.clear()

        # ── Step 7: Process all interviews ──────────────────────────────────────────
        # The steps above (Discover, Review fields, Reconcile) work on a small sample to settle the
        # schema. Once it's settled, this runs the complete pipeline — Step 1, Step 2, type coercion,
        # normalisation, verbatim-fidelity gate, reflection-retry — on EVERY transcript in the
        # project in one click, no per-file review pause (that checkpoint already happened, on the
        # sample, during schema design). Ignores the Step 1 checkbox selection on purpose — this is
        # "process everything," not "process what's currently checked."
        _section("7 · Process all interviews",
                 f"Runs Step 1 + Step 2 + gate + retry on every one of the {len(index)} transcripts "
                 f"and stores the result — no per-file review",
                 accent=_P["red"], icon="⚡")
        with st.container(border=True):
            st.caption(
                "Use this once Discover/Review fields/Reconcile above have settled the schema on a "
                "sample. This runs the full pipeline on every transcript in the project — Step 1 → "
                "Step 2 → type coercion → gate → one reflection-retry if needed — and writes "
                "straight to matrices/. Already-extracted files are re-run too, so every respondent "
                "ends up on the current schema and master prompt, not a mix of old and new."
            )
            if not _p3_review_applied:
                st.caption("🔒 Apply Step 2b — Review discovered fields (above) to enable this — "
                           "this button runs every kept field/value against every transcript in the "
                           "project, so it must be a reviewed set, not whatever discovery last proposed.")
            if st.button(f"⚡ Run full pipeline on all {len(index)} interviews",
                         disabled=(not _p3_review_applied),
                         key=f"{proj_id}_es_run_all", type="primary"):
                matrices_dir.mkdir(parents=True, exist_ok=True)
                _all_files = sorted(index.keys())
                _prog = st.progress(0.0)
                _status_area = st.empty()
                _results = []
                for i, fn in enumerate(_all_files):
                    entry = index[fn]
                    md_rel = entry.get("output_md")
                    md_path = (project_dir / md_rel) if md_rel else None
                    doc_id = _doc_id_for_project(proj, fn)
                    _status_area.caption(f"Processing {doc_id} ({i+1}/{len(_all_files)})…")
                    if not md_path or not md_path.exists():
                        _results.append((fn, f"ERROR: md not found: {md_path}"))
                        _prog.progress((i + 1) / len(_all_files))
                        continue
                    try:
                        md_text = md_path.read_text(encoding="utf-8")
                        _meta, body = _pex._parse_frontmatter(md_text)
                        step1_text = _pex.run_step1(step1_section, body, model=_extraction_model,
                                                     project_id=proj_id, doc_id=doc_id)
                        respondent_hint = {
                            "doc_id": doc_id, "filename": fn,
                            "word_count": len(body.split()),
                            "respondent": {
                                "city": _meta.get("city"), "segment": _meta.get("segment"),
                                "gender": _meta.get("gender"), "age_band": None, "occupation": None,
                            },
                        }
                        step2_text = _pex.run_step2(step2_section, step1_text, respondent_hint,
                                                     model=_extraction_model, project_id=proj_id,
                                                     doc_id=doc_id)
                        parsed = _pex._extract_json(step2_text)
                        if parsed is None:
                            _results.append((fn, "PARSE_ERROR"))
                            _prog.progress((i + 1) / len(_all_files))
                            continue
                        parsed.setdefault("doc_id", doc_id)
                        parsed.setdefault("filename", fn)
                        parsed.setdefault("word_count", respondent_hint["word_count"])
                        if "respondent" not in parsed:
                            parsed["respondent"] = respondent_hint["respondent"]
                        parsed = _pex._coerce_field_types(parsed, schema)
                        parsed = _pex._normalise_matrix(parsed, schema)
                        parsed = _pex.gate_matrix(parsed, schema, body)
                        parsed = _pex.run_reflection_retry(parsed, schema, body, model=_extraction_model,
                                                             project_id=proj_id, doc_id=doc_id)
                        parsed["_step1_text"] = step1_text
                        parsed["_extracted_at"] = datetime.now().isoformat()
                        parsed["_schema_fields_at_extraction"] = sorted(
                            schema.get("layer2", {}).get("fields", {}).keys())
                        (matrices_dir / f"{doc_id}_matrix.json").write_text(
                            json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
                        q_label = parsed.get("_quality_label", "n/a")
                        status = "OK" if not parsed.get("_needs_review") else f"OK — needs review ({q_label})"
                        _results.append((fn, status))
                    except Exception as e:
                        _results.append((fn, f"ERROR: {e}"))
                    _prog.progress((i + 1) / len(_all_files))
                _status_area.empty()
                n_ok = sum(1 for _, s in _results if s.startswith("OK"))
                st.success(f"Done — {n_ok}/{len(_results)} processed successfully.")
                for fn, status in _results:
                    (st.warning if "needs review" in status else
                     st.success if status == "OK" else st.error)(f"{fn}: {status}")
                st.cache_data.clear()

        # ── Step 8: Unmatched categories ────────────────────────────────────────────
        # Per-interview signal that a real answer didn't fit any value the schema currently declares
        # (the model was told to write "NEW: <label>" instead of force-fitting it — see master_prompt's
        # ENUM CONSTRAINTS). This is the "pipeline should tell me when a new category is needed" gap.
        _unmatched_agg: dict[str, list[dict]] = {}
        if matrices_dir and matrices_dir.exists():
            for mf in matrices_dir.glob("*_matrix.json"):
                try:
                    mdata = json.loads(mf.read_text(encoding="utf-8"))
                except Exception as _e:
                    print(f"[quote_explorer] WARN: failed to parse {mf.name} (unmatched categories): {_e}")
                    continue
                for u in mdata.get("_unmatched_categories", []) or []:
                    _unmatched_agg.setdefault(u.get("field", "?"), []).append(
                        {"doc_id": mdata.get("doc_id", mf.stem), "value": u.get("proposed_value", "")})

        if _unmatched_agg:
            _section("8 · Unmatched categories", "Real answers that didn't fit any current schema value",
                     accent=_P["amber"], icon="⚠")
            with st.container(border=True):
                st.caption("These respondents said something for a field that doesn't match any value "
                           "declared in the schema — the model flagged it instead of silently forcing a "
                           "fit. Consider adding these as new enum values next time you refresh the schema.")
                for field, items in _unmatched_agg.items():
                    st.markdown(f"**`{field}`** — {len(items)} unmatched response(s)")
                    for it in items:
                        st.markdown(f"- `{it['doc_id']}`: *\"{it['value']}\"*")


# ═════════════════════════════════════════════════════════════════════════════
# CONCEPT TESTING VIEW — any project with study_type == "concept_testing"
# Routes by study_type, not hardcoded project ID.
# ═════════════════════════════════════════════════════════════════════════════
if _study_type == "concept_testing":
    from infoleap.views.concept_testing_renderer import render_concept_testing
    _ct_proj = _pm.get_project(_active_project) or {}
    if not _ct_proj:
        st.error(f"Project '{_active_project}' not found in registry.")
        st.stop()

    _render_pipeline_sync_banner(_active_project)

    if st.toggle("🔬 Extraction Studio — redo extraction with review", key="_es_toggle_open"):
        st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)
        _render_extraction_studio(_active_project, _ct_proj)
        st.divider()

    try:
        render_concept_testing(
            proj=_ct_proj,
            base_path=_BASE,
            call_openrouter_fn=_call_openrouter_free,
        )
    except Exception as _ct_err:
        import traceback
        st.error(f"Dashboard error: {_ct_err}")
        with st.expander("Traceback"):
            st.code(traceback.format_exc())

    st.stop()


# ═════════════════════════════════════════════════════════════════════════════
# ETHNOGRAPHIC STUDY VIEW — any project with study_type == "ethnographic"
# Routes by study_type from registry, not hardcoded project ID.
# Mixer is currently the only ethnographic project; future studies auto-route here.
# ═════════════════════════════════════════════════════════════════════════════
if _study_type == "ethnographic":
    from infoleap.views.ethnographic_renderer import render_ethnographic
    _eth_proj = _pm.get_project(_active_project) or {}
    if not _eth_proj:
        st.error(f"Project '{_active_project}' not found in registry.")
        st.stop()

    if st.toggle("🔬 Extraction Studio — redo extraction with review", key=f"_es_toggle_open_{_active_project}"):
        st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)
        _render_extraction_studio(_active_project, _eth_proj)
        st.divider()

    _eth_status = _pm.get_status(_active_project) or "unknown"
    _eth_sc = _STATUS_COLORS.get(_eth_status, "#94a3b8")
    st.markdown(
        f'<div style="background:rgba(14,165,233,0.05);border:1.5px solid rgba(14,165,233,0.25);'
        f'border-radius:12px;padding:14px 18px;margin-bottom:16px;">'
        f'<div style="font-size:0.68rem;font-weight:700;color:#0ea5e9;'
        f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px;">Active Project</div>'
        f'<div style="font-size:1.1rem;font-weight:900;color:#f1f5f9;">'
        f'{_eth_proj.get("display_name","Ethnographic Study")}</div>'
        f'<div style="font-size:0.78rem;color:#6b7280;margin-top:4px;">'
        f'{_eth_proj.get("description","")} &nbsp;|&nbsp; '
        f'<span style="color:{_eth_sc};font-weight:700;">Status: {_eth_status}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    _render_pipeline_sync_banner(_active_project)

    try:
        render_ethnographic(
            proj=_eth_proj,
            base_path=_BASE,
            call_openrouter_fn=_call_openrouter_free,
        )
    except Exception as _eth_err:
        import traceback
        st.error(f"Dashboard error: {_eth_err}")
        with st.expander("Traceback"):
            st.code(traceback.format_exc())

    st.stop()


# ═════════════════════════════════════════════════════════════════════════════
# GENERIC PROJECT VIEW — projects not handled by a dedicated renderer above
# Driven by ui_config.json + extraction_schema.json — no code changes needed
# per new project.
# ═════════════════════════════════════════════════════════════════════════════
if _study_type not in ("concept_testing", "ethnographic"):  # unknown study types → generic renderer
    _gen_proj = _pm.get_project(_active_project) or {}
    if not _gen_proj:
        st.error(f"Project '{_active_project}' not found in registry. Add it to data/projects/registry.json.")
        st.stop()

    _gen_paths = _gen_proj.get("abs_paths", {})
    _gen_m_dir = _gen_paths.get("matrices")
    _gen_schema_path = _gen_paths.get("schema")
    _gen_schema_dir = _gen_schema_path.parent if _gen_schema_path and not _gen_schema_path.is_dir() else (
        _BASE / "data" / "projects" / _active_project / "schema"
    )

    # Always show setup UI first (handles extraction flow)
    _render_project_setup(_active_project, _gen_proj)
    _render_pipeline_sync_banner(_active_project)

    # If matrices exist, show generic analysis driven by ui_config.json
    _gen_m_count = len(list(_gen_m_dir.glob("*_matrix.json"))) if _gen_m_dir and _gen_m_dir.exists() else 0
    if _gen_m_count > 0:
        try:
            from infoleap.views.qual_generic_renderer import render_generic_project, load_ui_config
            _gen_ui_config = load_ui_config(_active_project, _gen_schema_dir)
            _gen_matrix_errors = []
            _gen_matrices = [
                m for m in (
                    _load_json_safe(_gfp, _gen_matrix_errors)
                    for _gfp in sorted(_gen_m_dir.glob("*_matrix.json"))
                ) if m is not None
            ]
            if _gen_matrix_errors:
                # 2026-07-27: was a bare `except: pass` — a malformed matrix (partial write,
                # truncated LLM extraction output) vanished with zero trace, so a "18 respondents
                # shown" count with no way to know it should have been 19. Now surfaced via the
                # shared _load_json_safe/_warn_json_errors helpers instead of a reimplemented
                # inline try/except — this route used to duplicate that logic.
                _warn_json_errors(_gen_matrix_errors, context="matrix file(s) — re-run extraction for them")
                with st.expander("Parse error details"):
                    for name, err in _gen_matrix_errors:
                        st.caption(f"`{name}`: {err}")
            if _gen_matrices:
                st.markdown("---")
                render_generic_project(
                    proj=_gen_proj,
                    ui_config=_gen_ui_config,
                    base_path=_BASE,
                    call_openrouter_fn=_call_openrouter_free,
                )
        except ImportError:
            st.info(f"{_gen_m_count} matrices extracted — generic analysis renderer loading.")
        except Exception as _gen_err:
            st.warning(f"Renderer error: {_gen_err}")

    st.stop()
