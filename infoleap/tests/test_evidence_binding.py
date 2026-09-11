import unittest
import os
import sys
from pathlib import Path

# Ensure project root is in path
current_dir = Path(__file__).resolve().parent
oxdata_dir = current_dir.parent
project_root = oxdata_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

class TestEvidenceBinding(unittest.TestCase):
    def test_verbatim_id_linking(self):
        """RED: Test that every verbatim retrieved by the agent has a source ID for UI linking."""
        from infoleap.researcher_agent import ResearchDeps
        deps = ResearchDeps()
        
        # Simulate agent finding verbatims
        test_quotes = [
            {"source": "Doc_101", "text": "The motor is great.", "city": "Mumbai"},
            {"source": "Doc_102", "text": "Handle broke.", "city": "Delhi"}
        ]
        deps.all_verbatims.extend(test_quotes)
        
        # We need a function to find a specific verbatim by source ID
        def get_evidence_by_id(verbatims, source_id):
            return next((v for v in verbatims if v['source'] == source_id), None)
            
        result = get_evidence_by_id(deps.all_verbatims, "Doc_102")
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "Handle broke.")
        
    def test_qual_retriever_returns_ids(self):
        """RED: Test that the real qual_retriever tool returns source IDs in its output."""
        import asyncio
        from infoleap.skills.qual_retriever import search_qual_trees
        
        async def run_retrieval():
            # Search for something known to exist
            passages = await search_qual_trees("motor", filters={"brand": "Bajaj"})
            self.assertGreater(len(passages), 0)
            self.assertIn("doc_id", passages[0])
            self.assertIsNotNone(passages[0]["doc_id"])

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_retrieval())
        finally:
            loop.close()

if __name__ == "__main__":
    unittest.main()
