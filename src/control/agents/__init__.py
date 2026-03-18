"""
src/control/agents/__init__.py
Re-exports run_email_processing so callers don't need to know the internal layout.
"""

from src.control.agents.graph import run_email_processing  # noqa: F401
