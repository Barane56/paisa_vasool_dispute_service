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


def _strip_quoted_reply(text: str) -> str:
    """
    Remove quoted reply content from email body text.

    Strips:
      - Lines starting with > (Gmail / standard reply quoting)
      - "On <date> ... wrote:" attribution lines and everything after them
      - "---- Original Message ----" blocks

    Keeps only the new content the customer wrote in this reply.
    """
    # Split into lines for line-level processing
    lines = text.split('\n')
    cleaned: list[str] = []

    # Regex that matches "On <anything> wrote:" attribution lines
    # Handles multi-line attributions too (On Mon,\nFoo <bar> wrote:)
    attribution_re = re.compile(
        r'^On\s.{5,200}wrote\s*:\s*$',
        re.IGNORECASE | re.DOTALL,
    )
    # Also matches single-line "On Mon, 23 Mar 2026 at 10:55 AM <x> wrote:"
    attribution_inline_re = re.compile(
        r'^On\s.+wrote\s*:',
        re.IGNORECASE,
    )

    in_quote = False
    for line in lines:
        stripped = line.strip()

        # Standard > quoting — skip
        if stripped.startswith('>'):
            in_quote = True
            continue

        # Once we hit an attribution line, everything below is quoted
        if attribution_inline_re.match(stripped):
            in_quote = True
            continue

        # Standard reply separators
        if re.match(r'^-{4,}\s*(original message|forwarded message)\s*-{4,}',
                    stripped, re.IGNORECASE):
            in_quote = True
            continue

        if in_quote:
            # A non-empty, non-quoted line after a quote block means the
            # customer may have written something after quoting.
            # We conservatively stop stripping — but only if the line looks
            # like real content (not another > line caught late).
            if stripped and not stripped.startswith('>'):
                in_quote = False
                cleaned.append(line)
        else:
            cleaned.append(line)

    result = '\n'.join(cleaned)
    # Collapse excessive blank lines left after stripping
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


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
        # Matches full INV-YYYY-NNN style numbers (e.g. INV-2024-004, INV/2024/015).
        # The old pattern \b(INV[-/]?\d{3,10})\b stopped at the first digit group,
        # turning INV-2024-004 into INV-2024 and missing the DB lookup entirely.
        r"\b(INV[-/]?[\d][\d\-/]{2,15})\b",
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
    # Strip quoted reply content from body_text before building full text
    clean_body = _strip_quoted_reply(state["body_text"])
    if clean_body != state["body_text"]:
        logger.info(
            f"[email_id={state['email_id']}] Stripped quoted reply content "
            f"({len(state['body_text'])} → {len(clean_body)} chars)"
        )
    # Update body_text in state so downstream nodes see the clean version
    state = {**state, "body_text": clean_body}
    all_text = _build_full_text(state)
    logger.info(f"[email_id={state['email_id']}] Extracted text ({len(all_text)} chars)")
    return {**state, "all_text": all_text}