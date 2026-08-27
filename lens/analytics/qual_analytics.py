"""
LENS Qualitative Analytics Engine
=================================
This engine "thinks" about qualitative evidence. It doesn't just retrieve text;
it extracts themes, sentiments, and unmet needs from raw interview passages.
"""

import os
import json
import re

def rprint(*args, **kwargs):
    import re
    text = " ".join(str(a) for a in args)
    text = re.sub(r"\[/?[a-zA-Z_ ]*\]", "", text)
    print(text)

# Lazy Groq client — instantiated only on first use.
# Importing at module level crashes if GROQ_API_KEY is absent.
_groq_client = None

def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY not set — qualitative analytics unavailable. "
                "Set the environment variable and restart."
            )
        from groq import Groq
        _groq_client = Groq(api_key=api_key)
    return _groq_client


def analyze_qualitative_intent(query: str) -> dict:
    """
    Step 1: Deep Intent Analysis.
    Decides what kind of qualitative indicators to look for.
    """
    PROMPT = f"""You are a Qualitative Research Director.
Analyze this user query: "{query}"

What are the specific qualitative "Need States" or "Observation Goals" for this query?
Example: For "price vs quality", goals are "Value Perception", "Willingness to Pay", "Durability expectations".

Return a JSON object:
{{
  "research_objectives": ["obj1", "obj2"],
  "target_sentiments": ["positive", "frustrated", etc],
  "look_for_keywords": ["kw1", "kw2"]
}}
"""
    try:
        response = _get_groq_client().chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": PROMPT}],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {"research_objectives": ["General consumer voice"], "target_sentiments": [], "look_for_keywords": []}


def process_qualitative_evidence(passages: list[dict], intent: dict) -> list[dict]:
    """
    Step 2 & 3: Manipulation & Consolidation.
    Takes raw passages and extracts "Thematic Findings".
    """
    if not passages:
        return []

    # Prepare passages for analysis (compact format)
    evidence_text = ""
    for i, p in enumerate(passages[:10]):
        evidence_text += f"ID: {i}\nDoc: {p.get('doc_id')}\nContent: {p.get('content')}\n\n"

    PROMPT = f"""You are a Qualitative Data Analyst.
OBJECTIVES: {json.dumps(intent['research_objectives'])}

EVIDENCE:
{evidence_text}

Task:
1. Group these voices into 3-4 distinct 'Thematic Findings'.
2. For each finding, provide a one-sentence 'Observation' and a supporting 'Key Quote'.
3. Assign a 'Dominant Sentiment'.

Return a JSON object with a 'findings' array.
"""
    try:
        response = _get_groq_client().chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": PROMPT}],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("findings", [])
    except Exception as e:
        rprint(f"[red]Qualitative processing failed:[/red] {e}")
        return []

def run_qualitative_pipeline(query: str, passages: list[dict]) -> list[dict]:
    """
    The master pipeline that 'thinks' and 'manipulates' qual data.
    """
    rprint("[blue]Qual Engine:[/blue] Analyzing research intent...")
    intent = analyze_qualitative_intent(query)

    rprint(f"[blue]Qual Engine:[/blue] Extracting themes from {len(passages)} passages...")
    findings = process_qualitative_evidence(passages, intent)

    return findings
