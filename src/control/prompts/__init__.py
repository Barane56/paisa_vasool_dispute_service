"""
src/control/prompts/__init__.py
================================
Re-exports all prompt builders and registers them with Langfuse on first import
so every prompt call is traceable (name + version appear in the Langfuse UI).

Usage in nodes / llm_client:
    from src.control.prompts import build_classify_prompt, PROMPTS_META
"""

from .classify_email      import build_classify_prompt,              PROMPT_NAME as _CN,  PROMPT_VERSION as _CV   # noqa
from .generate_response   import build_generate_response_prompt,     PROMPT_NAME as _GN,  PROMPT_VERSION as _GV   # noqa
from .extract_invoice     import build_extract_invoice_prompt,       PROMPT_NAME as _EN,  PROMPT_VERSION as _EV   # noqa
from .summarize_episodes  import build_summarize_episodes_prompt,    PROMPT_NAME as _SN,  PROMPT_VERSION as _SV   # noqa

__all__ = [
    "build_classify_prompt",
    "build_generate_response_prompt",
    "build_extract_invoice_prompt",
    "build_summarize_episodes_prompt",
    "PROMPTS_META",
]

# Metadata registry — consumed by Langfuse observation tagging
PROMPTS_META: dict[str, str] = {
    _CN: _CV,
    _GN: _GV,
    _EN: _EV,
    _SN: _SV,
}
