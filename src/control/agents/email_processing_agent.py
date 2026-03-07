"""
LangGraph-based email processing agent.

Pipeline:
  extract_text
      ↓
  extract_invoice_data_via_groq   ← Groq intelligently extracts all invoice fields
      ↓
  identify_invoice                ← matches invoice_number against DB; fetches ALL payment records
      ↓
  fetch_context                   ← loads invoice + ALL payments + memory episodes
      ↓
  classify_email                  ← DISPUTE | CLARIFICATION | UNKNOWN
      ↓
  generate_ai_response            ← conservative auto-response logic
      ↓
  embed_and_search          [NEW] ← embeds ai_summary → pgvector similarity search
      ↓                           scoped to customer_id; finds best matching past episode
  resolve_dispute_link      [NEW] ← links to existing dispute if match found above threshold
      ↓                           OR drafts "please share invoice details" response + creates
      ↓                              new dispute + assigns to FA if no match and no invoice
  persist_results                 ← saves everything + auto-links supporting docs

Key design notes
────────────────
• embed_and_search embeds the ai_summary (not raw email text) — compact and signal-rich.
• Similarity threshold is read from settings.EPISODE_SIMILARITY_THRESHOLD (default 0.75).
• resolve_dispute_link only fires the "ask for invoice" path when BOTH conditions are true:
    - No invoice was matched from the email (matched_invoice_id is None)
    - No similar past episode was found above threshold
  If either condition is satisfied, we already have enough context to proceed normally.
• The new state fields are:
    similar_episodes        List[dict]   top-k results from pgvector search
    embedding_matched       bool         True if a high-confidence match was found
    embedding_dispute_id    Optional[int] dispute_id of the best-matched episode
    embedding_similarity    float        similarity score of the best match
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

    # Groq-extracted invoice data
    groq_extracted: Optional[Dict]
    candidate_invoice_numbers: List[str]

    # DB-matched
    matched_invoice_id: Optional[int]
    matched_invoice_number: Optional[str]
    matched_payment_ids: List[int]
    customer_id: Optional[str]
    routing_confidence: float

    # Context
    invoice_details: Optional[Dict]
    all_payment_details: List[Dict]
    existing_dispute_id: Optional[int]
    memory_summary: Optional[str]
    recent_episodes: List[Dict]
    pending_questions: List[Dict]
    available_dispute_types: List[Dict]

    # Classification
    classification: str
    dispute_type_name: str
    priority: str
    description: str

    # AI output
    ai_summary: str
    ai_response: Optional[str]
    confidence_score: float
    auto_response_generated: bool
    questions_to_ask: List[str]
    memory_context_used: bool
    episodes_referenced: List[int]
    _answers_pending_questions: List[int]

    # ── NEW: embedding search results ─────────────────────────────────────────
    similar_episodes: List[Dict]        # top-k results from pgvector search
    embedding_matched: bool             # True  → found a past episode above threshold
    embedding_dispute_id: Optional[int] # dispute_id of the best-matched episode
    embedding_similarity: float         # cosine similarity of the best match (0→1)
    # ─────────────────────────────────────────────────────────────────────────

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


# ─── Nodes ────────────────────────────────────────────────────────────────────

async def node_extract_text(state: EmailProcessingState) -> EmailProcessingState:
    return {**state, "all_text": _build_full_text(state)}


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
            logger.warning(f"[email_id={state['email_id']}] Groq invoice extraction failed: {e}. Falling back to regex.")

    for c in _regex_invoice_numbers(state["all_text"]):
        if c not in candidates:
            candidates.append(c)

    logger.info(f"[email_id={state['email_id']}] Invoice candidates: {candidates}")
    return {**state, "groq_extracted": groq_extracted, "candidate_invoice_numbers": candidates}


async def node_identify_invoice(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
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

    matched_payment_ids: List[int] = []
    if matched_invoice:
        payments = await pay_repo.get_all_by_invoice_number(matched_invoice.invoice_number)
        matched_payment_ids = [p.payment_detail_id for p in payments]
        logger.info(
            f"[email_id={state['email_id']}] Matched {len(matched_payment_ids)} payment(s) "
            f"for invoice={matched_invoice.invoice_number}: ids={matched_payment_ids}"
        )

    return {
        **state,
        "matched_invoice_id":     matched_invoice.invoice_id if matched_invoice else None,
        "matched_invoice_number": matched_invoice.invoice_number if matched_invoice else None,
        "matched_payment_ids":    matched_payment_ids,
        "customer_id":            customer_id,
        "routing_confidence":     confidence,
    }


async def node_fetch_context(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    if not db_session:
        return {
            **state,
            "invoice_details": None,
            "all_payment_details": [],
            "available_dispute_types": [],
        }

    from src.data.repositories.repositories import (
        InvoiceRepository, PaymentRepository, DisputeRepository,
        MemoryEpisodeRepository, MemorySummaryRepository, OpenQuestionRepository,
        DisputeTypeRepository,
    )

    invoice_details      = None
    all_payment_details: List[Dict] = []
    existing_dispute_id  = None
    memory_summary       = None
    recent_episodes      = []
    pending_questions    = []

    if state["matched_invoice_id"]:
        inv_repo = InvoiceRepository(db_session)
        invoice  = await inv_repo.get_by_id(state["matched_invoice_id"])
        if invoice:
            db_details  = invoice.invoice_details or {}
            groq_data   = state.get("groq_extracted") or {}
            invoice_details = {**db_details, **{k: v for k, v in groq_data.items() if v is not None}}

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

    if state["customer_id"] and state["matched_invoice_id"]:
        dispute_repo = DisputeRepository(db_session)
        open_disputes = await dispute_repo.get_by_customer(state["customer_id"])
        matching = [d for d in open_disputes if d.invoice_id == state["matched_invoice_id"]]

        if matching:
            existing_dispute    = matching[0]
            existing_dispute_id = existing_dispute.dispute_id

            ep_repo      = MemoryEpisodeRepository(db_session)
            recent_eps   = await ep_repo.get_latest_n(existing_dispute_id, n=5)
            recent_episodes = [
                {"actor": ep.actor, "type": ep.episode_type, "text": ep.content_text[:400]}
                for ep in recent_eps
            ]

            sum_repo = MemorySummaryRepository(db_session)
            summary  = await sum_repo.get_for_dispute(existing_dispute_id)
            if summary:
                memory_summary = summary.summary_text

            q_repo       = OpenQuestionRepository(db_session)
            pending_qs   = await q_repo.get_pending_for_dispute(existing_dispute_id)
            pending_questions = [
                {"question_id": q.question_id, "text": q.question_text}
                for q in pending_qs
            ]

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

    return {
        **state,
        "invoice_details":        invoice_details,
        "all_payment_details":    all_payment_details,
        "existing_dispute_id":    existing_dispute_id,
        "memory_summary":         memory_summary,
        "recent_episodes":        recent_episodes,
        "pending_questions":      pending_questions,
        "available_dispute_types": available_dispute_types,
    }


async def node_classify_email(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    if not llm_client:
        text_lower = state["all_text"].lower()
        dispute_keywords = ["wrong", "incorrect", "mismatch", "overcharged", "dispute", "error", "short payment"]
        classification = "DISPUTE" if any(k in text_lower for k in dispute_keywords) else "CLARIFICATION"
        return {
            **state,
            "classification":             classification,
            "dispute_type_name":          "Pricing Mismatch" if classification == "DISPUTE" else "General Clarification",
            "priority":                   "MEDIUM",
            "description":                state["body_text"][:500],
            "_answers_pending_questions": [],
        }

    groq_block = ""
    if state.get("groq_extracted"):
        groq_block = f"\nEXTRACTED INVOICE DATA: {json.dumps(state['groq_extracted'])}"

    available_types = state.get("available_dispute_types", [])
    types_details   = "\n".join([
        f"  - {dt['reason_name']}: {dt['description']} (severity: {dt['severity_level']})"
        for dt in available_types
    ])

    payments_summary = ""
    all_pmts = state.get("all_payment_details", [])
    if all_pmts:
        payments_summary = f"\nPAYMENT RECORDS ({len(all_pmts)} total): " + json.dumps(all_pmts)[:600]

    prompt = f"""You are an AR dispute classification expert. Analyze the following customer email and classify it.

EMAIL SUBJECT: {state['subject']}
EMAIL FROM: {state['sender_email']}
EMAIL BODY: {state['body_text'][:1000]}
ATTACHMENT TEXT: {' '.join(state['attachment_texts'])[:500]}
{groq_block}
{payments_summary}

EXISTING DISPUTE CONTEXT (if any):
{state.get('memory_summary') or 'None'}

RECENT CONVERSATION HISTORY:
{json.dumps(state.get('recent_episodes', [])[:3])}

PENDING UNANSWERED QUESTIONS:
{json.dumps(state['pending_questions']) if state['pending_questions'] else 'None'}

AVAILABLE DISPUTE TYPES IN DATABASE:
{types_details if types_details else 'None defined yet'}

Return ONLY valid JSON:
{{
  "classification": "DISPUTE" or "CLARIFICATION",
  "dispute_type_name": "Choose from available types or suggest a new one",
  "is_new_type": true or false,
  "new_type_description": "If is_new_type=true, describe the new type",
  "priority": "LOW" or "MEDIUM" or "HIGH",
  "description": "2-3 sentence summary of the issue",
  "answers_pending_questions": [list of question_ids this email answers]
}}"""

    try:
        response = await llm_client.chat(prompt)
        data     = json.loads(response)
        result = {
            **state,
            "classification":             data.get("classification", "CLARIFICATION"),
            "dispute_type_name":          data.get("dispute_type_name", "General Clarification"),
            "priority":                   data.get("priority", "MEDIUM"),
            "description":                data.get("description", state["body_text"][:500]),
            "_answers_pending_questions": data.get("answers_pending_questions", []),
        }
        if data.get("is_new_type"):
            result["_new_dispute_type"] = {
                "reason_name":    data.get("dispute_type_name"),
                "description":    data.get("new_type_description", ""),
                "severity_level": data.get("priority", "MEDIUM"),
            }
        return result
    except Exception as e:
        logger.error(f"Classification LLM error: {e}")
        return {
            **state,
            "classification":             "CLARIFICATION",
            "dispute_type_name":          "General Clarification",
            "priority":                   "MEDIUM",
            "description":                state["body_text"][:500],
            "_answers_pending_questions": [],
        }


async def node_generate_ai_response(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
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

    invoice_ctx = json.dumps(state.get("invoice_details") or {}, indent=2)
    all_pmts    = state.get("all_payment_details") or []
    payment_ctx = json.dumps(all_pmts, indent=2) if all_pmts else "{}"
    memory_ctx  = state.get("memory_summary") or "No previous conversation"
    recent_eps  = state.get("recent_episodes", [])
    pending_qs  = state.get("pending_questions", [])

    prompt = f"""You are an AR dispute resolution AI assistant. Your job is to analyze customer \
emails and decide whether to auto-respond or escalate to the finance/AR team.

CUSTOMER EMAIL:
Subject: {state['subject']}
From: {state['sender_email']}
Body: {state['body_text'][:800]}

INVOICE CONTEXT:
{invoice_ctx}

PAYMENT RECORDS ({len(all_pmts)} record(s) on file):
{payment_ctx}

CONVERSATION MEMORY:
{memory_ctx}

RECENT EPISODES:
{json.dumps(recent_eps[:3])}

PENDING QUESTIONS:
{json.dumps(pending_qs)}

CLASSIFICATION:
- Type: {state.get('classification')}
- Category: {state.get('dispute_type_name')}
- Priority: {state.get('priority')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DECISION RULES (summary)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ AUTO-RESPOND only for READ-ONLY fact lookups where the answer is clearly
   present in INVOICE CONTEXT or PAYMENT CONTEXT (totals, dates, tax, status).

❌ ESCALATE for any DISPUTE, adjustment request, ambiguity, missing data,
   or HIGH priority email.

When in doubt → escalate.

Return ONLY valid JSON:
{{
  "ai_summary": "2-3 sentence summary of the issue",
  "can_auto_respond": true or false,
  "auto_respond_reason": "one sentence explanation",
  "ai_response": "full draft response to customer",
  "customer_questions_included": true or false,
  "confidence_score": 0.0-1.0,
  "questions_to_ask": ["FA internal question 1", "FA internal question 2"],
  "episodes_referenced": [0, 1],
  "memory_context_used": true or false
}}"""

    try:
        response = await llm_client.chat(prompt)
        data     = json.loads(response)

        logger.info(
            f"[email_id={state['email_id']}] Auto-respond: {data.get('can_auto_respond')} | "
            f"reason: {data.get('auto_respond_reason', 'N/A')}"
        )

        return {
            **state,
            "ai_summary":              data.get("ai_summary", state.get("description", "")),
            "ai_response":             data.get("ai_response"),
            "confidence_score":        data.get("confidence_score", 0.7),
            "auto_response_generated": bool(data.get("can_auto_respond")),
            "questions_to_ask":        data.get("questions_to_ask", []),
            "memory_context_used":     data.get("memory_context_used", False),
            "episodes_referenced":     data.get("episodes_referenced", []),
        }
    except Exception as e:
        logger.error(f"AI response generation error: {e}")
        return {
            **state,
            "ai_summary":              state.get("description", ""),
            "ai_response":             None,
            "confidence_score":        0.5,
            "auto_response_generated": False,
            "questions_to_ask":        [],
            "memory_context_used":     False,
            "episodes_referenced":     [],
        }


# ─── NEW NODE 1: embed_and_search ─────────────────────────────────────────────

async def node_embed_and_search(
    state: EmailProcessingState,
    llm_client=None,
    db_session=None,
) -> EmailProcessingState:
    """
    Embed the ai_summary (produced by generate_ai_response) using the local
    sentence-transformers model and run a pgvector cosine similarity search
    against all past episodes for this customer_id.

    Why ai_summary and not raw email?
    ──────────────────────────────────
    The AI summary is compact and distilled — it captures the customer's intent,
    the relevant invoice/payment context, and the dispute nature without the noise
    of email threading, greetings, signatures, or verbose re-phrasing. Embedding
    this gives a much tighter similarity signal than embedding the raw email body.

    Results are stored in state.similar_episodes and used by the next node
    (resolve_dispute_link) to decide whether to link to an existing dispute or
    ask the customer for more information.
    """
    # Default: no match
    defaults = {
        **state,
        "similar_episodes":    [],
        "embedding_matched":   False,
        "embedding_dispute_id": None,
        "embedding_similarity": 0.0,
    }

    summary_text = state.get("ai_summary", "").strip()
    customer_id  = state.get("customer_id")

    if not summary_text or not customer_id:
        logger.warning(
            f"[email_id={state['email_id']}] embed_and_search skipped: "
            f"summary_text={'empty' if not summary_text else 'ok'}, "
            f"customer_id={customer_id}"
        )
        return defaults

    if not llm_client or not db_session:
        return defaults

    from src.data.repositories.repositories import MemoryEpisodeRepository
    from src.config.settings import settings

    # 1. Generate embedding for the current email's AI summary
    embedding = await llm_client.embed(summary_text)
    if not embedding:
        logger.warning(f"[email_id={state['email_id']}] Embedding generation returned None — skipping search")
        return defaults

    logger.info(f"[email_id={state['email_id']}] Generated embedding (dims={len(embedding)}) for ai_summary")

    # 2. pgvector similarity search scoped to this customer
    ep_repo     = MemoryEpisodeRepository(db_session)
    similar_eps = await ep_repo.search_similar_by_customer(
        customer_id=customer_id,
        query_embedding=embedding,
        top_k=5,
        threshold=settings.EPISODE_SIMILARITY_THRESHOLD,  # default 0.75
    )

    if similar_eps:
        best        = similar_eps[0]
        best_score  = best["similarity"]
        best_dispute = best["dispute_id"]
        logger.info(
            f"[email_id={state['email_id']}] Found {len(similar_eps)} similar episode(s). "
            f"Best: dispute_id={best_dispute}, similarity={best_score}"
        )
        return {
            **state,
            "similar_episodes":     similar_eps,
            "embedding_matched":    True,
            "embedding_dispute_id": best_dispute,
            "embedding_similarity": best_score,
        }

    logger.info(
        f"[email_id={state['email_id']}] No similar episodes found above "
        f"threshold={settings.EPISODE_SIMILARITY_THRESHOLD} for customer={customer_id}"
    )
    return defaults


# ─── NEW NODE 2: resolve_dispute_link ─────────────────────────────────────────

async def node_resolve_dispute_link(
    state: EmailProcessingState,
    llm_client=None,
    db_session=None,
) -> EmailProcessingState:
    """
    Decision node that runs after embed_and_search.

    Three scenarios
    ───────────────
    A) Invoice matched (matched_invoice_id is set)
       → We already have the right context. Proceed normally.
         existing_dispute_id is already set if applicable. No changes needed.

    B) No invoice matched BUT embedding search found a similar past episode
       → Link this email to the matched dispute (embedding_dispute_id).
         Update existing_dispute_id so persist_results attaches to the right dispute.
         Log the link for auditability.

    C) No invoice matched AND no similar episode found
       → We genuinely don't know what the customer is referring to.
         Override ai_response with a polite message asking for invoice details.
         Set auto_response_generated=False so the FA team is also looped in.
         A new dispute will be created by persist_results and assigned to FA.

    The "ask for invoice" response template is intentionally kept short and
    professional — no financial details, no assumptions about the issue.
    """

    invoice_matched   = state.get("matched_invoice_id") is not None
    embedding_matched = state.get("embedding_matched", False)

    # ── Scenario A: invoice already matched ──────────────────────────────────
    if invoice_matched:
        logger.info(
            f"[email_id={state['email_id']}] resolve_dispute_link: "
            f"invoice matched (id={state['matched_invoice_id']}) — no action needed"
        )
        return state

    # ── Scenario B: no invoice but embedding match found ─────────────────────
    if embedding_matched and state.get("embedding_dispute_id"):
        linked_dispute_id = state["embedding_dispute_id"]
        similarity        = state.get("embedding_similarity", 0.0)

        logger.info(
            f"[email_id={state['email_id']}] resolve_dispute_link: "
            f"no invoice match but embedding linked to dispute_id={linked_dispute_id} "
            f"(similarity={similarity})"
        )

        # Load the matched dispute's context to enrich the response if needed
        if db_session:
            from src.data.repositories.repositories import MemoryEpisodeRepository, MemorySummaryRepository
            ep_repo   = MemoryEpisodeRepository(db_session)
            sum_repo  = MemorySummaryRepository(db_session)

            # Fetch recent episodes from the matched dispute for context
            recent_eps = await ep_repo.get_latest_n(linked_dispute_id, n=5)
            recent_episodes_ctx = [
                {"actor": ep.actor, "type": ep.episode_type, "text": ep.content_text[:400]}
                for ep in recent_eps
            ]

            summary_obj = await sum_repo.get_for_dispute(linked_dispute_id)
            memory_summary = summary_obj.summary_text if summary_obj else state.get("memory_summary")

            logger.info(
                f"[email_id={state['email_id']}] Loaded context from linked dispute_id={linked_dispute_id}: "
                f"{len(recent_episodes_ctx)} episodes"
            )

            return {
                **state,
                "existing_dispute_id": linked_dispute_id,   # ← link to matched dispute
                "recent_episodes":     recent_episodes_ctx,  # ← enrich with its history
                "memory_summary":      memory_summary,
            }

        return {**state, "existing_dispute_id": linked_dispute_id}

    # ── Scenario C: no invoice, no embedding match → ask for invoice details ─
    logger.info(
        f"[email_id={state['email_id']}] resolve_dispute_link: "
        f"no invoice and no embedding match — requesting invoice details from customer"
    )

    ask_for_invoice_response = (
        f"Thank you for reaching out to us.\n\n"
        f"We'd like to help you with your query, but we were unable to locate the relevant "
        f"invoice from the details provided. To ensure we look into this promptly and accurately, "
        f"could you please share the following:\n\n"
        f"  1. Invoice number (e.g. INV-2024-001)\n"
        f"  2. Approximate invoice date or the amount on the invoice\n\n"
        f"Once we have these details, our finance/AR team will review your query and get back "
        f"to you as soon as possible. We appreciate your patience."
    )

    # Override the AI response with the invoice-request message.
    # auto_response_generated stays False → persist_results will assign to FA team.
    return {
        **state,
        "ai_response":             ask_for_invoice_response,
        "auto_response_generated": False,   # always escalate when we can't identify the invoice
        "questions_to_ask":        state.get("questions_to_ask", []) + [
            "Customer could not be matched to any invoice or past dispute — "
            "FA to identify the correct invoice once customer replies with details."
        ],
    }


# ─── persist_results ──────────────────────────────────────────────────────────

async def node_persist_results(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Saves dispute, analysis, memory episodes (with embeddings), open questions,
    and email routing.

    Embedding persistence
    ─────────────────────
    After creating the AI_RESPONSE / AI_ACKNOWLEDGEMENT episode, we embed the
    ai_summary and store it in content_embedding on that episode row.
    This means every episode from this point forward is searchable via pgvector.
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

        dispute_id        = state.get("existing_dispute_id")
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
                f"payment_ids={state.get('matched_payment_ids')}"
            )
        else:
            dispute = await DisputeRepository(db_session).get_by_id(dispute_id)
            if dispute and not dispute.payment_detail_id and primary_payment_id:
                dispute.payment_detail_id = primary_payment_id

            log = DisputeActivityLog(
                dispute_id=dispute_id,
                action_type="FOLLOW_UP_EMAIL_RECEIVED",
                notes=f"New email received: {state['subject'][:100]}",
            )
            db_session.add(log)

        # 3. Create AI analysis
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

        # 3a. Auto-register supporting documents
        ref_repo = AnalysisSupportingRefRepository(db_session)

        if state.get("matched_invoice_id"):
            await ref_repo.upsert_supporting_doc(
                analysis_id=analysis.analysis_id,
                reference_table="invoice_data",
                ref_id_value=state["matched_invoice_id"],
                context_note=(
                    f"Invoice {state.get('matched_invoice_number', state['matched_invoice_id'])} "
                    f"identified from email — primary supporting document"
                ),
            )

        for pid in state.get("matched_payment_ids", []):
            await ref_repo.upsert_supporting_doc(
                analysis_id=analysis.analysis_id,
                reference_table="payment_detail",
                ref_id_value=pid,
                context_note=(
                    f"Payment record {pid} for invoice "
                    f"{state.get('matched_invoice_number', '')} — supporting document"
                ),
            )

        # 4. Memory episode – incoming email
        email_episode = DisputeMemoryEpisode(
            dispute_id=dispute_id,
            episode_type="CUSTOMER_EMAIL",
            actor="CUSTOMER",
            content_text=f"Subject: {state['subject']}\n\n{state['body_text'][:1000]}",
            email_id=state["email_id"],
        )
        db_session.add(email_episode)
        await db_session.flush()

        # 5. Memory episode – AI response / acknowledgement
        ai_episode = None
        if state.get("ai_response"):
            ai_episode = DisputeMemoryEpisode(
                dispute_id=dispute_id,
                episode_type="AI_RESPONSE" if state.get("auto_response_generated") else "AI_ACKNOWLEDGEMENT",
                actor="AI",
                content_text=state["ai_response"],
                email_id=state["email_id"],
            )
            db_session.add(ai_episode)
            await db_session.flush()

            if state.get("auto_response_generated"):
                answered_ids = state.get("_answers_pending_questions", [])
                if answered_ids:
                    q_repo = OpenQuestionRepository(db_session)
                    for qid in answered_ids:
                        q = await q_repo.get_by_id(qid)
                        if q and q.status == "PENDING":
                            q.status            = "ANSWERED"
                            q.answered_in_episode_id = ai_episode.episode_id
                            q.answered_at        = datetime.now(timezone.utc)

        # ── 5a. Persist embedding on the AI episode ──────────────────────────
        # We embed the ai_summary (not the full response text) because the summary
        # is what we search against in future emails for this customer.
        # This is the row that will be returned by search_similar_by_customer().
        ai_summary_text = state.get("ai_summary", "").strip()
        if ai_episode and ai_summary_text:
            from src.handlers.http_clients.llm_client import get_llm_client
            try:
                _llm = get_llm_client()
                embedding = await _llm.embed(ai_summary_text)
                if embedding:
                    ep_repo = MemoryEpisodeRepository(db_session)
                    await ep_repo.upsert_embedding(ai_episode.episode_id, embedding)
                    logger.info(
                        f"[email_id={state['email_id']}] Saved embedding "
                        f"(dims={len(embedding)}) on episode_id={ai_episode.episode_id}"
                    )
            except Exception as emb_err:
                # Embedding failure is non-fatal — log and continue
                logger.warning(f"[email_id={state['email_id']}] Failed to save episode embedding: {emb_err}")
        # ─────────────────────────────────────────────────────────────────────

        # 6. Open questions (FA internal investigation tasks)
        for question_text in state.get("questions_to_ask", []):
            question = DisputeOpenQuestion(
                dispute_id=dispute_id,
                asked_in_episode_id=email_episode.episode_id,
                question_text=question_text,
                status="PENDING",
            )
            db_session.add(question)

        # 7. Update email routing
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

        # 8. Auto-assign to FA team if not fully auto-responded
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

        # 9. Trigger episode summarisation if threshold reached
        ep_repo   = MemoryEpisodeRepository(db_session)
        ep_count  = await ep_repo.count_for_dispute(dispute_id)
        from src.config.settings import settings
        if ep_count >= settings.EPISODE_SUMMARIZE_THRESHOLD:
            from src.control.tasks import summarize_episodes_task
            summarize_episodes_task.delay(dispute_id)

        return {**state, "dispute_id": dispute_id, "analysis_id": analysis.analysis_id}

    except Exception as e:
        logger.error(f"Persist error for email_id={state['email_id']}: {e}", exc_info=True)
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

    graph.add_node("extract_text",                 node_extract_text)
    graph.add_node("extract_invoice_data_via_groq", partial(node_extract_invoice_data_via_groq, llm_client=llm_client))
    graph.add_node("identify_invoice",              partial(node_identify_invoice,              db_session=db_session))
    graph.add_node("fetch_context",                 partial(node_fetch_context,                 db_session=db_session))
    graph.add_node("classify_email",                partial(node_classify_email,                llm_client=llm_client))
    graph.add_node("generate_ai_response",          partial(node_generate_ai_response,          llm_client=llm_client))
    graph.add_node("embed_and_search",              partial(node_embed_and_search,              llm_client=llm_client, db_session=db_session))
    graph.add_node("resolve_dispute_link",          partial(node_resolve_dispute_link,          llm_client=llm_client, db_session=db_session))
    graph.add_node("persist_results",               partial(node_persist_results,               db_session=db_session))

    graph.set_entry_point("extract_text")
    graph.add_edge("extract_text",                  "extract_invoice_data_via_groq")
    graph.add_edge("extract_invoice_data_via_groq", "identify_invoice")
    graph.add_edge("identify_invoice",              "fetch_context")
    graph.add_edge("fetch_context",                 "classify_email")
    graph.add_edge("classify_email",                "generate_ai_response")
    graph.add_edge("generate_ai_response",          "embed_and_search")       # NEW
    graph.add_edge("embed_and_search",              "resolve_dispute_link")   # NEW
    graph.add_edge("resolve_dispute_link",          "persist_results")
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
        "invoice_details":        None,
        "all_payment_details":    [],
        "existing_dispute_id":    None,
        "memory_summary":         None,
        "recent_episodes":        [],
        "pending_questions":      [],
        "available_dispute_types": [],
        "classification":         "UNKNOWN",
        "dispute_type_name":      "General Clarification",
        "priority":               "MEDIUM",
        "description":            "",
        "ai_summary":             "",
        "ai_response":            None,
        "confidence_score":       0.0,
        "auto_response_generated": False,
        "questions_to_ask":       [],
        "memory_context_used":    False,
        "episodes_referenced":    [],
        "_answers_pending_questions": [],
        # NEW fields
        "similar_episodes":       [],
        "embedding_matched":      False,
        "embedding_dispute_id":   None,
        "embedding_similarity":   0.0,
        # Final
        "dispute_id":             None,
        "analysis_id":            None,
        "error":                  None,
    }

    return await graph.ainvoke(initial_state)