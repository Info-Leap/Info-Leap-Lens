import unittest
import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is in path
current_dir = Path(__file__).resolve().parent
oxdata_dir = current_dir.parent
project_root = oxdata_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from infoleap.researcher_agent import run_autonomous_research_async

class TestInvestigationStreaming(unittest.TestCase):
    def test_investigation_event_sequence(self):
        """RED: Test that agent yields specific step-based thoughts for an investigation query."""
        async def run_test():
            query = "Investigate Preethi NPS drop"
            events = []
            async for event in run_autonomous_research_async(query, session_id="test_investigation"):
                events.append(event)
            
            # Check for thought sequence
            thoughts = [e['content'] for e in events if e['type'] == 'thought']
            
            # We expect at least these key investigation steps in the streaming output
            self.assertTrue(any("CONFIRM" in t.upper() for t in thoughts), "Missing CONFIRM step")
            self.assertTrue(any("SLICE" in t.upper() for t in thoughts), "Missing SLICE step")
            self.assertTrue(any("QUAL" in t.upper() for t in thoughts), "Missing QUAL step")
            
            # Final result should exist
            self.assertTrue(any(e['type'] == 'result' for e in events))

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_test())
        finally:
            loop.close()

if __name__ == "__main__":
    unittest.main()
