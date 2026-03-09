"""
src/control/agents/nodes/identify_invoice.py
"""

from __future__ import annotations
import logging
from typing import Optional, List

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState

logger = logging.getLogger(__name__)


@observe(name="node_identify_invoice")
async def node_identify_invoice(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Match invoice number against DB. Fetch ALL payment records for the matched invoice.
    Derive customer_id from Groq extraction first, then sender email domain as fallback.
    """
    if not db_session:
        return {**state, "matched_invoice_id": None, "routing_confidence": 0.0}

    from src.data.repositories.repositories import InvoiceRepository, PaymentRepository
    inv_repo = InvoiceRepository(db_session)
    pay_repo = PaymentRepository(db_session)

    matched_invoice = None
    confidence = 0.0

    for candidate in state["candidate_invoice_numbers"]:
        invoice = await inv_repo.get_by_invoice_number(candidate)
        if invoice:
            matched_invoice = invoice
            confidence = 0.95
            break

    if not matched_invoice and state["candidate_invoice_numbers"]:
        for candidate in state["candidate_invoice_numbers"]:
            results = await inv_repo.search_by_number_fuzzy(candidate)
            if results:
                matched_invoice = results[0]
                confidence = 0.65
                break

    # Derive customer_id
    customer_id = state.get("customer_id")
    if not customer_id and state.get("groq_extracted"):
        customer_id = (
            state["groq_extracted"].get("customer_id")
            or state["groq_extracted"].get("customer_name")
        )
    if not customer_id:
        sender = state["sender_email"]
        domain = sender.split("@")[-1].split(".")[0] if "@" in sender else sender
        customer_id = domain

    # Fetch ALL payments for this invoice
    matched_payment_ids: List[int] = []
    if matched_invoice:
        payments = await pay_repo.get_all_by_invoice_number(matched_invoice.invoice_number)
        matched_payment_ids = [p.payment_detail_id for p in payments]
        logger.info(
            f"[email_id={state['email_id']}] Matched invoice={matched_invoice.invoice_number}, "
            f"payments={matched_payment_ids}"
        )
    else:
        logger.info(
            f"[email_id={state['email_id']}] No invoice matched from "
            f"candidates={state['candidate_invoice_numbers']}"
        )

    langfuse_context.update_current_observation(
        output={
            "matched_invoice_number": matched_invoice.invoice_number if matched_invoice else None,
            "confidence": confidence,
            "payment_count": len(matched_payment_ids),
            "customer_id": customer_id,
        }
    )

    return {
        **state,
        "matched_invoice_id":     matched_invoice.invoice_id if matched_invoice else None,
        "matched_invoice_number": matched_invoice.invoice_number if matched_invoice else None,
        "matched_payment_ids":    matched_payment_ids,
        "customer_id":            customer_id,
        "routing_confidence":     confidence,
    }
