"""
src/control/prompts/generate_response.py
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Optional
from poml import poml as render_poml

PROMPT_NAME    = "generate_ar_response"
PROMPT_VERSION = "3.1"
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
    is_focused_issue: bool = False,
    focus_invoice_number: Optional[str] = None,
    attachment_metadata: Optional[List[Dict]] = None,  # NEW: [{file_name, file_type, extracted_text}]
) -> str:
    inline_issues_ctx = "None"
    if inline_issues and not is_focused_issue:
        safe_issues = []
        for idx, iss in enumerate(inline_issues, 2):
            safe_issues.append({
                "description":       iss.get("description", ""),
                "invoice_number":    iss.get("invoice_number"),
                "disputed_amount":   iss.get("disputed_amount"),
                "token_placeholder": f"{{DISPUTE_TOKEN_{idx}}}",
            })
        inline_issues_ctx = json.dumps(safe_issues, indent=2)

    # Build attachment context block
    att_ctx = "No attachments"
    if attachment_metadata:
        parts = []
        for meta in attachment_metadata:
            fname     = meta.get("file_name", "attachment")
            ftype     = meta.get("file_type", "unknown")
            extracted = meta.get("extracted_text", "")
            if extracted:
                parts.append(f"[{fname} ({ftype.upper()})]\n{extracted[:1500]}")
        if parts:
            att_ctx = "\n\n---\n\n".join(parts)[:5000]

    context = {
        "subject":               subject,
        "sender_email":          sender_email,
        "body_text":             body_text,
        "invoice_ctx":           json.dumps(invoice_details or {}, indent=2),
        "payment_ctx":           (json.dumps(all_payment_details, indent=2)
                                  if all_payment_details else "No payment records on file"),
        "memory_ctx":            memory_summary or "No previous conversation on record",
        "recent_eps":            json.dumps(recent_episodes[:4], indent=2),
        "pending_qs":            (json.dumps(pending_questions, indent=2)
                                  if pending_questions else "None"),
        "classification":        classification,
        "dispute_type_name":     dispute_type_name,
        "priority":              priority,
        "description":           description,
        "dispute_token":         dispute_token or "{DISPUTE_TOKEN}",
        "inline_issues_summary": inline_issues_summary,
        "inline_issues_ctx":     inline_issues_ctx,
        # Focused-issue fields
        "is_focused_issue":      is_focused_issue,
        "focus_invoice_number":  focus_invoice_number or "not specified",
        "attachment_ctx":        att_ctx,
    }

    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))