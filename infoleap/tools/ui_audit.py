import asyncio
from playwright.async_api import async_playwright
import sys

async def audit_streamlit_ui():
    async with async_playwright() as p:
        # 1. Launch Browser
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        print("[UI AUDIT] Navigating to LENS 2.0 at http://localhost:8503...")
        await page.goto("http://localhost:8503")
        
        # 2. Wait for app to load
        await page.wait_for_selector("input[placeholder='Ask a research question...']")
        print("[UI AUDIT] App Loaded Successfully.")

        # 3. Submit a complex driver question
        test_q = "What actually drives loyalty for Crompton Fans?"
        print(f"[UI AUDIT] Submitting Question: {test_q}")
        await page.fill("input[placeholder='Ask a research question...']", test_q)
        await page.keyboard.press("Enter")

        # 4. Wait for the 'Thinking' process and then the answer
        # Streamlit status blocks usually have 'stStatus' or specific classes
        print("[UI AUDIT] Waiting for Agent Thinking and Math Execution...")
        await page.wait_for_timeout(10000) # Give it 10s for the regression math

        # 5. Extract the rendered Markdown Answer
        # In Streamlit chat, answers are in .stChatMessage
        chat_messages = await page.query_selector_all(".stChatMessage")
        if len(chat_messages) > 1:
            assistant_reply = await chat_messages[-1].inner_text()
            print("\n--- ACTUAL UI OUTPUT CAPTURED ---\n")
            print(assistant_reply)
            print("\n----------------------------------\n")
            
            # 6. Audit Checks
            if "###" in assistant_reply: print("✅ Markdown Headers: Detected.")
            if "TOP-MOST DRIVERS" in assistant_reply: print("✅ Quadrant Logic: Rendered.")
            if "%" in assistant_reply: print("✅ Percentage Calculations: Visible.")
            if "🚀" in assistant_reply or "✅" in assistant_reply: print("✅ Emojis/Visuals: Rendered.")
        else:
            print("❌ Error: Assistant reply not found in UI.")

        # 7. Close
        await browser.close()

if __name__ == "__main__":
    asyncio.run(audit_streamlit_ui())
