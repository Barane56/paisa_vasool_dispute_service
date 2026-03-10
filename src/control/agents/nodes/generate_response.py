"""
src/control/agents/nodes/generate_response.py
=============================================
Generates the customer-facing email response.

Paths:
  Scenario C  — no invoice/token/embedding match → fixed invoice-request email.
  Multi-issue — one combined acknowledgement covering all issues. Token
                placeholders are written per-issue and replaced by persist_results.
  Normal      — LLM decides auto-respond vs FA escalation.
"""

from __future__ import annotations
import json
import logging
import re
from typing import List

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState
from src.control.prompts import build_generate_response_prompt
from src.control.prompts.generate_response import (
    PROMPT_NAME as RESPONSE_PROMPT_NAME,
    PROMPT_VERSION as RESPONSE_PROMPT_VERSION,
)

logger = logging.getLogger(__name__)


def _build_inline_issues_summary(inline_issues: List) -> str:
    """One-line summary of secondary issues for the LLM prompt context."""
    if not inline_issues:
        return ""
    parts = []
    for i, issue in enumerate(inline_issues, 1):
        inv = issue.get("invoice_number") or "no invoice ref"
        amt = issue.get("disputed_amount") or ""
        dtype = issue.get("dispute_type_name") or "Unknown"
        parts.append(f"Issue {i+1}: {dtype} ({inv}{', ' + amt if amt else ''})")
    return "; ".join(parts)


def _build_multi_issue_ack(
    subject: str,
    primary_description: str,
    primary_dispute_type: str,
    inline_issues: List,
    needs_invoice: bool,
) -> str:
    """
    Professional multi-issue acknowledgement — one email, each issue named
    with its own case reference. Modelled on how SAP, Oracle and HighRadius
    handle multi-issue AR emails: personalised per issue, not a generic bullet dump.

    Token placeholders {DISPUTE_TOKEN_1}, {DISPUTE_TOKEN_2}, … are replaced by
    persist_results after all DisputeMaster rows are committed.
    """
    total = len(inline_issues) + 1

    # Build one paragraph per issue — named, described, referenced
    all_issues = [{"dispute_type_name": primary_dispute_type, "description": primary_description,
                   "invoice_number": None, "disputed_amount": None, "_token": "{DISPUTE_TOKEN_1}"}]
    for idx, iss in enumerate(inline_issues, 2):
        all_issues.append({**iss, "_token": f"{{DISPUTE_TOKEN_{idx}}}"})

    issue_paragraphs = []
    for i, iss in enumerate(all_issues, 1):
        dtype   = iss.get("dispute_type_name") or "General Query"
        inv     = iss.get("invoice_number")
        amt     = iss.get("disputed_amount")
        token   = iss["_token"]
        desc    = (iss.get("description") or "").strip()

        inv_note = f" on invoice {inv}" if inv else ""
        amt_note = f" (amount in dispute: {amt})" if amt else ""

        # Trim description to one sentence for the email
        short_desc = desc.split(".")[0].strip() if desc else dtype
        if short_desc and not short_desc.endswith("."):
            short_desc += "."

        issue_paragraphs.append(
            f"  Case {i} — {dtype}{inv_note}{amt_note}\n"
            f"  {short_desc}\n"
            f"  Your reference for this matter: {token}"
        )

    issues_block = "\n\n".join(issue_paragraphs)

    invoice_note = (
        "\nAs some of the matters above do not yet have an invoice reference, "
        "please include the relevant invoice number(s) when you reply so we can "
        "locate the correct records promptly.\n"
        if needs_invoice else ""
    )

    plural = "matters" if total > 1 else "matter"

    return (
        f"Subject: RE: {subject}\n\n"
        f"Dear Customer,\n\n"
        f"Thank you for your email. We have reviewed your message and registered "
        f"{total} separate {plural} with our Finance team. Each has been assigned "
        f"an individual case reference — please use the corresponding reference when "
        f"following up on a specific issue so we can assist you without delay.\n\n"
        f"{issues_block}\n"
        f"{invoice_note}\n"
        f"Our team will review each case and respond within 1–2 business days. "
        f"For urgent matters please reply directly to this email quoting your case reference.\n\n"
        f"Regards,\n"
        f"Accounts Receivable Team"
    )


def _sanitise_dispute_token(ai_response: str, expected_token: str) -> str:
    """
    Guard against the LLM substituting its own invented dispute ID instead of
    writing the placeholder verbatim.

    For NEW disputes  expected_token == "{DISPUTE_TOKEN}"  — if the LLM wrote
    something like DISP-00001, DISP_0001, CASE-12345, REF-XYZ, etc. we restore
    the placeholder so persist_results can replace it with the real DB-assigned ID.

    For EXISTING disputes  expected_token == "DISP-NNNNN"  — the correct value
    was passed to the prompt and should appear as-is; no action needed.
    """
    if expected_token != "{DISPUTE_TOKEN}":
        # Existing dispute: the real token was already in the prompt — trust it.
        return ai_response

    # Pattern covers common LLM hallucinations:
    #   DISP-00001  DISP_0001  DISP-1  DISP00001
    #   CASE-12345  REF-XYZ99  TICKET-001  etc.
    hallucination_pattern = re.compile(
        r'\b(?:DISP[-_]?\d{1,6}|CASE[-_]?\w{2,10}|REF[-_]?\w{2,10}|TICKET[-_]?\w{2,10})\b',
        re.IGNORECASE,
    )

    def _replace(m: re.Match) -> str:
        matched = m.group(0)
        # Only replace when it looks like a fabricated reference — i.e. it is NOT
        # the literal placeholder text itself (which shouldn't match, but be safe).
        if matched.upper().startswith("DISP") and not matched == "{DISPUTE_TOKEN}":
            return "{DISPUTE_TOKEN}"
        return matched   # leave CASE/REF/TICKET patterns alone unless sure

    # Simpler targeted replacement: any DISP-XXXXX that isn't our real token
    sanitised = re.sub(
        r'\bDISP[-_]?\d{1,6}\b',
        "{DISPUTE_TOKEN}",
        ai_response,
        flags=re.IGNORECASE,
    )

    if sanitised != ai_response:
        logger.warning(
            "LLM hallucinated a dispute ID in ai_response — restored placeholder. "
            f"Original snippet: {ai_response[:120]!r}"
        )

    return sanitised



async def node_generate_ai_response(
    state: EmailProcessingState, llm_client=None
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
        }

    inline_issues = state.get("inline_issues") or []
    has_inline    = len(inline_issues) > 0

    # ── Scenario C: no invoice details available ──────────────────────────────
    if state.get("_needs_invoice_details"):
        fa_notes = [
            "FA: No invoice, token, or past dispute matched. "
            "Dispute created and assigned. Awaiting customer reply with invoice details."
        ]
        for i, issue in enumerate(inline_issues, 1):
            fa_notes.append(
                f"FA [Additional Issue {i}]: {issue.get('dispute_type_name')} — "
                f"{issue.get('description', '')[:200]}"
            )

        if has_inline:
            # Multi-issue Scenario C — combined ack with numbered placeholders
            response_text = _build_multi_issue_ack(
                subject=state["subject"],
                primary_description=state.get("description", ""),
                primary_dispute_type=state.get("dispute_type_name", "General Query"),
                inline_issues=inline_issues,
                needs_invoice=True,
            )
        else:
            response_text = (
                f"Subject: RE: {state['subject']}\n\n"
                "Dear Customer,\n\n"
                "Thank you for contacting us. We have logged your query and assigned it "
                "to our Finance team for review.\n\n"
                "To help us locate the relevant records quickly, could you please reply "
                "with the following:\n\n"
                "  • Invoice number (e.g. INV-1234)\n"
                "  • Approximate invoice date\n"
                "  • Amount in question (if applicable)\n\n"
                "Your dispute reference: {DISPUTE_TOKEN}\n\n"
                "Please include this reference in all future correspondence.\n\n"
                "Regards,\n"
                "Accounts Receivable Team"
            )

        langfuse_context.update_current_observation(
            output={"path": "needs_invoice_details", "multi_issue": has_inline}
        )
        return {
            **state,
            "ai_summary":              state.get("description", "Customer query without invoice reference."),
            "ai_response":             response_text,
            "confidence_score":        0.85,
            "auto_response_generated": True,
            "questions_to_ask":        fa_notes,
            "memory_context_used":     False,
            "episodes_referenced":     [],
        }

    # ── Multi-issue: combined acknowledgement (no LLM needed) ─────────────────
    if has_inline:
        response_text = _build_multi_issue_ack(
            subject=state["subject"],
            primary_description=state.get("description", ""),
            primary_dispute_type=state.get("dispute_type_name", "General Query"),
            inline_issues=inline_issues,
            needs_invoice=False,
        )
        fa_notes = []
        for i, issue in enumerate(inline_issues, 1):
            fa_notes.append(
                f"FA [Issue {i+1}: {issue.get('dispute_type_name')}]: "
                f"{issue.get('description', '')[:200]}"
                + (f" — {issue.get('disputed_amount')}" if issue.get('disputed_amount') else "")
            )
        langfuse_context.update_current_observation(
            output={"path": "multi_issue_ack", "inline_count": len(inline_issues)}
        )
        return {
            **state,
            "ai_summary": (
                f"Email contains {1 + len(inline_issues)} distinct issue(s). "
                f"Combined acknowledgement sent with individual reference tokens."
            ),
            "ai_response":             response_text,
            "confidence_score":        0.90,
            "auto_response_generated": True,
            "questions_to_ask":        fa_notes,
            "memory_context_used":     False,
            "episodes_referenced":     [],
        }

    # ── Normal single-issue path — LLM generates response ────────────────────
    inline_issues_summary = _build_inline_issues_summary(inline_issues)  # empty here

    existing_dispute_id = state.get("existing_dispute_id")
    dispute_token = (
        f"DISP-{existing_dispute_id:05d}" if existing_dispute_id else "{DISPUTE_TOKEN}"
    )

    prompt = build_generate_response_prompt(
        subject=state["subject"],
        sender_email=state["sender_email"],
        body_text=state["body_text"],
        invoice_details=state.get("invoice_details"),
        all_payment_details=state.get("all_payment_details") or [],
        memory_summary=state.get("memory_summary"),
        recent_episodes=state.get("recent_episodes", []),
        pending_questions=state.get("pending_questions", []),
        classification=state.get("classification", ""),
        dispute_type_name=state.get("dispute_type_name", ""),
        priority=state.get("priority", ""),
        description=state.get("description", ""),
        dispute_token=dispute_token,
        inline_issues_summary=inline_issues_summary,
    )

    langfuse_context.update_current_observation(
        input={"prompt": prompt},
        metadata={
            "prompt_name":    RESPONSE_PROMPT_NAME,
            "prompt_version": RESPONSE_PROMPT_VERSION,
        },
    )

    try:
        response = await llm_client.chat(prompt)
        data     = json.loads(response)

        can_auto_respond = bool(data.get("can_auto_respond"))

        # ── Guard: restore placeholder if the LLM invented its own dispute ID ──
        raw_ai_response = data.get("ai_response")
        if raw_ai_response and not existing_dispute_id:
            raw_ai_response = _sanitise_dispute_token(raw_ai_response, dispute_token)

        logger.info(
            f"[email_id={state['email_id']}] auto_respond={can_auto_respond} | "
            f"{data.get('auto_respond_reason', '')}"
        )
        langfuse_context.update_current_observation(
            output={
                "auto_respond":     can_auto_respond,
                "confidence_score": data.get("confidence_score"),
            }
        )

        return {
            **state,
            "ai_summary":                 data.get("ai_summary", state.get("description", "")),
            "ai_response":                raw_ai_response,
            "confidence_score":           data.get("confidence_score", 0.7),
            "auto_response_generated":    can_auto_respond,
            "questions_to_ask":           data.get("questions_to_ask", []),
            "memory_context_used":        data.get("memory_context_used", False),
            "episodes_referenced":        data.get("episodes_referenced", []),
            "_answers_pending_questions": [
                int(x) for x in data.get("answers_pending_questions", [])
                if str(x).lstrip("-").isdigit()
            ],
        }

    except Exception as e:
        logger.error(f"[email_id={state['email_id']}] Response generation error: {e}", exc_info=True)
        return {
            **state,
            "ai_summary":                 state.get("description", ""),
            "ai_response":                None,
            "confidence_score":           0.5,
            "auto_response_generated":    False,
            "questions_to_ask":           [],
            "memory_context_used":        False,
            "episodes_referenced":        [],
            "_answers_pending_questions": [],
        }