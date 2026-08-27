import sys
import os
import json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, '.')
from analytics.market_analytics import select_tools
from synthesis.synthesiser import synthesise

prompt = 'Compare Bajaj vs Philips on brand health and satisfaction in the Mixer Grinder category.'
print('Running Analytics...')
analytics = select_tools(prompt, 'Mixer Grinder')

model_config = {'backend': 'gemini', 'model': 'gemini-2.5-flash', 'api_key': os.getenv("GEMINI_API_KEY")}
print(f'Running Synthesis with {model_config["model"]}...')

res = synthesise(prompt, [], {'analytics_tools': analytics}, None, model_config)
print('\n--------------- FINAL ANSWER ---------------')
print(res.get('answer'))
print('\n--------------- KEY FINDINGS ---------------')
for k in res.get('key_findings', []):
    print(f"- {k}")

