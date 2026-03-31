"""
src/control/prompts/generate_response.py
"""

from __future__ import annotations

import json
from pathlib import Path

from poml import poml as render_poml

PROMPT_NAME = "generate_ar_response"
PROMPT_VERSION = "3.1"
_TEMPLATE = str(Path(__file__).parent / "templates" / "generate_response.poml")


def build_generate_response_prompt(
    subject: str,
    sender_email: str,
    body_text: str,
    invoice_details: dict | None,
    all_payment_details: list[dict],
    memory_summary: str | None,
    recent_episodes: list[dict],
    pending_questions: list[dict],
    classification: str,
    dispute_type_name: str,
    priority: str,
    description: str,
    dispute_token: str | None = None,
    inline_issues_summary: str = "",
    inline_issues: list[dict] | None = None,
    is_focused_issue: bool = False,
    focus_invoice_number: str | None = None,
    attachment_metadata: list[dict] | None = None,
    ar_document_chain: list[dict] | None = None,
    related_dispute_token: str
    | None = None,  # new case on same invoice — mention to customer
) -> str:
    inline_issues_ctx = "None"
    if inline_issues and not is_focused_issue:
        safe_issues = []
        for idx, iss in enumerate(inline_issues, 2):
            safe_issues.append(
                {
                    "description": iss.get("description", ""),
                    "invoice_number": iss.get("invoice_number"),
                    "disputed_amount": iss.get("disputed_amount"),
                    "token_placeholder": f"{{DISPUTE_TOKEN_{idx}}}",
                }
            )
        inline_issues_ctx = json.dumps(safe_issues, indent=2)

    # Build attachment context block
    att_ctx = "No attachments"
    if attachment_metadata:
        parts = []
        for meta in attachment_metadata:
            fname = meta.get("file_name", "attachment")
            ftype = meta.get("file_type", "unknown")
            extracted = meta.get("extracted_text", "")
            if extracted:
                parts.append(f"[{fname} ({ftype.upper()})]\n{extracted[:1500]}")
        if parts:
            att_ctx = "\n\n---\n\n".join(parts)[:5000]

    # Build AR document chain context
    doc_chain_ctx = "No AR documents on file for this invoice."
    if ar_document_chain:
        parts = []
        for item in ar_document_chain:
            doc_type = item.get("doc_type", "UNKNOWN")
            doc_date = item.get("doc_date") or "date unknown"
            shared = item.get("shared_keys", [])
            shared_str = ", ".join(
                f"{k['key_type']}={k['key_value_raw']}" for k in shared
            )
            parts.append(f"  - {doc_type} (dated {doc_date}) linked via: {shared_str}")
        doc_chain_ctx = "Uploaded AR documents linked to this invoice:\n" + "\n".join(
            parts
        )

    # Related dispute context — set when L2 Gate B detected a different issue
    # on the same invoice. The LLM uses this to inform the customer.
    related_dispute_ctx = ""
    if related_dispute_token:
        related_dispute_ctx = (
            f"NOTE: The customer already has an open case {related_dispute_token} "
            f"on this same invoice. That case covers a different issue. "
            f"This is a NEW case. If the customer thinks this is the same issue, "
            f"ask them to quote {related_dispute_token} in their next reply."
        )

    context = {
        "subject": subject,
        "sender_email": sender_email,
        "body_text": body_text,
        "invoice_ctx": json.dumps(invoice_details or {}, indent=2),
        "payment_ctx": (
            json.dumps(all_payment_details, indent=2)
            if all_payment_details
            else "No payment records on file"
        ),
        "memory_ctx": memory_summary or "No previous conversation on record",
        "recent_eps": json.dumps(recent_episodes[:4], indent=2),
        "pending_qs": (
            json.dumps(pending_questions, indent=2) if pending_questions else "None"
        ),
        "classification": classification,
        "dispute_type_name": dispute_type_name,
        "priority": priority,
        "description": description,
        "dispute_token": dispute_token or "{DISPUTE_TOKEN}",
        "inline_issues_summary": inline_issues_summary,
        "inline_issues_ctx": inline_issues_ctx,
        # Focused-issue fields
        "is_focused_issue": is_focused_issue,
        "focus_invoice_number": focus_invoice_number or "not specified",
        "attachment_ctx": att_ctx,
        "doc_chain_ctx": doc_chain_ctx,
        "related_dispute_ctx": related_dispute_ctx,
    }

    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
