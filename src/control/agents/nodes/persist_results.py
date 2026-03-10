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
):
    from src.data.models.postgres.models import DisputeMaster

    dispute = DisputeMaster(
        email_id=email_id,
        invoice_id=invoice_id,
        payment_detail_id=payment_detail_id,
        customer_id=customer_id,
        dispute_type_id=dispute_type_id,
        status="OPEN",
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
    from src.data.repositories.repositories import DisputeAssignmentRepository, UserRepository

    assign_repo       = DisputeAssignmentRepository(db_session)
    active_assignment = await assign_repo.get_active_assignment(dispute_id)
    if active_assignment:
        logger.info(f"[email_id={email_id}] {label} dispute_id={dispute_id} already assigned")
        return

    user_repo = UserRepository(db_session)
    all_users = await user_repo.get_all(limit=10)
    if not all_users:
        logger.warning(f"[email_id={email_id}] {label} no users — dispute_id={dispute_id} unassigned")
        return

    db_session.add(DisputeAssignment(
        dispute_id=dispute_id,
        assigned_to=all_users[0].user_id,
        status="ACTIVE",
    ))
    logger.info(
        f"[email_id={email_id}] {label} auto-assigned dispute_id={dispute_id} "
        f"to user_id={all_users[0].user_id}"
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
        if issue.get("disputed_amount"):
            description = f"{description} (Disputed amount: {issue['disputed_amount']})"

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

        dispute_id         = state.get("existing_dispute_id")
        primary_payment_id = (
            state["matched_payment_ids"][0] if state.get("matched_payment_ids") else None
        )

        # ── 2. Create or reuse primary dispute ────────────────────────────────
        if not dispute_id:
            # Build a description that includes disputed amount if available
            primary_description = state.get("description", "")
            if state.get("disputed_amount"):
                primary_description = (
                    f"{primary_description} (Disputed amount: {state['disputed_amount']})"
                )

            dispute = await _create_dispute(
                db_session,
                email_id=email_id,
                invoice_id=state.get("matched_invoice_id"),
                payment_detail_id=primary_payment_id,
                customer_id=state.get("customer_id") or "unknown",
                dispute_type_id=dispute_type.dispute_type_id,
                priority=state.get("priority", "MEDIUM"),
                description=primary_description,
            )
            dispute_id    = dispute.dispute_id
            dispute_token = dispute.dispute_token

            # Back-fill the real token into the AI response.
            # Multi-issue emails use {DISPUTE_TOKEN_1} for the primary.
            # Single-issue emails use {DISPUTE_TOKEN}.
            if state.get("ai_response"):
                updated_response = state["ai_response"]
                updated_response = updated_response.replace("{DISPUTE_TOKEN_1}", dispute_token)
                updated_response = updated_response.replace("{DISPUTE_TOKEN}", dispute_token)
                state = {**state, "ai_response": updated_response}

            logger.info(
                f"[email_id={email_id}] New primary dispute: id={dispute_id} "
                f"token={dispute_token} "
                f"invoice_id={state.get('matched_invoice_id')} "
                f"inline_issues={len(state.get('inline_issues') or [])}"
            )
        else:
            dispute_token = f"DISP-{dispute_id:05d}"
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

        # ── 3. AI analysis record ─────────────────────────────────────────────
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

        # ── 3a. Supporting docs ───────────────────────────────────────────────
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

        # ── 4. Customer email episode ─────────────────────────────────────────
        email_episode = DisputeMemoryEpisode(
            dispute_id=dispute_id,
            episode_type="CUSTOMER_EMAIL",
            actor="CUSTOMER",
            content_text=f"Subject: {state['subject']}\n\n{state['body_text'][:1000]}",
            email_id=email_id,
        )
        db_session.add(email_episode)
        await db_session.flush()

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
        # CRITICAL ORDER: inline disputes are created BEFORE the AI episode is
        # written. The multi-issue ack email contains {DISPUTE_TOKEN_2},
        # {DISPUTE_TOKEN_3} … placeholders that are only resolved once the
        # inline DisputeMaster rows are flushed and their IDs are known.
        # Writing the AI episode after this block guarantees the stored episode
        # content always contains real DISP-XXXXX tokens — never raw placeholders.
        inline_dispute_ids: List[int] = []
        inline_issues = state.get("inline_issues") or []
        if inline_issues:
            # Pass ai_response=None — we write the ack episode below once
            # all tokens are substituted, so inline timelines don't get a
            # stale copy with unresolved placeholders.
            inline_dispute_ids = await _persist_inline_disputes(
                db_session,
                primary_dispute_id=dispute_id,
                inline_issues=inline_issues,
                email_id=email_id,
                customer_id=state.get("customer_id") or "unknown",
                matched_invoice_id=state.get("matched_invoice_id"),
                email_subject=state.get("subject", ""),
                email_body=state.get("body_text", ""),
                ai_response=None,   # filled in below after token resolution
            )
            # Substitute {DISPUTE_TOKEN_2}, {DISPUTE_TOKEN_3} … now that IDs exist.
            # {DISPUTE_TOKEN_1} / {DISPUTE_TOKEN} were replaced for the primary above.
            if state.get("ai_response"):
                updated_response = state["ai_response"]
                for seq_idx, iid in enumerate(inline_dispute_ids, 2):
                    updated_response = updated_response.replace(
                        f"{{DISPUTE_TOKEN_{seq_idx}}}", f"DISP-{iid:05d}"
                    )
                state = {**state, "ai_response": updated_response}

            logger.info(
                f"[email_id={email_id}] Created {len(inline_dispute_ids)} inline dispute(s): "
                f"{inline_dispute_ids}"
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

            # Back-fill the fully-resolved ack onto each inline dispute's timeline
            if inline_dispute_ids:
                for iid in inline_dispute_ids:
                    db_session.add(DisputeMemoryEpisode(
                        dispute_id=iid,
                        episode_type="AI_ACKNOWLEDGEMENT",
                        actor="AI",
                        content_text=state["ai_response"],
                        email_id=email_id,
                    ))

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

        # ── 10. Async summarisation trigger ───────────────────────────────────
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
