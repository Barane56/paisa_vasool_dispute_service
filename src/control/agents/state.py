"""
src/control/agents/state.py
============================
Single source of truth for EmailProcessingState.
Imported by every node and by the graph builder.
"""

from __future__ import annotations
from typing import TypedDict, Optional, List, Dict


class EmailProcessingState(TypedDict):
    # ── Input ─────────────────────────────────────────────────────────────────
    email_id:         int
    sender_email:     str
    subject:          str
    body_text:        str
    attachment_texts: List[str]

    # ── Text ──────────────────────────────────────────────────────────────────
    all_text: str

    # ── Groq-extracted invoice fields ─────────────────────────────────────────
    groq_extracted:            Optional[Dict]
    candidate_invoice_numbers: List[str]

    # ── DB-matched invoice + payments ─────────────────────────────────────────
    matched_invoice_id:     Optional[int]
    matched_invoice_number: Optional[str]
    matched_payment_ids:    List[int]
    customer_id:            Optional[str]
    routing_confidence:     float

    # ── Classification (runs BEFORE fetch_context) ────────────────────────────
    # Primary issue fields (used by all downstream nodes as before)
    classification:             str
    dispute_type_name:          str
    priority:                   str
    description:                str
    invoice_number:             Optional[str]   # invoice number for PRIMARY issue
    disputed_amount:            Optional[str]   # amount string for PRIMARY issue
    _answers_pending_questions: List[int]
    _new_dispute_type:          Optional[Dict]

    # Additional issues found in the SAME email — each becomes its own dispute.
    # Shape per item: {classification, dispute_type_name, is_new_type,
    #   new_type_description, new_type_severity, priority, description,
    #   invoice_number, disputed_amount}
    inline_issues: List[Dict]

    # ── Context (fetched AFTER classification) ────────────────────────────────
    invoice_details:         Optional[Dict]
    all_payment_details:     List[Dict]
    existing_dispute_id:     Optional[int]
    memory_summary:          Optional[str]
    recent_episodes:         List[Dict]
    pending_questions:       List[Dict]
    available_dispute_types: List[Dict]

    # ── Embedding search ──────────────────────────────────────────────────────
    similar_episodes:     List[Dict]
    embedding_matched:    bool
    embedding_dispute_id: Optional[int]
    embedding_similarity: float

    # ── Routing flags ─────────────────────────────────────────────────────────
    _needs_invoice_details:   bool
    token_matched_dispute_id: Optional[int]   # Layer 1: DISP-XXXXX token match

    # ── Context-shift / fork detection (follow-up emails) ─────────────────────
    context_shift_detected:        bool
    context_shift_confidence:      float
    context_shift_reasoning:       Optional[str]
    forked_issues:                 List[Dict]
    original_dispute_still_active: bool

    # ── AI output ─────────────────────────────────────────────────────────────
    ai_summary:              str
    ai_response:             Optional[str]   # primary issue (single-issue compat)
    confidence_score:        float
    auto_response_generated: bool
    questions_to_ask:        List[str]
    memory_context_used:     bool
    episodes_referenced:     List[int]

    # Per-issue responses for multi-issue emails.
    # Each entry shape:
    #   issue_index, invoice_number, classification, description,
    #   ai_response, can_auto_respond, ai_summary, confidence_score,
    #   questions_to_ask, dispute_token (placeholder resolved by persist_results)
    per_issue_responses: List[Dict]

    # ── Final ─────────────────────────────────────────────────────────────────
    dispute_id:          Optional[int]   # primary dispute id for this email
    forked_dispute_ids:  List[int]       # context-shift forks (follow-up emails)
    inline_dispute_ids:  List[int]       # additional disputes from same email (new emails)
    analysis_id:         Optional[int]
    error:               Optional[str]


# ─── Initial state factory ────────────────────────────────────────────────────

def build_initial_state(
    email_id:         int,
    sender_email:     str,
    subject:          str,
    body_text:        str,
    attachment_texts: List[str],
) -> EmailProcessingState:
    return {
        "email_id":                    email_id,
        "sender_email":                sender_email,
        "subject":                     subject,
        "body_text":                   body_text,
        "attachment_texts":            attachment_texts,
        "all_text":                    "",
        "groq_extracted":              None,
        "candidate_invoice_numbers":   [],
        "matched_invoice_id":          None,
        "matched_invoice_number":      None,
        "matched_payment_ids":         [],
        "customer_id":                 None,
        "routing_confidence":          0.0,
        "classification":              "UNKNOWN",
        "dispute_type_name":           "General Clarification",
        "priority":                    "MEDIUM",
        "description":                 "",
        "invoice_number":              None,
        "disputed_amount":             None,
        "_answers_pending_questions":  [],
        "_new_dispute_type":           None,
        "inline_issues":               [],
        "invoice_details":             None,
        "all_payment_details":         [],
        "existing_dispute_id":         None,
        "memory_summary":              None,
        "recent_episodes":             [],
        "pending_questions":           [],
        "available_dispute_types":     [],
        "similar_episodes":            [],
        "embedding_matched":           False,
        "embedding_dispute_id":        None,
        "embedding_similarity":        0.0,
        "_needs_invoice_details":      False,
        "token_matched_dispute_id":    None,
        "context_shift_detected":      False,
        "context_shift_confidence":    0.0,
        "context_shift_reasoning":     None,
        "forked_issues":               [],
        "original_dispute_still_active": True,
        "ai_summary":                  "",
        "ai_response":                 None,
        "confidence_score":            0.0,
        "auto_response_generated":     False,
        "questions_to_ask":            [],
        "memory_context_used":         False,
        "episodes_referenced":         [],
        "per_issue_responses":          [],
        "dispute_id":                  None,
        "forked_dispute_ids":          [],
        "inline_dispute_ids":          [],
        "analysis_id":                 None,
        "error":                       None,
    }