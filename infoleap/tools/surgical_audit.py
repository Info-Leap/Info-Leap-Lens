import os
import sys
import asyncio
import json
import pandas as pd

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from researcher_agent import run_autonomous_research
from utils.diary_util import record_thought

def audit_question(question, expected_elements=None):
    print(f"\n🚀 CRITICAL AUDIT: {question}")
    
    start_time = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0
    import time
    real_start = time.time()
    
    res = run_autonomous_research(question)
    
    real_duration = time.time() - real_start
    summary = res.output.summary
    thinking = res.output.thinking
    df = res.df
    
    print(f"⏱️ Latency: {real_duration:.2f}s")
    print(f"🧠 Thinking: {thinking}")
    print(f"📊 Summary: {summary}")
    
    issues = []
    
    # 1. Element Check
    if expected_elements:
        for elem in expected_elements:
            if elem.lower() not in summary.lower() and elem.lower() not in thinking.lower():
                issues.append(f"Missing expected detail: '{elem}'")
    
    # 2. Logic Check: Funnel Integrity
    if "funnel" in question.lower() or "awareness" in question.lower():
        import re
        nums = [int(n) for n in re.findall(r'\d+', summary)]
        if len(nums) < 4: # Comparison should have at least 2 numbers per brand
             # If it's a funnel, it should ideally have 3 per brand
             if "funnel" in question.lower() and len(nums) < 6:
                 issues.append(f"Funnel summary too sparse. Only found {len(nums)} numbers.")

    # 3. UI/Chart Check
    if df is not None:
        print(f"📈 DF Shape: {df.shape}")
        if df.empty:
            issues.append("Empty DataFrame - No UI charts")
        if df.shape == (1, 1):
            print("✨ UI: Metric card will be used.")
        elif len(df.columns) < 2:
            issues.append("Single-column DF - Visualizations will be limited")

    if issues:
        print(f"❌ AUDIT FAILED: {issues}")
        record_thought("Audit Failure", f"Q: {question} | Issues: {issues}")
        return False
    else:
        print("✅ AUDIT PASSED")
        record_thought("Audit Success", f"Q: {question} cleared all gates.")
        return True

if __name__ == "__main__":
    # Target Question
    q = "Show me the top 3 cities with the highest 'Aided Awareness' for Usha Water Heaters."
    audit_question(q, expected_elements=["Usha", "Water Heaters", "Aided", "city"])
