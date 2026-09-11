from streamlit.testing.v1 import AppTest
import pandas as pd
import time
import os

def test_lens_ui_accuracy():
    # 1. Initialize App
    print("[UI TEST] Initializing AppTest from app.py...")
    at = AppTest.from_file("app.py", default_timeout=30)
    at.run()
    
    # 2. Check if Proactive Dashboard is visible
    print("[UI TEST] Verifying Proactive Overview...")
    assert len(at.info) >= 1 or len(at.markdown) > 0, "Proactive Dashboard failed to render."
    
    # 3. Simulate User Question
    test_q = "what is our total unique reviewer count from quantitative data"
    print(f"[UI TEST] Simulating Input: {test_q}")
    at.chat_input[0].set_value(test_q).run()
    
    # 4. Verify Answer Rendering
    # The assistant reply should be in a chat_message and contain the number 6631
    assistant_msgs = [m for m in at.chat_message if m.name == "assistant"]
    if assistant_msgs:
        final_answer = assistant_msgs[-1].markdown[0].value
        print(f"[UI TEST] Captured Answer: {final_answer}")
        assert "6631" in final_answer or "6,631" in final_answer, "Calculation result missing from UI."
    else:
        # Check for error blocks
        if at.error:
            print(f"[UI TEST] Found UI Error: {at.error[0].value}")
        raise AssertionError("Assistant failed to reply in UI.")

    # 5. Test Driver Analysis UI
    driver_q = "What drives loyalty for Bajaj Fans?"
    print(f"[UI TEST] Simulating Driver Query: {driver_q}")
    at.chat_input[0].set_value(driver_q).run()
    
    assistant_msgs = [m for m in at.chat_message if m.name == "assistant"]
    final_answer = assistant_msgs[-1].markdown[0].value
    
    print("[UI TEST] Verifying Quadrant Formatting...")
    assert "TOP-MOST DRIVERS" in final_answer or "🚀" in final_answer, "Quadrant headers missing from UI."
    assert "%" in final_answer, "Impact percentages missing from UI."

    print("\n✅ LENS 2.0 UI AUDIT PASSED: Accuracy and Formatting Verified.")

if __name__ == "__main__":
    try:
        test_lens_ui_accuracy()
    except Exception as e:
        print(f"\n❌ UI TEST FAILED: {e}")
        # Diagnostic: print what was on the screen
        at = AppTest.from_file("app.py")
        at.run()
        print(f"Current Title: {at.title[0].value if at.title else 'None'}")
