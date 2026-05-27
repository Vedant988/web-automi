"""
web_automi/core/exceptions.py
-----------------------------
Custom exceptions hierarchy for the Web-Automi system.
"""

class WebAutomiError(Exception):
    """Base exception for all Web-Automi errors."""
    pass

class ConfigurationError(WebAutomiError):
    """Raised when environment variables or API keys are missing or misconfigured."""
    pass

class DatabaseError(WebAutomiError):
    """Raised when SQLite operations fail or encounter lock conflicts."""
    pass

class BrowserError(WebAutomiError):
    """Raised when Playwright launches, navigations, or page interactions fail."""
    pass

class RateLimitError(WebAutomiError):
    """Raised when all API keys from the pool are exhausted or rate-limited."""
    pass

class AuthenticationError(WebAutomiError):
    """Raised when user registration, login, or session validation fails."""
    pass

class CancellationError(WebAutomiError):
    """Raised when the agent run loop is cancelled mid-execution."""
    pass
