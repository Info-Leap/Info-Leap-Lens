"""
Transcript Matrix Builder — Phase 1 of the Qual Intelligence Pipeline.

Takes raw cleaned transcript MD files and extracts an 8-dimension structured
matrix via two LLM passes (OpenRouter free models). Output: *_matrix.json per doc.

Usage:
    python lens/ingestion/transcript_matrix_builder.py --dry-run   # 5 docs only
    python lens/ingestion/transcript_matrix_builder.py              # all 233 docs
"""

import os
import sys
import json
import time
import argparse
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI

# Force unbuffered output so progress shows in background job logs
sys.stdout.reconfigure(line_buffering=True)

load_dotenv(Path(__file__).parent.parent.parent / "oxdata" / ".env")

# ── Config ──────────────────────────────────────────────────────────────────

PROCESSED_DIR = Path(__file__).parent.parent.parent / "oxdata" / "data" / "qualitative" / "processed"
OUTPUT_DIR    = Path(__file__).parent.parent.parent / "oxdata" / "data" / "qual_matrices"

OR_BASE_URL = "https://openrouter.ai/api/v1"
OR_API_KEY  = os.getenv("OPENROUTER_API_KEY", "")

FREE_MODELS = [
    "openai/gpt-oss-120b:free",        # 120B primary — best quality, same speed as 20B
    "openai/gpt-oss-20b:free",         # 20B fallback
    "moonshotai/kimi-k2.6:free",       # fallback
    "google/gemma-4-31b-it:free",      # last resort
]

MAX_WORKERS      = 5     # concurrent docs — no rate limit observed on this key
MAX_TRANSCRIPT_CHARS = 18_000  # ~4,500 words — fits all free model contexts

# ── OpenRouter client ────────────────────────────────────────────────────────

def get_client() -> OpenAI:
    return OpenAI(base_url=OR_BASE_URL, api_key=OR_API_KEY)


def _extract_json_from_text(text: str) -> Optional[dict]:
    """Try direct parse, then extract first {...} block from prose output."""
    text = text.strip()
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text.rstrip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find outermost {...} block
    start = text.find("{")
    if start == -1:
        return None
    depth, end = 0, -1
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def call_llm(client: OpenAI, prompt: str, doc_name: str, pass_name: str) -> Optional[dict]:
    """Try each free model in order. Handles 429 with backoff, extracts JSON from prose."""
    for model in FREE_MODELS:
        for attempt in range(2):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=4096,
                )
                raw = resp.choices[0].message.content or ""
                result = _extract_json_from_text(raw)
                if result is not None:
                    short = model.split("/")[-1]
                    print(f"    [{pass_name}] OK via {short}")
                    return result
                print(f"    [{pass_name}] JSON extract failed on {model} — trying next")
                break
            except Exception as e:
                err_str = str(e)
                # 429: skip to next model immediately — waiting 35s is too costly
                if "429" in err_str:
                    print(f"    [{pass_name}] 429 on {model} — skipping to next model")
                    break
                print(f"    [{pass_name}] Error on {model}: {err_str[:80]} — next model")
                time.sleep(1.0)
                break
    print(f"    [{pass_name}] ALL models failed for {doc_name}")
    return None


# ── Prompt ───────────────────────────────────────────────────────────────────

COMBINED_PROMPT = """You are a qualitative research analyst processing an In-Depth Interview (IDI) from an Indian electrical appliances market research study.
Study: Mixer grinders, food processors, ceiling fans, air coolers. Respondents are middle-class Indian household decision-makers across Chennai, Mumbai, Pune, Delhi, Ludhiana, Lucknow, Bhubaneswar, Kochi, Bangalore, Indore.

Read the complete transcript. Extract ALL dimensions below into ONE JSON object.
Return ONLY valid JSON. No markdown fences. No explanation. Raw JSON only.

{{
  "respondent": {{
    "city": "city from transcript context",
    "brand_owned": "primary brand discussed in depth",
    "journey_stage": "loyal_user | recent_buyer | intender | lapsed | aware_only",
    "usage_context": "homemaker | working_professional | business_cook | mixed",
    "household_size": "small_1_3 | medium_4_6 | large_7_plus"
  }},
  "brand_mentions": [
    {{"brand": "name", "sentiment": "positive|negative|neutral|mixed",
      "attributes_mentioned": ["grinding_texture","jar_quality","noise"],
      "verbatim_quote": "direct quote"}}
  ],
  "pain_points": [
    {{"severity": "high|medium|low", "issue_description": "specific problem",
      "product_area": "motor|jar|blade|noise|service|price|dough|texture|cleaning|other",
      "workaround_used": "what they do instead or null",
      "verbatim_quote": "direct quote"}}
  ],
  "jobs_to_be_done": [
    "concrete specific job — e.g. grind 3kg ginger-garlic paste for bulk biryani — never generic"
  ],
  "decision_drivers": [
    {{"trigger": "what caused purchase", "info_source": "YouTube|dealer|friend|family|online_research|TV_ad|other",
      "key_reason": "why they chose this brand"}}
  ],
  "feature_priorities": [
    {{"feature": "specific feature", "importance": "high|medium|low", "verbatim_quote": "quote"}}
  ],
  "service_mentions": [
    {{"type": "repair|AMC|dealer_support|warranty", "sentiment": "positive|negative|neutral", "verbatim_quote": "quote"}}
  ],
  "nps_signal": "promoter|passive|detractor|unclear",
  "price_sensitivity": "high|medium|low|not_mentioned",
  "cultural_context": {{
    "regional_food_culture": "south_indian|north_indian|western_india|east_india|mixed",
    "primary_use_cases": ["idly_batter","ginger_garlic_paste","chapati_dough","bulk_biryani"],
    "event_cooking": false,
    "time_pressure_type": "morning_rush|working_woman_schedule|business_volume|none",
    "traditional_vs_modern": "traditional_lean|modern_lean|mixed",
    "family_food_values": "health_first|convenience_first|taste_first|mixed"
  }},
  "brand_relationship": {{
    "relationship_stage": "honeymoon|settled_satisfied|resigned_acceptance|strained|at_risk|churned_mentally",
    "loyalty_depth": "surface|functional|emotional|identity",
    "switching_consideration": false,
    "switching_trigger": "what would make them switch or null",
    "advocacy_likelihood": "will_recommend|might_recommend|wont_recommend",
    "unresolved_tension": "the one thing still bothering them",
    "relationship_verbatim": "best single quote capturing their relationship with the brand"
  }},
  "all_passages": [
    {{"content": "verbatim respondent quote min 25 words — INCLUDE AT LEAST 10 ENTRIES",
      "sentiment": "positive|negative|neutral",
      "topic": "grinding_texture|dough_kneading|jar_quality|noise|purchase_decision|brand_trust|cooking_habits|time_saving|service|price|family_dynamics|aspirations|workaround|product_performance|cleaning|social_influence",
      "pain_point": false, "decision_signal": false, "brand_mentioned": null}}
  ],
  "emotional_arc": [
    {{"moment": "early|mid|late", "emotion": "frustration|delight|resignation|pride|anxiety|surprise|ambivalence|relief|confusion|overwhelm",
      "arousal_intensity": 5, "valence": "positive|negative",
      "trigger": "specific trigger", "attribution": "self|product|brand|dealer|family|situation",
      "verbatim_quote": "quote showing emotion — INCLUDE MINIMUM 5 ENTRIES"}}
  ],
  "peak_emotional_moment": {{
    "emotion": "emotion name", "trigger": "what caused it", "verbatim_quote": "the quote"
  }},
  "emotional_resolution": "positive|negative|neutral",
  "self_blame_instances": ["exact quote where respondent blames themselves"],
  "product_blame_instances": ["exact quote where respondent blames product or brand"],
  "identity_signals": {{
    "cook_self_image": "skilled|competent|struggling|efficient|creative|burdened|mixed",
    "kitchen_role": "primary_caregiver|working_professional|business_cook|shared_responsibility",
    "pride_moments": [{{"trigger": "what made them proud", "verbatim_quote": "quote"}}],
    "inadequacy_moments": [{{"trigger": "what made them feel inadequate", "verbatim_quote": "quote"}}],
    "appliance_as_identity": "high|medium|low",
    "competence_framing": "quote showing how they see themselves as a cook"
  }},
  "social_dynamics": {{
    "household_type": "nuclear|joint|extended",
    "purchase_decision_maker": "self|husband|joint|mother_in_law|other",
    "influencer_network": [
      {{"relation": "friend|colleague|dealer|YouTube|family|social_media|neighbour",
        "influence_type": "recommendation|warning|demonstration|gifted|observed",
        "brand_mentioned": null, "verbatim_quote": "quote"}}
    ],
    "cooking_labor": "solo|shared|helped_by_family|helped_by_domestic_help",
    "generational_knowledge": false,
    "social_proof_reliance": "high|medium|low"
  }},
  "aspiration_reality_gaps": [
    {{"aspiration": "what they wish the product could do — specific",
      "current_reality": "what actually happens",
      "workaround": "invented solution or null",
      "emotional_charge": "resignation|frustration|acceptance|hope|silent_suffering",
      "commercial_opportunity": "high|medium|low",
      "verbatim_quote": "quote revealing the gap"}}
  ],
  "unspoken_needs": [
    "need inferred from workarounds or accepted inconveniences — never directly stated"
  ],
  "language_patterns": {{
    "hedging_phrases": ["exact uncertainty phrases like maybe, kind of"],
    "certainty_statements": ["exact conviction phrases like always, never"],
    "metaphors_used": ["figurative language"],
    "self_blame_phrases": ["exact self-blame phrases"],
    "product_blame_phrases": ["exact product-blame phrases"],
    "aspiration_markers": ["phrases like if only, I wish, would be better if"],
    "emotional_intensifiers": ["strong feeling words"]
  }}
}}

CRITICAL RULES:
- all_passages: include ALL significant respondent quotes — do not compress. More is better.
- topic: ALWAYS specific — the word general is forbidden
- emotional_arc: 3 to 8 entries covering start, middle, end of interview
- aspiration_reality_gaps: extract ALL you find, including things they accept as normal
- unspoken_needs: infer from workarounds and accepted difficulties — not from direct statements
- city: look in conversation context, not just filename

TRANSCRIPT:
{transcript}"""

# Keep PASS1_PROMPT and PASS2_PROMPT for reference only — no longer used in main flow
PASS1_PROMPT = """You are analyzing an In-Depth Interview (IDI) from an Indian electrical appliances market research study.
Study context: Mixer grinders, food processors, ceiling fans, air coolers. Respondents are middle-class Indian household decision-makers across cities like Chennai, Mumbai, Pune, Delhi, Ludhiana, Lucknow, Bhubaneswar, Kochi, Bangalore, Indore.

Read this complete transcript carefully. Extract the following as a valid JSON object.

Return ONLY valid JSON. No markdown fences. No explanation. Just the raw JSON object.

{{
  "respondent": {{
    "city": "city name extracted from transcript context",
    "brand_owned": "primary brand of appliance being discussed in depth",
    "journey_stage": "loyal_user | recent_buyer | intender | lapsed | aware_only",
    "usage_context": "homemaker | working_professional | business_cook | mixed",
    "household_size": "small_1_3 | medium_4_6 | large_7_plus"
  }},
  "brand_mentions": [
    {{
      "brand": "brand name",
      "sentiment": "positive | negative | neutral | mixed",
      "attributes_mentioned": ["specific attributes like grinding_texture, jar_quality, noise, motor_power"],
      "verbatim_quote": "direct quote from respondent about this brand"
    }}
  ],
  "pain_points": [
    {{
      "severity": "high | medium | low",
      "issue_description": "specific description of the problem",
      "product_area": "motor | jar | blade | noise | service | price | dough | texture | cleaning | other",
      "workaround_used": "what they do instead, or null",
      "verbatim_quote": "direct quote expressing this pain"
    }}
  ],
  "jobs_to_be_done": [
    "concrete job description — e.g. grind 3kg ginger-garlic paste for bulk biryani, NOT generic like use mixer grinder"
  ],
  "decision_drivers": [
    {{
      "trigger": "what caused the purchase decision",
      "info_source": "YouTube | dealer | friend | family | online_research | TV_ad | other",
      "key_reason": "primary reason they chose this brand or product"
    }}
  ],
  "feature_priorities": [
    {{
      "feature": "specific feature name",
      "importance": "high | medium | low",
      "verbatim_quote": "quote mentioning this feature"
    }}
  ],
  "service_mentions": [
    {{
      "type": "repair | AMC | dealer_support | warranty",
      "sentiment": "positive | negative | neutral",
      "verbatim_quote": "quote about service experience"
    }}
  ],
  "nps_signal": "promoter | passive | detractor | unclear",
  "price_sensitivity": "high | medium | low | not_mentioned",
  "cultural_context": {{
    "regional_food_culture": "south_indian | north_indian | western_india | east_india | mixed",
    "primary_use_cases": ["specific dishes or tasks — e.g. idly_batter, ginger_garlic_paste, chapati_dough, bulk_biryani"],
    "event_cooking": true,
    "time_pressure_type": "morning_rush | working_woman_schedule | business_volume | none",
    "traditional_vs_modern": "traditional_lean | modern_lean | mixed",
    "family_food_values": "health_first | convenience_first | taste_first | mixed"
  }},
  "brand_relationship": {{
    "relationship_stage": "honeymoon | settled_satisfied | resigned_acceptance | strained | at_risk | churned_mentally",
    "loyalty_depth": "surface | functional | emotional | identity",
    "switching_consideration": false,
    "switching_trigger": "what would make them switch, or null",
    "advocacy_likelihood": "will_recommend | might_recommend | wont_recommend",
    "unresolved_tension": "the one thing still bothering them about the product",
    "relationship_verbatim": "single best quote capturing their relationship with the brand"
  }},
  "all_passages": [
    {{
      "content": "verbatim respondent quote, minimum 25 words",
      "sentiment": "positive | negative | neutral",
      "topic": "grinding_texture | dough_kneading | jar_quality | noise | purchase_decision | brand_trust | cooking_habits | time_saving | service | price | family_dynamics | aspirations | workaround | product_performance | cleaning | social_influence",
      "pain_point": false,
      "decision_signal": false,
      "brand_mentioned": null
    }}
  ]
}}

RULES:
- all_passages: include ALL significant respondent quotes — do not compress or summarise. We keep the full record.
- topic: ALWAYS specific — never use the word general or other
- city: look in the conversation itself, not just the filename
- brand_owned: the brand discussed most deeply, not just mentioned
- event_cooking: true if they cook in bulk for events, business, or large gatherings

TRANSCRIPT:
{transcript}"""

PASS2_PROMPT = """You are a qualitative research analyst specializing in consumer psychology and behavioral economics.
You are analyzing an In-Depth Interview from an Indian electrical appliances study (mixer grinders, food processors).
Respondents are middle-class Indian women, primarily homemakers or working professionals.

Read this transcript and extract the EMOTIONAL and BEHAVIORAL layers — the human truth beneath the surface facts.

Return ONLY valid JSON. No markdown fences. No explanation. Raw JSON only.

{{
  "emotional_arc": [
    {{
      "moment": "early | mid | late",
      "emotion": "frustration | delight | resignation | pride | anxiety | surprise | ambivalence | relief | confusion | overwhelm",
      "arousal_intensity": 5,
      "valence": "positive | negative",
      "trigger": "specific trigger — what caused this emotion",
      "attribution": "self | product | brand | dealer | family | situation",
      "verbatim_quote": "quote showing this emotion"
    }}
  ],
  "peak_emotional_moment": {{
    "emotion": "emotion name",
    "trigger": "what caused it",
    "verbatim_quote": "the quote"
  }},
  "emotional_resolution": "positive | negative | neutral",
  "self_blame_instances": [
    "exact quote where respondent blames themselves for a product failure or difficulty"
  ],
  "product_blame_instances": [
    "exact quote where respondent blames the product or brand"
  ],
  "identity_signals": {{
    "cook_self_image": "skilled | competent | struggling | efficient | creative | burdened | mixed",
    "kitchen_role": "primary_caregiver | working_professional | business_cook | shared_responsibility",
    "pride_moments": [
      {{"trigger": "what made them proud", "verbatim_quote": "quote"}}
    ],
    "inadequacy_moments": [
      {{"trigger": "what made them feel inadequate or embarrassed", "verbatim_quote": "quote"}}
    ],
    "appliance_as_identity": "high | medium | low",
    "competence_framing": "direct quote that best captures how they see themselves as a cook"
  }},
  "social_dynamics": {{
    "household_type": "nuclear | joint | extended",
    "purchase_decision_maker": "self | husband | joint | mother_in_law | other",
    "influencer_network": [
      {{
        "relation": "friend | colleague | dealer | YouTube | family | social_media | neighbour",
        "influence_type": "recommendation | warning | demonstration | gifted | observed",
        "brand_mentioned": null,
        "verbatim_quote": "quote showing this influence"
      }}
    ],
    "cooking_labor": "solo | shared | helped_by_family | helped_by_domestic_help",
    "generational_knowledge": false,
    "social_proof_reliance": "high | medium | low"
  }},
  "aspiration_reality_gaps": [
    {{
      "aspiration": "what they wish existed or wish the product could do — be specific",
      "current_reality": "what actually happens now",
      "workaround": "what they do to cope — the invented solution",
      "emotional_charge": "resignation | frustration | acceptance | hope | silent_suffering",
      "commercial_opportunity": "high | medium | low",
      "verbatim_quote": "the quote revealing this gap"
    }}
  ],
  "unspoken_needs": [
    "needs described around but never stated directly — infer from workarounds, effort descriptions, accepted inconveniences"
  ],
  "language_patterns": {{
    "hedging_phrases": ["exact phrases showing uncertainty — maybe, kind of, I think, not sure"],
    "certainty_statements": ["exact phrases showing conviction — always, never, definitely, only"],
    "metaphors_used": ["figurative language or vivid comparisons they made"],
    "self_blame_phrases": ["exact phrases where they blame themselves"],
    "product_blame_phrases": ["exact phrases blaming the product"],
    "aspiration_markers": ["phrases like if only, I wish, would be better if, what I want is"],
    "emotional_intensifiers": ["strong feeling words — so much, terrible, love, hate, amazing, horrible"]
  }}
}}

QUANTITY RULES — the model must meet these minimums or the extraction is incomplete:
- all_passages: MINIMUM 10 passages. Every significant respondent statement is a passage. Do not stop at 5 or 6. A 4000-word interview has 60+ respondent turns — capture at least 10 of them verbatim.
- emotional_arc: MINIMUM 5 entries, up to 10. Do NOT stop at 3. Cover early, multiple mid-points, and late. Most interviews have 6-8 distinct emotional shifts.
- aspiration_reality_gaps: MINIMUM 3. Most transcripts reveal 4-6 gaps once you look at workarounds. A workaround = a gap. A resigned complaint = a gap. An "if only" = a gap.
- unspoken_needs: MINIMUM 3. These are inferred — look for workarounds described casually, things accepted as inevitably hard, effort described without complaint.
- pain_points: MINIMUM 2. Extract all distinct problems, not just the biggest one.

QUALITY RULES:
- topic in all_passages: ALWAYS specific — the word general is forbidden
- aspiration_reality_gaps: commercial_opportunity must be "high" if the gap appears in 3+ passages
- language_patterns: use exact quotes, not paraphrases
- self_blame_instances: include ANY instance where respondent attributes product failure to their own mistake

TRANSCRIPT:
{transcript}"""


# ── Filename metadata parsing ────────────────────────────────────────────────

BRAND_LIST = ["Bajaj", "Preethi", "Philips", "Usha", "Butterfly", "Prestige",
              "Havells", "Crompton", "Sujata", "Maharaja", "Hindware", "Orient", "V-Guard"]

CITY_LIST = ["Chennai", "Mumbai", "Delhi", "Pune", "Bangalore", "Ludhiana",
             "Lucknow", "Bhubaneswar", "Indore", "Kochi", "Hyderabad", "Kolkata",
             "Jaipur", "Ahmedabad"]

# Non-transcript filenames to skip — methodology docs, questionnaires, discussion guides
_SKIP_PATTERNS = [
    "discussion_guide", "questionnaire", "discussion guide",
    "moderator guide", "topic guide", "screener",
]

FAILURE_LOG = OUTPUT_DIR.parent / "qual_matrix_failures.json"

def parse_filename_metadata(filename: str) -> dict:
    name = filename.lower()
    brand = next((b for b in BRAND_LIST if b.lower() in name), None)
    city  = next((c for c in CITY_LIST if c.lower() in name), None)
    return {"filename_brand": brand, "filename_city": city}


def _is_non_transcript(filename: str, content: str) -> bool:
    """Return True if this file is clearly a methodology doc, not an IDI transcript."""
    name_low = filename.lower()
    if any(p in name_low for p in _SKIP_PATTERNS):
        return True
    # Check content: real transcripts have M:/R: dialogue turns
    dialogue_turns = len(re.findall(r'^\s*[MR]\s*:', content, re.MULTILINE))
    return dialogue_turns < 5


def _extract_raw_passages(raw_md: str) -> list:
    """
    Extract all respondent (R:) turns from raw MD transcript.
    This gives 100% retention of respondent voice — the LLM is lossy,
    this is deterministic. Each R: turn with >20 words becomes a passage.
    Returns list of {content, sentiment, topic, pain_point, decision_signal}.
    """
    from collections import Counter

    # Strip frontmatter
    text = re.sub(r"^---[\s\S]*?---\n", "", raw_md).strip()

    # Extract R: turns
    # Pattern: "R:" at start of line (possibly with spaces), capture until next M: or R: or EOF
    r_blocks = re.findall(
        r'(?:^|\n)\s*R\s*:\s*(.*?)(?=\n\s*[MR]\s*:|\Z)',
        text, re.DOTALL | re.MULTILINE
    )

    _POS_KW = {"good","great","best","excellent","love","happy","satisfied","smooth",
               "easy","fast","strong","reliable","trusted","perfect","worth","nice"}
    _NEG_KW = {"bad","poor","broken","loud","noisy","expensive","problem","issue",
               "difficult","hard","fail","broke","disappointed","repair","complaint"}
    _PAIN_KW = {"noise","broke","difficult","hard","problem","issue","repair","complaint",
                "fail","expensive","leak","stuck","messy","clean"}
    _DEC_KW  = {"bought","purchase","decided","choose","chose","switched","recommend",
                "buy","search","youtube","dealer","friend","online"}

    passages = []
    seen = set()

    for block in r_blocks:
        content = " ".join(block.strip().split())  # normalize whitespace
        if len(content.split()) < 15:
            continue
        if content in seen:
            continue
        seen.add(content)

        words = set(re.findall(r'\b\w+\b', content.lower()))
        pos_hits = len(words & _POS_KW)
        neg_hits = len(words & _NEG_KW)
        if pos_hits > neg_hits:   sentiment = "positive"
        elif neg_hits > pos_hits: sentiment = "negative"
        else:                     sentiment = "neutral"

        pain_point     = bool(words & _PAIN_KW)
        decision_signal = bool(words & _DEC_KW)

        # Topic: pick most specific matching keyword set
        topic = "cooking_habits"  # default — not "general"
        if words & {"price","cost","expensive","cheap","affordable","money"}:
            topic = "price"
        elif words & {"noise","loud","noisy","sound","quiet"}:
            topic = "noise"
        elif words & {"clean","wash","rinse","scrub"}:
            topic = "cleaning"
        elif words & {"dough","knead","chapati","atta"}:
            topic = "dough_kneading"
        elif words & {"grind","paste","texture","batter","idly","smooth"}:
            topic = "grinding_texture"
        elif words & {"jar","leak","lid","seal","cap"}:
            topic = "jar_quality"
        elif words & {"motor","power","watt","burn","heat","smoke"}:
            topic = "product_performance"
        elif words & {"warranty","service","repair","technician","amc"}:
            topic = "service"
        elif words & {"brand","trust","famous","reliable","known","popular"}:
            topic = "brand_trust"
        elif words & {"buy","bought","decided","choose","youtube","dealer","friend","search"}:
            topic = "purchase_decision"
        elif words & {"if","wish","want","better","could","should","would"}:
            topic = "aspirations"
        elif words & {"used","doing","instead","other","different"}:
            topic = "workaround"
        elif words & {"family","husband","mother","children","kids","home"}:
            topic = "family_dynamics"

        passages.append({
            "content":        content,
            "sentiment":      sentiment,
            "topic":          topic,
            "pain_point":     pain_point,
            "decision_signal": decision_signal,
            "brand_mentioned": None,
            "_source":        "raw",   # flag: extracted from raw MD, not LLM
        })

    return passages


def _merge_passages(llm_passages: list, raw_passages: list) -> list:
    """
    Merge LLM-extracted passages (rich metadata) with raw-extracted passages (complete).
    LLM passages take priority. Raw passages fill the gap.
    Deduplication by content similarity (skip raw if it's substantially covered by an LLM passage).
    """
    merged = []
    seen_words: list = []

    # LLM passages first (they have better metadata)
    for p in llm_passages:
        c = (p.get("content") or "").lower()
        if not c or len(c.split()) < 10:
            continue
        merged.append(p)
        seen_words.append(set(c.split()))

    def _is_duplicate(content: str) -> bool:
        cwords = set(content.lower().split())
        if len(cwords) < 10:
            return True
        for sw in seen_words:
            overlap = len(cwords & sw) / max(len(cwords), 1)
            if overlap > 0.65:  # 65% word overlap = same passage
                return True
        return False

    # Add raw passages not already covered
    for p in raw_passages:
        content = p.get("content", "")
        if not content or _is_duplicate(content):
            continue
        merged.append(p)
        seen_words.append(set(content.lower().split()))

    return merged


def _validate_matrix(matrix: dict) -> list:
    """Return list of quality warnings for a matrix."""
    warnings = []
    if len(matrix.get("all_passages", [])) < 8:
        warnings.append(f"low_passages:{len(matrix.get('all_passages',[]))}")
    if not matrix.get("pain_points"):
        warnings.append("no_pain_points")
    if not matrix.get("aspiration_reality_gaps"):
        warnings.append("no_gaps")
    if len(matrix.get("emotional_arc", [])) < 2:
        warnings.append(f"low_emo_arc:{len(matrix.get('emotional_arc',[]))}")
    if not matrix.get("respondent", {}).get("city"):
        warnings.append("no_city")
    return warnings


def _log_failure(doc_id: str, reason: str):
    """Append to failure log JSON."""
    try:
        FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        log = {}
        if FAILURE_LOG.exists():
            try:
                log = json.loads(FAILURE_LOG.read_text(encoding="utf-8"))
            except Exception:
                log = {}
        log[doc_id] = {"reason": reason, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
        FAILURE_LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Core builder ─────────────────────────────────────────────────────────────

def build_matrix_for_doc(md_path: Path, client: OpenAI) -> Optional[dict]:
    """Run combined LLM extraction + raw passage supplement. Return matrix or None."""
    doc_id = md_path.stem
    print(f"\n  Processing: {md_path.name}")

    with open(md_path, encoding="utf-8") as f:
        raw = f.read()

    # Detect and skip non-transcript files
    if _is_non_transcript(md_path.name, raw):
        print(f"    SKIP (not a transcript): {md_path.name}")
        _log_failure(doc_id, "not_a_transcript")
        return None

    # Strip YAML frontmatter
    transcript = re.sub(r"^---[\s\S]*?---\n", "", raw).strip()
    # Strip profile/header block before first M: line
    transcript = re.sub(
        r"(?s)^.*?(?=\nM\s*:|^M\s*:)", "", transcript, count=1, flags=re.MULTILINE,
    ).strip()
    if not transcript:
        transcript = re.sub(r"^---[\s\S]*?---\n", "", raw).strip()
    transcript = transcript[:MAX_TRANSCRIPT_CHARS]

    fn_meta = parse_filename_metadata(md_path.name)

    # ── Raw passage extraction (deterministic, 100% retention) ───────────────
    raw_passages = _extract_raw_passages(raw)

    # ── LLM combined extraction ───────────────────────────────────────────────
    combined_prompt = COMBINED_PROMPT.format(transcript=transcript)
    result = call_llm(client, combined_prompt, doc_id, "combined")
    if result is None:
        _log_failure(doc_id, "llm_failed_all_models")
        print(f"    PARTIAL — LLM failed, using raw passages only for {doc_id}")
        # Build minimal matrix from raw passages + no LLM fields
        result = {}

    # ── Merge LLM passages with raw (fills the 15% retention gap) ────────────
    llm_passages  = result.get("all_passages", [])
    merged_passages = _merge_passages(llm_passages, raw_passages)

    # ── Build matrix ──────────────────────────────────────────────────────────
    matrix = {
        "doc_id": doc_id,
        "filename": md_path.name,
        "word_count": len(raw.split()),
        "filename_meta": fn_meta,
        "respondent":              result.get("respondent", {}),
        "brand_mentions":          result.get("brand_mentions", []),
        "pain_points":             result.get("pain_points", []),
        "jobs_to_be_done":         result.get("jobs_to_be_done", []),
        "decision_drivers":        result.get("decision_drivers", []),
        "feature_priorities":      result.get("feature_priorities", []),
        "service_mentions":        result.get("service_mentions", []),
        "nps_signal":              result.get("nps_signal", "unclear"),
        "price_sensitivity":       result.get("price_sensitivity", "not_mentioned"),
        "cultural_context":        result.get("cultural_context", {}),
        "brand_relationship":      result.get("brand_relationship", {}),
        "all_passages":            merged_passages,
        "emotional_arc":           result.get("emotional_arc", []),
        "peak_emotional_moment":   result.get("peak_emotional_moment", {}),
        "emotional_resolution":    result.get("emotional_resolution", "neutral"),
        "self_blame_instances":    result.get("self_blame_instances", []),
        "product_blame_instances": result.get("product_blame_instances", []),
        "identity_signals":        result.get("identity_signals", {}),
        "social_dynamics":         result.get("social_dynamics", {}),
        "aspiration_reality_gaps": result.get("aspiration_reality_gaps", []),
        "unspoken_needs":          result.get("unspoken_needs", []),
        "language_patterns":       result.get("language_patterns", {}),
        "_quality_warnings":       [],
    }

    # ── Validate output ────────────────────────────────────────────────────────
    warnings = _validate_matrix(matrix)
    matrix["_quality_warnings"] = warnings
    if warnings:
        print(f"    WARNINGS: {', '.join(warnings)}")

    # Resolve city and brand:
    # Filename metadata is authoritative for city and primary brand —
    # LLM sometimes picks a minor mention or returns "unknown".
    resp = matrix["respondent"]
    _null_values = {None, "", "unknown", "null", "n/a", "not mentioned"}

    llm_city = (resp.get("city") or "").lower()
    fn_city  = fn_meta.get("filename_city")
    if fn_city:
        resp["city"] = fn_city  # filename always wins for city
    elif llm_city in _null_values:
        resp["city"] = None

    llm_brand = resp.get("brand_owned") or ""
    fn_brand  = fn_meta.get("filename_brand")
    known_brands_lower = {b.lower() for b in BRAND_LIST}
    if fn_brand:
        resp["brand_owned"] = fn_brand  # filename always wins for primary brand
    elif llm_brand.lower() not in known_brands_lower:
        resp["brand_owned"] = None  # LLM returned unknown brand name — discard

    return matrix


def _process_one(md_path: Path, force: bool, counter: dict, lock: threading.Lock) -> str:
    """Worker fn: build + save matrix for one doc. Returns status string."""
    out_path = OUTPUT_DIR / (md_path.stem + "_matrix.json")

    if out_path.exists() and not force:
        with lock:
            counter["skip"] += 1
            done = counter["skip"] + counter["ok"] + counter["fail"]
            total = counter["total"]
            print(f"  [{done}/{total}] SKIP: {md_path.name[:55]}")
        return "skip"

    # Each thread gets its own client — OpenAI client is not thread-safe to share
    client = get_client()
    matrix = build_matrix_for_doc(md_path, client)

    if matrix:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(matrix, f, indent=2, ensure_ascii=False)
        passages = len(matrix.get("all_passages", []))
        pain     = len(matrix.get("pain_points", []))
        gaps     = len(matrix.get("aspiration_reality_gaps", []))
        with lock:
            counter["ok"] += 1
            done = counter["skip"] + counter["ok"] + counter["fail"]
            total = counter["total"]
            print(f"  [{done}/{total}] SAVED: {md_path.stem[:45]} | pass={passages} pain={pain} gaps={gaps}")
        return "ok"
    else:
        with lock:
            counter["fail"] += 1
            done = counter["skip"] + counter["ok"] + counter["fail"]
            print(f"  [{done}/{counter['total']}] FAIL: {md_path.name[:55]}")
        return "fail"


def run(dry_run: bool = False, force: bool = False, workers: int = MAX_WORKERS):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    md_files = sorted(PROCESSED_DIR.glob("*_clean.md"))
    if not md_files:
        print(f"No *_clean.md files found in {PROCESSED_DIR}")
        return

    if dry_run:
        md_files = md_files[:10]
        print(f"DRY RUN — {len(md_files)} docs, {workers} workers")
    else:
        print(f"Processing {len(md_files)} docs with {workers} parallel workers")

    counter = {"ok": 0, "skip": 0, "fail": 0, "total": len(md_files)}
    lock    = threading.Lock()
    t0      = time.time()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_one, md_path, force, counter, lock): md_path
            for md_path in md_files
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                md_path = futures[future]
                print(f"  UNHANDLED ERROR {md_path.name}: {e}")
                with lock:
                    counter["fail"] += 1

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s ({elapsed/60:.1f}min) — "
          f"saved={counter['ok']} skipped={counter['skip']} failed={counter['fail']}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build qual matrices from IDI transcripts")
    parser.add_argument("--dry-run", action="store_true", help="Process first 10 docs only")
    parser.add_argument("--force",   action="store_true", help="Re-process even if matrix exists")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Parallel workers (default 5)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, force=args.force, workers=args.workers)
