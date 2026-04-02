"""
src/control/agents/nodes/enhanced_verify.py
=============================================
ENHANCED verification with:
1. Contract rate extraction using LLM
2. PO-GRN matching for delivery claims
3. Credit note detection for duplicates
4. Enhanced FA action suggestions
5. Confidence threshold override
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.control.agents.state import EmailProcessingState
from src.control.prompts.extract_contract_rates import (
    build_extract_contract_rates_prompt,
)
from src.observability import observe

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.7  # Below this, always escalate to FA

# FA Action suggestions with reasoning
FA_ACTION_SUGGESTIONS = {
    "ISSUE_CREDIT": {
        "action": "ISSUE_CREDIT",
        "suggestion": "Customer overcharged by ₹{amount}. "
        "Recommend issuing credit note for amount ₹{amount}.",
        "reasoning": "Invoice amount exceeds contracted rate. Evidence: {evidence}",
    },
    "VERIFY_CONTRACT_RATE": {
        "action": "VERIFY_CONTRACT_RATE",
        "suggestion": "Contract rate needs verification. "
        "FA to compare contract clause with invoice line items.",
        "reasoning": "Contract found but rate ambiguity. "
        "Need FA to manually compare contract clause X with invoice line Y.",
    },
    "REQUEST_CONTRACT_COPY": {
        "action": "REQUEST_CONTRACT_COPY",
        "suggestion": "No contract on file. "
        "Request copy from customer referencing their claimed agreement.",
        "reasoning": (
            "Customer references contract but no matching document in our records."
        ),
    },
    "INVESTIGATE_PAYMENT": {
        "action": "INVESTIGATE_PAYMENT",
        "suggestion": "Payment status unclear. "
        "FA to check bank statements for payment reference {ref}.",
        "reasoning": "Cannot verify payment. Need FA to check bank statements.",
    },
    "REISSUE_INVOICE": {
        "action": "REISSUE_INVOICE",
        "suggestion": "Invoice has error in field '{field}'. "
        "Recommend reissuing with correction.",
        "reasoning": "Invoice contains incorrect data that needs correction.",
    },
    "CHECK_CREDIT_NOTE": {
        "action": "CHECK_CREDIT_NOTE",
        "suggestion": "Previous credit note may exist. "
        "FA to check if CN-{cn_number} already issued.",
        "reasoning": "Credit note lookup needed to prevent duplicate credits.",
    },
    "COMPARE_PO_GRN": {
        "action": "COMPARE_PO_GRN",
        "suggestion": "Delivery quantity dispute. "
        "FA to compare PO qty {po_qty} vs GRN qty {grn_qty}.",
        "reasoning": "Customer claims delivery issue. Need PO-GRN reconciliation.",
    },
    "NO_ACTION": {
        "action": "NO_ACTION",
        "suggestion": "Our records are correct. "
        "No action needed - respond to customer with evidence.",
        "reasoning": "Verification complete: our records match the invoice.",
    },
}


async def _extract_contract_rates(
    llm_client,
    contract_text: str,
    invoice_number: str,
    invoice_amount: str | None = None,
    line_items: str | None = None,
) -> dict:
    """Use LLM to extract rates from contract document."""
    if not llm_client or not contract_text:
        return {
            "contract_rate": None,
            "comparison_result": "NEED_REVIEW",
            "confidence": 0.0,
        }

    try:
        prompt = build_extract_contract_rates_prompt(
            contract_text=contract_text,
            invoice_number=invoice_number,
            invoice_amount=invoice_amount,
            line_items=line_items,
        )
        response = await llm_client.chat_reasoning(prompt)
        data = json.loads(response)
        return {
            "contract_rate": data.get("contract_rate"),
            "contract_rate_unit": data.get("contract_rate_unit"),
            "comparison_result": data.get("comparison_result", "NEED_REVIEW"),
            "comparison_details": data.get("comparison_details", ""),
            "confidence": data.get("confidence", 0.5),
        }
    except Exception as e:
        logger.warning(f"Contract rate extraction failed: {e}")
        return {
            "contract_rate": None,
            "comparison_result": "NEED_REVIEW",
            "confidence": 0.0,
        }


async def _check_credit_notes(
    db_session,
    invoice_number: str,
    customer_scope: str,
) -> dict:
    """Check if credit note already issued for this invoice."""
    if not db_session:
        return {"has_credit_note": False, "credit_note_details": None}

    try:
        from sqlalchemy import and_, select

        from src.data.models.postgres.ar_document_models import (
            ARDocument,
            ARDocumentKey,
        )

        query = (
            select(ARDocument)
            .join(ARDocumentKey, ARDocumentKey.doc_id == ARDocument.doc_id)
            .where(
                and_(
                    ARDocumentKey.key_type == "inv_number",
                    ARDocumentKey.key_value_norm == invoice_number.upper(),
                    ARDocument.customer_scope == customer_scope,
                    ARDocument.doc_type == "CREDIT_NOTE",
                    ARDocument.status == "ACTIVE",
                )
            )
        )
        credit_notes = (await db_session.execute(query)).scalars().all()

        if credit_notes:
            return {
                "has_credit_note": True,
                "credit_note_details": [
                    {"doc_id": cn.doc_id, "doc_date": cn.doc_date}
                    for cn in credit_notes
                ],
            }
    except Exception as e:
        logger.warning(f"Credit note lookup failed: {e}")

    return {"has_credit_note": False, "credit_note_details": None}


async def _match_po_grn(
    db_session,
    invoice_number: str,
    customer_scope: str,
) -> dict:
    """Match invoice to PO and GRN for delivery verification."""
    if not db_session:
        return {"po_found": False, "grn_found": False, "match_status": "UNKNOWN"}

    try:
        from sqlalchemy import and_, select

        from src.data.models.postgres.ar_document_models import (
            ARDocument,
            ARDocumentKey,
        )

        # Find PO linked to this invoice
        po_query = (
            select(ARDocument)
            .join(ARDocumentKey, ARDocumentKey.doc_id == ARDocument.doc_id)
            .where(
                and_(
                    ARDocumentKey.key_type == "inv_number",
                    ARDocumentKey.key_value_norm == invoice_number.upper(),
                    ARDocument.customer_scope == customer_scope,
                    ARDocument.doc_type == "PO",
                )
            )
        )
        pos = (await db_session.execute(po_query)).scalars().all()

        # Find GRN linked to this invoice
        grn_query = (
            select(ARDocument)
            .join(ARDocumentKey, ARDocumentKey.doc_id == ARDocument.doc_id)
            .where(
                and_(
                    ARDocumentKey.key_type == "inv_number",
                    ARDocumentKey.key_value_norm == invoice_number.upper(),
                    ARDocument.customer_scope == customer_scope,
                    ARDocument.doc_type == "GRN",
                )
            )
        )
        grns = (await db_session.execute(grn_query)).scalars().all()

        if pos and grns:
            return {
                "po_found": True,
                "po_count": len(pos),
                "grn_found": True,
                "grn_count": len(grns),
                "match_status": "MATCHED",
            }
        elif pos:
            return {
                "po_found": True,
                "po_count": len(pos),
                "grn_found": False,
                "match_status": "PO_ONLY",
            }
        elif grns:
            return {
                "po_found": False,
                "grn_found": True,
                "grn_count": len(grns),
                "match_status": "GRN_ONLY",
            }
        else:
            return {"po_found": False, "grn_found": False, "match_status": "NOT_FOUND"}

    except Exception as e:
        logger.warning(f"PO-GRN matching failed: {e}")
        return {"po_found": False, "grn_found": False, "match_status": "ERROR"}


def _build_fa_suggestion(
    resolution_action: str,
    context: dict,
) -> dict:
    """Build enhanced FA suggestion with reasoning."""
    suggestion_template = FA_ACTION_SUGGESTIONS.get(
        resolution_action, FA_ACTION_SUGGESTIONS["NO_ACTION"]
    )

    suggestion = suggestion_template["suggestion"].format(**context)
    reasoning = suggestion_template["reasoning"].format(**context)

    return {
        "action": resolution_action,
        "suggestion": suggestion,
        "reasoning": reasoning,
    }


@observe(name="node_enhanced_verify")
async def node_enhanced_verify(
    state: EmailProcessingState,
    db_session=None,
    llm_client=None,
) -> EmailProcessingState:
    """
    Enhanced verification with contract extraction, PO-GRN matching,
    and credit note detection.
    """
    email_id = state["email_id"]
    classification = state.get("classification", "").upper()
    confidence_score = state.get("confidence_score", 0.0)

    # Skip for non-disputes
    if classification != "DISPUTE":
        return state

    customer_scope = state.get("sender_email", "").lower().strip()
    invoice_number = state.get("invoice_number")

    # Get contract text from ar_documents
    ar_docs = state.get("ar_docs_for_verification", [])
    contract_text = None
    for doc in ar_docs:
        if doc.get("doc_type") in ("CONTRACT", "MSA", "AGREEMENT"):
            contract_text = doc.get("raw_text")
            break

    # Get invoice details
    invoice_details = state.get("invoice_details")
    invoice_amount = invoice_details.get("total_amount") if invoice_details else None
    line_items = invoice_details.get("line_items") if invoice_details else None

    # Run enhanced verifications
    results: dict[str, Any] = {
        "contract_extraction": None,
        "credit_note_check": None,
        "po_grn_match": None,
    }

    # 1. Extract contract rates
    if contract_text:
        results["contract_extraction"] = await _extract_contract_rates(
            llm_client=llm_client,
            contract_text=contract_text,
            invoice_number=invoice_number or "UNKNOWN",
            invoice_amount=str(invoice_amount) if invoice_amount else None,
            line_items=json.dumps(line_items) if line_items else None,
        )

    # 2. Check credit notes
    if invoice_number:
        results["credit_note_check"] = await _check_credit_notes(
            db_session=db_session,
            invoice_number=invoice_number,
            customer_scope=customer_scope,
        )
        results["po_grn_match"] = await _match_po_grn(
            db_session=db_session,
            invoice_number=invoice_number,
            customer_scope=customer_scope,
        )

    # Determine final status based on all verifications
    resolution_action = state.get("resolution_action", "")

    # Override with confidence threshold
    should_escalate = confidence_score < CONFIDENCE_THRESHOLD

    # Build enhanced FA suggestion
    fa_suggestion = _build_fa_suggestion(
        resolution_action or "NO_ACTION",
        {
            "amount": state.get("disputed_amount", "X"),
            "evidence": state.get("verification_evidence", ""),
            "ref": invoice_number or "UNKNOWN",
            "field": "rate/qty",
            "cn_number": "TODO",
            "po_qty": "TODO",
            "grn_qty": "TODO",
        },
    )

    # Update state with enhanced results
    logger.info(
        f"[email_id={email_id}] Enhanced verify: "
        f"contract_extract={results['contract_extraction'] is not None}, "
        f"credit_note={results['credit_note_check']}, "
        f"po_grn={results['po_grn_match']}, "
        f"escalate={should_escalate}"
    )

    return {
        **state,
        "contract_extraction": results["contract_extraction"],
        "credit_note_check": results["credit_note_check"],
        "po_grn_match": results["po_grn_match"],
        "fa_suggestion": fa_suggestion,
        "should_escalate": should_escalate,
        # Override auto-response based on confidence threshold
        "auto_response_generated": not should_escalate
        and state.get("auto_response_generated", False),
    }
