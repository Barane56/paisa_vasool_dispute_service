"""
src/control/prompts/generate_response.py
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Optional
from poml import poml as render_poml

PROMPT_NAME    = "generate_ar_response"
PROMPT_VERSION = "1.0"
_TEMPLATE = str(Path(__file__).parent / "templates" / "generate_response.poml")


def build_generate_response_prompt(
    subject: str,
    sender_email: str,
    body_text: str,
    invoice_details: Optional[Dict],
    all_payment_details: List[Dict],
    memory_summary: Optional[str],
    recent_episodes: List[Dict],
    pending_questions: List[Dict],
    classification: str,
    dispute_type_name: str,
    priority: str,
    description: str,
) -> str:
    context = {
        "subject":           subject,
        "sender_email":      sender_email,
        "body_text":         body_text[:800],
        "invoice_ctx":       json.dumps(invoice_details or {}, indent=2),
        "payment_ctx":       json.dumps(all_payment_details, indent=2) if all_payment_details else "No payment records on file",
        "memory_ctx":        memory_summary or "No previous conversation on record",
        "recent_eps":        json.dumps(recent_episodes[:4], indent=2),
        "pending_qs":        json.dumps(pending_questions, indent=2) if pending_questions else "None",
        "classification":    classification,
        "dispute_type_name": dispute_type_name,
        "priority":          priority,
        "description":       description,
    }

    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
