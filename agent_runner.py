"""
agent_runner.py
---------------
Backwards compatibility wrapper delegating to the new modular runner.
"""

from web_automi.agent.runner import ModularAgentRunner, AgentStep, StderrCapture, StdoutCapture

class AgentRunner(ModularAgentRunner):
    """Backwards compatibility wrapper for ModularAgentRunner."""
    pass
