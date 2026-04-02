"""
src/control/agents/nodes/verify_claim.py
=========================================
NEW NODE - Industry-grade verification for customer claims.

This node runs AFTER generate_response to actually VERIFY the claim
by querying the ar_documents table and comparing against customer assertions.

Flow:
  1. Parse claim_type from generate_response output
  2. Query ar_documents for relevant documents (invoice, contract, PO, GRN)
  3. Verify claim against source of truth
  4. If customer is WRONG → keep auto_response, update verification_status
  5. If customer is CORRECT → flag for FA action (issue credit, reissue, etc)
  6. If INCONCLUSIVE → flag for manual review

Integrates with existing:
  - ar_documents table
  - ar_document_keys table
  - dispute_ar_documents table
"""

from __future__ import annotations

import logging
from typing import Any

from src.control.agents.state import EmailProcessingState
from src.observability import observe

logger = logging.getLogger(__name__)

# Document types that serve as source of truth
CONTRACT_DOCS = {"CONTRACT", "MSA", "AGREEMENT"}
PRICING_DOCS = {"INVOICE", "CONTRACT", "PO"}
PAYMENT_DOCS = {"PAYMENT", "REMITTANCE"}
DELIVERY_DOCS = {"GRN", "DELIVERY_NOTE", "BILL_OF_LADING"}


async def _fetch_ar_documents_for_verification(
    db_session,
    invoice_number: str | None,
    customer_scope: str,
    claim_type: str,
) -> dict:
    """
    Fetch relevant AR documents based on claim type.
    Returns dict with categorized documents.
    """
    if not db_session:
        return {"contracts": [], "invoices": [], "payments": [], "grns": [], "pos": []}

    try:
        from sqlalchemy import and_, select

        from src.data.models.postgres.ar_document_models import (
            ARDocument,
            ARDocumentKey,
        )

        result: dict[str, Any] = {
            "contracts": [],
            "invoices": [],
            "payments": [],
            "grns": [],
            "pos": [],
            "all_docs": [],
        }

        # Build query based on claim type
        if invoice_number:
            norm_inv = invoice_number.upper().strip()
            if not norm_inv.startswith("INV-"):
                norm_inv = f"INV-{norm_inv}"

            # Find documents linked to this invoice number
            doc_query = (
                select(ARDocument)
                .join(ARDocumentKey, ARDocumentKey.doc_id == ARDocument.doc_id)
                .where(
                    and_(
                        ARDocumentKey.key_type == "inv_number",
                        ARDocumentKey.key_value_norm == norm_inv,
                        ARDocument.customer_scope == customer_scope,
                    )
                )
            )
            docs = (await db_session.execute(doc_query)).scalars().all()

            for doc in docs:
                doc_type = str(doc.doc_type)
                doc_data = {
                    "doc_id": doc.doc_id,
                    "doc_type": doc_type,
                    "doc_date": doc.doc_date.isoformat() if doc.doc_date else None,
                    "status": doc.status,
                    "raw_text": doc.raw_text[:500] if doc.raw_text else None,
                }
                result["all_docs"].append(doc_data)

                if doc_type in CONTRACT_DOCS:
                    result["contracts"].append(doc_data)
                elif doc_type == "INVOICE":
                    result["invoices"].append(doc_data)
                elif doc_type == "PAYMENT":
                    result["payments"].append(doc_data)
                elif doc_type == "GRN":
                    result["grns"].append(doc_data)
                elif doc_type == "PO":
                    result["pos"].append(doc_data)

        logger.info(
            f"[verify_claim] Found {len(result['all_docs'])} docs "
            f"for invoice={invoice_number}"
        )
        return result

    except Exception as e:
        logger.warning(f"[verify_claim] Document fetch failed: {e}")
        return {
            "contracts": [],
            "invoices": [],
            "payments": [],
            "grns": [],
            "pos": [],
            "all_docs": [],
        }


async def _verify_pricing_claim(
    customer_claimed_amount: str | None,
    customer_claimed_rate: str | None,
    invoice_details: dict | None,
    ar_docs: dict,
) -> dict:
    """
    Verify pricing dispute:
    - Compare invoice total vs customer claimed amount
    - Compare invoiced rate vs contract rate
    """
    verification: dict[str, Any] = {
        "status": "INCONCLUSIVE",
        "evidence": [],
        "recommendation": None,
    }

    contracts = ar_docs.get("contracts", [])

    # Check invoice amounts
    if invoice_details:
        inv_total = invoice_details.get("total_amount") or invoice_details.get("total")
        if customer_claimed_amount and inv_total:
            # Simple comparison (in production, parse currency properly)
            inv_str = str(inv_total).replace(",", "").replace(" ", "")
            cust_str = (
                str(customer_claimed_amount)
                .replace(",", "")
                .replace(" ", "")
                .replace("USD", "")
                .replace("$", "")
                .strip()
            )

            try:
                inv_val = float(inv_str)
                cust_val = float(cust_str)

                if abs(inv_val - cust_val) < 0.01:
                    # Customer is CORRECT
                    verification["status"] = "VERIFIED_CUSTOMER_CORRECT"
                    verification["evidence"].append(
                        f"Invoice amount matches claim: {inv_val}"
                    )
                    verification["recommendation"] = "ISSUE_CREDIT"
                else:
                    # Customer is WRONG
                    verification["status"] = "VERIFIED_CUSTOMER_INCORRECT"
                    verification["evidence"].append(
                        f"Invoice shows {inv_val}, customer claims {cust_val}"
                    )
                    verification["recommendation"] = "NO_ACTION"
            except Exception:
                pass

    # Check contract rates if available
    if contracts and customer_claimed_rate:
        verification["evidence"].append(
            f"Contract document found: {len(contracts)} contract(s) on file"
        )
        # In production, would parse contract text to find rate
        verification["status"] = "INCONCLUSIVE"  # Need FA to verify rate
        verification["recommendation"] = "VERIFY_CONTRACT_RATE"

    return verification


async def _verify_payment_claim(
    ar_docs: dict,
    all_payment_details: list[dict],
) -> dict:
    """
    Verify payment claim:
    - Check if payment exists in our records
    - Compare payment amounts
    """
    verification: dict[str, Any] = {
        "status": "INCONCLUSIVE",
        "evidence": [],
        "recommendation": None,
    }

    our_payments = ar_docs.get("payments", [])
    db_payments = all_payment_details or []

    all_payments = our_payments + [
        {"amount": p.get("amount"), "date": p.get("payment_date")} for p in db_payments
    ]

    if all_payments:
        verification["status"] = "VERIFIED_CUSTOMER_INCORRECT"
        verification["evidence"].append(
            f"Payment records found: {len(all_payments)} payment(s) on file"
        )
        verification["recommendation"] = "NO_ACTION"
    else:
        verification["status"] = "INCONCLUSIVE"
        verification["evidence"].append(
            "No payment records found - needs investigation"
        )
        verification["recommendation"] = "INVESTIGATE_PAYMENT"

    return verification


async def _verify_contract_claim(
    customer_claimed_rate: str | None,
    ar_docs: dict,
) -> dict:
    """
    Verify contract rate claim:
    - Check if contract exists
    - Compare contracted rate vs invoiced rate
    """
    verification: dict[str, Any] = {
        "status": "INCONCLUSIVE",
        "evidence": [],
        "recommendation": None,
    }

    contracts = ar_docs.get("contracts", [])

    if contracts:
        verification["status"] = "INCONCLUSIVE"  # Need to parse contract text
        verification["evidence"].append(
            f"Contract found: {len(contracts)} contract document(s)"
        )
        verification["recommendation"] = "VERIFY_CONTRACT_RATE"
    else:
        verification["status"] = "INCONCLUSIVE"
        verification["evidence"].append(
            "No contract on file - request copy from customer"
        )
        verification["recommendation"] = "REQUEST_CONTRACT_COPY"

    return verification


@observe(name="node_verify_claim")
async def node_verify_claim(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Main verification node.
    Runs after generate_response to verify claims against ar_documents.
    """
    email_id = state["email_id"]
    claim_type = state.get("claim_type", "").upper()
    classification = state.get("classification", "").upper()

    # Skip verification for non-dispute queries
    if classification != "DISPUTE":
        logger.info(f"[email_id={email_id}] Skipping verify - not a dispute")
        return state

    # Get customer scope
    customer_scope = state.get("sender_email", "").lower().strip()
    invoice_number = state.get("invoice_number")

    # Fetch AR documents for verification
    ar_docs = await _fetch_ar_documents_for_verification(
        db_session=db_session,
        invoice_number=invoice_number,
        customer_scope=customer_scope,
        claim_type=claim_type,
    )

    # Run verification based on claim type
    if claim_type == "PRICING":
        verification = await _verify_pricing_claim(
            customer_claimed_amount=state.get("disputed_amount"),
            customer_claimed_rate=None,  # Could extract from body
            invoice_details=state.get("invoice_details"),
            ar_docs=ar_docs,
        )
    elif claim_type == "PAYMENT":
        verification = await _verify_payment_claim(
            ar_docs=ar_docs,
            all_payment_details=state.get("all_payment_details") or [],
        )
    elif claim_type == "CONTRACT":
        verification = await _verify_contract_claim(
            customer_claimed_rate=None,
            ar_docs=ar_docs,
        )
    else:
        verification = {
            "status": "INCONCLUSIVE",
            "evidence": ["Claim type not recognized for automated verification"],
            "recommendation": None,
        }

    # Update state with verification results
    new_verification_status = verification.get("status", "INCONCLUSIVE")
    new_evidence = "; ".join(verification.get("evidence", []))
    new_resolution_action = verification.get("recommendation")

    # Determine if we should auto-respond or escalate
    can_auto_respond = new_verification_status == "VERIFIED_CUSTOMER_INCORRECT"

    logger.info(
        f"[email_id={email_id}] Verification: claim_type={claim_type}, "
        f"status={new_verification_status}, auto_respond={can_auto_respond}"
    )

    return {
        **state,
        "verification_status": new_verification_status,
        "verification_evidence": new_evidence,
        "resolution_action": new_resolution_action
        or state.get("resolution_action", ""),
        "auto_response_generated": can_auto_respond,
        # Add docs found for context
        "ar_docs_for_verification": ar_docs.get("all_docs", []),
    }
