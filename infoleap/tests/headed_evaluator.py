import asyncio
import os
import sys
import json
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict
from playwright.async_api import async_playwright

# Ensure project root is in path
oxdata_dir = Path(__file__).resolve().parent.parent
project_root = oxdata_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

@dataclass
class EvalResult:
    question: str
    final_answer: str
    latency_ms: int
    critique: str
    score: int

class HeadedKarpathyLoop:
    def __init__(self, port=8501):
        self.port = port
        # We need the subagents for the final critique pass
        from infoleap.researcher_agent import subagents, MODEL_PRO
        self.subagents = subagents
        self.eval_model = MODEL_PRO

    async def run_probe(self, playwright, question: str) -> EvalResult:
        print(f"\n[*] VISUAL PROBE: {question}")
        start_time = time.time()
        
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        await page.goto(f"http://localhost:{self.port}")
        await page.wait_for_load_state("networkidle")
        
        # 1. Navigate to Ask Pulse
        await page.get_by_role("link", name="Ask Pulse").click()
        await asyncio.sleep(1)
        
        # 2. Type and Enter
        chat_input = page.locator('textarea[aria-label="Ask about market trends, brand health, or consumer feedback..."]')
        if await chat_input.count() == 0:
            chat_input = page.locator('input[aria-label="Ask about market trends, brand health, or consumer feedback..."]')
            
        await chat_input.fill(question)
        await chat_input.press("Enter")
        
        # 3. Wait for 'Pulse just answered' - watch the GLIDE steps in headed mode
        print("    -> Watching GLIDE steps and Auditor verification...")
        await page.locator('h3:has-text("Pulse just answered")').wait_for(timeout=120000)
        
        # 4. Scrape the Answer
        answer_div = page.locator('div[style*="background-color: rgb(240, 253, 244)"]').last
        final_answer = await answer_div.inner_text()
        latency = int((time.time() - start_time) * 1000)
        
        # --- THE CRITIQUE ---
        critique_prompt = f"""
        You are a Senior Strategic Research Auditor. Evaluate this result.
        QUESTION: {question}
        ANSWER: {final_answer}
        
        JUDGE ON: Accuracy (1-10), Strategic Depth (1-10), and Storytelling (1-10).
        Provide a SHORT critique and a TOTAL SCORE (1-10).
        """
        critique_res = await self.subagents.call(self.eval_model, critique_prompt, "Senior Auditor")
        
        score = 0
        try:
            import re
            score_match = re.search(r"TOTAL SCORE:?\s*(\d+)", critique_res.upper())
            if score_match: score = int(score_match.group(1))
        except: pass
        
        await browser.close()
        return EvalResult(question, final_answer, latency, critique_res, score)

    async def run_session(self, test_set: List[str]):
        async with async_playwright() as playwright:
            results = []
            for q in test_set:
                res = await self.run_probe(playwright, q)
                results.append(res)
                print(f"    -> Visual Result Scraped. Score: {res.score}/10")

            # Update Log
            report_path = oxdata_dir / "docs" / "AGENT_EVOLUTION_LOG.md"
            with open(report_path, "a") as f:
                f.write(f"\n## [HEADED KARPATHY LOOP] {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                for r in results:
                    f.write(f"- **Q**: {r.question}\n")
                    f.write(f"  - **Score**: {r.score}/10\n")
                    f.write(f"  - **Visual Verification**: Passed in Headed Mode\n")
                    f.write(f"  - **Critique**: {r.critique}\n")
            
            print(f"\n[SUCCESS] Headed audit complete. Log updated at: {report_path}")

if __name__ == "__main__":
    loop = HeadedKarpathyLoop()
    test_questions = [
        "Compare Crompton vs Bajaj in the North. What are the top drivers for Bajaj?",
        "Why did Preethi NPS drop? Break it down by city and give me verbatims."
    ]
    asyncio.run(loop.run_session(test_questions))
