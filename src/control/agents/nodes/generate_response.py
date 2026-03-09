"""
src/control/agents/nodes/generate_response.py
"""

from __future__ import annotations
import json
import logging

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState
from src.control.prompts import build_generate_response_prompt
from src.control.prompts.generate_response import PROMPT_NAME as RESPONSE_PROMPT_NAME, PROMPT_VERSION as RESPONSE_PROMPT_VERSION

logger = logging.getLogger(__name__)


@observe(name="node_generate_ai_response")
async def node_generate_ai_response(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    """
    Balanced response generation:
    • Plain conversational text — no email draft format.
    • Answers factual read-only queries directly when data is present.
    • Escalates disputes, adjustments, or anything requiring financial decision.
    • Asks clarifying questions only when genuinely missing reference data (max 2).
    • _needs_invoice_details=True → fixed short response, skip LLM.
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

    # Fixed response when no invoice or dispute could be identified
    if state.get("_needs_invoice_details"):
        response_text = (
            "Thanks for reaching out. We weren't able to locate the relevant invoice "
            "from the details provided. Could you share the invoice number and approximate "
            "invoice date so we can look into this for you?"
        )
        langfuse_context.update_current_observation(
            output={"path": "needs_invoice_details", "auto_respond": True}
        )
        return {
            **state,
            "ai_summary":              state.get("description", "Customer query without invoice reference."),
            "ai_response":             response_text,
            "confidence_score":        0.9,
            "auto_response_generated": True,
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

    prompt = build_generate_response_prompt(
        subject=state["subject"],
        sender_email=state["sender_email"],
        body_text=state["body_text"],
        invoice_details=state.get("invoice_details"),
        all_payment_details=all_pmts,
        memory_summary=state.get("memory_summary"),
        recent_episodes=recent_eps,
        pending_questions=pending_qs,
        classification=state.get("classification", ""),
        dispute_type_name=state.get("dispute_type_name", ""),
        priority=state.get("priority", ""),
        description=state.get("description", ""),
    )

    langfuse_context.update_current_observation(
        input={"prompt": prompt},
        metadata={"prompt_name": RESPONSE_PROMPT_NAME, "prompt_version": RESPONSE_PROMPT_VERSION},
    )

    try:
        response = await llm_client.chat(prompt)
        data     = json.loads(response)

        logger.info(
            f"[email_id={state['email_id']}] auto_respond={data.get('can_auto_respond')} | "
            f"{data.get('auto_respond_reason', '')}"
        )
        langfuse_context.update_current_observation(
            output={
                "auto_respond":     data.get("can_auto_respond"),
                "confidence_score": data.get("confidence_score"),
                "reason":           data.get("auto_respond_reason"),
            }
        )

        return {
            **state,
            "ai_summary":                 data.get("ai_summary", state.get("description", "")),
            "ai_response":                data.get("ai_response"),
            "confidence_score":           data.get("confidence_score", 0.7),
            "auto_response_generated":    bool(data.get("can_auto_respond")),
            "questions_to_ask":           data.get("questions_to_ask", []),
            "memory_context_used":        data.get("memory_context_used", False),
            "episodes_referenced":        data.get("episodes_referenced", []),
            "_answers_pending_questions": [
                int(x) for x in data.get("answers_pending_questions", [])
                if str(x).lstrip("-").isdigit()
            ],
        }

    except Exception as e:
        logger.error(f"[email_id={state['email_id']}] Response generation error: {e}")
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
