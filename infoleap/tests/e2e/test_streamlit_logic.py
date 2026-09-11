import os
import subprocess
import time
import pytest
from playwright.sync_api import sync_playwright, expect

# --- CONFIGURATION ---
STREAMLIT_PORT = "8502"
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
        pytest.fail("Streamlit server failed to start.")
        
    yield
    process.terminate()

def run_interaction(page, question, test_name):
    try:
        # Increase default timeout for headed mode
        page.set_default_timeout(120000)
        
        page.goto(BASE_URL)
        page.wait_for_selector("text=Ask LENS", timeout=120000)
        
        chat_input = page.get_by_placeholder("Enter research question...")
        chat_input.fill(question)
        chat_input.press("Enter")
        
        # Wait for either completion or error
        page.wait_for_selector("text=Strategic Insight", timeout=120000)
    except Exception as e:
        page.screenshot(path=f"{SCREENSHOT_DIR}/{test_name}_failure.png")
        raise e

def test_respondent_count_accuracy(page):
    run_interaction(page, "tell me total unique respondents", "count_accuracy")
    expect(page.get_by_text("6,847")).to_be_visible(timeout=20000)

def test_competitor_ranking_north(page):
    run_interaction(page, "show me the awareness ranking of brands in the North", "ranking_north")
    expect(page.locator(".stPlotlyChart")).to_be_visible(timeout=20000)
    expect(page.locator("text=Bajaj").first).to_be_visible(timeout=10000)

def test_qualitative_evidence_rendering(page):
    run_interaction(page, "what are people saying about Crompton in North zone?", "qual_evidence")
    # Universal Evidence Engine should catch this
    expect(page.get_by_text("Raw Consumer Voices (Evidence)")).to_be_visible(timeout=20000)
    page.get_by_text("Raw Consumer Voices (Evidence)").click()
    expect(page.get_by_text("Source:")).to_have_count(1, timeout=10000)
