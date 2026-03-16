"""
src/control/agents/nodes/identify_invoice.py
============================================
Matches invoice numbers against the DB and resolves customer_id.

customer_id resolution — strict priority order
-----------------------------------------------
1. Sender email domain  ← AUTHORITATIVE — who sent the email IS the customer.
   e.g. finance@metro.com  →  "metro"
        ap@acmecorp.com    →  "acmecorp"

2. Groq LLM extraction (customer_id / customer_name field from the email body).
   Only used when the domain alone is ambiguous (e.g. gmail.com, yahoo.com,
   or single-character domains).

3. Payment record's customer_id — used ONLY to CONFIRM, never to override.
   The invoice might belong to a different customer in the DB (shared invoices,
   re-invoicing) so we NEVER pull customer_id from the payment record and assign
   it to the dispute. The dispute always belongs to whoever sent the email.

Why this matters
----------------
If Metro emails us about invoice INV-2024-005 (which may exist in DB under
acmecorp's payment records), the dispute must belong to Metro — not acmecorp.
Using the payment's customer_id would silently mis-assign the dispute.
"""

from __future__ import annotations
import logging
from typing import List, Optional

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState

logger = logging.getLogger(__name__)

# Domains that are generic mail providers — don't use as customer_id
_GENERIC_DOMAINS = frozenset({
    "gmail", "yahoo", "hotmail", "outlook", "rediffmail",
    "icloud", "protonmail", "live", "msn", "aol",
    "ymail", "googlemail", "mail", "inbox", "zoho",
    "tutanota", "fastmail", "pm",
})


def _extract_domain(email: str) -> Optional[str]:
    """Return the domain part of an email, lowercased."""
    if not email or "@" not in email:
        return None
    return email.strip().lower().split("@", 1)[1]


def _is_generic_domain(domain: str) -> bool:
    """True if the domain is a well-known public mail provider."""
    # Match on the first label only (gmail.com → gmail, yahoo.co.uk → yahoo)
    first_label = domain.split(".")[0]
    return first_label in _GENERIC_DOMAINS


def _check_invoice_ownership(invoice_customer_id: str, sender_email: str) -> tuple[bool, str]:
    """
    Returns (is_verified, reason).

    Level 1 — Exact match:  invoice.customer_id == sender_email  → verified
    Level 2 — Domain match: same non-generic corporate domain     → verified
    Level 3 — No match:                                           → unverified
    """
    inv_cid   = (invoice_customer_id or "").strip().lower()
    sender    = (sender_email or "").strip().lower()

    # Level 1: exact match
    if inv_cid == sender:
        return True, "exact_match"

    # Level 2: corporate domain match (skip generic providers)
    inv_domain    = _extract_domain(inv_cid)
    sender_domain = _extract_domain(sender)

    if (
        inv_domain
        and sender_domain
        and inv_domain == sender_domain
        and not _is_generic_domain(sender_domain)
    ):
        return True, f"domain_match:{sender_domain}"

    return False, f"no_match(invoice_owner={inv_cid}, sender={sender})"


def _derive_customer_id_from_sender(sender_email: str) -> Optional[str]:
    """
    Use the full sender email address as the customer identifier.
    This is the most accurate and unambiguous customer ID — avoids confusion
    where domain extraction (e.g. "kce" from kce.ac.in, "accounts" from
    accounts@google.com) produces misleading or colliding identifiers.
    """
    if not sender_email or "@" not in sender_email:
        return None
    return sender_email.strip().lower()


@observe(name="node_identify_invoice")
async def node_identify_invoice(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    1. Match invoice number(s) against DB (exact → fuzzy fallback).
    2. Fetch ALL payment records for the matched invoice.
    3. Resolve customer_id — sender email is ALWAYS authoritative.
    """
    if not db_session:
        return {**state, "matched_invoice_id": None, "routing_confidence": 0.0}

    from src.data.repositories.repositories import InvoiceRepository, PaymentRepository
    inv_repo = InvoiceRepository(db_session)
    pay_repo = PaymentRepository(db_session)

    # ── Invoice matching ──────────────────────────────────────────────────────
    matched_invoice = None
    confidence      = 0.0

    for candidate in state["candidate_invoice_numbers"]:
        invoice = await inv_repo.get_by_invoice_number(candidate)
        if invoice:
            matched_invoice = invoice
            confidence      = 0.95
            break

    if not matched_invoice and state["candidate_invoice_numbers"]:
        for candidate in state["candidate_invoice_numbers"]:
            results = await inv_repo.search_by_number_fuzzy(candidate)
            if results:
                matched_invoice = results[0]
                confidence      = 0.65
                break

    # ── customer_id resolution — sender-first, always ─────────────────────────
    # Step 1: sender email domain (most reliable — who sent the email IS the customer)
    customer_id = _derive_customer_id_from_sender(state["sender_email"])

    # Step 2: Groq extraction — used when sender domain is generic or absent
    if not customer_id and state.get("groq_extracted"):
        groq_cid = (
            state["groq_extracted"].get("customer_id")
            or state["groq_extracted"].get("customer_name")
        )
        if groq_cid:
            customer_id = str(groq_cid).strip()

    # Step 3: absolute fallback — full sender email (never use payment record)
    if not customer_id:
        customer_id = state["sender_email"]

    logger.info(
        f"[email_id={state['email_id']}] customer_id resolved to '{customer_id}' "
        f"from sender='{state['sender_email']}'"
    )

    # ── Ownership verification ────────────────────────────────────────────────
    # customer_id lives on PaymentDetail (not InvoiceData). Look it up via
    # the payment records linked to this invoice, then verify the sender.
    # Unverified disputes are still created but flagged for FA review —
    # we never leak invoice data to unverified senders.
    ownership_unverified = False
    if matched_invoice:
        # Fetch payment records to get customer_id
        _payments = await pay_repo.get_all_by_invoice_number(matched_invoice.invoice_number)
        _invoice_customer_id = _payments[0].customer_id if _payments else None

        if _invoice_customer_id:
            is_verified, reason = _check_invoice_ownership(
                invoice_customer_id=_invoice_customer_id,
                sender_email=state["sender_email"],
            )
            if not is_verified:
                ownership_unverified = True
                logger.warning(
                    f"[email_id={state['email_id']}] Ownership UNVERIFIED for "
                    f"invoice={matched_invoice.invoice_number}: {reason} — "
                    f"clearing invoice details, flagging dispute as UNVERIFIED"
                )
                # Clear the matched invoice so the LLM never receives its details
                matched_invoice = None
                confidence      = 0.0
            else:
                logger.info(
                    f"[email_id={state['email_id']}] Ownership verified ({reason}) "
                    f"for invoice={matched_invoice.invoice_number}"
                )
        else:
            # No payment record yet — can't verify ownership, let it through
            # FA will review manually
            logger.info(
                f"[email_id={state['email_id']}] No payment record found for "
                f"invoice={matched_invoice.invoice_number} — skipping ownership check"
            )

    # ── Payment records — for supporting docs only, NOT for customer_id ───────
    matched_payment_ids: List[int] = []
    if matched_invoice:
        payments = await pay_repo.get_all_by_invoice_number(matched_invoice.invoice_number)
        matched_payment_ids = [p.payment_detail_id for p in payments]
        logger.info(
            f"[email_id={state['email_id']}] Matched invoice={matched_invoice.invoice_number}, "
            f"payments={matched_payment_ids}, customer_id={customer_id}"
        )
    else:
        logger.info(
            f"[email_id={state['email_id']}] No invoice matched from "
            f"candidates={state['candidate_invoice_numbers']}"
        )

    langfuse_context.update_current_observation(
        output={
            "matched_invoice_number":  matched_invoice.invoice_number if matched_invoice else None,
            "confidence":              confidence,
            "payment_count":           len(matched_payment_ids),
            "customer_id":             customer_id,
            "ownership_unverified":    ownership_unverified,
        }
    )

    return {
        **state,
        "matched_invoice_id":      matched_invoice.invoice_id if matched_invoice else None,
        "matched_invoice_number":  matched_invoice.invoice_number if matched_invoice else None,
        "matched_payment_ids":     matched_payment_ids,
        "customer_id":             customer_id,
        "routing_confidence":      confidence,
        "_ownership_unverified":   ownership_unverified,
    }
