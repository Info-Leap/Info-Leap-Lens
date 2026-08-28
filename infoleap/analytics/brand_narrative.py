"""
BrandNarrative — generates a deep market research narrative for a brand.
Uses OpenRouter (Llama 3.3 70B) via synchronous OpenAI client.
Falls back to a rule-based template if API unavailable.
"""

import os
import sys
from pathlib import Path

_LENS_DIR = Path(__file__).resolve().parent.parent
_PROJ_ROOT = _LENS_DIR.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))


def _load_env():
    """Load .env from oxdata/ if not already loaded."""
    env_file = _PROJ_ROOT / "oxdata" / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(str(env_file), override=False)
        except ImportError:
            pass


_load_env()

OR_KEY      = os.getenv("OPENROUTER_API_KEY")
OR_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OR_MODEL    = os.getenv("OPENROUTER_MODEL_PRO", "meta-llama/llama-4-scout:free")

_FREE_MODELS_NR = [
    "meta-llama/llama-4-scout:free",
    "google/gemma-3-12b-it:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "mistralai/mistral-7b-instruct:free",
]


def _rule_based_narrative(brand_name: str, brand_data: dict, base_n: int,
                           zone_data: dict, city_nps: list, rivals: list,
                           brands_list: list = None) -> dict:
    """Deterministic fallback narrative from data returning a dictionary."""
    tom_pct   = brand_data.get("tom_pct", 0)
    spont_pct = brand_data.get("spont_pct", 0)
    aided_pct = brand_data.get("aided_pct", 0)
    nps       = brand_data.get("nps")
    strat     = brand_data.get("strat_score", 0)

    best_zone = max(zone_data.items(), key=lambda x: x[1].get("tom_pct", 0),
                    default=(None, {}))[0] if zone_data else None
    worst_zone = min(zone_data.items(), key=lambda x: x[1].get("tom_pct", 0),
                     default=(None, {}))[0] if zone_data else None

    best_city  = city_nps[0]  if city_nps else None
    worst_city = city_nps[-1] if len(city_nps) > 1 else None
    nps_str = f"NPS {nps:+.0f}" if nps is not None else "NPS data insufficient"
    rival_str = (
        ", ".join(f"{r['brand_name']} (TOM {r['tom_pct']}%)" for r in rivals[:2])
        if rivals else "no direct competitors tracked"
    )

    # brand-perspective descriptions (not stat-heavy)
    if (nps or 0) > 50:
        brand_love = "commands strong consumer affection"
    elif (nps or 0) > 20:
        brand_love = "maintains a solid base of loyalists"
    elif (nps or 0) >= 0:
        brand_love = "holds neutral ground — loved by some, unconvincing to many"
    else:
        brand_love = "faces a trust deficit that must be addressed before growth can accelerate"

    recall_depth = "deep mental presence" if tom_pct > 15 else "moderate recall depth"

    overview = (
        f"{brand_name} occupies {recall_depth} in its category — {tom_pct}% of consumers name it first, "
        f"out of {aided_pct}% who recognise it. "
        f"The brand {brand_love}, with an NPS of {nps_str} against an industry benchmark of +45."
    )

    geographic = "Geographic breakdown unavailable."
    if best_zone and worst_zone:
        bz = zone_data[best_zone]
        wz = zone_data[worst_zone]
        geographic = (
            f"{brand_name}'s heartland is {best_zone} — this is where the brand has built its strongest resonance. "
            f"{worst_zone} is relatively untapped territory and represents the clearest geographic growth opportunity."
        )
        if best_city:
            geographic += f" {best_city['city_name']} leads on consumer advocacy (NPS {best_city['nps']:+.0f})."

    competitive = (
        f"Against key rivals ({rival_str}), {brand_name} is "
        f"{'holding its own or leading' if strat > 50 else 'operating from a challenger position'}. "
        f"{'Its strategic score signals it is a brand to beat in this category.' if strat > 50 else 'There is headroom to close the gap through targeted visibility and loyalty investment.'}"
    ) if rivals else "Competitive context pending — rival data not available for this segment."

    nps_insight = "Advocacy data pending."
    if nps is not None:
        p = brand_data.get("nps_promoters_pct", 0)
        d = brand_data.get("nps_detractors_pct", 0)
        if nps > 50:
            nps_insight = (
                f"{p}% of {brand_name}'s customers are active promoters — a powerful word-of-mouth engine. "
                f"With only {d}% detractors, this brand has earned genuine goodwill that can be leveraged in referral and community-led marketing."
            )
        elif nps >= 0:
            nps_insight = (
                f"{brand_name} has {p}% promoters but {d}% critics — a split that signals unmet expectations. "
                f"Closing the detractor gap through product experience improvements would unlock a significant NPS uplift."
            )
        else:
            nps_insight = (
                f"With {d}% detractors outweighing {p}% promoters, {brand_name} faces a reputation risk. "
                f"Addressing the root causes of customer dissatisfaction is the most urgent brand priority before any campaign investment."
            )

    spont_conv = round(spont_pct / aided_pct * 100, 1) if aided_pct > 0 else 0
    tom_conv   = round(tom_pct / spont_pct * 100, 1) if spont_pct > 0 else 0

    if tom_conv > 60:
        funnel_story = "This is a brand with deep conviction — consumers who recall it are highly likely to consider it first."
    elif tom_conv > 35:
        funnel_story = "The brand has reasonable depth but needs to convert more spontaneous awareness into first-choice preference."
    else:
        funnel_story = "The brand has broad reach but shallow salience — many know it, few prioritise it. The marketing imperative is to deepen emotional relevance, not just expand reach."
    funnel = (
        f"{brand_name} reaches {aided_pct}% of the market at the recognition level, but only {tom_pct}% claim it as their first thought — "
        f"a {tom_conv}% spontaneous-to-TOM conversion rate. "
        + funnel_story
    )

    # Compute top-5 avg for radar comparison
    def _norm_nps(v):
        return round((v + 100) / 2.0, 1) if v is not None else 50.0
    # Rater Depth = brand's NPS base relative to the LARGEST base in the set
    # (self-normalising — no hardcoded /6631 that breaks under filtered views).
    _max_base = max(
        [brand_data.get("nps_base", 0) or 0]
        + [(b.get("nps_base", 0) or 0) for b in (brands_list or [])]
    ) or 1

    def _rater_depth(nps_base):
        return min(round((nps_base or 0) / _max_base * 100, 1), 100)

    b_vals = [tom_pct, spont_pct, aided_pct, _norm_nps(brand_data.get("nps")),
              _rater_depth(brand_data.get("nps_base", 0))]
    if brands_list and len(brands_list) >= 5:
        top5 = sorted(brands_list, key=lambda x: x.get("aided_pct", 0), reverse=True)[:5]
        # Respondent-weighted NPS across top-5 (rater bases vary 35→2515)
        _nps_w = sum(_norm_nps(b.get("nps")) * (b.get("nps_base") or 0) for b in top5)
        _nps_d = sum((b.get("nps_base") or 0) for b in top5)
        _top5_nps = round(_nps_w / _nps_d, 1) if _nps_d > 0 else round(
            sum(_norm_nps(b.get("nps")) for b in top5) / 5, 1)
        avg_vals = [
            round(sum(b["tom_pct"]   for b in top5) / 5, 1),
            round(sum(b["spont_pct"] for b in top5) / 5, 1),
            round(sum(b["aided_pct"] for b in top5) / 5, 1),
            _top5_nps,
            round(sum(_rater_depth(b.get("nps_base", 0)) for b in top5) / 5, 1),
        ]
        strengths  = []
        weaknesses = []
        axes_names = ["Salience (TOM)", "Recall (SPONT)", "Reach (AIDED)", "Loyalty (NPS)", "Rater Depth"]
        for i, (bv, av, ax) in enumerate(zip(b_vals, avg_vals, axes_names)):
            if bv > av + 2:
                strengths.append(f"{ax} ({bv}% vs avg {av}%)")
            elif bv < av - 2:
                weaknesses.append(f"{ax} ({bv}% vs avg {av}%)")
        radar = (
            f"{brand_name} radar: Salience {b_vals[0]}%, Recall {b_vals[1]}%, Reach {b_vals[2]}%, "
            f"Loyalty {b_vals[3]}, Rater Depth {b_vals[4]}%. "
            + (f"Competitive moats: {', '.join(strengths)}. " if strengths else "")
            + (f"Investment priorities: {', '.join(weaknesses)}." if weaknesses else
               "Performance broadly in line with top-5 average across all axes.")
        )
    else:
        radar = (
            f"{brand_name} radar — Salience {b_vals[0]}%, Recall {b_vals[1]}%, "
            f"Reach {b_vals[2]}%, Loyalty (NPS norm) {b_vals[3]}, Rater Depth {b_vals[4]}%. "
            f"Areas extending beyond the comparison ring represent competitive moats; gaps signal investment priorities."
        )

    nps_league = "NPS league ranking pending."
    if brands_list:
        eligible = [b for b in brands_list if b.get("nps") is not None]
        eligible.sort(key=lambda x: x["nps"], reverse=True)
        rank  = next((i + 1 for i, b in enumerate(eligible) if b["brand_name"] == brand_name), None)
        total = len(eligible)
        if rank and nps is not None:
            tier = "top third" if rank <= total / 3 else ("middle tier" if rank <= 2 * total / 3 else "bottom third")
            if nps >= 45:
                nps_league = (
                    f"{brand_name} holds rank #{rank} of {total} brands on consumer advocacy, placing it firmly in the {tier}. "
                    f"This is an asset worth activating — promoter-led referral programmes, testimonial campaigns, "
                    f"and community seeding can turn satisfied customers into a low-cost acquisition engine."
                )
            else:
                nps_league = (
                    f"Ranked #{rank} of {total} brands on consumer advocacy, {brand_name} sits in the {tier} on loyalty. "
                    f"The path forward is not more advertising — it is fixing the experience gaps that turn satisfied "
                    f"customers into neutral or critical ones. Service quality, post-purchase engagement, and warranty "
                    f"trust are typically the levers in appliance categories."
                )

    city_story = "City-level NPS breakdown pending."
    if city_nps and len(city_nps) >= 2:
        top_c  = city_nps[0]
        bot_c  = city_nps[-1]
        if bot_c['nps'] < 0:
            city_recovery = (
                f"{bot_c['city_name']} is a brand repair market — consumers there have actively negative sentiment. "
                f"Direct service interventions, dealer training, or city-specific resolution campaigns are needed before "
                f"any awareness spend makes sense."
            )
        else:
            city_recovery = (
                f"{bot_c['city_name']} is an opportunity market — the brand is present but hasn't yet earned loyalty. "
                f"Local activation, distributor-led programmes, or influencer seeding could shift the NPS trajectory."
            )
        city_story = (
            f"{brand_name}'s loyalty stronghold is {top_c['city_name']} — consumers there are active advocates. "
            f"This market should anchor any word-of-mouth or referral campaign as the proof point. "
            + city_recovery
        )
    elif city_nps:
        city_story = f"{city_nps[0]['city_name']}: NPS {city_nps[0]['nps']:+.0f} — the brand's primary loyalty market."

    median_tom = (
        sum(b.get("tom_pct", 0) for b in brands_list) / max(len(brands_list), 1)
        if brands_list else tom_pct
    )
    nps_val = nps or 0
    if tom_pct > median_tom and nps_val >= 45:
        quadrant = "Market Leader"
        marketing_play = (
            "protect category leadership by widening the moat: invest in premium positioning, "
            "deepen loyalty through exclusive owner communities, and use the strong NPS as social proof "
            "in category-entry point communications (dealer point-of-sale, search, and comparison sites)."
        )
    elif tom_pct <= median_tom and nps_val >= 45:
        quadrant = "Loyalty Hidden Gem"
        marketing_play = (
            "convert satisfied customers into brand ambassadors — the NPS score is the brand's most "
            "underdeveloped commercial asset. Referral mechanics, user-generated content, and 'hear from our "
            "customers' media campaigns can convert word-of-mouth into top-line awareness growth without "
            "requiring heavy traditional media spend."
        )
    elif tom_pct > median_tom and nps_val < 45:
        quadrant = "Awareness Leader"
        marketing_play = (
            "shift the marketing mix from awareness-building to experience-deepening: the brand is already "
            "well-known but hasn't earned trust. Post-purchase engagement programmes, service quality campaigns, "
            "and closing the promoter-detractor gap are higher-leverage than more TV or digital reach."
        )
    else:
        quadrant = "Growth Opportunity"
        marketing_play = (
            "focus on a specific geography or consumer segment where the brand can win first, rather than "
            "spreading thin across the whole market. A regional stronghold strategy — own the North or own "
            "the mid-income segment — builds a credible base before national scale."
        )

    positioning = (
        f"{brand_name} sits in the '{quadrant}' position — "
        f"{'above' if tom_pct > median_tom else 'below'} the market median on salience and "
        f"{'above' if nps_val >= 45 else 'below'} the industry benchmark on consumer advocacy. "
        f"The marketing play here is to {marketing_play}"
    )

    return {
        "overview":    overview,
        "geographic":  geographic,
        "competitive": competitive,
        "nps":         nps_insight,
        "funnel":      funnel,
        "radar":       radar,
        "nps_league":  nps_league,
        "city_story":  city_story,
        "positioning": positioning,
        "salience_finding": (
            f"{brand_name} is a {'first-choice brand that consumers name without prompting' if tom_conv > 50 else 'recognised brand that has not yet earned first-choice status'} — "
            f"the gap between aided awareness and top-of-mind recall is "
            f"{'narrow, signalling genuine mental ownership of the category' if tom_conv > 60 else 'material, signalling that familiarity has not yet converted to preference'}."
        ),
        "loyalty_finding": (
            f"{'Consumer advocacy is a genuine strength for ' + brand_name + ' — its promoter base is a marketing asset that can be activated through referral mechanics and owner community campaigns' if nps_val >= 45 else brand_name + ' has a loyalty gap that marketing spend alone cannot fix — improving the customer experience at key post-purchase touchpoints is the most direct route to NPS recovery'}."
        ),
        "dynamics_finding": (
            f"{brand_name} is a '{quadrant}' brand — "
            f"{'the challenge is to stay ahead as competitors invest; complacency is the main risk at this position' if quadrant == 'Market Leader' else 'the opportunity is asymmetric: improving the weaker dimension (loyalty or salience) will unlock disproportionate brand value'}."
        ),
        "imagery_finding": (
            f"{'Strong strategic score signals a brand with clear, owned territory in the category' if strat > 50 else 'The developing strategic score signals opportunity — the brand has not yet staked out a distinct, ownable position'}; "
            f"{'reinforcing this through consistent communication will cement category leadership' if strat > 50 else 'differentiation and a sharper brand narrative are the levers to pull'}."
        ),
    }


def generate_brand_narrative(brand_name: str, brand_data: dict, base_n: int,
                              zone_data: dict, city_nps: list,
                              rivals: list, brands_list: list = None,
                              theme: str = "All", top_n: int = 10) -> dict:
    """
    Generates a dictionary of market research insights (13 keys).
    Uses OpenRouter LLM with pre-computed facts to prevent hallucinations.
    Keys: overview, geographic, competitive, nps, funnel, radar, nps_league, city_story, positioning,
          salience_finding, loyalty_finding, dynamics_finding, imagery_finding
    """
    if not OR_KEY:
        return _rule_based_narrative(brand_name, brand_data, base_n,
                                     zone_data, city_nps, rivals, brands_list)

    # 2026-07-27: found live-testing a project with zero fact_brand_nps rows (not every project
    # necessarily has an NPS/recommend question mapped) — `.get(key, 0)` only substitutes the
    # default when the KEY is absent, not when it's present but explicitly None (which is what
    # the upstream data function returns for "no NPS data for this brand"), so `nps > rnps` threw
    # TypeError: '>' not supported between float and NoneType. `or 0` catches both cases.
    tom   = brand_data.get('tom_pct', 0) or 0
    spont = brand_data.get('spont_pct', 0) or 0
    aided = brand_data.get('aided_pct', 0) or 0
    nps   = brand_data.get('nps', 0) or 0

    rival_facts = []
    for r in rivals[:2]:
        rtom = r.get('tom_pct', 0) or 0
        rnps = r.get('nps', 0) or 0
        rival_facts.append(
            f"- vs {r['brand_name']}: TOM ({tom}% vs {rtom}%) is {'HIGHER' if tom > rtom else 'LOWER'}. "
            f"NPS ({nps} vs {rnps}) is {'HIGHER' if nps > rnps else 'LOWER'}."
        )
    rival_fact_str = "\n".join(rival_facts)

    zone_summary = "\n".join(
        f"  {z}: TOM {d['tom_pct']}% | SPONT {d['spont_pct']}% | "
        f"Aided {d['aided_pct']}% | NPS {d['nps']:+.0f} (n={d['nps_base']})"
        if d.get("nps") is not None else
        f"  {z}: TOM {d['tom_pct']}% | SPONT {d['spont_pct']}% | Aided {d['aided_pct']}%"
        for z, d in zone_data.items()
    )

    best_city_str  = f"{city_nps[0]['city_name']} (NPS {city_nps[0]['nps']:+.0f})"  if city_nps else "—"
    worst_city_str = f"{city_nps[-1]['city_name']} (NPS {city_nps[-1]['nps']:+.0f})" if len(city_nps) > 1 else "—"
    nps_str = f"{brand_data['nps']:+.0f}" if brand_data.get("nps") is not None else "N/A"

    spont_conv = round(spont / aided * 100, 1) if aided > 0 else 0
    tom_conv   = round(tom / spont * 100, 1)   if spont > 0 else 0

    nps_rank_str = "—"
    if brands_list:
        eligible = [b for b in brands_list if b.get("nps") is not None]
        eligible.sort(key=lambda x: x["nps"], reverse=True)
        rank = next((i + 1 for i, b in enumerate(eligible) if b["brand_name"] == brand_name), None)
        if rank:
            nps_rank_str = f"#{rank} of {len(eligible)}"

    median_tom = (
        sum(b.get("tom_pct", 0) for b in brands_list) / max(len(brands_list), 1)
        if brands_list else tom
    )
    nps_val = brand_data.get("nps") or 0
    if tom > median_tom and nps_val >= 45:
        quadrant = "Market Leader"
    elif tom <= median_tom and nps_val >= 45:
        quadrant = "Loyalty Hidden Gem"
    elif tom > median_tom:
        quadrant = "Awareness Leader"
    else:
        quadrant = "Growth Opportunity"

    # 2026-07-28: found live via browser-testing a freshly-ingested project with zero NPS rows —
    # same root cause as the nps_val guard above (line 363): brand_data.get(key, 0) only
    # substitutes the default when the KEY is absent, not when it's present but explicitly None
    # (what the upstream data function returns for "no NPS data"). `or 0` catches both. Guarded
    # once here and reused below (prompt f-string) instead of repeating the unguarded .get() calls
    # inline, which would still crash on the `:.0f` format spec applied to a bare None.
    promoters_pct = brand_data.get('nps_promoters_pct', 0) or 0
    detractors_pct = brand_data.get('nps_detractors_pct', 0) or 0
    passives_pct = round(100 - promoters_pct - detractors_pct, 0)
    strongest_zone = max(zone_data.items(), key=lambda x: x[1].get('tom_pct', 0))[0] if zone_data else '—'
    weakest_zone   = min(zone_data.items(), key=lambda x: x[1].get('tom_pct', 0))[0] if zone_data else '—'

    prompt = f"""You are a senior brand strategy consultant briefing the CMO of an Indian electrical appliances company.

MARKET CONTEXT:
This is an FMCD (Fast Moving Consumer Durables) brand health study — ceiling fans, mixer-grinders, water heaters, room coolers, and related electrical appliances bought by middle-class Indian households (₹2,000–₹50,000 price range). Key dynamics: dealer-led purchase decisions, word-of-mouth is the dominant acquisition channel, family influencers shape brand choice, service reputation is decisive post-purchase. Urban metros and Tier-2 cities show divergent brand loyalty patterns.

=== VERIFIED DATA — USE THESE EXACT NUMBERS. NEVER INVENT ADDITIONAL DATA. ===

BRAND: {brand_name}
SAMPLE BASE: {base_n:,} respondents

AWARENESS FUNNEL:
  Aided Awareness  (prompted recognition): {aided}%
  Spontaneous Recall (unprompted naming): {spont}%
  Top of Mind / TOM (named FIRST): {tom}%
  Aided → Spont conversion rate: {spont_conv:.1f}%
  Spont → TOM conversion rate: {tom_conv:.1f}%

ADVOCACY (NPS):
  NPS Score: {nps_str}  |  Industry benchmark: +45  |  Rank: {nps_rank_str}
  Promoters: {promoters_pct:.0f}%  |  Passives: {passives_pct:.0f}%  |  Detractors: {detractors_pct:.0f}%

STRATEGIC QUADRANT: {quadrant}
  TOM {tom}% vs market median {median_tom:.1f}% → {'ABOVE' if tom > median_tom else 'BELOW'} median
  NPS {nps_str} vs benchmark +45 → {'ABOVE' if (brand_data.get('nps') or 0) >= 45 else 'BELOW'} benchmark

REGIONAL FOOTPRINT (TOM | SPONT | Aided | NPS):
{zone_summary if zone_summary else "  Regional data unavailable."}
  Strongest zone (TOM): {strongest_zone}  |  Weakest zone (TOM): {weakest_zone}

CITY NPS:
  Highest: {best_city_str}  |  Lowest: {worst_city_str}

COMPETITIVE FACTS (exact — do NOT soften or rephrase):
{rival_fact_str if rival_fact_str else "  No rival data tracked in this wave."}

=== WRITE 13 BRAND INSIGHTS ===

NON-NEGOTIABLE RULES:
1. Every sentence must contain at least one specific metric or named entity from the data above.
2. Banned phrases: "it appears", "may suggest", "seems to", "could indicate", "it is worth noting", "interestingly". State verdicts directly.
3. Bold (**) the single most important phrase in each section — the one thing the CMO must retain.
4. Each section must illuminate a DIFFERENT dimension of the brand picture — no repeating.
5. Tie insights to Indian FMCD context: word-of-mouth, dealer push, service trust, regional identity.

─── SECTION INSTRUCTIONS ───

OVERVIEW (2 sentences):
  S1: Is {brand_name} a first-choice brand (consumers think of it first) or a prompted brand (recalled only when reminded)? Cite TOM and aided figures to make the verdict specific.
  S2: What is the ONE commercial risk or opportunity this awareness profile creates right now?
  Example of the right tone: "**[Brand] is a dealer's brand, not a household name** — {aided}% recognition collapses to {tom}% first-choice, a profile that wins at point-of-sale but loses in the consumer's unaided consideration set."

GEOGRAPHIC (2 sentences):
  S1: Name the geographic personality. Is it a North-dominant, South-stronghold, or East-West split brand? Use zone TOM data to name actual regions.
  S2: Which zone to defend (strongest) and which to invest (weakest)? Give actionable territory logic, not just description.

COMPETITIVE (2 sentences — use ONLY the competitive facts listed above):
  S1: On salience (TOM), deliver a verdict: winning, trailing, or matching each named rival by how many percentage points.
  S2: On loyalty (NPS), same — then state the strategic priority: press the advantage, close the gap, or build a moat.

NPS (2 sentences):
  S1: Decode what {promoters_pct:.0f}% promoters and {detractors_pct:.0f}% detractors reveals about the type of customer experience this brand delivers. Is this widespread delight, divided opinion, or an indifferent majority?
  S2: Name the commercial play — word-of-mouth activation (if promoters are high), detractor root-cause fix (if detractors are high), or passive conversion programme (if passives dominate). In FMCD, promoters are the primary acquisition channel through referrals.

FUNNEL (2 sentences):
  S1: Diagnose WHERE the funnel leaks most: at aided→spont ({spont_conv:.1f}%) or at spont→TOM ({tom_conv:.1f}%)? Name the bigger loss stage and what it signals about the brand's weakness.
  S2: Match the marketing fix to the specific break point — media spend addresses aided awareness gaps; salience campaigns and SOV address spontaneous recall gaps; emotional relevance and category-entry-point advertising addresses TOM gaps.

RADAR (2 sentences):
  S1: Name {brand_name}'s single strongest pillar vs. the top-5 average and its single weakest. Be specific — not "the brand performs well on some axes."
  S2: Does the radar shape reveal a specialist brand (one dominant spike = deep association on fewer dimensions) or a generalist brand (broad but shallow scores)? What does this shape imply for communications strategy?

NPS_LEAGUE (2 sentences):
  S1: State the exact rank ({nps_rank_str}), what tier it places this brand in, and whether this makes {brand_name} an advocacy leader, a mid-pack brand, or a loyalty laggard.
  S2: How should a brand at rank {nps_rank_str} talk to consumers differently than a brand ranked #1? What media or CRM moves does this loyalty rank unlock or foreclose?

CITY_STORY (2 sentences):
  S1: Characterise the structural NPS pattern across cities — concentrated stronghold (1-2 cities dominate), broadly consistent, or polarised (some cities love, some cities hate). Name the actual cities.
  S2: Which city should anchor referral/word-of-mouth campaigns ({best_city_str})? Which needs a service rescue or targeted recovery plan ({worst_city_str})?

POSITIONING (2 sentences):
  S1: Translate '{quadrant}' into the commercial situation: what problem does this position create or what advantage does it lock in — for this specific brand, not generically.
  S2: The single most urgent go-to-market action. Be specific: which channel, which consumer segment, which message, which rival to target or gap to exploit.
  Example of the right tone: "As an **Awareness Leader** with {tom}% TOM but NPS below +45, {brand_name} is the brand everyone knows but fewer love — the media spend is working, the experience is not. The immediate priority is a post-purchase service quality campaign targeting owners in {weakest_zone}, where detractors are likeliest to cluster."

─── REACTIVE FINDINGS — 1 sentence each, binary verdict, no qualifications ───

SALIENCE_FINDING: Is {brand_name} a **top-of-mind brand** or a **prompted brand**? The {tom_conv:.1f}% spont→TOM conversion proves which — state the verdict and what it means for media investment.

LOYALTY_FINDING: Is {brand_name}'s NPS of {nps_str} an **advocacy engine to activate** or a **trust deficit to repair first**? State which and what the single repair lever is.

DYNAMICS_FINDING: In this competitive set, is {brand_name} in a **position to attack** (challenger with momentum) or **position to defend** (leader under pressure)? State which and name the specific competitive threat or opportunity.

IMAGERY_FINDING: Does {brand_name} have a **single ownable attribute** it can stamp on every touchpoint, or is it a **generic presence with no distinctive territory**? State which and name the attribute (or the gap).

=== OUTPUT FORMAT (exact labels, no blank lines between entries) ===
OVERVIEW: [text]
GEOGRAPHIC: [text]
COMPETITIVE: [text]
NPS: [text]
FUNNEL: [text]
RADAR: [text]
NPS_LEAGUE: [text]
CITY_STORY: [text]
POSITIONING: [text]
SALIENCE_FINDING: [text]
LOYALTY_FINDING: [text]
DYNAMICS_FINDING: [text]
IMAGERY_FINDING: [text]"""

    sys_msg = (
        "Senior brand strategy analyst. Decisive, opinionated, specific. "
        "Use **bold** to emphasise key terms. Trust all pre-computed facts exactly — never invent data."
    )
    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user",   "content": prompt},
    ]
    content = None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OR_KEY, base_url=OR_BASE_URL)
        for model in _FREE_MODELS_NR:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.15,
                    max_tokens=2200,
                )
                content = response.choices[0].message.content.strip()
                if content:
                    break
            except Exception:
                continue

        if not content:
            raise RuntimeError("All free models returned empty response")

        parsed = {}
        for line in content.split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                k = key.strip().lower().replace(" ", "_")
                parsed[k] = val.strip()

        required = ["overview", "geographic", "competitive", "nps",
                    "funnel", "radar", "nps_league", "city_story", "positioning",
                    "salience_finding", "loyalty_finding", "dynamics_finding", "imagery_finding"]
        if all(k in parsed for k in required):
            return parsed

        fallback = _rule_based_narrative(brand_name, brand_data, base_n,
                                         zone_data, city_nps, rivals, brands_list)
        for k in required:
            if k not in parsed:
                parsed[k] = fallback[k]
        return parsed

    except Exception as e:
        print(f"[BrandNarrative] LLM call failed: {e}. Using rule-based fallback.")
        return _rule_based_narrative(brand_name, brand_data, base_n,
                                     zone_data, city_nps, rivals, brands_list)


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTIVE COMMAND BRIEFING — C-suite role-aware strategic synthesis
# ─────────────────────────────────────────────────────────────────────────────

def _briefing_facts(brand_name, brand_data, base_n, zone_data, rivals, brands_list):
    """Pre-compute the verified fact pack used by both LLM and rule-based briefings."""
    tom   = float(brand_data.get("tom_pct", 0) or 0)
    spont = float(brand_data.get("spont_pct", 0) or 0)
    aided = float(brand_data.get("aided_pct", 0) or 0)
    nps   = brand_data.get("nps")
    nps_v = float(nps) if nps is not None else None
    prom  = float(brand_data.get("nps_promoters_pct", 0) or 0)
    detr  = float(brand_data.get("nps_detractors_pct", 0) or 0)
    pass_ = round(100 - prom - detr, 0)
    consid = float(brand_data.get("consideration_pct", 0) or 0)

    spont_conv = round(spont / aided * 100, 1) if aided > 0 else 0.0
    tom_conv   = round(tom / spont * 100, 1) if spont > 0 else 0.0

    bl = brands_list or []
    tom_sorted = sorted(bl, key=lambda x: x.get("tom_pct", 0) or 0, reverse=True)
    tom_rank = next((i + 1 for i, b in enumerate(tom_sorted)
                     if b.get("brand_name") == brand_name), None)
    nps_eligible = sorted([b for b in bl if b.get("nps") is not None],
                          key=lambda x: x["nps"], reverse=True)
    nps_rank = next((i + 1 for i, b in enumerate(nps_eligible)
                     if b.get("brand_name") == brand_name), None)
    n_brands = len(bl)
    n_nps = len(nps_eligible)  # NPS rank denominator (only brands with NPS data)
    median_tom = (sum(b.get("tom_pct", 0) or 0 for b in bl) / max(len(bl), 1)) if bl else tom

    if tom > median_tom and (nps_v or 0) >= 45:
        quadrant = "Market Leader"
    elif tom <= median_tom and (nps_v or 0) >= 45:
        quadrant = "Loyalty Hidden Gem"
    elif tom > median_tom:
        quadrant = "Awareness Leader"
    else:
        quadrant = "Growth Opportunity"

    strongest_zone = max(zone_data.items(), key=lambda x: x[1].get("tom_pct", 0))[0] if zone_data else None
    weakest_zone   = min(zone_data.items(), key=lambda x: x[1].get("tom_pct", 0))[0] if zone_data else None

    if spont_conv < tom_conv:
        leak_stage, leak_val = "Aided-to-Spontaneous", spont_conv
    else:
        leak_stage, leak_val = "Spontaneous-to-TOM", tom_conv

    return dict(
        tom=tom, spont=spont, aided=aided, nps=nps_v, prom=prom, detr=detr,
        passives=pass_, consid=consid, spont_conv=spont_conv, tom_conv=tom_conv,
        tom_rank=tom_rank, nps_rank=nps_rank, n_brands=n_brands, n_nps=n_nps, median_tom=median_tom,
        quadrant=quadrant, strongest_zone=strongest_zone, weakest_zone=weakest_zone,
        leak_stage=leak_stage, leak_val=leak_val,
    )


def _rule_based_briefing(brand_name, f):
    """Deterministic C-suite briefing fallback from the fact pack `f`."""
    nps_str = f"{f['nps']:+.0f}" if f["nps"] is not None else "N/A"
    tom_rank_str = f"#{f['tom_rank']} of {f['n_brands']}" if f["tom_rank"] else "-"
    nps_rank_str = f"#{f['nps_rank']} of {f.get('n_nps', f['n_brands'])}" if f["nps_rank"] else "-"
    above_med = f["tom"] > f["median_tom"]
    nps_strong = (f["nps"] or 0) >= 45

    bottom_line = (
        f"{brand_name} is a **{f['quadrant']}** - salience rank {tom_rank_str}, "
        f"advocacy rank {nps_rank_str}. "
        + ("The brand wins on both reach and loyalty; the job is to defend the lead."
           if above_med and nps_strong else
           "Awareness outruns loyalty - the experience is not keeping pace with the marketing."
           if above_med else
           "Loyalty outruns awareness - a strong product under-marketed."
           if nps_strong else
           "Both reach and loyalty trail the field - this is a build, not a defend, situation.")
    )

    market = (
        f"{brand_name} holds {f['tom']:.1f}% top-of-mind (rank {tom_rank_str}) "
        f"against a {f['median_tom']:.1f}% category average, with NPS {nps_str} (rank {nps_rank_str}). "
        f"Salience is strongest in the {f['strongest_zone'] or 'core'} zone and weakest in "
        f"{f['weakest_zone'] or 'under-indexed regions'}, marking the {f['weakest_zone'] or 'lagging'} "
        f"zone as the clearest **distribution headroom**. "
        + ("The position is defensible on both reach and loyalty."
           if above_med and nps_strong else
           "Growth potential is real but constrained by experience quality.")
    )

    demand = (
        f"The funnel leaks most at **{f['leak_stage']}** ({f['leak_val']:.0f}% conversion). "
        + ("The constraint is first-choice salience and distinctiveness rather than reach."
           if f["leak_stage"] == "Spontaneous-to-TOM" else
           "Recognition is not converting into unaided recall, indicating a salience and "
           "distinctive-asset gap.") +
        f" Consideration stands at {f['consid']:.1f}%, and with {f['prom']:.0f}% promoters, "
        f"referral is the lowest-cost demand channel in this FMCD set."
    )

    experience = (
        f"Advocacy splits **{f['prom']:.0f}% promoters / {f['detr']:.0f}% detractors** "
        f"({f['passives']:.0f}% passive). "
        + ("Detractors are the binding constraint, concentrated in service and reliability complaints "
           "that cap NPS and word-of-mouth."
           if f["detr"] >= f["prom"] or (f["nps"] or 0) < 30 else
           "A strong promoter base signals product-market fit, with room to consolidate a single "
           "ownable performance attribute across the range.") +
        " Service trust and durability are the dominant experience signals on advocacy in this category."
    )

    return {
        "bottom_line": bottom_line,
        "market": market, "demand": demand, "experience": experience,
        "quadrant": f["quadrant"],
        "tom_rank_str": tom_rank_str, "nps_rank_str": nps_rank_str,
    }


def generate_executive_briefing(brand_name, brand_data, base_n, zone_data,
                                 city_nps, rivals, brands_list=None,
                                 imagery_context: str = "") -> dict:
    """C-suite Executive Command Briefing: a Bottom Line plus CEO / CMO / Product
    role-targeted strategic briefs, grounded in verified metrics.

    Returns dict keys: bottom_line, ceo, cmo, product, quadrant,
                       tom_rank_str, nps_rank_str.
    """
    f = _briefing_facts(brand_name, brand_data, base_n, zone_data, rivals, brands_list)

    if not OR_KEY:
        return _rule_based_briefing(brand_name, f)

    nps_str = f"{f['nps']:+.0f}" if f["nps"] is not None else "N/A"
    tom_rank_str = f"#{f['tom_rank']} of {f['n_brands']}" if f["tom_rank"] else "-"
    nps_rank_str = f"#{f['nps_rank']} of {f.get('n_nps', f['n_brands'])}" if f["nps_rank"] else "-"

    rival_facts = []
    for r in (rivals or [])[:3]:
        rival_facts.append(
            f"- {r['brand_name']}: TOM {r.get('tom_pct',0)}% | NPS {r.get('nps','N/A')}"
        )
    rival_str = "\n".join(rival_facts) or "  No rivals tracked this wave."

    img_block = f"\nIMAGERY / DRIVER SIGNALS:\n{imagery_context}\n" if imagery_context else ""

    prompt = f"""You are a senior market-research analyst writing the executive summary of a brand
health study for an Indian FMCD (electrical appliances) brand. Households buy ceiling fans,
mixer-grinders, water heaters, coolers (Rs 2,000-50,000). Purchase is dealer-influenced,
word-of-mouth led, service-reputation decisive.

=== VERIFIED DATA - USE THESE EXACT NUMBERS. NEVER INVENT. ===
BRAND: {brand_name}  |  BASE: {base_n:,} respondents
AWARENESS: Aided {f['aided']}% - Spontaneous {f['spont']}% - TOM {f['tom']}% (rank {tom_rank_str})
  Conversion: Aided-to-Spont {f['spont_conv']:.0f}% - Spont-to-TOM {f['tom_conv']:.0f}%  (worst leak: {f['leak_stage']} at {f['leak_val']:.0f}%)
ADVOCACY: NPS {nps_str} (rank {nps_rank_str}, benchmark +45) - Promoters {f['prom']:.0f}% - Passives {f['passives']:.0f}% - Detractors {f['detr']:.0f}%
CONSIDERATION: {f['consid']:.1f}%
STRATEGIC QUADRANT: {f['quadrant']} (TOM {f['tom']}% vs category average {f['median_tom']:.1f}%)
GEOGRAPHY: strongest {f['strongest_zone'] or '-'} - weakest {f['weakest_zone'] or '-'}
RIVALS:
{rival_str}{img_block}
=== WRITE THE SUMMARY ===
Four sections, neutral and objective analyst voice. Do NOT address any role (no "the CEO should",
no "you"). State findings and their implications, grounded in the data. Each sentence must carry a
specific metric or named entity. Banned: "it appears", "may suggest", "seems to", "could indicate".
Bold (**) the single most important phrase per section. Each section MUST take a different angle.

BOTTOM_LINE (1 sentence): the headline verdict - what kind of brand this is on the evidence, and the single most consequential implication.

MARKET (2 sentences): market position - salience rank, NPS rank, and the regional pattern. Identify the structural advantage and where the headroom or risk sits, by named zone. Objective, not prescriptive advice.

DEMAND (2 sentences): the awareness-to-preference funnel. Name the exact break point ({f['leak_stage']}) and what it implies about the demand gap (reach vs salience vs distinctiveness). Reference consideration {f['consid']:.1f}% and {f['prom']:.0f}% promoters as a referral signal.

EXPERIENCE (2 sentences): the advocacy/experience signal. Decode {f['prom']:.0f}% promoters / {f['detr']:.0f}% detractors and what it indicates about product/service experience - service trust, durability, or a distinctive attribute.

=== OUTPUT FORMAT (exact labels, no blank lines) ===
BOTTOM_LINE: [text]
MARKET: [text]
DEMAND: [text]
EXPERIENCE: [text]"""

    sys_msg = (
        "Senior market-research analyst writing an objective executive summary. Decisive, specific, "
        "numerate, neutral - never address a role or use 'you'. Use **bold** for the key phrase. "
        "Trust all pre-computed facts exactly - never invent data."
    )
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OR_KEY, base_url=OR_BASE_URL)
        content = None
        for model in _FREE_MODELS_NR:
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": sys_msg},
                              {"role": "user", "content": prompt}],
                    temperature=0.15, max_tokens=1100,
                )
                content = (resp.choices[0].message.content or "").strip()
                if content:
                    break
            except Exception:
                continue
        if not content:
            raise RuntimeError("All free models empty")

        parsed = {}
        cur_key = None
        for line in content.split("\n"):
            m = line.split(":", 1)
            tag = m[0].strip().lower()
            if tag in ("bottom_line", "market", "demand", "experience") and len(m) == 2:
                cur_key = tag
                parsed[cur_key] = m[1].strip()
            elif cur_key and line.strip():
                parsed[cur_key] += " " + line.strip()

        required = ["bottom_line", "market", "demand", "experience"]
        if all(k in parsed and parsed[k] for k in required):
            parsed.update({"quadrant": f["quadrant"],
                           "tom_rank_str": tom_rank_str, "nps_rank_str": nps_rank_str})
            return parsed

        fb = _rule_based_briefing(brand_name, f)
        for k in required:
            if not parsed.get(k):
                parsed[k] = fb[k]
        parsed.update({"quadrant": f["quadrant"],
                       "tom_rank_str": tom_rank_str, "nps_rank_str": nps_rank_str})
        return parsed

    except Exception as e:
        print(f"[ExecBriefing] LLM failed: {e}. Rule-based fallback.")
        return _rule_based_briefing(brand_name, f)
