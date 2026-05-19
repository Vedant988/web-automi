import os
import sys
import time
import json
import base64
import asyncio
import argparse
from dotenv import load_dotenv
from groq import Groq
from collections import deque
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
import prompts as prompts_module

load_dotenv()

# Ensure stdout/stderr handle full Unicode (LLM responses contain characters
# outside cp1252 on Windows, e.g. narrow no-break spaces, em-dashes).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_BASE = os.getenv("GROQ_API_BASE")

if not GROQ_API_KEY:
    GROQ_API_KEY = None


TOOLS = prompts_module.TOOLS


def get_system_prompt(user_text: str = "") -> str:
    """
    Support both prompt module variants:
    - newer shape: get_system_prompt(user_text="")
    - older shape: get_system_prompt()
    """
    prompt_builder = getattr(prompts_module, "get_system_prompt", None)
    if prompt_builder is None:
        raise RuntimeError("prompts.py is missing get_system_prompt")

    try:
        return prompt_builder(user_text)
    except TypeError:
        return prompt_builder()


def get_final_answer_prompt(user_text: str = "") -> str:
    """
    Support both prompt module variants:
    - newer shape: get_final_answer_prompt(user_text="")
    - older shape: FINAL_ANSWER_SYSTEM_PROMPT constant
    """
    prompt_builder = getattr(prompts_module, "get_final_answer_prompt", None)
    if callable(prompt_builder):
        return prompt_builder(user_text)

    fallback_prompt = getattr(prompts_module, "FINAL_ANSWER_SYSTEM_PROMPT", None)
    if isinstance(fallback_prompt, str) and fallback_prompt.strip():
        return fallback_prompt

    raise RuntimeError(
        "prompts.py must define either get_final_answer_prompt() or FINAL_ANSWER_SYSTEM_PROMPT"
    )

# ── Multi-key pool: loads GROQ_API_KEY, GROQ_API_KEY1, GROQ_API_KEY2, … ──────
# On 429 rate-limit errors the client rotates to the next available key
# immediately instead of waiting.
def _load_api_key_pool() -> list[str]:
    keys = []
    # Primary key (no suffix)
    k = os.getenv("GROQ_API_KEY", "").strip()
    if k:
        keys.append(k)
    # Numbered fallbacks: GROQ_API_KEY1, GROQ_API_KEY2, …
    for i in range(1, 20):
        k = os.getenv(f"GROQ_API_KEY{i}", "").strip()
        if k:
            keys.append(k)
        else:
            break  # stop at first gap
    return keys

_API_KEY_POOL: list[str] = _load_api_key_pool()
_current_key_index: int = 0


def _get_current_key() -> str | None:
    if not _API_KEY_POOL:
        return None
    return _API_KEY_POOL[_current_key_index % len(_API_KEY_POOL)]


def _rotate_key() -> str | None:
    global _current_key_index
    if len(_API_KEY_POOL) <= 1:
        return _get_current_key()
    _current_key_index = (_current_key_index + 1) % len(_API_KEY_POOL)
    new_key = _API_KEY_POOL[_current_key_index]
    print(
        f"[rate-limit] Rotated to API key #{_current_key_index + 1} / {len(_API_KEY_POOL)}",
        file=sys.stderr, flush=True,
    )
    return new_key


def build_client(api_key: str | None = None) -> "Groq | None":
    key = api_key or _get_current_key()
    if not key:
        return None
    if GROQ_API_BASE:
        return Groq(api_key=key, base_url=GROQ_API_BASE)
    return Groq(api_key=key)


# ============================================================================
# Browser-Use Integration (async)
# ============================================================================



from browser_tools import search_web, navigate_url, select_chrome_profile
import browser_tools

DEFAULT_FINAL_MODEL = "llama-3.3-70b-versatile"
DEFAULT_REASONING_EFFORT = "low"
MODELS_WITHOUT_REASONING_EFFORT = {
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "moonshotai/kimi-k2-instruct",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    # Llama models produce broken XML tool-call format when reasoning_effort is set
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
}


def pick_final_model(primary_model: str, requested_final_model: str | None = None) -> str:
    if requested_final_model:
        return requested_final_model
    if primary_model == "openai/gpt-oss-120b":
        return DEFAULT_FINAL_MODEL
    return primary_model


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

# Fast, cheap model used only for compressing tool results in the ReAct loop.
# Needs to be in MODELS_WITHOUT_REASONING_EFFORT since we don't set that param.
_SUMMARIZER_MODEL = "llama-3.1-8b-instant"
_SUMMARIZER_TRUNCATE_FALLBACK = 3500  # raised from 2000 — preserves apply links & stipends


def summarize_tool_result(result: str, user_query: str, groq_client) -> str:
    """
    Compress a raw tool result (e.g. search_web output) down to the key facts
    that are relevant to user_query.

    Uses llama-3.1-8b-instant — fast and cheap — so it doesn't eat into the
    main model's token budget.  The summarizer is called ONLY when the result
    exceeds _SUMMARIZER_TRUNCATE_FALLBACK chars; short results are passed through.

    Falls back to plain truncation if the API call fails for any reason.
    """
    if len(result) <= _SUMMARIZER_TRUNCATE_FALLBACK:
        return result  # already small enough — no-op

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
        f"Raw search result to compress:\n{result[:6000]}"  # hard cap on input
    )
    try:
        resp = groq_client.chat.completions.create(
            model=_SUMMARIZER_MODEL,
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
                f"[summarizer] Compressed {len(result)} → {len(summary)} chars "
                f"(saved {len(result) - len(summary)} chars)",
                file=sys.stderr, flush=True,
            )
            return f"[summarized]\n{summary}"
    except Exception as exc:
        print(
            f"[summarizer] [WARN] Summarization failed ({exc}); falling back to truncation",
            file=sys.stderr, flush=True,
        )
    # Fallback: plain truncation
    return result[:_SUMMARIZER_TRUNCATE_FALLBACK] + "\n...[truncated]"


def build_final_answer_messages(user_text: str, executed_tools: list[dict], retry: int = 0):
    tool_sections = []
    for index, item in enumerate(executed_tools, start=1):
        tool_sections.append(
            "\n".join(
                [
                    f"Tool #{index}: {item['name']}",
                    f"Arguments: {json.dumps(item['arguments'], ensure_ascii=True)}",
                    "Result:",
                    render_tool_result(item["result"]),
                ]
            )
        )

    messages = [
        {"role": "system", "content": get_final_answer_prompt(user_text)},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    "User request:",
                    user_text.strip(),
                    "Tool results you must use:",
                    "\n\n".join(tool_sections) if tool_sections else "No tool results were available.",
                    "Write the final user-facing answer in plain text.",
                ]
            ),
        },
    ]

    if retry > 0:
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your previous reply was invalid because it was empty or attempted tool syntax. "
                    "Return only a plain-text final answer with no tool calls, no JSON, and no code fences."
                ),
            }
        )

    return messages


def build_tool_result_fallback(executed_tools: list[dict]) -> str:
    if not executed_tools:
        return "I couldn't generate a final answer."

    sections = []
    for item in executed_tools:
        sections.append(
            "\n".join(
                [
                    f"{item['name']}({json.dumps(item['arguments'], ensure_ascii=True)})",
                    render_tool_result(item["result"], limit=1200),
                ]
            )
        )

    return (
        "I couldn't get a plain-text final answer from the model, so here is the tool output I collected:\n\n"
        + "\n\n".join(sections)
    )


def stream_chat_with_tools(
    user_text: str,
    model: str = "openai/gpt-oss-20b",
    temperature: float = 0.0,
    max_completion_tokens: int = 2048,
    top_p: float = 0.9,
    retries: int = 1,
    final_model: str | None = None,
    final_temperature: float = 0.0,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    final_reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    max_tool_calls: int = 3,
    max_final_answer_retries: int = 2,
):
    """
    Stream chat with a two-phase tool flow.
    Phase 1 uses a tool-enabled model.
    Phase 2 uses a tool-free final-answer model with defensive retries.
    """
    TPM_LIMIT = 8000
    RPM_LIMIT = 30

    class RequestTracker:
        def __init__(self):
            self.queue = deque()

        def _prune(self):
            cutoff = time.time() - 60
            while self.queue and self.queue[0][0] < cutoff:
                self.queue.popleft()

        def token_sum(self):
            self._prune()
            return sum(t for _, t in self.queue)

        def request_count(self):
            self._prune()
            return len(self.queue)

        def can_send(self, tokens_requested: int):
            self._prune()
            return (
                self.token_sum() + tokens_requested <= TPM_LIMIT
                and self.request_count() < RPM_LIMIT
            )

        def register(self, tokens_requested: int):
            self.queue.append((time.time(), tokens_requested))

        def wait_for_slot(self, tokens_requested: int, timeout: float = 30.0):
            start = time.time()
            while not self.can_send(tokens_requested):
                if time.time() - start > timeout:
                    return False
                time.sleep(0.5)
            return True

    tracker = RequestTracker()

    # -----------------------------------------------------------------------
    # Usage accumulator — collects actual token counts from every API call.
    # -----------------------------------------------------------------------
    usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "api_calls": 0,
        # Latest rate-limit snapshot from Groq response headers
        "rl_limit_requests": None,   # RPD limit
        "rl_remaining_requests": None,
        "rl_reset_requests": None,
        "rl_limit_tokens": None,     # TPM limit
        "rl_remaining_tokens": None,
        "rl_reset_tokens": None,
    }

    def estimate_tokens(text: str) -> int:
        if not text:
            return 1
        return max(1, int(len(text) / 4))

    def estimate_message_tokens(chat_messages) -> int:
        total = 0
        for message in chat_messages:
            total += estimate_tokens(extract_text_content(message.get("content", "")))
            if "tool_calls" in message:
                total += estimate_tokens(json.dumps(message["tool_calls"]))
            if "name" in message:
                total += estimate_tokens(message["name"])
        return max(1, total)

    def create_completion(
        call_model: str,
        call_messages,
        call_temperature: float,
        *,
        request_tools=None,
        call_tool_choice=None,
        call_reasoning_effort: str | None = None,
        label: str = "groq",
    ):
        nonlocal client  # allow key rotation to rebuild the Groq client on 429
        prompt_tokens = estimate_message_tokens(call_messages)
        desired_completion = max_completion_tokens
        total_requested = prompt_tokens + desired_completion

        if total_requested > TPM_LIMIT:
            if prompt_tokens >= TPM_LIMIT - 10:
                raise RuntimeError("Prompt too large to fit token limit")
            desired_completion = max(1, TPM_LIMIT - prompt_tokens - 10)
            total_requested = prompt_tokens + desired_completion
            print(
                f"[rate-limit] Adjusted completion tokens to {desired_completion} for {label}",
                file=sys.stderr,
                flush=True,
            )

        if desired_completion <= 0:
            raise RuntimeError("Prompt too large to fit token limit")

        # Let the Groq API SDK handle exact token limitations via HTTP 429 errors

        tool_count = len(request_tools) if request_tools else 0
        print(
            f"[{label}] [START] Sending request with {len(call_messages)} message(s), tools={tool_count}",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"[{label}] Model={call_model}, temp={call_temperature}, reasoning={call_reasoning_effort or 'default'}, completion_tokens={desired_completion}",
            file=sys.stderr,
            flush=True,
        )

        request_kwargs = {
            "model": call_model,
            "messages": call_messages,
            "temperature": call_temperature,
            "max_completion_tokens": desired_completion,
            "top_p": top_p,
        }
        if request_tools is not None:
            request_kwargs["tools"] = request_tools
        if call_tool_choice is not None:
            request_kwargs["tool_choice"] = call_tool_choice
        supports_reasoning_effort = (
            call_reasoning_effort is not None
            and call_model not in MODELS_WITHOUT_REASONING_EFFORT
        )
        if supports_reasoning_effort:
            request_kwargs["reasoning_effort"] = call_reasoning_effort

        print(f"[{label}] [WAIT] Calling client.chat.completions.create()...", file=sys.stderr, flush=True)
        import re
        # Each retry may use a different API key from the pool
        max_api_retries = max(3, len(_API_KEY_POOL) + 1)
        keys_tried: set[int] = set()
        for api_attempt in range(max_api_retries):
            try:
                raw = client.chat.completions.with_raw_response.create(**request_kwargs)
                response = raw.parse()
                break
            except Exception as exc:
                error_text = str(exc)
                if supports_reasoning_effort and "`reasoning_effort` is not supported with this model" in error_text:
                    MODELS_WITHOUT_REASONING_EFFORT.add(call_model)
                    request_kwargs.pop("reasoning_effort", None)
                    print(
                        f"[{label}] [WARN] {call_model} does not support reasoning_effort; retrying without it",
                        file=sys.stderr,
                        flush=True,
                    )
                    raw = client.chat.completions.with_raw_response.create(**request_kwargs)
                    response = raw.parse()
                    break
                elif "429" in error_text or "rate limit" in error_text.lower():
                    keys_tried.add(_current_key_index)
                    if len(_API_KEY_POOL) > 1 and len(keys_tried) < len(_API_KEY_POOL):
                        # Rotate to the next key and rebuild client immediately
                        _rotate_key()
                        client = build_client()
                        print(
                            f"[{label}] [WARN] Rate limit on key #{list(keys_tried)[-1]+1}. "
                            f"Switched to key #{_current_key_index+1}. Retrying now...",
                            file=sys.stderr, flush=True,
                        )
                    elif api_attempt < max_api_retries - 1:
                        # Only one key available — fall back to timed wait
                        wait_match = re.search(r"try again in ([\d\.]+)s", error_text)
                        wait_s = float(wait_match.group(1)) + 1.0 if wait_match else 30.0
                        print(f"[{label}] [WARN] Rate limit hit (only 1 key). Waiting {wait_s:.1f}s...", file=sys.stderr, flush=True)
                        time.sleep(wait_s)
                    else:
                        raise RuntimeError(f"Rate limit: all {len(_API_KEY_POOL)} key(s) exhausted. (API error: {error_text})")
                elif "tool_use_failed" in error_text or "failed_generation" in error_text:
                    # Llama models sometimes emit tool calls in a broken XML format
                    # (<function=name {...}> instead of JSON). This is triggered by
                    # reasoning_effort being set. Retry WITH tools but WITHOUT
                    # reasoning_effort so the model produces valid JSON tool calls.
                    print(
                        f"[{label}] [WARN] Model produced broken tool-call format (XML-style). "
                        "Retrying with tools but without reasoning_effort...",
                        file=sys.stderr, flush=True,
                    )
                    recovery_kwargs = {k: v for k, v in request_kwargs.items()}
                    recovery_kwargs.pop("reasoning_effort", None)
                    if recovery_kwargs.get("tool_choice") == "required":
                        recovery_kwargs["tool_choice"] = "auto"
                    # Mark this model so we never set reasoning_effort for it again
                    MODELS_WITHOUT_REASONING_EFFORT.add(call_model)
                    raw = client.chat.completions.with_raw_response.create(**recovery_kwargs)
                    response = raw.parse()
                    break
                elif "Tool choice is required, but model did not call a tool" in error_text:
                    print(
                        f"[{label}] [WARN] Model skipped a required tool call. "
                        "Retrying with tool_choice=auto...",
                        file=sys.stderr, flush=True,
                    )
                    recovery_kwargs = {k: v for k, v in request_kwargs.items()}
                    recovery_kwargs["tool_choice"] = "auto"
                    recovery_kwargs.pop("reasoning_effort", None)
                    MODELS_WITHOUT_REASONING_EFFORT.add(call_model)
                    raw = client.chat.completions.with_raw_response.create(**recovery_kwargs)
                    response = raw.parse()
                    break
                else:
                    raise

        tracker.register(total_requested)

        # --- Accumulate real token counts from the API response -------------
        usage["api_calls"] += 1
        if response.usage:
            usage["prompt_tokens"] += response.usage.prompt_tokens or 0
            usage["completion_tokens"] += response.usage.completion_tokens or 0
            usage["total_tokens"] += response.usage.total_tokens or 0

        # --- Extract rate-limit headers (always present, per Groq docs) ----
        hdrs = dict(raw.headers)
        usage["rl_limit_requests"] = hdrs.get("x-ratelimit-limit-requests")
        usage["rl_remaining_requests"] = hdrs.get("x-ratelimit-remaining-requests")
        usage["rl_reset_requests"] = hdrs.get("x-ratelimit-reset-requests")
        usage["rl_limit_tokens"] = hdrs.get("x-ratelimit-limit-tokens")
        usage["rl_remaining_tokens"] = hdrs.get("x-ratelimit-remaining-tokens")
        usage["rl_reset_tokens"] = hdrs.get("x-ratelimit-reset-tokens")

        if response.usage:
            print(
                f"[{label}] [USAGE] prompt={response.usage.prompt_tokens} "
                f"completion={response.usage.completion_tokens} "
                f"total={response.usage.total_tokens} "
                f"| TPM remaining={usage['rl_remaining_tokens']}/{usage['rl_limit_tokens']} "
                f"reset={usage['rl_reset_tokens']}",
                file=sys.stderr,
                flush=True,
            )

        print(
            f"[{label}] [DONE] Received response with {len(response.choices)} choice(s)",
            file=sys.stderr,
            flush=True,
        )
        return response

    def request_final_answer(executed_tools: list[dict], resolved_final_model: str) -> str | None:
        for final_attempt in range(max_final_answer_retries + 1):
            final_messages = build_final_answer_messages(user_text, executed_tools, retry=final_attempt)
            response = create_completion(
                resolved_final_model,
                final_messages,
                final_temperature,
                call_tool_choice="none",
                call_reasoning_effort=final_reasoning_effort,
                label="final-phase",
            )

            for choice in response.choices:
                content = extract_text_content(choice.message.content).strip()
                has_tool_calls = bool(choice.message.tool_calls)
                print(
                    f"[final-phase] Choice: content={len(content)} chars, tool_calls={len(choice.message.tool_calls) if has_tool_calls else 0}",
                    file=sys.stderr,
                    flush=True,
                )

                if has_tool_calls:
                    print(
                        "[final-phase] [WARN] Final answer call still returned tool calls; retrying with stronger guard",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue

                if not content:
                    print(
                        "[final-phase] [WARN] Final answer call returned empty content; retrying",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue

                if looks_like_tool_output(content):
                    print(
                        "[final-phase] [WARN] Final answer looks like tool syntax; retrying",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue

                return content

        return None

    client = build_client()
    if client is None:
        raise RuntimeError("GROQ_API_KEY is not set in the environment")
    print(
        f"[init] API key pool: {len(_API_KEY_POOL)} key(s) available",
        file=sys.stderr, flush=True,
    )

    resolved_final_model = pick_final_model(model, final_model)

    print(
        f"[init] Tool model: {model}, Final model: {resolved_final_model}, Retries: {retries}",
        file=sys.stderr,
        flush=True,
    )
    print(f"[init] User text length: {len(user_text)} chars", file=sys.stderr, flush=True)

    for attempt in range(retries + 1):
        try:
            # Prepend current datetime and dynamic persona as a system message
            _date_ctx = get_system_prompt(user_text)
            messages = [
                {"role": "system", "content": _date_ctx},
                {"role": "user", "content": user_text},
            ]
            collected_output = ""
            tool_call_count = 0
            executed_tools = []
            react_round = 0

            # ---------------------------------------------------------------
            # ReAct loop: Reason → Act → Observe → Reason → ...
            #
            # After each search the tool model receives the full result in
            # context and decides:
            #   (a) Make another search_web call with a refined query, OR
            #   (b) Return a plain-text reply — meaning it has enough info.
            #
            # This gives the agent true chain-of-thought: it can evaluate
            # result quality and iterate without human intervention.
            # ---------------------------------------------------------------
            # FIRST call: start with tool_choice="auto". Some models, notably
            # gpt-oss variants, can fail hard when "required" is set and they
            # miss the exact tool-call format. We still enforce real browsing
            # below if the model tries to answer without any tool usage.
            # ---------------------------------------------------------------
            response = create_completion(
                model,
                messages,
                temperature,
                request_tools=TOOLS,
                call_tool_choice="auto",
                call_reasoning_effort=reasoning_effort,
                label="tool-phase",
            )

            while True:
                react_round += 1
                handled_tool_calls = False

                for choice in response.choices:
                    if not choice.message.tool_calls:
                        # Model returned a plain-text reply — it decided it
                        # has sufficient information to answer the user.
                        # NOTE: We do NOT stream this to stdout yet — if tools
                        # were executed, the final-answer phase will synthesize
                        # a proper answer from real search results.  We only
                        # use this inline text when no tools ran at all.
                        content = extract_text_content(choice.message.content)
                        if content:
                            collected_output += content
                        continue

                    # ---- Model wants to call a tool ------------------------
                    handled_tool_calls = True
                    tool_calls = choice.message.tool_calls
                    print(
                        f"[react-loop] Round {react_round}: model issued "
                        f"{len(tool_calls)} tool call(s)",
                        file=sys.stderr, flush=True,
                    )

                    # Record the assistant's tool-call turn in message history
                    messages.append(
                        {
                            "role": "assistant",
                            "content": choice.message.content or "",
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": tc.type,
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in tool_calls
                            ],
                        }
                    )

                    # Execute each tool call and append results to history
                    for tool_call in tool_calls:
                        if tool_call_count >= max_tool_calls:
                            print(
                                f"[react-loop] [WARN] Reached max_tool_calls="
                                f"{max_tool_calls}; stopping search loop",
                                file=sys.stderr, flush=True,
                            )
                            break

                        tool_call_count += 1
                        func_name = tool_call.function.name
                        try:
                            func_args = json.loads(tool_call.function.arguments)
                        except json.JSONDecodeError:
                            func_args = {"raw_arguments": tool_call.function.arguments}

                        print(
                            f"[tool-call-{tool_call_count}] {func_name}({func_args})",
                            file=sys.stderr,
                        )

                        if func_name == "search_web":
                            query = func_args.get("query", "")
                            print(
                                f"[tool-call-{tool_call_count}] [START] Executing "
                                f"search_web with query: {query}",
                                file=sys.stderr, flush=True,
                            )
                            # Always create a fresh event loop in this background
                            # thread — never use asyncio.get_event_loop() which may
                            # return the uvicorn main-thread loop and raise
                            # "This event loop is already running".
                            _tool_loop = asyncio.new_event_loop()
                            try:
                                tool_result = _tool_loop.run_until_complete(
                                    search_web(query)
                                )
                            except Exception as e:
                                print(
                                    f"[tool-call-{tool_call_count}] [ERROR] "
                                    f"Async execution failed: {e}",
                                    file=sys.stderr, flush=True,
                                )
                                import traceback
                                traceback.print_exc(file=sys.stderr)
                                tool_result = f"Error executing search: {str(e)}"
                            finally:
                                _tool_loop.close()

                            print(
                                f"[tool-call-{tool_call_count}] [DONE] search_web "
                                f"returned {len(tool_result)} chars",
                                file=sys.stderr, flush=True,
                            )
                        elif func_name == "navigate_url":
                            nav_url = func_args.get("url", "") or ""
                            inp_text = func_args.get("input_text") or ""
                            inp_sel  = func_args.get("input_selector") or ""
                            clk_sel  = func_args.get("click_selector") or ""
                            print(
                                f"[tool-call-{tool_call_count}] [START] Executing "
                                f"navigate_url: {nav_url}",
                                file=sys.stderr, flush=True,
                            )
                            _nav_loop = asyncio.new_event_loop()
                            try:
                                tool_result = _nav_loop.run_until_complete(
                                    navigate_url(
                                        url=nav_url,
                                        input_text=inp_text,
                                        input_selector=inp_sel,
                                        click_selector=clk_sel,
                                    )
                                )
                            except Exception as e:
                                tool_result = f"navigate_url failed: {e}"
                            finally:
                                _nav_loop.close()
                            print(
                                f"[tool-call-{tool_call_count}] [DONE] navigate_url "
                                f"returned {len(tool_result)} chars",
                                file=sys.stderr, flush=True,
                            )
                        elif func_name in ("open_web", "browse_web", "web_search", "browser", "open_browser"):
                            # The model hallucinated a tool from its training data.
                            # Redirect: extract URL or query and run search_web.
                            url_hint = (
                                func_args.get("id")
                                or func_args.get("url")
                                or func_args.get("query")
                                or ""
                            )
                            print(
                                f"[tool-call-{tool_call_count}] [REDIRECT] "
                                f"'{func_name}' → search_web('{url_hint[:80]}')",
                                file=sys.stderr, flush=True,
                            )
                            _redir_loop = asyncio.new_event_loop()
                            try:
                                tool_result = _redir_loop.run_until_complete(
                                    search_web(url_hint)
                                )
                            except Exception as e:
                                tool_result = f"Redirected search failed: {e}"
                            finally:
                                _redir_loop.close()
                        else:
                            tool_result = f"Unknown tool: {func_name}. Only 'search_web' is available."

                        executed_tools.append(
                            {
                                "name": func_name,
                                "arguments": func_args,
                                "result": tool_result,
                            }
                        )

                        # Summarize the tool result before adding it to the
                        # message history.  This keeps context small across
                        # ReAct rounds while preserving key facts the model
                        # needs to decide its next action.
                        # The FULL raw result is kept in executed_tools so the
                        # final-answer phase still has everything.
                        tool_result_for_history = summarize_tool_result(
                            tool_result, user_text, client
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": func_name,
                                "content": tool_result_for_history,
                            }
                        )

                # ── Decide whether to continue the ReAct loop ────────────────
                if not handled_tool_calls:
                    # Model returned plain text — it decided it has enough info.
                    # Only accept this if at least one tool call was already made;
                    # otherwise the response is just training-data hallucination.
                    if tool_call_count == 0:
                        print(
                            f"[react-loop] Model tried to answer without any tool calls. "
                            "Forcing a search call now.",
                            file=sys.stderr, flush=True,
                        )
                        messages.append({
                            "role": "user",
                            "content": "You have NOT performed any web searches yet. You MUST call search_web now before answering. Do not describe what you would search — call the tool immediately."
                        })
                        response = create_completion(
                            model,
                            messages,
                            temperature,
                            request_tools=TOOLS,
                            call_tool_choice="auto",
                            call_reasoning_effort=reasoning_effort,
                            label=f"react-{react_round}-forced",
                        )
                        continue
                    else:
                        # Model returned plain text after at least one search — accept it.
                        print(
                            f"[react-loop] Model answered directly on round {react_round} "
                            "(no further tool calls needed)",
                            file=sys.stderr, flush=True,
                        )
                        break

                if tool_call_count >= max_tool_calls:
                    print(
                        f"[react-loop] Hit max_tool_calls={max_tool_calls}; "
                        "exiting loop and synthesising final answer",
                        file=sys.stderr, flush=True,
                    )
                    break

                # Feed ALL tool results back to the tool model so it can reason:
                # "Was that search good enough? Do I need to refine my query?"
                _est_prompt = estimate_message_tokens(messages)
                print(
                    f"[react-loop] Round {react_round} done — asking model to evaluate "
                    f"results and decide next action... "
                    f"(estimated prompt tokens: {_est_prompt}/{TPM_LIMIT})",
                    file=sys.stderr, flush=True,
                )
                response = create_completion(
                    model,
                    messages,
                    temperature,
                    request_tools=TOOLS,
                    call_tool_choice="auto",
                    call_reasoning_effort=reasoning_effort,
                    label=f"react-{react_round}",
                )
            # ── End ReAct loop ───────────────────────────────────────────────

            # If any tools were executed, ALWAYS synthesize the final answer
            # from the actual search results — never use the inline "thinking"
            # monologue the model may have produced in the ReAct loop.
            if executed_tools:
                # Discard any inline text collected during the ReAct loop;
                # it is the model's reasoning stream, not the final answer.
                collected_output = ""
                print(
                    f"[final-phase] [START] Synthesizing answer from {len(executed_tools)} tool result(s)...",
                    file=sys.stderr, flush=True,
                )
                final_text = request_final_answer(executed_tools, resolved_final_model)
                if final_text is None:
                    final_text = build_tool_result_fallback(executed_tools)
                    print(
                        "[final-phase] [WARN] Falling back to raw tool output after "
                        "repeated invalid final answers",
                        file=sys.stderr, flush=True,
                    )
                print(final_text, end="", flush=True)
                collected_output = final_text

            if not collected_output:
                raise RuntimeError("Model returned no content and no tool calls")


            print()  # newline after streaming

            # ---------------------------------------------------------------
            # Usage summary — printed to stderr so it appears after the answer
            # ---------------------------------------------------------------
            sep = "-" * 52
            print(f"\n[usage] {sep}", file=sys.stderr, flush=True)
            print(f"[usage]  Query complete ({usage['api_calls']} API call(s))", file=sys.stderr, flush=True)
            print(f"[usage]  Tokens used this query:", file=sys.stderr, flush=True)
            print(f"[usage]    Prompt     : {usage['prompt_tokens']:>6}", file=sys.stderr, flush=True)
            print(f"[usage]    Completion : {usage['completion_tokens']:>6}", file=sys.stderr, flush=True)
            print(f"[usage]    Total      : {usage['total_tokens']:>6}", file=sys.stderr, flush=True)
            if usage["rl_limit_tokens"] is not None:
                print(f"[usage]  Rate limits (from last response headers):", file=sys.stderr, flush=True)
                print(
                    f"[usage]    TPM  limit     : {usage['rl_limit_tokens']}",
                    file=sys.stderr, flush=True,
                )
                print(
                    f"[usage]    TPM  remaining : {usage['rl_remaining_tokens']}  (resets in {usage['rl_reset_tokens']})",
                    file=sys.stderr, flush=True,
                )
                print(
                    f"[usage]    RPD  limit     : {usage['rl_limit_requests']}",
                    file=sys.stderr, flush=True,
                )
                print(
                    f"[usage]    RPD  remaining : {usage['rl_remaining_requests']}  (resets in {usage['rl_reset_requests']})",
                    file=sys.stderr, flush=True,
                )
            print(f"[usage] {sep}", file=sys.stderr, flush=True)

            return collected_output

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if attempt < retries:
                backoff = 1.5 ** (attempt + 1)
                print(
                    f"\n[error] Attempt {attempt + 1}/{retries + 1} failed: {exc}",
                    file=sys.stderr,
                )
                print(
                    f"[retry] Waiting {backoff:.1f}s before retry...", file=sys.stderr
                )
                time.sleep(backoff)
            else:
                print(f"\n[error] Failed after {retries + 1} attempts: {exc}", file=sys.stderr)
                raise


def main():
    browser_tools.SELECTED_CHROME_PROFILE = select_chrome_profile()
    
    parser = argparse.ArgumentParser(
        description="Groq streaming chat with browser-use tool calling"
    )
    parser.add_argument(
        "text",
        nargs="?",
        help="User input text. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--model",
        default="openai/gpt-oss-20b",
        help="Tool-calling model to use for phase 1.",
    )
    parser.add_argument(
        "--final-model",
        default=None,
        help="Optional tool-free model to use for the final answer phase.",
    )
    args = parser.parse_args()

    if args.text:
        user_text = args.text
    elif sys.stdin.isatty():
        user_text = input("Enter your prompt: ").strip()
    else:
        user_text = sys.stdin.read().strip()

    if not user_text:
        print("No input provided; exiting.", file=sys.stderr)
        sys.exit(1)

    stream_chat_with_tools(
        user_text,
        model=args.model,
        final_model=args.final_model,
    )


if __name__ == "__main__":
    main()
