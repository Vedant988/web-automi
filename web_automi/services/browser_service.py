"""
web_automi/services/browser_service.py
--------------------------------------
Playwright concrete implementation of the IBrowserService interface.
"""

import os
import re
import sys
import gc
import base64
import asyncio
from urllib.parse import urlparse, urljoin, quote_plus, unquote, parse_qs
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from web_automi.core.interfaces import IBrowserService
from web_automi.core.exceptions import BrowserError
from web_automi.core.config import (
    VLM_MODEL,
    _SUMMARIZER_TRUNCATE_FALLBACK,
    build_client
)

try:
    from playwright_stealth import Stealth as _Stealth
    _STEALTH_INSTANCE = _Stealth()
    _STEALTH_AVAILABLE = True
except Exception as _stealth_err:
    _STEALTH_INSTANCE = None
    _STEALTH_AVAILABLE = False
    print(
        f"[browser-use] [WARN] playwright-stealth unavailable ({_stealth_err}); running without stealth",
        file=sys.stderr, flush=True,
    )

async def _apply_stealth(page) -> None:
    if _STEALTH_AVAILABLE and _STEALTH_INSTANCE is not None:
        try:
            await _STEALTH_INSTANCE.apply_stealth_async(page)
        except Exception:
            pass

SEARCH_ENGINES = [
    {
        "name": "duckduckgo",
        "url": "https://html.duckduckgo.com/html/?q={query}",
        "selectors": {
            "container": ".result",
            "title": "a.result__a",
            "link": "a.result__a",
            "snippet": ".result__snippet",
        },
    },
    {
        "name": "bing",
        "url": "https://www.bing.com/search?q={query}",
        "selectors": {
            "container": "li.b_algo",
            "title": "h2 a",
            "link": "h2 a",
            "snippet": ".b_caption p",
        },
    },
    {
        "name": "google",
        "url": "https://www.google.com/search?q={query}&hl=en",
        "selectors": {
            "container": "div.g, div.MjjYud",
            "title": "h3",
            "link": "a",
            "snippet": "div.VwiC3b, div.yXK7lf, span.aCOpRe, .MUxGbd",
        },
    },
]

SELECTED_CHROME_PROFILE = None

async def setup_memory_saving_routes(page) -> None:
    tracker_keywords = (
        "google-analytics", "doubleclick", "adsystem", "adsense", 
        "analytics", "tracker", "facebook.net", "facebook.com/tr", 
        "pixel", "hotjar", "mixpanel", "amplitude", "segment.io"
    )
    
    async def route_handler(route):
        try:
            req = route.request
            res_type = req.resource_type
            url = req.url.lower()
            if res_type in ("image", "media", "font"):
                await route.abort()
                return
            if any(kw in url for kw in tracker_keywords):
                await route.abort()
                return
            await route.continue_()
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass
            
    try:
        await page.route("**/*", route_handler)
    except Exception as exc:
        print(f"[browser-use] [WARN] Failed to setup memory routing: {exc}", file=sys.stderr, flush=True)

def normalize_whitespace(text: str, limit: int | None = None) -> str:
    cleaned = "\n".join(line.strip() for line in (text or "").splitlines() if line.strip())
    if limit is not None:
        return cleaned[:limit]
    return cleaned

async def extract_page_text(page, timeout_ms: int = 4000, limit: int = 6000) -> str:
    try:
        body_text = await page.locator("body").inner_text(timeout=timeout_ms)
        normalized = normalize_whitespace(body_text, limit=limit)
        if normalized:
            return normalized
    except Exception:
        pass

    try:
        body_text = await page.evaluate("() => (document.body || document.documentElement).innerText || ''")
        normalized = normalize_whitespace(body_text, limit=limit)
        if normalized:
            return normalized
    except Exception:
        pass

    try:
        body_text = await page.evaluate(
            """
            () => Array.from(document.querySelectorAll('main, article, section, h1, h2, h3, p, li'))
                .slice(0, 120)
                .map(node => (node.innerText || node.textContent || '').trim())
                .filter(Boolean)
                .join('\\n')
            """
        )
        return normalize_whitespace(body_text, limit=limit)
    except Exception:
        return ""

def looks_like_blocked_page(title: str, body_text: str) -> bool:
    signals = (
        "unusual traffic", "verify you are human", "detected unusual traffic",
        "press and hold", "captcha", "not a robot", "automated queries",
        "access denied", "temporarily blocked", "checking your browser",
        "just a moment", "enable javascript and cookies", "ddos protection by cloudflare",
        "ray id", "403 forbidden", "429 too many requests"
    )
    haystack = f"{title}\n{body_text}".lower()
    return any(signal in haystack for signal in signals)

def force_kill_browser():
    try:
        import psutil
        killed_count = 0
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = (proc.info['name'] or '').lower()
                if 'chrome' in name or 'chromium' in name:
                    proc.kill()
                    killed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception:
        pass

async def get_browser_and_page(p, is_search=False):
    extra_headers = {
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        # Advanced contexts isolation: always create a fresh, clean context
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_extra_http_headers(extra_headers)
        return browser, context, page, True
    except Exception:
        force_kill_browser()
        is_headless = bool(os.environ.get("HEADLESS"))
        
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-popup-blocking",
            "--window-size=1280,720",
            "--disable-extensions",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-gpu-sandbox",
            "--disable-setuid-sandbox",
            "--js-flags=--max-old-space-size=256",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-breakpad",
            "--disable-client-side-phishing-detection",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-domain-reliability",
            "--disable-features=AudioServiceOutOfProcess,IsolateOrigins,site-per-process",
            "--disable-ipc-flooding-protection",
            "--disable-print-preview",
            "--disable-prompt-on-repost",
            "--disable-renderer-backgrounding",
            "--disable-sync",
            "--mute-audio",
            "--no-first-run",
            "--no-default-browser-check",
            "--metrics-recording-only",
        ]

        kwargs = {
            "headless": is_headless,
            "args": args
        }
        browser = await p.chromium.launch(**kwargs)
        context_kwargs = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "locale": "en-US",
            "viewport": {"width": 1280, "height": 720}
        }
        if extra_headers:
            context_kwargs["extra_http_headers"] = extra_headers
        
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        return browser, context, page, False

async def cleanup_browser(browser_or_none, context, page, is_remote):
    try:
        if page:
            await page.close()
    except Exception:
        pass
    try:
        if is_remote:
            if context:
                await context.close()
        else:
            if context:
                await context.close()
            if browser_or_none:
                await browser_or_none.close()
    except Exception:
        pass


class PlaywrightBrowserService(IBrowserService):
    """Playwright Browser Service implementing low-memory scraping and safe teardown."""

    async def search_web(self, query: str, timeout: int = 90) -> str:
        try:
            async def search_with_browser():
                async with async_playwright() as p:
                    browser_or_none, context, page, is_remote = await get_browser_and_page(p, is_search=True)
                    await setup_memory_saving_routes(page)
                    
                    try:
                        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                        await _apply_stealth(page)

                        notes = []
                        _vlm_client = build_client()

                        for engine in SEARCH_ENGINES:
                            search_url = engine["url"].format(query=quote_plus(query))
                            try:
                                await page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
                                await page.wait_for_timeout(1200)
                            except Exception as exc:
                                notes.append(f"{engine['name']}: navigation failed ({exc})")
                                continue

                            title = ""
                            try:
                                title = await page.title()
                            except Exception:
                                pass
                                
                            normalized_body = await extract_page_text(page, timeout_ms=2500, limit=5000)
                            if looks_like_blocked_page(title, normalized_body):
                                notes.append(f"{engine['name']}: blocked or anti-bot detected")
                                continue

                            # Extract search results logic here
                            script = """
                            ({ selectors, limit }) => {
                                const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                                return Array.from(document.querySelectorAll(selectors.container))
                                    .slice(0, 5)
                                    .map((node) => {
                                        const titleNode = node.querySelector(selectors.title);
                                        const linkNode = node.querySelector(selectors.link);
                                        const snippetNode = node.querySelector(selectors.snippet);
                                        return {
                                            title: clean(titleNode ? titleNode.textContent : ''),
                                            url: linkNode ? (linkNode.href || linkNode.getAttribute('href') || '') : '',
                                            snippet: clean(snippetNode ? snippetNode.textContent : ''),
                                        };
                                    })
                                    .filter((item) => item.title || item.url || item.snippet);
                            }
                            """
                            raw_results = await page.evaluate(script, {"selectors": engine["selectors"]})
                            results = []
                            for r in raw_results:
                                results.append({
                                    "title": r.get("title", "").strip() or "(untitled)",
                                    "url": r.get("url", "").strip(),
                                    "snippet": r.get("snippet", "").strip()
                                })

                            # Visited pages logic
                            visited_pages = []
                            for res in results[:2]:
                                if not res.get("url", "").startswith("http"):
                                    continue
                                page_visit = await context.new_page()
                                try:
                                    await _apply_stealth(page_visit)
                                    await page_visit.goto(res["url"], timeout=30000, wait_until="domcontentloaded")
                                    await page_visit.wait_for_timeout(800)
                                    vt = (await page_visit.title()).strip() or res["title"]
                                    ve = await extract_page_text(page_visit, timeout_ms=2500, limit=600)
                                    
                                    if looks_like_blocked_page(vt, ve):
                                        snippet = res.get("snippet", "").strip()
                                        if snippet:
                                            visited_pages.append({
                                                "title": res["title"],
                                                "url": res["url"],
                                                "excerpt": f"[SERP snippet fallback] {snippet}"
                                            })
                                    else:
                                        visited_pages.append({
                                            "title": vt,
                                            "url": res["url"],
                                            "excerpt": ve
                                        })
                                except Exception:
                                    snippet = res.get("snippet", "").strip()
                                    if snippet:
                                        visited_pages.append({
                                            "title": res["title"],
                                            "url": res["url"],
                                            "excerpt": f"[SERP snippet fallback – nav error] {snippet}"
                                        })
                                finally:
                                    await page_visit.close()

                            if results or visited_pages:
                                payload = [
                                    f"Search query: {query}",
                                    f"Search engine used: {engine['name']}",
                                    "Search engine page content:\n" + normalized_body
                                ]
                                if results:
                                    payload.append("Top search results:")
                                    for idx, r in enumerate(results, start=1):
                                        payload.append(f"{idx}. {r['title']}\n   URL: {r['url']}\n   Snippet: {r['snippet']}")
                                if visited_pages:
                                    payload.append("Visited result pages:")
                                    for p_vis in visited_pages:
                                        payload.append(f"- {p_vis['title']}\n  URL: {p_vis['url']}\n  Excerpt: {p_vis['excerpt']}")
                                return "\n".join(payload)

                        return "No search engine returned usable results."
                    finally:
                        await cleanup_browser(browser_or_none, context, page, is_remote)

            result = await asyncio.wait_for(search_with_browser(), timeout=timeout)
            gc.collect()
            return result
        except Exception as err:
            raise BrowserError(f"Automated web search failed: {err}")

    async def navigate_url(
        self,
        url: str,
        input_text: str = "",
        input_selector: str = "",
        click_selector: str = "",
        timeout: int = 60
    ) -> str:
        try:
            async def _run():
                async with async_playwright() as p:
                    browser_or_none, context, page, is_remote = await get_browser_and_page(p, is_search=False)
                    await setup_memory_saving_routes(page)
                    try:
                        await _apply_stealth(page)
                        await page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                        await page.wait_for_timeout(2000)

                        # Dismiss popups
                        selectors = [
                            "button[aria-label*='close' i]",
                            "button[aria-label*='dismiss' i]",
                            "[class*='close-button' i]",
                            "[class*='modal-close' i]",
                            "button:has-text('✕')",
                            "button:has-text('✖')"
                        ]
                        for sel in selectors:
                            try:
                                els = await page.locator(sel).all()
                                for el in els:
                                    if await el.is_visible():
                                        await el.click(timeout=1000)
                                        await page.wait_for_timeout(500)
                            except Exception:
                                pass

                        if input_text and input_selector:
                            try:
                                await page.locator(input_selector).first.fill(input_text, timeout=5000)
                            except Exception:
                                await page.keyboard.type(input_text)

                        if click_selector:
                            try:
                                await page.locator(click_selector).first.click(timeout=5000)
                                await page.wait_for_timeout(3000)
                            except Exception:
                                pass

                        title = (await page.title()).strip()
                        page_content = await extract_page_text(page, timeout_ms=3000, limit=6000)

                        if looks_like_blocked_page(title, page_content):
                            return f"Page loaded but appears blocked.\nTitle: {title}"
                        return f"Page title: {title}\n\n{page_content}"
                    finally:
                        await cleanup_browser(browser_or_none, context, page, is_remote)

            result = await asyncio.wait_for(_run(), timeout=timeout + 10)
            gc.collect()
            return result
        except Exception as err:
            raise BrowserError(f"URL navigation failed for {url}: {err}")
