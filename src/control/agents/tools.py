"""
src/control/agents/tools.py
===========================
Industry-standard typed tool interfaces for AR dispute resolution.

Tools are executed after verification to take actual actions:
- CheckInvoiceDetails: Verify invoice data
- CompareWithContract: Check contract rates
- ResolveDiscrepancy: Issue credit/reissue
- RequestClarification: Ask customer for info
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolName(StrEnum):
    CHECK_INVOICE_DETAILS = "CheckInvoiceDetails"
    COMPARE_WITH_CONTRACT = "CompareWithContract"
    COMPARE_WITH_PO = "CompareWithPO"
    RESOLVE_DISCREPANCY = "ResolveDiscrepancy"
    ISSUE_CREDIT_NOTE = "IssueCreditNote"
    REISSUE_INVOICE = "ReissueInvoice"
    REQUEST_CLARIFICATION = "RequestClarification"
    ESCALATE_TO_FA = "EscalateToFA"


class CheckInvoiceDetailsArgs(BaseModel):
    invoice_number: str
    claimed_amount: str | None = None
    claimed_rate: str | None = None
    customer_scope: str


class CompareWithContractArgs(BaseModel):
    invoice_number: str
    contract_number: str | None = None
    customer_scope: str


class CompareWithPOArgs(BaseModel):
    invoice_number: str
    po_number: str | None = None
    customer_scope: str


class ResolveDiscrepancyArgs(BaseModel):
    dispute_id: int
    resolution_type: str  # ISSUE_CREDIT, REISSUE_INVOICE, NO_ACTION
    amount: float | None = None
    reason: str


class IssueCreditNoteArgs(BaseModel):
    dispute_id: int
    invoice_number: str
    amount: float
    reason: str
    credit_note_number: str | None = None


class ReissueInvoiceArgs(BaseModel):
    dispute_id: int
    original_invoice_number: str
    corrections: dict[str, Any]  # field -> new_value


class RequestClarificationArgs(BaseModel):
    dispute_id: int
    clarification_needed: str
    customer_email: str
    priority: str = "MEDIUM"


class EscalateToFAArgs(BaseModel):
    dispute_id: int
    reason: str
    priority: str = "MEDIUM"
    assign_to_user_id: int | None = None


class ToolReceipt(BaseModel):
    """Receipt returned after tool execution - industry standard"""

    tool: str
    ok: bool
    ref: str
    message: str = ""
    data: dict[str, Any] | None = None
    executed_at: str | None = None
    idempotency_key: str | None = None


class ToolProposal(BaseModel):
    """A proposed tool call from the LLM"""

    name: ToolName
    args: dict[str, Any]
    preconditions: str
    idempotency_key: str = Field(default_factory=lambda: f"idem-{uuid.uuid4()}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "args": self.args,
            "preconditions": self.preconditions,
            "idempotency_key": self.idempotency_key,
        }


# Valid claim types for policy enforcement
class ClaimType(StrEnum):
    PRICING = "policy:invoice:pricing"
    DISCREPANCY = "policy:invoice:discrepancy"
    PAYMENT = "policy:invoice:payment"
    TIMELINE = "policy:invoice:timeline"
    CONTRACT = "policy:contract:terms"


# Policy claims that can be cited
POLICY_CLAIMS = {
    "policy:invoice:pricing": {
        "text": "Invoices must reflect contracted rates. "
        "Price deviations require FA approval.",
        "effective_date": "2025-01-01",
    },
    "policy:invoice:discrepancy": {
        "text": "Invoice discrepancies must be resolved within 10 business days.",
        "effective_date": "2025-01-01",
    },
    "policy:invoice:payment": {
        "text": "Payment records must be verified against bank statements.",
        "effective_date": "2025-01-01",
    },
    "policy:invoice:timeline": {
        "text": "Customer disputes must be acknowledged within 24 hours.",
        "effective_date": "2025-01-01",
    },
    "policy:contract:terms": {
        "text": "Contract terms take precedence over verbal agreements.",
        "effective_date": "2025-01-01",
    },
}


__all__ = [
    "ToolName",
    "CheckInvoiceDetailsArgs",
    "CompareWithContractArgs",
    "CompareWithPOArgs",
    "ResolveDiscrepancyArgs",
    "IssueCreditNoteArgs",
    "ReissueInvoiceArgs",
    "RequestClarificationArgs",
    "EscalateToFAArgs",
    "ToolReceipt",
    "ToolProposal",
    "ClaimType",
    "POLICY_CLAIMS",
]
