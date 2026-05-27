"""
web_automi/core/interfaces.py
-----------------------------
Abstract Base Classes (interfaces) for Web-Automi modules.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IDatabase(ABC):
    """Interface for DB operations (users, sessions, tasks, task steps)."""

    @abstractmethod
    def init_db(self) -> None:
        """Initialize tables and run migrations."""
        pass

    @abstractmethod
    def register_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Register a new user. Return user dict or None if already exists."""
        pass

    @abstractmethod
    def login_user(self, username: str, password: str) -> Optional[str]:
        """Verify user and generate session token, or None if invalid."""
        pass

    @abstractmethod
    def get_user_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate session token and return user dict, or None."""
        pass

    @abstractmethod
    def logout_user(self, token: str) -> None:
        """Destroy user session token."""
        pass

    @abstractmethod
    def create_task(self, prompt: str, user_id: Optional[int] = None, model: str = "openai/gpt-oss-120b") -> Dict[str, Any]:
        """Create a new task in active running state."""
        pass

    @abstractmethod
    def list_tasks(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """List tasks, optionally filtered by user_id."""
        pass

    @abstractmethod
    def get_task(self, task_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Retrieve task details, optionally validating user_id."""
        pass

    @abstractmethod
    def update_task(self, task_id: str, status: str, result: Optional[str] = None) -> None:
        """Update task status and optional final result string."""
        pass

    @abstractmethod
    def delete_task(self, task_id: str, user_id: Optional[int] = None) -> bool:
        """Delete a task, return True on success."""
        pass

    @abstractmethod
    def add_step(self, task_id: str, step_number: int, step_type: str, status: str, title: str, detail: str = "", browser_url: str = "") -> None:
        """Log a specific progression step event for a task."""
        pass

    @abstractmethod
    def get_steps(self, task_id: str) -> List[Dict[str, Any]]:
        """Retrieve all progression steps logged for a task."""
        pass


class IBrowserService(ABC):
    """Interface for automated web browsing, searching and visual Set-of-Marks VLM routing."""

    @abstractmethod
    async def search_web(self, query: str, timeout: int = 90) -> str:
        """Execute a search across multiple search engines and extract top results."""
        pass

    @abstractmethod
    async def navigate_url(
        self,
        url: str,
        input_text: str = "",
        input_selector: str = "",
        click_selector: str = "",
        timeout: int = 60
    ) -> str:
        """Open a specific URL, optionally enter text, click, and return page content."""
        pass


class IPromptEngine(ABC):
    """Interface for compilation of dynamic system prompts and tools schemas."""

    @abstractmethod
    def get_system_prompt(self, user_text: str = "") -> str:
        """Compile dynamic persona system prompt with current IST timestamp."""
        pass

    @abstractmethod
    def get_final_answer_prompt(self, user_text: str = "") -> str:
        """Get instructions for final plain-text answer synthesis."""
        pass

    @abstractmethod
    def get_tools_definition(self) -> List[Dict[str, Any]]:
        """Retrieve list of tool schemas for agent registration."""
        pass


class IAgent(ABC):
    """Interface for stateful ReAct loop execution agent."""

    @abstractmethod
    def stream_chat_with_tools(
        self,
        user_text: str,
        model: str = "openai/gpt-oss-20b",
        temperature: float = 0.0,
        max_completion_tokens: int = 2048,
        final_model: Optional[str] = None
    ) -> str:
        """Execute a stateful two-phase ReAct run loop and return the final plain-text result."""
        pass
