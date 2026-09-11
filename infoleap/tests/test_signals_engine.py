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

class TestSignalsEngine(unittest.TestCase):
    def test_signals_detection_structure(self):
        """RED: Test that SignalsEngine identifies anomalies and returns expected schema."""
        try:
            from infoleap.utils.signals_engine import SignalsEngine
            engine = SignalsEngine()
            signals = engine.detect_anomalies()
            
            self.assertIsInstance(signals, list)
            if len(signals) > 0:
                s = signals[0]
                self.assertIn("title", s)
                self.assertIn("description", s)
                self.assertIn("severity", s) # 'Critical', 'Warning', 'Positive'
                self.assertIn("recommendation", s)
        except ImportError:
            self.fail("SignalsEngine module not found. RED verified.")

if __name__ == "__main__":
    unittest.main()
