import asyncio
import os
import sys
import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict
from playwright.async_api import async_playwright

# Ensure project root is in path
oxdata_dir = Path(__file__).resolve().parent.parent
project_root = oxdata_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

PROGRESS_PATH = oxdata_dir / "tests" / "audit_progress.json"
TRUTH_PATH = oxdata_dir / "tests" / "golden_truth.json"

@dataclass
class GoldenResult:
    question: str
    truth: str
    agent_answer: str
    score: int
    critique: str

class AgentEvolutionHarness:
    def __init__(self, port=8501):
        self.port = port
        from infoleap.researcher_agent import subagents, MODEL_PRO
        self.subagents = subagents
        self.eval_model = MODEL_PRO
        self.load_state()

    def load_state(self):
        if PROGRESS_PATH.exists():
            with open(PROGRESS_PATH, "r") as f:
                self.state = json.load(f)
        else:
            self.state = {"last_successful_index": -1, "total_questions": 30, "history": [], "failures": {}}

    def save_state(self):
        with open(PROGRESS_PATH, "w") as f:
            json.dump(self.state, f, indent=4)

    async def run_probe(self, page, question: str, truth: str) -> GoldenResult:
        print(f"\n[*] PROBING: {question}")

        # Count existing bubbles to identify the new one later
        bubble_selector = 'div[style*="background-color: rgb(240, 253, 244)"]'
        existing_count = await page.locator(bubble_selector).count()

        await page.locator('textarea[aria-label*="Ask about market trends"]').fill(question)
        await page.keyboard.press("Enter")

        print(f"    -> Pulse is thinking (waiting for bubble {existing_count + 1})...")
        try:
            # Wait for the NEW header to appear
            await page.locator('h3:has-text("Pulse just answered")').nth(existing_count).wait_for(timeout=180000)
            print("    -> Waiting for Senior Auditor to polish the answer...")
            await asyncio.sleep(15) 

            # Scrape the specific new bubble
            agent_answer = await page.locator(bubble_selector).nth(existing_count).inner_text()
            print(f"    -> Scraped Answer ({len(agent_answer)} chars)")
        except Exception as e:
            print(f"    [!] Scrape failure: {e}")
            agent_answer = "TIMEOUT / FAILURE"
        # Applied fuzzy match at line 46-55.
        eval_prompt = f"""
        You are a Senior Strategic Research Auditor.
        GOLDEN TRUTH: {truth}
        AGENT ANSWER: {agent_answer}
        
        CRITIQUE CRITERIA:
        1. Accuracy: Did it match the truth numbers/facts?
        2. Storytelling: Did it sound like a professional data explainer?
        3. Strategic Depth: Did it provide useful context? (Did it commit to the answer?)
        
        Provide a concise critique and a FINAL SCORE (1-10).
        """
        critique = await self.subagents.call(self.eval_model, eval_prompt, "Senior Auditor")
        
        score = 0
        try:
            import re
            score_match = re.search(r"SCORE:?\s*([\d\.]+)", critique.upper())
            if score_match: score = int(float(score_match.group(1)))
        except: pass
        
        return GoldenResult(question, truth, agent_answer, score, critique)

    async def run_session(self):
        with open(TRUTH_PATH, "r") as f:
            truth_data = json.load(f)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False)
            page = await browser.new_page()
            await page.goto(f"http://localhost:{self.port}")
            await page.wait_for_load_state("networkidle")
            await page.get_by_role("link", name="Ask Pulse").click()
            await asyncio.sleep(2)
            
            # --- START SINGLE SESSION ---
            try:
                await page.get_by_role("button", name="New Session").click()
                print("[*] SESSION RESET: Starting fresh multi-turn journey.")
                await asyncio.sleep(2)
            except:
                pass

            start_idx = self.state["last_successful_index"] + 1
            print(f"[*] RESUMING audit from Question {start_idx + 1}")

            for i in range(start_idx, len(truth_data)):
                item = truth_data[i]
                res = await self.run_probe(page, item["q"], item["truth"])
                
                if res.score >= 8:
                    print(f"    [PASS] Score: {res.score}/10")
                    self.state["last_successful_index"] = i
                    self.state["failures"].pop(str(i), None)
                    
                    # REGRESSION CHECK (Verify Question 1 periodically)
                    if i > 0 and i % 5 == 0:
                        print("    [!] REGRESSION CHECK: Re-verifying Question 1...")
                        reg_res = await self.run_probe(page, truth_data[0]["q"], truth_data[0]["truth"])
                        if reg_res.score < 8:
                            print(f"    [FAIL] REGRESSION DETECTED on Question 1! Score: {reg_res.score}")
                            self.state["failures"]["0"] = asdict(reg_res)
                            self.state["last_successful_index"] = -1 # Full reset required
                            self.save_state()
                            break

                else:
                    print(f"    [FAIL] Score: {res.score}/10")
                    self.state["failures"][str(i)] = asdict(res)
                    print(f"\nCRITICAL DISCREPANCY LOGGED:\n{res.critique}\n")
                    self.save_state()
                    break 
                
                self.save_state()

            await browser.close()

if __name__ == "__main__":
    harness = AgentEvolutionHarness()
    asyncio.run(harness.run_session())
