"""
src/control/agents/nodes/classify_email.py
"""

from __future__ import annotations
import json
import logging
from typing import List, Dict

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState
from src.control.prompts import build_classify_prompt
from src.control.prompts.classify_email import PROMPT_NAME as CLASSIFY_PROMPT_NAME, PROMPT_VERSION as CLASSIFY_PROMPT_VERSION

logger = logging.getLogger(__name__)


@observe(name="node_classify_email")
async def node_classify_email(
    state: EmailProcessingState, llm_client=None, db_session=None
) -> EmailProcessingState:
    """
    Classify the email BEFORE fetching context so that dispute_type_name is
    available when fetch_context does its precise dispute lookup.
    """
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

    prompt = build_classify_prompt(
        subject=state["subject"],
        sender_email=state["sender_email"],
        body_text=state["body_text"],
        attachment_texts=state["attachment_texts"],
        available_dispute_types=available_dispute_types,
        groq_extracted=state.get("groq_extracted"),
    )

    langfuse_context.update_current_observation(
        input={"prompt": prompt},
        metadata={"prompt_name": CLASSIFY_PROMPT_NAME, "prompt_version": CLASSIFY_PROMPT_VERSION},
    )

    try:
        response = await llm_client.chat(prompt)
        data     = json.loads(response)

        langfuse_context.update_current_observation(
            output={
                "classification":   data.get("classification"),
                "dispute_type":     data.get("dispute_type_name"),
                "priority":         data.get("priority"),
                "is_new_type":      data.get("is_new_type", False),
            }
        )

        result = {
            **state,
            "available_dispute_types":    available_dispute_types,
            "classification":             data.get("classification", "CLARIFICATION"),
            "dispute_type_name":          data.get("dispute_type_name") or "General Clarification",
            "priority":                   data.get("priority", "MEDIUM"),
            "description":                data.get("description", state["body_text"][:500]),
            "_answers_pending_questions": [],
            "_new_dispute_type":          None,
        }

        if data.get("is_new_type"):
            result["_new_dispute_type"] = {
                "reason_name":    data.get("dispute_type_name"),
                "description":    data.get("new_type_description", ""),
                "severity_level": data.get("new_type_severity", "MEDIUM"),
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
