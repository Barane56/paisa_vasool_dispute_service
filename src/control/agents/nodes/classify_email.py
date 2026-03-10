"""
src/control/agents/nodes/classify_email.py
==========================================
Two-step classification pipeline:

  Step 1 — STRUCTURE  (structure_email.poml)
    Decides: how many issues, what each is about, DISPUTE or CLARIFICATION.
    No dispute types list is shown — eliminates type-scaffold splitting bias.

  Step 2 — TYPE ASSIGNMENT  (assign_dispute_type.poml)
    Assigns a dispute_type_name to each issue identified in step 1.
    Called once per issue (primary + each additional).
    The full dispute types list is shown here, but splitting is already locked.

By decoupling these decisions the LLM can no longer use the types list
as a scaffold to justify splitting a single email into multiple disputes.

Output shape is identical to the old single-step node — all downstream
nodes (fetch_context, generate_response, persist_results) are unchanged.
"""

from __future__ import annotations
import json
import logging
from typing import Any, Dict, List, Optional

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState
from src.control.prompts.structure_email    import build_structure_prompt,    PROMPT_NAME as STRUCTURE_PROMPT_NAME,    PROMPT_VERSION as STRUCTURE_PROMPT_VERSION
from src.control.prompts.assign_dispute_type import build_assign_type_prompt, PROMPT_NAME as ASSIGN_PROMPT_NAME,       PROMPT_VERSION as ASSIGN_PROMPT_VERSION

logger = logging.getLogger(__name__)

_VALID_PRIORITIES      = frozenset({"LOW", "MEDIUM", "HIGH"})
_VALID_CLASSIFICATIONS = frozenset({"DISPUTE", "CLARIFICATION"})


def _safe_priority(v: Any) -> str:
    return v.upper() if isinstance(v, str) and v.upper() in _VALID_PRIORITIES else "MEDIUM"


def _safe_classification(v: Any) -> str:
    return v.upper() if isinstance(v, str) and v.upper() in _VALID_CLASSIFICATIONS else "CLARIFICATION"


async def _assign_type(
    llm_client,
    classification: str,
    description: str,
    available_dispute_types: List[Dict],
    invoice_number: Optional[str],
    email_id: int,
    label: str = "primary",
) -> Dict:
    """
    Call the type-assignment LLM once for a single issue.
    Returns a dict with dispute_type_name, is_new_type, etc.
    Falls back to safe defaults on any error.
    """
    prompt = build_assign_type_prompt(
        classification=classification,
        description=description,
        available_dispute_types=available_dispute_types,
        invoice_number=invoice_number,
    )
    try:
        response = await llm_client.chat(prompt)
        data = json.loads(response)
        return {
            "dispute_type_name":    (data.get("dispute_type_name") or "General Clarification").strip(),
            "is_new_type":          bool(data.get("is_new_type", False)),
            "new_type_description": (data.get("new_type_description") or "").strip() or None,
            "new_type_severity":    _safe_priority(data.get("new_type_severity") or "MEDIUM"),
        }
    except Exception as e:
        logger.warning(
            f"[email_id={email_id}] Type assignment failed for {label}: {e} — using fallback"
        )
        return {
            "dispute_type_name":    "General Clarification" if classification == "CLARIFICATION" else "General Dispute",
            "is_new_type":          False,
            "new_type_description": None,
            "new_type_severity":    "MEDIUM",
        }


@observe(name="node_classify_email")
async def node_classify_email(
    state: EmailProcessingState, llm_client=None, db_session=None
) -> EmailProcessingState:
    """
    Two-step classification:
      1. Structure prompt  → how many issues, what they are (no types list)
      2. Assign-type prompt → dispute_type_name per issue (full types list, split locked)
    """
    # ── Load dispute types ────────────────────────────────────────────────────
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
            "active dispute types"
        )

    # ── Keyword fallback when no LLM ─────────────────────────────────────────
    if not llm_client:
        text_lower = state["all_text"].lower()
        dispute_keywords = [
            "wrong", "incorrect", "mismatch", "overcharged", "dispute",
            "error", "short payment", "not received", "overcharge", "discrepancy",
        ]
        classification = "DISPUTE" if any(k in text_lower for k in dispute_keywords) else "CLARIFICATION"
        return {
            **state,
            "available_dispute_types":    available_dispute_types,
            "classification":             classification,
            "dispute_type_name":          "Pricing Mismatch" if classification == "DISPUTE" else "General Clarification",
            "priority":                   "MEDIUM",
            "description":                state["body_text"][:500],
            "invoice_number":             None,
            "disputed_amount":            None,
            "_answers_pending_questions": [],
            "_new_dispute_type":          None,
            "inline_issues":              [],
        }

    # ── Step 1: Structure — how many issues, what are they ───────────────────
    structure_prompt = build_structure_prompt(
        subject=state["subject"],
        sender_email=state["sender_email"],
        body_text=state["body_text"],
        attachment_texts=state["attachment_texts"],
        groq_extracted=state.get("groq_extracted"),
    )

    langfuse_context.update_current_observation(
        input={"structure_prompt": structure_prompt},
        metadata={
            "prompt_name":    STRUCTURE_PROMPT_NAME,
            "prompt_version": STRUCTURE_PROMPT_VERSION,
        },
    )

    try:
        structure_response = await llm_client.chat(structure_prompt)
        structure_data: Dict = json.loads(structure_response)
    except Exception as e:
        logger.error(f"[email_id={state['email_id']}] Structure step failed: {e}", exc_info=True)
        return {
            **state,
            "available_dispute_types":    available_dispute_types,
            "classification":             "CLARIFICATION",
            "dispute_type_name":          "General Clarification",
            "priority":                   "MEDIUM",
            "description":                state["body_text"][:500],
            "invoice_number":             None,
            "disputed_amount":            None,
            "_answers_pending_questions": [],
            "_new_dispute_type":          None,
            "inline_issues":              [],
        }

    # Parse structure output
    primary_classification  = _safe_classification(structure_data.get("classification"))
    primary_description     = (structure_data.get("description") or state["body_text"][:500]).strip()
    primary_priority        = _safe_priority(structure_data.get("priority"))
    primary_invoice_number  = (structure_data.get("invoice_number") or "").strip() or None
    primary_disputed_amount = (structure_data.get("disputed_amount") or "").strip() or None

    raw_additional = structure_data.get("additional_issues") or []
    # Validate additional issues — each needs at minimum a description
    structured_additional = []
    for idx, raw in enumerate(raw_additional):
        if not isinstance(raw, dict):
            continue
        desc = (raw.get("description") or "").strip()
        if not desc:
            logger.warning(f"[email_id={state['email_id']}] additional_issue[{idx}] has no description, skipped")
            continue
        structured_additional.append({
            "classification":  _safe_classification(raw.get("classification")),
            "description":     desc,
            "invoice_number":  (raw.get("invoice_number") or "").strip() or None,
            "disputed_amount": (raw.get("disputed_amount") or "").strip() or None,
            "priority":        _safe_priority(raw.get("priority")),
        })

    total_issues = 1 + len(structured_additional)
    logger.info(
        f"[email_id={state['email_id']}] Structure step: "
        f"{total_issues} issue(s) detected "
        f"(primary={primary_classification}, additional={len(structured_additional)})"
    )

    # ── Step 2: Type assignment — one LLM call per issue ─────────────────────
    # Primary issue
    primary_type_data = await _assign_type(
        llm_client=llm_client,
        classification=primary_classification,
        description=primary_description,
        available_dispute_types=available_dispute_types,
        invoice_number=primary_invoice_number,
        email_id=state["email_id"],
        label="primary",
    )

    # Additional issues (parallel-friendly but kept sequential for simplicity)
    inline_issues: List[Dict] = []
    for idx, issue in enumerate(structured_additional):
        type_data = await _assign_type(
            llm_client=llm_client,
            classification=issue["classification"],
            description=issue["description"],
            available_dispute_types=available_dispute_types,
            invoice_number=issue.get("invoice_number"),
            email_id=state["email_id"],
            label=f"additional[{idx}]",
        )
        inline_issues.append({
            **issue,
            "dispute_type_name":    type_data["dispute_type_name"],
            "is_new_type":          type_data["is_new_type"],
            "new_type_description": type_data["new_type_description"],
            "new_type_severity":    type_data["new_type_severity"],
        })

    # Build _new_dispute_type for the primary if needed
    new_dispute_type = None
    if primary_type_data["is_new_type"]:
        new_dispute_type = {
            "reason_name":    primary_type_data["dispute_type_name"],
            "description":    primary_type_data["new_type_description"] or "",
            "severity_level": primary_type_data["new_type_severity"],
        }

    if inline_issues:
        logger.info(
            f"[email_id={state['email_id']}] Multi-issue email: "
            f"1 primary ({primary_type_data['dispute_type_name']}) + "
            f"{len(inline_issues)} additional → "
            f"{1 + len(inline_issues)} disputes will be created"
        )

    langfuse_context.update_current_observation(
        output={
            "classification":    primary_classification,
            "dispute_type":      primary_type_data["dispute_type_name"],
            "priority":          primary_priority,
            "inline_issues":     len(inline_issues),
            "is_new_type":       primary_type_data["is_new_type"],
            "structure_version": STRUCTURE_PROMPT_VERSION,
            "assign_version":    ASSIGN_PROMPT_VERSION,
        }
    )

    return {
        **state,
        "available_dispute_types":    available_dispute_types,
        "classification":             primary_classification,
        "dispute_type_name":          primary_type_data["dispute_type_name"],
        "priority":                   primary_priority,
        "description":                primary_description,
        "invoice_number":             primary_invoice_number,
        "disputed_amount":            primary_disputed_amount,
        "_answers_pending_questions": [],
        "_new_dispute_type":          new_dispute_type,
        "inline_issues":              inline_issues,
    }
