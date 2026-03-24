"""
src/control/agents/nodes/pre_fetch_dispute_context.py
======================================================
Lightweight pre-classify dispute context fetch.

Purpose
-------
classify_email runs BEFORE fetch_context in the pipeline, so when an email
arrives as a follow-up (token matched or task-level existing_dispute_id), the
classifier has no idea what the active dispute is about.  This causes it to
emit requires_fork=False for single-invoice DISPUTE emails even when the new
email is genuinely raising a different issue — because all it can see is
"same invoice, one issue" and it has no baseline to compare against.

This node fires ONLY when a prior dispute is already known at resolve_token
time (token_matched_dispute_id is set).  It pulls the minimal fields needed
to give classify_email a comparison baseline:

    existing_dispute_context = {
        "dispute_id":          int,
        "dispute_type":        str,
        "description":         str,
        "status":              str,
        "invoice_number":      str | None,
    }

That dict is injected into state and the classify_email prompt template
renders it as a "Active Dispute" block, allowing the LLM to correctly decide
whether the new email is a continuation or a different issue.

Gate
----
• Fires only when token_matched_dispute_id is set.
• No-op (pass-through) otherwise — zero cost on the hot new-email path.
• No-op if db_session is missing.

This node does NOT replace fetch_context.  fetch_context still runs later
and does the full L0-L4 matching, memory load, AR document chain, etc.
This node only provides the classify_email prompt with a comparison baseline.
"""

from __future__ import annotations

import logging
from typing import Optional, Dict

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState

logger = logging.getLogger(__name__)

_EMPTY_CONTEXT: Dict = {}


@observe(name="node_pre_fetch_dispute_context")
async def node_pre_fetch_dispute_context(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Pull minimal existing-dispute metadata so classify_email can compare
    the incoming email against the active dispute before deciding requires_fork.

    No-op if no token match or no db_session.
    """
    email_id         = state["email_id"]
    token_dispute_id = state.get("token_matched_dispute_id")

    # Task-level existing_dispute_id is set when tasks.py matched the email
    # via In-Reply-To/References thread headers BEFORE the pipeline started.
    # This is the most common follow-up path — the customer replies via their
    # email client without any DISP token in the body.
    task_dispute_id  = state.get("existing_dispute_id")

    # Use whichever is available; token match is more authoritative
    prior_dispute_id = token_dispute_id or task_dispute_id

    # ── Gate ──────────────────────────────────────────────────────────────────
    if not prior_dispute_id:
        logger.debug(
            f"[email_id={email_id}] pre_fetch_dispute_context: skipped — "
            "no token match and no task-level existing_dispute_id"
        )
        langfuse_context.update_current_observation(
            output={"skipped": True, "reason": "no prior dispute id"}
        )
        return {**state, "existing_dispute_context": _EMPTY_CONTEXT}

    if not db_session:
        logger.warning(
            f"[email_id={email_id}] pre_fetch_dispute_context: skipped — no db_session"
        )
        langfuse_context.update_current_observation(
            output={"skipped": True, "reason": "no db_session"}
        )
        return {**state, "existing_dispute_context": _EMPTY_CONTEXT}

    # ── Fetch ─────────────────────────────────────────────────────────────────
    try:
        from src.data.repositories.repositories import DisputeRepository

        dispute = await DisputeRepository(db_session).get_by_id(prior_dispute_id)

        if not dispute:
            logger.warning(
                f"[email_id={email_id}] pre_fetch_dispute_context: "
                f"prior_dispute_id={prior_dispute_id} not found in DB — skipping"
            )
            langfuse_context.update_current_observation(
                output={"skipped": True, "reason": "dispute_not_found"}
            )
            return {**state, "existing_dispute_context": _EMPTY_CONTEXT}

        context: Dict = {
            "dispute_id":     dispute.dispute_id,
            "dispute_type":   (
                dispute.dispute_type.reason_name if dispute.dispute_type else "Unknown"
            ),
            "description":    dispute.description or "",
            "status":         dispute.status or "OPEN",
            "invoice_number": (
                dispute.invoice.invoice_number if dispute.invoice else None
            ),
        }

        _source = "token" if token_dispute_id else "task_thread"
        logger.info(
            f"[email_id={email_id}] pre_fetch_dispute_context: loaded context for "
            f"dispute_id={dispute.dispute_id} type='{context['dispute_type']}' "
            f"invoice={context['invoice_number']} source={_source}"
        )
        langfuse_context.update_current_observation(
            output={
                "dispute_id":   dispute.dispute_id,
                "dispute_type": context["dispute_type"],
                "status":       context["status"],
                "source":       _source,
            }
        )

        return {**state, "existing_dispute_context": context}

    except Exception as err:
        # Non-fatal — classify_email degrades gracefully when context is empty
        logger.warning(
            f"[email_id={email_id}] pre_fetch_dispute_context: DB fetch failed "
            f"(non-fatal): {err}"
        )
        langfuse_context.update_current_observation(
            output={"skipped": True, "reason": f"db_error: {err}"}
        )
        return {**state, "existing_dispute_context": _EMPTY_CONTEXT}
