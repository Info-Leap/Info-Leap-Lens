import os
import subprocess
import time
import pytest
from playwright.sync_api import sync_playwright, expect

# --- CONFIGURATION ---
STREAMLIT_PORT = "8504" 
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
        pytest.fail("Streamlit server failed to start for Session Audit.")
        
    yield
    process.terminate()

def chat_turn(page, question, expected_text=None, timeout=120000):
    """Performs a single chat turn and validates the result."""
    print(f"\n[TURN] Question: {question}")
    
    chat_input = page.get_by_placeholder("Enter research question...")
    chat_input.fill(question)
    chat_input.press("Enter")
    
    # 1. Wait for completion
    try:
        page.wait_for_selector("h3:has-text('Strategic Insight')", timeout=timeout)
    except Exception as e:
        page.screenshot(path=f"{SCREENSHOT_DIR}/session_timeout_{int(time.time())}.png")
        # Check for error box instead
        error_box = page.locator("[data-testid='stNotificationContentError']").first
        if error_box.is_visible():
            pytest.fail(f"Logic Error in turn: {error_box.inner_text()}")
        raise e

    # 2. Extract last message content for validation
    # This avoids strict mode violations by only looking at the latest response
    last_response = page.locator("[data-testid='stChatMessage']").last
    
    if expected_text:
        expect(last_response).to_contain_text(expected_text, timeout=10000)
    
    print(f"[SUCCESS] Turn completed.")
    return last_response

def test_deep_session_audit(page):
    """
    Scenario: Multi-turn research conversation.
    Objective: Validate continuity, context resolution, and UI consistency.
    """
    page.set_default_timeout(120000)
    page.goto(BASE_URL)
    page.wait_for_selector("text=Ask LENS")

    # TURN 1: Context Setting
    chat_turn(page, "What is the NPS of Crompton in the North?", expected_text="NPS")
    
    # TURN 2: Continuity (Resolves "its" and "there")
    # "its" -> Crompton, "there" -> North
    chat_turn(page, "Now show me its competition there.", expected_text="Bajaj")
    
    # TURN 3: Deep Dive (Triggers BQ3 on the resolved brand)
    # Note: BQ3 turn has longer timeout
    chat_turn(page, "Why is Bajaj leading in the North?", timeout=180000)
    
    # TURN 4: Qual Correlation (Universal Evidence)
    chat_turn(page, "Show me the actual feedback for the leaders.", expected_text="Raw Consumer Voices")
    
    # Final Screenshot of the entire session
    page.screenshot(path=f"{SCREENSHOT_DIR}/full_session_audit_final.png", full_page=True)
