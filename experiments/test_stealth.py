import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def test_bot_detection(url, p):
    print(f"\\nTesting Stealth against: {url}")
    browser = await p.chromium.launch(
        headless=False, # Watch it happen
        args=[
            "--disable-blink-features=AutomationControlled", 
            "--no-sandbox",
            "--disable-infobars",
            "--window-size=1920,1080",
        ]
    )
    
    # Advanced stealth headers
    extra_headers = {
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    }

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        extra_http_headers=extra_headers
    )
    
    page = await context.new_page()
    
    # Mask navigator.webdriver
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    # Apply standard stealth
    await Stealth().apply_stealth_async(page)
    
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        print("Page loaded. Waiting 5 seconds to observe any Javascript bot-checks...")
        await page.wait_for_timeout(5000)
        
        # Take a screenshot to verify what the page sees
        filename = f"{url.replace('https://', '').replace('/', '_')}.png"
        await page.screenshot(path=filename)
        print(f"Screenshot saved to {filename}. Check this to see if you passed the bot checks!")
        
    except Exception as e:
        print(f"Error connecting: {e}")
    finally:
        await browser.close()

async def main():
    targets = [
        "https://bot.sannysoft.com",       # Ultimate stealth test
        "https://nowsecure.nl",            # Cloudflare Turnstile test
        "https://www.linkedin.com/jobs/"   # Notorious for aggressive bot walls
    ]
    
    async with async_playwright() as p:
        for target in targets:
            await test_bot_detection(target, p)

if __name__ == "__main__":
    asyncio.run(main())
