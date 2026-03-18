"""
src/control/agents/email_processing_agent.py
=============================================
Kept for backward compatibility.
All logic has been split into:

  src/control/agents/
  ├── state.py          — EmailProcessingState TypedDict + build_initial_state()
  ├── graph.py          — build_email_processing_graph() + run_email_processing()
  └── nodes/
      ├── extract_text.py
      ├── extract_invoice.py
      ├── identify_invoice.py
      ├── classify_email.py
      ├── fetch_context.py
      ├── embed_and_search.py
      ├── resolve_dispute_link.py
      ├── generate_response.py
      └── persist_results.py

Existing callers (tasks.py) importing run_email_processing from this module
continue to work unchanged.
"""

from src.control.agents.graph import (  # noqa: F401
    run_email_processing,
    build_email_processing_graph,
)
from src.control.agents.state import EmailProcessingState  # noqa: F401
