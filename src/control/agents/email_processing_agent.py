"""
LangGraph email processing agent — final version.

Pipeline (reordered from previous versions):
  extract_text
      ↓
  extract_invoice_data_via_groq   ← Groq extracts invoice fields from email + attachments
      ↓
  identify_invoice                ← match invoice number against DB, fetch all payments
      ↓
  classify_email                  ← MOVED UP: classify before fetch_context so dispute_type
      ↓                              is known when we look up existing disputes
  fetch_context                   ← REWRITTEN: uses customer_id + invoice_id + dispute_type_name
      ↓                              for precise dispute lookup. Also loads memory episodes for
      ↓                              customer even when no invoice matched (cold mail support)
  embed_and_search                ← cold mail path: if no invoice matched, embed ai_summary
      ↓                              and search customer's past episodes via pgvector
  resolve_dispute_link            ← link to existing dispute if found, else ask for details
      ↓
  generate_ai_response            ← REWRITTEN PROMPT: balanced, plain response (no email draft)
      ↓                              answers factual read-only queries directly
      ↓                              escalates disputes/adjustments
      ↓                              asks clarifying questions only when genuinely needed
  persist_results                 ← saves everything, embeds ai_summary on episode

Key design decisions
────────────────────
• classify_email before fetch_context so dispute type is available for precise DB lookup.
• fetch_context looks up dispute by customer_id + invoice_id + dispute_type (all three).
  Falls back to customer_id + invoice_id if no type match, then customer_id only for cold mail.
• Cold mail (no invoice number in email): embed_and_search searches ALL past episodes for
  this customer via pgvector cosine similarity. If a match is found above threshold, the email
  is linked to that dispute. Only if no match found do we ask the customer for invoice details.
• Memory is always loaded if any dispute is found — not gated on invoice match.
• ai_response is a plain conversational response, never formatted as an email draft.
• Clarifying questions are appended inline to the response only when info is genuinely missing.
"""

from __future__ import annotations

import re
import json
import logging
from typing import TypedDict, Optional, List, Dict
from datetime import datetime, timezone

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)


# ─── State ────────────────────────────────────────────────────────────────────

class EmailProcessingState(TypedDict):
    # Input
    email_id: int
    sender_email: str
    subject: str
    body_text: str
    attachment_texts: List[str]

    # Text
    all_text: str

    # Groq-extracted
    groq_extracted: Optional[Dict]
    candidate_invoice_numbers: List[str]

    # DB-matched invoice + payments
    matched_invoice_id: Optional[int]
    matched_invoice_number: Optional[str]
    matched_payment_ids: List[int]
    customer_id: Optional[str]
    routing_confidence: float

    # Classification (now runs BEFORE fetch_context)
    classification: str
    dispute_type_name: str
    priority: str
    description: str
    _answers_pending_questions: List[int]
    _new_dispute_type: Optional[Dict]

    # Context (fetched AFTER classification so dispute_type is known)
    invoice_details: Optional[Dict]
    all_payment_details: List[Dict]
    existing_dispute_id: Optional[int]
    memory_summary: Optional[str]
    recent_episodes: List[Dict]
    pending_questions: List[Dict]
    available_dispute_types: List[Dict]

    # Embedding search
    similar_episodes: List[Dict]
    embedding_matched: bool
    embedding_dispute_id: Optional[int]
    embedding_similarity: float

    # AI output
    ai_summary: str
    ai_response: Optional[str]
    confidence_score: float
    auto_response_generated: bool
    questions_to_ask: List[str]
    memory_context_used: bool
    episodes_referenced: List[int]

    # Final
    dispute_id: Optional[int]
    analysis_id: Optional[int]
    error: Optional[str]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _build_full_text(state: EmailProcessingState) -> str:
    parts = [state["subject"], state["body_text"]] + state["attachment_texts"]
    return "\n\n".join(filter(None, parts))


def _regex_invoice_numbers(text: str) -> List[str]:
    candidates: set[str] = set()
    patterns = [
        r"(?:invoice\s*(?:no\.?|number|#|num)[:\s#-]*)([\w\-/]+)",
        r"(?:inv[\.#\-/]*)([\w\-/]{4,20})",
        r"(?:bill\s*(?:no\.?|number|#)[:\s#-]*)([\w\-/]+)",
        r"(?:reference\s*(?:no\.?|number|#|:)\s*)([\w\-/]{4,20})",
        r"\b(INV[-/]?\d{3,10})\b",
        r"(?:invoice|inv)\D{0,10}(\d{4,8})\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            val = m.group(1).strip().upper()
            if len(val) >= 3:
                candidates.add(val)
    return list(candidates)


# ─── NODE 1: extract_text ─────────────────────────────────────────────────────

async def node_extract_text(state: EmailProcessingState) -> EmailProcessingState:
    return {**state, "all_text": _build_full_text(state)}


# ─── NODE 2: extract_invoice_data_via_groq ────────────────────────────────────

async def node_extract_invoice_data_via_groq(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    groq_extracted: Optional[Dict] = None
    candidates: List[str] = []

    if llm_client:
        try:
            groq_extracted = await llm_client.extract_invoice_data(state["all_text"])
            inv_num = groq_extracted.get("invoice_number")
            if inv_num:
                candidates.append(str(inv_num).upper().strip())
            po = groq_extracted.get("po_number")
            if po:
                candidates.append(str(po).upper().strip())
        except Exception as e:
            logger.warning(
                f"[email_id={state['email_id']}] Groq invoice extraction failed: {e}. "
                f"Falling back to regex."
            )

    for c in _regex_invoice_numbers(state["all_text"]):
        if c not in candidates:
            candidates.append(c)

    logger.info(f"[email_id={state['email_id']}] Invoice candidates: {candidates}")
    return {**state, "groq_extracted": groq_extracted, "candidate_invoice_numbers": candidates}


# ─── NODE 3: identify_invoice ─────────────────────────────────────────────────

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
            f"[email_id={state['email_id']}] No invoice matched from candidates={state['candidate_invoice_numbers']}"
        )

    return {
        **state,
        "matched_invoice_id":     matched_invoice.invoice_id if matched_invoice else None,
        "matched_invoice_number": matched_invoice.invoice_number if matched_invoice else None,
        "matched_payment_ids":    matched_payment_ids,
        "customer_id":            customer_id,
        "routing_confidence":     confidence,
    }


# ─── NODE 4: classify_email (MOVED UP — before fetch_context) ─────────────────

async def node_classify_email(
    state: EmailProcessingState, llm_client=None, db_session=None
) -> EmailProcessingState:
    """
    Classify the email BEFORE fetching context so that dispute_type_name is
    available when fetch_context does its precise dispute lookup.

    We still need available_dispute_types from DB here, so we do a lightweight
    fetch of just the dispute types (no episode loading — that's fetch_context's job).
    """
    # Fetch available dispute types for classification
    available_dispute_types: List[Dict] = []
    if db_session:
        from src.data.repositories.repositories import DisputeTypeRepository
        dtype_repo = DisputeTypeRepository(db_session)
        all_types  = await dtype_repo.get_active_types()
        available_dispute_types = [
            {
                "reason_name":    dt.reason_name,
                "description":    dt.description or "",
                "severity_level": dt.severity_level or "MEDIUM",
            }
            for dt in all_types
        ]
        logger.info(
            f"[email_id={state['email_id']}] Loaded {len(available_dispute_types)} "
            f"active dispute types for classification"
        )

    if not llm_client:
        text_lower = state["all_text"].lower()
        dispute_keywords = ["wrong", "incorrect", "mismatch", "overcharged", "dispute",
                            "error", "short payment", "not received"]
        classification = "DISPUTE" if any(k in text_lower for k in dispute_keywords) else "CLARIFICATION"
        return {
            **state,
            "available_dispute_types":    available_dispute_types,
            "classification":             classification,
            "dispute_type_name":          "Pricing Mismatch" if classification == "DISPUTE" else "General Clarification",
            "priority":                   "MEDIUM",
            "description":                state["body_text"][:500],
            "_answers_pending_questions": [],
            "_new_dispute_type":          None,
        }

    groq_block = ""
    if state.get("groq_extracted"):
        groq_block = f"\nEXTRACTED INVOICE DATA: {json.dumps(state['groq_extracted'])}"

    types_details = "\n".join([
        f"  - {dt['reason_name']}: {dt['description']} (severity: {dt['severity_level']})"
        for dt in available_dispute_types
    ])

    prompt = f"""You are an AR dispute classification expert.

EMAIL SUBJECT: {state['subject']}
EMAIL FROM: {state['sender_email']}
EMAIL BODY: {state['body_text'][:1000]}
ATTACHMENT TEXT: {' '.join(state['attachment_texts'])[:500]}
{groq_block}

AVAILABLE DISPUTE TYPES:
{types_details if types_details else 'None defined yet'}

Classify this email. Return ONLY valid JSON:
{{
  "classification": "DISPUTE" or "CLARIFICATION",
  "dispute_type_name": "Pick from available types above, or suggest a new one if none fit",
  "is_new_type": true or false,
  "new_type_description": "If is_new_type=true, describe the new type in 1-2 sentences",
  "priority": "LOW" or "MEDIUM" or "HIGH",
  "description": "2-3 sentence summary of what the customer is asking about"
}}"""

    try:
        response = await llm_client.chat(prompt)
        data     = json.loads(response)

        result = {
            **state,
            "available_dispute_types":    available_dispute_types,
            "classification":             data.get("classification", "CLARIFICATION"),
            "dispute_type_name":          data.get("dispute_type_name", "General Clarification"),
            "priority":                   data.get("priority", "MEDIUM"),
            "description":                data.get("description", state["body_text"][:500]),
            "_answers_pending_questions": [],
            "_new_dispute_type":          None,
        }

        if data.get("is_new_type"):
            result["_new_dispute_type"] = {
                "reason_name":    data.get("dispute_type_name"),
                "description":    data.get("new_type_description", ""),
                "severity_level": data.get("priority", "MEDIUM"),
            }
            logger.info(
                f"[email_id={state['email_id']}] New dispute type suggested: "
                f"{data.get('dispute_type_name')}"
            )

        return result

    except Exception as e:
        logger.error(f"[email_id={state['email_id']}] Classification error: {e}")
        return {
            **state,
            "available_dispute_types":    available_dispute_types,
            "classification":             "CLARIFICATION",
            "dispute_type_name":          "General Clarification",
            "priority":                   "MEDIUM",
            "description":                state["body_text"][:500],
            "_answers_pending_questions": [],
            "_new_dispute_type":          None,
        }


# ─── NODE 5: fetch_context (REWRITTEN) ───────────────────────────────────────

async def node_fetch_context(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    REWRITTEN — dispute lookup now uses customer_id + invoice_id + dispute_type_name
    (all three, because classify_email now runs before this node).

    Fallback chain for dispute lookup:
      1. customer_id + invoice_id + dispute_type_name   (most precise)
      2. customer_id + invoice_id                        (type mismatch — could be a re-open)
      3. customer_id only                                (cold mail — no invoice, load all
                                                          open disputes for this customer so
                                                          embed_and_search has episode history)

    Memory (episodes, summary, pending questions) is loaded whenever ANY dispute is found,
    regardless of how it was matched. This ensures the AI always has conversation history.
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

    # ── Dispute lookup (3-level fallback) ─────────────────────────────────────
    customer_id      = state.get("customer_id")
    dispute_type_name = state.get("dispute_type_name", "")
    matched_invoice_id = state.get("matched_invoice_id")

    matched_dispute = None

    if customer_id:
        dispute_repo  = DisputeRepository(db_session)
        open_disputes = await dispute_repo.get_by_customer(customer_id)

        if open_disputes:
            # Level 1: customer + invoice + dispute type (most precise)
            if matched_invoice_id and dispute_type_name:
                for d in open_disputes:
                    if (
                        d.invoice_id == matched_invoice_id
                        and d.dispute_type
                        and d.dispute_type.reason_name == dispute_type_name
                    ):
                        matched_dispute = d
                        logger.info(
                            f"[email_id={state['email_id']}] Dispute matched: "
                            f"customer + invoice + type → dispute_id={d.dispute_id}"
                        )
                        break

            # Level 2: customer + invoice (type mismatch, could be a re-open)
            if not matched_dispute and matched_invoice_id:
                for d in open_disputes:
                    if d.invoice_id == matched_invoice_id:
                        matched_dispute = d
                        logger.info(
                            f"[email_id={state['email_id']}] Dispute matched: "
                            f"customer + invoice (type mismatch) → dispute_id={d.dispute_id}"
                        )
                        break

            # Level 3: customer only (cold mail — no invoice in email)
            # Take the most recent open dispute for this customer.
            # embed_and_search will validate the semantic match later.
            if not matched_dispute and not matched_invoice_id:
                matched_dispute = open_disputes[0]  # most recent (get_by_customer orders desc)
                logger.info(
                    f"[email_id={state['email_id']}] Cold mail — loaded most recent dispute "
                    f"for customer={customer_id}: dispute_id={matched_dispute.dispute_id}"
                )

    # ── Load memory for whatever dispute we found ─────────────────────────────
    if matched_dispute:
        existing_dispute_id = matched_dispute.dispute_id

        ep_repo    = MemoryEpisodeRepository(db_session)
        recent_eps = await ep_repo.get_latest_n(existing_dispute_id, n=5)
        recent_episodes = [
            {
                "actor": ep.actor,
                "type":  ep.episode_type,
                "text":  ep.content_text[:400],
            }
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
            f"[email_id={state['email_id']}] Loaded memory for dispute_id={existing_dispute_id}: "
            f"{len(recent_episodes)} episodes, {len(pending_questions)} pending questions"
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


# ─── NODE 6: embed_and_search ─────────────────────────────────────────────────

async def node_embed_and_search(
    state: EmailProcessingState,
    llm_client=None,
    db_session=None,
) -> EmailProcessingState:
    """
    Embeds the email description (from classify_email) and searches past episodes
    for this customer via pgvector cosine similarity.

    When is this most useful?
    ─────────────────────────
    Cold mail (no invoice number in email): fetch_context loaded the most recent dispute
    for this customer as a candidate. embed_and_search now validates whether the current
    email is actually semantically related to that (or any other) dispute's past episodes.

    If a strong match is found, resolve_dispute_link will confirm the link.
    If no match, resolve_dispute_link asks the customer for invoice details.

    We use the `description` field (from classify_email) as the text to embed rather than
    the raw email body — it's already distilled by the LLM and carries the intent cleanly.
    """
    defaults = {
        **state,
        "similar_episodes":     [],
        "embedding_matched":    False,
        "embedding_dispute_id": None,
        "embedding_similarity": 0.0,
    }

    # Only run embedding search when NO invoice was matched
    # (if invoice matched, we already have the right dispute context)
    if state.get("matched_invoice_id"):
        logger.info(
            f"[email_id={state['email_id']}] embed_and_search skipped: "
            f"invoice already matched"
        )
        return defaults

    text_to_embed = state.get("description", "").strip() or state.get("body_text", "").strip()
    customer_id   = state.get("customer_id")

    if not text_to_embed or not customer_id:
        logger.warning(
            f"[email_id={state['email_id']}] embed_and_search skipped: "
            f"missing text or customer_id"
        )
        return defaults

    if not llm_client or not db_session:
        return defaults

    from src.data.repositories.repositories import MemoryEpisodeRepository
    from src.config.settings import settings

    embedding = await llm_client.embed(text_to_embed)
    if not embedding:
        logger.warning(f"[email_id={state['email_id']}] Embedding returned None")
        return defaults

    logger.info(
        f"[email_id={state['email_id']}] Searching past episodes for "
        f"customer={customer_id} (dims={len(embedding)})"
    )

    ep_repo     = MemoryEpisodeRepository(db_session)
    similar_eps = await ep_repo.search_similar_by_customer(
        customer_id=customer_id,
        query_embedding=embedding,
        top_k=5,
        threshold=settings.EPISODE_SIMILARITY_THRESHOLD,
    )

    if similar_eps:
        best = similar_eps[0]
        logger.info(
            f"[email_id={state['email_id']}] Best episode match: "
            f"dispute_id={best['dispute_id']}, similarity={best['similarity']}"
        )
        return {
            **state,
            "similar_episodes":     similar_eps,
            "embedding_matched":    True,
            "embedding_dispute_id": best["dispute_id"],
            "embedding_similarity": best["similarity"],
        }

    logger.info(
        f"[email_id={state['email_id']}] No similar episodes found above "
        f"threshold={settings.EPISODE_SIMILARITY_THRESHOLD}"
    )
    return defaults


# ─── NODE 7: resolve_dispute_link ─────────────────────────────────────────────

async def node_resolve_dispute_link(
    state: EmailProcessingState,
    db_session=None,
) -> EmailProcessingState:
    """
    Decides the final dispute context for this email.

    Scenario A — Invoice matched
      → existing_dispute_id already set correctly by fetch_context. Pass through.

    Scenario B — No invoice, but embedding found a matching past episode
      → Override existing_dispute_id with the embedding-matched dispute.
        Load that dispute's memory (episodes + summary) to replace the candidate
        loaded by fetch_context (which was just the most recent dispute, unvalidated).

    Scenario C — No invoice, no embedding match
      → We genuinely can't identify what this email is about.
        Set a flag so generate_ai_response asks the customer for invoice details.
        existing_dispute_id stays None — persist_results creates a new dispute.
    """
    invoice_matched   = state.get("matched_invoice_id") is not None
    embedding_matched = state.get("embedding_matched", False)

    # ── Scenario A ────────────────────────────────────────────────────────────
    if invoice_matched:
        logger.info(
            f"[email_id={state['email_id']}] resolve: invoice matched "
            f"(id={state['matched_invoice_id']}) — no change"
        )
        return {**state, "_needs_invoice_details": False}

    # ── Scenario B ────────────────────────────────────────────────────────────
    if embedding_matched and state.get("embedding_dispute_id"):
        linked_id  = state["embedding_dispute_id"]
        similarity = state.get("embedding_similarity", 0.0)

        logger.info(
            f"[email_id={state['email_id']}] resolve: embedding linked to "
            f"dispute_id={linked_id} (similarity={similarity})"
        )

        # Replace the candidate context with the confirmed matched dispute's memory
        if db_session:
            from src.data.repositories.repositories import (
                MemoryEpisodeRepository, MemorySummaryRepository, OpenQuestionRepository
            )

            ep_repo    = MemoryEpisodeRepository(db_session)
            recent_eps = await ep_repo.get_latest_n(linked_id, n=5)
            recent_episodes = [
                {"actor": ep.actor, "type": ep.episode_type, "text": ep.content_text[:400]}
                for ep in recent_eps
            ]

            sum_repo    = MemorySummaryRepository(db_session)
            summary_obj = await sum_repo.get_for_dispute(linked_id)
            memory_summary = summary_obj.summary_text if summary_obj else state.get("memory_summary")

            q_repo     = OpenQuestionRepository(db_session)
            pending_qs = await q_repo.get_pending_for_dispute(linked_id)
            pending_questions = [
                {"question_id": q.question_id, "text": q.question_text}
                for q in pending_qs
            ]

            return {
                **state,
                "existing_dispute_id": linked_id,
                "recent_episodes":     recent_episodes,
                "memory_summary":      memory_summary,
                "pending_questions":   pending_questions,
                "_needs_invoice_details": False,
            }

        return {**state, "existing_dispute_id": linked_id, "_needs_invoice_details": False}

    # ── Scenario C ────────────────────────────────────────────────────────────
    logger.info(
        f"[email_id={state['email_id']}] resolve: no invoice, no embedding match — "
        f"will ask customer for invoice details"
    )
    return {
        **state,
        "existing_dispute_id": None,
        "_needs_invoice_details": True,
    }


# ─── NODE 8: generate_ai_response (REWRITTEN PROMPT) ─────────────────────────

async def node_generate_ai_response(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    """
    Balanced response generation.

    Rules:
    ──────
    • Response is plain conversational text — NOT an email draft. No "Dear X", no sign-offs.
    • Answer factual read-only queries directly if the data is present in context.
      e.g. "What is the due date?" → just answer it from invoice_details.
    • Escalate (can_auto_respond=false) for disputes, adjustments, ambiguous amounts,
      payment discrepancies, or anything requiring a financial decision.
    • Ask clarifying questions ONLY when info is genuinely missing and needed to proceed.
      Max 2 questions, factual only (invoice number, payment reference, date of payment).
      Do NOT ask questions just to seem thorough.
    • If _needs_invoice_details=True, the response must ask for invoice number / date.
    • questions_to_ask is for FA team internal investigation — never shown to customer.
    """
    if not llm_client:
        return {
            **state,
            "ai_summary":              state.get("description", "Email processed."),
            "ai_response":             None,
            "confidence_score":        0.5,
            "auto_response_generated": False,
            "questions_to_ask":        [],
            "memory_context_used":     False,
            "episodes_referenced":     [],
        }

    # If we couldn't identify any invoice or dispute, skip LLM and use a fixed response
    if state.get("_needs_invoice_details"):
        response_text = (
            "Thanks for reaching out. We weren't able to locate the relevant invoice "
            "from the details provided. Could you share the invoice number and approximate "
            "invoice date so we can look into this for you?"
        )
        return {
            **state,
            "ai_summary":              state.get("description", "Customer query without invoice reference."),
            "ai_response":             response_text,
            "confidence_score":        0.9,
            "auto_response_generated": True,   # this is a safe, factual ask — fine to send
            "questions_to_ask":        [
                "FA: No invoice or past dispute could be matched. "
                "Await customer reply with invoice details before creating a full dispute."
            ],
            "memory_context_used":     False,
            "episodes_referenced":     [],
        }

    invoice_ctx = json.dumps(state.get("invoice_details") or {}, indent=2)
    all_pmts    = state.get("all_payment_details") or []
    payment_ctx = json.dumps(all_pmts, indent=2) if all_pmts else "No payment records on file"
    memory_ctx  = state.get("memory_summary") or "No previous conversation on record"
    recent_eps  = state.get("recent_episodes", [])
    pending_qs  = state.get("pending_questions", [])

    prompt = f"""You are an AR (accounts receivable) assistant helping handle customer invoice queries.

CUSTOMER EMAIL
──────────────
Subject: {state['subject']}
From: {state['sender_email']}
Body:
{state['body_text'][:800]}

INVOICE ON RECORD
─────────────────
{invoice_ctx}

PAYMENT RECORDS ({len(all_pmts)} on file)
─────────────────
{payment_ctx}

PREVIOUS CONVERSATION SUMMARY
──────────────────────────────
{memory_ctx}

RECENT CONVERSATION EPISODES
─────────────────────────────
{json.dumps(recent_eps[:4], indent=2)}

PENDING UNANSWERED QUESTIONS (from previous interactions)
──────────────────────────────────────────────────────────
{json.dumps(pending_qs, indent=2) if pending_qs else 'None'}

CLASSIFICATION
──────────────
Type: {state.get('classification')}
Category: {state.get('dispute_type_name')}
Priority: {state.get('priority')}
Summary: {state.get('description')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSE RULES — READ CAREFULLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Your response is a plain conversational reply. NO email formatting, no "Dear ...",
no "Best regards", no sign-off. Write as if you are typing a direct reply.

STEP 1 — Decide: can you answer this directly?

  ✅ Answer directly (can_auto_respond=true) ONLY when ALL of these are true:
     • The question is READ-ONLY — customer wants to KNOW something, not change anything
     • The answer is clearly present in INVOICE ON RECORD or PAYMENT RECORDS above
     • There is no dispute, discrepancy, or disagreement about the figures
     Examples: due date, invoice total, tax amount, payment status, line item breakdown,
               accepted payment methods, account manager contact

  ❌ Escalate (can_auto_respond=false) when ANY of these are true:
     • Customer is disputing an amount, claiming they paid more/less, or saying figures are wrong
     • Customer wants an adjustment, credit, waiver, or refund
     • Customer is questioning the correctness of the invoice (even politely)
     • Payment was made but not reflected on record (needs verification, not a lookup)
     • The answer is NOT clearly present in the data above
     • Priority is HIGH
     • Legal language or escalation threat in the email

STEP 2 — Write the response

  If can_auto_respond=true:
    • State the answer directly from the data. Be specific — include the actual value.
    • Keep it short and professional. 2-4 sentences max.
    • Do NOT over-explain or add unnecessary caveats.
    • Only ask a clarifying question if something critical is genuinely missing from
      the email AND you cannot answer without it. Max 1 question in this case.

  If can_auto_respond=false:
    • Acknowledge the query briefly and warmly.
    • Do NOT mention amounts, dates, or financial figures.
    • Do NOT attempt to investigate or answer the dispute in the response.
    • Tell them the team will review and follow up.
    • If a critical reference (e.g. payment transaction ID, remittance advice) is
      genuinely missing and needed for the FA team to investigate, ask for it.
      Maximum 2 questions. Only ask if not already present in the email or context.

STEP 3 — FA investigation questions (for questions_to_ask, NEVER shown to customer)
    • List what the FA team specifically needs to verify to resolve this.
    • Be specific: name the field, amount, or record to check.

STEP 4 — Check pending questions
    • If this email answers any of the PENDING UNANSWERED QUESTIONS above,
      include their question_ids in answers_pending_questions.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY valid JSON:
{{
  "ai_summary": "2-3 sentence summary of what the customer wants and what action is needed",
  "can_auto_respond": true or false,
  "auto_respond_reason": "one sentence: why this is safe to answer OR why it needs escalation",
  "ai_response": "Your plain conversational response text here. No email formatting.",
  "confidence_score": 0.0-1.0,
  "questions_to_ask": ["FA internal question 1", "FA internal question 2"],
  "answers_pending_questions": [list of question_ids answered by this email, e.g. [2, 5]],
  "episodes_referenced": [0, 1],
  "memory_context_used": true or false
}}"""

    try:
        response = await llm_client.chat(prompt)
        data     = json.loads(response)

        logger.info(
            f"[email_id={state['email_id']}] Auto-respond={data.get('can_auto_respond')} | "
            f"reason: {data.get('auto_respond_reason', 'N/A')}"
        )

        # Merge answered pending questions into state
        answered_ids = data.get("answers_pending_questions", [])

        return {
            **state,
            "ai_summary":                 data.get("ai_summary", state.get("description", "")),
            "ai_response":                data.get("ai_response"),
            "confidence_score":           data.get("confidence_score", 0.7),
            "auto_response_generated":    bool(data.get("can_auto_respond")),
            "questions_to_ask":           data.get("questions_to_ask", []),
            "memory_context_used":        data.get("memory_context_used", False),
            "episodes_referenced":        data.get("episodes_referenced", []),
            "_answers_pending_questions": answered_ids,
        }

    except Exception as e:
        logger.error(f"[email_id={state['email_id']}] AI response generation error: {e}")
        return {
            **state,
            "ai_summary":                 state.get("description", ""),
            "ai_response":                None,
            "confidence_score":           0.5,
            "auto_response_generated":    False,
            "questions_to_ask":           [],
            "memory_context_used":        False,
            "episodes_referenced":        [],
            "_answers_pending_questions": [],
        }


# ─── NODE 9: persist_results ──────────────────────────────────────────────────

async def node_persist_results(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Saves dispute, analysis, memory episodes (with embeddings), open questions,
    and email routing.

    Embedding persistence: after creating the AI episode, embed the ai_summary
    and store in content_embedding so future pgvector searches find it.
    """
    if not db_session:
        return state

    from src.data.repositories.repositories import (
        DisputeTypeRepository, DisputeRepository, EmailRepository,
        MemoryEpisodeRepository, OpenQuestionRepository, UserRepository,
        AnalysisSupportingRefRepository,
    )
    from src.data.models.postgres.models import (
        DisputeMaster, DisputeAIAnalysis, DisputeType,
        DisputeMemoryEpisode, DisputeOpenQuestion,
        DisputeActivityLog, DisputeAssignment,
    )
    from sqlalchemy import update as sa_update
    from src.data.models.postgres.models import EmailInbox

    try:
        # 1. Resolve or create dispute type
        dtype_repo   = DisputeTypeRepository(db_session)
        dispute_type = await dtype_repo.get_by_name(state["dispute_type_name"])

        if not dispute_type:
            new_type_data = state.get("_new_dispute_type")
            if new_type_data:
                dispute_type = DisputeType(
                    reason_name=new_type_data["reason_name"],
                    description=new_type_data.get("description", ""),
                    severity_level=new_type_data.get("severity_level", "MEDIUM"),
                    is_active=True,
                )
                db_session.add(dispute_type)
                await db_session.flush()
                logger.info(
                    f"[email_id={state['email_id']}] Created new dispute type: "
                    f"{dispute_type.reason_name}"
                )
            else:
                dispute_type = await dtype_repo.get_by_name("General Clarification")
                if not dispute_type:
                    dispute_type = DisputeType(
                        reason_name="General Clarification",
                        description="General inquiries and clarification requests",
                        severity_level="LOW",
                        is_active=True,
                    )
                    db_session.add(dispute_type)
                    await db_session.flush()

        dispute_id         = state.get("existing_dispute_id")
        primary_payment_id = state["matched_payment_ids"][0] if state.get("matched_payment_ids") else None

        # 2. Create or reuse dispute
        if not dispute_id:
            dispute = DisputeMaster(
                email_id=state["email_id"],
                invoice_id=state.get("matched_invoice_id"),
                payment_detail_id=primary_payment_id,
                customer_id=state["customer_id"] or "unknown",
                dispute_type_id=dispute_type.dispute_type_id,
                status="OPEN",
                priority=state.get("priority", "MEDIUM"),
                description=state.get("description", ""),
            )
            db_session.add(dispute)
            await db_session.flush()
            dispute_id = dispute.dispute_id
            logger.info(
                f"[email_id={state['email_id']}] Created dispute_id={dispute_id} | "
                f"invoice_id={state.get('matched_invoice_id')} | "
                f"payments={state.get('matched_payment_ids')}"
            )
        else:
            dispute = await DisputeRepository(db_session).get_by_id(dispute_id)
            if dispute and not dispute.payment_detail_id and primary_payment_id:
                dispute.payment_detail_id = primary_payment_id

            log = DisputeActivityLog(
                dispute_id=dispute_id,
                action_type="FOLLOW_UP_EMAIL_RECEIVED",
                notes=f"New email: {state['subject'][:100]}",
            )
            db_session.add(log)

        # 3. AI analysis
        analysis = DisputeAIAnalysis(
            dispute_id=dispute_id,
            predicted_category=state["dispute_type_name"],
            confidence_score=state.get("confidence_score", 0.0),
            ai_summary=state.get("ai_summary", ""),
            ai_response=state.get("ai_response"),
            auto_response_generated=state.get("auto_response_generated", False),
            memory_context_used=state.get("memory_context_used", False),
            episodes_referenced=state.get("episodes_referenced") or [],
        )
        db_session.add(analysis)
        await db_session.flush()

        # 3a. Supporting documents
        ref_repo = AnalysisSupportingRefRepository(db_session)
        if state.get("matched_invoice_id"):
            await ref_repo.upsert_supporting_doc(
                analysis_id=analysis.analysis_id,
                reference_table="invoice_data",
                ref_id_value=state["matched_invoice_id"],
                context_note=(
                    f"Invoice {state.get('matched_invoice_number', state['matched_invoice_id'])} "
                    f"— primary supporting document"
                ),
            )
        for pid in state.get("matched_payment_ids", []):
            await ref_repo.upsert_supporting_doc(
                analysis_id=analysis.analysis_id,
                reference_table="payment_detail",
                ref_id_value=pid,
                context_note=(
                    f"Payment {pid} for invoice "
                    f"{state.get('matched_invoice_number', '')} — supporting document"
                ),
            )

        # 4. Customer email episode
        email_episode = DisputeMemoryEpisode(
            dispute_id=dispute_id,
            episode_type="CUSTOMER_EMAIL",
            actor="CUSTOMER",
            content_text=f"Subject: {state['subject']}\n\n{state['body_text'][:1000]}",
            email_id=state["email_id"],
        )
        db_session.add(email_episode)
        await db_session.flush()

        # 5. AI response episode
        ai_episode = None
        if state.get("ai_response"):
            ep_type    = "AI_RESPONSE" if state.get("auto_response_generated") else "AI_ACKNOWLEDGEMENT"
            ai_episode = DisputeMemoryEpisode(
                dispute_id=dispute_id,
                episode_type=ep_type,
                actor="AI",
                content_text=state["ai_response"],
                email_id=state["email_id"],
            )
            db_session.add(ai_episode)
            await db_session.flush()

            # Mark answered pending questions
            answered_ids = state.get("_answers_pending_questions", [])
            if answered_ids:
                q_repo = OpenQuestionRepository(db_session)
                for qid in answered_ids:
                    q = await q_repo.get_by_id(qid)
                    if q and q.status == "PENDING":
                        q.status                 = "ANSWERED"
                        q.answered_in_episode_id = ai_episode.episode_id
                        q.answered_at            = datetime.now(timezone.utc)

        # 5a. Embed ai_summary and save on AI episode
        ai_summary_text = state.get("ai_summary", "").strip()
        if ai_episode and ai_summary_text:
            from src.handlers.http_clients.llm_client import get_llm_client
            try:
                _llm      = get_llm_client()
                embedding = await _llm.embed(ai_summary_text)
                if embedding:
                    ep_repo = MemoryEpisodeRepository(db_session)
                    await ep_repo.upsert_embedding(ai_episode.episode_id, embedding)
                    logger.info(
                        f"[email_id={state['email_id']}] Saved embedding "
                        f"(dims={len(embedding)}) on episode_id={ai_episode.episode_id}"
                    )
            except Exception as emb_err:
                logger.warning(
                    f"[email_id={state['email_id']}] Embedding save failed (non-fatal): {emb_err}"
                )

        # 6. FA open questions
        for question_text in state.get("questions_to_ask", []):
            question = DisputeOpenQuestion(
                dispute_id=dispute_id,
                asked_in_episode_id=email_episode.episode_id,
                question_text=question_text,
                status="PENDING",
            )
            db_session.add(question)

        # 7. Email routing
        email_repo = EmailRepository(db_session)
        await email_repo.update_status(state["email_id"], "PROCESSED")
        stmt = (
            sa_update(EmailInbox)
            .where(EmailInbox.email_id == state["email_id"])
            .values(
                dispute_id=dispute_id,
                routing_confidence=state.get("routing_confidence", 0.0),
            )
        )
        await db_session.execute(stmt)

        # 8. Auto-assign to FA if not auto-responded
        if not state.get("auto_response_generated"):
            user_repo = UserRepository(db_session)
            all_users = await user_repo.get_all(limit=10)
            if all_users:
                assign = DisputeAssignment(
                    dispute_id=dispute_id,
                    assigned_to=all_users[0].user_id,
                    status="ACTIVE",
                )
                db_session.add(assign)

        await db_session.commit()

        # 9. Summarisation trigger
        ep_repo  = MemoryEpisodeRepository(db_session)
        ep_count = await ep_repo.count_for_dispute(dispute_id)
        from src.config.settings import settings
        if ep_count >= settings.EPISODE_SUMMARIZE_THRESHOLD:
            from src.control.tasks import summarize_episodes_task
            summarize_episodes_task.delay(dispute_id)

        return {**state, "dispute_id": dispute_id, "analysis_id": analysis.analysis_id}

    except Exception as e:
        logger.error(f"Persist error email_id={state['email_id']}: {e}", exc_info=True)
        await db_session.rollback()
        try:
            email_repo = EmailRepository(db_session)
            await email_repo.update_status(state["email_id"], "FAILED", str(e))
            await db_session.commit()
        except Exception:
            pass
        return {**state, "error": str(e)}


# ─── Graph ────────────────────────────────────────────────────────────────────

def build_email_processing_graph(db_session=None, llm_client=None):
    from functools import partial

    graph = StateGraph(EmailProcessingState)

    graph.add_node("extract_text",                  node_extract_text)
    graph.add_node("extract_invoice_data_via_groq",  partial(node_extract_invoice_data_via_groq, llm_client=llm_client))
    graph.add_node("identify_invoice",               partial(node_identify_invoice,              db_session=db_session))
    graph.add_node("classify_email",                 partial(node_classify_email,                llm_client=llm_client, db_session=db_session))
    graph.add_node("fetch_context",                  partial(node_fetch_context,                 db_session=db_session))
    graph.add_node("embed_and_search",               partial(node_embed_and_search,              llm_client=llm_client, db_session=db_session))
    graph.add_node("resolve_dispute_link",           partial(node_resolve_dispute_link,          db_session=db_session))
    graph.add_node("generate_ai_response",           partial(node_generate_ai_response,          llm_client=llm_client))
    graph.add_node("persist_results",                partial(node_persist_results,               db_session=db_session))

    graph.set_entry_point("extract_text")
    graph.add_edge("extract_text",                  "extract_invoice_data_via_groq")
    graph.add_edge("extract_invoice_data_via_groq", "identify_invoice")
    graph.add_edge("identify_invoice",              "classify_email")
    graph.add_edge("classify_email",                "fetch_context")
    graph.add_edge("fetch_context",                 "embed_and_search")
    graph.add_edge("embed_and_search",              "resolve_dispute_link")
    graph.add_edge("resolve_dispute_link",          "generate_ai_response")
    graph.add_edge("generate_ai_response",          "persist_results")
    graph.add_edge("persist_results",               END)

    return graph.compile()


async def run_email_processing(
    email_id: int,
    sender_email: str,
    subject: str,
    body_text: str,
    attachment_texts: List[str],
    db_session=None,
    llm_client=None,
) -> EmailProcessingState:
    graph = build_email_processing_graph(db_session=db_session, llm_client=llm_client)

    initial_state: EmailProcessingState = {
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
        # classification (set before fetch_context)
        "classification":         "UNKNOWN",
        "dispute_type_name":      "General Clarification",
        "priority":               "MEDIUM",
        "description":            "",
        "_answers_pending_questions": [],
        "_new_dispute_type":      None,
        # context
        "invoice_details":        None,
        "all_payment_details":    [],
        "existing_dispute_id":    None,
        "memory_summary":         None,
        "recent_episodes":        [],
        "pending_questions":      [],
        "available_dispute_types": [],
        # embedding
        "similar_episodes":       [],
        "embedding_matched":      False,
        "embedding_dispute_id":   None,
        "embedding_similarity":   0.0,
        # ai output
        "ai_summary":             "",
        "ai_response":            None,
        "confidence_score":       0.0,
        "auto_response_generated": False,
        "questions_to_ask":       [],
        "memory_context_used":    False,
        "episodes_referenced":    [],
        # final
        "dispute_id":             None,
        "analysis_id":            None,
        "error":                  None,
    }

    return await graph.ainvoke(initial_state)