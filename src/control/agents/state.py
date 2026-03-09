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
    groq_extracted:           Optional[Dict]
    candidate_invoice_numbers: List[str]

    # ── DB-matched invoice + payments ─────────────────────────────────────────
    matched_invoice_id:     Optional[int]
    matched_invoice_number: Optional[str]
    matched_payment_ids:    List[int]
    customer_id:            Optional[str]
    routing_confidence:     float

    # ── Classification (runs BEFORE fetch_context) ───────────────────────────
    classification:             str
    dispute_type_name:          str
    priority:                   str
    description:                str
    _answers_pending_questions: List[int]
    _new_dispute_type:          Optional[Dict]

    # ── Context (fetched AFTER classification) ────────────────────────────────
    invoice_details:       Optional[Dict]
    all_payment_details:   List[Dict]
    existing_dispute_id:   Optional[int]
    memory_summary:        Optional[str]
    recent_episodes:       List[Dict]
    pending_questions:     List[Dict]
    available_dispute_types: List[Dict]

    # ── Embedding search ──────────────────────────────────────────────────────
    similar_episodes:     List[Dict]
    embedding_matched:    bool
    embedding_dispute_id: Optional[int]
    embedding_similarity: float

    # ── Routing flags ─────────────────────────────────────────────────────────
    _needs_invoice_details: bool

    # ── AI output ─────────────────────────────────────────────────────────────
    ai_summary:              str
    ai_response:             Optional[str]
    confidence_score:        float
    auto_response_generated: bool
    questions_to_ask:        List[str]
    memory_context_used:     bool
    episodes_referenced:     List[int]

    # ── Final ─────────────────────────────────────────────────────────────────
    dispute_id:  Optional[int]
    analysis_id: Optional[int]
    error:       Optional[str]


# ─── Initial state factory ────────────────────────────────────────────────────

def build_initial_state(
    email_id:         int,
    sender_email:     str,
    subject:          str,
    body_text:        str,
    attachment_texts: List[str],
) -> EmailProcessingState:
    return {
        "email_id":               email_id,
        "sender_email":           sender_email,
        "subject":                subject,
        "body_text":              body_text,
        "attachment_texts":       attachment_texts,
        "all_text":               "",
        "groq_extracted":         None,
        "candidate_invoice_numbers": [],
        "matched_invoice_id":     None,
        "matched_invoice_number": None,
        "matched_payment_ids":    [],
        "customer_id":            None,
        "routing_confidence":     0.0,
        "classification":         "UNKNOWN",
        "dispute_type_name":      "General Clarification",
        "priority":               "MEDIUM",
        "description":            "",
        "_answers_pending_questions": [],
        "_new_dispute_type":      None,
        "invoice_details":        None,
        "all_payment_details":    [],
        "existing_dispute_id":    None,
        "memory_summary":         None,
        "recent_episodes":        [],
        "pending_questions":      [],
        "available_dispute_types": [],
        "similar_episodes":       [],
        "embedding_matched":      False,
        "embedding_dispute_id":   None,
        "embedding_similarity":   0.0,
        "_needs_invoice_details": False,
        "ai_summary":             "",
        "ai_response":            None,
        "confidence_score":       0.0,
        "auto_response_generated": False,
        "questions_to_ask":       [],
        "memory_context_used":    False,
        "episodes_referenced":    [],
        "dispute_id":             None,
        "analysis_id":            None,
        "error":                  None,
    }
