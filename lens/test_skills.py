import sys, os, time
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, '.')
from analytics.market_analytics import select_tools
from synthesis.synthesiser import synthesise, select_skill, load_skill, compress_evidence

queries = [
    ("Compare Bajaj vs Philips on brand health", "groq", "llama-3.1-8b-instant"),
    ("What are the most important features for mixer grinder consumers?", "groq", "llama-3.1-8b-instant"),
    ("Which cities have highest satisfaction?", "groq", "llama-3.1-8b-instant"),
]

for prompt, backend, model in queries:
    print(f"\n{'='*60}")
    print(f"Q: {prompt}")
    skill = select_skill(prompt)
    print(f"Skill selected: {skill}")

    analytics = select_tools(prompt, 'Mixer Grinder')
    evidence = compress_evidence(analytics, [])
    print(f"Evidence size: {len(evidence)} chars (~{len(evidence)//4} tokens)")

    t = time.time()
    result = synthesise(prompt, [], {'analytics_tools': analytics}, None,
                       {'backend': backend, 'model': model, 'api_key': os.getenv('GROQ_API_KEY')})
    elapsed = time.time() - t

    print(f"Time: {elapsed:.1f}s | Confidence: {result.get('confidence','?')}")
    print(f"Answer: {result.get('answer','')[:300]}...")
    print(f"Findings: {len(result.get('key_findings',[]))}")

