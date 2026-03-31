"""
src/control/prompts/__init__.py
================================
Re-exports all prompt builders and registers them with Langfuse on first import
so every prompt call is traceable (name + version appear in the Langfuse UI).
"""

from .assign_dispute_type import (
    PROMPT_NAME as _ATN,
)
from .assign_dispute_type import (
    PROMPT_VERSION as _ATV,
)
from .assign_dispute_type import (
    build_assign_type_prompt,
)  # noqa
from .classify_email import (
    PROMPT_NAME as _CN,
)
from .classify_email import (
    PROMPT_VERSION as _CV,
)

# Keep the old classify_email builder available so any external callers
# (tests, notebooks) that import it directly don't break.
from .classify_email import (
    build_classify_prompt,
)  # noqa
from .detect_context_shift import (
    PROMPT_NAME as _DN,
)
from .detect_context_shift import (
    PROMPT_VERSION as _DV,
)
from .detect_context_shift import (
    build_detect_context_shift_prompt,
)  # noqa
from .extract_invoice import (
    PROMPT_NAME as _EN,
)
from .extract_invoice import (
    PROMPT_VERSION as _EV,
)
from .extract_invoice import (
    build_extract_invoice_prompt,
)  # noqa
from .generate_response import (
    PROMPT_NAME as _GN,
)
from .generate_response import (
    PROMPT_VERSION as _GV,
)
from .generate_response import (
    build_generate_response_prompt,
)  # noqa
from .structure_email import (
    PROMPT_NAME as _SEN,
)
from .structure_email import (
    PROMPT_VERSION as _SEV,
)
from .structure_email import (
    build_structure_prompt,
)  # noqa
from .summarize_episodes import (
    PROMPT_NAME as _SUN,
)
from .summarize_episodes import (
    PROMPT_VERSION as _SUV,
)
from .summarize_episodes import (
    build_summarize_episodes_prompt,
)  # noqa

__all__ = [
    "build_structure_prompt",
    "build_assign_type_prompt",
    "build_classify_prompt",  # legacy — node no longer uses this directly
    "build_generate_response_prompt",
    "build_extract_invoice_prompt",
    "build_summarize_episodes_prompt",
    "build_detect_context_shift_prompt",
    "PROMPTS_META",
]

# Metadata registry — consumed by Langfuse observation tagging
PROMPTS_META: dict[str, str] = {
    _SEN: _SEV,
    _ATN: _ATV,
    _GN: _GV,
    _EN: _EV,
    _SUN: _SUV,
    _DN: _DV,
    _CN: _CV,  # legacy
}
