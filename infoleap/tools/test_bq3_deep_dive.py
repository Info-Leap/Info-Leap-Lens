import asyncio
import time
import sys
import os

# Ensure project root is in path
sys.path.append(os.getcwd())

from researcher_agent import run_autonomous_research_async

async def run_scenario(name, q):
    print(f"\n🚀 [SCENARIO: {name}]")
    print(f"Query: '{q}'")
    start = time.time()
    
    try:
        async for event in run_autonomous_research_async(q):
            if event["type"] == "result":
                latency = time.time() - start
                res = event["data"]
                print(f"✅ SUCCESS ({latency:.2f}s)")
                print("-" * 30)
                print(f"📊 SUMMARY: {res.summary[:250]}...")
                print(f"\n📈 BENCHMARKS:\n{res.benchmarks}")
                print(f"\n🚀 STRATEGIC DRIVERS (DATA):\n{res.drivers}")
                print("-" * 30)
            elif event["type"] == "error":
                print(f"❌ ERROR: {event['content']}")
    except Exception as e:
        print(f"💥 FATAL EXCEPTION: {e}")

async def main():
    print("LENS 3.0: Sequential BQ3 Analytical Stress-Test")
    print("=" * 60)
    
    scenarios = [
        ("National Deep-Dive", "What are the top strategic drivers for Bajaj in Ceiling Fans?"),
        ("Regional Slicing", "What drives Crompton loyalty in the South zone?"),
        ("Competitive Benchmarking", "Compare Bajaj and Crompton top drivers in the North zone."),
        ("Cross-Category Philips", "How do drivers for Philips LED Bulbs compare to Philips Ceiling Fans?")
    ]
    
    for name, q in scenarios:
        await run_scenario(name, q)
        print(f"⏳ Cooldown: Waiting 15 seconds to avoid 429 Rate Limits...")
        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
