"""
ImageryInterpreter — parallel AI interpretation for CAN MAP + BIP tabs.

Generates 12 analyst-style paragraphs (~150 words each) using free OpenRouter
models in round-robin rotation. All calls run concurrently via ThreadPoolExecutor.
Falls back to rule-based text if all LLM calls fail.

Models used (round-robin, i % 7):
  0: nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
  1: meta-llama/llama-4-scout:free
  2: openai/gpt-oss-120b:free
  3: nousresearch/hermes-3-llama-3.1-405b:free
  4: qwen/qwen3-next-80b-a3b-instruct:free
  5: google/gemma-4-31b-it:free
  6: deepseek/deepseek-v4-flash:free
"""

from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

_LENS_DIR  = Path(__file__).resolve().parent.parent
_PROJ_ROOT = _LENS_DIR.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))


def _load_env():
    env_file = _PROJ_ROOT / "oxdata" / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(str(env_file), override=False)
        except ImportError:
            pass

_load_env()

OR_KEY     = os.getenv("OPENROUTER_API_KEY", "")
OR_URL     = "https://openrouter.ai/api/v1/chat/completions"

FREE_MODELS = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "meta-llama/llama-4-scout:free",
    "openai/gpt-oss-120b:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "google/gemma-4-31b-it:free",
    "deepseek/deepseek-v4-flash:free",
]

_SYSTEM = (
    "You are a senior brand strategy consultant — NOT a statistician — interpreting perceptual mapping data "
    "from an Indian electrical appliance brand health study (n=6,631 respondents, 18 cities, FMCD category: "
    "fans, mixer-grinders, water heaters, room coolers). "
    "Your audience is a brand manager or CMO who already knows the numbers — your job is to say what they MEAN "
    "and what to DO. "
    "Category dynamics: dealer-led purchase decisions, word-of-mouth is primary acquisition, "
    "service experience shapes post-purchase brand perception sharply, middle-class household buyers.\n\n"
    "Banned phrases: 'it appears', 'may suggest', 'could indicate', 'interesting to note', 'statistically significant'.\n"
    "Banned jargon: χ², eigenvalues, inertia, p-value, t-statistic, cos², factor loading.\n"
    "Required framing: brand equity, consumer perception, purchase drivers, competitive threat, "
    "white space, differentiation opportunity, emotional vs. functional territory.\n\n"
    "Respond in EXACTLY this structure — three labelled lines, nothing else:\n"
    "FINDING: [One bold, decisive sentence. Must name at least one specific brand AND one specific attribute. "
    "State a clear verdict — a winner, a loser, a threat, or an opportunity. No neutral observations.]\n"
    "DETAIL: [Two or three sentences. Name actual brands and actual attributes from the input data. "
    "Frame as consumer perception: 'consumers associate **Brand X** with **Attribute Y**, "
    "giving it a perceptual moat competitors cannot easily replicate.' "
    "Identify the competitive implication: which brand is gaining or losing ground on which attribute.]\n"
    "ACTION: [One specific, actionable recommendation. Name the brand, name the attribute or segment, "
    "name the channel or lever. Example: '**Crompton** should anchor its next campaign on **energy efficiency** "
    "— it already over-indexes on this attribute and no rival has claimed it — before **Havells** closes the gap.' "
    "Be concrete enough that a brand team could brief an agency from this sentence alone.]\n\n"
    "Use **double asterisks** around brand names, attribute names, and the core recommendation. "
    "Name actual brands and attributes from the input — never use placeholders like 'Brand A' or 'Attribute X'."
)


# ─────────────────────────────────────────────────────────────────────────────
# Core LLM call with retry
# ─────────────────────────────────────────────────────────────────────────────

def _llm_call(prompt: str, model_idx: int, timeout: int = 12) -> str:
    if not OR_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    for attempt in range(2):
        model = FREE_MODELS[(model_idx + attempt) % len(FREE_MODELS)]
        try:
            resp = requests.post(
                OR_URL,
                headers={"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user",   "content": prompt},
                    ],
                    "max_tokens": 350,
                    "temperature": 0.4,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            # Strip <think>...</think> and <thinking>...</thinking> reasoning blocks
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"<thinking>.*?</thinking>", "", content, flags=re.DOTALL).strip()
            
            # Validate output format
            if not content.startswith("FINDING:"):
                idx = content.find("FINDING:")
                if idx != -1:
                    content = content[idx:].strip()
                else:
                    continue  # Invalid format, try next attempt/model
            return content
        except Exception:
            continue
    raise RuntimeError("All LLM attempts failed")


def _safe_call(prompt: str, model_idx: int, fallback: str) -> str:
    try:
        return _llm_call(prompt, model_idx)
    except Exception:
        return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Data extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _top_n(series_or_dict, n=5, ascending=False):
    if isinstance(series_or_dict, dict):
        series_or_dict = pd.Series(series_or_dict)
    if isinstance(series_or_dict, pd.Series):
        s = series_or_dict.dropna().sort_values(ascending=ascending)
        return list(s.head(n).items())
    return []


def _df_from_ca(obj):
    """Convert list-of-records (from get_full_ca_tables) to DataFrame."""
    if isinstance(obj, pd.DataFrame):
        return obj
    try:
        df = pd.DataFrame(obj)
        _IDX = ("brand_name", "attr_label", "brand", "attribute", "index", "Factor")
        if not df.empty and df.columns[0] in _IDX:
            df = df.set_index(df.columns[0])
        return df
    except Exception:
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# CAN MAP tab interpreters
# ─────────────────────────────────────────────────────────────────────────────

def _interp_ca_input_table(ca_res: dict) -> str:
    try:
        mat = _df_from_ca(ca_res["tables"]["contingency_table"])
        if mat.empty:
            raise ValueError("empty")
        # Use NaN for zeros: 0 means brand absent from category, not a real low score.
        # Avoids deflating averages and inflating std-dev for category-specific attributes.
        mat_nz = mat.replace(0, float("nan"))
        brand_avgs  = mat_nz.mean(axis=1).sort_values(ascending=False)
        top_brands  = list(brand_avgs.head(3).items())
        low_brands  = list(brand_avgs.tail(2).items())
        attr_avgs   = mat_nz.mean(axis=0).sort_values(ascending=False)
        top_attrs   = list(attr_avgs.head(4).items())
        # Contestation = std dev among brands that actually compete on this attribute
        contested   = mat_nz.std(axis=0).sort_values(ascending=False)
        top_cont    = list(contested.head(3).items())
        # Count how many brands compete per attribute (non-zero)
        brand_counts = (mat > 0).sum(axis=0)

        prompt = (
            f"Brand-attribute % association matrix summary:\n"
            f"Highest avg association brands (among their active attributes): {', '.join(f'{b} ({v:.1f}%)' for b,v in top_brands)}\n"
            f"Lowest avg association brands: {', '.join(f'{b} ({v:.1f}%)' for b,v in low_brands)}\n"
            f"Most associated attributes (avg among competing brands only): {', '.join(f'{a} ({v:.1f}%, {brand_counts[a]}brands)' for a,v in top_attrs)}\n"
            f"Most contested attributes (highest std dev among competing brands): {', '.join(f'{a} ({v:.1f}, {brand_counts[a]}brands)' for a,v in top_cont)}\n"
            f"Matrix shape: {mat.shape[0]} brands × {mat.shape[1]} attributes\n"
            f"Note: zeros excluded from stats — they indicate category absence, not low association.\n\n"
            f"Interpret this contingency table. What does the distribution of association rates reveal "
            f"about brand-attribute ownership, category penetration, and competitive differentiation?"
        )
        fallback = (
            f"The association matrix spans {mat.shape[0]} brands and {mat.shape[1]} attributes. "
            f"{top_brands[0][0]} leads with {top_brands[0][1]:.1f}% average association among its active attributes. "
            f"'{top_attrs[0][0]}' has the highest average association score at {top_attrs[0][1]:.1f}% "
            f"(across {brand_counts[top_attrs[0][0]]} competing brands). "
            f"'{top_cont[0][0]}' shows the highest competitive contestation among brands active in that attribute, "
            f"suggesting divergent brand positioning on this dimension."
        )
        return _safe_call(prompt, 0, fallback)
    except Exception as e:
        return f"Input table interpretation unavailable: {e}"


def _interp_ca_chi2_eigen(ca_res: dict) -> str:
    try:
        chi2_t = ca_res["ca_results"]["chi2_test"]
        eig    = ca_res["ca_results"]["eigenvalues"]
        inert  = ca_res["ca_results"]["total_inertia"]
        f1_pct = float(eig["Inertia_%"].iloc[0])
        f2_pct = float(eig["Inertia_%"].iloc[1]) if len(eig) > 1 else 0
        f3_pct = float(eig["Inertia_%"].iloc[2]) if len(eig) > 2 else 0
        cum2   = f1_pct + f2_pct

        differentiation_level = "highly differentiated" if chi2_t["significant"] else "perceptually similar"
        map_quality = "reliable" if cum2 >= 65 else "partial"
        prompt = (
            f"Brand perceptual differentiation analysis:\n"
            f"Are brands perceptually distinct? {'YES — brands occupy distinct positions' if chi2_t['significant'] else 'NO — brands appear interchangeable to consumers'}\n"
            f"How well does the 2D perceptual map capture the full picture? {cum2:.0f}% of brand perception is explained (F1={f1_pct:.0f}%, F2={f2_pct:.0f}%)\n"
            f"Overall perceptual spread (total inertia): {inert:.4f} — {'wide spread = brands are clearly distinct' if inert > 0.05 else 'tight clustering = brands risk being seen as interchangeable'}\n\n"
            f"As a brand strategist, interpret what this means for brand positioning strategy. "
            f"Focus on: are there brands dangerously close to each other (overlap risk)? "
            f"Is the market fragmented or winner-takes-all? "
            f"What does the map coverage ({map_quality}) mean for strategic confidence?"
        )
        fallback = (
            f"Brands in this category are {'clearly differentiated' if chi2_t['significant'] else 'NOT clearly differentiated'} "
            f"in consumers' minds. "
            f"The 2D perceptual map captures {cum2:.0f}% of brand perception — "
            f"{'a reliable basis for positioning decisions' if cum2 >= 65 else 'a partial view; some perception dimensions are not captured'}. "
            f"{'Brands occupy distinct perceptual territories — there is room to own specific attributes.' if chi2_t['significant'] else 'Brands risk being seen as interchangeable — differentiation investment is critical.'}"
        )
        return _safe_call(prompt, 1, fallback)
    except Exception as e:
        return f"Chi-square/eigenvalue interpretation unavailable: {e}"


def _interp_ca_symmetric(ca_res: dict) -> str:
    try:
        ca       = ca_res["ca_results"]
        pc       = ca["row_results"]["principal_coords"]
        ctrs_row = ca["row_results"]["contributions"]
        cos2_row = ca["row_results"]["cos2"]
        ctrs_col = ca["col_results"]["contributions"]
        eig      = ca["eigenvalues"]
        f1_pct   = float(eig["Inertia_%"].iloc[0])
        f2_pct   = float(eig["Inertia_%"].iloc[1]) if len(eig) > 1 else 0

        top_f1_brands = ctrs_row["F1"].nlargest(3).index.tolist() if "F1" in ctrs_row.columns else []
        top_f2_brands = ctrs_row["F2"].nlargest(3).index.tolist() if "F2" in ctrs_row.columns else []
        top_f1_attrs  = ctrs_col["F1"].nlargest(4).index.tolist() if "F1" in ctrs_col.columns else []
        best_rep      = (cos2_row["F1"] + cos2_row.get("F2", 0)).nlargest(3).index.tolist()

        prompt = (
            f"Symmetric perceptual map summary:\n"
            f"F1 ({f1_pct:.1f}% inertia) defined by brands: {', '.join(top_f1_brands)}\n"
            f"F2 ({f2_pct:.1f}% inertia) defined by brands: {', '.join(top_f2_brands)}\n"
            f"Top attributes pulling F1 axis: {', '.join(top_f1_attrs)}\n"
            f"Best 2D-represented brands (high cos²): {', '.join(best_rep)}\n\n"
            f"Interpret this symmetric perceptual map. What are the key competitive axes? "
            f"Which brands cluster together (and therefore compete directly)? "
            f"Which brands occupy unique perceptual positions? What strategic implications follow?"
        )
        fallback = (
            f"The symmetric map positions brands along two axes explaining {f1_pct+f2_pct:.1f}% of variation. "
            f"F1 is primarily defined by {', '.join(top_f1_brands[:2])}, indicating the main axis of perceptual "
            f"differentiation. Brands {', '.join(best_rep)} are best represented in this 2D space, making their "
            f"positions most reliable for strategic interpretation."
        )
        return _safe_call(prompt, 2, fallback)
    except Exception as e:
        return f"Symmetric map interpretation unavailable: {e}"


def _interp_ca_asymmetric(ca_res: dict) -> str:
    try:
        ca       = ca_res["ca_results"]
        pc       = ca["row_results"]["principal_coords"]
        ctrs_col = ca["col_results"]["contributions"]
        eig      = ca["eigenvalues"]
        f1_pct   = float(eig["Inertia_%"].iloc[0])
        f2_pct   = float(eig["Inertia_%"].iloc[1]) if len(eig) > 1 else 0

        top_f1_attrs = ctrs_col["F1"].nlargest(5).index.tolist() if "F1" in ctrs_col.columns else []
        top_f2_attrs = ctrs_col["F2"].nlargest(4).index.tolist() if "F2" in ctrs_col.columns else []

        # Top brands by F1 principal coord magnitude
        f1_coords = pc["F1"].abs().sort_values(ascending=False).head(4)
        extreme_brands = list(f1_coords.index)

        prompt = (
            f"Asymmetric perceptual map (brands on principal coords, attributes on standard coords):\n"
            f"F1 ({f1_pct:.1f}%) top attribute drivers: {', '.join(top_f1_attrs)}\n"
            f"F2 ({f2_pct:.1f}%) top attribute drivers: {', '.join(top_f2_attrs)}\n"
            f"Brands furthest from origin on F1 (most differentiated): {', '.join(extreme_brands)}\n\n"
            f"In an asymmetric biplot, brand positions show where they ARE in perception space, "
            f"and attribute vectors show what PULLS them there. Interpret: which attributes are "
            f"most strongly associated with which brand clusters? What do the attribute pull "
            f"directions reveal about competitive positioning?"
        )
        fallback = (
            f"The asymmetric map reveals attribute pull directions across the perceptual landscape. "
            f"'{top_f1_attrs[0] if top_f1_attrs else 'key attributes'}' most strongly drive F1, "
            f"the primary axis of brand differentiation. "
            f"Brands {', '.join(extreme_brands[:2])} are most distinct from the field on this axis, "
            f"indicating clear perceptual ownership of specific attribute clusters."
        )
        return _safe_call(prompt, 3, fallback)
    except Exception as e:
        return f"Asymmetric map interpretation unavailable: {e}"


def _interp_ca_brand_results(ca_res: dict) -> str:
    try:
        ca       = ca_res["ca_results"]
        ctrs     = ca["row_results"]["contributions"]
        cos2     = ca["row_results"]["cos2"]
        dists    = ca["row_results"]["sq_distances"]

        f1_leaders  = ctrs["F1"].nlargest(3).index.tolist() if "F1" in ctrs.columns else []
        f2_leaders  = ctrs["F2"].nlargest(3).index.tolist() if "F2" in ctrs.columns else []
        best_2d     = (cos2["F1"] + cos2.get("F2", 0)).nlargest(3).index.tolist()
        poor_2d     = (cos2["F1"] + cos2.get("F2", 0)).nsmallest(2).index.tolist()
        far_brands  = dists.nlargest(3).index.tolist() if isinstance(dists, pd.Series) else []

        prompt = (
            f"Brand CA results:\n"
            f"Top F1 contributors: {', '.join(f1_leaders)} (these brands most define horizontal axis)\n"
            f"Top F2 contributors: {', '.join(f2_leaders)} (these brands most define vertical axis)\n"
            f"Best 2D representation (cos² F1+F2): {', '.join(best_2d)}\n"
            f"Poorest 2D fit (position unreliable): {', '.join(poor_2d)}\n"
            f"Brands furthest from centroid: {', '.join(far_brands)}\n\n"
            f"Interpret what these brand-level CA statistics reveal about competitive structure. "
            f"Which brands are the anchors of perceptual space? Which are outliers? "
            f"What does poor 2D representation imply for strategy?"
        )
        fallback = (
            f"Among brands, {', '.join(f1_leaders[:2])} contribute most to defining the primary axis "
            f"of perceptual differentiation. {', '.join(best_2d)} are reliably positioned in 2D space, "
            f"making their map locations strategically actionable. Treat {', '.join(poor_2d)} positions "
            f"with caution — their 2D cos² scores suggest meaningful structure in higher dimensions not captured here."
        )
        return _safe_call(prompt, 4, fallback)
    except Exception as e:
        return f"Brand results interpretation unavailable: {e}"


def _interp_ca_attr_results(ca_res: dict) -> str:
    try:
        ca       = ca_res["ca_results"]
        ctrs     = ca["col_results"]["contributions"]
        cos2     = ca["col_results"]["cos2"]

        f1_attrs = ctrs["F1"].nlargest(5).index.tolist() if "F1" in ctrs.columns else []
        f2_attrs = ctrs["F2"].nlargest(4).index.tolist() if "F2" in ctrs.columns else []
        best_2d  = (cos2["F1"] + cos2.get("F2", 0)).nlargest(4).index.tolist()
        diffuse  = (cos2["F1"] + cos2.get("F2", 0)).nsmallest(3).index.tolist()

        prompt = (
            f"Attribute CA results:\n"
            f"Attributes most discriminating on F1: {', '.join(f1_attrs)}\n"
            f"Attributes most discriminating on F2: {', '.join(f2_attrs)}\n"
            f"Attributes with best 2D map quality (cos²): {', '.join(best_2d)}\n"
            f"Attributes poorly represented in 2D (diffuse signal): {', '.join(diffuse)}\n\n"
            f"Interpret what the attribute CA statistics reveal about the attribute landscape. "
            f"Which attributes are true differentiators? Which are table-stakes (low discrimination)? "
            f"What does the F1 vs F2 attribute split suggest about the two main competitive dimensions?"
        )
        fallback = (
            f"Attribute analysis shows '{f1_attrs[0] if f1_attrs else 'key attribute'}' and "
            f"'{f1_attrs[1] if len(f1_attrs) > 1 else 'second attribute'}' as the primary F1 differentiators, "
            f"defining the main competitive axis in the category. "
            f"Attributes {', '.join(diffuse)} show weak 2D representation, suggesting either universal "
            f"ownership (table stakes) or multi-dimensional association patterns beyond F1+F2."
        )
        return _safe_call(prompt, 5, fallback)
    except Exception as e:
        return f"Attribute results interpretation unavailable: {e}"


def _interp_ca_diagnostics(ca_res: dict) -> str:
    try:
        ca       = ca_res["ca_results"]
        eig      = ca["eigenvalues"]
        cos2_row = ca["row_results"]["cos2"]
        dists    = ca["row_results"]["chisq_distances"]

        pcts = eig["Inertia_%"].tolist()
        cum  = eig["Cum_Inertia_%"].tolist()
        dims_to_70 = next((i+1 for i, c in enumerate(cum) if c >= 70), len(cum))

        avg_cos2_2d = (cos2_row["F1"] + cos2_row.get("F2", 0)).mean() if "F1" in cos2_row.columns else 0
        max_pair = None  # guard: only set if dists is a valid DataFrame
        if isinstance(dists, pd.DataFrame):
            max_dist = dists.values[~np.eye(dists.shape[0], dtype=bool)].max()
            max_pair = None
            for r in dists.index:
                for c in dists.columns:
                    if r != c and abs(dists.loc[r, c] - max_dist) < 0.001:
                        max_pair = (r, c)
                        break

        prompt = (
            f"CA diagnostics summary:\n"
            f"Scree profile: F1={pcts[0]:.1f}%, F2={pcts[1] if len(pcts)>1 else 0:.1f}%, "
            f"F3={pcts[2] if len(pcts)>2 else 0:.1f}%\n"
            f"Dimensions needed to reach 70% variance: {dims_to_70}\n"
            f"Average cos² (F1+F2) across brands: {avg_cos2_2d:.3f}\n"
            f"{'Most perceptually distant brand pair: ' + str(max_pair) if max_pair else ''}\n\n"
            f"Interpret the diagnostic indicators. Is the 2D representation adequate? "
            f"What does the scree profile's shape (steep vs gradual drop) imply about the "
            f"true dimensionality of the perceptual space? Which brand pairs are most perceptually "
            f"distinct (highest chi-square distance)?"
        )
        fallback = (
            f"The scree plot shows {'a steep initial drop' if len(pcts) > 1 and pcts[0] > 40 else 'gradual decline'}, "
            f"with F1 alone explaining {pcts[0]:.1f}% of variation — "
            f"{'indicating a dominant single axis of competition' if pcts[0] > 40 else 'suggesting multi-dimensional competition'}. "
            f"Average 2D cos² of {avg_cos2_2d:.2f} "
            f"{'confirms adequate' if avg_cos2_2d > 0.6 else 'suggests limited'} 2D map reliability. "
            f"At least {dims_to_70} dimensions are needed to capture 70% of perceptual structure."
        )
        return _safe_call(prompt, 6, fallback)
    except Exception as e:
        return f"Diagnostics interpretation unavailable: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# BIP tab interpreters
# ─────────────────────────────────────────────────────────────────────────────

def _interp_bip_raw(bip_res: dict) -> str:
    try:
        mat = bip_res.get("matrix")
        if mat is None or (hasattr(mat, "empty") and mat.empty):
            raise ValueError("empty")
        # Exclude zeros (category-absent brands) from averages
        mat_nz = mat.replace(0, float("nan"))
        brand_avgs = mat_nz.mean(axis=1).sort_values(ascending=False)
        attr_avgs  = mat_nz.mean(axis=0).sort_values(ascending=False)
        top_brands = list(brand_avgs.head(3).items())
        top_attrs  = list(attr_avgs.head(4).items())
        low_brands = list(brand_avgs.tail(2).items())
        brand_counts = (mat > 0).sum(axis=0)

        prompt = (
            f"BIP raw association matrix ({mat.shape[0]} brands × {mat.shape[1]} attributes):\n"
            f"Highest avg association brands (among active attributes): {', '.join(f'{b} ({v:.1f}%)' for b,v in top_brands)}\n"
            f"Lowest avg association brands: {', '.join(f'{b} ({v:.1f}%)' for b,v in low_brands)}\n"
            f"Most associated attributes (avg among competing brands): {', '.join(f'{a} ({v:.1f}%, {brand_counts[a]}brands)' for a,v in top_attrs)}\n"
            f"Note: zeros excluded — they indicate category absence, not low association.\n\n"
            f"Interpret the raw association matrix. What does the absolute level of brand-attribute "
            f"association rates reveal? Which brands have broad-based high awareness of their attributes? "
            f"Are any brands suffering from low attribute visibility across the board?"
        )
        fallback = (
            f"Raw association rates show {top_brands[0][0]} leading at {top_brands[0][1]:.1f}% average "
            f"association across its active attributes — the strongest raw imagery presence in the category. "
            f"'{top_attrs[0][0]}' has the highest average association score at {top_attrs[0][1]:.1f}% "
            f"(across {brand_counts[top_attrs[0][0]]} competing brands), "
            f"suggesting it functions as a category baseline rather than a differentiator at this stage."
        )
        return _safe_call(prompt, 0, fallback)
    except Exception as e:
        return f"Raw matrix interpretation unavailable: {e}"


def _interp_bip_normalized(bip_res: dict) -> str:
    try:
        tables = bip_res.get("tables", {})
        t3 = tables.get("table3_norm_pct")
        if t3 is None or (hasattr(t3, "empty") and t3.empty):
            raise ValueError("empty")
        over_indexed  = {brand: (t3.loc[brand] > 0).sum() for brand in t3.index}
        under_indexed = {brand: (t3.loc[brand] < 0).sum() for brand in t3.index}
        top_over  = sorted(over_indexed.items(), key=lambda x: -x[1])[:3]
        top_under = sorted(under_indexed.items(), key=lambda x: -x[1])[:2]

        # Top over-indexed cells
        max_cell = t3.stack().idxmax()
        min_cell = t3.stack().idxmin()

        prompt = (
            f"BIP normalized matrix (normalized deviation from category average (% above/below market mean)):\n"
            f"Brands with most positive (over-indexed) attributes: "
            f"{', '.join(f'{b} ({n} attrs)' for b,n in top_over)}\n"
            f"Brands with most negative (under-indexed) attributes: "
            f"{', '.join(f'{b} ({n} attrs)' for b,n in top_under)}\n"
            f"Highest single deviation: {max_cell[0]} × '{max_cell[1]}' (+{t3.loc[max_cell]:.1f})\n"
            f"Lowest single deviation: {min_cell[0]} × '{min_cell[1]}' ({t3.loc[min_cell]:.1f})\n\n"
            f"Interpret the normalized deviation matrix. After removing category-level bias, "
            f"which brands genuinely own their attributes vs. merely benefit from category strength? "
            f"What do the under-indexed scores reveal about brand imagery gaps?"
        )
        fallback = (
            f"After normalisation,{top_over[0][0] if top_over else 'leading brand'} "
            f"shows the broadest positive over-indexing across {top_over[0][1] if top_over else 'multiple'} attributes, "
            f"indicating genuine brand-attribute ownership beyond category baselines. "
            f"The strongest single association ({max_cell[0]} × '{max_cell[1]}') represents a "
            f"clear perceptual anchor that this brand should protect and amplify in communications."
        )
        return _safe_call(prompt, 1, fallback)
    except Exception as e:
        return f"Normalized matrix interpretation unavailable: {e}"


def _interp_bip_significance(bip_res: dict) -> str:
    try:
        tables = bip_res.get("tables", {})
        t14 = tables.get("table14_significance")
        if t14 is None or (hasattr(t14, "empty") and t14.empty):
            raise ValueError("empty")
        yes_counts = (t14 == "YES").sum(axis=1).sort_values(ascending=False)
        no_counts  = (t14 == "YES").sum(axis=0)  # attrs that are YES for any brand
        top_owned  = yes_counts.head(3)
        unowned    = (t14 == "YES").sum(axis=0)
        contested  = unowned[unowned > 1].sort_values(ascending=False).head(3)
        exclusive  = unowned[unowned == 1].index.tolist()[:3]

        summary = bip_res.get("significance_summary", {})

        prompt = (
            f"BIP significance matrix (YES = brand significantly over-indexed on attribute):\n"
            f"Brands by owned attribute count: {dict(top_owned.items())}\n"
            f"Most contested attributes (owned by multiple brands): "
            f"{', '.join(contested.index.tolist())}\n"
            f"Exclusive attributes (owned by exactly one brand): {', '.join(exclusive[:3])}\n"
            f"Total significant brand-attribute pairs: {(t14 == 'YES').values.sum()}\n\n"
            f"Interpret the significance pattern. Which brands have the strongest imagery 'signatures'? "
            f"Which attributes are genuinely contested vs. exclusively owned? "
            f"What does this imply for brand positioning and communication strategy?"
        )
        fallback = (
            f"Significance analysis shows {top_owned.index[0] if len(top_owned) else 'top brand'} "
            f"with {int(top_owned.iloc[0]) if len(top_owned) else 0} significantly owned attributes — "
            f"the most distinctive imagery profile in the set. "
            f"{'Exclusively owned attributes signal defensible positioning opportunities.' if exclusive else ''} "
            f"Contested attributes represent battlegrounds where differentiation requires higher investment "
            f"or sharper message focusing."
        )
        return _safe_call(prompt, 2, fallback)
    except Exception as e:
        return f"Significance interpretation unavailable: {e}"


def _interp_bip_profiles(bip_res: dict) -> str:
    try:
        summary = bip_res.get("significance_summary", {})
        tables  = bip_res.get("tables", {})
        t14     = tables.get("table14_significance")
        t3      = tables.get("table3_norm_pct")

        if t14 is not None and not (hasattr(t14, "empty") and t14.empty):
            yes_by_brand = (t14 == "YES").sum(axis=1)
            top_brand    = yes_by_brand.idxmax() if len(yes_by_brand) else "N/A"
            max_count    = int(yes_by_brand.max()) if len(yes_by_brand) else 0
        else:
            top_brand, max_count = "N/A", 0

        if t3 is not None and not (hasattr(t3, "empty") and t3.empty):
            # Most positive deviation per brand
            brand_peaks = t3.max(axis=1).sort_values(ascending=False).head(3)
        else:
            brand_peaks = pd.Series()

        prompt = (
            f"BIP visual profile summary:\n"
            f"Brand with most owned attributes: {top_brand} ({max_count} YES)\n"
            f"Brands by peak attribute deviation: "
            f"{', '.join(f'{b} (+{v:.1f})' for b,v in brand_peaks.items())}\n"
            f"Significance summary: {summary}\n\n"
            f"Interpret the visual profiles — the normalized heatmap, section radar, and brand strength chart. "
            f"Which brands have the most coherent imagery profiles? Which are scattered or weak? "
            f"What do the section radar shapes reveal about functional vs. emotional attribute ownership?"
        )
        fallback = (
            f"Visual profiles show {top_brand} with the broadest imagery signature ({max_count} significant attributes). "
            f"The radar chart reveals which brand-attribute sections are strongest, highlighting where "
            f"each brand's identity is most coherent and which sections represent imagery gaps. "
            f"Brands with high peak deviations but few total YES attributes have concentrated but narrow positioning."
        )
        return _safe_call(prompt, 3, fallback)
    except Exception as e:
        return f"Profile interpretation unavailable: {e}"


def _interp_bip_stats(bip_res: dict) -> str:
    try:
        tables = bip_res.get("tables", {})
        t13    = tables.get("table13_combined")
        t7     = tables.get("table7_ranks")
        t3     = tables.get("table3_norm_pct")

        stats_lines = []
        if t13 is not None and not (hasattr(t13, "empty") and t13.empty):
            top_combined = t13.mean(axis=1).nlargest(3) if hasattr(t13, "mean") else pd.Series()
            stats_lines.append(f"Top combined score brands: {dict(top_combined.round(2).items())}")

        if t3 is not None and not (hasattr(t3, "empty") and t3.empty):
            attr_range = t3.max(axis=0) - t3.min(axis=0)
            contested  = attr_range.nlargest(4)
            stats_lines.append(f"Most contested attributes (highest brand score range): "
                               f"{', '.join(f'{a} ({v:.1f})' for a,v in contested.items())}")

        prompt = (
            f"BIP statistics summary:\n"
            + "\n".join(stats_lines) +
            f"\n\nInterpret the statistical rankings and combined scores. "
            f"Which brands show the most consistently strong imagery across all attributes? "
            f"Which attributes show the widest competitive range (most contested)? "
            f"What strategic actions do these rankings suggest for brand investment and communication focus?"
        )
        fallback = (
            f"Statistical ranking confirms the imagery hierarchy across brands and attributes. "
            f"Combined scores integrate both the frequency and magnitude of over-indexing, "
            f"providing a single comparable metric for brand imagery strength. "
            f"High-range attributes represent the greatest battlegrounds, while low-range attributes "
            f"signal either universal table stakes or underdeveloped category territory."
        )
        return _safe_call(prompt, 4, fallback)
    except Exception as e:
        return f"Statistics interpretation unavailable: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_all(ca_res: dict, bip_res: Optional[dict] = None) -> dict:
    """
    Generate all 12 tab interpretations concurrently.

    Parameters
    ----------
    ca_res  : dict — output of run_ca_pipeline()
    bip_res : dict — output of BIPNormalizationEngine.run(), or None

    Returns
    -------
    dict[str, str]  keys: tab_0..tab_6, bip_0..bip_4
    """
    bip_res = bip_res or {}

    tasks = {
        "tab_0": (_interp_ca_input_table,  (ca_res,)),
        "tab_1": (_interp_ca_chi2_eigen,   (ca_res,)),
        "tab_2": (_interp_ca_symmetric,    (ca_res,)),
        "tab_3": (_interp_ca_asymmetric,   (ca_res,)),
        "tab_4": (_interp_ca_brand_results,(ca_res,)),
        "tab_5": (_interp_ca_attr_results, (ca_res,)),
        "tab_6": (_interp_ca_diagnostics,  (ca_res,)),
        "bip_0": (_interp_bip_raw,         (bip_res,)),
        "bip_1": (_interp_bip_normalized,  (bip_res,)),
        "bip_2": (_interp_bip_significance,(bip_res,)),
        "bip_3": (_interp_bip_profiles,    (bip_res,)),
        "bip_4": (_interp_bip_stats,       (bip_res,)),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_to_key = {pool.submit(fn, *args): key for key, (fn, args) in tasks.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                results[key] = f"Interpretation unavailable ({e})"

    return results
