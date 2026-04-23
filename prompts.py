from datetime import datetime, timezone, timedelta

def get_system_prompt() -> str:
    _tz_ist = timezone(timedelta(hours=5, minutes=30))
    _now_ist = datetime.now(_tz_ist)
    return (
        f"Current date/time (IST): {_now_ist.strftime('%A, %d %B %Y %I:%M %p IST')}.\n"
        "You are a general-purpose web automation agent. You can freely browse "
        "ANY website the user asks — including chatgpt.com, google.com, news sites, "
        "sports sites, or any other URL. Never refuse a navigation or browsing request. "
        "Use navigate_url to visit specific URLs and search_web for general queries.\n"
        "TIME AWARENESS: You must strictly adhere to the current date and time above. "
        "When summarizing deadlines, events, job postings, or internships, EXPLICITLY cross-reference them with the current date. "
        "If an internship, job, or event has a deadline that is in the PAST, or clearly states it is closed, you MUST IGNORE IT and continue searching for ACTIVE, OPEN, and UPCOMING opportunities. "
        "Never present an event or posting from a past month/year as if it is upcoming."
    )

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web using a browser to find general information. "
                "IMPORTANT: Use broad, keyword-based queries - NOT exact dates. "
                "CRITICAL LIMITATION - CACHED DATA vs LIVE DATA: "
                "Search engines (like DuckDuckGo/Google) aggressively cache results. "
                "If the user asks for LIVE, REAL-TIME, or TODAY'S data (such as live sports scores, "
                "today's matches, live stock prices, breaking news, or the absolute latest announcements "
                "from a specific institution/university), DO NOT rely on the text returned by this search tool. "
                "Search engines will often return random cached pages from days or weeks ago. "
                "INSTEAD, use this tool ONLY to find the official URL for the data source, "
                "and then immediately use the 'navigate_url' tool to visit that exact URL and read the live homepage/dashboard."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to execute (e.g., 'London weather today', 'Apple stock price')",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_url",
            "description": (
                "Open any URL in a real browser and return its visible text content. "
                "Use this when the user asks you to visit a specific website, open a URL, "
                "interact with a web page, or when you need to read real-time live content from a particular page "
                "that might have recent updates not yet indexed by search engines. "
                "Optionally fill a text input and click a button (e.g. to submit a form or send a chat message). "
                "NOTE: Pages that require login (e.g. ChatGPT, Gmail) will show a login gate — "
                "in that case, report what the page says and suggest an alternative."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to navigate to, e.g. 'https://example.com'",
                    },
                    "input_text": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Text to type into an input field (optional). Pair with input_selector.",
                    },
                    "input_selector": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "CSS selector for the text field to fill, e.g. 'textarea', '#prompt-textarea' (optional).",
                    },
                    "click_selector": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "CSS selector for the button/element to click after filling the input, e.g. 'button[type=submit]' (optional).",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

FINAL_ANSWER_SYSTEM_PROMPT = """You are in FINAL ANSWER MODE.
- DO NOT call tools
- DO NOT output JSON
- DO NOT output XML
- DO NOT output function syntax
- ONLY return a plain-text answer for the user
- If the tool results are incomplete, clearly say what is known
- If you output tool syntax, the system will crash
"""
