"""
web_automi/agent/engine.py
--------------------------
Stateful OOP ReActAgent concrete implementation of the IAgent interface.
"""

import os
import sys
import gc
import time
import json
import asyncio
from collections import deque
from typing import Any, Dict, List, Optional

import web_automi.core.config as config
from web_automi.core.interfaces import IAgent
from web_automi.core.exceptions import RateLimitError, CancellationError, BrowserError
from web_automi.services.browser_service import PlaywrightBrowserService
from web_automi.services.prompt_engine import PromptEngine


class ReActAgent(IAgent):
    """OOP ReAct Agent coordinating token scaling, dynamic key pool rotation, and context pruning."""

    def __init__(self, browser_service: PlaywrightBrowserService, prompt_engine: PromptEngine):
        self.browser = browser_service
        self.prompt_engine = prompt_engine
        self.usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "api_calls": 0,
            "rl_limit_requests": None,
            "rl_remaining_requests": None,
            "rl_reset_requests": None,
            "rl_limit_tokens": None,
            "rl_remaining_tokens": None,
            "rl_reset_tokens": None,
        }

    def estimate_tokens(self, text: str, is_json: bool = False) -> int:
        if not text:
            return 1
        factor = 2.5 if is_json else 3.0
        return max(1, int(len(text) / factor))

    def estimate_message_tokens(self, chat_messages: List[Dict[str, Any]]) -> int:
        total = 0
        from groq_chat import extract_text_content
        for message in chat_messages:
            total += self.estimate_tokens(extract_text_content(message.get("content", "")), is_json=False)
            if "tool_calls" in message:
                total += self.estimate_tokens(json.dumps(message["tool_calls"]), is_json=True)
            if "name" in message:
                total += self.estimate_tokens(message["name"])
        return max(1, total)

    def build_final_answer_messages(self, user_text: str, executed_tools: List[Dict[str, Any]], retry: int = 0) -> List[Dict[str, Any]]:
        tool_sections = []
        from groq_chat import render_tool_result
        for index, item in enumerate(executed_tools, start=1):
            tool_sections.append(
                "\n".join([
                    f"Tool #{index}: {item['name']}",
                    f"Arguments: {json.dumps(item['arguments'], ensure_ascii=True)}",
                    "Result:",
                    render_tool_result(item["result"]),
                ])
            )

        messages = [
            {"role": "system", "content": self.prompt_engine.get_final_answer_prompt(user_text)},
            {
                "role": "user",
                "content": "\n\n".join([
                    "User request:",
                    user_text.strip(),
                    "Tool results you must use:",
                    "\n\n".join(tool_sections) if tool_sections else "No tool results were available.",
                    "Write the final user-facing answer in plain text.",
                ]),
            },
        ]

        if retry > 0:
            messages.append({
                "role": "user",
                "content": (
                    "Your previous reply was invalid because it was empty or attempted tool syntax. "
                    "Return only a plain-text final answer with no tool calls, no JSON, and no code fences."
                ),
            })
        return messages

    def create_completion(
        self,
        call_model: str,
        call_messages: List[Dict[str, Any]],
        call_temperature: float,
        *,
        request_tools: Optional[List[Dict[str, Any]]] = None,
        call_tool_choice: Optional[str] = None,
        call_reasoning_effort: Optional[str] = None,
        label: str = "groq",
    ) -> Any:
        tpm_safety_margin = 800
        safe_tpm_limit = config.TPM_LIMIT - tpm_safety_margin

        prompt_tokens = self.estimate_message_tokens(call_messages)
        
        # Self-healing: if prompt exceeds safe limit, prune older tool outputs in-place
        if prompt_tokens >= safe_tpm_limit - 50:
            print(
                f"[rate-limit] Prompt size {prompt_tokens} is close to safe limit {safe_tpm_limit}. "
                "Pruning older tool results to reclaim token budget...",
                file=sys.stderr, flush=True
            )
            for msg in call_messages:
                if msg.get("role") == "tool" and msg.get("content"):
                    content = msg["content"]
                    if len(content) > 600:
                        msg["content"] = content[:500] + "\n...[truncated to fit rate limit]"
                        prompt_tokens = self.estimate_message_tokens(call_messages)
                        if prompt_tokens < safe_tpm_limit - 150:
                            break
            if prompt_tokens >= safe_tpm_limit - 50:
                for msg in call_messages:
                    if msg.get("role") == "tool" and msg.get("content"):
                        msg["content"] = "[highly compressed to fit rate limit]"
                prompt_tokens = self.estimate_message_tokens(call_messages)

        desired_completion = 2048
        total_requested = prompt_tokens + desired_completion

        if total_requested > safe_tpm_limit:
            desired_completion = max(100, safe_tpm_limit - prompt_tokens)
            total_requested = prompt_tokens + desired_completion
            print(
                f"[rate-limit] Adjusted completion tokens to {desired_completion} for {label} "
                f"to stay within safe TPM limit ({safe_tpm_limit})",
                file=sys.stderr, flush=True
            )

        if desired_completion <= 0:
            raise RuntimeError("Prompt too large to fit token limit")

        tool_count = len(request_tools) if request_tools else 0
        current_reasoning_effort = call_reasoning_effort
        current_tool_choice = call_tool_choice

        max_api_retries = max(3, len(config._API_KEY_POOL) + 1)
        keys_tried = set()

        for api_attempt in range(max_api_retries):
            # Dynamic stateful request parameters
            request_kwargs = {
                "model": call_model,
                "messages": call_messages,
                "temperature": call_temperature,
                "max_completion_tokens": desired_completion,
                "top_p": 0.9,
            }
            if request_tools is not None:
                request_kwargs["tools"] = request_tools
                if current_tool_choice is not None:
                    request_kwargs["tool_choice"] = current_tool_choice
            
            supports_reasoning_effort = (
                current_reasoning_effort is not None
                and call_model not in config.MODELS_WITHOUT_REASONING_EFFORT
            )
            if supports_reasoning_effort:
                request_kwargs["reasoning_effort"] = current_reasoning_effort

            print(
                f"[{label}] [START] Sending request with {len(call_messages)} message(s), tools={tool_count} (attempt {api_attempt + 1}/{max_api_retries})",
                file=sys.stderr, flush=True
            )
            print(
                f"[{label}] Model={call_model}, temp={call_temperature}, reasoning={current_reasoning_effort or 'default'}, completion_tokens={desired_completion}",
                file=sys.stderr, flush=True
            )

            client = config.build_client()
            if not client:
                raise RuntimeError("API keys pool is empty or misconfigured")

            try:
                raw = client.chat.completions.with_raw_response.create(**request_kwargs)
                response = raw.parse()
                break
            except Exception as exc:
                error_text = str(exc)
                if supports_reasoning_effort and "`reasoning_effort` is not supported with this model" in error_text:
                    config.MODELS_WITHOUT_REASONING_EFFORT.add(call_model)
                    current_reasoning_effort = None
                    print(
                        f"[{label}] [WARN] {call_model} does not support reasoning_effort; retrying without it",
                        file=sys.stderr, flush=True
                    )
                    continue
                elif "429" in error_text or "rate limit" in error_text.lower():
                    keys_tried.add(config._current_key_index)
                    if len(config._API_KEY_POOL) > 1 and len(keys_tried) < len(config._API_KEY_POOL):
                        config.rotate_key()
                        print(
                            f"[{label}] [WARN] Rate limit on key #{list(keys_tried)[-1]+1}. Retrying now...",
                            file=sys.stderr, flush=True,
                        )
                    elif api_attempt < max_api_retries - 1:
                        import re
                        wait_match = re.search(r"try again in ([\d\.]+)s", error_text)
                        wait_s = float(wait_match.group(1)) + 1.0 if wait_match else 30.0
                        print(f"[{label}] [WARN] Rate limit hit. Waiting {wait_s:.1f}s...", file=sys.stderr, flush=True)
                        time.sleep(wait_s)
                    else:
                        raise RateLimitError(f"Rate limit: all API keys exhausted. Error: {error_text}")
                elif "tool_use_failed" in error_text or "failed_generation" in error_text:
                    print(
                        f"[{label}] [WARN] Model produced broken tool-call format. Retrying without reasoning...",
                        file=sys.stderr, flush=True,
                    )
                    current_reasoning_effort = None
                    if current_tool_choice == "required":
                        current_tool_choice = "auto"
                    config.MODELS_WITHOUT_REASONING_EFFORT.add(call_model)
                    continue
                elif "Tool choice is required, but model did not call a tool" in error_text:
                    print(
                        f"[{label}] [WARN] Model skipped a required tool call. Retrying with tool_choice=auto...",
                        file=sys.stderr, flush=True,
                    )
                    current_tool_choice = "auto"
                    current_reasoning_effort = None
                    config.MODELS_WITHOUT_REASONING_EFFORT.add(call_model)
                    continue
                else:
                    raise

        self.usage["api_calls"] += 1
        if response.usage:
            self.usage["prompt_tokens"] += response.usage.prompt_tokens or 0
            self.usage["completion_tokens"] += response.usage.completion_tokens or 0
            self.usage["total_tokens"] += response.usage.total_tokens or 0

        hdrs = dict(raw.headers)
        self.usage["rl_limit_requests"] = hdrs.get("x-ratelimit-limit-requests")
        self.usage["rl_remaining_requests"] = hdrs.get("x-ratelimit-remaining-requests")
        self.usage["rl_reset_requests"] = hdrs.get("x-ratelimit-reset-requests")
        self.usage["rl_limit_tokens"] = hdrs.get("x-ratelimit-limit-tokens")
        self.usage["rl_remaining_tokens"] = hdrs.get("x-ratelimit-remaining-tokens")
        self.usage["rl_reset_tokens"] = hdrs.get("x-ratelimit-reset-tokens")

        return response

    def request_final_answer(self, user_text: str, executed_tools: List[Dict[str, Any]], resolved_final_model: str) -> Optional[str]:
        from groq_chat import looks_like_tool_output
        for final_attempt in range(3):
            final_messages = self.build_final_answer_messages(user_text, executed_tools, retry=final_attempt)
            response = self.create_completion(
                resolved_final_model,
                final_messages,
                0.0,
                call_tool_choice=None,
                call_reasoning_effort="low",
                label="final-phase",
            )

            for choice in response.choices:
                from groq_chat import extract_text_content
                content = extract_text_content(choice.message.content).strip()
                has_tool_calls = bool(choice.message.tool_calls)

                if has_tool_calls or not content or looks_like_tool_output(content):
                    continue
                return content
        return None

    def stream_chat_with_tools(
        self,
        user_text: str,
        model: str = "openai/gpt-oss-20b",
        temperature: float = 0.0,
        max_completion_tokens: int = 2048,
        final_model: Optional[str] = None
    ) -> str:
        resolved_final_model = final_model or config.DEFAULT_FINAL_MODEL
        
        # Reset dynamic cancellation flag
        config.AGENT_SHOULD_STOP = False

        _date_ctx = self.prompt_engine.get_system_prompt(user_text)
        messages = [
            {"role": "system", "content": _date_ctx},
            {"role": "user", "content": user_text},
        ]
        collected_output = ""
        tool_call_count = 0
        executed_tools = []
        react_round = 0

        response = self.create_completion(
            model,
            messages,
            temperature,
            request_tools=self.prompt_engine.get_tools_definition(),
            call_tool_choice="auto",
            call_reasoning_effort=config.DEFAULT_REASONING_EFFORT,
            label="tool-phase",
        )

        while True:
            # Cooperative Cancellation Guard
            if config.AGENT_SHOULD_STOP:
                raise CancellationError("Execution stopped by user.")

            react_round += 1
            handled_tool_calls = False

            for choice in response.choices:
                if not choice.message.tool_calls:
                    from groq_chat import extract_text_content
                    content = extract_text_content(choice.message.content)
                    if content:
                        collected_output += content
                    continue

                handled_tool_calls = True
                tool_calls = choice.message.tool_calls

                messages.append({
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
                        } for tc in tool_calls
                    ],
                })

                for tool_call in tool_calls:
                    if config.AGENT_SHOULD_STOP:
                        raise CancellationError("Execution stopped by user.")

                    if tool_call_count >= 6:  # max tool calls
                        break

                    tool_call_count += 1
                    func_name = tool_call.function.name
                    try:
                        func_args = json.loads(tool_call.function.arguments)
                    except Exception:
                        func_args = {"raw_arguments": tool_call.function.arguments}

                    print(f"[tool-call-{tool_call_count}] {func_name}({func_args})", file=sys.stderr)

                    # Execute modular services
                    if func_name == "search_web":
                        q = func_args.get("query", "")
                        print(f"[tool-call-{tool_call_count}] [START] Executing search_web: {q}", file=sys.stderr, flush=True)
                        _tool_loop = asyncio.new_event_loop()
                        try:
                            tool_result = _tool_loop.run_until_complete(self.browser.search_web(q))
                        finally:
                            _tool_loop.close()
                    elif func_name == "navigate_url":
                        nav_url = func_args.get("url", "")
                        print(f"[tool-call-{tool_call_count}] [START] Executing navigate_url: {nav_url}", file=sys.stderr, flush=True)
                        _tool_loop = asyncio.new_event_loop()
                        try:
                            tool_result = _tool_loop.run_until_complete(
                                self.browser.navigate_url(
                                    url=nav_url,
                                    input_text=func_args.get("input_text", ""),
                                    input_selector=func_args.get("input_selector", ""),
                                    click_selector=func_args.get("click_selector", ""),
                                )
                            )
                        finally:
                            _tool_loop.close()
                    else:
                        tool_result = f"Unknown tool: {func_name}"

                    executed_tools.append({
                        "name": func_name,
                        "arguments": func_args,
                        "result": tool_result
                    })

                    from groq_chat import summarize_tool_result, build_client
                    client = config.build_client()
                    tool_result_for_history = summarize_tool_result(tool_result, user_text, client)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": tool_result_for_history,
                    })

            if not handled_tool_calls:
                if tool_call_count == 0:
                    messages.append({
                        "role": "user",
                        "content": "You have NOT performed any web searches yet. You MUST call search_web now before answering."
                    })
                    response = self.create_completion(
                        model,
                        messages,
                        temperature,
                        request_tools=self.prompt_engine.get_tools_definition(),
                        call_tool_choice="auto",
                        call_reasoning_effort=config.DEFAULT_REASONING_EFFORT,
                        label=f"react-{react_round}-forced",
                    )
                    continue
                else:
                    break

            if tool_call_count >= 6:
                break

            response = self.create_completion(
                model,
                messages,
                temperature,
                request_tools=self.prompt_engine.get_tools_definition(),
                call_tool_choice="auto",
                call_reasoning_effort=config.DEFAULT_REASONING_EFFORT,
                label=f"react-{react_round}",
            )

        if executed_tools:
            collected_output = ""
            final_text = self.request_final_answer(user_text, executed_tools, resolved_final_model)
            if final_text is None:
                from groq_chat import build_tool_result_fallback
                final_text = build_tool_result_fallback(executed_tools)
            print(final_text, end="", flush=True)
            collected_output = final_text

        if not collected_output:
            raise RuntimeError("Model returned no content")

        gc.collect()
        return collected_output
