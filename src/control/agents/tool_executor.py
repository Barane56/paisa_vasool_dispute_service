"""
src/control/agents/tool_executor.py
====================================
Industry-standard tool execution with verification and receipts.

Flow:
  1. LLM proposes tools with preconditions
  2. Verify preconditions are met
  3. Execute tool
  4. Get receipt (ok/ref/message/data)
  5. Log execution for audit trail

Integrates with existing services:
  - InvoiceRepository
  - ARDocumentService
  - DisputeService (for escalation, credit notes)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from src.control.agents.tools import (
    CheckInvoiceDetailsArgs,
    CompareWithContractArgs,
    CompareWithPOArgs,
    EscalateToFAArgs,
    RequestClarificationArgs,
    ResolveDiscrepancyArgs,
    ToolName,
    ToolProposal,
    ToolReceipt,
)

logger = logging.getLogger(__name__)

ALLOWED_TOOLS = {
    ToolName.CHECK_INVOICE_DETAILS,
    ToolName.COMPARE_WITH_CONTRACT,
    ToolName.COMPARE_WITH_PO,
    ToolName.RESOLVE_DISCREPANCY,
    ToolName.ISSUE_CREDIT_NOTE,
    ToolName.REISSUE_INVOICE,
    ToolName.REQUEST_CLARIFICATION,
    ToolName.ESCALATE_TO_FA,
}


def generate_idem_key() -> str:
    return f"idem-{uuid.uuid4()}"


def verify_proposal(proposal: dict[str, Any]) -> str:
    """Verify a tool proposal is valid. Returns error message or empty if valid."""
    required = {"name", "args", "preconditions", "idempotency_key"}
    if not required.issubset(proposal.keys()):
        return f"Missing required fields: {required - proposal.keys()}"

    try:
        tool_name = ToolName(proposal["name"])
    except ValueError:
        return f"Unknown tool: {proposal['name']}"

    if tool_name not in ALLOWED_TOOLS:
        return f"Tool not allowed: {tool_name}"

    return ""


async def execute_check_invoice_details(
    args: CheckInvoiceDetailsArgs,
    db_session,
) -> ToolReceipt:
    """Check invoice details against our records."""
    try:
        from src.data.repositories.repositories import InvoiceRepository

        inv_repo = InvoiceRepository(db_session)
        invoice = await inv_repo.get_by_invoice_number(args.invoice_number)

        if not invoice:
            return ToolReceipt(
                tool="CheckInvoiceDetails",
                ok=False,
                ref="invoice-not-found",
                message=f"Invoice {args.invoice_number} not found",
            )

        invoice_data = dict(invoice.invoice_details or {})

        # Compare claimed amount if provided
        if args.claimed_amount:
            inv_amount = invoice_data.get("total_amount") or invoice_data.get(
                "total", 0
            )
            try:
                claimed = float(
                    str(args.claimed_amount).replace(",", "").replace("$", "")
                )
                if abs(float(inv_amount) - claimed) > 0.01:
                    return ToolReceipt(
                        tool="CheckInvoiceDetails",
                        ok=True,
                        ref="amount-mismatch",
                        message=(
                            f"Amount mismatch: invoice={inv_amount}, claimed={claimed}"
                        ),
                        data={"invoice_amount": inv_amount, "claimed_amount": claimed},
                    )
            except Exception:
                pass

        return ToolReceipt(
            tool="CheckInvoiceDetails",
            ok=True,
            ref=f"invoice-{args.invoice_number}",
            message="Invoice details verified",
            data=invoice_data,
        )

    except Exception as e:
        return ToolReceipt(
            tool="CheckInvoiceDetails",
            ok=False,
            ref="error",
            message=str(e),
        )


async def execute_compare_with_contract(
    args: CompareWithContractArgs,
    db_session,
) -> ToolReceipt:
    """Compare invoice against contract rates."""
    try:
        from sqlalchemy import and_, select

        from src.data.models.postgres.ar_document_models import (
            ARDocument,
            ARDocumentKey,
        )

        # Find contract for this customer/invoice
        norm_inv = args.invoice_number.upper().strip()

        doc_query = (
            select(ARDocument)
            .join(ARDocumentKey, ARDocumentKey.doc_id == ARDocument.doc_id)
            .where(
                and_(
                    ARDocumentKey.key_type == "inv_number",
                    ARDocumentKey.key_value_norm == norm_inv,
                    ARDocument.customer_scope == args.customer_scope,
                    ARDocument.doc_type.in_(["CONTRACT", "MSA", "AGREEMENT"]),
                )
            )
        )
        contracts = (await db_session.execute(doc_query)).scalars().all()

        if not contracts:
            return ToolReceipt(
                tool="CompareWithContract",
                ok=False,
                ref="no-contract-found",
                message="No contract document found for this invoice",
            )

        return ToolReceipt(
            tool="CompareWithContract",
            ok=True,
            ref=f"contracts-{len(contracts)}",
            message=f"Found {len(contracts)} contract document(s)",
            data={"contract_ids": [c.doc_id for c in contracts]},
        )

    except Exception as e:
        return ToolReceipt(
            tool="CompareWithContract",
            ok=False,
            ref="error",
            message=str(e),
        )


async def execute_compare_with_po(
    args: CompareWithPOArgs,
    db_session,
) -> ToolReceipt:
    """Compare invoice against PO."""
    try:
        from sqlalchemy import and_, select

        from src.data.models.postgres.ar_document_models import (
            ARDocument,
            ARDocumentKey,
        )

        # Find PO for this invoice
        norm_inv = args.invoice_number.upper().strip()

        doc_query = (
            select(ARDocument)
            .join(ARDocumentKey, ARDocumentKey.doc_id == ARDocument.doc_id)
            .where(
                and_(
                    ARDocumentKey.key_type == "inv_number",
                    ARDocumentKey.key_value_norm == norm_inv,
                    ARDocument.customer_scope == args.customer_scope,
                    ARDocument.doc_type == "PO",
                )
            )
        )
        pos = (await db_session.execute(doc_query)).scalars().all()

        if not pos:
            return ToolReceipt(
                tool="CompareWithPO",
                ok=False,
                ref="no-po-found",
                message="No PO document found for this invoice",
            )

        return ToolReceipt(
            tool="CompareWithPO",
            ok=True,
            ref=f"pos-{len(pos)}",
            message=f"Found {len(pos)} PO document(s)",
            data={"po_ids": [p.doc_id for p in pos]},
        )

    except Exception as e:
        return ToolReceipt(
            tool="CompareWithPO",
            ok=False,
            ref="error",
            message=str(e),
        )


async def execute_resolve_discrepancy(
    args: ResolveDiscrepancyArgs,
    db_session,
) -> ToolReceipt:
    """Log resolution action (credit, reissue, etc) - FA must approve."""
    try:
        # Log the resolution intent - FA will handle actual execution
        logger.info(
            f"[dispute_id={args.dispute_id}] Resolution requested: "
            f"{args.resolution_type} amount={args.amount}"
        )

        return ToolReceipt(
            tool="ResolveDiscrepancy",
            ok=True,
            ref=f"resolution-{args.dispute_id}",
            message=f"Resolution {args.resolution_type} queued for FA approval",
            data={
                "dispute_id": args.dispute_id,
                "resolution_type": args.resolution_type,
                "amount": args.amount,
                "reason": args.reason,
            },
        )

    except Exception as e:
        return ToolReceipt(
            tool="ResolveDiscrepancy",
            ok=False,
            ref="error",
            message=str(e),
        )


async def execute_request_clarification(
    args: RequestClarificationArgs,
    db_session,
) -> ToolReceipt:
    """Request clarification from customer."""
    try:
        # This would typically update the dispute with a pending question
        logger.info(
            f"[dispute_id={args.dispute_id}] Clarification requested: "
            f"{args.clarification_needed}"
        )

        return ToolReceipt(
            tool="RequestClarification",
            ok=True,
            ref=f"clarify-{args.dispute_id}",
            message=f"Clarification requested: {args.clarification_needed}",
            data={
                "dispute_id": args.dispute_id,
                "clarification_needed": args.clarification_needed,
                "customer_email": args.customer_email,
                "priority": args.priority,
            },
        )

    except Exception as e:
        return ToolReceipt(
            tool="RequestClarification",
            ok=False,
            ref="error",
            message=str(e),
        )


async def execute_escalate_to_fa(
    args: EscalateToFAArgs,
    db_session,
) -> ToolReceipt:
    """Escalate dispute to FA for manual review."""
    try:
        # This would trigger assignment and notification
        logger.info(
            f"[dispute_id={args.dispute_id}] Escalating to FA: "
            f"reason={args.reason}, priority={args.priority}"
        )

        return ToolReceipt(
            tool="EscalateToFA",
            ok=True,
            ref=f"escalate-{args.dispute_id}",
            message=f"Escalated to FA: {args.reason}",
            data={
                "dispute_id": args.dispute_id,
                "reason": args.reason,
                "priority": args.priority,
                "assign_to_user_id": args.assign_to_user_id,
            },
        )

    except Exception as e:
        return ToolReceipt(
            tool="EscalateToFA",
            ok=False,
            ref="error",
            message=str(e),
        )


async def execute_tool(
    tool_name: str,
    args: dict[str, Any],
    db_session,
) -> ToolReceipt:
    """Execute a single tool and return receipt."""
    try:
        if tool_name == ToolName.CHECK_INVOICE_DETAILS.value:
            return await execute_check_invoice_details(
                CheckInvoiceDetailsArgs(**args), db_session
            )
        elif tool_name == ToolName.COMPARE_WITH_CONTRACT.value:
            return await execute_compare_with_contract(
                CompareWithContractArgs(**args), db_session
            )
        elif tool_name == ToolName.COMPARE_WITH_PO.value:
            return await execute_compare_with_po(CompareWithPOArgs(**args), db_session)
        elif tool_name == ToolName.RESOLVE_DISCREPANCY.value:
            return await execute_resolve_discrepancy(
                ResolveDiscrepancyArgs(**args), db_session
            )
        elif tool_name == ToolName.REQUEST_CLARIFICATION.value:
            return await execute_request_clarification(
                RequestClarificationArgs(**args), db_session
            )
        elif tool_name == ToolName.ESCALATE_TO_FA.value:
            return await execute_escalate_to_fa(EscalateToFAArgs(**args), db_session)
        else:
            return ToolReceipt(
                tool=tool_name,
                ok=False,
                ref="unknown-tool",
                message=f"Tool {tool_name} not implemented",
            )
    except Exception as e:
        logger.error(f"Tool execution failed: {tool_name} - {e}")
        return ToolReceipt(
            tool=tool_name,
            ok=False,
            ref="execution-error",
            message=str(e),
        )


async def execute_tool_proposals(
    proposals: list[dict[str, Any]],
    db_session,
) -> list[ToolReceipt]:
    """
    Execute multiple tool proposals with verification.
    Returns list of receipts in same order as proposals.
    """
    receipts: list[ToolReceipt] = []

    for prop in proposals:
        # Verify proposal is valid
        error = verify_proposal(prop)
        if error:
            receipts.append(
                ToolReceipt(
                    tool=prop.get("name", "unknown"),
                    ok=False,
                    ref="validation-failed",
                    message=error,
                )
            )
            continue

        # Execute tool
        receipt = await execute_tool(
            tool_name=prop["name"],
            args=prop.get("args", {}),
            db_session=db_session,
        )
        receipt.idempotency_key = prop.get("idempotency_key")
        receipt.executed_at = datetime.now(UTC).isoformat()
        receipts.append(receipt)

        # Stop on failure for critical tools
        if not receipt.ok and prop["name"] in {
            ToolName.RESOLVE_DISCREPANCY.value,
            ToolName.ISSUE_CREDIT_NOTE.value,
        }:
            logger.warning(f"Critical tool {prop['name']} failed, stopping execution")
            break

    return receipts


def render_tool_results(
    proposals: list[dict[str, Any]],
    receipts: list[ToolReceipt],
) -> str:
    """Render tool execution results as a summary string."""
    lines = []

    for prop, receipt in zip(proposals, receipts, strict=True):
        tool_name = prop.get("name", "unknown")
        status = "✓" if receipt.ok else "✗"

        lines.append(f"{status} {tool_name}: {receipt.message}")

        if receipt.data:
            lines.append(f"   Data: {json.dumps(receipt.data)[:200]}")

    return "\n".join(lines)


__all__ = [
    "execute_tool",
    "execute_tool_proposals",
    "verify_proposal",
    "render_tool_results",
    "ToolReceipt",
    "ToolProposal",
    "ALLOWED_TOOLS",
]
