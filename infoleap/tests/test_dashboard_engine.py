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

class TestDashboardEngine(unittest.TestCase):
    def test_dashboard_engine_raw_stats_keys(self):
        """RED: Test that DashboardEngine returns correctly structured data."""
        try:
            from infoleap.utils.dashboard_engine import DashboardEngine
            engine = DashboardEngine()
            stats = engine.get_raw_stats()
            
            self.assertIsInstance(stats, dict)
            self.assertIn("respondents", stats)
            self.assertIn("nps", stats)
            self.assertIn("share", stats)
            self.assertIsInstance(stats["respondents"], int)
            self.assertGreater(stats["respondents"], 0)
        except ImportError:
            self.fail("DashboardEngine module not found. RED verified.")

    def test_dashboard_engine_hero_insight(self):
        """RED: Test that DashboardEngine generates an AI insight."""
        from infoleap.utils.dashboard_engine import DashboardEngine
        engine = DashboardEngine()
        # Mocking the async call or just checking if it exists
        self.assertTrue(hasattr(engine, 'get_ai_hero_insight'))
        
        # Actually run it (requires loop)
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            insight = loop.run_until_complete(engine.get_ai_hero_insight())
            self.assertIsInstance(insight, str)
            self.assertIn("<span class='insight-highlight'>", insight)
        finally:
            loop.close()

    def test_cached_dashboard_data(self):
        """RED: Test that cached dashboard data returns valid structure."""
        from infoleap.utils.dashboard_engine import get_cached_dashboard_data
        data = get_cached_dashboard_data()
        
        self.assertIsInstance(data, dict)
        self.assertIn("stats", data)
        self.assertIn("hero_insight", data)
        self.assertIn("timestamp", data)
        self.assertGreater(data["stats"]["respondents"], 0)

if __name__ == "__main__":
    unittest.main()
