import sys
import time
from playwright.sync_api import sync_playwright

def run_audit():
    with sync_playwright() as p:
        # Launch headed mode with fallback to headless
        print("[*] Launching browser...")
        try:
            browser = p.chromium.launch(headless=False)
        except Exception as e:
            print(f"Headed mode failed ({e}), falling back to headless.")
            browser = p.chromium.launch(headless=True)
            
        page = browser.new_page()
        print("[*] Navigating to http://localhost:8501...")
        try:
            # Note: User said 'single port', so I'll try 8501 (default) or 8505 if 8501 is down.
            # I will use 8501 as the primary.
            page.goto('http://localhost:8501', wait_until='networkidle', timeout=15000)
        except Exception as e:
            print(f"[!] Failed to load page: {e}. Trying port 8505...")
            try:
                page.goto('http://localhost:8505', wait_until='networkidle', timeout=15000)
            except Exception as e2:
                print(f"[!] Failed to load page on port 8505: {e2}")
                return
            
        print("\n--- DASHBOARD AUDIT ---")
        page.wait_for_timeout(3000)
        
        try:
            hero_insight = page.locator('.hero-insight').inner_text(timeout=5000)
            print(f"Hero Insight Scraped:\n{hero_insight}\n")
            
            # Check scoreboards
            scoreboard_count = page.locator('.scoreboard-bar-container').count()
            print(f"Found {scoreboard_count} scoreboard bars on Dashboard.")
        except Exception as e:
            print(f"[!] Error during Dashboard scraping: {e}")

        print("\n--- ASK PULSE AUDIT ---")
        try:
            # Navigate to Ask Pulse via sidebar role link
            page.get_by_role("link", name="Ask Pulse").click()
            page.wait_for_timeout(2000)
            
            # Input query
            chat_input = page.locator('textarea[aria-label="Ask about market trends, brand health, or consumer feedback..."]')
            # If textarea fails, try input
            if chat_input.count() == 0:
                 chat_input = page.locator('input[aria-label="Ask about market trends, brand health, or consumer feedback..."]')

            query = "Why did Preethi NPS drop 6 points?"
            chat_input.fill(query)
            chat_input.press("Enter")
            print(f"Asked strategic question: '{query}'")
            
            # Wait for response (look for the answer header)
            start_time = time.time()
            print("[*] Waiting for Pulse agent to generate answer (max 90s)...")
            page.locator('h3:has-text("Pulse just answered")').wait_for(timeout=90000)
            latency = time.time() - start_time
            print(f"Agent response latency: {latency:.2f} seconds.")
            
            # Scrape the answer text
            answer_text = page.locator('div[style*="background-color: rgb(240, 253, 244)"]').last.inner_text()
            print(f"\nFinal Pulse Answer Scraped:\n{answer_text}\n")
            
            # Scrape Evidence
            evidence_chips = page.locator('.chip').all_inner_texts()
            print(f"Sources/Chips cited: {evidence_chips}")
            
        except Exception as e:
            print(f"[!] Error during Ask Pulse interaction: {e}")

        browser.close()
        print("[*] Audit complete.")

if __name__ == "__main__":
    run_audit()
