import asyncio
import time
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

ENGINES = {
    "google": "https://www.google.com/search?q={query}",
    "duckduckgo": "https://html.duckduckgo.com/html/?q={query}",
    "bing": "https://www.bing.com/search?q={query}"
}

QUERY = "AI internship bangalore 2027"

async def test_engine(engine_name, url_template, p):
    print(f"\\n[{engine_name}] Launching test...")
    browser = await p.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080}
    )
    page = await context.new_page()
    await Stealth().apply_stealth_async(page)
    
    url = url_template.format(query=QUERY.replace(" ", "+"))
    
    start_time = time.time()
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        end_time = time.time()
        
        status = response.status if response else "Unknown"
        title = await page.title()
        
        # Check for common bot walls
        body_text = await page.locator("body").inner_text(timeout=5000)
        is_blocked = "verify you are human" in body_text.lower() or "captcha" in body_text.lower() or "unusual traffic" in body_text.lower()
        
        print(f"[{engine_name}] Status Code: {status}")
        print(f"[{engine_name}] Page Title: {title}")
        print(f"[{engine_name}] Time Taken: {end_time - start_time:.2f}s")
        if is_blocked:
            print(f"[{engine_name}] 🚨 BLOCKED / CAPTCHA DETECTED!")
        else:
            print(f"[{engine_name}] ✅ Clean load (No obvious bot wall).")
            
    except Exception as e:
        print(f"[{engine_name}] ❌ Error: {e}")
    finally:
        await browser.close()

async def main():
    async with async_playwright() as p:
        for name, url_template in ENGINES.items():
            await test_engine(name, url_template, p)

if __name__ == "__main__":
    asyncio.run(main())
