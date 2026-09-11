import asyncio
import time
import sys
import os

# Ensure project root is in path
sys.path.append(os.getcwd())

from researcher_agent import run_autonomous_research_async

async def test_lens_tiers():
    questions = [
        ("BASIC", "total respondents"),
        ("INTERMEDIATE", "What is the NPS for Bajaj Fans in North zone?"),
        ("COMPLEX", "What drives brand loyalty for Crompton Fans among women in South zone?")
    ]
    
    print(f"🚀 LENS 3.0: Tiered Performance Benchmark\n" + "="*50)
    
    for tier, q in questions:
        print(f"\n[TIER: {tier}] Query: '{q}'")
        start = time.time()
        
        try:
            async for event in run_autonomous_research_async(q):
                if event["type"] == "thought":
                    print(f"  > {event['content']}")
                elif event["type"] == "error":
                    print(f"  ❌ ERROR: {event['content']}")
                elif event["type"] == "result":
                    latency = time.time() - start
                    print(f"  ✅ Result Received ({latency:.2f}s)")
                    summary = str(event['data'].summary)[:100].replace("\n", " ")
                    print(f"  Summary: {summary}...")
                    if event['data'].recommendation:
                        rec = str(event['data'].recommendation)[:150].replace("\n", " ")
                        print(f"  Strategic Advice (Critiqued): {rec}...")
        except Exception as e:
            print(f"  💥 FATAL EXCEPTION: {e}")
    
    print("\n" + "="*50 + "\nBenchmark Complete.")

if __name__ == "__main__":
    asyncio.run(test_lens_tiers())
