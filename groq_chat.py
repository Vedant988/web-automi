"""
groq_chat.py
------------
Backwards compatibility wrapper delegating to the new modular ReActAgent.
"""

import os
import sys
import gc
import json
import asyncio
from typing import Any, Dict, List, Optional
import web_automi.core.config as config

# Re-expose helper functions for backward compatibility and internal agent usage
def extract_text_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
            else:
                text = getattr(item, "text", None)
            if text:
                parts.append(text)
        return "".join(parts)
    return str(content)


def render_tool_result(text: str, limit: int = 4000) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "(empty tool result)"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "\n...[truncated]"


def looks_like_tool_output(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    suspicious_snippets = (
        "\"tool_calls\"",
        "\"function\":",
        "<tool_call",
        "<function_call",
        "```json",
        "\"name\": \"search_web\"",
    )
    if any(snippet in lowered for snippet in suspicious_snippets):
        return True
    return lowered.startswith("{") and "\"query\"" in lowered


def build_client(api_key: str | None = None):
    return config.build_client(api_key)


def build_tool_result_fallback(executed_tools: list[dict]) -> str:
    if not executed_tools:
        return "I couldn't generate a final answer."
    sections = []
    for item in executed_tools:
        sections.append(
            "\n".join([
                f"{item['name']}({json.dumps(item['arguments'], ensure_ascii=True)})",
                render_tool_result(item["result"], limit=1200),
            ])
        )
    return (
        "I couldn't get a plain-text final answer from the model, so here is the tool output I collected:\n\n"
        + "\n\n".join(sections)
    )


def summarize_tool_result(result: str, user_query: str, groq_client) -> str:
    if len(result) <= config._SUMMARIZER_TRUNCATE_FALLBACK:
        return result
    system_prompt = (
        "You are a research assistant that compresses verbose web search results "
        "into a tight, factual summary.\n"
        "Rules:\n"
        "- Keep ONLY facts directly relevant to the user query.\n"
        "- CRITICAL: ALWAYS preserve all URLs, apply links, and direct application links VERBATIM.\n"
        "- CRITICAL: ALWAYS preserve stipend/salary amounts, company names, role titles, and posting dates.\n"
        "- CRITICAL: ALWAYS preserve location information and eligibility/batch year details.\n"
        "- Drop navigation text, ads, repeated boilerplate, cookie banners, and off-topic content.\n"
        "- Output plain text, 200-350 words maximum.\n"
        "- Do NOT add any commentary, preamble, or closing remarks.\n"
        "- Do NOT summarize away or omit any apply link, stipend, or company detail."
    )
    user_prompt = (
        f"User query: {user_query}\n\n"
        f"Raw search result to compress:\n{result[:6000]}"
    )
    try:
        resp = groq_client.chat.completions.create(
            model=config._SUMMARIZER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.0,
            max_completion_tokens=400,
        )
        summary = (resp.choices[0].message.content or "").strip()
        if summary:
            print(
                f"[summarizer] Compressed {len(result)} → {len(summary)} chars",
                file=sys.stderr, flush=True,
            )
            return f"[summarized]\n{summary}"
    except Exception as exc:
        print(f"[summarizer] [WARN] Summarization failed ({exc}); falling back to truncation", file=sys.stderr, flush=True)
    return result[:config._SUMMARIZER_TRUNCATE_FALLBACK] + "\n...[truncated]"


def stream_chat_with_tools(
    user_text: str,
    model: str = "openai/gpt-oss-20b",
    temperature: float = 0.0,
    max_completion_tokens: int = 2048,
    final_model: Optional[str] = None
) -> str:
    # Resolve services dynamically to completely prevent any circular imports at load time
    from web_automi.services.browser_service import PlaywrightBrowserService
    from web_automi.services.prompt_engine import PromptEngine
    from web_automi.agent.engine import ReActAgent

    browser = PlaywrightBrowserService()
    prompter = PromptEngine()
    agent = ReActAgent(browser_service=browser, prompt_engine=prompter)

    return agent.stream_chat_with_tools(
        user_text=user_text,
        model=model,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
        final_model=final_model
    )
