"""
src/control/agents/nodes/detect_context_shift.py
=================================================
Detects whether an incoming follow-up email introduces a genuinely new
dispute issue that should be forked out of the active conversation.

Gate condition
--------------
This node is a no-op (passes state through unchanged) unless:
  • An existing_dispute_id is already set in state (i.e. this IS a follow-up), AND
  • The email has NOT been matched via the DISP token (token matches are definitive
    continuations by definition — the customer used our own reference).

When the gate is open the LLM analyses the email against the active dispute
context and populates:
  context_shift_detected        — True if ≥1 new issue found
  context_shift_confidence      — how certain the model is
  context_shift_reasoning       — plain-English audit trail
  forked_issues                 — list of new dispute specs to create
  original_dispute_still_active — whether the original dispute also continues

Edge cases handled
------------------
• LLM returns malformed JSON           → logged, treated as no shift
• LLM confidence < CONFIDENCE_THRESHOLD → treated as no shift, flagged for FA
• new_issues is empty but is_context_shift=True → treated as no shift, warning logged
• Multiple new issues in one email     → all forked, all linked
• No db_session / no llm_client        → pass-through, no-op
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState
from src.control.prompts.detect_context_shift import (
    build_detect_context_shift_prompt,
    PROMPT_NAME,
    PROMPT_VERSION,
)

logger = logging.getLogger(__name__)

# Minimum LLM confidence required to act on a detected shift automatically.
# Below this threshold the shift is flagged for FA review instead of auto-forking.
CONFIDENCE_THRESHOLD: float = 0.70

# Valid relationship types accepted from the LLM (guards against hallucination)
_VALID_RELATIONSHIP_TYPES = frozenset(
    {"FORKED_FROM", "SAME_CUSTOMER_BATCH", "ESCALATION_OF", "RELATED"}
)
_VALID_PRIORITIES = frozenset({"LOW", "MEDIUM", "HIGH"})


def _safe_relationship_type(value: Any) -> str:
    """Return a valid relationship_type string, defaulting to FORKED_FROM."""
    if isinstance(value, str) and value.upper() in _VALID_RELATIONSHIP_TYPES:
        return value.upper()
    return "FORKED_FROM"


def _safe_priority(value: Any) -> str:
    """Return a valid priority string, defaulting to MEDIUM."""
    if isinstance(value, str) and value.upper() in _VALID_PRIORITIES:
        return value.upper()
    return "MEDIUM"


def _normalise_issue(raw: Any) -> Optional[Dict]:
    """
    Normalise a single entry from the LLM's new_issues list.
    Returns None if the entry is structurally invalid.
    """
    if not isinstance(raw, dict):
        return None
    description = (raw.get("new_dispute_description") or "").strip()
    if not description:
        return None
    return {
        "invoice_number":    (raw.get("new_dispute_invoice_number") or "").strip() or None,
        "type_hint":         (raw.get("new_dispute_type_hint") or "General Clarification").strip(),
        "description":       description,
        "priority":          _safe_priority(raw.get("priority")),
        "context_note":      (raw.get("context_note") or "").strip() or None,
        "relationship_type": _safe_relationship_type(raw.get("relationship_type")),
    }


@observe(name="node_detect_context_shift")
async def node_detect_context_shift(
    state: EmailProcessingState, llm_client=None, db_session=None
) -> EmailProcessingState:
    """
    Gate: skip entirely when there is no existing dispute or this is a token match.
    Otherwise ask the LLM whether the email introduces new issues.
    """
    email_id          = state["email_id"]
    existing_id       = state.get("existing_dispute_id")
    token_matched     = state.get("token_matched_dispute_id") is not None

    # ── Gate ─────────────────────────────────────────────────────────────────
    # Skip when:
    #   • No existing dispute — brand-new email, nothing to fork from
    #   • Token-matched — customer explicitly referenced this dispute
    #   • Intent flag says no fork needed — SOCIAL, IRRELEVANT, etc.
    #   • requires_fork is explicitly False from the classifier
    intent         = state.get("intent", "UNKNOWN")
    requires_fork  = state.get("requires_fork", True)

    # Intents that are never forks — no billing content means nothing to split
    _NON_FORK_INTENTS = frozenset({
        "SOCIAL", "IRRELEVANT", "ABUSIVE", "RESOLUTION_ACK",
        "DUPLICATE_CONTACT", "PAYMENT_ADVICE", "ADVANCE_PAYMENT",
    })

    skip_reason: str | None = None
    if not existing_id:
        skip_reason = "no existing dispute"
    elif token_matched:
        skip_reason = "token-matched (definitive continuation)"
    elif intent in _NON_FORK_INTENTS:
        skip_reason = f"intent={intent} never requires a fork"
    elif not requires_fork:
        skip_reason = f"classifier set requires_fork=False for intent={intent}"

    if skip_reason:
        logger.debug(f"[email_id={email_id}] detect_context_shift: skipped — {skip_reason}")
        langfuse_context.update_current_observation(
            output={"skipped": True, "reason": skip_reason}
        )
        return {
            **state,
            "context_shift_detected":      False,
            "context_shift_confidence":    0.0,
            "context_shift_reasoning":     None,
            "forked_issues":               [],
            "original_dispute_still_active": True,
        }

    # ── Gather active dispute metadata for the prompt ─────────────────────────
    existing_invoice_number = None
    existing_dispute_type   = state.get("dispute_type_name", "Unknown")
    existing_description    = ""
    existing_status         = "OPEN"

    if db_session:
        try:
            from src.data.repositories.repositories import DisputeRepository
            dispute = await DisputeRepository(db_session).get_by_id(existing_id)
            if dispute:
                existing_invoice_number = (
                    dispute.invoice.invoice_number if dispute.invoice else None
                )
                existing_dispute_type = (
                    dispute.dispute_type.reason_name if dispute.dispute_type else existing_dispute_type
                )
                existing_description = dispute.description or ""
                existing_status      = dispute.status or "OPEN"
        except Exception as db_err:
            logger.warning(
                f"[email_id={email_id}] detect_context_shift: could not load dispute "
                f"metadata for dispute_id={existing_id}: {db_err}"
            )

    # ── No LLM available → pass through ──────────────────────────────────────
    if not llm_client:
        logger.warning(
            f"[email_id={email_id}] detect_context_shift: no llm_client, skipping detection"
        )
        langfuse_context.update_current_observation(
            output={"skipped": True, "reason": "no llm_client"}
        )
        return {
            **state,
            "context_shift_detected":      False,
            "context_shift_confidence":    0.0,
            "context_shift_reasoning":     None,
            "forked_issues":               [],
            "original_dispute_still_active": True,
        }

    # Newest invoice number extracted from this email (may differ from existing)
    new_invoice_number: Optional[str] = (
        state.get("matched_invoice_number")
        or (state["candidate_invoice_numbers"][0] if state.get("candidate_invoice_numbers") else None)
    )

    # ── Build and send prompt ─────────────────────────────────────────────────
    prompt = build_detect_context_shift_prompt(
        subject=state["subject"],
        sender_email=state["sender_email"],
        body_text=state["body_text"],
        existing_dispute_id=existing_id,
        existing_invoice_number=existing_invoice_number,
        existing_dispute_type=existing_dispute_type,
        existing_description=existing_description,
        existing_status=existing_status,
        recent_episodes=state.get("recent_episodes", []),
        new_invoice_number=new_invoice_number,
    )

    langfuse_context.update_current_observation(
        input={"prompt": prompt},
        metadata={"prompt_name": PROMPT_NAME, "prompt_version": PROMPT_VERSION},
    )

    # ── LLM call with full error isolation ───────────────────────────────────
    try:
        raw_response = await llm_client.chat_reasoning(prompt)
        data: Dict   = json.loads(raw_response)
    except json.JSONDecodeError as json_err:
        logger.error(
            f"[email_id={email_id}] detect_context_shift: LLM returned invalid JSON: "
            f"{json_err}. Treating as no shift."
        )
        langfuse_context.update_current_observation(
            output={"error": "json_decode_error", "skipped": True}
        )
        return {**state, "context_shift_detected": False, "context_shift_confidence": 0.0,
                "context_shift_reasoning": None, "forked_issues": [],
                "original_dispute_still_active": True}
    except Exception as llm_err:
        logger.error(
            f"[email_id={email_id}] detect_context_shift: LLM call failed: {llm_err}. "
            "Treating as no shift."
        )
        langfuse_context.update_current_observation(
            output={"error": str(llm_err), "skipped": True}
        )
        return {**state, "context_shift_detected": False, "context_shift_confidence": 0.0,
                "context_shift_reasoning": None, "forked_issues": [],
                "original_dispute_still_active": True}

    # ── Parse response ────────────────────────────────────────────────────────
    is_shift   = bool(data.get("is_context_shift", False))
    confidence = float(data.get("confidence", 0.0))
    reasoning  = (data.get("reasoning") or "").strip() or None
    original_still_active = bool(data.get("original_dispute_still_active", True))

    raw_new_issues: List = data.get("new_issues") or []

    # ── No shift detected ─────────────────────────────────────────────────────
    if not is_shift:
        logger.info(
            f"[email_id={email_id}] detect_context_shift: no shift detected "
            f"(confidence={confidence:.2f})"
        )
        langfuse_context.update_current_observation(
            output={"is_context_shift": False, "confidence": confidence}
        )
        return {
            **state,
            "context_shift_detected":      False,
            "context_shift_confidence":    confidence,
            "context_shift_reasoning":     reasoning,
            "forked_issues":               [],
            "original_dispute_still_active": True,
        }

    # ── Shift detected — validate new_issues ─────────────────────────────────
    normalised_issues: List[Dict] = []
    for idx, raw_issue in enumerate(raw_new_issues):
        issue = _normalise_issue(raw_issue)
        if issue is None:
            logger.warning(
                f"[email_id={email_id}] detect_context_shift: issue[{idx}] is malformed "
                f"and will be skipped: {raw_issue!r}"
            )
            continue
        normalised_issues.append(issue)

    if not normalised_issues:
        # LLM said is_context_shift=True but gave us nothing actionable
        logger.warning(
            f"[email_id={email_id}] detect_context_shift: is_context_shift=True but "
            "new_issues is empty or all malformed — treating as no shift"
        )
        langfuse_context.update_current_observation(
            output={"is_context_shift": True, "confidence": confidence,
                    "new_issues_count": 0, "action": "downgraded_to_no_shift"}
        )
        return {
            **state,
            "context_shift_detected":      False,
            "context_shift_confidence":    confidence,
            "context_shift_reasoning":     reasoning,
            "forked_issues":               [],
            "original_dispute_still_active": True,
        }

    # ── Low confidence — flag for FA but do NOT auto-fork ────────────────────
    if confidence < CONFIDENCE_THRESHOLD:
        logger.info(
            f"[email_id={email_id}] detect_context_shift: shift detected but confidence "
            f"{confidence:.2f} < threshold {CONFIDENCE_THRESHOLD} — "
            "flagging for FA review instead of auto-forking"
        )
        # Surface as an FA question so the agent surfaces it in the response
        fa_note = (
            f"[CONTEXT SHIFT SUSPECTED — LOW CONFIDENCE {confidence:.0%}] "
            f"{reasoning or 'No explanation provided.'} "
            f"Possible new issue(s): "
            + "; ".join(
                f"{i['type_hint']} (inv: {i['invoice_number'] or 'unknown'})"
                for i in normalised_issues
            )
            + " — please review and fork manually if appropriate."
        )
        langfuse_context.update_current_observation(
            output={"is_context_shift": True, "confidence": confidence,
                    "action": "flagged_for_fa", "new_issues_count": len(normalised_issues)}
        )
        # Inject FA note into questions_to_ask so it reaches the FA dashboard
        existing_questions = list(state.get("questions_to_ask") or [])
        return {
            **state,
            "context_shift_detected":      False,   # not auto-forking
            "context_shift_confidence":    confidence,
            "context_shift_reasoning":     reasoning,
            "forked_issues":               [],
            "original_dispute_still_active": True,
            "questions_to_ask":            existing_questions + [fa_note],
        }

    # ── High confidence shift — proceed with auto-fork ───────────────────────
    logger.info(
        f"[email_id={email_id}] detect_context_shift: CONTEXT SHIFT CONFIRMED "
        f"(confidence={confidence:.2f}, issues={len(normalised_issues)}) — "
        f"existing_dispute_id={existing_id}, original_still_active={original_still_active}"
    )
    langfuse_context.update_current_observation(
        output={
            "is_context_shift":           True,
            "confidence":                 confidence,
            "new_issues_count":           len(normalised_issues),
            "original_dispute_still_active": original_still_active,
        }
    )

    return {
        **state,
        "context_shift_detected":      True,
        "context_shift_confidence":    confidence,
        "context_shift_reasoning":     reasoning,
        "forked_issues":               normalised_issues,
        "original_dispute_still_active": original_still_active,
    }
