"""
Shared prompt templates for Quote Explorer's structured analysis flow.
One place for: (1) the options-menu prompt that lists what a user can deep-dive
into before any narrative is generated, (2) methodology notes, which are
plain deterministic strings — NEVER LLM-generated, because the formula behind
a number must not vary between runs the way prose can.
"""
from __future__ import annotations

_SIGNAL_DESCRIPTIONS = {
    "pain_points":       "Recurring product/service complaints extracted from interviews",
    "aspiration_gaps":   "What consumers wish existed but doesn't yet",
    "nps_trend":         "Promoter/detractor mix and what's driving it",
    "unspoken_needs":    "Needs inferred from workarounds, never stated directly",
    "blame_attribution": "Whether consumers blame themselves or the product when things go wrong",
}


def build_options_menu_prompt(entity: str, available_signals: list[str]) -> str:
    """Prompt that makes the LLM propose 2-4 concrete deep-dive options with rationale,
    instead of jumping straight to a narrative. User picks one before deep-dive fires.

    entity must be a known brand/config value (e.g. sourced from the registry or
    project config), not unsanitized user text — it is f-string-interpolated
    directly into the prompt with no escaping applied.
    """
    lines = [
        f"You are helping a brand analyst decide what to investigate about {entity}.",
        "Available signals in the data (pick 2-4 of these, do not invent others):",
    ]
    for sig in available_signals:
        desc = _SIGNAL_DESCRIPTIONS.get(sig, sig)
        lines.append(f"  - {sig}: {desc}")
    lines += [
        "",
        "Return a numbered list of 2-4 options. Each option: one line, format:",
        f'  N. <signal_name> — <one-sentence reason this is worth investigating for {entity}>',
        "Do not analyze anything yet. Only propose what's worth looking at and why.",
    ]
    return "\n".join(lines)


def build_methodology_note(metric: str, formula: str, source: str) -> str:
    """Deterministic (non-LLM) methodology string shown next to a stat, separate
    from any AI-written narrative about what the stat means."""
    return f"{metric} = {formula}\nSource: {source}"
