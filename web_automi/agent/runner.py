"""
web_automi/agent/runner.py
--------------------------
Thread-safe ModularAgentRunner coordinating background execution and real-time streaming.
"""

import sys
import queue
import threading
from datetime import datetime, timezone

import web_automi.core.config as config
from web_automi.core.exceptions import WebAutomiError
from web_automi.agent.engine import ReActAgent
from web_automi.services.browser_service import PlaywrightBrowserService, force_kill_browser
from web_automi.services.prompt_engine import PromptEngine
from agent_runner import AgentStep, StderrCapture, StdoutCapture


class ModularAgentRunner:
    """Thread-safe Agent Runner orchestrating background ReAct loops and WebSocket progression events."""

    def __init__(self):
        self.event_queue: queue.Queue = queue.Queue()
        self.status = "idle"
        self.result = None
        self.steps = []
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    def start(self, prompt: str, model: str = "openai/gpt-oss-120b", final_model: str | None = None) -> None:
        if self.is_running:
            raise RuntimeError("Agent is already active")

        self.event_queue = queue.Queue()
        self.status = "running"
        self.result = None
        self.steps = []

        self._thread = threading.Thread(
            target=self._run,
            args=(prompt, model, final_model),
            daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        # State-driven cancellation
        config.AGENT_SHOULD_STOP = True
        self.status = "failed"
        now = datetime.now(timezone.utc).isoformat()
        self.event_queue.put(AgentStep(
            len(self.steps) + 1, "error", "failed", "Agent stopped", "Stopped by user", now
        ))
        # Direct active browser termination to force break thread hangs instantly
        force_kill_browser()

    def _run(self, prompt: str, model: str, final_model: str | None) -> None:
        # Reset cooperative cancel token on start
        config.AGENT_SHOULD_STOP = False

        original_stderr, original_stdout = sys.stderr, sys.stdout
        capture = StderrCapture(self.event_queue, original_stderr)
        stdout_capture = StdoutCapture(self.event_queue, original_stdout)

        try:
            sys.stderr = capture
            sys.stdout = stdout_capture

            # Initialize decoupled OOP services
            db_path = config.DB_PATH
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
            self.event_queue.put(AgentStep(
                capture.step_count + 1, "done", "success", "Task complete", "", now
            ))
        except BaseException as exc:
            self.status = "failed"
            self.result = f"Error: {exc}"
            now = datetime.now(timezone.utc).isoformat()
            self.event_queue.put(AgentStep(
                capture.step_count + 1, "error", "failed", "Task failed", str(exc), now
            ))
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
