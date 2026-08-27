import sys
import os
import time
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, '.')
from analytics.market_analytics import select_tools
from synthesis.synthesiser import synthesise

prompt = 'Compare Bajaj vs Philips on brand health and satisfaction in the Mixer Grinder category.'

print('--- STAGE 1: Routing & Analytics ---')
t1 = time.time()
analytics = select_tools(prompt, 'Mixer Grinder')
print(f'Analytics complete in {time.time()-t1:.2f}s')

model_config = {
    'backend': 'ollama', 
    'model': 'qwen3.5:4b', 
    'ollama_url': 'http://localhost:11434/v1'
}

print(f'\n--- STAGE 2: Local Synthesis with {model_config["model"]} ---')
t2 = time.time()
try:
    res = synthesise(prompt, [], {'analytics_tools': analytics}, None, model_config)
    elapsed = time.time() - t2
    print(f'Synthesis complete in {elapsed:.2f}s')
    
    print('\n--------------- LOCAL MODEL OUTPUT ---------------')
    print(res.get('answer'))
    print('\n--------------- KEY FINDINGS ---------------')
    for k in res.get('key_findings', []):
        print(f"- {k}")
    print('--------------------------------------------------')
    
except Exception as e:
    print(f'Local synthesis failed: {e}')

