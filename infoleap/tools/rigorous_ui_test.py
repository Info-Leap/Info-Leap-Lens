import unittest
from streamlit.testing.v1 import AppTest
import pandas as pd
import time
import os
import json

class TestLensRigorousPOV(unittest.TestCase):
    def setUp(self):
        # Initialize AppTest from the main app entry point
        self.at = AppTest.from_file("app.py", default_timeout=60)
        self.at.run()

    def test_pov_1_brand_drivers(self):
        """Test Case: Brand POV - Specific loyalty drivers."""
        print("\n[POV TEST] 1. Brand Drivers (Bajaj Fans)...")
        self.at.chat_input[0].set_value("What drives loyalty for Bajaj Fans?").run()
        
        # Verify the answer structure
        assistant_msgs = [m for m in self.at.chat_message if m.name == "assistant"]
        self.assertTrue(len(assistant_msgs) > 0, "No response from assistant.")
        
        content = assistant_msgs[-1].markdown[0].value
        self.assertIn("TOP-MOST DRIVERS", content.upper())
        self.assertIn("Bajaj", content)
        print("✅ Brand POV Passed.")

    def test_pov_2_regional_comparison(self):
        """Test Case: Regional POV - Comparing two segments."""
        print("\n[POV TEST] 2. Regional Comparison (North vs South)...")
        # Note: Complex multi-agent queries might take longer
        self.at.chat_input[0].set_value("Compare drivers for Crompton in North vs South zone.").run()
        
        assistant_msgs = [m for m in self.at.chat_message if m.name == "assistant"]
        content = assistant_msgs[-1].markdown[0].value
        self.assertIn("North", content)
        self.assertIn("South", content)
        print("✅ Regional POV Passed.")

    def test_pov_3_demographic_drivers(self):
        """Test Case: Demographic POV - Slicing by Gender/Income."""
        print("\n[POV TEST] 3. Demographic Slicing (Women + Mixer Grinder)...")
        self.at.chat_input[0].set_value("What are the top drivers for Mixer Grinders among female respondents?").run()
        
        assistant_msgs = [m for m in self.at.chat_message if m.name == "assistant"]
        content = assistant_msgs[-1].markdown[0].value
        self.assertIn("Mixer Grinder", content)
        self.assertIn("female", content.lower())
        print("✅ Demographic POV Passed.")

    def test_pov_4_cross_category(self):
        """Test Case: Cross-Category POV - Statistical Transferability."""
        print("\n[POV TEST] 4. Cross-Category Correlation...")
        self.at.chat_input[0].set_value("Does the preference for silence in Fans correlate with Mixer Grinders?").run()
        
        assistant_msgs = [m for m in self.at.chat_message if m.name == "assistant"]
        content = assistant_msgs[-1].markdown[0].value
        # Check for correlation coefficient or heatmap mention
        self.assertIn("correlation", content.lower())
        print("✅ Cross-Category POV Passed.")

    def test_fast_path_sizing(self):
        """Test Case: Fast-Path Logic for simple sizing."""
        print("\n[POV TEST] 5. Fast-Path Sizing (Total Respondents)...")
        self.at.chat_input[0].set_value("totla number of respondant by city").run()
        
        assistant_msgs = [m for m in self.at.chat_message if m.name == "assistant"]
        content = assistant_msgs[-1].markdown[0].value
        # Should bypass heavy reasoning and give a table/count
        self.assertIn("6,631", content)
        print("✅ Fast-Path Passed.")

if __name__ == "__main__":
    # Running tests one by one manually to avoid TPD limits if possible
    suite = unittest.TestSuite()
    suite.addTest(TestLensRigorousPOV('test_pov_1_brand_drivers'))
    suite.addTest(TestLensRigorousPOV('test_pov_2_regional_comparison'))
    suite.addTest(TestLensRigorousPOV('test_pov_3_demographic_drivers'))
    suite.addTest(TestLensRigorousPOV('test_pov_4_cross_category'))
    suite.addTest(TestLensRigorousPOV('test_fast_path_sizing'))
    
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)
