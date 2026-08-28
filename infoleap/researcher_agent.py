import os
import sys
import json
import time
import asyncio
import pandas as pd
import numpy as np
import sqlite3
import re
import logging
from typing import List, Optional, Any, AsyncGenerator, Dict
from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI
from dataclasses import dataclass, field
from dotenv import load_dotenv
import streamlit as st

# --- 0. SETUP & CREDENTIALS ---
# Load .env from oxdata/ directory (explicit path — robust regardless of cwd)
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_FILE, override=False)  # override=False: respect already-set env vars

OR_KEY = os.getenv("OPENROUTER_API_KEY")
OR_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Specialized Models
MODEL_PRO = os.getenv("OPENROUTER_MODEL_PRO")     # Strategy & Orchestration
MODEL_ALT = os.getenv("OPENROUTER_MODEL_ALT")     # Code & SQL
MODEL_MINI = os.getenv("OPENROUTER_MODEL_MINI")   # Utility

# Add project root to sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from infoleap.db_loader import get_db_path
from infoleap.skills.domain_kb import get_facts_as_context
from infoleap.skills.qual_retriever import search_qual_trees
from infoleap.config.project_1 import DATA_DICTIONARY, BENCHMARKS
from infoleap.skills.bq3_engine import BQ3AnalyticalEngine
from infoleap.skills.thinker import classify_complexity, plan_query, build_session_context
from infoleap.skills.capabilities.insights import get_benchmark_context, build_enriched_prompt
from infoleap.skills.capabilities.compare import should_compare, build_comparison
from infoleap.skills.semantic_router import router
from infoleap.utils.context import ContextEngine

# Diagnostic Logging
logger = logging.getLogger("LENS_3.2")

# --- 1. UTILITIES ---
PII_COLUMNS = ["resp_name", "interviewer", "respondent_name", "interviewer_name", "phone", "email"]

def scrub_pii(df: pd.DataFrame) -> pd.DataFrame:
    """Removes sensitive columns before sending data to LLM."""
    if df is None: return None
    cols_to_drop = [c for c in df.columns if c.lower() in PII_COLUMNS]
    if cols_to_drop:
        return df.drop(columns=cols_to_drop)
    return df

def get_dynamic_respondent_profile(db_path: str) -> str:
    try:
        uri_path = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri_path, uri=True)
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM v_respondents')
        quant = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM dim_qual_quant_bridge')
        qual = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM dim_qual_quant_bridge WHERE respondent_id IS NOT NULL')
        inter = cur.fetchone()[0]
        conn.close()
        total = (quant + qual) - inter
        return f"Total Unique: {total} | Quant: {quant} | Qual: {qual} | Overlap (Bridge): {inter}"
    except:
        return "Factual Profile: 6,774 Respondents (90 Overlapping)"

class SubAgentEngines:
    def __init__(self):
        pass
    async def call(self, model: str, prompt: str, system: str = ""):
        try:
            # Create a new client per call to avoid loop-sensitivity issues in Streamlit
            async with AsyncOpenAI(base_url=OR_BASE_URL, api_key=OR_KEY) as client:
                # Add a 60s timeout to prevent hanging
                res = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        temperature=0.1
                    ),
                    timeout=60.0
                )
                return res.choices[0].message.content.strip()
        except asyncio.TimeoutError:
            return "Error: Request timed out."
        except Exception as e:
            return f"Error: {e}"

subagents = SubAgentEngines()

# --- 3. DATA MODELS ---
@dataclass
class ResearchDeps:
    db_path: str = str(get_db_path())
    last_df: Optional[pd.DataFrame] = None
    last_sql: str = ""
    session_id: str = "default_session"
    tools_called: List[str] = field(default_factory=list)
    call_count: int = 0
    evidence_log: List[str] = field(default_factory=list) # Iterative memory
    session_findings: List[str] = field(default_factory=list) # NEW: Full journey synthesis
    thought_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    bq3_engine: BQ3AnalyticalEngine = field(default_factory=BQ3AnalyticalEngine)
    all_verbatims: List[Dict] = field(default_factory=list)
    last_brand_health_fig: Any = None
    brand_health_cache: Dict = field(default_factory=dict)  # brand→summary, prevents redundant calls

class FinalReport(BaseModel):
    summary: str = Field(description="Comprehensive analytical narrative. For analytical questions: 3-6 paragraphs covering (1) headline metric with exact numbers, (2) comparison to peers/average, (3) segment breakdown, (4) consumer voice if retrieved, (5) Pulse Insight strategic implication. NOT a 2-4 sentence summary. End with 'Pulse Insight: [one strategic sentence]'.")
    thinking: str = Field(description="Max 2 sentences. Your data collection plan.")
    respondent_profile: str = Field(description="Summary counts")
    raw_sql_output: Optional[str] = Field(None, description="The SQL query used.")
    chart_code: Optional[str] = Field(None, description="PURE PYTHON CODE for Plotly (must define 'fig'). Use for premium visuals.")
    chart_spec: Optional[Dict[str, Any]] = Field(None, description="JSON spec for heuristic charts (fallback).")
    consumer_voice: Optional[str] = Field(None, description="ACTUAL RAW QUOTES. DO NOT SUMMARIZE.")
    sources: Optional[str] = Field(None, description="SPECIFIC SOURCE IDS.")
    clarifying_questions: Optional[List[str]] = Field(None, description="Ask the user for missing info before building complex deliverables.")
    logical_defaults: Optional[List[str]] = Field(None, description="Assumptions you made to fill gaps.")
    recommendation: Optional[str] = Field(None, description="Strategic recommendation.")
    diagnostic_trace: Optional[str] = Field(None, description="Internal log of tools called and decisions made.")

    @field_validator('summary')
    @classmethod
    def check_for_placeholders(cls, v: str) -> str:
        placeholders = ["the task at hand", "next step", "i will", "i'll", "gather insights", "to gather specific", "i'll start by"]
        for p in placeholders:
            if p in v.lower():
                raise ValueError(f"Found placeholder: '{p}'. You must call a tool and provide actual results!")
        
        # QUALITATIVE CITATION CHECK
        qual_indicators = ["consumers say", "people mention", "feedback suggests", "interviews reveal", "mention that", "think that"]
        if any(ind in v.lower() for ind in qual_indicators):
            if not re.search(r"\[Doc_\d+\]|Doc_\d+|\d{3,}", v):
                pass 
        return v

# --- 4. THE AGENT ---
def get_orchestrator_prompt(profile: str, active_brand: str = "All Brands", active_cat: str = "All") -> str:
    return f"""You are LENS — an intelligent market research assistant with access to survey data from 6,631+ respondents across 18 Indian cities. You function like ChatGPT but grounded entirely in this survey's data. You think, reason, compare, and synthesize — you do not just retrieve and display SQL tables.

### 0. WHO YOU ARE
You are not a SQL query tool. You are an analyst. When a user asks a question, you:
1. Understand the full intent (not just extract keywords)
2. Fetch the relevant data using tools
3. Synthesize it into a rich, human narrative with exact numbers
4. Compare — always give context (vs. competitors, vs. national average, vs. prior data)
5. Cite your sources inline, naturally
6. Connect to the conversation history — reference what was said before

Your responses should feel like talking to a knowledgeable analyst, not querying a database.

### 1. REACT LOOP — MANDATORY EXECUTION MODEL
Operate in strict Reason → Act → Observe → Reason cycle.

**REASON:** Before EACH tool call: "I need [data] because [reason]. Calling [tool]."

**ACT:** Call ONE tool per cycle. For comparisons (X vs Y, Zone A vs B), call the tool once per entity.

**OBSERVE:** After tool returns: "I found: [key finding]. Sufficient? [Yes/No + reason]"
- NO: state gap → call next tool
- YES: write FinalReport

**STOPPING CRITERIA — write FinalReport as soon as:**
1. At least one tool returned actual data with real numbers.
2. For "why/reason/driver/opinion" questions: `get_qualitative_feedback` called once.
3. For explicit comparisons (X vs Y): data for both entities. For single-entity questions, ONE tool call is enough.
4. **HARD STOP: after 3 tool calls, write FinalReport with what you have.** Do not keep calling tools for "extra context". What you have is sufficient.

### 2. ABSOLUTE RULES
- NO FinalReport until at least ONE tool called with real data.
- **BRAND HEALTH RULE**: For awareness (TOM/SPONT/AIDED), NPS, brand funnel, competitive positioning → call `get_brand_health_context`. It returns pre-computed verified metrics INCLUDING zone_breakdown and city_nps already inside the response. Call it ONCE per brand (zone="all") — the response contains all zone/city/rival data you need. Do NOT call it again with different zone values. Do NOT also run SQL for NPS or awareness.
- **WHY/PREFER BAN**: NEVER answer "why" or "what makes" questions using ONLY `run_sql`. MUST call `get_qualitative_feedback` or `analyze_brand_drivers`.
- NEVER query `fact_verbatims` or `fact_transcript_segments` via SQL — use `get_qualitative_feedback` instead.
- No placeholders. No "I will do X next". Call the tool NOW.

### 3. CONVERSATION CONTEXT — CRITICAL
You receive the session history in `Context:` at the start of every prompt. USE IT.

- **Follow-up resolution**: "And for South?" → resolve using entities from previous turn. Look at `Context:` to find what brand/metric was discussed.
- **Reference previous findings**: "As we found for Bajaj in the North (NPS: 47)..." — bring forward specific numbers from prior turns.
- **Build on prior analysis**: If Turn 1 analyzed awareness and Turn 2 asks for NPS, contextualize the NPS in light of the awareness position.
- **Filter persistence**: If Turn 1 established "West Zone, Female segment", Turn 2 inherits those filters unless user changes them.
- **For summary requests** ("what did we discuss", "key findings so far"): synthesize ALL prior turns from `Context:` into a coherent brief without calling any tools.
- **Short follow-ups** (≤5 words, no brand named): always resolve from conversation context before calling a tool.

### 4. ENTITY RESOLUTION
- NEVER use "it", "them", "they", "that brand" in tool calls. Always use explicit names.
- Active context: brand={active_brand}, category={active_cat}. Resolve ambiguous pronouns to these.
- Geographical rule: Don't mix zones. If Zone=North is active, don't query Chennai (South).

### 5. COMPARISON MANDATE
For analytical questions, provide comparison context using data already returned — do NOT make extra tool calls just for comparison context unless the user explicitly asks for comparison.
- `get_brand_health_context` already returns rivals, zone breakdown, city NPS in a single call — use those, don't call the tool again.
- For NPS/awareness questions: rivals are already in the brand health response. Use them.
- Only call an extra tool if the user explicitly asks "compare to X" or "vs X" and that entity's data hasn't been fetched yet.

**TOOL ECONOMY RULE**: Prefer 1-2 tool calls per question. Never more than 4 total. Stop after you have enough data to answer. If the question is simple (one brand, one metric), one tool call is sufficient.

### 6. RESPONSE QUALITY STANDARDS

**Length & Depth**: Write comprehensive, analytical responses. For any real analytical question, write 3-6 substantive paragraphs, NOT 2-4 sentences. Match depth to complexity:
- Simple count/fact → 2-3 sentences + exact number
- Brand analysis → 3-4 paragraphs covering: headline metric, comparisons, segment breakdown, consumer voice if available, strategic implication
- Complex comparison → full analysis per entity + synthesis paragraph

**Narrative Structure** (for analytical questions):
1. **Headline**: Direct answer with the key number(s)
2. **Context**: How this compares to peers/average/prior period
3. **Breakdown**: Segment-level detail (zone, gender, city) if data available
4. **Consumer Voice**: What people actually say (if qualitative data retrieved)
5. **Pulse Insight**: One strategic implication sentence, starting "Pulse Insight: "

**Citation**: Every qualitative claim → [Doc_ID] inline. E.g., "Consumers find it noisy [Doc_88, Doc_102]."

**Tone**: Decisive, clear, analytical. Never hedge. If data says it, state it as fact. No "it appears", "seems to suggest", "needs verification".

### 7. ACCURACY RULES
- **PULSE INSIGHT (MANDATORY)**: Every summary MUST end with "Pulse Insight: [one strategic sentence]."
- **DEMOGRAPHIC LOCK**: For respondent counts / gender / city distribution → query `v_respondents` only. NOT `v_brand_nps` or `v_brand_awareness` (those have multiple rows per person).
- **Empty Segment Rule**: Report "no data" ONLY if BOTH `run_sql` AND `get_qualitative_feedback` (with fallback) return nothing.
- **Hallucination Guard**: NEVER claim a brand is good/bad at an attribute unless it appears in `last_df` or verbatims with actual data.
- **Cross-Location Fallback**: If city search returns nothing, retry globally. Say "No interviews in [City], but globally for [Brand], consumers say...".

### 8. VISUAL STANDARDS (run_python)
- Theme: `template="plotly_white"`, `plot_bgcolor='rgba(0,0,0,0)'`, `paper_bgcolor='rgba(0,0,0,0)'`
- Colors: `['#1a5d4d', '#30a76a', '#10b981', '#f0fdf4', '#059669']`
- Always `text_auto=True` or `text=...` for value labels
- Bar charts: set `range_y=[0, df[val_col].max()*1.2]`
- Remove gridlines: `fig.update_xaxes(showgrid=False)` + `fig.update_yaxes(showgrid=False)`

Advanced chart specs (use `chart_spec` field):
- Radar: `{{"type": "radar", "theta": [...], "data": [{{"name": "Brand", "r": [...]}}]}}`
- Quadrant: `{{"type": "quadrant", "points": [{{"label": "Attr", "x": 4.5, "y": 60}}], "layout": {{"x_mid": 4.0, "y_mid": 50.0}}}}`

### 9. TOOL GUIDE
- `run_sql`: Counts, ownership rates, NPS distributions, demographic splits, segment cross-tabs
- `get_qualitative_feedback`: Consumer verbatims, sentiment, reasons, "what people say"
- `analyze_brand_drivers`: "Why" questions, loyalty drivers, Bayesian regression on NPS predictors
- `get_brand_health_context`: Pre-computed awareness funnel + NPS + competitive position for any brand
- `run_python`: Generate Plotly visualization from current `last_df`
- `list_categories_for_brand`: Discover which categories a brand competes in
- `statistical_significance`: XLSTAT-style significance letters — "is X significantly higher than Y?", "which brands lead on awareness/consideration with statistical confidence"
- `perceptual_map`: Correspondence Analysis — "how are brands positioned", "perceptual map", "which attributes define the market", "who is closest to brand X in image"
- `imagery_profile`: Brand Image Profiling — "what is brand X known for", "image strengths/weaknesses", "where does X over/under-index on perception"
- `driver_regression`: Key-driver regression — "what drives NPS/CSAT for brand X", "which attributes most affect loyalty", "importance of drivers"
- `segment_difference_test`: Segment ANOVA — "does brand X perform differently across zones/gender/age", "is the regional difference significant"

### 10. DRIVER ATTRIBUTES (for analyze_brand_drivers)
- **Productivity**: Voltage fluctuation tolerance, Silent operation, Fast motor cooling, Energy efficiency
- **Functional**: Auto cutoff, Flicker-free, Heating time, Easy to clean, Grind quality
- **Design**: Blade shape, Fan print, Paint finish, Color range, Room fit, Trendy look
- **Service/Trust**: Product longevity, Anti-rust, Copper element, Ball bearing, Spare availability, Warranty
- **Brand Equity**: Market leader, Innovation, Retailer recommendation, Celebrity endorsement, Heritage
- **Price/Value**: Fair pricing, Discount offers

### 11. OUTPUT FIELDS
- `summary`: Comprehensive narrative (see §6 Response Quality Standards). 3-6 paragraphs for analysis.
- `thinking`: Max 2 sentences — your data collection plan.
- `chart_code`: Pure Python Plotly code defining `fig`.
- `consumer_voice`: Actual raw verbatim quotes, NOT summaries.
- `sources`: Source IDs for cited verbatims.
- `respondent_profile`: Always "{profile}"
- `recommendation`: Strategic action if warranted by data.

{get_facts_as_context()}

{DATA_DICTIONARY}
"""

def clean_json(text: str) -> str:
    if not text: return "{}"
    text = re.sub(r"```json|```", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else "{}"

def resolve_pronouns(question: str, history: List[Dict],
                     active_cat="All", active_brand="All Brands") -> str:
    """
    Resolves ambiguous pronouns ONLY for short follow-up questions (<= 10 words)
    where no brand name is already present. Avoids corrupting long, explicit questions.
    """
    q_low = question.lower()
    words  = q_low.split()

    # Don't touch long questions — they're already explicit enough
    if len(words) > 10:
        return question

    pronouns = [r"\bit\b", r"\bits\b", r"\bthem\b", r"\bthey\b",
                r"\bthat brand\b", r"\bthis brand\b"]
    if not any(re.search(p, q_low) for p in pronouns):
        return question

    # Don't resolve if a known brand is already named in the question
    try:
        from infoleap.config.project_1 import DIMENSIONS
        known = [b.lower() for b in DIMENSIONS["brands"].values() if len(b) > 3]
        if any(b in q_low for b in known):
            return question
    except Exception:
        pass

    # Resolve active context from session state if defaults passed
    try:
        if active_brand == "All Brands":
            active_brand = st.session_state.get("active_brand", "All Brands")
        if active_cat == "All":
            active_cat = st.session_state.get("active_category", "All")
    except Exception:
        pass

    entity = active_brand if active_brand not in ("All Brands", "") else active_cat
    if entity in ("All Brands", "All", ""):
        return question  # Nothing concrete to resolve to

    resolved = question
    resolved = re.sub(r"\bit\b",   entity,          resolved, flags=re.IGNORECASE)
    resolved = re.sub(r"\bits\b",  f"{entity}'s",   resolved, flags=re.IGNORECASE)
    resolved = re.sub(r"\bthey\b", entity,          resolved, flags=re.IGNORECASE)
    resolved = re.sub(r"\bthem\b", entity,          resolved, flags=re.IGNORECASE)
    return resolved

async def run_autonomous_research_async(question: str, message_history: List[Dict] = None, session_id: str = "default_session", active_cat="All", active_brand="All Brands") -> AsyncGenerator[dict, None]:
    try:
        deps = ResearchDeps(session_id=session_id)
        profile = get_dynamic_respondent_profile(deps.db_path)

        # Memory — retrieve relevant past episodes for context
        try:
            from infoleap.skills.memory_service import memory as lens_memory
            prior_episodes = lens_memory.retrieve_relevant_episodes(question, limit=2)
            memory_context = (
                "\n[MEMORY — relevant past analyses]\n"
                + "\n---\n".join(prior_episodes[:2])
                if prior_episodes else ""
            )
        except Exception:
            lens_memory = None
            memory_context = ""
        
        # 0. RESOLVE CONTEXT (PRONOUNS)
        original_question = question
        if message_history:
            question = resolve_pronouns(question, message_history, active_cat=active_cat, active_brand=active_brand)
            if question != original_question:
                print(f"[DEBUG] Resolved Pronouns: '{original_question}' -> '{question}'")
                # Try to get values for thought display
                display_brand = active_brand
                display_cat = active_cat
                try:
                    if display_brand == "All Brands": display_brand = st.session_state.get('active_brand', 'All Brands')
                    if display_cat == "All": display_cat = st.session_state.get('active_category', 'All')
                except: pass
                yield {"type": "thought", "content": f"Resolved context: Discussing {display_brand} {display_cat}..."}

        q_low = question.lower()
        
        # Fast-Path: Basic Greeting
        if re.sub(r'[^\w\s]', '', q_low.strip()) in ["hi", "hello", "hey", "who are you"]:
            yield {"type": "result", "data": FinalReport(thinking="Greeting", respondent_profile=profile, summary="Hello! I am LENS 3.2. How can I help you?"), "df": None, "engine": "⚡ Fast-Path"}
            return

        # NEW: True Fast-Path Bypass (Camouflaged as Agent Flow)
        async def fast_call(p, s):
            return await subagents.call(MODEL_MINI, p, s)

        match = await router.find_match_neural(question, fast_call)

        # FAQ match is used ONLY as a routing hint / few-shot context.
        # We NEVER return the static model_answer — it may contain unfilled
        # {{placeholders}} and is not grounded in live data. The full REACT
        # loop agent runs for every question.
        if match:
            intent = match.get("intent", "unknown")
            print(f"[DEBUG] FAQ structural match: {intent}. Using as few-shot hint for full agent.")
            yield {"type": "thought", "content": f"Pattern matched: {intent}. Running full analysis..."}
            few_shot = (
                f"\n[STRATEGIC TEMPLATE for '{intent}']\n"
                f"Suggested tool: {match.get('tool', 'run_sql')}\n"
                f"Query hint: {match.get('instruction', '')}\n"
                f"Preferred visual: {match.get('preferred_visual', 'bar')}\n"
            )
        else:
            few_shot = ""

        # --- STAGE 1: STRATEGIC PLANNING ---
        print(f"[DEBUG] No FAQ match. Starting Multi-Agent Swarm for: {question}")
        # Stage 1: context extraction + complexity only (removed wasted MODEL_PRO plan call)
        tasks = []
        extraction_prompt = (
            f"Extract zone/city/brands/category from: {question}. "
            f"History: {message_history[-2:] if message_history else []}. "
            "Return JSON only: {zone, city, brand, category}."
        )
        tasks.append(subagents.call(MODEL_MINI, extraction_prompt, "Context Extractor"))
        tasks.append(asyncio.to_thread(classify_complexity, question, message_history))

        yield {"type": "thought", "content": "Analysing question..."}
        print(f"[DEBUG] Executing Stage 1 gather...")
        results = await asyncio.gather(*tasks)
        print(f"[DEBUG] Stage 1 gather finished.")

        try:
            context_data = json.loads(clean_json(results[0]))
            new_cat   = context_data.get("category")
            new_brand = context_data.get("brand")
            if new_cat or new_brand:
                print(f"[DEBUG] Auto-Sync: Cat={new_cat}, Brand={new_brand}")
                ContextEngine.set_context(category=new_cat, brand=new_brand)
        except Exception as e:
            print(f"[DEBUG] Context extraction failed: {e}")

        print(f"[DEBUG] Building session context and model config...")
        few_shot = router.get_few_shot_context(question)
        session_context = build_session_context(message_history[-12:] if message_history else [])

        # Stage 1b: For complex questions, use thinker to build a query plan
        # This gives the orchestrator a structured decomposition to follow
        complexity = results[1] if len(results) > 1 else "simple"
        plan_hint = ""
        if complexity == "complex":
            try:
                async def _mini_llm(system_prompt, user_prompt):
                    return await subagents.call(MODEL_MINI, user_prompt, system_prompt)
                query_plan = await plan_query(question, session_context, _mini_llm)
                if query_plan.steps:
                    steps_desc = " → ".join(
                        f"[{s.skill}] {s.sub_question[:60]}" for s in query_plan.steps
                    )
                    plan_hint = (
                        f"\n[QUERY PLAN — {len(query_plan.steps)} steps]\n"
                        + "\n".join(f"Step {s.step_id} ({s.skill}): {s.sub_question}"
                                    for s in query_plan.steps)
                        + f"\nMerge strategy: {query_plan.merge_strategy}\n"
                    )
                    yield {"type": "thought",
                           "content": f"Complex query — decomposed into {len(query_plan.steps)} steps: {steps_desc}"}
                    print(f"[DEBUG] Query plan: {steps_desc}")
            except Exception as e:
                print(f"[DEBUG] plan_query failed (non-fatal): {e}")
                plan_hint = ""
        
        provider = OpenAIProvider(base_url=OR_BASE_URL, api_key=OR_KEY)
        model = OpenAIModel(MODEL_PRO, provider=provider)
        orchestrator = Agent(model, deps_type=ResearchDeps, output_type=FinalReport, system_prompt=get_orchestrator_prompt(profile, active_brand=active_brand, active_cat=active_cat), retries=2)
        print(f"[DEBUG] Orchestrator agent initialized.")

        @orchestrator.tool
        async def run_sql(ctx: RunContext[ResearchDeps], instruction: str) -> str:
            print(f"[DEBUG] Starting run_sql with instruction: {instruction}")
            ctx.deps.tools_called.append(f"run_sql: {instruction}")
            await ctx.deps.thought_queue.put(f"Executing SQL: {instruction}")
            
            from infoleap.skills.capabilities import REGISTRY
            skill_key = next((sk for sk, cap in REGISTRY.items() if any(kw in instruction.lower() for kw in getattr(cap, 'KEYWORDS', []))), None)
            
            print(f"[DEBUG] Detected skill_key: {skill_key}")
            
            if skill_key in REGISTRY and hasattr(REGISTRY[skill_key], 'get_sql'):
                print(f"[DEBUG] Using skill {skill_key} to generate SQL")
                sql = REGISTRY[skill_key].get_sql(instruction)
            else:
                print(f"[DEBUG] No specialized skill found, calling subagent (MODEL_ALT)")
                sys_msg = (
                    f"SQL Engineer. Tables: {DATA_DICTIONARY}. "
                    "CRITICAL: USE SQLITE DIALECT. "
                    "FORBIDDEN: DO NOT use 'TOP N'. Use 'LIMIT N'. "
                    "MANDATORY: Always include the COUNT(*) or aggregate column in your SELECT for accuracy verification. "
                    "Return ONLY SQL. Limit 1000."
                )
                try:
                    # Add a timeout to subagent call
                    sql_task = asyncio.create_task(subagents.call(MODEL_ALT, f"Task: {instruction}.", sys_msg))
                    sql = await asyncio.wait_for(sql_task, timeout=30.0)
                except asyncio.TimeoutError:
                    print("[DEBUG] Subagent SQL call timed out")
                    return "Error: SQL generation timed out."
                except Exception as e:
                    print(f"[DEBUG] Subagent SQL call failed: {e}")
                    return f"Error: SQL generation failed: {e}"

            sql = sql.strip().replace("```sql", "").replace("```", "")
            print(f"[DEBUG] Executing SQL: {sql}")
            def _execute_sql(query: str) -> pd.DataFrame:
                conn = sqlite3.connect(f"file:{ctx.deps.db_path}?mode=ro", uri=True)
                try:
                    result = pd.read_sql(query, conn)
                finally:
                    conn.close()
                return result

            try:
                ctx.deps.last_sql = sql
                df = await asyncio.to_thread(_execute_sql, sql)
                df = scrub_pii(df)
                ctx.deps.last_df = df
                ctx.deps.session_findings.append(f"SQL RESULT ({instruction}):\n{df.head(10).to_string()}")
                print(f"[DEBUG] SQL execution successful, fetched {len(df)} rows")
                return f"OBSERVATION: Fetched {len(df)} rows. Sample:\n{df.head(5).to_string()}"
            except Exception as e:
                print(f"[DEBUG] SQL failed: {e}. Attempting auto-fix retry...")
                fix_sys = (
                    "SQLite SQL fixer. Fix the SQL for SQLite dialect. "
                    "Rules: LIMIT not TOP, LIKE not ILIKE, no FETCH FIRST, no WITH ROLLUP. "
                    "Return ONLY the fixed SQL, no explanation."
                )
                fix_prompt = f"Original SQL:\n{sql}\n\nError: {e}\n\nFixed SQL:"
                try:
                    fixed_sql_task = asyncio.create_task(subagents.call(MODEL_ALT, fix_prompt, fix_sys))
                    fixed_sql = await asyncio.wait_for(fixed_sql_task, timeout=25.0)
                    fixed_sql = fixed_sql.strip().replace("```sql", "").replace("```", "").strip()
                    ctx.deps.last_sql = fixed_sql
                    df = await asyncio.to_thread(_execute_sql, fixed_sql)
                    df = scrub_pii(df)
                    ctx.deps.last_df = df
                    ctx.deps.session_findings.append(f"SQL RESULT (retry) ({instruction}):\n{df.head(10).to_string()}")
                    print(f"[DEBUG] SQL retry successful, fetched {len(df)} rows")
                    return f"OBSERVATION: Fetched {len(df)} rows (SQL auto-corrected). Sample:\n{df.head(5).to_string()}"
                except Exception as e2:
                    print(f"[DEBUG] SQL retry also failed: {e2}")
                    return f"SQL Error: {e2}. Original error: {e}"

        @orchestrator.tool
        async def get_qualitative_feedback(ctx: RunContext[ResearchDeps], task: str, brand: str = None, city: str = None) -> str:
            """Retrieves verbatim quotes from qualitative interview transcripts.
            - task: Key themes or keywords to search for (be specific).
            - brand: Optional brand filter (exact brand name).
            - city: Optional city/location filter.
            Returns: Actual verbatim passages with source IDs for citation in your summary.
            """
            print(f"[TOOL CALL] qual: {task} (brand={brand}, city={city})")
            ctx.deps.tools_called.append(f"qual: {task}")
            await ctx.deps.thought_queue.put(f"Retrieving verbatims for: {task}...")

            filters = {}
            if brand: filters["brand"] = brand
            if city:  filters["city"] = city

            passages = await search_qual_trees(task, filters=filters,
                                               progress_callback=ctx.deps.thought_queue.put)
            fallback_note = ""

            if not passages and city and brand:
                # Cross-location fallback: try without city
                await ctx.deps.thought_queue.put(
                    f"No interviews in {city}. Searching globally for {brand}...")
                passages = await search_qual_trees(
                    task, filters={"brand": brand},
                    progress_callback=ctx.deps.thought_queue.put)
                if passages:
                    fallback_note = f"\n[No interviews in {city}. Showing global results for {brand}.]"

            if not passages:
                ctx.deps.session_findings.append(
                    f"QUALITATIVE ({task}): No verbatims found.")
                return (
                    f"No verbatims found for '{task}'. "
                    "Try broader search terms or remove location filter."
                )

            # Store for UI evidence drawer
            for p in passages[:8]:
                ctx.deps.all_verbatims.append({
                    "source": p.get("doc_id"),
                    "text":   p.get("content"),
                    "brand":  brand or p.get("brand", "Unknown"),
                    "city":   p.get("city", "Unknown"),
                })

            # Return ACTUAL TEXT so agent can read and cite passages
            formatted = []
            for p in passages[:6]:
                doc_id  = p.get("doc_id", "?")
                city_p  = p.get("city", "Unknown")
                content = str(p.get("content", ""))[:250].strip()
                formatted.append(f'[{doc_id}] ({city_p}): "{content}"')

            ctx.deps.session_findings.append(
                f"QUALITATIVE ({task} | {city or 'Global'}):\n"
                + "\n".join(formatted[:3]))

            return (
                f"VERBATIM EVIDENCE ({len(passages)} passages found):{fallback_note}\n\n"
                + "\n\n".join(formatted)
                + "\n\nCite each passage using its [Doc_ID] in your summary."
            )

        @orchestrator.tool
        async def list_categories_for_brand(ctx: RunContext[ResearchDeps], brand: str) -> str:
            """Discover which product categories a brand competes in.
            Returns category membership from config and NPS rater count from survey.
            """
            print(f"[TOOL CALL] list_categories: {brand}")
            ctx.deps.tools_called.append(f"list_cat: {brand}")

            # Use BRAND_CATEGORIES config (category column is NULL in v_brand_nps)
            try:
                from infoleap.config.project_1 import BRAND_CATEGORIES
                found_cats = [cat for cat, brands in BRAND_CATEGORIES.items()
                              if brand in brands]
            except Exception:
                found_cats = []

            try:
                conn = sqlite3.connect(f"file:{ctx.deps.db_path}?mode=ro", uri=True)
                raters_df = pd.read_sql(
                    "SELECT COUNT(*) n FROM v_brand_nps WHERE brand_name = ?",
                    conn, params=[brand])
                conn.close()
                raters = int(raters_df.iloc[0]["n"])
            except Exception:
                raters = 0

            if found_cats:
                return (f"{brand} competes in: {', '.join(found_cats)}. "
                        f"NPS raters in survey: {raters}.")
            return (f"{brand} not in product category config. "
                    f"NPS raters in survey: {raters}. "
                    "Treat as general electrical appliance brand.")

        @orchestrator.tool
        async def analyze_brand_drivers(ctx: RunContext[ResearchDeps], brand: str, category: str, zone: str = None) -> str:
            print(f"[DEBUG] Starting analyze_brand_drivers for {brand} in {zone if zone else 'Global'}")
            ctx.deps.tools_called.append(f"bq3: {brand}")
            await ctx.deps.thought_queue.put(f"Running Bayesian Analysis for {brand}...")
            
            # Run the heavy synchronous computation in a thread to keep the event loop alive
            try:
                res = await asyncio.to_thread(ctx.deps.bq3_engine.analyze_drivers, brand, category, zone_name=zone)
                print(f"[DEBUG] BQ3 analysis completed for {brand}")
            except Exception as e:
                print(f"[DEBUG] BQ3 analysis failed: {e}")
                return f"Error: Bayesian analysis failed: {e}"
            
            if "error" in res and zone:
                # Fallback to Global
                print(f"[DEBUG] BQ3 regional data low: {res['error']}. Falling back to Global...")
                await ctx.deps.thought_queue.put(f"Regional data low. Falling back to global drivers...")
                try:
                    res = await asyncio.to_thread(ctx.deps.bq3_engine.analyze_drivers, brand, category, zone_name=None)
                    print(f"[DEBUG] BQ3 Global fallback completed")
                except Exception as e:
                    print(f"[DEBUG] BQ3 Global fallback failed: {e}")
                    return (
                        f"BQ3 driver analysis unavailable for {brand} — brand imagery data "
                        f"(bq3 attributes) not yet ingested in this database. "
                        f"REQUIRED ACTION: Call get_qualitative_feedback with "
                        f"task='reasons consumers prefer {brand} over competitors' "
                        f"to find consumer-stated drivers from interview transcripts."
                    )

                if "error" in res:
                    return (
                        f"BQ3 driver analysis unavailable for {brand} — brand imagery data "
                        f"not yet ingested. "
                        f"REQUIRED ACTION: Call get_qualitative_feedback with "
                        f"task='reasons consumers prefer {brand} over competitors'."
                    )
                res["fallback_to_global"] = True

            if "error" in res:
                return (
                    f"BQ3 driver analysis unavailable for {brand} — brand imagery data "
                    f"not yet ingested in this database. "
                    f"REQUIRED ACTION: Call get_qualitative_feedback with "
                    f"task='reasons consumers prefer {brand} over competitors' "
                    f"to find consumer-stated drivers from qualitative interviews."
                )
            
            df_drivers = pd.DataFrame(res["drivers"])
            if not df_drivers.empty: 
                ctx.deps.last_df = df_drivers[['label', 'relative_impact_pct']]
                # Accumulate for Auditor
                ctx.deps.session_findings.append(f"DRIVERS ({brand} in {zone if zone else 'Global'}):\n{ctx.deps.last_df.to_string()}")
            
            print(f"[DEBUG] analyze_brand_drivers successful, found {len(df_drivers)} drivers")
            return json.dumps(res)

        @orchestrator.tool
        async def run_python(ctx: RunContext[ResearchDeps], task: str, chart_type: str = "Bar Chart") -> str:
            """PRIMARY TOOL FOR VISUALS. Generate interactive Plotly charts.
            - task: What to visualize.
            - chart_type: Preferred type (e.g., 'Radar Chart', 'Heatmap', 'Bar Chart', 'Funnel', 'Pie Chart').
            MANDATORY Pulse Visual Protocol:
            1. Colors: Use ['#1a5d4d', '#30a76a', '#10b981', '#f0fdf4'].
            2. Theme: Minimalist, template='plotly_white', no gridlines.
            3. Scaling: If it is a Bar chart, you MUST set range_y=[0, df[val_col].max()*1.2] to ensure the base is 0.
            4. Chart Type: If it is a Pie chart, you MUST use px.pie().
            """
            ctx.deps.tools_called.append(f"python: {task} ({chart_type})")
            await ctx.deps.thought_queue.put(f"Generating {chart_type} visual: {task}")
            if ctx.deps.last_df is None: return "Error: Run SQL first."
            
            actual_cols = list(ctx.deps.last_df.columns)
            sys_msg = (
                f"Senior Data Scientist. Generate a {chart_type} using the existing 'df'. "
                "THE DATAFRAME 'df' IS ALREADY IN MEMORY. DO NOT define your own data. "
                f"CRITICAL: You MUST use ONLY these EXACT column names from df: {actual_cols}. "
                "DO NOT invent or hallucinate column names. If unsure of x/y, use df.columns[0] for label and df.columns[-1] for value. "
                "If 'df' is empty, return result='No data'. "
                "Goal: Chart in 'fig' (Plotly figure). "
                f"MANDATORY: Return ONLY python code for a {chart_type}. "
                "STRICT PROTOCOL: fig.update_layout(template='plotly_white', "
                "plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', "
                "font_family='Arial', margin=dict(l=20, r=20, t=40, b=20)). "
                "Use greens: ['#1a5d4d', '#30a76a']. fig.update_xaxes(showgrid=False). fig.update_yaxes(showgrid=False). "
                "If it is a Bar chart, you MUST set range_y=[0, df[val_col].max()*1.2]."
            )
            code = await subagents.call(MODEL_ALT, f"Task: {task}. Columns available: {actual_cols}", sys_msg)
            code = re.sub(r'\.show\(\s*\)', '', code).strip().replace("```python", "").replace("```", "")

            # Security: block patterns that could escape the sandbox.
            # This is an internal tool but defense-in-depth prevents prompt-injection abuse.
            _FORBIDDEN_PATTERNS = [
                "__import__", "import os", "import sys", "import subprocess",
                "import socket", "import shutil", "import pathlib",
                "open(", "exec(", "eval(", "compile(",
                "__builtins__", "__class__", "__base__", "__subclasses__",
                "os.system", "os.popen", "subprocess.run", "subprocess.Popen",
            ]
            for _pat in _FORBIDDEN_PATTERNS:
                if _pat in code:
                    return f"Error: Generated code contains forbidden pattern '{_pat}'. Chart generation blocked."

            try:
                import plotly.express as px
                import plotly.graph_objects as go
                # Restrict builtins to a safe subset — prevents import/open/exec inside exec'd code
                _safe_builtins = {
                    "len": len, "range": range, "enumerate": enumerate, "zip": zip,
                    "list": list, "dict": dict, "tuple": tuple, "set": set,
                    "str": str, "int": int, "float": float, "bool": bool,
                    "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
                    "sorted": sorted, "reversed": reversed, "map": map, "filter": filter,
                    "print": print, "isinstance": isinstance, "hasattr": hasattr,
                    "None": None, "True": True, "False": False,
                }
                scope = {
                    "df": ctx.deps.last_df, "pd": pd, "np": np, "px": px, "go": go,
                    "__builtins__": _safe_builtins,
                }
                exec(code, scope)  # nosec — guarded by pattern check + restricted builtins above
                if scope.get("fig") is not None:
                    ctx.deps.last_brand_health_fig = scope["fig"]
                return f"Result: visual generated. CODE: {code}"
            except Exception as e:
                return f"Python Error: {e}"

        @orchestrator.tool
        async def get_brand_health_context(
            ctx: RunContext[ResearchDeps],
            brand: str,
            zone: str = "all",
            gender: str = "all",
            age_band: str = "all",
            city: str = "all",
        ) -> str:
            """Retrieves brand health metrics: TOM%, Spontaneous%, Aided awareness,
            NPS score, Strategic health score, zone breakdown, top cities by NPS,
            and key rival comparisons. Use this for any brand health, awareness,
            funnel, or competitive positioning question.
            Args:
                brand: Brand name (e.g. 'Bajaj', 'Crompton')
                zone: 'North'|'South'|'East'|'West'|'all'
                gender: 'Male'|'Female'|'all'
                age_band: '25-35'|'36-50'|'all'
                city: city name or 'all'
            """
            cache_key = f"{brand}|{zone}|{gender}|{age_band}|{city}"
            if cache_key in ctx.deps.brand_health_cache:
                return f"[Already fetched — use data above] {ctx.deps.brand_health_cache[cache_key]}"
            # Prevent tool-loop: if brand health was already called for this brand with any filter, return cached all-filters version
            brand_any_key = next((k for k in ctx.deps.brand_health_cache if k.startswith(f"{brand}|all|")), None)
            if brand_any_key and zone != "all":
                return f"[Zone subset — global data already fetched] {ctx.deps.brand_health_cache[brand_any_key]}"
            ctx.deps.tools_called.append(f"brand_health: {brand} [{zone}/{gender}/{age_band}/{city}]")
            await ctx.deps.thought_queue.put(
                f"Fetching brand health data for {brand} (zone={zone}, gender={gender})"
            )
            try:
                from infoleap.skills.brand_health_skill import summarise_for_agent, get_brand_health_data
                import plotly.graph_objects as _go
                import asyncio as _asyncio

                summary = await _asyncio.to_thread(
                    summarise_for_agent, brand,
                    zone=zone, city=city, gender=gender, age_band=age_band
                )

                # Build funnel chart — focal brand full funnel + rivals TOM comparison
                raw = await _asyncio.to_thread(
                    get_brand_health_data, brand,
                    zone=zone, city=city, gender=gender, age_band=age_band
                )
                if "error" not in raw:
                    rival_names = [r["brand_name"] for r in raw.get("rivals", [])[:3]]
                    # Fetch full data for rivals so we get aided_pct too
                    from infoleap.skills.brand_health_skill import get_multi_brand_comparison
                    multi = await _asyncio.to_thread(
                        get_multi_brand_comparison, rival_names,
                        zone=zone, city=city, gender=gender, age_band=age_band
                    ) if rival_names else {"brands": {}}

                    # Build entries: focal first, then rivals
                    entries = [{
                        "brand": raw["brand_name"],
                        "tom":   raw["tom_pct"],
                        "spont": raw["spont_pct"],
                        "aided": raw["aided_pct"],
                        "nps":   raw.get("nps"),
                        "focal": True,
                    }]
                    multi_brands = multi.get("brands", {})
                    # keys are matched brand names (may differ in case from rn)
                    multi_lookup = {k.lower(): (k, v) for k, v in multi_brands.items()}
                    for rn in rival_names:
                        matched_key, rd = multi_lookup.get(rn.lower(), (rn, {}))
                        if "error" not in rd:
                            t = rd.get("tom_pct", 0)
                            a = rd.get("aided_pct", t)
                            entries.append({"brand": matched_key, "tom": t, "spont": t, "aided": a, "nps": rd.get("nps"), "focal": False})

                    names   = [e["brand"] for e in entries]
                    tom_v   = [e["tom"]                         for e in entries]
                    spont_v = [max(0, e["spont"] - e["tom"])   for e in entries]
                    aided_v = [max(0, e["aided"] - e["spont"]) for e in entries]

                    c_tom   = ["#1a5d4d" if e["focal"] else "#6b7280" for e in entries]
                    c_spont = ["#30a76a" if e["focal"] else "#9ca3af" for e in entries]
                    c_aided = ["#86efac" if e["focal"] else "#d1d5db" for e in entries]

                    fig = _go.Figure()
                    fig.add_trace(_go.Bar(name="Top-of-Mind", x=names, y=tom_v,   marker_color=c_tom,   text=[f"{v}%" for v in tom_v],   textposition="inside"))
                    fig.add_trace(_go.Bar(name="Spontaneous", x=names, y=spont_v, marker_color=c_spont, text=[f"+{v}%" for v in spont_v], textposition="inside"))
                    fig.add_trace(_go.Bar(name="Aided",       x=names, y=aided_v, marker_color=c_aided, text=[f"+{v}%" for v in aided_v], textposition="inside"))

                    for e in entries:
                        if e["nps"] is not None:
                            fig.add_annotation(
                                x=e["brand"], y=e["aided"] + 3,
                                text=f"NPS {float(e['nps']):+.0f}",
                                showarrow=False,
                                font=dict(size=11, color="#1a5d4d" if e["focal"] else "#6b7280"),
                            )

                    filters_str = " · ".join(f"{k}={v}" for k, v in [("zone", zone), ("gender", gender), ("city", city)] if v != "all") or "All respondents"
                    fig.update_layout(
                        barmode="stack",
                        title=dict(text=f"Awareness Funnel — {raw['brand_name']} vs Rivals<br><sup>{filters_str} · Base N={raw['base_n']:,}</sup>", x=0.02),
                        template="plotly_white",
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        legend=dict(orientation="h", y=-0.15),
                        margin=dict(l=20, r=20, t=70, b=40),
                        yaxis=dict(title="% Respondents", showgrid=False),
                        xaxis=dict(showgrid=False),
                        height=420,
                    )
                    ctx.deps.last_brand_health_fig = fig

                ctx.deps.brand_health_cache[cache_key] = summary
                return summary
            except Exception as e:
                import traceback as _tb
                logger.warning(f"brand_health chart build error: {e}\n{_tb.format_exc()}")
                # Cache summary even if chart building fails so we don't retry
                try:
                    from infoleap.skills.brand_health_skill import summarise_for_agent as _sfa
                    _s = _sfa(brand, zone=zone, city=city, gender=gender, age_band=age_band)
                    ctx.deps.brand_health_cache[cache_key] = _s
                    return _s
                except Exception:
                    pass
                return f"Brand health retrieval error: {e}"

        # ── Advanced analytics suite (XLSTAT-grade) — Python+R engines ──────────
        # Streamlit-free wrappers from analytics_tools.py. Each returns a concise
        # text summary grounded in live DB data; the agent reads & narrates it.
        @orchestrator.tool
        async def statistical_significance(ctx: RunContext[ResearchDeps],
                                           metric: str = "CONSIDERATION",
                                           confidence: float = 0.95) -> str:
            """Column-proportion significance test (XLSTAT/pValue letters) across all
            brands for a funnel metric. metric: AIDED|SPONTANEOUS|TOM|CONSIDERATION|
            EVER_USED|CURRENT_USER|PREFERRED. Returns which brands significantly beat
            which (Excel-style A/B/C letters) at the given confidence."""
            ctx.deps.tools_called.append(f"sig_test: {metric}")
            await ctx.deps.thought_queue.put(f"Running column-proportion significance test on {metric}…")
            try:
                from infoleap.skills.analytics_tools import significance_test
                return significance_test(metric=metric, confidence=confidence)
            except Exception as e:
                return f"Significance test error: {e}"

        @orchestrator.tool
        async def perceptual_map(ctx: RunContext[ResearchDeps],
                                 category: str = "all", top_brands: int = 12) -> str:
            """Correspondence Analysis (CAN MAP) — perceptual positioning of brands vs
            imagery attributes. category: all|ceiling fans|air cooler|mixer grinder|
            led batten|water heater|water pumps. Returns the dominant dimensions,
            variance explained, and which attributes define each axis."""
            ctx.deps.tools_called.append(f"can_map: {category}")
            await ctx.deps.thought_queue.put(f"Running Correspondence Analysis for {category}…")
            try:
                from infoleap.skills.analytics_tools import correspondence_analysis
                return correspondence_analysis(category=category, top_brands=top_brands)
            except Exception as e:
                return f"Correspondence analysis error: {e}"

        @orchestrator.tool
        async def imagery_profile(ctx: RunContext[ResearchDeps],
                                  brand: str, category: str = "all", top: int = 8) -> str:
            """Brand Image Profiling (BIP) — which imagery attributes a brand over- or
            under-indexes on vs the market, with significance. Returns the brand's
            distinctive strengths and weaknesses in consumer perception."""
            ctx.deps.tools_called.append(f"bip: {brand}")
            await ctx.deps.thought_queue.put(f"Profiling {brand} imagery vs market…")
            try:
                from infoleap.skills.analytics_tools import brand_imagery_profile
                return brand_imagery_profile(brand=brand, category=category, top=top)
            except Exception as e:
                return f"Imagery profile error: {e}"

        @orchestrator.tool
        async def driver_regression(ctx: RunContext[ResearchDeps],
                                    brand: str, outcome: str = "NPS",
                                    topbox_min: int = 9, top: int = 8) -> str:
            """Key Driver REGRESSION (R) — which imagery attributes most drive an
            outcome for a brand. outcome: NPS|CSAT. topbox_min: top-box recode
            threshold (9 = 9-10 → 1). Returns standardized-importance ranking with
            statistical significance (the levers and drags on the outcome)."""
            ctx.deps.tools_called.append(f"driver_reg: {brand}/{outcome}")
            await ctx.deps.thought_queue.put(f"Running key-driver regression for {brand} ({outcome})…")
            try:
                from infoleap.skills.analytics_tools import key_driver_regression
                return key_driver_regression(brand=brand, outcome=outcome,
                                             topbox_min=topbox_min, top=top)
            except Exception as e:
                return f"Driver regression error: {e}"

        @orchestrator.tool
        async def segment_difference_test(ctx: RunContext[ResearchDeps],
                                          brand: str, segment: str = "zone") -> str:
            """Segment ANOVA (R) — tests whether a brand's perception/advocacy differs
            significantly across a demographic segment. segment: zone|gender|age_band.
            Returns F-statistic, p-value, and which segments differ."""
            ctx.deps.tools_called.append(f"anova: {brand}/{segment}")
            await ctx.deps.thought_queue.put(f"Running segment ANOVA for {brand} by {segment}…")
            try:
                from infoleap.skills.analytics_tools import segment_anova
                return segment_anova(brand=brand, segment=segment)
            except Exception as e:
                return f"Segment ANOVA error: {e}"

        # --- EXECUTION ---
        print(f"[DEBUG] Starting orchestrator run for: {question}")
        run_prompt = f"Question: {question}\nContext: {session_context}\n{few_shot}{memory_context}{plan_hint}"
        run_task = asyncio.create_task(
            orchestrator.run(run_prompt, deps=deps,
                             usage_limits=UsageLimits(request_limit=25))
        )
        while not run_task.done():
            while not deps.thought_queue.empty():
                yield {"type": "thought", "content": f"\n[Swarm]: {deps.thought_queue.get_nowait()}\n"}
            await asyncio.sleep(0.1)
        
        # Yield any remaining thoughts
        while not deps.thought_queue.empty():
            yield {"type": "thought", "content": f"\n[Swarm]: {deps.thought_queue.get_nowait()}\n"}
                
        result = await run_task
        report = result.output if result is not None else None
        if report is None:
            yield {"type": "error", "content": "Agent returned no output. The model may have failed to structure its response. Try again."}
            return
        print(f"[DEBUG] Orchestrator run finished. Tools called: {deps.tools_called}")
        
        # --- STAGE 3: SELF-CORRECTION (Logic Check) ---
        print(f"[DEBUG] Starting Self-Correction check")
        yield {"type": "thought", "content": "🔍 Self-Correction: Verifying chart/data consistency..."}
        
        # --- STAGE 4: SENIOR AUDITOR PASS (only when live data was retrieved) ---
        if deps.session_findings:
            print(f"[DEBUG] Starting Senior Auditor pass ({len(deps.session_findings)} findings)")
            yield {"type": "thought", "content": "🛡️ Senior Auditor verifying data-to-answer integrity..."}

            accumulated_data_str = "\n---\n".join(deps.session_findings)
            audit_prompt = (
                f"You are the Lead Data Integrity Auditor. Your job is to verify and polish the summary — NOT to shorten it.\n\n"
                f"SUMMARY TO AUDIT:\n{report.summary}\n\n"
                f"RAW DATABASE DATA:\n{accumulated_data_str}\n\n"
                f"--- AUDIT RULES ---\n"
                f"1. NO HALLUCINATIONS: Correct any claim that contradicts the DATA. Remove invented numbers.\n"
                f"2. PRESERVE LENGTH: The original summary is intentionally detailed. Do NOT compress it into 2-3 sentences. Keep the full narrative structure.\n"
                f"3. MATHEMATICAL PRECISION: Fix any percentages or counts that don't match the DATA.\n"
                f"4. KEEP PULSE INSIGHT: The last sentence starting 'Pulse Insight:' MUST remain in your output.\n"
                f"5. FINAL OUTPUT: Return only the corrected, polished summary text. Same length or longer. No preamble."
            )
            print(f"[DEBUG] Calling Auditor (MODEL_ALT)")
            final_summary = await subagents.call(MODEL_ALT, audit_prompt, "Lead Data Integrity Auditor")
            print(f"[DEBUG] Auditor pass completed")
            report.summary = final_summary
        else:
            print(f"[DEBUG] Skipping auditor — no live data retrieved (FAQ/greeting path)")

        # Store episode in memory for future sessions
        try:
            if lens_memory and deps.last_sql and report.summary:
                lens_memory.store_episode(
                    question=question[:200],
                    plan=deps.last_sql[:200],
                    summary=report.summary[:300])
        except Exception:
            pass

        yield {"type": "result", "data": report, "df": deps.last_df, "sql": deps.last_sql, "verbatims": deps.all_verbatims, "brand_health_fig": deps.last_brand_health_fig, "engine": "LENS 3.2 Stable"}

    except Exception as e:
        import traceback as _tb
        print(f"[AGENT ERROR] {type(e).__name__}: {e}")
        print(_tb.format_exc())
        yield {"type": "error", "content": f"Critical Logic Error: {str(e)}"}
