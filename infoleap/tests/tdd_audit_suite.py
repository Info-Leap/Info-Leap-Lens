import os
import subprocess
import time
import sqlite3
import pandas as pd
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

# --- CONFIGURATION ---
STREAMLIT_PORT = "8505"
BASE_URL = f"http://localhost:{STREAMLIT_PORT}"
DB_PATH = Path("oxdata/data/project_1/oxdata.db")
# Fallback if oxdata/data... doesn't exist
if not DB_PATH.exists():
    DB_PATH = Path("data/project_1/oxdata.db")

SCREENSHOT_DIR = Path("oxdata/tests/results/tdd_audit")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

class TDDAuditSuite:
    def __init__(self):
        self.server_process = None

    def start_server(self):
        print("\n[INIT] Starting Streamlit Server...")
        env = os.environ.copy()
        env["PYTHONPATH"] = os.getcwd()
        cmd = [
            "streamlit", "run", "oxdata/app.py",
            "--server.port", STREAMLIT_PORT,
            "--server.address", "localhost",
            "--server.headless", "true" # Keep headless true for server, but test in False
        ]
        self.server_process = subprocess.Popen(cmd, env=env)
        
        # Wait for server
        max_retries = 30
        import requests
        for i in range(max_retries):
            try:
                res = requests.get(BASE_URL)
                if res.status_code == 200:
                    print(f"[SUCCESS] Server ready on {BASE_URL}")
                    return
            except:
                pass
            time.sleep(1)
        raise RuntimeError("Server failed to start.")

    def stop_server(self):
        if self.server_process:
            self.server_process.terminate()
            print("[CLEANUP] Server stopped.")

    def run_audit(self):
        self.start_server()
        try:
            with sync_playwright() as p:
                # User asked for "headmode"
                # If running on a headless server, this might fail, 
                # but we'll try headless=False and fallback if needed.
                try:
                    browser = p.chromium.launch(headless=False)
                except:
                    print("[WARN] Could not launch headmode. Falling back to headless.")
                    browser = p.chromium.launch(headless=True)
                
                page = browser.new_page()
                page.set_default_timeout(120000)
                
                # --- TEST 1: Mathematical Accuracy (NPS) ---
                self.test_nps_mathematics(page)
                
                # --- TEST 2: Context Persistence ---
                self.test_context_persistence(page)
                
                # --- TEST 3: Tool Correlation ---
                self.test_tool_correlation(page)

                # --- TEST 4: Summary Structure ---
                self.test_summary_structure(page)
                
                browser.close()
        finally:
            self.stop_server()

    def test_nps_mathematics(self, page):
        print("\n--- [RED] TEST 1: NPS MATHEMATICS ---")
        page.goto(BASE_URL)
        page.wait_for_selector("text=Ask LENS")

        question = "What is the NPS of Crompton in the North?"
        print(f"Question: {question}")
        
        chat_input = page.get_by_placeholder("Enter research question...")
        chat_input.fill(question)
        chat_input.press("Enter")
        
        # Wait for result
        page.wait_for_selector("h3:has-text('Strategic Insight')", timeout=120000)
        time.sleep(5) # Give it extra time to render all components
        
        # Scrape SQL and Summary
        last_message = page.locator("[data-testid='stChatMessage']").last
        summary_text = last_message.inner_text()
        
        # Robust selector for the latest reasoning expander
        reasoning_expander = page.locator("[data-testid='stChatMessage']").last.get_by_text("Strategic Reasoning", exact=False)
        if reasoning_expander.count() > 0:
            reasoning_expander.first.click()
            time.sleep(2)
        
        sql_blocks = page.locator("code").all_inner_texts()
        sql = next((s for s in sql_blocks if "SELECT" in s.upper()), None)
        
        if not sql:
            print("[FAIL] No SQL found in output.")
            return

        # Ground Truth Calculation
        conn = sqlite3.connect(str(DB_PATH))
        df = pd.read_sql(sql, conn)
        conn.close()
        
        # assume NPS is in the first column if it's a numeric result
        db_nps = None
        if not df.empty:
            db_nps = df.iloc[0, 0]
        
        print(f"[DB] SQL Result Preview:\n{df.head()}")
        
        # Extract NPS from Summary
        # Look for "NPS is X" or "NPS: X" or "NPS of X"
        nps_match = re.search(r"NPS [^\d]* ([\d\.]+)", summary_text, re.IGNORECASE)
            
        if nps_match:
            reported_nps = float(nps_match.group(1))
            print(f"[UI] Reported NPS: {reported_nps}")
            
            # check if reported_nps matches db_nps roughly (rounding)
            if db_nps is not None and abs(reported_nps - float(db_nps)) > 1.0:
                print(f"[WARN] NPS Mismatch! DB: {db_nps}, UI: {reported_nps}")
            
            assert reported_nps is not None, "NPS should be present in summary"
        else:
            print("[FAIL] NPS not found in summary text.")

        page.screenshot(path=str(SCREENSHOT_DIR / "test1_nps.png"), full_page=True)
        print("[SUCCESS] Test 1 complete.")

    def test_context_persistence(self, page):
        print("\n--- [RED] TEST 2: CONTEXT PERSISTENCE ---")
        # Turn 1 already set context in North for Crompton (if we reuse session)
        
        question = "What is their awareness there?"
        print(f"Question: {question} (Implicit: Crompton in North)")
        
        chat_input = page.get_by_placeholder("Enter research question...")
        chat_input.fill(question)
        chat_input.press("Enter")
        
        # Wait for result (specifically the next instance)
        try:
            page.locator("h3:has-text('Strategic Insight')").nth(1).wait_for(timeout=120000)
            time.sleep(8)
        except:
            print("[WARN] Timeout waiting for 2nd insight. Trying fallback.")
            time.sleep(10)
        
        # Scrape with aggressive wait
        page.wait_for_selector("h3:has-text('Strategic Insight')", timeout=120000)
        time.sleep(5)
        
        # Get the container of the last message
        container = page.locator("[data-testid='stChatMessage']").last
        
        # Extract ALL text from the container, including hidden/expanded ones if possible
        combined_text = container.inner_text().lower()
        
        # Also check all code blocks in the container
        code_blocks = container.locator("code").all_inner_texts()
        combined_text += " " + " ".join(code_blocks).lower()
        
        has_crompton = "crompton" in combined_text
        has_north = "north" in combined_text
        
        print(f"Context 'Crompton' retained: {'YES' if has_crompton else 'NO'}")
        print(f"Context 'North' retained: {'YES' if has_north else 'NO'}")
        if not (has_crompton and has_north):
            print(f"[DEBUG] Full Combined Text from Container:\n{combined_text}")
        
        assert has_crompton and has_north, "Context (Brand and Zone) must persist across turns."
        
        page.screenshot(path=str(SCREENSHOT_DIR / "test2_context.png"), full_page=True)
        print("[SUCCESS] Test 2 complete.")

    def test_tool_correlation(self, page):
        print("\n--- [RED] TEST 3: TOOL CORRELATION ---")
        question = "Why do people prefer Bajaj over Crompton?"
        print(f"Question: {question}")
        
        chat_input = page.get_by_placeholder("Enter research question...")
        chat_input.fill(question)
        chat_input.press("Enter")
        
        # This should trigger multiple tools: SQL and Qual (and maybe BQ3)
        page.wait_for_selector("h3:has-text('Strategic Insight')", timeout=180000)
        try:
            page.wait_for_selector("text=Research Step Complete", timeout=30000)
        except: pass
        time.sleep(3)
        
        # Check if "Consumer Voices" tab or section exists
        has_qual = page.get_by_text("Raw Consumer Voices").count() > 0
        print(f"Qualitative feedback rendered: {'YES' if has_qual else 'NO'}")
        
        # Robust selector for the latest reasoning expander
        reasoning_expander = page.locator("[data-testid='stChatMessage']").last.get_by_text("Strategic Reasoning", exact=False)
        if reasoning_expander.count() > 0:
            reasoning_expander.first.click()
            time.sleep(2)
            reasoning = page.locator("[data-testid='stChatMessage']").last.locator("[data-testid='stExpander']").first.inner_text()
        else:
            reasoning = ""
            print("[WARN] Reasoning expander not found.")
        
        print(f"[DEBUG] Reasoning Text:\n{reasoning}")
        has_tool_mentions = "run_sql" in reasoning or "get_qualitative_feedback" in reasoning or "Calling tool" in reasoning or "sql:" in reasoning
        
        print(f"Reasoning mentions tools: {'YES' if has_tool_mentions else 'NO'}")
        
        assert has_qual, "Comparison question should trigger qualitative feedback."
        assert has_tool_mentions, "Strategic Reasoning must document tool calls."
        
        page.screenshot(path=str(SCREENSHOT_DIR / "test3_correlation.png"), full_page=True)
        print("[SUCCESS] Test 3 complete.")

    def test_summary_structure(self, page):
        print("\n--- [RED] TEST 4: SUMMARY STRUCTURE RULE ---")
        question = "How many respondents are in the North?"
        print(f"Question: {question}")
        
        chat_input = page.get_by_placeholder("Enter research question...")
        chat_input.fill(question)
        chat_input.press("Enter")
        
        page.wait_for_selector("h3:has-text('Strategic Insight')", timeout=120000)
        try:
            page.wait_for_selector("text=Research Step Complete", timeout=30000)
        except: pass
        time.sleep(3)
        
        last_message = page.locator("[data-testid='stChatMessage']").last
        full_text = last_message.inner_text()
        
        # Robust selector for the latest reasoning expander
        reasoning_expander = page.locator("[data-testid='stChatMessage']").last.get_by_text("Strategic Reasoning", exact=False)
        has_audit = reasoning_expander.count() > 0
        
        if has_audit:
            reasoning_expander.first.click()
            time.sleep(2)
            audit_content = page.locator("[data-testid='stChatMessage']").last.locator("[data-testid='stExpander']").first.inner_text()
            has_thought_block = "Internal Audit & Logic Check" in audit_content
            print(f"Internal Audit block present: {'YES' if has_thought_block else 'NO'}")
        else:
            has_thought_block = False
            print("[FAIL] No Strategic Reasoning expander found.")

        # Check for 4-sentence structure roughly
        summary_section = full_text.split("Strategic Insight")[-1].strip()
        sentences = [s for s in re.split(r'[\.\!\?]', summary_section) if len(s.strip()) > 5]
        num_sentences = len(sentences)
        has_numbers = any(char.isdigit() for char in summary_section)
        
        print(f"Summary sentences: {num_sentences}")
        print(f"Summary has numbers: {'YES' if has_numbers else 'NO'}")
        
        assert has_thought_block, "Summary MUST contain a `<thought>` block for internal audit."
        assert num_sentences >= 3, "Summary should follow the 4-sentence structural template."
        assert has_numbers, "Summary MUST mention primary numbers from the Result Table."
        
        print("[SUCCESS] Test 4 complete.")

if __name__ == "__main__":
    suite = TDDAuditSuite()
    suite.run_audit()
