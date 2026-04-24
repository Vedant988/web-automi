import asyncio
from playwright.async_api import async_playwright

async def main():
    p = await async_playwright().start()
    b = await p.chromium.launch(headless=True, args=['--remote-debugging-port=9222'])
    print('launched')
    b2 = await p.chromium.connect_over_cdp('http://localhost:9222')
    print(b2.contexts)
    await b.close()
    await p.stop()

asyncio.run(main())
