"""
web_automi/core/runner.py
-------------------------
Shared runner singleton instance for Web-Automi.
"""

from web_automi.agent.runner import ModularAgentRunner

runner = ModularAgentRunner()
