"""
Central configuration for all LLM Models used across the OxData platform.
"""

# SAFE MODE CONFIGURATION (High Rate Limits)
SQL_MODEL = "llama-3.1-8b-instant"
SUMMARY_MODEL = "llama-3.1-8b-instant"
ROUTER_MODEL = "llama-3.1-8b-instant"
REASONING_FALLBACK = "gemini-1.5-flash"

# Legacy support
AGENT_MODEL = "llama-3.1-8b-instant"
FALLBACK_MODEL = "gemini-1.5-flash"
