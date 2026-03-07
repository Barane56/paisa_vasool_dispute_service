"""
LangGraph-based email processing agent (Groq edition).

Pipeline:
  extract_text
      ↓
  extract_invoice_data_via_groq   ← Groq intelligently extracts all invoice fields
      ↓
  identify_invoice                ← matches invoice_number against DB; fetches ALL payment records for that invoice
      ↓
  fetch_context                   ← loads invoice + ALL payments + memory episodes
      ↓
  classify_email                  ← DISPUTE | CLARIFICATION | UNKNOWN (dynamic dispute types from DB)
      ↓
  generate_ai_response            ← conservative auto-response logic with full supporting doc context
      ↓
  persist_results                 ← saves dispute/analysis to DB + auto-links invoice & payments
                                    as AnalysisSupportingRefs (supporting documents) on the analysis

Key design notes
────────────────
• Payment identification uses get_all_by_invoice_number (invoice-number match) rather than
  a customer_id string match, which was fragile because the DB stores full company names
  while the agent was deriving an email domain.  Invoice number is the reliable join key.
• Supporting documents (invoice_data + payment_detail rows) are automatically registered
  in analysis_supporting_refs after every analysis so the FA team can see exactly which
  DB records back the AI's conclusions.
• State carries matched_payment_ids: List[int] (all payments) instead of a single id.
• The typo `return results` at the end of run_email_processing has been fixed to `return result`.
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
    matched_payment_ids: List[int]        # ALL payment_detail_ids for the invoice
    customer_id: Optional[str]
    routing_confidence: float

    # Context
    invoice_details: Optional[Dict]
    all_payment_details: List[Dict]       # ALL payment records for the invoice
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
    """
    Matches the invoice against the DB using candidate invoice numbers.

    Payment identification fix
    ──────────────────────────
    The previous approach called get_by_customer_and_invoice(customer_id, invoice_number)
    where customer_id was derived from the sender's email domain (e.g. "techsoft").
    The DB stores full company names ("TechSoft Solutions"), so that join almost never
    matched.  Invoice number is the reliable anchor — once we have a confirmed invoice
    match we simply fetch ALL payment_detail rows for that invoice number directly.
    """
    if not db_session:
        return {**state, "matched_invoice_id": None, "routing_confidence": 0.0}

    from src.data.repositories.repositories import InvoiceRepository, PaymentRepository
    inv_repo = InvoiceRepository(db_session)
    pay_repo = PaymentRepository(db_session)

    matched_invoice = None
    confidence = 0.0

    # 1. Exact match on every candidate
    for candidate in state["candidate_invoice_numbers"]:
        invoice = await inv_repo.get_by_invoice_number(candidate)
        if invoice:
            matched_invoice = invoice
            confidence = 0.95
            break

    # 2. Fuzzy fallback
    if not matched_invoice and state["candidate_invoice_numbers"]:
        for candidate in state["candidate_invoice_numbers"]:
            results = await inv_repo.search_by_number_fuzzy(candidate)
            if results:
                matched_invoice = results[0]
                confidence = 0.65
                break

    # 3. Derive customer_id (for dispute lookup / memory, NOT for payment matching)
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

    # 4. Fetch ALL payment records keyed by invoice_number (reliable join)
    matched_payment_ids: List[int] = []
    if matched_invoice:
        payments = await pay_repo.get_all_by_invoice_number(matched_invoice.invoice_number)
        matched_payment_ids = [p.payment_detail_id for p in payments]
        if matched_payment_ids:
            logger.info(
                f"[email_id={state['email_id']}] Matched {len(matched_payment_ids)} payment(s) "
                f"for invoice={matched_invoice.invoice_number}: ids={matched_payment_ids}"
            )
        else:
            logger.info(
                f"[email_id={state['email_id']}] No payment records found "
                f"for invoice={matched_invoice.invoice_number}"
            )

    return {
        **state,
        "matched_invoice_id": matched_invoice.invoice_id if matched_invoice else None,
        "matched_invoice_number": matched_invoice.invoice_number if matched_invoice else None,
        "matched_payment_ids": matched_payment_ids,
        "customer_id": customer_id,
        "routing_confidence": confidence,
    }


async def node_fetch_context(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Loads invoice details, ALL associated payment records, dispute memory, and
    available dispute types.  All payment records are passed downstream so the
    LLM has the complete payment picture (partial payments, chargebacks, etc.).
    """
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

    invoice_details = None
    all_payment_details: List[Dict] = []
    existing_dispute_id = None
    memory_summary = None
    recent_episodes = []
    pending_questions = []

    # ── Invoice ──────────────────────────────────────────────────────────────
    if state["matched_invoice_id"]:
        inv_repo = InvoiceRepository(db_session)
        invoice = await inv_repo.get_by_id(state["matched_invoice_id"])
        if invoice:
            db_details = invoice.invoice_details or {}
            groq_data = state.get("groq_extracted") or {}
            # Groq wins on fields it found; DB fills the rest
            invoice_details = {**db_details, **{k: v for k, v in groq_data.items() if v is not None}}

    # ── All payments for this invoice ────────────────────────────────────────
    if state.get("matched_payment_ids"):
        pay_repo = PaymentRepository(db_session)
        for pid in state["matched_payment_ids"]:
            payment = await pay_repo.get_by_id(pid)
            if payment and payment.payment_details:
                all_payment_details.append({
                    "payment_detail_id": payment.payment_detail_id,
                    "invoice_number": payment.invoice_number,
                    **payment.payment_details,
                })
        logger.info(
            f"[email_id={state['email_id']}] Loaded {len(all_payment_details)} "
            f"payment record(s) for invoice={state.get('matched_invoice_number')}"
        )

    # ── Existing open disputes for customer + invoice ─────────────────────────
    if state["customer_id"] and state["matched_invoice_id"]:
        dispute_repo = DisputeRepository(db_session)
        open_disputes = await dispute_repo.get_by_customer(state["customer_id"])
        matching = [d for d in open_disputes if d.invoice_id == state["matched_invoice_id"]]

        if matching:
            existing_dispute = matching[0]
            existing_dispute_id = existing_dispute.dispute_id
            logger.info(
                f"[email_id={state['email_id']}] Found existing dispute_id={existing_dispute_id} "
                f"for customer={state['customer_id']}, invoice_id={state['matched_invoice_id']}"
            )

            ep_repo = MemoryEpisodeRepository(db_session)
            recent_eps = await ep_repo.get_latest_n(existing_dispute_id, n=5)
            recent_episodes = [
                {"actor": ep.actor, "type": ep.episode_type, "text": ep.content_text[:400]}
                for ep in recent_eps
            ]
            logger.info(
                f"[email_id={state['email_id']}] Loaded {len(recent_episodes)} "
                f"recent episodes from dispute memory"
            )

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

    # ── Active dispute types ──────────────────────────────────────────────────
    dtype_repo = DisputeTypeRepository(db_session)
    all_types = await dtype_repo.get_active_types()
    available_dispute_types = [
        {
            "reason_name": dt.reason_name,
            "description": dt.description or "",
            "severity_level": dt.severity_level or "MEDIUM",
        }
        for dt in all_types
    ]
    logger.info(
        f"[email_id={state['email_id']}] Loaded {len(available_dispute_types)} "
        f"active dispute types from DB"
    )

    return {
        **state,
        "invoice_details": invoice_details,
        "all_payment_details": all_payment_details,
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
    types_details = "\n".join([
        f"  - {dt['reason_name']}: {dt['description']} (severity: {dt['severity_level']})"
        for dt in available_types
    ])

    # Summarise payments for context (avoid dumping huge JSON)
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
        print(data)
        result = {
            **state,
            "classification": data.get("classification", "CLARIFICATION"),
            "dispute_type_name": data.get("dispute_type_name", "General Clarification"),
            "priority": data.get("priority", "MEDIUM"),
            "description": data.get("description", state["body_text"][:500]),
            "_answers_pending_questions": data.get("answers_pending_questions", []),
        }

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
    all_pmts = state.get("all_payment_details") or []
    payment_ctx = json.dumps(all_pmts, indent=2) if all_pmts else "{}"
    memory_ctx = state.get("memory_summary") or "No previous conversation"
    recent_eps = state.get("recent_episodes", [])
    pending_qs = state.get("pending_questions", [])

#     # ── UPDATED PROMPT ────────────────────────────────────────────────────────
#     prompt = f"""You are an AR dispute resolution AI assistant. Your job is to analyze customer \
# emails and decide whether to auto-respond or escalate to the finance/AR team.

# CUSTOMER EMAIL:
# Subject: {state['subject']}
# From: {state['sender_email']}
# Body: {state['body_text'][:800]}

# INVOICE CONTEXT:
# {invoice_ctx}

# PAYMENT RECORDS ({len(all_pmts)} record(s) on file):
# {payment_ctx}

# CONVERSATION MEMORY:
# {memory_ctx}

# RECENT EPISODES:
# {json.dumps(recent_eps[:3])}

# PENDING QUESTIONS:
# {json.dumps(pending_qs)}

# CLASSIFICATION:
# - Type: {state.get('classification')}
# - Category: {state.get('dispute_type_name')}
# - Priority: {state.get('priority')}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CRITICAL DECISION RULE — READ CAREFULLY:
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# You must be VERY conservative about auto-responding. Even if you have all the data
# in context, DO NOT auto-respond if the query touches any of the following:

# ❌ NEVER auto-respond (set can_auto_respond=false) when the email involves:
#   - Any disputed amount, short payment, overpayment, or refund request
#   - Any request to adjust, credit, or waive a charge (even partially)
#   - Payment deadline extensions or changes to agreed payment terms
#   - Penalty, interest, or late fee disputes
#   - Contract terms or pricing agreement disputes
#   - Any scenario where acting on the response could result in financial loss or liability
#   - Multi-invoice disputes or bulk adjustments
#   - Any email classified as HIGH priority
#   - Legal language, escalation threats, or mentions of legal action
#   - Cases where the customer states a different amount than what is on record
#   - Any ambiguity about whether a payment was received or applied correctly

# ✅ ONLY auto-respond (can_auto_respond=true) for purely factual / informational queries:
#   - "What is the tax rate applied on this invoice?" → answer from invoice data only
#   - "What discount was applied on invoice X?" → answer from invoice data only
#   - "What are your accepted payment methods?" → standard factual info
#   - "Can you resend the invoice?" → acknowledgement only, no financial details
#   - "What is the due date on invoice X?" → factual date from record
#   - "Who is the account manager for our account?" → factual contact info from record
#   - Simple acknowledgements where NO financial commitment or decision is being made

# When in doubt → set can_auto_respond=false. It is always safer to escalate.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESPONSE DRAFTING RULES:
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# If can_auto_respond=true (safe, informational only):
#   - Answer the specific factual question using ONLY data present in INVOICE CONTEXT
#     or PAYMENT CONTEXT above — do not invent or infer values
#   - Be concise and professional
#   - Do NOT make promises, adjustments, or commitments of any kind
#   - Close with: "If you have any further questions, please don't hesitate to reach out."

# If can_auto_respond=false (sensitive / financial / dispute / uncertain):
#   - Draft a polite acknowledgement ONLY — do NOT attempt to resolve, answer, or
#     comment on the dispute details in the response
#   - Use a structure similar to this (adapt wording naturally to context):

#       "Thank you for reaching out regarding [brief neutral topic description].
#       We have received your query and our finance/AR team will carefully review
#       the details and get back to you shortly. If you have any additional
#       information or supporting documents related to this matter, please feel
#       free to share them. We appreciate your patience."

#   - NEVER include amounts, dates, percentages, or any financial figures in this
#     acknowledgement response — even if you can see them in context
#   - In questions_to_ask, list the specific questions the FA team will need to
#     investigate to resolve this (these are for internal use, NOT sent to customer)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CUSTOMER-FACING CLARIFICATION QUESTIONS:
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Sometimes the customer's email is missing information we genuinely need before
# the FA team can even begin to investigate. In those cases ONLY, you may append
# 1-2 short, polite clarifying questions at the end of the acknowledgement response.

# Rules for including customer-facing questions:
#   ✅ Include a question ONLY if the answer is not already present anywhere in the
#      email, attachments, invoice context, or conversation history
#   ✅ Ask only for factual reference data — e.g. a missing PO number, payment
#      reference, remittance advice, or date of payment
#   ✅ Maximum 2 questions — pick only the most essential ones
#   ✅ Frame them gently, e.g. "To help us investigate promptly, could you also
#      share [X]?"

#   ❌ Do NOT ask questions if the customer has already provided sufficient detail
#   ❌ Do NOT ask questions whose answers are visible in INVOICE CONTEXT or PAYMENT CONTEXT
#   ❌ Do NOT ask questions about amounts, rates, or contract terms — those are for
#      the FA team to verify internally, not the customer to justify
#   ❌ Do NOT add questions just for the sake of it — no questions is perfectly fine
#      when the email is already detailed enough

# Example of a well-formed acknowledgement with a question:
#   "Thank you for reaching out regarding your invoice query. We have received your
#   request and our finance/AR team will review the details and get back to you
#   shortly. To help us investigate promptly, could you share the RTGS/NEFT
#   transaction reference number for the payment made? We appreciate your patience."

# Example of a well-formed acknowledgement WITHOUT questions (customer gave full detail):
#   "Thank you for reaching out regarding your invoice query. We have received your
#   request and our finance/AR team will review the details and get back to you
#   shortly. We appreciate your patience."

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Your task:
# 1. Determine if this is SAFE (informational) or SENSITIVE (financial/dispute/uncertain)
# 2. Summarize the issue in 2-3 sentences
# 3. Draft the appropriate response based on the category above
# 4. Decide whether any critical reference information is missing from the customer's
#    email — if yes, include at most 2 gentle clarifying questions in the response
# 5. For sensitive cases, list questions the FA team should investigate internally
#    (these go in questions_to_ask, never in the customer-facing ai_response)
# 6. Note which memory episodes you referenced (by position index, e.g. [0, 1])

# Return ONLY valid JSON:
# {{
#   "ai_summary": "2-3 sentence summary of the issue",
#   "can_auto_respond": true or false,
#   "auto_respond_reason": "One sentence explaining the decision, e.g. 'Purely informational query about tax rate — safe to answer from invoice data' OR 'Customer is disputing the invoice amount — requires FA review'",
#   "ai_response": "The full draft response email text (required for BOTH true and false cases). For false cases this is the acknowledgement, optionally ending with 1-2 clarifying questions if critical info is missing.",
#   "customer_questions_included": true or false,
#   "confidence_score": 0.0-1.0,
#   "questions_to_ask": ["FA investigation question 1", "FA investigation question 2"],
#   "episodes_referenced": [0, 1],
#   "memory_context_used": true or false
# }}"""
#     # ── END UPDATED PROMPT ────────────────────────────────────────────────────

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
STEP 1 — CHECK IF YOU HAVE THE DATA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before deciding anything, ask: "Is the answer to this question already present
in INVOICE CONTEXT or PAYMENT CONTEXT above?"

- If YES and the query is READ-ONLY (looking up a fact, not changing anything):
  → This is a SAFE query. You can answer it directly. Set can_auto_respond=true.

- If NO (data is missing or null in context):
  → Do NOT guess or infer. Set can_auto_respond=false and ask the customer for
    the missing reference, OR flag it for FA team to investigate.

- If the query asks you to DO something (adjust, credit, waive, extend, confirm
  a payment was received, dispute an amount):
  → Always set can_auto_respond=false regardless of what data you have.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — DECISION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ AUTO-RESPOND (can_auto_respond=true) — READ-ONLY fact lookups where the
   answer is clearly present in INVOICE CONTEXT or PAYMENT CONTEXT:

  • Invoice totals, subtotals, tax amounts, tax rates, due dates, issue dates
    → e.g. "What is the total amount on INV-2024-001?" — answer from invoice data
  • Line item details, quantities, unit prices, discounts applied
    → e.g. "What discount was applied?" — answer from invoice data
  • Payment amounts, payment dates, reference numbers already on record
    → e.g. "What payment do you have on file for this invoice?" — answer from payment data
  • Standard factual info: accepted payment methods, account manager contact,
    invoice format questions, how to submit remittance advice
  • Requests to resend invoice — acknowledge and confirm it will be sent
  • Status lookups: "Is this invoice paid / overdue / partially paid?"
    → answer from payment data only if status is clearly determinable

  KEY RULE: Reading and reporting data ≠ making a financial decision.
  Telling a customer their invoice total is $5,000 is SAFE. It's already on
  the invoice they received. You are simply confirming a fact from the record.

❌ ESCALATE (can_auto_respond=false) — anything that involves a DECISION,
   CHANGE, or UNVERIFIED CLAIM:

  • Customer disputes an amount ("you charged me wrong", "I should pay less")
  • Requests to adjust, credit, waive, or reverse any charge
  • Payment deadline extension or change to agreed payment terms
  • Penalty, interest, or late fee disputes
  • Contract or pricing agreement disputes
  • Customer states a different amount than what is on record
  • Ambiguity about whether a payment was received or correctly applied
  • Refund requests of any kind
  • Multi-invoice disputes or bulk adjustments
  • Legal language, escalation threats, or mentions of legal action
  • Any email classified as HIGH priority
  • The answer to their question is NOT present in the data you have

  When in doubt → escalate. The cost of a wrong financial decision is always
  higher than the cost of a brief delay.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — DRAFTING THE RESPONSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

If can_auto_respond=true:
  - Answer the specific question using ONLY data from INVOICE CONTEXT or
    PAYMENT CONTEXT — never invent or infer values not present in the data
  - Be concise and professional
  - State the specific value clearly (e.g. "The total amount on INV-2024-001
    is $X,XXX.XX, which includes tax of $XXX.XX.")
  - Do NOT make promises, adjustments, or commitments
  - Close with: "If you have any further questions, please don't hesitate to reach out."

If can_auto_respond=false (two sub-cases):

  SUB-CASE A — Data is missing (you can't answer even a factual query because
  the invoice/payment data is absent or null in context):
    - Acknowledge receipt politely
    - Include 1-2 targeted clarifying questions to get the missing reference data
      (e.g. "Could you confirm the invoice number?" or "Could you share the
      payment reference number?")
    - Example:
      "Thank you for reaching out. We'd be happy to look into this for you.
      To locate your invoice, could you confirm the invoice number or the
      approximate invoice date? We'll follow up as soon as we have the details."

  SUB-CASE B — This is a dispute/change/decision request (data may be present
  but action is required):
    - Draft a polite acknowledgement ONLY
    - Do NOT comment on the dispute details, amounts, or who is right
    - Do NOT include any figures, dates, or financial data in this response
    - Use this structure (adapt wording naturally):
      "Thank you for reaching out regarding [brief neutral topic description].
      We have received your query and our finance/AR team will carefully review
      the details and get back to you shortly. If you have any additional
      information or supporting documents, please feel free to share them.
      We appreciate your patience."
    - In questions_to_ask, list what the FA team needs to investigate internally

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 4 — CLARIFYING QUESTIONS (when to ask the customer)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Ask the customer a question ONLY when ALL of these are true:
  ✅ The answer is not already in the email, attachments, or invoice/payment context
  ✅ You genuinely cannot proceed without this information
  ✅ It is a simple factual reference (invoice number, payment ref, date of payment,
     PO number, remittance advice)
  ✅ Maximum 2 questions — pick the most essential ones only

NEVER ask the customer:
  ❌ To justify amounts, rates, or contract terms — that's the FA team's job
  ❌ Questions whose answers are visible in INVOICE CONTEXT or PAYMENT CONTEXT
  ❌ Vague or open-ended questions like "Can you provide more details?"
  ❌ Questions just to seem thorough — no questions is fine when email is detailed

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 5 — FA TEAM INVESTIGATION QUESTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

For escalated cases (can_auto_respond=false), populate questions_to_ask with
specific internal investigation tasks for the FA team. These are NEVER shown
to the customer. Examples of good FA questions:
  - "Verify whether payment ref TXN-XXXX has been applied to INV-2024-001"
  - "Check if the tax calculation on line item 3 matches the contract rate"
  - "Confirm whether a credit note was issued for the disputed amount"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY valid JSON:
{{
  "ai_summary": "2-3 sentence summary of the issue",
  "can_auto_respond": true or false,
  "auto_respond_reason": "One sentence: explain exactly why this is SAFE (read-only fact present in data) or SENSITIVE (dispute/action/data missing)",
  "ai_response": "Full draft response to customer. Always required. For true: direct factual answer. For false: acknowledgement ± 1-2 clarifying questions if reference data is missing.",
  "customer_questions_included": true or false,
  "confidence_score": 0.0-1.0,
  "questions_to_ask": ["FA internal investigation question 1", "FA internal investigation question 2"],
  "episodes_referenced": [0, 1],
  "memory_context_used": true or false
}}"""

    try:
        response = await llm_client.chat(prompt)
        data = json.loads(response)

        # Log the auto-respond decision reason for auditability
        logger.info(
            f"[email_id={state['email_id']}] Auto-respond decision: "
            f"can_auto_respond={data.get('can_auto_respond')} | "
            f"customer_questions_included={data.get('customer_questions_included', False)} | "
            f"reason={data.get('auto_respond_reason', 'N/A')}"
        )

        return {
            **state,
            "ai_summary": data.get("ai_summary", state.get("description", "")),
            # ai_response is now always populated (acknowledgement or full answer)
            "ai_response": data.get("ai_response"),
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
    Saves dispute, analysis, memory episodes, open questions, and email routing.

    Supporting documents auto-registration
    ───────────────────────────────────────
    After creating the DisputeAIAnalysis record we call
    AnalysisSupportingRefRepository.upsert_supporting_doc() for:
      • the matched invoice_data row  (reference_table="invoice_data")
      • every matched payment_detail row  (reference_table="payment_detail")
    This means the FA team always sees which DB records back the AI's conclusions,
    without needing to manually add them via the UI.
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
        dtype_repo = DisputeTypeRepository(db_session)
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
                logger.info(f"[email_id={state['email_id']}] Created new dispute type: {dispute_type.reason_name}")
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

        dispute_id = state.get("existing_dispute_id")

        # 2. Create or reuse dispute
        #    Use first payment_detail_id (if any) as the primary FK on the dispute row.
        primary_payment_id = state["matched_payment_ids"][0] if state.get("matched_payment_ids") else None

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
                logger.info(
                    f"[email_id={state['email_id']}] Updated dispute_id={dispute_id} "
                    f"with primary payment_detail_id={primary_payment_id}"
                )

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

        # ── 3a. Auto-register supporting documents ───────────────────────────
        # Invoice and all payment_detail rows are the "supporting documents" that
        # back this analysis.  We upsert them into analysis_supporting_refs so the
        # FA team can see exactly which records the AI examined.
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
            logger.info(
                f"[email_id={state['email_id']}] Registered invoice_data id="
                f"{state['matched_invoice_id']} as supporting doc for analysis_id={analysis.analysis_id}"
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
        if state.get("matched_payment_ids"):
            logger.info(
                f"[email_id={state['email_id']}] Registered {len(state['matched_payment_ids'])} "
                f"payment_detail record(s) as supporting docs for analysis_id={analysis.analysis_id}"
            )
        # ─────────────────────────────────────────────────────────────────────

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
                            q.status = "ANSWERED"
                            q.answered_in_episode_id = ai_episode.episode_id
                            q.answered_at = datetime.now(timezone.utc)

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

    graph.add_node("extract_text",                  node_extract_text)
    graph.add_node("extract_invoice_data_via_groq",  partial(node_extract_invoice_data_via_groq, llm_client=llm_client))
    graph.add_node("identify_invoice",               partial(node_identify_invoice,               db_session=db_session))
    graph.add_node("fetch_context",                  partial(node_fetch_context,                  db_session=db_session))
    graph.add_node("classify_email",                 partial(node_classify_email,                 llm_client=llm_client))
    graph.add_node("generate_ai_response",           partial(node_generate_ai_response,           llm_client=llm_client))
    graph.add_node("persist_results",                partial(node_persist_results,                db_session=db_session))

    graph.set_entry_point("extract_text")
    graph.add_edge("extract_text",                  "extract_invoice_data_via_groq")
    graph.add_edge("extract_invoice_data_via_groq", "identify_invoice")
    graph.add_edge("identify_invoice",              "fetch_context")
    graph.add_edge("fetch_context",                 "classify_email")
    graph.add_edge("classify_email",                "generate_ai_response")
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
        "matched_payment_ids": [],
        "customer_id": None,
        "routing_confidence": 0.0,
        "invoice_details": None,
        "all_payment_details": [],
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