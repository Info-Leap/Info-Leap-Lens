import asyncio
from playwright.async_api import async_playwright

async def audit_buttons():
    print("[*] Starting System-Wide Button & Routing Audit...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto("http://localhost:8501")
        await page.wait_for_load_state("networkidle")
        
        # 1. Test Sidebar Navigation
        nav_items = ["Home", "Ask Pulse", "Investigations", "Signals", "Brand Health", "Quote Explorer", "Repository", "Settings"]
        for item in nav_items:
            print(f"    -> Testing Link: {item}")
            try:
                await page.get_by_role("link", name=item).click()
                await asyncio.sleep(1)
                # Check for 404 or specific header
                title = await page.title()
                header = await page.locator('h1, h2, h3').first.inner_text()
                print(f"       [SUCCESS] Navigated to: {header[:30]}")
            except Exception as e:
                print(f"       [FAILURE] Routing for {item} failed: {e}")

        # 2. Test Dashboard 'Open Ask' Button
        print("    -> Testing Dashboard 'Open Ask' Button")
        await page.get_by_role("link", name="Home").click()
        try:
            await page.get_by_role("button", name="Open Ask").click()
            await asyncio.sleep(1)
            header = await page.locator('h1').first.inner_text()
            if "Ask Pulse" in header:
                print("       [SUCCESS] 'Open Ask' redirected correctly.")
            else:
                print(f"       [FAILURE] Redirected to wrong page: {header}")
        except Exception as e:
             print(f"       [FAILURE] Button error: {e}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(audit_buttons())
