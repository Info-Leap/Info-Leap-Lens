import os
import subprocess
import time
import pytest
from playwright.sync_api import sync_playwright, expect

# --- CONFIGURATION ---
STREAMLIT_PORT = "8503" # Different port for stress tests
BASE_URL = f"http://localhost:{STREAMLIT_PORT}"
SCREENSHOT_DIR = "oxdata/tests/results/screenshots"

os.makedirs(SCREENSHOT_DIR, exist_ok=True)

@pytest.fixture(scope="module", autouse=True)
def streamlit_server():
    """Starts the streamlit server in the background."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    
    cmd = [
        "streamlit", "run", "oxdata/app.py",
        "--server.port", STREAMLIT_PORT,
        "--server.address", "localhost",
        "--server.headless", "false"
    ]
    
    process = subprocess.Popen(cmd, env=env)
    
    max_retries = 20
    ready = False
    import requests
    for _ in range(max_retries):
        try:
            res = requests.get(BASE_URL)
            if res.status_code == 200:
                ready = True
                break
        except:
            pass
        time.sleep(1)
    
    if not ready:
        process.kill()
        pytest.fail("Streamlit server failed to start for Stress Tests.")
        
    yield
    process.terminate()

def run_stress_test(page, question, test_id):
    try:
        page.set_default_timeout(120000)
        page.goto(BASE_URL)
        page.wait_for_selector("text=Ask LENS")
        
        chat_input = page.get_by_placeholder("Enter research question...")
        chat_input.fill(question)
        chat_input.press("Enter")
        
        # Wait for either completion or error
        try:
            # Use specific Streamlit test IDs for error vs success
            page.wait_for_selector("[data-testid='stNotificationContentError'], h3:has-text('Strategic Insight')", timeout=120000)
            
            # Check for real error box
            error_box = page.locator("[data-testid='stNotificationContentError']").first
            if error_box.is_visible():
                error_text = error_box.inner_text()
                print(f"\n[FAIL] UI Error Detected: {error_text}")
                page.screenshot(path=f"{SCREENSHOT_DIR}/stress_{test_id}_logic_error.png")
                pytest.fail(f"Logic Error in UI: {error_text}")
                
        except Exception as e:
            if "Timeout" in str(e):
                page.screenshot(path=f"{SCREENSHOT_DIR}/stress_{test_id}_timeout.png")
                pytest.fail(f"UI Timeout: The agent was too slow to answer.")
            raise e

        # Capture success screenshot
        page.screenshot(path=f"{SCREENSHOT_DIR}/stress_{test_id}_success.png")
        
        # Allow background logging to flush
        time.sleep(2)
        return True
    except Exception as e:
        raise e

# --- DYNAMIC TEST CASES ---
# I will update these cases iteratively as I find loopholes

def test_s1_demographic_filtering(page):
    """S1: Cross-table Demographic Filtering."""
    question = "What is the awareness of Usha among females aged 25-34 in Mumbai?"
    run_stress_test(page, question, "s1")
    # Assertions will be specific to the question
    expect(page.get_by_text("Data Evidence")).to_be_visible()
    # If the logic is wrong, the summary won't mention Mumbai or females accurately
    # expect(page.get_by_text("Mumbai")).to_be_visible()

def test_s2_bq3_integration(page):
    """S2: Statistical Driver (BQ3) Integration."""
    question = "Why is Bajaj losing loyalty in Mixer Grinders?"
    run_stress_test(page, question, "s2")
    # Expect BQ3 tool call trace
    expect(page.get_by_text("bq3")).to_be_visible()

def test_s3_complex_audience_joins(page):
    """S3: Complex Audience Joins (Ownership + NPS)."""
    question = "Compare the NPS of Crompton among people who own Mixer Grinders vs those who own Water Heaters."
    run_stress_test(page, question, "s3")
    expect(page.get_by_text("Data Evidence")).to_be_visible()

def test_s4_multihop_logic(page):
    """S4: Multi-hop Reasoning (Ownership -> NPS)."""
    question = "What is the NPS of the brand that has the highest ownership in the North zone?"
    run_stress_test(page, question, "s4")
    expect(page.get_by_text("Strategic Insight")).to_be_visible()
    # Should at least identify a brand and a number
    expect(page.get_by_text("NPS")).to_be_visible()

def test_s5_killer_logic(page):
    """S5: The Killer Question (Joints + Demographics + Logic)."""
    question = "What is the NPS of the top awareness brand in Mumbai among males?"
    run_stress_test(page, question, "s5")
    expect(page.get_by_text("Strategic Insight")).to_be_visible()
    expect(page.get_by_text("Mumbai").first).to_be_visible()

def test_s6_trend_analysis(page):
    """S6: Trend Analysis (Time-Series)."""
    question = "How has Crompton's NPS changed between April and June?"
    run_stress_test(page, question, "s6")
    expect(page.get_by_text("Strategic Insight")).to_be_visible()
    expect(page.get_by_text("April")).to_be_visible()
    expect(page.get_by_text("June")).to_be_visible()

def test_s7_null_handling(page):
    """S7: Null/Non-existent Entity Handling."""
    question = "What is the awareness of 'SpaceX' in Bangalore?"
    run_stress_test(page, question, "s7")
    expect(page.get_by_text("Strategic Insight")).to_be_visible()
    # Expect graceful "no data" message, not a crash
    expect(page.get_by_text("0")).to_be_visible()

def test_s8_extreme_segmentation(page):
    """S8: Extreme Triple-Join Segmentation."""
    question = "What is the NPS of Bajaj among females aged 18-24 who own a dishwasher in Chennai?"
    run_stress_test(page, question, "s8")
    expect(page.get_by_text("Strategic Insight")).to_be_visible()

def test_s9_mixed_mode_correlation(page):
    """S9: Mixed-Mode Qual-Quant Correlation."""
    question = "What do people who gave Bajaj an NPS of 0-4 say about the brand?"
    run_stress_test(page, question, "s9")
    expect(page.get_by_text("Strategic Insight")).to_be_visible()
    expect(page.get_by_text("Raw Consumer Voices")).to_be_visible()
