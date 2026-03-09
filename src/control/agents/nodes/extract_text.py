"""
src/control/agents/nodes/extract_text.py
"""

from __future__ import annotations
import re
import logging
from typing import List

from src.observability import observe
from src.control.agents.state import EmailProcessingState

logger = logging.getLogger(__name__)


def _build_full_text(state: EmailProcessingState) -> str:
    parts = [state["subject"], state["body_text"]] + state["attachment_texts"]
    return "\n\n".join(filter(None, parts))


def _regex_invoice_numbers(text: str) -> List[str]:
    candidates: set[str] = set()
    patterns = [
        r"(?:invoice\s*(?:no\.?|number|#|num)[:\s#-]*)([\w\-/]+)",
        r"(?:inv[\.#\-/]*)([\w\-/]{4,20})",
        r"(?:bill\s*(?:no\.?|number|#)[:\s#-]*)([\w\-/]+)",
        r"(?:reference\s*(?:no\.?|number|#|:)\s*)([\w\-/]{4,20})",
        r"\b(INV[-/]?\d{3,10})\b",
        r"(?:invoice|inv)\D{0,10}(\d{4,8})\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            val = m.group(1).strip().upper()
            if len(val) >= 3:
                candidates.add(val)
    return list(candidates)


@observe(name="node_extract_text")
async def node_extract_text(state: EmailProcessingState) -> EmailProcessingState:
    all_text = _build_full_text(state)
    logger.info(f"[email_id={state['email_id']}] Extracted text ({len(all_text)} chars)")
    return {**state, "all_text": all_text}
