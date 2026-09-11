import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is in path
current_file = Path(__file__).resolve()
project_root = current_file.parents[2] # Go up to info-leap
sys.path.insert(0, str(project_root))

from infoleap.researcher_agent import run_autonomous_research_async

async def diagnose_gender_bug():
    print("[*] Probing Gender Breakdown Bug...")
    query = "Which city has the highest number of respondents?"
    
    async for event in run_autonomous_research_async(query, session_id="diagnosis"):
        if event["type"] == "thought":
            print(f"    [THOUGHT]: {event['content']}")
        elif event["type"] == "result":
            report = event["data"]
            print("\n--- AGENT DIAGNOSTIC ---")
            print(f"SQL Used: {event.get('sql')}")
            print(f"Python Code: {report.chart_code}")
            print(f"Summary: {report.summary}")
            print(f"Trace: {report.diagnostic_trace}")

if __name__ == "__main__":
    asyncio.run(diagnose_gender_bug())
