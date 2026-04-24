import os
import re
import sys
import json
import time
import asyncio
import base64
from urllib.parse import urlparse, urljoin, quote_plus, unquote, parse_qs
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# playwright-stealth: imported once at module level so any import error is
# reported a single time rather than once per page visit.
# v2 API: Stealth().apply_stealth_async(page)  (no longer stealth_async)
# ---------------------------------------------------------------------------
try:
    from playwright_stealth import Stealth as _Stealth
    _STEALTH_INSTANCE = _Stealth()
    _STEALTH_AVAILABLE = True
except Exception as _stealth_err:
    _STEALTH_INSTANCE = None
    _STEALTH_AVAILABLE = False
    print(
        f"[browser-use] [WARN] playwright-stealth unavailable ({_stealth_err}); "
        "running without stealth",
        file=sys.stderr,
        flush=True,
    )


async def _apply_stealth(page) -> None:
    """Apply stealth patches to a Playwright page if the library is available."""
    if _STEALTH_AVAILABLE and _STEALTH_INSTANCE is not None:
        try:
            await _STEALTH_INSTANCE.apply_stealth_async(page)
            print(
                "[browser-use] [INFO] Stealth patches applied (navigator.webdriver masked)",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            print(
                f"[browser-use] [WARN] stealth patch failed: {exc}",
                file=sys.stderr,
                flush=True,
            )



SEARCH_ENGINES = [
    {
        # DuckDuckGo HTML-only: zero IP-based rate limiting, zero bot detection.
        # Google and Bing both block scrapers at the IP level regardless of stealth
        # tricks — DDG never does. Always try first for reliable results.
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
        # Bing: moderate bot detection — works sometimes on residential IPs.
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
        # Google: freshest data and sports widgets but aggressive IP-level blocking.
        # Kept as last-resort — will succeed on some IPs/networks.
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

def select_chrome_profile():
    import json
    import os
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data: return None
    user_data_dir = os.path.join(local_app_data, "Google", "Chrome", "User Data")
    local_state_path = os.path.join(user_data_dir, "Local State")
    if not os.path.exists(local_state_path): return None
    
    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        profiles = data.get("profile", {}).get("info_cache", {})
        if not profiles: return None
        
        print("\nAvailable Chrome Profiles:")
        profile_list = list(profiles.items())
        for i, (profile_dir, info) in enumerate(profile_list):
            name = info.get("name", "Unknown")
            email = info.get("user_name", "No Email")
            print(f"[{i+1}] {name} ({email})")
        print(f"[{len(profile_list)+1}] Use isolated temporary browser (Default)")
        
        choice = input(f"\nSelect a profile to use for automation (1-{len(profile_list)+1}): ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(profile_list):
                selected_dir = profile_list[idx][0]
                print(f"Selected profile: {selected_dir}\nIMPORTANT: Make sure all Chrome windows for this profile are closed!\n")
                return {"user_data_dir": user_data_dir, "profile_directory": selected_dir}
    except Exception as e:
        print(f"Error reading Chrome profiles: {e}")
    print("Using isolated temporary browser.\n")
    return None

BLOCKED_PAGE_SIGNALS = (
    "unusual traffic",
    "verify you are human",
    "detected unusual traffic",
    "press and hold",
    "captcha",
    "not a robot",
    "automated queries",
    "access denied",
    "temporarily blocked",
    # Cloudflare / generic bot-wall signals
    "checking your browser",
    "just a moment",
    "enable javascript and cookies",
    "ddos protection by cloudflare",
    "ray id",
    "403 forbidden",
    "429 too many requests",
    "blocked",
)

SEARCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SELECTED_CHROME_PROFILE = None

# Vision model used by the Set-of-Mark visual navigation agent.
VLM_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"


def normalize_whitespace(text: str, limit: int | None = None) -> str:
    cleaned = "\n".join(line.strip() for line in (text or "").splitlines() if line.strip())
    if limit is not None:
        return cleaned[:limit]
    return cleaned


def looks_like_blocked_page(title: str, body_text: str) -> bool:
    haystack = f"{title}\n{body_text}".lower()
    return any(signal in haystack for signal in BLOCKED_PAGE_SIGNALS)


def normalize_search_result_url(raw_url: str) -> str:
    if not raw_url:
        return ""

    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)

    for key in ("uddg", "q", "url"):
        if key in query and query[key]:
            return unquote(query[key][0])

    if "u" in query and query["u"]:
        encoded = query["u"][0]
        if encoded.startswith("a1"):
            encoded = encoded[2:]
        padding = "=" * (-len(encoded) % 4)
        try:
            decoded = base64.b64decode(encoded + padding).decode("utf-8")
            if decoded.startswith("http://") or decoded.startswith("https://"):
                return decoded
        except Exception:
            pass

    if raw_url.startswith("//"):
        return "https:" + raw_url

    return raw_url


def format_search_payload(
    query: str,
    engine_name: str,
    results: list[dict],
    page_excerpt: str,
    visited_pages: list[dict] | None = None,
    notes: list[str] | None = None,
) -> str:
    lines = [
        f"Search query: {query}",
        f"Search engine used: {engine_name}",
    ]

    # SERP body goes FIRST — it contains inline widgets (live scores, weather,
    # knowledge panels) that are more authoritative than individual result links.
    if page_excerpt:
        lines.append("Search engine page content (most authoritative — read this first):")
        lines.append(page_excerpt)

    if notes:
        lines.append("Notes:")
        for note in notes:
            lines.append(f"- {note}")

    if results:
        lines.append("Top search results:")
        for index, result in enumerate(results, start=1):
            lines.append(f"{index}. {result['title']}")
            lines.append(f"   URL: {result['url']}")
            if result["snippet"]:
                lines.append(f"   Snippet: {result['snippet']}")
    else:
        lines.append("Top search results: none extracted")

    if visited_pages:
        lines.append("Visited result pages:")
        for page in visited_pages:
            lines.append(f"- {page['title']}")
            lines.append(f"  URL: {page['url']}")
            lines.append(f"  Excerpt: {page['excerpt']}")

    return "\n".join(lines)


async def extract_google_sports_widget(page) -> str:
    """
    Pull the live score / sports widget Google renders at the top of SERP.
    Google inlines this data directly in the page for sports queries — it is
    the most reliable path to real-time scores without visiting any result page.
    Tries multiple selector generations since Google's class names change.
    """
    # Ordered from most-specific (sports widget) to most-general (featured snippet)
    score_selectors = [
        ".sports-app-scoreboard",  # full scoreboard widget
        ".imso_mh__mh",            # match header (team names + scores)
        ".iQXTJe",                 # score area inside widget
        ".BmP5tf",                 # live match panel
        ".L3Ezfd",                 # score numbers
        ".kno-rdesc span",         # knowledge panel description
        ".BNeawe",                 # short featured-snippet text (mobile-style)
        ".hgKElc",                 # highlighted answer
        "[data-attrid] .LGOjhe",   # structured data answers
        ".aCOpRe span",            # snippet text spans
    ]
    parts: list[str] = []
    for sel in score_selectors:
        try:
            els = page.locator(sel)
            count = await els.count()
            for i in range(min(count, 4)):
                text = (await els.nth(i).inner_text(timeout=800)).strip()
                if text and text not in parts and len(text) < 400:
                    parts.append(text)
        except Exception:
            pass
    return "\n".join(parts)


# ============================================================================
# Set-of-Mark Visual Navigation
# ============================================================================

# JavaScript injected into a live Playwright page to draw numbered red bounding
# boxes over every visible interactive/text element in the current viewport.
SET_OF_MARKS_JS = """
() => {
    document.querySelectorAll('[data-som]').forEach(e => e.remove());
    const seen = new Set();
    const elements = [];
    let counter = 1;

    const candidates = [
        ...document.querySelectorAll(
            'a[href], button, h1, h2, h3, p, li, span, td, th, ' +
            '[class*="score"], [class*="result"], [class*="widget"], ' +
            '[class*="title"], [class*="snippet"]'
        )
    ];

    for (const el of candidates) {
        if (counter > 80) break;
        const rect = el.getBoundingClientRect();
        if (rect.width < 20 || rect.height < 8) continue;
        if (rect.top < -5 || rect.bottom > window.innerHeight + 5) continue;
        if (rect.left < -5 || rect.right > window.innerWidth + 5) continue;
        const text = (el.innerText || el.textContent || '').trim().slice(0, 120);
        if (!text) continue;
        const key = Math.round(rect.left/5)*5 + ',' + Math.round(rect.top/5)*5;
        if (seen.has(key)) continue;
        seen.add(key);

        const box = document.createElement('div');
        box.setAttribute('data-som', counter);
        box.style.cssText = [
            'position:fixed',
            'left:' + rect.left + 'px',
            'top:' + rect.top + 'px',
            'width:' + rect.width + 'px',
            'height:' + rect.height + 'px',
            'outline:2px solid red',
            'background:rgba(255,0,0,0.04)',
            'pointer-events:none',
            'z-index:2147483647',
            'box-sizing:border-box'
        ].join(';');

        const badge = document.createElement('span');
        badge.style.cssText = [
            'position:absolute', 'top:-14px', 'left:0',
            'background:red', 'color:#fff',
            'font:bold 10px/12px monospace',
            'padding:1px 3px', 'border-radius:2px', 'white-space:nowrap'
        ].join(';');
        badge.textContent = counter;
        box.appendChild(badge);
        document.body.appendChild(box);

        elements.push({
            id: counter,
            tag: el.tagName.toLowerCase(),
            text: text,
            href: el.href || null,
            cx: Math.round(rect.left + rect.width / 2),
            cy: Math.round(rect.top + rect.height / 2)
        });
        counter++;
    }
    return elements;
}
"""


async def inject_set_of_marks(page) -> list[dict]:
    """Inject numbered red bounding boxes over visible viewport elements."""
    try:
        return await page.evaluate(SET_OF_MARKS_JS)
    except Exception as exc:
        print(f"[visual] [WARN] SoM injection failed: {exc}", file=sys.stderr, flush=True)
        return []


async def capture_screenshot_b64(page) -> str:
    """Capture the current viewport as a base64-encoded PNG string."""
    try:
        raw = await page.screenshot(type="png", full_page=False)
        return base64.b64encode(raw).decode("utf-8")
    except Exception as exc:
        print(f"[visual] [WARN] Screenshot failed: {exc}", file=sys.stderr, flush=True)
        return ""


def ask_vlm_about_page(b64_image: str, query: str, client) -> str:
    """
    Send the annotated screenshot to the VLM (Llama-4-Scout) and return its reply.

    Reply formats (one of three):
      "ANSWER: <text>"  - the answer is visible on the current screen
      "CLICK_ON: <N>"   - click numbered element N to reach the answer
      "BLOCKED"         - CAPTCHA / bot-wall; do not continue
    """
    if not b64_image:
        return ""

    vlm_prompt = (
        f'You are an autonomous web agent viewing a browser screenshot.\n'
        f'The user query is: "{query}"\n\n'
        'Red numbered bounding boxes mark visible elements on the page.\n'
        'Respond with EXACTLY one of:\n'
        '  ANSWER: <exact text visible on screen that answers the query>\n'
        '  CLICK_ON: <number>   (to navigate further toward the answer)\n'
        '  BLOCKED              (if this is a CAPTCHA or bot-wall page)\n\n'
        'Be concise. No other text.'
    )
    try:
        response = client.chat.completions.create(
            model=VLM_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vlm_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64_image}"},
                        },
                    ],
                }
            ],
            max_completion_tokens=256,
            temperature=0.0,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        print(f"[visual] [WARN] VLM call failed: {exc}", file=sys.stderr, flush=True)
        return ""


async def visual_navigate_page(
    page,
    query: str,
    client,
    max_steps: int = 2,
) -> str | None:
    """
    Set-of-Mark visual navigation loop.

    Each step:
      1. Inject numbered bounding boxes into the live page.
      2. Take a viewport screenshot.
      3. Ask the VLM (Llama-4-Scout) what it sees.
      4a. ANSWER: <text>  -> return the answer string.
      4b. CLICK_ON: <N>   -> click that element's centre, wait, repeat.
      4c. BLOCKED         -> return None (page is a bot wall).

    Returns the answer string, or None if not found within max_steps.
    """
    for step in range(max_steps):
        els = await inject_set_of_marks(page)
        if not els:
            print(
                f"[visual] [WARN] No elements marked on step {step + 1}",
                file=sys.stderr, flush=True,
            )
            break

        b64 = await capture_screenshot_b64(page)
        if not b64:
            break

        # Guard: a page with fewer than 5 visible elements is almost certainly
        # a loading screen, redirect, or minimal error page — not a real SERP.
        # Sending it to the VLM wastes tokens and causes false BLOCKED replies.
        if len(els) < 5:
            print(
                f"[visual] [WARN] Only {len(els)} element(s) marked on step {step + 1} "
                "(likely a loading screen); skipping VLM",
                file=sys.stderr, flush=True,
            )
            break

        print(
            f"[visual] [STEP {step + 1}] {len(els)} elements marked; asking VLM...",
            file=sys.stderr, flush=True,
        )

        # VLM call is synchronous (Groq SDK) - run in executor to avoid blocking loop
        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(
            None, ask_vlm_about_page, b64, query, client
        )
        print(
            f"[visual] [VLM] {reply[:140]}",
            file=sys.stderr, flush=True,
        )

        if reply.startswith("ANSWER:"):
            return reply[len("ANSWER:"):].strip()

        if reply.strip() == "BLOCKED":
            print("[visual] VLM reports page is blocked", file=sys.stderr, flush=True)
            return None

        if reply.startswith("CLICK_ON:"):
            try:
                target_id = int(reply.split(":", 1)[1].strip())
                target = next((e for e in els if e["id"] == target_id), None)
                if target:
                    print(
                        f"[visual] Clicking element {target_id}: {target['text'][:60]}",
                        file=sys.stderr, flush=True,
                    )
                    await page.mouse.click(target["cx"], target["cy"])
                    await page.wait_for_timeout(1800)
                else:
                    print(
                        f"[visual] [WARN] Element {target_id} not in map; stopping",
                        file=sys.stderr, flush=True,
                    )
                    break
            except (ValueError, StopIteration):
                print(
                    f"[visual] [WARN] Unparseable CLICK_ON reply: {reply}",
                    file=sys.stderr, flush=True,
                )
                break

    return None


async def extract_search_results(page, selectors: dict, limit: int = 5) -> list[dict]:
    script = """
    ({ selectors, limit }) => {
        const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
        return Array.from(document.querySelectorAll(selectors.container))
            .slice(0, limit)
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
    raw_results = await page.evaluate(script, {"selectors": selectors, "limit": limit})

    normalized = []
    for item in raw_results:
        url = normalize_search_result_url(item.get("url", ""))
        normalized.append(
            {
                "title": item.get("title", "").strip() or "(untitled result)",
                "url": url,
                "snippet": item.get("snippet", "").strip(),
            }
        )

    return normalized


async def visit_result_pages(context, results: list[dict], per_page_timeout_ms: int = 30000) -> list[dict]:
    """
    Visit the top result pages and return an excerpt from each.

    Stealth: playwright-stealth is applied to each new page so that
    navigator.webdriver and other headless fingerprints are masked.

    Snippet fallback: if a fetched page triggers BLOCKED_PAGE_SIGNALS
    (Cloudflare, CAPTCHA, Access Denied, etc.) we discard the raw body
    text and fall back to the pre-extracted SERP snippet instead.
    This is almost always the quickest win for real-time queries because
    search engines already extracted the relevant sentence(s).
    """
    visited = []
    for result in results[:2]:
        url = result.get("url", "")
        if not url.startswith("http"):
            continue

        parsed = urlparse(url)
        if any(domain in parsed.netloc for domain in ("google.", "bing.com", "duckduckgo.com")):
            continue

        page = await context.new_page()
        try:
            await _apply_stealth(page)
            await page.goto(url, timeout=per_page_timeout_ms, wait_until="domcontentloaded")
            title = (await page.title()).strip() or result["title"]
            body_text = await page.locator("body").inner_text(timeout=10000)
            excerpt = normalize_whitespace(body_text, limit=600)

            # ------------------------------------------------------------------
            # Snippet fallback: if the page looks blocked, use the SERP snippet
            # ------------------------------------------------------------------
            if looks_like_blocked_page(title, excerpt):
                print(
                    f"[browser-use] [WARN] Blocked page detected for {url}; "
                    "falling back to SERP snippet",
                    file=sys.stderr,
                    flush=True,
                )
                snippet = result.get("snippet", "").strip()
                if snippet:
                    visited.append(
                        {
                            "title": result["title"],
                            "url": url,
                            "excerpt": f"[SERP snippet fallback] {snippet}",
                        }
                    )
                # Don't append the blocked page body
            elif excerpt:
                visited.append(
                    {
                        "title": title,
                        "url": url,
                        "excerpt": excerpt,
                    }
                )
        except Exception as exc:
            print(
                f"[browser-use] [WARN] Visiting result page failed for {url}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            # Best-effort: surface the snippet so the LLM still has something
            snippet = result.get("snippet", "").strip()
            if snippet:
                visited.append(
                    {
                        "title": result["title"],
                        "url": url,
                        "excerpt": f"[SERP snippet fallback – nav error] {snippet}",
                    }
                )
        finally:
            await page.close()

    return visited

def force_kill_browser():
    """
    Forcefully kills any dangling Chrome/Chromium zombie processes.
    Crucial for Render's lower-tier containers where processes can hang.
    """
    import subprocess
    try:
        subprocess.run(["pkill", "-9", "-f", "chrome"], check=False, capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "chromium"], check=False, capture_output=True)
        print("[System] Forcefully killed all Chromium processes.", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[System] Error killing browser: {e}", file=sys.stderr, flush=True)

async def get_browser_and_page(p, is_search=False):
    """
    Tries to connect to an existing Chrome instance on port 9222.
    If none found, launches a local persistent Chromium.
    Returns: (browser_or_none, context, page, is_remote)
    """
    try:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        print("[browser-use] Connected to existing Chrome instance on port 9222", file=sys.stderr, flush=True)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        return browser, context, page, True
    except Exception:
        print("[browser-use] No running Chrome found on port 9222. Launching persistent Chromium...", file=sys.stderr, flush=True)
        force_kill_browser()
        import os
        global SELECTED_CHROME_PROFILE
        is_headless = bool(os.environ.get("HEADLESS"))
        
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-popup-blocking",
            "--window-size=1920,1080",
            "--disable-extensions",
        ]
        
        # Apply highly natural browser headers universally to evade advanced bot walls
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
            if SELECTED_CHROME_PROFILE:
                user_data_dir = SELECTED_CHROME_PROFILE["user_data_dir"]
                profile_arg = f"--profile-directory={SELECTED_CHROME_PROFILE['profile_directory']}"
                args.extend(["--start-maximized", profile_arg])
                
                kwargs = {
                    "user_data_dir": user_data_dir,
                    "headless": is_headless,
                    "channel": "chrome",
                    "user_agent": SEARCH_USER_AGENT,
                    "locale": "en-US",
                    "viewport": {"width": 1440, "height": 960},
                    "args": args
                }
                if extra_headers:
                    kwargs["extra_http_headers"] = extra_headers
                    
                context = await p.chromium.launch_persistent_context(**kwargs)
                page = context.pages[0] if context.pages else await context.new_page()
                return None, context, page, False
            else:
                # Default behavior: Ephemeral browser instance
                # This prevents "profile currently IN USE" locking errors on concurrent runs or after crashes.
                kwargs = {
                    "headless": is_headless,
                    "args": args
                }
                browser = await p.chromium.launch(**kwargs)
                context_kwargs = {
                    "user_agent": SEARCH_USER_AGENT,
                    "locale": "en-US",
                    "viewport": {"width": 1440, "height": 960}
                }
                if extra_headers:
                    context_kwargs["extra_http_headers"] = extra_headers
                
                context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                return browser, context, page, False
                
        except Exception as e:
            if "exitCode=21" in str(e) or "TargetClosedError" in repr(e) or "in use" in str(e).lower() or "Target closed" in str(e):
                raise RuntimeError(
                    "\n\n[FATAL ERROR] Chrome failed to launch because your selected profile is currently IN USE by another process.\n"
                    "👉 SOLUTION: Close all background Chrome processes or start Chrome with --remote-debugging-port=9222.\n"
                ) from None
            raise

async def cleanup_browser(browser_or_none, context, page, is_remote):
    try:
        await page.close()
        if not is_remote:
            await context.close()
        elif browser_or_none:
            await browser_or_none.close()
    except Exception:
        pass

async def search_web(query: str, timeout: int = 90) -> str:
    """
    Use Playwright to run a generic web search worker.
    The worker tries multiple engines, detects blocked pages, extracts structured
    search results, and inspects top result pages for cleaner evidence.
    """
    try:
        print(f"[browser-use] [START] Importing Playwright...", file=sys.stderr, flush=True)
        from playwright.async_api import async_playwright
        print(f"[browser-use] [DONE] Playwright imported", file=sys.stderr, flush=True)
        
        async def search_with_browser():
            async with async_playwright() as p:
                browser_or_none, context, page, is_remote = await get_browser_and_page(p, is_search=True)
                
                try:
                    # Mask navigator.webdriver at the JS level (belt + suspenders with stealth)
                    await page.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                    )
                    await _apply_stealth(page)

                    notes = []

                    # Build a VLM client for visual navigation fallback.
                    # Uses the same API key as the main Groq client.
                    _vlm_client = None
                    import os
                    from groq import Groq
                    GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
                    if GROQ_API_KEY:
                        try:
                            _vlm_client = Groq(api_key=GROQ_API_KEY)
                        except Exception:
                            pass

                    for engine in SEARCH_ENGINES:
                        search_url = engine["url"].format(query=quote_plus(query))
                        print(
                            f"[browser-use] [START] Searching with {engine['name']}: {query}",
                            file=sys.stderr,
                            flush=True,
                        )

                        try:
                            await page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
                            await page.wait_for_timeout(1200)
                        except Exception as exc:
                            notes.append(f"{engine['name']}: navigation failed ({exc})")
                            print(
                                f"[browser-use] [WARN] {engine['name']} navigation failed: {exc}",
                                file=sys.stderr,
                                flush=True,
                            )
                            continue

                        title = await page.title()
                        try:
                            body_text = await page.locator("body").inner_text(timeout=5000)
                        except Exception:
                            body_text = ""

                        normalized_body = normalize_whitespace(body_text, limit=5000)
                        if looks_like_blocked_page(title, normalized_body):
                            notes.append(f"{engine['name']}: blocked or anti-bot page detected")
                            print(
                                f"[browser-use] [WARN] {engine['name']} appears blocked",
                                file=sys.stderr,
                                flush=True,
                            )
                            continue

                        # For Google, try to pull the inline sports/score widget
                        # before doing any result-page visits.
                        if engine["name"] == "google":
                            widget_text = await extract_google_sports_widget(page)
                            if widget_text:
                                notes.append(
                                    f"Google live widget data:\n{widget_text}"
                                )
                                print(
                                    f"[browser-use] [INFO] Google sports widget captured "
                                    f"({len(widget_text)} chars)",
                                    file=sys.stderr,
                                    flush=True,
                                )

                        results = await extract_search_results(page, engine["selectors"])
                        visited_pages = await visit_result_pages(context, results)

                        # --------------------------------------------------
                        # Visual fallback: if DOM extraction got no real page
                        # content (only snippet-fallbacks or nothing), use the
                        # Set-of-Mark agent to read the SERP visually.
                        # --------------------------------------------------
                        all_fallbacks = (
                            not visited_pages
                            or all(
                                p.get("excerpt", "").startswith("[SERP snippet fallback")
                                for p in visited_pages
                            )
                        )
                        if all_fallbacks and _vlm_client is not None:
                            print(
                                f"[visual] DOM yielded no direct content on {engine['name']}; "
                                "launching Set-of-Mark agent on SERP...",
                                file=sys.stderr, flush=True,
                            )
                            visual_answer = await visual_navigate_page(
                                page, query, _vlm_client
                            )
                            if visual_answer:
                                print(
                                    f"[visual] Agent found answer ({len(visual_answer)} chars)",
                                    file=sys.stderr, flush=True,
                                )
                                notes.append(
                                    f"[Visual agent - {engine['name']} SERP] {visual_answer}"
                                )

                        if results or visited_pages or any(
                            n.startswith("[Visual agent") for n in notes
                        ):
                            result_text = format_search_payload(
                                query=query,
                                engine_name=engine["name"],
                                results=results,
                                page_excerpt=normalized_body,
                                visited_pages=visited_pages,
                                notes=notes,
                            )
                            return result_text

                        notes.append(f"{engine['name']}: loaded page but extracted no structured results")

                    fallback_text = format_search_payload(
                        query=query,
                        engine_name="none",
                        results=[],
                        page_excerpt="",
                        visited_pages=[],
                        notes=notes or ["No search engine returned usable results."],
                    )

                    return fallback_text
                    
                finally:
                    await cleanup_browser(browser_or_none, context, page, is_remote)
        
        print(f"[browser-use] [START] Running async browser search (timeout={timeout}s)...", file=sys.stderr, flush=True)
        result = await asyncio.wait_for(search_with_browser(), timeout=timeout)
        print(f"[browser-use] [DONE] Search completed", file=sys.stderr, flush=True)
        return result
        
    except asyncio.TimeoutError:
        print(f"[browser-use] [TIMEOUT] Browser search exceeded {timeout}s timeout", file=sys.stderr, flush=True)
        force_kill_browser()
        return f"Timeout: Web search exceeded {timeout} seconds"
    except ImportError as e:
        print(f"[browser-use] [ERROR] ImportError: {e}", file=sys.stderr, flush=True)
        return f"Error: Playwright not installed. Install with: pip install playwright && playwright install chromium"
    except Exception as e:
        print(f"[browser-use] [ERROR] Exception ({type(e).__name__}): {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return f"Error during web search: {str(e)}"


async def navigate_url(
    url: str,
    input_text: str = "",
    input_selector: str = "",
    click_selector: str = "",
    timeout: int = 30,
) -> str:
    """
    Navigate to any URL with Playwright and return the page text content.

    Optional automation steps (executed in order if provided):
      1. Fill `input_text` into `input_selector` (CSS selector for a text field)
      2. Click `click_selector` (CSS selector for a button/link)
      3. Wait 3 seconds for the page to update, then re-capture text

    Returns the visible page text (up to 6000 chars).
    """
    try:
        from playwright.async_api import async_playwright

        async def _run():
            async with async_playwright() as p:
                browser_or_none, context, page, is_remote = await get_browser_and_page(p, is_search=False)
                try:
                    await _apply_stealth(page)

                    print(
                        f"[navigate_url] Navigating to: {url}",
                        file=sys.stderr, flush=True,
                    )
                    await page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)

                    # Attempt to dismiss common login/signup/cookie popups
                    async def dismiss_popups():
                        selectors = [
                            "button[aria-label*='close' i]",
                            "button[aria-label*='dismiss' i]",
                            "[class*='close-button' i]",
                            "[class*='modal-close' i]",
                            "svg[aria-label*='close' i]",
                            "button:has-text('✕')",
                            "button:has-text('✖')"
                        ]
                        for sel in selectors:
                            try:
                                elements = await page.locator(sel).all()
                                for el in elements:
                                    if await el.is_visible():
                                        print(f"[navigate_url] Dismissing popup using selector: {sel}", file=sys.stderr, flush=True)
                                        await el.click(timeout=1000)
                                        await page.wait_for_timeout(500)
                            except Exception:
                                pass
                    
                    await dismiss_popups()

                    # Optional: fill an input field
                    if input_text and input_selector:
                        print(
                            f"[navigate_url] Filling '{input_selector}' with: {input_text[:80]}",
                            file=sys.stderr, flush=True,
                        )
                        try:
                            await page.locator(input_selector).first.fill(input_text, timeout=5000)
                        except Exception:
                            # Try a broader approach — find the first visible textarea or input
                            await page.keyboard.type(input_text)

                    # Optional: click a button
                    if click_selector:
                        print(
                            f"[navigate_url] Clicking: {click_selector}",
                            file=sys.stderr, flush=True,
                        )
                        try:
                            await page.locator(click_selector).first.click(timeout=5000)
                            await page.wait_for_timeout(3000)  # wait for response
                        except Exception as exc:
                            print(
                                f"[navigate_url] [WARN] Click failed: {exc}",
                                file=sys.stderr, flush=True,
                            )

                    # Auto-scroll down to trigger lazy-loaded dynamic content (like internship boards)
                    try:
                        print(f"[navigate_url] Scrolling down to load dynamic content...", file=sys.stderr, flush=True)
                        for _ in range(3):
                            await page.keyboard.press("PageDown")
                            await page.wait_for_timeout(800)
                        await page.evaluate("window.scrollTo(0, 0)")
                        await page.wait_for_timeout(500)
                    except Exception:
                        pass

                    title = (await page.title()).strip()
                    body_text = ""
                    try:
                        body_text = await page.locator("body").inner_text(timeout=10000)
                    except Exception:
                        pass

                    page_content = normalize_whitespace(body_text, limit=6000)

                    if looks_like_blocked_page(title, page_content):
                        result = (
                            f"Page loaded but appears to be a bot-wall or login gate.\n"
                            f"Title: {title}\n"
                            f"Hint: The page requires login or CAPTCHA verification. "
                            "Try searching for the information instead."
                        )
                    else:
                        result = f"Page title: {title}\n\n{page_content}"

                    return result
                finally:
                    await cleanup_browser(browser_or_none, context, page, is_remote)

        result = await asyncio.wait_for(_run(), timeout=timeout + 10)
        print(
            f"[navigate_url] Done — {len(result)} chars returned",
            file=sys.stderr, flush=True,
        )
        return result
    except asyncio.TimeoutError:
        print(f"[navigate_url] [TIMEOUT] navigate_url exceeded {timeout}s", file=sys.stderr, flush=True)
        force_kill_browser()
        return f"Timeout: navigate_url exceeded {timeout}s"
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return f"Error navigating to {url}: {exc}"


# ============================================================================
# Tool Calling with Groq
# ============================================================================

from prompts import TOOLS, FINAL_ANSWER_SYSTEM_PROMPT, get_system_prompt

