"""
src/control/prompts/generate_response.py
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Optional
from poml import poml as render_poml

PROMPT_NAME    = "generate_ar_response"
PROMPT_VERSION = "2.0"
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
    dispute_token: Optional[str] = None,
    inline_issues_summary: str = "",
    inline_issues: Optional[List[Dict]] = None,
) -> str:
    # Build a stripped-down version of inline_issues for the prompt —
    # only description, invoice_number, disputed_amount, and token placeholder.
    # Internal fields like dispute_type_name are intentionally excluded so the
    # LLM cannot leak them into the customer-facing email.
    inline_issues_ctx = "None"
    if inline_issues:
        safe_issues = []
        for idx, iss in enumerate(inline_issues, 2):
            safe_issues.append({
                "description":     iss.get("description", ""),
                "invoice_number":  iss.get("invoice_number"),
                "disputed_amount": iss.get("disputed_amount"),
                "token_placeholder": f"{{DISPUTE_TOKEN_{idx}}}",
            })
        inline_issues_ctx = json.dumps(safe_issues, indent=2)

    context = {
        "subject":               subject,
        "sender_email":          sender_email,
        "body_text":             body_text,
        "invoice_ctx":           json.dumps(invoice_details or {}, indent=2),
        "payment_ctx":           json.dumps(all_payment_details, indent=2) if all_payment_details else "No payment records on file",
        "memory_ctx":            memory_summary or "No previous conversation on record",
        "recent_eps":            json.dumps(recent_episodes[:4], indent=2),
        "pending_qs":            json.dumps(pending_questions, indent=2) if pending_questions else "None",
        "classification":        classification,
        "dispute_type_name":     dispute_type_name,
        "priority":              priority,
        "description":           description,
        "dispute_token":         dispute_token or "{DISPUTE_TOKEN}",
        "inline_issues_summary": inline_issues_summary,
        "inline_issues_ctx":     inline_issues_ctx,
    }

    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))