import os
import sys
import time
import json
import pandas as pd
import asyncio
from typing import List

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from researcher_agent import run_autonomous_research
from utils.diary_util import record_thought

# --- 1. THE 100 QUESTION GENERATOR ---
def generate_test_suite() -> List[str]:
    brands = ["Bajaj", "Havells", "Crompton", "Philips", "Orient", "Usha"]
    zones = ["North", "South", "East", "West"]
    categories = ["Ceiling Fans", "Mixer Grinders", "LED Bulbs", "Water Heaters"]
    
    suite = []
    
    # Tier 1: Fact Finding (20 Questions)
    for b in brands:
        suite.append(f"What is the total unique respondent count for {b}?")
        suite.append(f"What is the national NPS for {b}?")
    suite.append("Total respondents in the dataset")
    suite.append("Count of respondents by zone")
    
    # Tier 2: Drilling (20 Questions)
    for z in zones:
        suite.append(f"How many respondents are in the {z} zone?")
        suite.append(f"What is the NPS for Bajaj in the {z} zone?")
    suite.append("Top 5 cities by respondent count")
    
    # Tier 3: Comparative (20 Questions)
    suite.append("Compare TOM awareness for Bajaj vs Havells")
    suite.append("Compare total awareness funnel for Philips and Crompton")
    suite.append("Which zone has the highest NPS for Ceiling Fans?")
    suite.append("Compare respondent gender distribution in North vs South")
    
    # Tier 4: Analytical Drivers (20 Questions)
    for b in brands[:3]:
        for c in categories[:2]:
            suite.append(f"What are the top 5 loyalty drivers for {b} in {c} national?")
            suite.append(f"Why is the NPS for {b} high/low in {c}?")
            
    # Tier 5: Logic & Resilience (20 Questions)
    suite.append("Run a driver analysis for Crompton in Mixer Grinders for the West zone")
    suite.append("Does the North zone prioritize 'Silent Operation' more than the South in Mixer Grinders?")
    suite.append("Which segment (Age/Gender) has the highest ownership of Water Heaters?")
    suite.append("Compare Bajaj and Havells on 'Trustworthy Brand' attribute across all categories")
    
    # Fill up to 100 with variations if needed
    while len(suite) < 100:
        suite.append(f"NPS check for {brands[len(suite)%len(brands)]} in {zones[len(suite)%len(zones)]} zone")
        
    return suite[:100]

# --- 2. AUDIT LOGIC ---
def audit_response(question: str, res: any) -> dict:
    report = res.output
    df = res.df
    
    audit = {
        "question": question,
        "summary_len": len(report.summary) if report.summary else 0,
        "has_drivers": bool(report.drivers),
        "has_df": df is not None,
        "df_shape": df.shape if df is not None else (0,0),
        "status": "PASS",
        "error": None
    }
    
    # UI Render-readiness check
    if df is not None and not df.empty:
        # Check if Bar Chart or Heatmap would fail
        if len(df.columns) < 2:
            audit["status"] = "WARNING"
            audit["error"] = "Single column DF - no bar chart"
            
    if audit["summary_len"] < 10:
        audit["status"] = "FAIL"
        audit["error"] = "Empty or too short summary"
        
    return audit

# --- 3. THE LOOP ---
def main():
    record_thought("Batch Audit Start", "Initializing the 100-question automated stress test.")
    suite = generate_test_suite()
    results = []
    
    start_total = time.time()
    
    for i, q in enumerate(suite):
        print(f"[{i+1}/100] Testing: {q}")
        try:
            start_q = time.time()
            res = run_autonomous_research(q)
            latency = int((time.time() - start_q) * 1000)
            
            audit = audit_response(q, res)
            audit["latency_ms"] = latency
            results.append(audit)
            
            print(f"   - Status: {audit['status']} ({latency}ms)")
            
            # Incremental Save
            pd.DataFrame(results).to_csv("batch_audit_results.csv", index=False)
            
            # Simulated Human Pacing & Rate Limit Protection
            # Active constraint: 45-60s for humans, we do 5s for the batch
            time.sleep(5)
            
            # Periodically log to diary
            if (i+1) % 10 == 0:
                record_thought("Batch Progress", f"Completed {i+1}/100. Current Average Latency: {sum(r['latency_ms'] for r in results)/len(results):.0f}ms")
                
        except Exception as e:
            print(f"   - CRASH: {e}")
            results.append({"question": q, "status": "CRASH", "error": str(e), "latency_ms": 0})
            record_thought("Batch Crash", f"Question {i+1} failed: {e}")

    # FINAL REPORT
    end_total = time.time()
    df_results = pd.DataFrame(results)
    pass_rate = (df_results['status'] == 'PASS').sum()
    
    summary_msg = f"""
    100-Question Stress Test Complete.
    Pass Rate: {pass_rate}/100
    Total Time: {int(end_total - start_total)}s
    Crashes: {(df_results['status'] == 'CRASH').sum()}
    Warnings: {(df_results['status'] == 'WARNING').sum()}
    """
    print(summary_msg)
    record_thought("Batch Audit Complete", summary_msg)
    
    # Save results for manual review
    df_results.to_csv("batch_audit_results.csv", index=False)

if __name__ == "__main__":
    main()
