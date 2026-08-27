import sys
import os
import time
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, '.')
from analytics.market_analytics import select_tools
from synthesis.synthesiser import _call_ollama

prompt = 'Compare Bajaj vs Philips on brand health and satisfaction in the Mixer Grinder category.'
analytics = select_tools(prompt, 'Mixer Grinder')

full_prompt = f"""=== EVIDENCE ===
{analytics}

=== USER QUESTION ===
{prompt}

Narrate the pre-computed analytics and qualitative evidence to produce a structured
JSON insight. Use ONLY numbers from the evidence block. Return valid JSON only."""

print('Running Ollama directly...')
t = time.time()
raw = _call_ollama(full_prompt, "qwen3.5:4b", "http://localhost:11434/v1")
print(f"Elapsed: {time.time() - t:.2f}s")
print("=== RAW OUTPUT ===")
print(raw)

