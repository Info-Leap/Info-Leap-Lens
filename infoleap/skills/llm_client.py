"""
Shared LLM client for the qual pipeline (schema_generator, project_extractor, findings_generator).

Replaces the old per-file OpenRouter free-model rotation (proven flaky — free-tier slugs get
deprecated silently, requests get rate-limited with no budget to fall back on) with a single
paid OpenRouter model. Default: meta-llama/llama-3.3-70b-instruct — cost-effective, strong at
structured JSON + instruction-following, and compliant with the Tier-2 privacy rule (Groq/Gemini
OK, no OpenAI) in the user's global CLAUDE.md — OPENROUTER_MODEL_PRO/_ALT/_MINI are OpenAI models
and must not be used for this project's data.

Usage:
    from llm_client import call_llm
    text = call_llm([{"role": "user", "content": prompt}], max_tokens=4000, temp=0.2, json_mode=True)
"""
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_OXDATA_DIR = Path(__file__).resolve().parent.parent
load_dotenv(str(_OXDATA_DIR / ".env"), override=False)
load_dotenv(str(_OXDATA_DIR.parent / ".env"), override=False)

from openai import OpenAI

# Primary model — chosen from real gate_matrix-scored comparisons on karat-coindcx DI_1 across
# two rounds: deepseek-chat scored highest both times (100/100 then 81.8/100 on a thinner schema)
# and never dropped a hard verbatim, at the lowest $/M-token price of any model tested
# ($0.20 in / $0.80 out). gemini-2.5-flash (reasoning-enabled) is close behind (90.4 / 76.5) and
# ~1.5x faster — kept as first fallback for latency-sensitive calls. z-ai/glm-4.6, qwen3-235b-a22b,
# and moonshotai/kimi-k2 were also tried and rejected: empty responses, an OpenRouter-side huge-
# integer JSON bug, and missing structured-output support respectively — not code bugs on our end.
# Non-OpenAI (Tier-2 privacy rule). Override via QUAL_LLM_MODEL env var if needed.
MODEL = os.getenv("QUAL_LLM_MODEL", "deepseek/deepseek-chat")
_MAX_RETRIES = 3
_BACKOFF_BASE = 3

# Fallback chain — tried in order when the primary model exhausts its retries. All non-OpenAI
# (Tier-2 privacy rule), all real-tested (see above). deepseek-r1 (reasoning) was tested and
# EXCLUDED — it paraphrased verbatim quotes instead of copying them exactly (3/6 hard-verbatim
# drops) and was the slowest of every model tried (320s) — reasoning didn't help this task.
# MODEL is always tried first even if it isn't first in this list.
# Override via QUAL_LLM_FALLBACKS env var (comma-separated slugs) if needed.
FALLBACK_MODELS = [
    m.strip() for m in os.getenv(
        "QUAL_LLM_FALLBACKS",
        "deepseek/deepseek-chat,google/gemini-2.5-flash,qwen/qwen-2.5-72b-instruct,meta-llama/llama-3.3-70b-instruct",
    ).split(",") if m.strip()
]


class LLMCallError(RuntimeError):
    """Raised when the LLM call fails after all retries — callers must handle this explicitly,
    never silently swallow it into an empty string (that's how gap #9 happened last time)."""


# Reasoning-capable models on OpenRouter — these support the unified `reasoning` request param
# (extended thinking before the visible answer). Matched by substring against the model slug.
_REASONING_CAPABLE = (
    "deepseek-r1", "deepseek-reasoner", "gemini-2.5", "gemini-3", "qwq", "qwen3",
    "o1", "o3", "grok-3", "grok-4",
)

# Thinking-token budget added on TOP of the caller's max_tokens when reasoning is requested —
# reasoning tokens are billed/counted against the same completion budget on OpenRouter, so
# without this the model burns its whole max_tokens on internal thinking and returns empty/
# truncated content (observed live: gemini-2.5-flash returned nothing at max_tokens=2000).
_REASONING_TOKEN_BUDGET = {"low": 1000, "medium": 3000, "high": 6000}


def _supports_reasoning(mdl: str) -> bool:
    m = mdl.lower()
    return any(tag in m for tag in _REASONING_CAPABLE)


def _get_client() -> OpenAI:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise LLMCallError("OPENROUTER_API_KEY not set in oxdata/.env or repo-root .env")
    # Explicit timeout — the openai SDK defaults to 10 minutes with no timeout set, which means
    # a stalled request just hangs the whole Streamlit script run instead of failing fast and
    # retrying (observed live: one ui_config call stalled 4+ minutes with zero progress).
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, timeout=60.0)


def _call_one_model(client: OpenAI, mdl: str, messages: list[dict], max_tokens: int,
                     temp: float, json_mode: bool, reasoning_effort: str | None = None) -> str:
    """Retry loop against a single model. Raises LLMCallError if all retries on THIS model fail.

    reasoning_effort: "low" | "medium" | "high" — only applied if the model is reasoning-capable
    (_supports_reasoning). Adds OpenRouter's unified `reasoning.effort` param AND bumps max_tokens
    by that effort's thinking-token budget, so the model has room to think AND still answer.
    """
    effective_tokens = max_tokens
    use_reasoning = reasoning_effort and _supports_reasoning(mdl)
    if use_reasoning:
        effective_tokens = max_tokens + _REASONING_TOKEN_BUDGET.get(reasoning_effort, 3000)

    kwargs = dict(model=mdl, messages=messages, max_tokens=effective_tokens, temperature=temp)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if use_reasoning:
        kwargs["extra_body"] = {"reasoning": {"effort": reasoning_effort, "exclude": True}}

    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(**kwargs)
            text = (resp.choices[0].message.content or "").strip()
            if not text:
                raise LLMCallError(f"Empty response from {mdl}")
            return text
        except Exception as e:
            last_err = e
            err_str = str(e)
            # response_format not supported by this model — retry once without it
            if json_mode and ("response_format" in err_str or "json_object" in err_str) and "response_format" in kwargs:
                kwargs.pop("response_format", None)
                continue
            is_retryable = any(c in err_str for c in ("429", "500", "502", "503", "504", "rate"))
            if is_retryable and attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                print(f"    [{mdl}] retryable error, wait {wait}s (attempt {attempt+1}/{_MAX_RETRIES}): {err_str[:120]}")
                time.sleep(wait)
                continue
            break

    raise LLMCallError(f"[{mdl}] failed after {_MAX_RETRIES} attempts: {last_err}")


def call_llm(
    messages: list[dict],
    max_tokens: int = 4000,
    temp: float = 0.2,
    json_mode: bool = False,
    model: str | None = None,
    fallback: bool = True,
    reasoning_effort: str | None = None,
) -> str:
    """
    Tries `model` (or MODEL if unset). If that model exhausts its own retries AND
    `fallback=True` AND no explicit `model` was pinned by the caller, walks FALLBACK_MODELS
    in order until one succeeds. Raises LLMCallError only if every model in the chain fails —
    callers decide what to do next, nothing is hidden.

    Pass `model=` explicitly to pin a single model with no fallback (used by the model
    comparison harness in this file to test one model in isolation).

    reasoning_effort: "low" | "medium" | "high" — request extended thinking before the answer,
    on models that support it (ignored for non-reasoning models, so it's always safe to pass).
    """
    client = _get_client()

    if model:
        return _call_one_model(client, model, messages, max_tokens, temp, json_mode, reasoning_effort)

    chain = [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL] if fallback else [MODEL]
    last_err = None
    for i, mdl in enumerate(chain):
        try:
            return _call_one_model(client, mdl, messages, max_tokens, temp, json_mode, reasoning_effort)
        except LLMCallError as e:
            last_err = e
            if i < len(chain) - 1:
                print(f"    {mdl} exhausted retries, falling back to {chain[i+1]}")
            continue

    raise LLMCallError(f"All models in fallback chain failed. Last error: {last_err}")


def call_llm_safe(messages: list[dict], max_tokens: int = 4000, temp: float = 0.2,
                   json_mode: bool = False, model: str | None = None,
                   reasoning_effort: str | None = None) -> str:
    """Non-raising variant for call sites that still want the old 'return empty string on
    failure' behaviour — prefer call_llm() + explicit except in new code."""
    try:
        return call_llm(messages, max_tokens=max_tokens, temp=temp, json_mode=json_mode,
                         model=model, reasoning_effort=reasoning_effort)
    except LLMCallError as e:
        print(f"    LLM call failed: {e}", file=sys.stderr)
        return ""
