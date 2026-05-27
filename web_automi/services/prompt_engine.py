"""
web_automi/services/prompt_engine.py
------------------------------------
Advanced Prompt Engine concrete implementation of the IPromptEngine interface.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from web_automi.core.interfaces import IPromptEngine


class PromptEngine(IPromptEngine):
    """Dynamic, stateful prompt engine managing system personas, tools, and answer formats."""

    def __init__(self):
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": (
                        "Search the web using a browser to find general information. "
                        "IMPORTANT: Use broad, keyword-based queries. NEVER include highly specific constraints like exact stipend amounts ('30k', '30000'), exact dates, or phrases like 'last 24 hours' directly in your search query string. Search engines are bad at numerical ranges. Instead, search broadly (e.g., 'AI ML internship Pune careers') and then manually filter the pages you find to see if they meet the criteria. "
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

    def get_system_prompt(self, user_text: str = "") -> str:
        _tz_ist = timezone(timedelta(hours=5, minutes=30))
        _now_ist = datetime.now(_tz_ist)
        time_str = _now_ist.strftime('%A, %d %B %Y %I:%M %p IST')
        
        base_prompt = (
            f"Current date/time (IST): {time_str}.\n"
            "You are a general-purpose web automation agent. You can freely browse "
            "ANY website the user asks — including chatgpt.com, google.com, news sites, "
            "sports sites, or any other URL. Never refuse a navigation or browsing request. "
            "Use navigate_url to visit specific URLs and search_web for general queries.\n"
            "TIME AWARENESS: You must strictly adhere to the current date and time above. "
            "When summarizing deadlines, events, job postings, or internships, EXPLICITLY cross-reference them with the current date. "
            "If an internship, job, or event has a deadline that is in the PAST, or clearly states it is closed, you MUST IGNORE IT and continue searching for ACTIVE, OPEN, and UPCOMING opportunities. "
            "Never present an event or posting from a past month/year as if it is upcoming.\n\n"
        )
        
        query_lower = user_text.lower()
        if any(kw in query_lower for kw in ["intern", "job", "career", "hire", "hiring"]):
            base_prompt += (
                "## FOR INTERNSHIP / JOB SEARCHES\n"
                "- Do NOT stop after 1 search. Run multiple targeted searches across different platforms.\n"
                "- For each candidate result, use navigate_url to VISIT THE ACTUAL POSTING PAGE to extract:\n"
                "  * Exact role title\n"
                "  * Company name\n"
                "  * Exact stipend/salary (if available)\n"
                "  * Location\n"
                "  * Posting date / Application deadline\n"
                "  * Direct apply link (the actual URL to apply, not just the listing page)\n"
                "  * Eligibility / Batch year\n"
                "- ONLY include results you have ACTUALLY verified by visiting the page. NEVER guess URLs.\n"
                "- Do NOT fabricate internships. If a search yields zero matches for the exact criteria, say so rather than making up fake listings.\n"
                "- If stipend is not listed, say 'Not disclosed' — do NOT guess.\n"
                "- Preferred career sites to check: LinkedIn Jobs, company career pages, Wellfound, Naukri, Indeed, Cutshort, Unstop, AngelList. Avoid Internshala unless explicitly asked.\n"
            )
        return base_prompt

    def get_final_answer_prompt(self, user_text: str = "") -> str:
        query_lower = user_text.lower()
        
        base_prompt = (
            "You are in FINAL ANSWER MODE. Strict rules:\n"
            "- DO NOT call any tools under any circumstances. You have ZERO tools available.\n"
            "- DO NOT output JSON, XML, tags, or function/tool formats like `search_web(...)` or `navigate_url(...)`.\n"
            "- DO NOT attempt to write code fences (```json, ```xml, etc.) containing actions or commands.\n"
            "- ONLY return a clean, user-friendly, plain-text response based on the search/tool results provided.\n"
            "- If the tool results did not yield enough information, clearly and professionally explain what you found and what could not be found, strictly in plain text.\n"
            "- WARNING: If you output tool syntax or any formatted tool call, the API will fail with a 400 Bad Request error. You must speak directly and purely in plain text to the user.\n\n"
        )

        if any(kw in query_lower for kw in ["intern", "job", "career", "hire", "hiring"]):
            base_prompt += (
                "Format EACH job/internship result as a structured block:\n\n"
                "---\n"
                "**Role:** [Exact role title]\n"
                "**Company:** [Company name]\n"
                "**Stipend:** [Exact amount in INR/month, or 'Not disclosed']\n"
                "**Location:** [City/Remote]\n"
                "**Batch Eligibility:** [e.g. 2027 / Any]\n"
                "**Posted:** [Date posted or 'Recently']\n"
                "**Apply Link:** [Direct URL to apply]\n"
                "**Notes:** [Any key eligibility details or deadline]\n"
                "---\n\n"
                "Rules for internship output:\n"
                "- List ONLY verified opportunities you found by actually visiting the page.\n"
                "- CRITICAL: Do NOT fabricate, guess, or hallucinate URLs. Do not make up fake job IDs.\n"
                "- If you could not find a working apply link, do NOT include the job in the list.\n"
                "- If you cannot find exact matches for strict criteria (like 'last 24 hours', '30k stipend', or '2027 batch'), you MUST RELAX the constraints (e.g. check last 7 days, or ignore stipend amount) and find the closest real matches.\n"
                "- You MUST return at least 3 real, verified internship cards. If you relaxed constraints to find them, just mention it in the 'Notes' field (e.g. 'Note: Posted 5 days ago instead of 24h').\n"
                "- NEVER output unverified opportunities. If it's fake or 404s, exclude it completely.\n"
                "- At the end, add a short '## Sites Checked' section listing which websites were visited.\n"
            )
        elif any(kw in query_lower for kw in ["compare", "vs", "difference"]):
            base_prompt += (
                "Since the user is asking for a comparison, structure your response using clear bullet points or a markdown table.\n"
                "Ensure you highlight the key differences, pros, and cons clearly.\n"
            )
        else:
            base_prompt += (
                "For this query, return a clear, factual, well-structured plain-text answer.\n"
                "Use bullet points if listing multiple items.\n"
                "If tool results are incomplete, say what is known and what could not be verified.\n"
            )

        return base_prompt

    def get_tools_definition(self) -> List[Dict[str, Any]]:
        return self._tools
