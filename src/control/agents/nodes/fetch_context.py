"""
src/control/agents/nodes/fetch_context.py
"""

from __future__ import annotations
import logging
from typing import Optional, List, Dict

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState

logger = logging.getLogger(__name__)


@observe(name="node_fetch_context")
async def node_fetch_context(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Dispute lookup uses a 4-level fallback chain:
      1. customer_id + invoice_id + dispute_type_name   (most precise)
      2. customer_id + invoice_id                        (type mismatch / re-open)
      3. customer_id + invoice_id IS NULL                (follow-up to a cold mail — links invoice)
      4. customer_id only                                (new cold mail, no invoice in email)

    Memory (episodes, summary, pending questions) is loaded for whatever dispute is found.
    """
    if not db_session:
        return {
            **state,
            "invoice_details":     None,
            "all_payment_details": [],
            "existing_dispute_id": None,
            "memory_summary":      None,
            "recent_episodes":     [],
            "pending_questions":   [],
        }

    from src.data.repositories.repositories import (
        InvoiceRepository, PaymentRepository, DisputeRepository,
        MemoryEpisodeRepository, MemorySummaryRepository, OpenQuestionRepository,
    )

    invoice_details:     Optional[Dict] = None
    all_payment_details: List[Dict]     = []
    existing_dispute_id: Optional[int]  = None
    memory_summary:      Optional[str]  = None
    recent_episodes:     List[Dict]     = []
    pending_questions:   List[Dict]     = []

    # ── Invoice details ───────────────────────────────────────────────────────
    if state["matched_invoice_id"]:
        inv_repo = InvoiceRepository(db_session)
        invoice  = await inv_repo.get_by_id(state["matched_invoice_id"])
        if invoice:
            db_details  = invoice.invoice_details or {}
            groq_data   = state.get("groq_extracted") or {}
            invoice_details = {
                **db_details,
                **{k: v for k, v in groq_data.items() if v is not None},
            }
            # Explicitly hoist line_items to the top level so the LLM prompt
            # can see them directly rather than having to dig through the blob.
            # If line_items is absent or empty, set an explicit marker so the
            # LLM knows the data is missing (not just not shown).
            if not invoice_details.get("line_items"):
                invoice_details["line_items"] = None  # explicit missing signal

    # ── All payment records for this invoice ─────────────────────────────────
    if state.get("matched_payment_ids"):
        pay_repo = PaymentRepository(db_session)
        for pid in state["matched_payment_ids"]:
            payment = await pay_repo.get_by_id(pid)
            if payment and payment.payment_details:
                all_payment_details.append({
                    "payment_detail_id": payment.payment_detail_id,
                    "invoice_number":    payment.invoice_number,
                    **payment.payment_details,
                })

    # ── Dispute lookup (4-level fallback) ─────────────────────────────────────
    customer_id       = state.get("customer_id")
    dispute_type_name = state.get("dispute_type_name", "")
    matched_invoice_id = state.get("matched_invoice_id")
    matched_dispute   = None

    if customer_id:
        dispute_repo  = DisputeRepository(db_session)
        open_disputes = await dispute_repo.get_by_customer(customer_id)

        if open_disputes:
            # Level 1: customer + invoice + dispute type (most precise)
            # Normalise both sides to lowercase + collapsed whitespace so LLM
            # capitalisation variance ("pricing mismatch" vs "Pricing Mismatch")
            # does not cause a miss.
            if matched_invoice_id and dispute_type_name:
                _norm = lambda s: " ".join((s or "").lower().split())
                _norm_type = _norm(dispute_type_name)
                for d in open_disputes:
                    if (
                        d.invoice_id == matched_invoice_id
                        and d.dispute_type
                        and _norm(d.dispute_type.reason_name) == _norm_type
                    ):
                        matched_dispute = d
                        logger.info(
                            f"[email_id={state['email_id']}] L1 match: "
                            f"customer+invoice+type → dispute_id={d.dispute_id} "
                            f"(matched '{d.dispute_type.reason_name}' ≈ '{dispute_type_name}')"
                        )
                        break

            # Level 2: customer + invoice (type mismatch / re-open)
            # If multiple disputes share the same invoice, prefer the one whose
            # type name is closest to what the LLM returned — handles LLM paraphrase
            # cases where L1 missed due to minor wording differences.
            if not matched_dispute and matched_invoice_id:
                invoice_disputes = [d for d in open_disputes if d.invoice_id == matched_invoice_id]
                if invoice_disputes:
                    if len(invoice_disputes) == 1:
                        matched_dispute = invoice_disputes[0]
                    else:
                        # Score each by normalised type name similarity
                        _norm = lambda s: " ".join((s or "").lower().split())
                        _norm_type = _norm(dispute_type_name)
                        def _score(d):
                            name = _norm(d.dispute_type.reason_name if d.dispute_type else "")
                            if name == _norm_type:
                                return 2   # exact match
                            if _norm_type and (name in _norm_type or _norm_type in name):
                                return 1   # partial match
                            return 0
                        matched_dispute = max(invoice_disputes, key=_score)
                    logger.info(
                        f"[email_id={state['email_id']}] L2 match: "
                        f"customer+invoice → dispute_id={matched_dispute.dispute_id} "
                        f"(from {len(invoice_disputes)} candidate(s))"
                    )

            # Level 3: follow-up to cold mail (dispute exists but had no invoice yet)
            if not matched_dispute and matched_invoice_id:
                for d in open_disputes:
                    if d.invoice_id is None:
                        matched_dispute = d
                        logger.info(
                            f"[email_id={state['email_id']}] L3 match (cold-mail follow-up): "
                            f"linking invoice_id={matched_invoice_id} → dispute_id={d.dispute_id}"
                        )
                        try:
                            d.invoice_id = matched_invoice_id
                            await db_session.flush()
                        except Exception as patch_err:
                            logger.warning(
                                f"[email_id={state['email_id']}] Could not patch "
                                f"invoice_id on dispute: {patch_err}"
                            )
                        break

            # Level 4: cold mail — no invoice in email
            if not matched_dispute and not matched_invoice_id:
                matched_dispute = open_disputes[0]
                logger.info(
                    f"[email_id={state['email_id']}] L4 match (cold mail): "
                    f"most recent dispute for customer={customer_id} → dispute_id={matched_dispute.dispute_id}"
                )

    # ── Load memory ───────────────────────────────────────────────────────────
    if matched_dispute:
        existing_dispute_id = matched_dispute.dispute_id

        ep_repo    = MemoryEpisodeRepository(db_session)
        recent_eps = await ep_repo.get_latest_n(existing_dispute_id, n=5)
        recent_episodes = [
            {"actor": ep.actor, "type": ep.episode_type, "text": ep.content_text[:400]}
            for ep in recent_eps
        ]

        sum_repo    = MemorySummaryRepository(db_session)
        summary_obj = await sum_repo.get_for_dispute(existing_dispute_id)
        if summary_obj:
            memory_summary = summary_obj.summary_text

        q_repo     = OpenQuestionRepository(db_session)
        pending_qs = await q_repo.get_pending_for_dispute(existing_dispute_id)
        pending_questions = [
            {"question_id": q.question_id, "text": q.question_text}
            for q in pending_qs
        ]

        logger.info(
            f"[email_id={state['email_id']}] Memory loaded for dispute_id={existing_dispute_id}: "
            f"{len(recent_episodes)} episodes, {len(pending_questions)} pending questions"
        )

    langfuse_context.update_current_observation(
        output={
            "existing_dispute_id":  existing_dispute_id,
            "episodes_loaded":      len(recent_episodes),
            "pending_questions":    len(pending_questions),
            "has_memory_summary":   memory_summary is not None,
        }
    )

    return {
        **state,
        "invoice_details":     invoice_details,
        "all_payment_details": all_payment_details,
        "existing_dispute_id": existing_dispute_id,
        "memory_summary":      memory_summary,
        "recent_episodes":     recent_episodes,
        "pending_questions":   pending_questions,
    }