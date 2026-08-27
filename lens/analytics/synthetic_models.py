"""
synthetic_models.py — TRUE statistical models (Kano, MaxDiff, TURF) run on
fully-customizable SYNTHETIC data, with output structured to match XLSTAT.

WHY THIS EXISTS
---------------
The InfoLeap survey wave did NOT collect the specialised inputs these models
strictly require:
  - Classic Kano needs paired functional / dysfunctional questions per feature.
  - MaxDiff needs a best-worst choice experimental design.
The live engines (kano_engine.py, maxdiff_engine.py) DERIVE approximate versions
from the real CSAT / importance data. This module is the complement: a "Model
Lab" where the *genuine* textbook model runs on synthetic data the user fully
controls — so they can rigorously test the model, see exactly the XLSTAT output
structure, and understand what real best-worst / dual-question collection would
yield.

Everything here is:
  - streamlit-free (pure compute; render lives in the views layer)
  - deterministic given a seed (reproducible test runs)
  - parameterised (the user tunes every knob)
  - structured to mirror XLSTAT's result tables exactly

No external API calls. Local compute only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ═════════════════════════════════════════════════════════════════════════════
# KANO MODEL (classic, dual-question) — XLSTAT "Kano model" output structure
# ═════════════════════════════════════════════════════════════════════════════
#
# Each respondent answers, per feature, a FUNCTIONAL question ("if the product
# HAS this feature, how do you feel?") and a DYSFUNCTIONAL question ("if it does
# NOT have it, how do you feel?"). Both on the 5-point Kano scale:
#     1 = Like it          (I like it that way)
#     2 = Must-be / Expect  (It must be that way)
#     3 = Neutral           (I am neutral)
#     4 = Live with         (I can live with it)
#     5 = Dislike           (I dislike it that way)
#
# The (functional, dysfunctional) pair maps through the Kano evaluation matrix to
# one of: A (Attractive), O (One-dimensional), M (Must-be), I (Indifferent),
#         R (Reverse), Q (Questionable).

KANO_SCALE = {1: "Like", 2: "Must-be", 3: "Neutral", 4: "Live-with", 5: "Dislike"}
KANO_CATEGORIES = ["A", "O", "M", "I", "R", "Q"]
KANO_CATEGORY_NAMES = {
    "A": "Attractive", "O": "One-dimensional", "M": "Must-be",
    "I": "Indifferent", "R": "Reverse", "Q": "Questionable",
}

# Kano evaluation matrix. Rows = functional answer (1-5), cols = dysfunctional
# answer (1-5). Standard Berger/Kano classification.
_KANO_MATRIX = {
    # functional: {dysfunctional: category}
    1: {1: "Q", 2: "A", 3: "A", 4: "A", 5: "O"},
    2: {1: "R", 2: "I", 3: "I", 4: "I", 5: "M"},
    3: {1: "R", 2: "I", 3: "I", 4: "I", 5: "M"},
    4: {1: "R", 2: "I", 3: "I", 4: "I", 5: "M"},
    5: {1: "R", 2: "R", 3: "R", 4: "R", 5: "Q"},
}


def classify_kano_pair(functional: int, dysfunctional: int) -> str:
    """Map one (functional, dysfunctional) answer pair to a Kano category."""
    return _KANO_MATRIX.get(int(functional), {}).get(int(dysfunctional), "Q")


# Archetype answer-probability profiles. For a feature of a given "true" latent
# type, these are the probabilities of each (functional, dysfunctional) pattern
# BEFORE noise. Used by the synthetic generator so a feature tagged "must_be"
# actually produces predominantly M classifications.
_KANO_ARCHETYPES = {
    # type   : (functional_dist over 1-5, dysfunctional_dist over 1-5)
    "attractive":     ([0.55, 0.10, 0.20, 0.10, 0.05], [0.02, 0.08, 0.30, 0.35, 0.25]),
    "one_dimensional":([0.60, 0.15, 0.10, 0.08, 0.07], [0.05, 0.05, 0.10, 0.20, 0.60]),
    "must_be":        ([0.10, 0.55, 0.20, 0.10, 0.05], [0.03, 0.05, 0.10, 0.22, 0.60]),
    "indifferent":    ([0.08, 0.12, 0.50, 0.22, 0.08], [0.08, 0.18, 0.48, 0.18, 0.08]),
    "reverse":        ([0.05, 0.08, 0.20, 0.17, 0.50], [0.45, 0.20, 0.20, 0.10, 0.05]),
}


@dataclass
class KanoFeatureSpec:
    """One synthetic feature: a name, a latent true type, and a noise level."""
    name: str
    true_type: str = "indifferent"      # key in _KANO_ARCHETYPES
    noise: float = 0.15                 # 0..1 blend toward uniform answers


def generate_kano_data(
    features: list[KanoFeatureSpec],
    n_respondents: int = 300,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generate synthetic dual-question Kano responses.

    Returns a long DataFrame: respondent_id, feature, functional, dysfunctional.
    Fully reproducible given `seed`. `noise` blends each feature's archetype
    answer distribution toward uniform (1/5 each) so the user can dial how clean
    or ambiguous the signal is.
    """
    rng = np.random.default_rng(seed)
    uniform = np.full(5, 0.2)
    rows = []
    for feat in features:
        f_dist, d_dist = _KANO_ARCHETYPES.get(
            feat.true_type, _KANO_ARCHETYPES["indifferent"]
        )
        nz = float(np.clip(feat.noise, 0.0, 1.0))
        f_p = (1 - nz) * np.asarray(f_dist) + nz * uniform
        d_p = (1 - nz) * np.asarray(d_dist) + nz * uniform
        f_p /= f_p.sum()
        d_p /= d_p.sum()
        f_ans = rng.choice([1, 2, 3, 4, 5], size=n_respondents, p=f_p)
        d_ans = rng.choice([1, 2, 3, 4, 5], size=n_respondents, p=d_p)
        for r in range(n_respondents):
            rows.append({
                "respondent_id": r,
                "feature": feat.name,
                "functional": int(f_ans[r]),
                "dysfunctional": int(d_ans[r]),
            })
    return pd.DataFrame(rows)


def run_kano_model(df: pd.DataFrame) -> dict:
    """
    Run the classic Kano model on dual-question data (synthetic or real).

    Input: long DataFrame with columns
        feature, functional (1-5), dysfunctional (1-5).

    Output mirrors XLSTAT's Kano result tables:
      {
        "classification": [   # one row per feature (XLSTAT classification table)
            {"feature","n","A","O","M","I","R","Q",        # counts
             "A_pct","O_pct","M_pct","I_pct","R_pct","Q_pct",
             "category",            # dominant (mode) category, full name
             "category_code",       # dominant single letter
             "category_strength",   # (top freq - 2nd freq) in %  [XLSTAT]
             "total_strength",      # (A+O+M)/(A+O+M+I+R+Q) in %
             "better",              # CS+  = (A+O)/(A+O+M+I)
             "worse"},              # CS-  = -(M+O)/(A+O+M+I)
            ...
        ],
        "n_respondents": int,
        "method": "Classic Kano model (dual-question evaluation matrix)",
        "error": None | str,
      }
    """
    try:
        req = {"feature", "functional", "dysfunctional"}
        if not req.issubset(df.columns):
            return {"classification": [], "n_respondents": 0,
                    "method": "Classic Kano model",
                    "error": f"Input must have columns {sorted(req)}"}

        out_rows = []
        for feat, grp in df.groupby("feature"):
            counts = dict.fromkeys(KANO_CATEGORIES, 0)
            for _, r in grp.iterrows():
                cat = classify_kano_pair(r["functional"], r["dysfunctional"])
                counts[cat] += 1
            n = int(len(grp))
            pcts = {c: (counts[c] / n * 100 if n else 0.0) for c in KANO_CATEGORIES}

            # Dominant category = mode (XLSTAT reports the most frequent)
            ranked = sorted(KANO_CATEGORIES, key=lambda c: counts[c], reverse=True)
            dom = ranked[0]
            top_pct = pcts[dom]
            second_pct = pcts[ranked[1]] if len(ranked) > 1 else 0.0
            cat_strength = top_pct - second_pct

            denom = counts["A"] + counts["O"] + counts["M"] + counts["I"]
            better = (counts["A"] + counts["O"]) / denom if denom else 0.0
            worse = -(counts["M"] + counts["O"]) / denom if denom else 0.0
            total_strength = (
                (counts["A"] + counts["O"] + counts["M"])
                / max(sum(counts.values()), 1) * 100
            )

            row = {
                "feature": feat, "n": n,
                **{c: counts[c] for c in KANO_CATEGORIES},
                **{f"{c}_pct": round(pcts[c], 1) for c in KANO_CATEGORIES},
                "category": KANO_CATEGORY_NAMES[dom],
                "category_code": dom,
                "category_strength": round(cat_strength, 1),
                "total_strength": round(total_strength, 1),
                "better": round(better, 3),
                "worse": round(worse, 3),
            }
            out_rows.append(row)

        # Order by Better desc (most actionable / attractive first), like XLSTAT plots
        out_rows.sort(key=lambda r: r["better"], reverse=True)
        return {
            "classification": out_rows,
            "n_respondents": int(df["respondent_id"].nunique()) if "respondent_id" in df.columns else int(len(df)),
            "method": "Classic Kano model (dual-question evaluation matrix)",
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 — degrade gracefully for the UI
        return {"classification": [], "n_respondents": 0,
                "method": "Classic Kano model", "error": str(e)}


# ═════════════════════════════════════════════════════════════════════════════
# MAXDIFF (best-worst scaling) — XLSTAT "MaxDiff" output structure
# ═════════════════════════════════════════════════════════════════════════════
#
# Experimental design: items are shown in tasks (subsets of size k). In each task
# the respondent picks the BEST and the WORST item. From these choices we recover
# item utilities and preference shares.


@dataclass
class MaxDiffItemSpec:
    """One synthetic MaxDiff item: a name and a latent true utility."""
    name: str
    true_utility: float = 0.0


@dataclass
class MaxDiffDesign:
    """Experimental-design knobs the user controls."""
    n_respondents: int = 200
    items_per_task: int = 4
    tasks_per_respondent: int = 8
    seed: int = 42


def generate_maxdiff_data(
    items: list[MaxDiffItemSpec],
    design: MaxDiffDesign = field(default_factory=MaxDiffDesign),
) -> pd.DataFrame:
    """
    Generate synthetic best-worst choice data via a Gumbel (multinomial-logit)
    choice process over the latent true utilities.

    Returns a long DataFrame, one row per shown item per task:
        respondent_id, task_id, item, shown(=1),
        chosen_best(0/1), chosen_worst(0/1)
    Reproducible given design.seed.
    """
    rng = np.random.default_rng(design.seed)
    names = [it.name for it in items]
    utils = np.array([it.true_utility for it in items], dtype=float)
    n_items = len(items)
    k = min(design.items_per_task, n_items)

    rows = []
    task_id = 0
    for r in range(design.n_respondents):
        for _ in range(design.tasks_per_respondent):
            shown_idx = rng.choice(n_items, size=k, replace=False)
            # Best choice: argmax of utility + Gumbel noise (MNL)
            g_best = rng.gumbel(size=k)
            best_local = int(np.argmax(utils[shown_idx] + g_best))
            # Worst choice: argmin of utility + independent Gumbel noise
            g_worst = rng.gumbel(size=k)
            worst_local = int(np.argmin(utils[shown_idx] + g_worst))
            if worst_local == best_local:  # guard degenerate tie
                order = np.argsort(utils[shown_idx] + g_worst)
                worst_local = int(order[0]) if int(order[0]) != best_local else int(order[1])
            for j, idx in enumerate(shown_idx):
                rows.append({
                    "respondent_id": r,
                    "task_id": task_id,
                    "item": names[idx],
                    "shown": 1,
                    "chosen_best": 1 if j == best_local else 0,
                    "chosen_worst": 1 if j == worst_local else 0,
                })
            task_id += 1
    return pd.DataFrame(rows)


def run_maxdiff_model(df: pd.DataFrame, tau: float = 1.0) -> dict:
    """
    Run the aggregate MaxDiff model on best-worst choice data.

    Implements the two analyses XLSTAT reports:
      1. COUNTING analysis  — Best count, Worst count, B-W score, per item.
      2. UTILITIES + PREFERENCE SHARES — utilities via the empirical best-worst
         logit (log of best-share minus log of worst-share, zero-centred), then
         preference share = softmax(utility/tau)*100 (sums to 100).

    Output mirrors XLSTAT's MaxDiff result table:
      {
        "items": [
           {"item","best_count","worst_count","bw_score","bw_mean",
            "n_shown","utility","std_error","pref_share","rescaled_0_100","rank"},
           ...                         # sorted by utility desc, rank 1 = best
        ],
        "n_respondents": int, "n_tasks": int, "tau": tau,
        "method": "MaxDiff — counting + best-worst logit utilities",
        "error": None | str,
      }
    """
    try:
        req = {"item", "shown", "chosen_best", "chosen_worst"}
        if not req.issubset(df.columns):
            return {"items": [], "n_respondents": 0, "n_tasks": 0, "tau": tau,
                    "method": "MaxDiff", "error": f"Input must have columns {sorted(req)}"}

        g = df.groupby("item")
        best = g["chosen_best"].sum()
        worst = g["chosen_worst"].sum()
        shown = g["shown"].sum()
        items = list(shown.index)

        # Empirical best-worst logit utility (standard aggregate MaxDiff estimator):
        #   util_i = log( (best_i + 0.5) / shown_i ) - log( (worst_i + 0.5) / shown_i )
        # 0.5 continuity correction avoids log(0). Then zero-centre.
        best_share = (best + 0.5) / (shown + 1.0)
        worst_share = (worst + 0.5) / (shown + 1.0)
        raw_util = np.log(best_share) - np.log(worst_share)
        util = raw_util - raw_util.mean()

        # Standard error of the B-W logit (delta method on the two proportions)
        se = np.sqrt(1.0 / (best + 0.5) + 1.0 / (worst + 0.5))

        # Preference shares via softmax of utilities (XLSTAT "probability scaling")
        ex = np.exp(util / max(tau, 1e-6))
        pref_share = ex / ex.sum() * 100.0

        bw_score = (best - worst)
        bw_mean = bw_score / shown.replace(0, np.nan)

        # Rescaled 0-100 (min-max of utility) — XLSTAT also reports a rescaled score
        umin, umax = util.min(), util.max()
        rescaled = (util - umin) / (umax - umin) * 100 if umax > umin else util * 0

        rows = []
        for it in items:
            rows.append({
                "item": it,
                "best_count": int(best[it]),
                "worst_count": int(worst[it]),
                "bw_score": int(bw_score[it]),
                "bw_mean": round(float(bw_mean[it]), 3) if pd.notna(bw_mean[it]) else 0.0,
                "n_shown": int(shown[it]),
                "utility": round(float(util[it]), 4),
                "std_error": round(float(se[it]), 4),
                "pref_share": round(float(pref_share[it]), 2),
                "rescaled_0_100": round(float(rescaled[it]), 1),
            })
        rows.sort(key=lambda r: r["utility"], reverse=True)
        for i, r in enumerate(rows):
            r["rank"] = i + 1

        return {
            "items": rows,
            "n_respondents": int(df["respondent_id"].nunique()) if "respondent_id" in df.columns else 0,
            "n_tasks": int(df["task_id"].nunique()) if "task_id" in df.columns else 0,
            "tau": tau,
            "method": "MaxDiff — counting + best-worst logit utilities",
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return {"items": [], "n_respondents": 0, "n_tasks": 0, "tau": tau,
                "method": "MaxDiff", "error": str(e)}


# ═════════════════════════════════════════════════════════════════════════════
# TURF (Total Unduplicated Reach & Frequency) on synthetic reach data
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class TurfItemSpec:
    """One synthetic TURF item: a name and its marginal reach probability."""
    name: str
    reach_prob: float = 0.3


def generate_turf_data(
    items: list[TurfItemSpec],
    n_respondents: int = 1000,
    overlap: float = 0.3,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generate a synthetic respondent×item binary reach matrix.

    `overlap` (0..1) controls correlation between items (shared latent demand) —
    higher overlap means items reach the same people (less incremental TURF gain).
    Returns a wide DataFrame: index respondent_id, one 0/1 column per item.
    """
    rng = np.random.default_rng(seed)
    latent = rng.standard_normal(n_respondents)  # shared demand factor
    data = {}
    ov = float(np.clip(overlap, 0.0, 0.99))
    for it in items:
        thr = rng.standard_normal(n_respondents)
        score = ov * latent + (1 - ov) * thr
        # Calibrate threshold so marginal reach ≈ reach_prob
        cut = np.quantile(score, 1 - float(np.clip(it.reach_prob, 0.01, 0.99)))
        data[it.name] = (score >= cut).astype(int)
    out = pd.DataFrame(data)
    out.index.name = "respondent_id"
    return out


def run_turf_model(reach_df: pd.DataFrame, max_portfolio: int = 5,
                   force_include: list[str] | None = None) -> dict:
    """
    Greedy TURF on a binary reach matrix (respondents × items).

    Output mirrors XLSTAT TURF: the reach curve (each step adds the item with the
    highest INCREMENTAL unduplicated reach) plus per-step reach % and frequency.
      {
        "steps": [
           {"step","item_added","cumulative_reach_n","cumulative_reach_pct",
            "incremental_pct","avg_frequency"}, ...
        ],
        "n_respondents": int, "method": "Greedy TURF", "error": None|str,
      }
    """
    try:
        n = len(reach_df)
        if n == 0 or reach_df.shape[1] == 0:
            return {"steps": [], "n_respondents": 0, "method": "Greedy TURF",
                    "error": "Empty reach matrix"}
        cols = list(reach_df.columns)
        mats = {c: set(reach_df.index[reach_df[c] == 1].tolist()) for c in cols}
        freq = reach_df.sum(axis=1)  # exposures per respondent (for frequency)

        portfolio, reached = [], set()
        remaining = dict(mats)
        steps = []

        for c in (force_include or []):
            if c in remaining:
                portfolio.append(c)
                reached |= remaining.pop(c)
                covered_freq = freq[freq.index.isin(reached)]
                steps.append({
                    "step": len(portfolio), "item_added": c,
                    "cumulative_reach_n": len(reached),
                    "cumulative_reach_pct": round(len(reached) / n * 100, 1),
                    "incremental_pct": round(len(reached) / n * 100, 1) if len(steps) == 0
                                       else round(len(reached) / n * 100 - steps[-1]["cumulative_reach_pct"], 1),
                    "avg_frequency": round(float(covered_freq.mean()), 2) if len(covered_freq) else 0.0,
                })

        while len(portfolio) < max_portfolio and remaining:
            best_item, best_incr = None, set()
            for c, s in remaining.items():
                incr = s - reached
                if len(incr) > len(best_incr):
                    best_item, best_incr = c, incr
            if best_item is None:
                break
            prev_pct = steps[-1]["cumulative_reach_pct"] if steps else 0.0
            portfolio.append(best_item)
            reached |= best_incr
            remaining.pop(best_item)
            covered_freq = freq[freq.index.isin(reached)]
            steps.append({
                "step": len(portfolio), "item_added": best_item,
                "cumulative_reach_n": len(reached),
                "cumulative_reach_pct": round(len(reached) / n * 100, 1),
                "incremental_pct": round(len(reached) / n * 100 - prev_pct, 1),
                "avg_frequency": round(float(covered_freq.mean()), 2) if len(covered_freq) else 0.0,
            })

        return {"steps": steps, "n_respondents": int(n),
                "method": "Greedy TURF", "error": None}
    except Exception as e:  # noqa: BLE001
        return {"steps": [], "n_respondents": 0, "method": "Greedy TURF", "error": str(e)}


# ═════════════════════════════════════════════════════════════════════════════
# Default demo specs (sensible starting point for the Model Lab UI)
# ═════════════════════════════════════════════════════════════════════════════

def default_kano_features() -> list[KanoFeatureSpec]:
    return [
        KanoFeatureSpec("Energy efficiency (BEE 5-star)", "must_be", 0.12),
        KanoFeatureSpec("Durable motor / long warranty", "one_dimensional", 0.12),
        KanoFeatureSpec("Premium design / finish", "attractive", 0.15),
        KanoFeatureSpec("Smart app / IoT control", "attractive", 0.18),
        KanoFeatureSpec("Quiet operation", "one_dimensional", 0.15),
        KanoFeatureSpec("After-sales service network", "must_be", 0.12),
        KanoFeatureSpec("Trendy colour options", "indifferent", 0.20),
        KanoFeatureSpec("Lowest price", "reverse", 0.20),
    ]


def default_maxdiff_items() -> list[MaxDiffItemSpec]:
    return [
        MaxDiffItemSpec("Energy efficiency", 1.6),
        MaxDiffItemSpec("Durability / build quality", 1.3),
        MaxDiffItemSpec("Brand trust", 0.9),
        MaxDiffItemSpec("Price / value", 0.7),
        MaxDiffItemSpec("After-sales service", 0.4),
        MaxDiffItemSpec("Design / aesthetics", -0.2),
        MaxDiffItemSpec("Smart features", -0.6),
        MaxDiffItemSpec("Colour options", -1.4),
    ]


def default_turf_items() -> list[TurfItemSpec]:
    return [
        TurfItemSpec("Variant A — Standard", 0.42),
        TurfItemSpec("Variant B — Premium", 0.30),
        TurfItemSpec("Variant C — Smart", 0.26),
        TurfItemSpec("Variant D — Budget", 0.38),
        TurfItemSpec("Variant E — Designer", 0.18),
        TurfItemSpec("Variant F — Compact", 0.22),
    ]
