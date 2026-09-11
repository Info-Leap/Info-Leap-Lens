import asyncio
import os
import sys
import json
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict

# Ensure project root is in path
oxdata_dir = Path(__file__).resolve().parent.parent
project_root = oxdata_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from infoleap.researcher_agent import run_autonomous_research_async

@dataclass
class EvalResult:
    question: str
    thought_process: List[str]
    final_answer: str
    latency_ms: int
    critique: str
    score: int # 1-10

class KarpathyLoop:
    def __init__(self):
        # We use the internal SubAgentEngines to call a high-tier model for evaluation
        from infoleap.researcher_agent import subagents, MODEL_PRO
        self.eval_model = MODEL_PRO
        self.subagents = subagents

    async def evaluate_agent(self, question: str) -> EvalResult:
        print(f"\n[*] PROBING: {question}")
        start_time = time.time()
        thoughts = []
        final_answer = ""
        
        async for event in run_autonomous_research_async(question, session_id="eval_loop"):
            if event["type"] == "thought":
                thoughts.append(event["content"])
            elif event["type"] == "result":
                final_answer = event["data"].summary
        
        latency = int((time.time() - start_time) * 1000)
        
        # --- THE CRITIQUE ---
        critique_prompt = f"""
        You are a Senior Strategic Research Auditor. Evaluate the following AI Research Agent's performance.
        
        QUESTION: {question}
        THOUGHT PROCESS: {thoughts}
        FINAL ANSWER: {final_answer}
        
        JUDGE ON:
        1. Accuracy: Did it use real data/tools? (1-10)
        2. Depth: Did it hypothesize geographic or demographic splits? (1-10)
        3. Storytelling: Is the tone professional, strategic, and easy to understand? (1-10)
        4. Citations: Did it cite specific sources? (1-10)
        
        Provide a SHORT critique and a TOTAL SCORE (1-10).
        """
        
        critique_res = await self.subagents.call(self.eval_model, critique_prompt, "Senior Auditor")
        
        # Extract score from critique (simple regex or heuristic)
        score = 0
        try:
            import re
            score_match = re.search(r"TOTAL SCORE:?\s*(\d+)", critique_res.upper())
            if score_match: score = int(score_match.group(1))
        except: pass
        
        return EvalResult(question, thoughts, final_answer, latency, critique_res, score)

    async def run_session(self, test_set: List[str]):
        results = []
        for q in test_set:
            res = await self.evaluate_agent(q)
            results.append(res)
            print(f"    -> Score: {res.score}/10")
            print(f"    -> Critique: {res.critique[:200]}...")
            
        # Log to report
        report_path = oxdata_dir / "docs" / "AGENT_EVOLUTION_LOG.md"
        with open(report_path, "a") as f:
            f.write(f"\n## [KARPATHY LOOP] {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            for r in results:
                f.write(f"- **Q**: {r.question}\n")
                f.write(f"  - **Score**: {r.score}/10\n")
                f.write(f"  - **Latency**: {r.latency_ms}ms\n")
                f.write(f"  - **Critique**: {r.critique}\n")
        
        print(f"\n[SUCCESS] Evolution log updated at: {report_path}")

if __name__ == "__main__":
    loop = KarpathyLoop()
    test_questions = [
        "Why did Preethi NPS drop 6 points? Break it down by city.",
        "Compare Crompton vs Bajaj in the North. What are the top drivers for Bajaj?",
        "What do Gen-Z consumers dislike about mixer grinders? Give me raw verbatims."
    ]
    asyncio.run(loop.run_session(test_questions))
