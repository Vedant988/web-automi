"""
web_automi/agent/runner.py
--------------------------
Thread-safe ModularAgentRunner coordinating background execution and real-time streaming,
with automatic self-healing database persistence for all progression steps.
"""

import io
import re
import sys
import json
import queue
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

import web_automi.core.config as config
from web_automi.core.exceptions import WebAutomiError
from web_automi.agent.engine import ReActAgent
from web_automi.services.browser_service import PlaywrightBrowserService, force_kill_browser
from web_automi.services.prompt_engine import PromptEngine


@dataclass
class AgentStep:
    step_number: int
    type: str        # init, thinking, tool_call, browsing, summarizing, reasoning, answering, error, done
    status: str      # running, success, failed
    title: str
    detail: str
    timestamp: str
    browser_url: str = ""

    def to_dict(self):
        return asdict(self)


class StderrCapture(io.TextIOBase):
    """Captures stderr writes and parses them into structured agent steps with automated database sync."""

    def __init__(self, event_queue: queue.Queue, original_stderr, task_id: str | None = None):
        super().__init__()
        self.queue = event_queue
        self.original_stderr = original_stderr
        self.task_id = task_id
        self.buffer = ""
        self.step_count = 0
        self._current_tool = None

    def write(self, text):
        if self.original_stderr:
            self.original_stderr.write(text)
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()
            if line:
                step = self._parse_line(line)
                if step:
                    self.queue.put(step)
                    if self.task_id:
                        try:
                            from web_automi.core.database import db
                            db.add_step(
                                self.task_id,
                                step.step_number,
                                step.type,
                                step.status,
                                step.title,
                                step.detail,
                                step.browser_url
                            )
                        except Exception:
                            pass
        return len(text)

    def flush(self):
        if self.original_stderr:
            self.original_stderr.flush()

    def _emit(self, typ, status, title, detail, **kw):
        now = datetime.now(timezone.utc).isoformat()
        return AgentStep(self.step_count, typ, status, title, detail, now, **kw)

    def _parse_line(self, line: str):
        # --- Initialization ---
        if line.startswith("[init]"):
            self.step_count += 1
            return self._emit("init", "success", "Initializing agent", line.split("]", 1)[-1].strip())

        # --- Tool phase (LLM thinking) ---
        if "[tool-phase] [START]" in line:
            self.step_count += 1
            return self._emit("thinking", "running", "Analyzing your query", "Sending request to AI model…")
        if "[tool-phase] [DONE]" in line:
            return self._emit("thinking", "success", "Analysis complete", "")

        # --- Tool calls ---
        tc = re.match(r'\[tool-call-(\d+)\]\s+(\w+)\((.+)\)', line)
        if tc:
            _, func, args = tc.groups()
            self.step_count += 1
            self._current_tool = func
            try:
                pa = json.loads(args)
                detail = pa.get("query", pa.get("url", args[:100]))
            except Exception:
                detail = args[:100]
            return self._emit("tool_call", "running", f"Calling {func}", str(detail))

        if re.match(r'\[tool-call-\d+\] \[START\]', line):
            return self._emit("tool_call", "running", f"Executing {self._current_tool or 'tool'}", line.split("[START]")[-1].strip())
        if re.match(r'\[tool-call-\d+\] \[DONE\]', line):
            return self._emit("tool_call", "success", "Tool completed", line.split("[DONE]")[-1].strip())

        # --- Browser actions ---
        if "[browser-use] [START] Searching with" in line:
            engine = line.split("Searching with")[-1].split(":")[0].strip()
            return self._emit("browsing", "running", f"Searching ({engine})", "")
        if "[browser-use] [DONE]" in line:
            return self._emit("browsing", "success", "Search complete", "")
        if "[browser-use] [WARN]" in line:
            return self._emit("browsing", "running", "Browser warning", line.split("[WARN]")[-1].strip())
        if "[browser-use] [START]" in line:
            return self._emit("browsing", "running", "Browser active", line.split("[START]")[-1].strip())

        # --- Navigation ---
        if "[navigate_url]" in line:
            if "Navigating to:" in line:
                url = line.split("Navigating to:")[-1].strip()
                return self._emit("browsing", "running", "Navigating to page", url, browser_url=url)
            if "Done" in line:
                return self._emit("browsing", "success", "Page loaded", "")
            return self._emit("browsing", "running", "Browser action", line.split("]", 1)[-1].strip())

        # --- Summarizer ---
        if "[summarizer]" in line and "Compressed" in line:
            return self._emit("summarizing", "success", "Compressing results", line.split("]", 1)[-1].strip())

        # --- ReAct loop ---
        if "[react-loop]" in line:
            detail = line.split("]", 1)[-1].strip()
            if "asking model to evaluate" in line:
                self.step_count += 1
                return self._emit("reasoning", "running", "Evaluating results", detail)
            if "Model answered directly" in line:
                return self._emit("reasoning", "success", "Agent has enough info", detail)
            if "Hit max_tool_calls" in line:
                return self._emit("reasoning", "success", "Max searches reached", detail)
            return self._emit("reasoning", "running", "Agent reasoning", detail)

        # --- React re-evaluation rounds ---
        if re.match(r'\[react-\d+\] \[START\]', line):
            self.step_count += 1
            return self._emit("thinking", "running", "Re-evaluating", "Deciding next action…")
        if re.match(r'\[react-\d+\] \[DONE\]', line):
            return self._emit("thinking", "success", "Evaluation complete", "")

        # --- Final answer phase ---
        if "[final-phase]" in line:
            if "[START]" in line:
                self.step_count += 1
                return self._emit("answering", "running", "Generating final answer", "Synthesizing information…")
            if "[DONE]" in line:
                return self._emit("answering", "success", "Answer ready", "")
            if "Choice:" in line:
                return self._emit("answering", "running", "Processing answer", line.split("Choice:")[-1].strip())
            return None

        # --- Errors ---
        if "[error]" in line:
            self.step_count += 1
            return self._emit("error", "failed", "Error occurred", line.split("]", 1)[-1].strip())

        # --- Visual agent ---
        if "[visual]" in line:
            return self._emit("browsing", "running", "Visual analysis", line.split("]", 1)[-1].strip())

        return None


class StdoutCapture(io.StringIO):
    """Captures stdout streams and pushes them to execution queue with selective database logging."""

    def __init__(self, event_queue: queue.Queue, original_stdout=None, task_id: str | None = None):
        super().__init__()
        self.event_queue = event_queue
        self.original_stdout = original_stdout
        self.task_id = task_id

    def write(self, s):
        if s:
            now = datetime.now(timezone.utc).isoformat()
            step = AgentStep(0, "stream", "running", "", s, now)
            self.event_queue.put(step)
            if self.task_id and s.strip():
                try:
                    from web_automi.core.database import db
                    db.add_step(
                        self.task_id,
                        step.step_number,
                        step.type,
                        step.status,
                        step.title,
                        step.detail,
                        step.browser_url
                    )
                except Exception:
                    pass
        if self.original_stdout:
            self.original_stdout.write(s)
            self.original_stdout.flush()
        return super().write(s)


class ModularAgentRunner:
    """Thread-safe Agent Runner orchestrating background ReAct loops and WebSocket progression events."""

    def __init__(self):
        self.event_queue: queue.Queue = queue.Queue()
        self.status = "idle"
        self.result = None
        self.steps = []
        self._thread = None
        self.task_id = None

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    def start(self, prompt: str, model: str = "openai/gpt-oss-120b", final_model: str | None = None, task_id: str | None = None) -> None:
        if self.is_running:
            raise RuntimeError("Agent is already active")

        self.event_queue = queue.Queue()
        self.status = "running"
        self.result = None
        self.steps = []
        self.task_id = task_id

        self._thread = threading.Thread(
            target=self._run,
            args=(prompt, model, final_model, task_id),
            daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        # State-driven cancellation
        config.AGENT_SHOULD_STOP = True
        self.status = "failed"
        now = datetime.now(timezone.utc).isoformat()
        step = AgentStep(
            len(self.steps) + 1, "error", "failed", "Agent stopped", "Stopped by user", now
        )
        self.event_queue.put(step)
        if self.task_id:
            try:
                from web_automi.core.database import db
                db.add_step(
                    self.task_id,
                    step.step_number,
                    step.type,
                    step.status,
                    step.title,
                    step.detail,
                    step.browser_url
                )
                db.update_task(self.task_id, "failed", "Stopped by user")
            except Exception:
                pass
        # Direct active browser termination to force break thread hangs instantly
        force_kill_browser()

    def _run(self, prompt: str, model: str, final_model: str | None, task_id: str | None = None) -> None:
        # Reset cooperative cancel token on start
        config.AGENT_SHOULD_STOP = False

        original_stderr, original_stdout = sys.stderr, sys.stdout
        capture = StderrCapture(self.event_queue, original_stderr, task_id=task_id)
        stdout_capture = StdoutCapture(self.event_queue, original_stdout, task_id=task_id)

        try:
            sys.stderr = capture
            sys.stdout = stdout_capture

            # Initialize decoupled OOP services
            browser = PlaywrightBrowserService()
            prompter = PromptEngine()
            agent = ReActAgent(browser_service=browser, prompt_engine=prompter)

            result = agent.stream_chat_with_tools(
                user_text=prompt,
                model=model,
                final_model=final_model
            )

            self.result = (result or "").strip() or stdout_capture.getvalue().strip()
            self.status = "completed"

            now = datetime.now(timezone.utc).isoformat()
            done_step = AgentStep(
                capture.step_count + 1, "done", "success", "Task complete", "", now
            )
            self.event_queue.put(done_step)
            if task_id:
                try:
                    from web_automi.core.database import db
                    db.add_step(
                        task_id,
                        done_step.step_number,
                        done_step.type,
                        done_step.status,
                        done_step.title,
                        done_step.detail,
                        done_step.browser_url
                    )
                    db.update_task(task_id, "completed", self.result)
                except Exception:
                    pass
        except BaseException as exc:
            self.status = "failed"
            self.result = f"Error: {exc}"
            now = datetime.now(timezone.utc).isoformat()
            err_step = AgentStep(
                capture.step_count + 1, "error", "failed", "Task failed", str(exc), now
            )
            self.event_queue.put(err_step)
            if task_id:
                try:
                    from web_automi.core.database import db
                    db.add_step(
                        task_id,
                        err_step.step_number,
                        err_step.type,
                        err_step.status,
                        err_step.title,
                        err_step.detail,
                        err_step.browser_url
                    )
                    db.update_task(task_id, "failed", self.result)
                except Exception:
                    pass
        finally:
            sys.stderr = original_stderr
            sys.stdout = original_stdout

    def get_events(self):
        while True:
            try:
                step = self.event_queue.get(timeout=0.5)
                self.steps.append(step)
                yield step
                if step.type in ("done", "error") and step.status in ("success", "failed"):
                    break
            except queue.Empty:
                if not self.is_running:
                    break
                yield None
