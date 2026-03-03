"""
LangGraph-based email processing agent (Groq edition).

Pipeline:
  extract_text
      ↓
  extract_invoice_data_via_groq   ← NEW: Groq intelligently extracts all invoice fields
      ↓
  identify_invoice                ← matches extracted invoice_number against DB
      ↓
  fetch_context                   ← loads invoice + payment records + memory (ENHANCED: uses memory episodes for matching)
      ↓
  classify_email                  ← DISPUTE | CLARIFICATION | UNKNOWN (ENHANCED: dynamic dispute types from DB)
      ↓
  generate_ai_response            ← tries to auto-respond
      ↓
  persist_results                 ← saves everything to DB (ENHANCED: creates new dispute types if needed)
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

    # Groq-extracted invoice data  (NEW)
    groq_extracted: Optional[Dict]           # raw dict from Groq extraction
    candidate_invoice_numbers: List[str]     # pulled from groq_extracted + regex fallback

    # DB-matched
    matched_invoice_id: Optional[int]
    matched_invoice_number: Optional[str]
    matched_payment_id: Optional[int]
    customer_id: Optional[str]
    routing_confidence: float

    # Context
    invoice_details: Optional[Dict]
    payment_details: Optional[Dict]
    existing_dispute_id: Optional[int]
    memory_summary: Optional[str]
    recent_episodes: List[Dict]
    pending_questions: List[Dict]

    # Available dispute types (NEW)
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

    # Final
    dispute_id: Optional[int]
    analysis_id: Optional[int]
    error: Optional[str]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _build_full_text(state: EmailProcessingState) -> str:
    parts = [state["subject"], state["body_text"]] + state["attachment_texts"]
    return "\n\n".join(filter(None, parts))


def _regex_invoice_numbers(text: str) -> List[str]:
    """Fallback regex extraction in case Groq can't find a number."""
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
    all_text = _build_full_text(state)
    return {**state, "all_text": all_text}


async def node_extract_invoice_data_via_groq(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    """
    NEW NODE: Send all text to Groq and extract invoice data intelligently.
    This gives us a structured dict with invoice_number, totals, line_items, etc.
    The extracted data is stored in groq_extracted and also used for DB matching.
    """
    groq_extracted: Optional[Dict] = None
    candidates: List[str] = []

    if llm_client:
        try:
            groq_extracted = await llm_client.extract_invoice_data(state["all_text"])
            # Pull invoice number from Groq result (primary)
            inv_num = groq_extracted.get("invoice_number")
            if inv_num:
                candidates.append(str(inv_num).upper().strip())
            # Also try PO number as secondary candidate
            po = groq_extracted.get("po_number")
            if po:
                candidates.append(str(po).upper().strip())
        except Exception as e:
            logger.warning(f"[email_id={state['email_id']}] Groq invoice extraction failed: {e}. Falling back to regex.")

    # Regex fallback
    regex_candidates = _regex_invoice_numbers(state["all_text"])
    for c in regex_candidates:
        if c not in candidates:
            candidates.append(c)

    logger.info(f"[email_id={state['email_id']}] Invoice candidates: {candidates}")
    return {
        **state,
        "groq_extracted": groq_extracted,
        "candidate_invoice_numbers": candidates,
    }


async def node_identify_invoice(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    if not db_session:
        return {**state, "matched_invoice_id": None, "routing_confidence": 0.0}

    from src.data.repositories.repositories import InvoiceRepository, PaymentRepository
    inv_repo = InvoiceRepository(db_session)
    pay_repo = PaymentRepository(db_session)

    matched_invoice = None
    matched_payment = None
    confidence = 0.0

    # Exact match
    for candidate in state["candidate_invoice_numbers"]:
        invoice = await inv_repo.get_by_invoice_number(candidate)
        if invoice:
            matched_invoice = invoice
            confidence = 0.95
            break

    # Fuzzy match fallback
    if not matched_invoice and state["candidate_invoice_numbers"]:
        for candidate in state["candidate_invoice_numbers"]:
            results = await inv_repo.search_by_number_fuzzy(candidate)
            if results:
                matched_invoice = results[0]
                confidence = 0.65
                break

    # Derive customer_id from Groq extraction first, then sender email domain
    customer_id = state.get("customer_id")
    if not customer_id and state.get("groq_extracted"):
        customer_id = state["groq_extracted"].get("customer_id") or state["groq_extracted"].get("customer_name")
    if not customer_id:
        sender = state["sender_email"]
        domain = sender.split("@")[-1].split(".")[0] if "@" in sender else sender
        customer_id = domain

    # Try to find matching payment - ENHANCED: This ensures payment_detail gets joined
    if matched_invoice and customer_id:
        matched_payment = await pay_repo.get_by_customer_and_invoice(
            customer_id, matched_invoice.invoice_number
        )
        if matched_payment:
            logger.info(f"[email_id={state['email_id']}] Matched payment_detail_id={matched_payment.payment_detail_id} for invoice={matched_invoice.invoice_number}")

    return {
        **state,
        "matched_invoice_id": matched_invoice.invoice_id if matched_invoice else None,
        "matched_invoice_number": matched_invoice.invoice_number if matched_invoice else None,
        "matched_payment_id": matched_payment.payment_detail_id if matched_payment else None,
        "customer_id": customer_id,
        "routing_confidence": confidence,
    }


async def node_fetch_context(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    ENHANCED: Now uses memory episodes for better dispute matching.
    Memory episodes provide context about previous interactions for the same invoice.
    """
    if not db_session:
        return {**state, "invoice_details": None, "payment_details": None, "available_dispute_types": []}

    from src.data.repositories.repositories import (
        InvoiceRepository, PaymentRepository, DisputeRepository,
        MemoryEpisodeRepository, MemorySummaryRepository, OpenQuestionRepository,
        DisputeTypeRepository,
    )

    invoice_details = None
    payment_details = None
    existing_dispute_id = None
    memory_summary = None
    recent_episodes = []
    pending_questions = []

    if state["matched_invoice_id"]:
        inv_repo = InvoiceRepository(db_session)
        invoice = await inv_repo.get_by_id(state["matched_invoice_id"])
        if invoice:
            # Merge DB invoice_details with Groq-extracted data for richer context
            db_details = invoice.invoice_details or {}
            groq_data = state.get("groq_extracted") or {}
            # Groq wins on fields it found; DB fills the rest
            invoice_details = {**db_details, **{k: v for k, v in groq_data.items() if v is not None}}

    if state["matched_payment_id"]:
        pay_repo = PaymentRepository(db_session)
        payment = await pay_repo.get_by_id(state["matched_payment_id"])
        if payment:
            payment_details = payment.payment_details

    # ENHANCED: Existing open disputes for customer + invoice
    # Now uses memory episodes to better understand dispute history
    if state["customer_id"] and state["matched_invoice_id"]:
        dispute_repo = DisputeRepository(db_session)
        open_disputes = await dispute_repo.get_by_customer(state["customer_id"])
        matching = [d for d in open_disputes if d.invoice_id == state["matched_invoice_id"]]
        
        if matching:
            existing_dispute = matching[0]
            existing_dispute_id = existing_dispute.dispute_id
            logger.info(f"[email_id={state['email_id']}] Found existing dispute_id={existing_dispute_id} for customer={state['customer_id']}, invoice_id={state['matched_invoice_id']}")

            # Fetch memory episodes for context
            ep_repo = MemoryEpisodeRepository(db_session)
            recent_eps = await ep_repo.get_latest_n(existing_dispute_id, n=5)
            recent_episodes = [
                {"actor": ep.actor, "type": ep.episode_type, "text": ep.content_text[:400]}
                for ep in recent_eps
            ]
            logger.info(f"[email_id={state['email_id']}] Loaded {len(recent_episodes)} recent episodes from dispute memory")

            sum_repo = MemorySummaryRepository(db_session)
            summary = await sum_repo.get_for_dispute(existing_dispute_id)
            if summary:
                memory_summary = summary.summary_text

            q_repo = OpenQuestionRepository(db_session)
            pending_qs = await q_repo.get_pending_for_dispute(existing_dispute_id)
            pending_questions = [
                {"question_id": q.question_id, "text": q.question_text}
                for q in pending_qs
            ]

    # ENHANCED: Fetch all active dispute types from DB for dynamic classification
    dtype_repo = DisputeTypeRepository(db_session)
    all_types = await dtype_repo.get_active_types()
    available_dispute_types = [
        {"reason_name": dt.reason_name, "description": dt.description or "", "severity_level": dt.severity_level or "MEDIUM"}
        for dt in all_types
    ]
    logger.info(f"[email_id={state['email_id']}] Loaded {len(available_dispute_types)} active dispute types from DB")

    return {
        **state,
        "invoice_details": invoice_details,
        "payment_details": payment_details,
        "existing_dispute_id": existing_dispute_id,
        "memory_summary": memory_summary,
        "recent_episodes": recent_episodes,
        "pending_questions": pending_questions,
        "available_dispute_types": available_dispute_types,
    }


async def node_classify_email(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    """
    ENHANCED: Now dynamically fetches dispute types from DB and sends to LLM.
    LLM can suggest new dispute types if none match.
    """
    if not llm_client:
        text_lower = state["all_text"].lower()
        dispute_keywords = ["wrong", "incorrect", "mismatch", "overcharged", "dispute", "error", "short payment"]
        classification = "DISPUTE" if any(k in text_lower for k in dispute_keywords) else "CLARIFICATION"
        return {
            **state,
            "classification": classification,
            "dispute_type_name": "Pricing Mismatch" if classification == "DISPUTE" else "General Clarification",
            "priority": "MEDIUM",
            "description": state["body_text"][:500],
            "_answers_pending_questions": [],
        }

    # Build extracted data block
    groq_block = ""
    if state.get("groq_extracted"):
        groq_block = f"\nEXTRACTED INVOICE DATA: {json.dumps(state['groq_extracted'])}"

    # Build available dispute types list
    available_types = state.get("available_dispute_types", [])
    types_list = [dt["reason_name"] for dt in available_types]
    types_details = "\n".join([
        f"  - {dt['reason_name']}: {dt['description']} (severity: {dt['severity_level']})"
        for dt in available_types
    ])

    prompt = f"""You are an AR dispute classification expert. Analyze the following customer email and classify it.

EMAIL SUBJECT: {state['subject']}
EMAIL FROM: {state['sender_email']}
EMAIL BODY: {state['body_text'][:1000]}
ATTACHMENT TEXT: {' '.join(state['attachment_texts'])[:500]}
{groq_block}

EXISTING DISPUTE CONTEXT (if any):
{state.get('memory_summary') or 'None'}

RECENT CONVERSATION HISTORY:
{json.dumps(state.get('recent_episodes', [])[:3])}

PENDING UNANSWERED QUESTIONS:
{json.dumps(state['pending_questions']) if state['pending_questions'] else 'None'}

AVAILABLE DISPUTE TYPES IN DATABASE:
{types_details if types_details else 'None defined yet'}

Return ONLY valid JSON with these exact keys:
{{
  "classification": "DISPUTE" or "CLARIFICATION",
  "dispute_type_name": "Choose from the available dispute types above, or suggest a NEW type name if none match (e.g., 'Payment Terms Dispute', 'Delivery Issue')",
  "is_new_type": true if suggesting a new type name, false otherwise,
  "new_type_description": "If is_new_type=true, provide a 1-2 sentence description of what this new dispute type covers",
  "priority": "LOW" or "MEDIUM" or "HIGH",
  "description": "2-3 sentence summary of the issue",
  "answers_pending_questions": [list of question_ids from pending questions that this email answers, e.g. [1, 3]]
}}

If the issue doesn't match any existing dispute type, create a meaningful new type name that captures the nature of the dispute."""

    try:
        response = await llm_client.chat(prompt)
        data = json.loads(response)
        
        # Store the classification result including new type flag
        result = {
            **state,
            "classification": data.get("classification", "CLARIFICATION"),
            "dispute_type_name": data.get("dispute_type_name", "General Clarification"),
            "priority": data.get("priority", "MEDIUM"),
            "description": data.get("description", state["body_text"][:500]),
            "_answers_pending_questions": data.get("answers_pending_questions", []),
        }
        
        # Add metadata for new dispute type creation
        if data.get("is_new_type"):
            result["_new_dispute_type"] = {
                "reason_name": data.get("dispute_type_name"),
                "description": data.get("new_type_description", ""),
                "severity_level": data.get("priority", "MEDIUM"),
            }
            logger.info(f"[email_id={state['email_id']}] LLM suggested new dispute type: {data.get('dispute_type_name')}")
        
        return result
        
    except Exception as e:
        logger.error(f"Classification LLM error: {e}")
        return {
            **state,
            "classification": "CLARIFICATION",
            "dispute_type_name": "General Clarification",
            "priority": "MEDIUM",
            "description": state["body_text"][:500],
            "_answers_pending_questions": [],
        }


async def node_generate_ai_response(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    if not llm_client:
        return {
            **state,
            "ai_summary": state.get("description", "Email processed."),
            "ai_response": None,
            "confidence_score": 0.5,
            "auto_response_generated": False,
            "questions_to_ask": [],
            "memory_context_used": False,
            "episodes_referenced": [],
        }

    # Build context
    invoice_ctx = json.dumps(state.get("invoice_details") or {}, indent=2)
    payment_ctx = json.dumps(state.get("payment_details") or {}, indent=2)
    memory_ctx = state.get("memory_summary") or "No previous conversation"
    recent_eps = state.get("recent_episodes", [])
    pending_qs = state.get("pending_questions", [])

    prompt = f"""You are an AR dispute resolution AI assistant. Generate a professional response.

CUSTOMER EMAIL:
Subject: {state['subject']}
From: {state['sender_email']}
Body: {state['body_text'][:800]}

INVOICE CONTEXT:
{invoice_ctx}

PAYMENT CONTEXT:
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

Your task:
1. Summarize the issue in 2-3 sentences
2. If you can fully resolve it with available data → draft a response email
3. If not → list questions needed (avoid duplicating pending questions)
4. Note which memory episodes you referenced (by position, e.g. [0, 1])

Return ONLY valid JSON:
{{
  "ai_summary": "Brief summary of the issue",
  "can_auto_respond": true/false,
  "ai_response": "Draft response email if can_auto_respond=true, else null",
  "confidence_score": 0.0-1.0,
  "questions_to_ask": ["question1", "question2"],
  "episodes_referenced": [0, 1],
  "memory_context_used": true/false
}}"""

    try:
        response = await llm_client.chat(prompt)
        data = json.loads(response)

        return {
            **state,
            "ai_summary": data.get("ai_summary", state.get("description", "")),
            "ai_response": data.get("ai_response") if data.get("can_auto_respond") else None,
            "confidence_score": data.get("confidence_score", 0.7),
            "auto_response_generated": bool(data.get("can_auto_respond")),
            "questions_to_ask": data.get("questions_to_ask", []),
            "memory_context_used": data.get("memory_context_used", False),
            "episodes_referenced": data.get("episodes_referenced", []),
        }
    except Exception as e:
        logger.error(f"AI response generation error: {e}")
        return {
            **state,
            "ai_summary": state.get("description", ""),
            "ai_response": None,
            "confidence_score": 0.5,
            "auto_response_generated": False,
            "questions_to_ask": [],
            "memory_context_used": False,
            "episodes_referenced": [],
        }


async def node_persist_results(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    ENHANCED: Now creates new dispute types if LLM suggested one that doesn't exist.
    Also ensures payment_detail_id is properly associated with disputes.
    """
    if not db_session:
        return state
 
    from src.data.repositories.repositories import (
        DisputeTypeRepository, DisputeRepository, EmailRepository,
        MemoryEpisodeRepository, OpenQuestionRepository, UserRepository,
    )
    from src.data.models.postgres.models import (
        DisputeMaster, DisputeAIAnalysis, DisputeType,
        DisputeMemoryEpisode, DisputeOpenQuestion,
        DisputeActivityLog, DisputeAssignment,
    )
    from sqlalchemy import update as sa_update
    from src.data.models.postgres.models import EmailInbox

    try:
        # 1. Resolve or create dispute type (ENHANCED)
        dtype_repo = DisputeTypeRepository(db_session)
        dispute_type = await dtype_repo.get_by_name(state["dispute_type_name"])
        
        if not dispute_type:
            # Check if LLM suggested a new type
            new_type_data = state.get("_new_dispute_type")
            if new_type_data:
                # Create the new dispute type
                dispute_type = DisputeType(
                    reason_name=new_type_data["reason_name"],
                    description=new_type_data.get("description", ""),
                    severity_level=new_type_data.get("severity_level", "MEDIUM"),
                    is_active=True
                )
                db_session.add(dispute_type)
                await db_session.flush()
                logger.info(f"[email_id={state['email_id']}] Created new dispute type: {dispute_type.reason_name}")
            else:
                # Fallback to General Clarification
                dispute_type = await dtype_repo.get_by_name("General Clarification")
                if not dispute_type:
                    # Create General Clarification if it doesn't exist
                    dispute_type = DisputeType(
                        reason_name="General Clarification",
                        description="General inquiries and clarification requests",
                        severity_level="LOW",
                        is_active=True
                    )
                    db_session.add(dispute_type)
                    await db_session.flush()

        dispute_id = state.get("existing_dispute_id")

        # 2. Create or reuse dispute (ENHANCED: ensures payment_detail_id is set)
        if not dispute_id:
            dispute = DisputeMaster(
                email_id=state["email_id"],
                invoice_id=state.get("matched_invoice_id"),
                payment_detail_id=state.get("matched_payment_id"),  # VERIFIED: Payment detail joins here
                customer_id=state["customer_id"] or "unknown",
                dispute_type_id=dispute_type.dispute_type_id,
                status="OPEN",
                priority=state.get("priority", "MEDIUM"),
                description=state.get("description", ""),
            )
            db_session.add(dispute)
            await db_session.flush()
            dispute_id = dispute.dispute_id
            logger.info(f"[email_id={state['email_id']}] Created new dispute_id={dispute_id} with payment_detail_id={state.get('matched_payment_id')}")
        else:
            # For existing disputes, ensure payment_detail is updated if it wasn't set before
            dispute = await DisputeRepository(db_session).get_by_id(dispute_id)
            if dispute and not dispute.payment_detail_id and state.get("matched_payment_id"):
                dispute.payment_detail_id = state.get("matched_payment_id")
                logger.info(f"[email_id={state['email_id']}] Updated existing dispute_id={dispute_id} with payment_detail_id={state.get('matched_payment_id')}")
            
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

        # 5. Memory episode – AI response (if any)
        if state.get("ai_response"):
            ai_episode = DisputeMemoryEpisode(
                dispute_id=dispute_id,
                episode_type="AI_RESPONSE",
                actor="AI",
                content_text=state["ai_response"],
                email_id=state["email_id"],
            )
            db_session.add(ai_episode)
            await db_session.flush()

            # Mark answered questions
            answered_ids = state.get("_answers_pending_questions", [])
            if answered_ids:
                q_repo = OpenQuestionRepository(db_session)
                for qid in answered_ids:
                    q = await q_repo.get_by_id(qid)
                    if q and q.status == "PENDING":
                        q.status = "ANSWERED"
                        q.answered_in_episode_id = ai_episode.episode_id
                        q.answered_at = datetime.now(timezone.utc)

        # 6. Open questions
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

        # 8. Auto-assign if not auto-responded
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

        # 9. Trigger summarization if episode threshold reached
        ep_repo = MemoryEpisodeRepository(db_session)
        ep_count = await ep_repo.count_for_dispute(dispute_id)
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

    graph.add_node("extract_text",                node_extract_text)
    graph.add_node("extract_invoice_data_via_groq", partial(node_extract_invoice_data_via_groq, llm_client=llm_client))
    graph.add_node("identify_invoice",             partial(node_identify_invoice,        db_session=db_session))
    graph.add_node("fetch_context",                partial(node_fetch_context,            db_session=db_session))
    graph.add_node("classify_email",               partial(node_classify_email,           llm_client=llm_client))
    graph.add_node("generate_ai_response",         partial(node_generate_ai_response,     llm_client=llm_client))
    graph.add_node("persist_results",              partial(node_persist_results,          db_session=db_session))

    graph.set_entry_point("extract_text")
    graph.add_edge("extract_text",                "extract_invoice_data_via_groq")
    graph.add_edge("extract_invoice_data_via_groq", "identify_invoice")
    graph.add_edge("identify_invoice",            "fetch_context")
    graph.add_edge("fetch_context",               "classify_email")
    graph.add_edge("classify_email",              "generate_ai_response")
    graph.add_edge("generate_ai_response",        "persist_results")
    graph.add_edge("persist_results",             END)

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
        "email_id": email_id,
        "sender_email": sender_email,
        "subject": subject,
        "body_text": body_text,
        "attachment_texts": attachment_texts,
        "all_text": "",
        "groq_extracted": None,
        "candidate_invoice_numbers": [],
        "matched_invoice_id": None,
        "matched_invoice_number": None,
        "matched_payment_id": None,
        "customer_id": None,
        "routing_confidence": 0.0,
        "invoice_details": None,
        "payment_details": None,
        "existing_dispute_id": None,
        "memory_summary": None,
        "recent_episodes": [],
        "pending_questions": [],
        "available_dispute_types": [],
        "classification": "UNKNOWN",
        "dispute_type_name": "General Clarification",
        "priority": "MEDIUM",
        "description": "",
        "ai_summary": "",
        "ai_response": None,
        "confidence_score": 0.0,
        "auto_response_generated": False,
        "questions_to_ask": [],
        "memory_context_used": False,
        "episodes_referenced": [],
        "_answers_pending_questions": [],
        "dispute_id": None,
        "analysis_id": None,
        "error": None,
    }

    result = await graph.ainvoke(initial_state)
    return result