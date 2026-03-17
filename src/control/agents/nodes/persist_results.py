"""
src/control/agents/nodes/persist_results.py
=============================================
Persists ALL artefacts produced by the email processing pipeline.

Dispute creation overview
--------------------------
Every email can produce multiple disputes:

  A) PRIMARY dispute
     Always created (or reused if existing_dispute_id is set).
     Represents the main / first issue in the email.

  B) INLINE disputes  (state["inline_issues"])
     One per additional issue detected by classify_email in the SAME email.
     These are brand-new disputes for the same customer, same email.
     Linked to primary via DisputeRelationship (SAME_CUSTOMER_BATCH).

  C) FORKED disputes  (state["forked_issues"])
     Created when detect_context_shift fires on a follow-up email.
     Linked to the existing dispute via DisputeRelationship (FORKED_FROM etc.).

Full persist sequence per email
---------------------------------
  1.  Resolve / create primary dispute type
  2.  Create or reuse primary dispute  (+token)
  3.  Write AI analysis record
  3a. Write supporting-doc references
  4.  Write customer email memory episode
  5.  Write AI response episode  (+mark answered questions)
  5a. Embed AI summary for pgvector search
  6.  Write FA open questions
  7.  Route email inbox record
  8.  Auto-assign primary dispute to FA
  9.  Create INLINE disputes  (new issue in same email)
 10.  Create FORKED disputes  (context-shift in follow-up)
 11.  Trigger async summarisation if episode threshold reached
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import update as sa_update

from src.control.agents.state import EmailProcessingState
from src.observability import langfuse_context, observe

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_or_create_dispute_type(db_session, name: str, new_type_data: Optional[Dict]):
    from src.data.models.postgres.models import DisputeType
    from src.data.repositories.repositories import DisputeTypeRepository

    dtype_repo = DisputeTypeRepository(db_session)
    dtype = await dtype_repo.get_by_name(name or "General Clarification")
    if dtype:
        return dtype

    if new_type_data:
        dtype = DisputeType(
            reason_name=new_type_data["reason_name"],
            description=new_type_data.get("description", ""),
            severity_level=new_type_data.get("severity_level", "MEDIUM"),
            is_active=True,
        )
    else:
        dtype = DisputeType(
            reason_name=name or "General Clarification",
            description="Auto-created from email classification",
            severity_level="MEDIUM",
            is_active=True,
        )
    db_session.add(dtype)
    await db_session.flush()
    logger.info(f"Created new dispute type: {dtype.reason_name}")
    return dtype


async def _create_dispute(
    db_session,
    *,
    email_id: int,
    invoice_id: Optional[int],
    payment_detail_id: Optional[int],
    customer_id: str,
    dispute_type_id: int,
    priority: str,
    description: str,
    parent_dispute_id: Optional[int] = None,
    ownership_unverified: bool = False,
):
    from src.data.models.postgres.models import DisputeMaster

    dispute = DisputeMaster(
        email_id=email_id,
        invoice_id=invoice_id,
        payment_detail_id=payment_detail_id,
        customer_id=customer_id,
        dispute_type_id=dispute_type_id,
        status="UNVERIFIED" if ownership_unverified else "OPEN",
        priority=priority,
        description=description,
        parent_dispute_id=parent_dispute_id,
    )
    db_session.add(dispute)
    await db_session.flush()

    dispute.dispute_token = f"DISP-{dispute.dispute_id:05d}"
    await db_session.flush()

    logger.info(
        f"Created dispute_id={dispute.dispute_id} token={dispute.dispute_token} "
        f"parent={parent_dispute_id}"
    )
    return dispute


async def _auto_assign(db_session, dispute_id: int, email_id: int, label: str = ""):
    from src.data.models.postgres.models import DisputeAssignment
    from src.data.repositories.repositories import DisputeAssignmentRepository, UserRoleRepository

    assign_repo       = DisputeAssignmentRepository(db_session)
    active_assignment = await assign_repo.get_active_assignment(dispute_id)
    if active_assignment:
        logger.info(f"[email_id={email_id}] {label} dispute_id={dispute_id} already assigned")
        return

    # get_all_fa() returns List[int] — plain user_id values, not User objects
    user_role_repo = UserRoleRepository(db_session)
    fa_user_ids    = await user_role_repo.get_all_fa()
    if not fa_user_ids:
        logger.warning(f"[email_id={email_id}] {label} no FA users found — dispute_id={dispute_id} unassigned")
        return

    db_session.add(DisputeAssignment(
        dispute_id=dispute_id,
        assigned_to=fa_user_ids[0],   # already an int
        status="ACTIVE",
    ))
    logger.info(
        f"[email_id={email_id}] {label} auto-assigned dispute_id={dispute_id} "
        f"to user_id={fa_user_ids[0]}"
    )


async def _link_disputes(
    db_session,
    *,
    source_dispute_id: int,
    target_dispute_id: int,
    relationship_type: str,
    context_note: Optional[str],
):
    """Create a DisputeRelationship between two disputes (idempotent)."""
    _valid = {"FORKED_FROM", "SAME_CUSTOMER_BATCH", "ESCALATION_OF", "RELATED"}
    if relationship_type not in _valid:
        relationship_type = "RELATED"

    from src.data.repositories.repositories import DisputeRelationshipRepository
    rel_repo = DisputeRelationshipRepository(db_session)

    # Guard against duplicates
    if await rel_repo.relationship_exists(source_dispute_id, target_dispute_id):
        logger.debug(
            f"Relationship {source_dispute_id}↔{target_dispute_id} already exists — skipped"
        )
        return

    await rel_repo.create(
        source_dispute_id=source_dispute_id,
        target_dispute_id=target_dispute_id,
        relationship_type=relationship_type,
        context_note=context_note,
        created_by="SYSTEM",
    )


def _inject_token_into_response(ai_response: str, dispute_token: str) -> str:
    """Replace {DISPUTE_TOKEN} placeholder with the real token."""
    return ai_response.replace("{DISPUTE_TOKEN}", dispute_token)


# ─────────────────────────────────────────────────────────────────────────────
# Inline disputes  (multiple issues detected in the same fresh email)
# ─────────────────────────────────────────────────────────────────────────────

async def _persist_inline_disputes(
    db_session,
    *,
    primary_dispute_id: int,
    inline_issues: List[Dict],
    email_id: int,
    customer_id: str,
    matched_invoice_id: Optional[int],
    # Full email content so each inline dispute gets its own self-contained timeline
    email_subject: str,
    email_body: str,
    ai_response: Optional[str],
) -> List[int]:
    """
    Create one DisputeMaster per additional issue found in the same email.
    Each gets:
      - Its own CUSTOMER_EMAIL episode  → timeline is self-contained
      - An AI_ACKNOWLEDGEMENT episode if an ai_response was generated
      - Activity log entries on both itself and the primary
      - SAME_CUSTOMER_BATCH relationship to primary
      - FA auto-assignment
    """
    from src.data.models.postgres.models import (
        DisputeActivityLog, DisputeMemoryEpisode,
    )
    from src.data.repositories.repositories import InvoiceRepository

    inline_ids: List[int] = []

    for idx, issue in enumerate(inline_issues):
        # ── Resolve dispute type ──────────────────────────────────────────────
        new_type_data = None
        if issue.get("is_new_type"):
            new_type_data = {
                "reason_name":    issue["dispute_type_name"],
                "description":    issue.get("new_type_description", ""),
                "severity_level": issue.get("new_type_severity", "MEDIUM"),
            }
        dtype = await _resolve_or_create_dispute_type(
            db_session,
            name=issue.get("dispute_type_name", "General Clarification"),
            new_type_data=new_type_data,
        )

        # ── Resolve invoice for this specific issue ───────────────────────────
        issue_invoice_id: Optional[int] = matched_invoice_id   # fallback to email's invoice
        issue_invoice_number = (issue.get("invoice_number") or "").strip()
        if issue_invoice_number:
            try:
                inv_repo = InvoiceRepository(db_session)
                inv = await inv_repo.get_by_invoice_number(issue_invoice_number)
                if inv:
                    issue_invoice_id = inv.invoice_id
                else:
                    results = await inv_repo.search_by_number_fuzzy(issue_invoice_number)
                    if results:
                        issue_invoice_id = results[0].invoice_id
            except Exception as inv_err:
                logger.warning(
                    f"[email_id={email_id}] inline[{idx}] invoice lookup failed "
                    f"for '{issue_invoice_number}': {inv_err}"
                )

        # ── Build description ─────────────────────────────────────────────────
        description = issue.get("description", "")

        # ── Create dispute row ────────────────────────────────────────────────
        inline_dispute = await _create_dispute(
            db_session,
            email_id=email_id,
            invoice_id=issue_invoice_id,
            payment_detail_id=None,
            customer_id=customer_id,
            dispute_type_id=dtype.dispute_type_id,
            priority=issue.get("priority", "MEDIUM"),
            description=description,
            parent_dispute_id=None,   # siblings, not children
        )
        inline_id = inline_dispute.dispute_id

        # ── Timeline ep 1: original customer email (verbatim) ────────────────
        # Identical content to the primary dispute's CUSTOMER_EMAIL episode.
        # Any FA opening this dispute directly sees the full original email —
        # no need to cross-reference the primary to understand the context.
        email_ep = DisputeMemoryEpisode(
            dispute_id=inline_id,
            episode_type="CUSTOMER_EMAIL",
            actor="CUSTOMER",
            content_text=f"Subject: {email_subject}\n\n{email_body[:1000]}",
            email_id=email_id,
        )
        issue_type_label = issue.get("dispute_type_name", "Additional Issue")
        db_session.add(email_ep)
        await db_session.flush()

        # ── Timeline ep 2: AI acknowledgement (if one was sent) ───────────────
        if ai_response:
            ack_ep = DisputeMemoryEpisode(
                dispute_id=inline_id,
                episode_type="AI_ACKNOWLEDGEMENT",
                actor="AI",
                content_text=(
                    f"[Combined acknowledgement sent for all issues in this email]\n\n"
                    f"{ai_response[:800]}"
                ),
                email_id=email_id,
            )
            db_session.add(ack_ep)

        # ── Activity logs ─────────────────────────────────────────────────────
        db_session.add(DisputeActivityLog(
            dispute_id=primary_dispute_id,
            action_type="INLINE_DISPUTE_CREATED",
            notes=(
                f"Additional issue logged as DISP-{inline_id:05d}: "
                f"{issue_type_label} — {description[:150]}"
            ),
        ))
        db_session.add(DisputeActivityLog(
            dispute_id=inline_id,
            action_type="CREATED_FROM_MULTI_ISSUE_EMAIL",
            notes=(
                f"Created alongside primary dispute DISP-{primary_dispute_id:05d} "
                f"from the same customer email (email_id={email_id}). "
                f"Issue type: {issue_type_label}."
            ),
        ))

        # ── Relationship ──────────────────────────────────────────────────────
        await _link_disputes(
            db_session,
            source_dispute_id=inline_id,
            target_dispute_id=primary_dispute_id,
            relationship_type="SAME_CUSTOMER_BATCH",
            context_note=(
                f"Both disputes raised in the same email. "
                f"This dispute: {issue_type_label}."
            ),
        )

        await _auto_assign(db_session, inline_id, email_id, label=f"[inline-{idx+1}]")
        await db_session.flush()

        inline_ids.append(inline_dispute.dispute_id)
        logger.info(
            f"[email_id={email_id}] Created inline dispute_id={inline_dispute.dispute_id} "
            f"({issue.get('dispute_type_name')}) linked to primary={primary_dispute_id}"
        )

    return inline_ids


# ─────────────────────────────────────────────────────────────────────────────
# Forked disputes  (context-shift in follow-up emails)
# ─────────────────────────────────────────────────────────────────────────────

async def _persist_forked_disputes(
    db_session,
    *,
    parent_dispute_id: int,
    forked_issues: List[Dict],
    email_id: int,
    customer_id: str,
) -> List[int]:
    from src.data.models.postgres.models import DisputeActivityLog
    from src.data.repositories.repositories import InvoiceRepository

    forked_ids: List[int] = []

    for issue in forked_issues:
        fork_dtype = await _resolve_or_create_dispute_type(
            db_session,
            name=issue.get("type_hint", "General Clarification"),
            new_type_data=None,
        )

        fork_invoice_id: Optional[int] = None
        invoice_number = (issue.get("invoice_number") or "").strip()
        if invoice_number:
            try:
                inv_repo = InvoiceRepository(db_session)
                inv = await inv_repo.get_by_invoice_number(invoice_number)
                if inv:
                    fork_invoice_id = inv.invoice_id
                else:
                    results = await inv_repo.search_by_number_fuzzy(invoice_number)
                    if results:
                        fork_invoice_id = results[0].invoice_id
            except Exception as inv_err:
                logger.warning(
                    f"[email_id={email_id}] fork invoice lookup failed "
                    f"for '{invoice_number}': {inv_err}"
                )

        fork_dispute = await _create_dispute(
            db_session,
            email_id=email_id,
            invoice_id=fork_invoice_id,
            payment_detail_id=None,
            customer_id=customer_id,
            dispute_type_id=fork_dtype.dispute_type_id,
            priority=issue.get("priority", "MEDIUM"),
            description=issue.get("description", ""),
            parent_dispute_id=parent_dispute_id,
        )

        db_session.add(DisputeActivityLog(
            dispute_id=parent_dispute_id,
            action_type="CONTEXT_SHIFT_FORK",
            notes=(
                f"New dispute DISP-{fork_dispute.dispute_id:05d} forked. "
                f"Reason: {issue.get('context_note') or 'Context shift detected by AI.'}"
            ),
        ))
        db_session.add(DisputeActivityLog(
            dispute_id=fork_dispute.dispute_id,
            action_type="FORKED_FROM_DISPUTE",
            notes=(
                f"Forked from DISP-{parent_dispute_id:05d}. "
                f"Reason: {issue.get('context_note') or 'Context shift detected by AI.'}"
            ),
        ))

        relationship_type = issue.get("relationship_type", "FORKED_FROM")

        await _link_disputes(
            db_session,
            source_dispute_id=fork_dispute.dispute_id,
            target_dispute_id=parent_dispute_id,
            relationship_type=relationship_type,
            context_note=issue.get("context_note"),
        )

        await _auto_assign(db_session, fork_dispute.dispute_id, email_id, label="[fork]")
        await db_session.flush()

        forked_ids.append(fork_dispute.dispute_id)
        logger.info(
            f"[email_id={email_id}] Forked dispute_id={fork_dispute.dispute_id} "
            f"({relationship_type}) from parent={parent_dispute_id}"
        )

    return forked_ids


# ─────────────────────────────────────────────────────────────────────────────
# Auto-response email sender
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Agent SMTP credentials builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_agent_smtp_override() -> dict:
    """
    Build the SMTP override dict for AI-agent auto-responses.
    Pulls credentials from settings so they can be managed centrally via
    environment variables / .env without touching code.
    """
    from src.config.settings import settings
    from src.core.services.imap_service import encode_password

    return {
        "smtp_host":    settings.AGENT_SMTP_HOST,
        "smtp_port":    settings.AGENT_SMTP_PORT,
        "smtp_use_tls": settings.AGENT_SMTP_USE_TLS,
        "username":     settings.AGENT_EMAIL,
        "password_enc": encode_password(settings.AGENT_EMAIL_PASSWORD),
        "from_address": settings.AGENT_EMAIL,
    }


async def _send_auto_response_email(
    *,
    dispute_id: int,
    sender_email: str,
    subject: str,
    ai_response: str,
    email_id: int,
    db_session,
    reply_to_message_id: int = None,
    dispute_type_name: str = "Payment Dispute",
) -> None:
    """
    Send the AI-generated response back to the customer via SMTP.
    Uses the first active, unpaused mailbox. Saves an OutboundEmail audit record.
    Errors are logged but never raise — email failure must not roll back the dispute.
    """
    try:
        from src.data.repositories.mailbox_repository import MailboxRepository, EmailInboxMessageRepository
        from src.core.services.outbound_email_service import OutboundEmailService
        from src.data.models.postgres.mailbox_models import EmailInboxMessage
        from sqlalchemy import select as _sa_select

        mb_repo   = MailboxRepository(db_session)
        mailboxes = await mb_repo.list_active_for_polling()
        if not mailboxes:
            logger.warning(
                f"[email_id={email_id}] Auto-response: no active mailbox configured — "
                f"response stored in DB only."
            )
            return

        mailbox = mailboxes[0]

        # Resolve the EmailInboxMessage.message_id for the inbound email that
        # triggered this response so the reply lands in the same thread.
        if reply_to_message_id is None:
            _row = await db_session.execute(
                _sa_select(EmailInboxMessage.message_id)
                .where(
                    EmailInboxMessage.email_inbox_id == email_id,
                    EmailInboxMessage.source == "INBOUND",
                )
                .limit(1)
            )
            _r = _row.first()
            reply_to_message_id = _r[0] if _r else None

        # Build a clean reply subject — fall back to dispute type if subject is blank
        _clean_subject = (subject or "").strip()
        if not _clean_subject or _clean_subject.lower() in ("re:", "re: ", "fw:", "fwd:"):
            _clean_subject = f"Re: {dispute_type_name}"
        elif not _clean_subject.lower().startswith("re:"):
            _clean_subject = f"Re: {_clean_subject}"
        reply_subject = _clean_subject

        # Convert the plain-text ai_response to minimal HTML
        body_html = "<p>" + ai_response.replace("\n", "<br/>") + "</p>"

        svc = OutboundEmailService(db_session)
        await svc.compose_and_send(
            dispute_id=dispute_id,
            sent_by_user_id=None,         # NULL = AI-generated (not an FA)
            to_email=sender_email,
            subject=reply_subject,
            body_html=body_html,
            body_text=ai_response,
            reply_to_message_id=reply_to_message_id,
            attachments=[],
            override_smtp_credentials=_build_agent_smtp_override(),
        )
        logger.info(
            f"[email_id={email_id}] Auto-response sent to {sender_email} "
            f"via mailbox {mailbox.email_address} for dispute_id={dispute_id} "
            f"reply_to_message_id={reply_to_message_id}"
        )
    except Exception as exc:
        logger.error(
            f"[email_id={email_id}] Auto-response email send failed "
            f"dispute_id={dispute_id} sender={sender_email}: {exc}",
            exc_info=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Main node
# ─────────────────────────────────────────────────────────────────────────────

@observe(name="node_persist_results")
async def node_persist_results(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    if not db_session:
        return state

    from src.data.repositories.repositories import (
        AnalysisSupportingRefRepository, DisputeRepository,
        EmailRepository, MemoryEpisodeRepository, OpenQuestionRepository,
    )
    from src.data.models.postgres.models import (
        DisputeActivityLog, DisputeAIAnalysis,
        DisputeMemoryEpisode, DisputeOpenQuestion, EmailInbox,
    )

    email_id = state["email_id"]

    try:
        # ── 1. Resolve primary dispute type ──────────────────────────────────
        dispute_type = await _resolve_or_create_dispute_type(
            db_session,
            name=state.get("dispute_type_name") or "General Clarification",
            new_type_data=state.get("_new_dispute_type"),
        )

        # Token match is the most authoritative signal — it means the customer
        # explicitly quoted our reference. Use it unconditionally even if
        # fetch_context overwrote existing_dispute_id with a different dispute.
        dispute_id = (
            state.get("token_matched_dispute_id")
            or state.get("existing_dispute_id")
        )
        primary_payment_id = (
            state["matched_payment_ids"][0] if state.get("matched_payment_ids") else None
        )

        # ── 2. Create or reuse primary dispute ────────────────────────────────
        if not dispute_id:
            # Build primary description
            primary_description = state.get("description", "")

            dispute = await _create_dispute(
                db_session,
                email_id=email_id,
                invoice_id=state.get("matched_invoice_id"),
                payment_detail_id=primary_payment_id,
                customer_id=state.get("customer_id") or "unknown",
                dispute_type_id=dispute_type.dispute_type_id,
                priority=state.get("priority", "MEDIUM"),
                description=primary_description,
                ownership_unverified=state.get("_ownership_unverified", False),
            )
            dispute_id    = dispute.dispute_id
            dispute_token = dispute.dispute_token

            # Flag unverified disputes with an activity log so FA knows why
            if state.get("_ownership_unverified"):
                db_session.add(DisputeActivityLog(
                    dispute_id=dispute_id,
                    action_type="OWNERSHIP_UNVERIFIED",
                    notes=(
                        f"Sender '{state.get('sender_email')}' could not be verified as "
                        f"the invoice owner. Invoice details were withheld from the AI response. "
                        f"FA should verify identity before proceeding."
                    ),
                ))

            # Resolve {DISPUTE_TOKEN} in the primary ai_response.
            # Also update per_issue_responses[0] so inline issues in step 9a
            # can replace any stray {DISPUTE_TOKEN} from their own LLM output.
            if state.get("ai_response"):
                updated_response = state["ai_response"]
                updated_response = updated_response.replace("{DISPUTE_TOKEN_1}", dispute_token)
                updated_response = updated_response.replace("{DISPUTE_TOKEN}", dispute_token)
                state = {**state, "ai_response": updated_response}

            # Keep per_issue_responses in sync — replace primary's token too
            pir = state.get("per_issue_responses") or []
            if pir:
                updated_pir = list(pir)
                if updated_pir[0].get("ai_response"):
                    updated_pir[0] = {
                        **updated_pir[0],
                        "ai_response": updated_pir[0]["ai_response"]
                            .replace("{DISPUTE_TOKEN_1}", dispute_token)
                            .replace("{DISPUTE_TOKEN}", dispute_token),
                    }
                state = {**state, "per_issue_responses": updated_pir}

            logger.info(
                f"[email_id={email_id}] New primary dispute: id={dispute_id} "
                f"token={dispute_token} "
                f"invoice_id={state.get('matched_invoice_id')} "
                f"inline_issues={len(state.get('inline_issues') or [])}"
            )
        else:
            dispute_token = f"DISP-{dispute_id:05d}"

            # Resolve {DISPUTE_TOKEN} in follow-up emails — the new dispute (step 2)
            # is skipped here but the LLM still wrote the placeholder. Replace it now.
            if state.get("ai_response"):
                updated_response = (
                    state["ai_response"]
                    .replace("{DISPUTE_TOKEN_1}", dispute_token)
                    .replace("{DISPUTE_TOKEN}", dispute_token)
                )
                state = {**state, "ai_response": updated_response}

            dispute = await DisputeRepository(db_session).get_by_id(dispute_id)
            if dispute:
                if not dispute.payment_detail_id and primary_payment_id:
                    dispute.payment_detail_id = primary_payment_id
                db_session.add(DisputeActivityLog(
                    dispute_id=dispute_id,
                    action_type="FOLLOW_UP_EMAIL_RECEIVED",
                    notes=f"Follow-up email: {state['subject'][:200]}",
                ))
                await db_session.flush()

        # ── 3. Customer email episode (FIRST — anchors timeline correctly) ──
        email_episode = DisputeMemoryEpisode(
            dispute_id=dispute_id,
            episode_type="CUSTOMER_EMAIL",
            actor="CUSTOMER",
            content_text=f"Subject: {state['subject']}\n\n{state['body_text'][:1000]}",
            email_id=email_id,
        )
        db_session.add(email_episode)
        await db_session.flush()

        # ── 4. AI analysis record (written after customer episode) ────────────
        analysis = DisputeAIAnalysis(
            dispute_id=dispute_id,
            predicted_category=state.get("dispute_type_name") or "General Clarification",
            confidence_score=state.get("confidence_score", 0.0),
            ai_summary=state.get("ai_summary", ""),
            ai_response=state.get("ai_response"),
            auto_response_generated=state.get("auto_response_generated", False),
            memory_context_used=state.get("memory_context_used", False),
            episodes_referenced=[
                int(x) for x in (state.get("episodes_referenced") or [])
                if str(x).lstrip("-").isdigit()
            ],
        )
        db_session.add(analysis)
        await db_session.flush()

        # ── 4a. Supporting docs ───────────────────────────────────────────────
        ref_repo = AnalysisSupportingRefRepository(db_session)
        if state.get("matched_invoice_id"):
            await ref_repo.upsert_supporting_doc(
                analysis_id=analysis.analysis_id,
                reference_table="invoice_data",
                ref_id_value=state["matched_invoice_id"],
                context_note=(
                    f"Invoice {state.get('matched_invoice_number', state['matched_invoice_id'])} "
                    "— primary supporting document"
                ),
            )
        for pid in state.get("matched_payment_ids", []):
            await ref_repo.upsert_supporting_doc(
                analysis_id=analysis.analysis_id,
                reference_table="payment_detail",
                ref_id_value=pid,
                context_note=f"Payment {pid} — supporting document",
            )

        # ── 6. FA open questions ──────────────────────────────────────────────
        for item in state.get("questions_to_ask", []):
            question_text = (
                item.get("question_text") or item.get("text") or str(item)
                if isinstance(item, dict) else str(item)
            )
            db_session.add(DisputeOpenQuestion(
                dispute_id=dispute_id,
                asked_in_episode_id=email_episode.episode_id,
                question_text=question_text,
                status="PENDING",
            ))

        # ── 7. Route email inbox record ───────────────────────────────────────
        email_repo = EmailRepository(db_session)
        await email_repo.update_status(email_id, "PROCESSED")
        await db_session.execute(
            sa_update(EmailInbox)
            .where(EmailInbox.email_id == email_id)
            .values(
                dispute_id=dispute_id,
                routing_confidence=state.get("routing_confidence", 0.0),
            )
        )

        # ── 8. Auto-assign primary dispute ────────────────────────────────────
        needs_fa = (
            not state.get("auto_response_generated")
            or state.get("_needs_invoice_details")
            or len(state.get("inline_issues") or []) > 0   # always assign if multi-issue
        )
        if needs_fa:
            await _auto_assign(db_session, dispute_id, email_id, label="[primary]")

        # ── 9a. Create INLINE disputes (multi-issue same email) ───────────────
        #
        # For multi-issue emails each inline issue now has its OWN ai_response
        # stored in state["per_issue_responses"]. We:
        #   1. Create each inline DisputeMaster row (same as before).
        #   2. Resolve its token {DISPUTE_TOKEN_N} in its own response string.
        #   3. Write its own DisputeAIAnalysis record with the resolved response.
        #   4. Write its own AI episode on its timeline.
        #
        # The primary state["ai_response"] is also updated for backwards compat.
        inline_dispute_ids: List[int] = []
        inline_issues      = state.get("inline_issues") or []
        per_issue_responses = state.get("per_issue_responses") or []

        # Build a lookup: issue_index → per_issue result (index 0 = primary)
        pir_by_index = {r["issue_index"]: r for r in per_issue_responses}

        if inline_issues:
            inline_dispute_ids = await _persist_inline_disputes(
                db_session,
                primary_dispute_id=dispute_id,
                inline_issues=inline_issues,
                email_id=email_id,
                customer_id=state.get("customer_id") or "unknown",
                matched_invoice_id=state.get("matched_invoice_id"),
                email_subject=state.get("subject", ""),
                email_body=state.get("body_text", ""),
                ai_response=None,   # each inline gets its own episode written below
            )

            # For each inline dispute: resolve its token, write its own analysis + episode
            for seq_idx, iid in enumerate(inline_dispute_ids, 2):
                issue_idx = seq_idx - 1          # issue_index in per_issue_responses
                pir       = pir_by_index.get(issue_idx)
                token_str = f"DISP-{iid:05d}"

                if pir:
                    # Resolve {DISPUTE_TOKEN_N} AND any stray bare {DISPUTE_TOKEN}
                    # — the LLM sometimes writes the bare placeholder even in focused
                    # mode. Both must map to THIS issue's real token.
                    resolved_resp = (
                        pir["ai_response"]
                        .replace(f"{{DISPUTE_TOKEN_{seq_idx}}}", token_str)
                        .replace("{DISPUTE_TOKEN}", token_str)
                    ) if pir.get("ai_response") else None

                    # Write this inline issue's own DisputeAIAnalysis
                    inline_analysis = DisputeAIAnalysis(
                        dispute_id=iid,
                        predicted_category=inline_issues[issue_idx - 1].get(
                            "dispute_type_name", "General Clarification"
                        ),
                        confidence_score=pir.get("confidence_score", 0.0),
                        ai_summary=pir.get("ai_summary", ""),
                        ai_response=resolved_resp,
                        auto_response_generated=pir.get("can_auto_respond", False),
                        memory_context_used=pir.get("memory_context_used", False),
                        episodes_referenced=pir.get("episodes_referenced") or [],
                    )
                    db_session.add(inline_analysis)
                    await db_session.flush()

                    # Write AI episode on this inline dispute's timeline
                    if resolved_resp:
                        ep_type = ("AI_RESPONSE" if pir.get("can_auto_respond")
                                   else "AI_ACKNOWLEDGEMENT")
                        db_session.add(DisputeMemoryEpisode(
                            dispute_id=iid,
                            episode_type=ep_type,
                            actor="AI",
                            content_text=resolved_resp,
                            email_id=email_id,
                        ))

                    # FA open questions for this inline dispute
                    for q_text in (pir.get("questions_to_ask") or []):
                        db_session.add(DisputeOpenQuestion(
                            dispute_id=iid,
                            question_text=q_text,
                            status="PENDING",
                        ))

                    await db_session.flush()

                # Also resolve the token in the primary's ai_response (backwards compat)
                if state.get("ai_response"):
                    state = {
                        **state,
                        "ai_response": state["ai_response"].replace(
                            f"{{DISPUTE_TOKEN_{seq_idx}}}", token_str
                        ),
                    }

            # Back-fill primary analysis with its own resolved response
            if state.get("ai_response"):
                analysis.ai_response = state["ai_response"]
                await db_session.flush()

            logger.info(
                f"[email_id={email_id}] Created {len(inline_dispute_ids)} inline dispute(s): "
                f"{inline_dispute_ids} — each with own ai_response"
            )
        # ── 5. AI response episode (written AFTER all tokens are resolved) ────
        # For single-issue emails ai_response never had inline placeholders so
        # order did not matter previously. For multi-issue emails we now have
        # the fully-substituted text. Writing here guarantees the stored episode
        # always shows real DISP-XXXXX references.
        ai_episode = None
        if state.get("ai_response"):
            ep_type = "AI_RESPONSE" if state.get("auto_response_generated") else "AI_ACKNOWLEDGEMENT"
            ai_episode = DisputeMemoryEpisode(
                dispute_id=dispute_id,
                episode_type=ep_type,
                actor="AI",
                content_text=state["ai_response"],
                email_id=email_id,
            )
            db_session.add(ai_episode)
            await db_session.flush()

            answered_ids = state.get("_answers_pending_questions", [])
            if answered_ids:
                q_repo = OpenQuestionRepository(db_session)
                for qid in answered_ids:
                    q = await q_repo.get_by_id(qid)
                    if q and q.status == "PENDING":
                        q.status                 = "ANSWERED"
                        q.answered_in_episode_id = ai_episode.episode_id
                        q.answered_at            = datetime.now(timezone.utc)

            # NOTE: inline dispute episodes are written individually in step 9a
            # (each with its own resolved ai_response). No back-fill needed here.

        # ── 5a. Embed AI summary ──────────────────────────────────────────────
        ai_summary_text = (state.get("ai_summary") or "").strip()
        if ai_episode and ai_summary_text:
            from src.handlers.http_clients.llm_client import get_llm_client
            try:
                embedding = await get_llm_client().embed(ai_summary_text)
                if embedding:
                    ep_repo = MemoryEpisodeRepository(db_session)
                    await ep_repo.upsert_embedding(ai_episode.episode_id, embedding)
                    logger.info(
                        f"[email_id={email_id}] Saved embedding (dims={len(embedding)}) "
                        f"on episode_id={ai_episode.episode_id}"
                    )
            except Exception as emb_err:
                logger.warning(f"[email_id={email_id}] Embedding save failed: {emb_err}")

        # ── 9b. Create FORKED disputes (context-shift follow-up) ──────────────
        forked_ids: List[int] = []
        if state.get("context_shift_detected") and state.get("forked_issues"):
            forked_ids = await _persist_forked_disputes(
                db_session,
                parent_dispute_id=dispute_id,
                forked_issues=state["forked_issues"],
                email_id=email_id,
                customer_id=state.get("customer_id") or "unknown",
            )
            logger.info(
                f"[email_id={email_id}] Context shift: created {len(forked_ids)} "
                f"forked dispute(s): {forked_ids}"
            )

            # Send a fresh-thread notification email for each forked dispute.
            # No In-Reply-To header — this intentionally starts a new thread so
            # the customer sees a distinct conversation for the new dispute.
            from src.core.services.outbound_email_service import OutboundEmailService
            _fork_svc = OutboundEmailService(db_session)
            for _fork_id in forked_ids:
                try:
                    _fork_dispute = await _fork_svc.disp_repo.get_by_id(_fork_id)
                    _fork_token   = getattr(_fork_dispute, "dispute_token", f"DISP-{_fork_id:05d}")
                    _fork_type    = state.get("dispute_type_name") or "Payment Dispute"
                    _fork_subject = f"[{_fork_token}] {_fork_type}"
                    _fork_body    = (
                        f"Dear Customer,\n\n"
                        f"We have identified a new dispute in your recent correspondence "
                        f"and raised it as a separate case.\n\n"
                        f"Your reference: {_fork_token}\n\n"
                        f"Please use this reference number in all future correspondence "
                        f"regarding this specific issue.\n\n"
                        f"Our team will be in touch shortly.\n\n"
                        f"Regards,\n"
                        f"Accounts Receivable Team"
                    )
                    await _fork_svc.compose_and_send(
                        dispute_id=_fork_id,
                        sent_by_user_id=None,
                        to_email=state["sender_email"],
                        subject=_fork_subject,
                        body_html="<p>" + _fork_body.replace("\n", "<br/>") + "</p>",
                        body_text=_fork_body,
                        reply_to_message_id=None,   # fresh thread — no In-Reply-To
                        force_new_thread=True,
                        attachments=[],
                        override_smtp_credentials=_build_agent_smtp_override(),
                    )
                    logger.info(
                        f"[email_id={email_id}] Fresh-thread notification sent "
                        f"for forked dispute_id={_fork_id} token={_fork_token}"
                    )
                except Exception as _fork_mail_err:
                    logger.error(
                        f"[email_id={email_id}] Fork notification email failed "
                        f"for dispute_id={_fork_id}: {_fork_mail_err}",
                        exc_info=True,
                    )

            if state.get("context_shift_reasoning"):
                db_session.add(DisputeActivityLog(
                    dispute_id=dispute_id,
                    action_type="CONTEXT_SHIFT_DETECTED",
                    notes=(
                        f"AI detected context shift (confidence="
                        f"{state.get('context_shift_confidence', 0):.0%}). "
                        f"Reason: {state['context_shift_reasoning']}. "
                        f"Forked: {forked_ids}."
                    ),
                ))

        await db_session.commit()

        # ── 10. Send AI response via SMTP whenever ai_response is set ──────
        # auto_response_generated=False means the AI flagged this for human
        # review, but we still send an acknowledgement so the customer knows
        # their email was received. The episode type (AI_RESPONSE vs
        # AI_ACKNOWLEDGEMENT) already captures the distinction.
        if state.get("ai_response"):
            await _send_auto_response_email(
                dispute_id=dispute_id,
                sender_email=state["sender_email"],
                subject=state.get("subject", ""),
                ai_response=state["ai_response"],
                email_id=email_id,
                db_session=db_session,
                dispute_type_name=state.get("dispute_type_name") or "Payment Dispute",
            )

        # ── 10a. Send individual emails for each INLINE dispute ───────────────
        # Each inline dispute has its own resolved ai_response (stored in
        # pir_by_index). We send a separate email per inline dispute so the
        # customer receives one email per issue with the correct dispute token.
        if inline_dispute_ids:
            pir_by_index_local = {
                r["issue_index"]: r
                for r in (state.get("per_issue_responses") or [])
            }
            for seq_idx, iid in enumerate(inline_dispute_ids, 2):
                issue_idx = seq_idx - 1
                pir       = pir_by_index_local.get(issue_idx)
                if not pir:
                    continue
                token_str     = f"DISP-{iid:05d}"
                resolved_resp = (
                    pir["ai_response"]
                    .replace(f"{{DISPUTE_TOKEN_{seq_idx}}}", token_str)
                    .replace("{DISPUTE_TOKEN}", token_str)
                ) if pir.get("ai_response") else None

                if resolved_resp:
                    try:
                        inline_type_name = (
                            inline_issues[issue_idx - 1].get("dispute_type_name")
                            if inline_issues and len(inline_issues) >= issue_idx
                            else "Payment Dispute"
                        ) or "Payment Dispute"
                        await _send_auto_response_email(
                            dispute_id=iid,
                            sender_email=state["sender_email"],
                            subject=state.get("subject", ""),
                            ai_response=resolved_resp,
                            email_id=email_id,
                            db_session=db_session,
                            dispute_type_name=inline_type_name,
                        )
                        logger.info(
                            f"[email_id={email_id}] Sent inline auto-response "
                            f"for dispute_id={iid} token={token_str}"
                        )
                    except Exception as _inline_mail_err:
                        logger.error(
                            f"[email_id={email_id}] Inline auto-response email failed "
                            f"for dispute_id={iid}: {_inline_mail_err}",
                            exc_info=True,
                        )

        # ── 11. Async summarisation trigger ───────────────────────────────────
        from src.config.settings import settings
        ep_repo  = MemoryEpisodeRepository(db_session)
        ep_count = await ep_repo.count_for_dispute(dispute_id)
        if ep_count >= settings.EPISODE_SUMMARIZE_THRESHOLD:
            from src.control.tasks import summarize_episodes_task
            summarize_episodes_task.delay(dispute_id)

        total_disputes = 1 + len(inline_dispute_ids) + len(forked_ids)
        langfuse_context.update_current_observation(
            output={
                "dispute_id":          dispute_id,
                "analysis_id":         analysis.analysis_id,
                "is_new_dispute":      state.get("existing_dispute_id") is None,
                "inline_dispute_ids":  inline_dispute_ids,
                "forked_dispute_ids":  forked_ids,
                "total_disputes":      total_disputes,
            }
        )

        return {
            **state,
            "dispute_id":         dispute_id,
            "analysis_id":        analysis.analysis_id,
            "inline_dispute_ids": inline_dispute_ids,
            "forked_dispute_ids": forked_ids,
        }

    except Exception as exc:
        logger.error(f"[email_id={email_id}] Persist error: {exc}", exc_info=True)
        await db_session.rollback()
        try:
            from src.data.repositories.repositories import EmailRepository as ER
            await ER(db_session).update_status(email_id, "FAILED", str(exc))
            await db_session.commit()
        except Exception:
            pass
        return {**state, "error": str(exc)}