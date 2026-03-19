"""
src/control/agents/nodes/generate_response.py
=============================================
Generates one independent customer-facing email response PER ISSUE.

For single-issue emails behaviour is unchanged — one LLM call, result stored
in the standard ai_response / ai_summary fields.

For multi-issue emails:
  - One LLM call per issue (primary + each inline issue).
  - Each call gets its own invoice + payment context fetched from DB.
  - Each call decides independently: can_auto_respond true/false.
  - Results stored in state["per_issue_responses"] as a list.
  - state["ai_response"] is set to the PRIMARY issue's response for
    backwards-compat with any downstream code that reads it directly.
  - persist_results reads per_issue_responses to write one DisputeAIAnalysis
    and one email per dispute, with its own {DISPUTE_TOKEN} replaced.

Scenario C (no invoice/token match) still emits a single "please send invoice
details" email for the whole thread — splitting is meaningless without context.
"""

from __future__ import annotations
import json
import logging
import re
from typing import Dict, List, Optional

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState
from src.control.prompts import build_generate_response_prompt
from src.control.prompts.generate_response import (
    PROMPT_NAME as RESPONSE_PROMPT_NAME,
    PROMPT_VERSION as RESPONSE_PROMPT_VERSION,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitise_dispute_token(ai_response: str, expected_token: str) -> str:
    """
    Replace any LLM-hallucinated dispute reference IDs with the correct placeholder.

    Catches all known hallucination patterns:
      DISP-00001        (our real format)
      DISPUTE-2025-001  (LLM makes up year-based references)
      DISPUTE-001       (LLM shortens it)
      REF-2025-001      (LLM uses REF prefix)
      DISP-2025-001     (LLM mixes formats)
    """
    if expected_token != "{DISPUTE_TOKEN}":
        return ai_response

    # Broad pattern: any word starting with DISP or DISPUTE or REF followed by
    # digits and hyphens — covers all known hallucination styles
    sanitised = re.sub(
        r'\b(?:DISPUTE|DISP|REF)[-_](?:\d{4}[-_])?\d{1,6}\b',
        "{DISPUTE_TOKEN}",
        ai_response,
        flags=re.IGNORECASE,
    )
    if sanitised != ai_response:
        logger.warning(
            f"LLM hallucinated dispute ID — restored placeholder. "
            f"Snippet: {ai_response[:120]!r}"
        )
    return sanitised


async def _fetch_issue_context(
    db_session,
    invoice_number: Optional[str],
    fallback_invoice_details: Optional[Dict],
    fallback_payment_details: List[Dict],
) -> tuple:
    """
    Fetch invoice + payment context for a single issue by invoice number.
    Falls back to the primary-level context when no distinct number exists
    or the DB lookup fails.
    """
    if not db_session or not invoice_number:
        return fallback_invoice_details, fallback_payment_details

    try:
        from src.data.repositories.repositories import InvoiceRepository, PaymentRepository

        inv_repo = InvoiceRepository(db_session)
        invoice  = await inv_repo.get_by_invoice_number(invoice_number)
        if not invoice:
            results = await inv_repo.search_by_number_fuzzy(invoice_number)
            invoice = results[0] if results else None

        if not invoice:
            return fallback_invoice_details, fallback_payment_details

        inv_details = dict(invoice.invoice_details or {})
        if not inv_details.get("line_items"):
            inv_details["line_items"] = None

        pay_repo     = PaymentRepository(db_session)
        payments     = await pay_repo.get_all_by_invoice_number(invoice.invoice_number)
        pay_details  = [
            {"payment_detail_id": p.payment_detail_id,
             "invoice_number":    p.invoice_number,
             **p.payment_details}
            for p in payments if p.payment_details
        ]
        return inv_details, pay_details

    except Exception as err:
        logger.warning(f"_fetch_issue_context failed for '{invoice_number}': {err}")
        return fallback_invoice_details, fallback_payment_details


async def _call_llm_for_issue(
    *,
    llm_client,
    issue_index: int,
    subject: str,
    sender_email: str,
    body_text: str,
    invoice_details: Optional[Dict],
    payment_details: List[Dict],
    memory_summary: Optional[str],
    recent_episodes: List[Dict],
    pending_questions: List[Dict],
    classification: str,
    dispute_type_name: str,
    priority: str,
    description: str,
    dispute_token: str,
    email_id: int,
    is_focused_issue: bool = False,
    focus_invoice_number: Optional[str] = None,
    attachment_metadata: Optional[List[Dict]] = None,
) -> Dict:
    """
    Run one full generate_response.poml LLM call for a single issue.
    Returns a normalised dict ready to append to per_issue_responses.
    """
    prompt = build_generate_response_prompt(
        subject=subject,
        sender_email=sender_email,
        body_text=body_text,
        invoice_details=invoice_details,
        all_payment_details=payment_details,
        memory_summary=memory_summary,
        recent_episodes=recent_episodes,
        pending_questions=pending_questions,
        classification=classification,
        dispute_type_name=dispute_type_name,
        priority=priority,
        description=description,
        dispute_token=dispute_token,
        inline_issues_summary="",  # each call is for ONE issue only
        inline_issues=None,
        is_focused_issue=is_focused_issue,
        focus_invoice_number=focus_invoice_number,
        attachment_metadata=attachment_metadata,
    )

    try:
        raw  = await llm_client.chat(prompt)
        data = json.loads(raw)

        ai_resp = data.get("ai_response") or ""
        if dispute_token == "{DISPUTE_TOKEN}":
            ai_resp = _sanitise_dispute_token(ai_resp, dispute_token)

        can_auto = bool(data.get("can_auto_respond"))
        logger.info(
            f"[email_id={email_id}] issue[{issue_index}] "
            f"auto_respond={can_auto} | {data.get('auto_respond_reason', '')}"
        )
        return {
            "issue_index":      issue_index,
            "classification":   classification,
            "description":      description,
            "ai_response":      ai_resp,
            "can_auto_respond": can_auto,
            "ai_summary":       data.get("ai_summary", description),
            "confidence_score": float(data.get("confidence_score", 0.7)),
            "questions_to_ask": data.get("questions_to_ask", []),
            "dispute_token":    dispute_token,
            "memory_context_used":        bool(data.get("memory_context_used", False)),
            "episodes_referenced":        [
                int(x) for x in (data.get("episodes_referenced") or [])
                if str(x).lstrip("-").isdigit()
            ],
            "_answers_pending_questions": [
                int(x) for x in (data.get("answers_pending_questions") or [])
                if str(x).lstrip("-").isdigit()
            ],
        }

    except Exception as err:
        logger.error(
            f"[email_id={email_id}] issue[{issue_index}] LLM call failed: {err}",
            exc_info=True,
        )
        # Safe fallback — always escalate on error
        return {
            "issue_index":      issue_index,
            "classification":   classification,
            "description":      description,
            "ai_response": (
                f"Subject: RE: {subject}\n\n"
                f"Dear Customer,\n\n"
                f"Thank you for reaching out. We have logged your query and our "
                f"Finance team will follow up within 1-2 business days.\n\n"
                f"Your reference: {dispute_token}\n\n"
                f"Please quote this in all future correspondence.\n\n"
                f"Regards,\nAccounts Receivable Team"
            ),
            "can_auto_respond":           False,
            "ai_summary":                 description,
            "confidence_score":           0.5,
            "questions_to_ask":           [f"[LLM ERROR issue {issue_index}] Manual review required."],
            "dispute_token":              dispute_token,
            "memory_context_used":        False,
            "episodes_referenced":        [],
            "_answers_pending_questions": [],
        }


def _build_needs_invoice_response(subject: str) -> str:
    return (
        f"Subject: RE: {subject}\n\n"
        f"Dear Customer,\n\n"
        f"Thank you for contacting us. We have received your query and our "
        f"Finance team will be happy to assist you.\n\n"
        f"To help us locate the relevant records quickly, could you please reply "
        f"with the following:\n\n"
        f"  - Invoice number (e.g. INV-1234)\n"
        f"  - Approximate invoice date\n"
        f"  - Amount in question (if applicable)\n\n"
        f"Once we receive these details, we will raise a ticket and follow up "
        f"with you promptly.\n\n"
        f"Regards,\n"
        f"Accounts Receivable Team"
    )


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

@observe(name="node_generate_ai_response")
async def node_generate_ai_response(
    state: EmailProcessingState, llm_client=None, db_session=None
) -> EmailProcessingState:

    if not llm_client:
        return {
            **state,
            "ai_summary":              state.get("description", "Email processed."),
            "ai_response":             None,
            "confidence_score":        0.5,
            "auto_response_generated": False,
            "questions_to_ask":        [],
            "memory_context_used":     False,
            "episodes_referenced":     [],
            "per_issue_responses":     [],
        }

    inline_issues = state.get("inline_issues") or []
    has_inline    = len(inline_issues) > 0
    email_id      = state["email_id"]

    # ── Scenario C: no invoice context ───────────────────────────────────────
    if state.get("_needs_invoice_details"):
        fa_notes = [
            "FA: No invoice, token, or past dispute matched. "
            "Awaiting customer reply with invoice details."
        ]
        for i, iss in enumerate(inline_issues, 1):
            fa_notes.append(
                f"FA [Issue {i+1}: {iss.get('dispute_type_name')}]: "
                f"{iss.get('description', '')[:200]}"
            )
        langfuse_context.update_current_observation(
            output={"path": "needs_invoice_details", "multi_issue": has_inline}
        )
        return {
            **state,
            "ai_summary":              state.get("description", "Customer query without invoice reference."),
            "ai_response":             _build_needs_invoice_response(state["subject"]),
            "confidence_score":        0.85,
            "auto_response_generated": True,
            "questions_to_ask":        fa_notes,
            "memory_context_used":     False,
            "episodes_referenced":     [],
            "per_issue_responses":     [],
        }

    existing_dispute_id = state.get("existing_dispute_id")
    dispute_token       = (
        f"DISP-{existing_dispute_id:05d}" if existing_dispute_id else "{DISPUTE_TOKEN}"
    )

    # ── Single-issue path ─────────────────────────────────────────────────────
    if not has_inline:
        result = await _call_llm_for_issue(
            llm_client=llm_client,
            issue_index=0,
            subject=state["subject"],
            sender_email=state["sender_email"],
            body_text=state["body_text"],
            invoice_details=state.get("invoice_details"),
            payment_details=state.get("all_payment_details") or [],
            memory_summary=state.get("memory_summary"),
            recent_episodes=state.get("recent_episodes", []),
            pending_questions=state.get("pending_questions", []),
            classification=state.get("classification", ""),
            dispute_type_name=state.get("dispute_type_name", ""),
            priority=state.get("priority", ""),
            description=state.get("description", ""),
            dispute_token=dispute_token,
            email_id=email_id,
            is_focused_issue=False,
            attachment_metadata=state.get("attachment_metadata"),
        )
        langfuse_context.update_current_observation(
            input={"prompt_name": RESPONSE_PROMPT_NAME, "prompt_version": RESPONSE_PROMPT_VERSION},
            output={"path": "single_issue", "auto_respond": result["can_auto_respond"]},
        )
        return {
            **state,
            "ai_summary":                 result["ai_summary"],
            "ai_response":                result["ai_response"],
            "confidence_score":           result["confidence_score"],
            "auto_response_generated":    result["can_auto_respond"],
            "questions_to_ask":           result["questions_to_ask"],
            "memory_context_used":        result["memory_context_used"],
            "episodes_referenced":        result["episodes_referenced"],
            "_answers_pending_questions": result["_answers_pending_questions"],
            "per_issue_responses":        [],
        }

    # ── Multi-issue path: one LLM call per issue ──────────────────────────────
    #
    # Token assignment:
    #   Primary   → {DISPUTE_TOKEN}    resolved at persist step 2 (primary row created)
    #   Inline[0] → {DISPUTE_TOKEN_2}  resolved after inline rows created in step 9a
    #   Inline[1] → {DISPUTE_TOKEN_3}  ...
    #
    all_issues_spec = [
        {
            "issue_index":      0,
            "classification":   state.get("classification", "CLARIFICATION"),
            "dispute_type_name": state.get("dispute_type_name", ""),
            "priority":         state.get("priority", "MEDIUM"),
            "description":      state.get("description", ""),
            "invoice_number":   state.get("invoice_number"),
            "dispute_token":    "{DISPUTE_TOKEN}",
        }
    ]
    # print(all_issues_spec)
    for seq, iss in enumerate(inline_issues, 2):
        all_issues_spec.append({
            "issue_index":      seq - 1,
            "classification":   iss.get("classification", "CLARIFICATION"),
            "dispute_type_name": iss.get("dispute_type_name", ""),
            "priority":         iss.get("priority", "MEDIUM"),
            "description":      iss.get("description", ""),
            "invoice_number":   iss.get("invoice_number"),
            "dispute_token":    f"{{DISPUTE_TOKEN_{seq}}}",
        })
    # print(all_issues_spec)
    per_issue_responses: List[Dict] = []
    all_fa_questions:    List[str]  = []

    for spec in all_issues_spec:
        inv_ctx, pay_ctx = await _fetch_issue_context(
            db_session=db_session,
            invoice_number=spec["invoice_number"],
            fallback_invoice_details=state.get("invoice_details"),
            fallback_payment_details=state.get("all_payment_details") or [],
        )

        # print(inv_ctx, pay_ctx)

        result = await _call_llm_for_issue(
            llm_client=llm_client,
            issue_index=spec["issue_index"],
            subject=state["subject"],
            sender_email=state["sender_email"],
            body_text=state["body_text"],
            invoice_details=inv_ctx,
            payment_details=pay_ctx,
            memory_summary=state.get("memory_summary"),
            recent_episodes=state.get("recent_episodes", []),
            pending_questions=state.get("pending_questions", []),
            classification=spec["classification"],
            dispute_type_name=spec["dispute_type_name"],
            priority=spec["priority"],
            description=spec["description"],
            dispute_token=spec["dispute_token"],
            email_id=email_id,
            is_focused_issue=True,
            focus_invoice_number=spec.get("invoice_number"),
            attachment_metadata=state.get("attachment_metadata"),
        )
        per_issue_responses.append(result)
        all_fa_questions.extend(result.get("questions_to_ask") or [])

    primary = per_issue_responses[0]

    langfuse_context.update_current_observation(
        input={"prompt_name": RESPONSE_PROMPT_NAME, "prompt_version": RESPONSE_PROMPT_VERSION},
        output={
            "path":          "multi_issue",
            "issue_count":   len(per_issue_responses),
            "auto_responds": sum(1 for r in per_issue_responses if r["can_auto_respond"]),
            "escalations":   sum(1 for r in per_issue_responses if not r["can_auto_respond"]),
        },
    )

    return {
        **state,
        # Primary fields for backwards compat + single-issue consumers
        "ai_summary":                 primary["ai_summary"],
        "ai_response":                primary["ai_response"],
        "confidence_score":           primary["confidence_score"],
        "auto_response_generated":    primary["can_auto_respond"],
        "questions_to_ask":           all_fa_questions,
        "memory_context_used":        primary.get("memory_context_used", False),
        "episodes_referenced":        primary.get("episodes_referenced", []),
        "_answers_pending_questions": primary.get("_answers_pending_questions", []),
        # Full per-issue list — consumed by persist_results
        "per_issue_responses":        per_issue_responses,
    }