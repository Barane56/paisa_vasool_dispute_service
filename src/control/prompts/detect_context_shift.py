"""
src/control/prompts/detect_context_shift.py
============================================
Prompt builder for context-shift detection.

Called only when an incoming email arrives on an EXISTING dispute thread.
The LLM decides whether the email introduces a genuinely new issue that
warrants forking one or more new disputes out of the ongoing conversation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from poml import poml as render_poml

PROMPT_NAME    = "detect_context_shift"
PROMPT_VERSION = "2.0"
_TEMPLATE      = str(Path(__file__).parent / "templates" / "detect_context_shift.poml")


def build_detect_context_shift_prompt(
    *,
    subject: str,
    sender_email: str,
    body_text: str,
    existing_dispute_id: int,
    existing_invoice_number: Optional[str],
    existing_dispute_type: str,
    existing_description: str,
    existing_status: str,
    recent_episodes: List[Dict],
    new_invoice_number: Optional[str] = None,
) -> str:
    context = {
        "subject":                 subject,
        "sender_email":            sender_email,
        "body_text":               body_text[:1200],
        "existing_dispute_id":     str(existing_dispute_id),
        "existing_invoice_number": existing_invoice_number or "Not recorded",
        "existing_dispute_type":   existing_dispute_type,
        "existing_description":    existing_description,
        "existing_status":         existing_status,
        "recent_episodes":         json.dumps(recent_episodes[:5], indent=2),
        "new_invoice_number":      new_invoice_number or "",
    }

    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
