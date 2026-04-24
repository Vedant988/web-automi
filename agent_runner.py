"""
agent_runner.py
---------------
Wraps the existing groq_chat engine with structured step capture.
Parses stderr debug logs into structured AgentStep events and
exposes them via a thread-safe queue for real-time SSE streaming.
"""

import io
import re
import sys
import json
import queue
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field


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
    """Captures stderr writes and parses them into structured agent steps."""

    def __init__(self, event_queue: queue.Queue, original_stderr):
        super().__init__()
        self.queue = event_queue
        self.original_stderr = original_stderr
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

        # Ignore [usage], [rate-limit], generic [WARN] lines
        return None

class StdoutCapture(io.StringIO):
    def __init__(self, event_queue, original_stdout=None):
        super().__init__()
        self.event_queue = event_queue
        self.original_stdout = original_stdout

    def write(self, s):
        if s:
            now = datetime.now(timezone.utc).isoformat()
            self.event_queue.put(AgentStep(0, "stream", "running", "", s, now))
        if self.original_stdout:
            self.original_stdout.write(s)
            self.original_stdout.flush()
        return super().write(s)



class AgentRunner:
    """Manages agent execution in a background thread with real-time step events."""

    def __init__(self):
        self.event_queue: queue.Queue = queue.Queue()
        self.status = "idle"
        self.result = None
        self.steps: list[AgentStep] = []
        self._thread = None

    @property
    def is_running(self):
        return self.status == "running"

    def start(self, prompt: str, model: str = "openai/gpt-oss-120b", final_model: str | None = None):
        if self.is_running:
            raise RuntimeError("Agent is already running")

        self.event_queue = queue.Queue()
        self.status = "running"
        self.result = None
        self.steps = []

        self._thread = threading.Thread(
            target=self._run, args=(prompt, model, final_model), daemon=True
        )
        self._thread.start()

    def stop(self):
        self.status = "failed"
        now = datetime.now(timezone.utc).isoformat()
        self.event_queue.put(AgentStep(
            len(self.steps) + 1, "error", "failed", "Agent stopped", "Stopped by user", now
        ))

    def _run(self, prompt, model, final_model):
        from groq_chat import stream_chat_with_tools

        original_stderr, original_stdout = sys.stderr, sys.stdout
        capture = StderrCapture(self.event_queue, original_stderr)
        stdout_capture = StdoutCapture(self.event_queue, original_stdout)

        try:
            sys.stderr = capture
            sys.stdout = stdout_capture

            result = stream_chat_with_tools(prompt, model=model, final_model=final_model)

            sys.stderr = original_stderr
            sys.stdout = original_stdout

            self.result = (result or "").strip() or stdout_capture.getvalue().strip()
            self.status = "completed"

            now = datetime.now(timezone.utc).isoformat()
            self.event_queue.put(AgentStep(
                capture.step_count + 1, "done", "success", "Task complete", "", now
            ))
        except Exception as exc:
            sys.stderr = original_stderr
            sys.stdout = original_stdout
            self.status = "failed"
            self.result = f"Error: {exc}"
            now = datetime.now(timezone.utc).isoformat()
            self.event_queue.put(AgentStep(
                capture.step_count + 1, "error", "failed", "Task failed", str(exc), now
            ))

    def get_events(self):
        """Generator yielding step events. Yields None as heartbeat."""
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
                yield None  # heartbeat
