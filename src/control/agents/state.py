"""
src/control/agents/state.py
============================
Single source of truth for EmailProcessingState.
Imported by every node and by the graph builder.
"""

from __future__ import annotations

from typing import Any, TypedDict


class EmailProcessingState(TypedDict):
    # ── Input ─────────────────────────────────────────────────────────────────
    email_id: int
    sender_email: str
    subject: str
    body_text: str
    attachment_texts: list[str]
    # Rich attachment metadata for multi-file prompt context
    # Each item: {file_name, file_type, extracted_text}
    attachment_metadata: list[dict[str, Any]]

    # ── Text ──────────────────────────────────────────────────────────────────
    all_text: str

    # ── Groq-extracted invoice fields ─────────────────────────────────────────
    groq_extracted: dict[str, Any] | None
    candidate_invoice_numbers: list[str]
    # Non-invoice AR reference numbers extracted from the email.
    # Each entry: {"value": "PO-BK-2025-001", "key_type": "po_number"}
    # Used by fetch_context as a fallback graph-walk trigger when no invoice matched.
    candidate_references: list[dict[str, Any]]

    # ── DB-matched invoice + payments ─────────────────────────────────────────
    matched_invoice_id: int | None
    matched_invoice_number: str | None
    matched_payment_ids: list[int]
    customer_id: str | None
    routing_confidence: float

    # ── Classification (runs BEFORE fetch_context) ────────────────────────────
    # Primary issue fields (used by all downstream nodes as before)
    classification: str
    dispute_type_name: str
    priority: str
    description: str
    invoice_number: str | None  # invoice number for PRIMARY issue
    document_reference: str | None  # non-invoice AR ref for PRIMARY issue (PO/GRN/etc.)
    document_reference_type: str | None  # key_type for document_reference
    disputed_amount: str | None  # amount string for PRIMARY issue
    _answers_pending_questions: list[int]
    _new_dispute_type: dict | None

    # ── Intent taxonomy (set by classify_email, drives pipeline decisions) ────
    # 19 possible values:
    #   Billing:    FACTUAL_QUERY, DISPUTE, DEDUCTION_CLAIM, CREDIT_REQUEST,
    #               PAYMENT_ADVICE, PAYMENT_DELAY_REQUEST, DOCUMENT_REQUEST,
    #               INVOICE_CORRECTION_REQUEST
    #   Escalation: ESCALATION, LEGAL_THREAT
    #   Relation:   SOCIAL, RESOLUTION_ACK, ABUSIVE, IRRELEVANT,
    #               DUPLICATE_CONTACT, MULTI_INTENT
    #   India AR:   GST_QUERY, TDS_DEDUCTION, ADVANCE_PAYMENT
    intent: str  # primary intent of this email
    requires_new_case: bool  # should persist_results create a new case?
    requires_fork: bool  # should detect_context_shift consider forking?
    escalate_immediately: bool  # notify FA immediately regardless of queue
    priority_override: str | None  # force HIGH/MEDIUM/LOW
    suggested_action: str  # CLOSE_CASE | UPDATE_CASE | CREATE_CASE | ACKNOWLEDGE_ONLY

    # (PO number, GRN number, payment ref, contract number) for issues where
    # no invoice number was stated — used for AR graph lookup in generate_response.
    inline_issues: list[dict[str, Any]]

    # ── Context (fetched AFTER classification) ────────────────────────────────
    invoice_details: dict[str, Any] | None
    all_payment_details: list[dict[str, Any]]
    existing_dispute_id: int | None
    memory_summary: str | None
    recent_episodes: list[dict[str, Any]]
    pending_questions: list[dict[str, Any]]
    available_dispute_types: list[dict[str, Any]]

    # ── Embedding search ──────────────────────────────────────────────────────
    similar_episodes: list[dict[str, Any]]
    embedding_matched: bool
    embedding_dispute_id: int | None
    embedding_similarity: float

    # ── Routing flags ─────────────────────────────────────────────────────────
    _needs_invoice_details: bool
    _ownership_unverified: bool
    token_matched_dispute_id: int | None  # Layer 1: DISP-XXXXX token match

    # ── Pre-classify dispute baseline (set by pre_fetch_dispute_context) ──────
    # Populated when a prior dispute is known BEFORE classify_email runs —
    # either via DISP token match or task-level thread-header matching.
    # Gives the classifier a comparison baseline so it can correctly set
    # requires_fork=True when the new email raises a different issue.
    # Shape: {dispute_id, dispute_type, description, status, invoice_number}
    # Contract: always a Dict (never None). Empty dict {} = no prior dispute
    # found or fetch failed. All reads should use .get() for safety.
    existing_dispute_context: dict[str, Any]  # {} means "not populated"

    # ── Related dispute (L2 soft match — same invoice, different issue) ───────
    # Set when L2 Gate A passes but Gate B similarity is below threshold.
    # The email gets a NEW case; this dispute is linked as RELATED for context.
    # Never used for routing — only for LLM context and DisputeRelationship write.
    related_dispute_id: int | None
    related_dispute_token: str | None

    # ── Context-shift / fork detection (follow-up emails) ─────────────────────
    context_shift_detected: bool
    context_shift_confidence: float
    context_shift_reasoning: str | None
    forked_issues: list[dict]
    original_dispute_still_active: bool

    # ── AI output ─────────────────────────────────────────────────────────────
    ai_summary: str
    ai_response: str | None  # primary issue (single-issue compat)
    confidence_score: float
    auto_response_generated: bool
    questions_to_ask: list[str]
    memory_context_used: bool
    episodes_referenced: list[int]

    # ── Verification fields (industry standard) ─────────────────────────────
    claim_type: str  # PRICING, PAYMENT, CONTRACT, DUPLICATE, DELIVERY, OTHER
    verification_status: (
        str  # VERIFIED_CUSTOMER_CORRECT, VERIFIED_CUSTOMER_INCORRECT, INCONCLUSIVE
    )
    verification_evidence: str  # What data was checked
    resolution_action: str  # ISSUE_CREDIT, REISSUE_INVOICE, NO_ACTION,
    # INVESTIGATE_PAYMENT, REQUEST_CONTRACT_COPY
    ar_docs_for_verification: list[dict[str, Any]]  # Docs found during verification

    # ── Tool proposals and receipts (industry standard) ────────────────────
    tool_proposals: list[dict[str, Any]]  # LLM-suggested tool calls
    tool_receipts: list[dict[str, Any]]  # Execution results with receipts
    executed_tool_count: int  # Number of tools executed

    # Policy claims for compliance ──────────────────────────────────────────
    cited_policy_claims: list[str]  # claim_ids used in response

    # Enhanced verification results ─────────────────────────────────────────
    contract_extraction: dict[str, Any] | None  # LLM-extracted contract rates
    credit_note_check: dict[str, Any] | None  # Credit note lookup result
    po_grn_match: dict[str, Any] | None  # PO-GRN matching result
    fa_suggestion: dict[str, Any] | None  # Enhanced FA action suggestion
    should_escalate: bool  # Override based on confidence threshold

    #   issue_index, invoice_number, classification, description,
    #   ai_response, can_auto_respond, ai_summary, confidence_score,
    #   questions_to_ask, dispute_token (placeholder resolved by persist_results)
    per_issue_responses: list[dict[str, Any]]

    # Related issue field added to IssueState in LangGraph repo.
    # related_issue_id: int | None
    ar_document_chain: list[
        dict[str, Any]
    ]  # related AR docs found via graph — injected into LLM

    # ── Final ─────────────────────────────────────────────────────────────────
    dispute_id: int | None  # primary dispute id for this email
    forked_dispute_ids: list[int]  # context-shift forks (follow-up emails)
    inline_dispute_ids: list[int]  # additional disputes from same email (new emails)
    analysis_id: int | None
    error: str | None


# ─── Initial state factory ────────────────────────────────────────────────────


def build_initial_state(
    email_id: int,
    sender_email: str,
    subject: str,
    body_text: str,
    attachment_texts: list[str],
    attachment_metadata: list[dict] | None = None,
    existing_dispute_id: int | None = None,
) -> EmailProcessingState:
    return {
        "email_id": email_id,
        "sender_email": sender_email,
        "subject": subject,
        "body_text": body_text,
        "attachment_texts": attachment_texts,
        "attachment_metadata": attachment_metadata or [],
        "all_text": "",
        "groq_extracted": None,
        "candidate_invoice_numbers": [],
        "candidate_references": [],
        "matched_invoice_id": None,
        "matched_invoice_number": None,
        "matched_payment_ids": [],
        "customer_id": None,
        "routing_confidence": 0.0,
        "classification": "UNKNOWN",
        "dispute_type_name": "General Clarification",
        "priority": "MEDIUM",
        "description": "",
        "invoice_number": None,
        "document_reference": None,
        "document_reference_type": None,
        "disputed_amount": None,
        "_answers_pending_questions": [],
        "_new_dispute_type": None,
        "inline_issues": [],
        "intent": "UNKNOWN",
        "requires_new_case": True,
        "requires_fork": False,
        "escalate_immediately": False,
        "priority_override": None,
        "suggested_action": "CREATE_CASE",
        "invoice_details": None,
        "all_payment_details": [],
        "existing_dispute_id": existing_dispute_id,
        "memory_summary": None,
        "recent_episodes": [],
        "pending_questions": [],
        "available_dispute_types": [],
        "similar_episodes": [],
        "embedding_matched": False,
        "embedding_dispute_id": None,
        "embedding_similarity": 0.0,
        "_needs_invoice_details": False,
        "_ownership_unverified": False,
        "token_matched_dispute_id": None,
        "existing_dispute_context": {},
        "related_dispute_id": None,
        "related_dispute_token": None,
        "context_shift_detected": False,
        "context_shift_confidence": 0.0,
        "context_shift_reasoning": None,
        "forked_issues": [],
        "original_dispute_still_active": True,
        "ai_summary": "",
        "ai_response": None,
        "confidence_score": 0.0,
        "auto_response_generated": False,
        "questions_to_ask": [],
        "memory_context_used": False,
        "episodes_referenced": [],
        "claim_type": "",
        "verification_status": "",
        "verification_evidence": "",
        "resolution_action": "",
        "ar_docs_for_verification": [],
        "tool_proposals": [],
        "tool_receipts": [],
        "executed_tool_count": 0,
        "cited_policy_claims": [],
        "contract_extraction": None,
        "credit_note_check": None,
        "po_grn_match": None,
        "fa_suggestion": None,
        "should_escalate": False,
        "per_issue_responses": [],
        "dispute_id": None,
        "forked_dispute_ids": [],
        "inline_dispute_ids": [],
        "analysis_id": None,
        "error": None,
        "ar_document_chain": [],
    }
