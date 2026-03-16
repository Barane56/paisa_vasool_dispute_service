"""
src/control/agents/nodes/resolve_dispute_link.py
"""

from __future__ import annotations
import logging

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState

logger = logging.getLogger(__name__)


@observe(name="node_resolve_dispute_link")
async def node_resolve_dispute_link(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Scenario T — Token match (Layer 1) → already resolved upstream, pass through.
    Scenario A — Invoice matched → pass through, context already correct.
    Scenario B — No invoice but embedding matched → reload memory from matched dispute.
    Scenario C — No invoice, no match → set _needs_invoice_details=True but still create dispute + assign FA.
    """
    # ── Scenario T (Token already matched upstream) ───────────────────────────
    if state.get("token_matched_dispute_id"):
        logger.info(
            f"[email_id={state['email_id']}] resolve: Scenario T — "
            f"token matched dispute_id={state['token_matched_dispute_id']}"
        )
        langfuse_context.update_current_observation(
            output={"scenario": "T", "dispute_id": state["token_matched_dispute_id"]}
        )
        return {**state, "_needs_invoice_details": False}
    invoice_matched   = state.get("matched_invoice_id") is not None
    embedding_matched = state.get("embedding_matched", False)

    # ── Scenario A ────────────────────────────────────────────────────────────
    if invoice_matched:
        logger.info(
            f"[email_id={state['email_id']}] resolve: Scenario A — invoice matched"
        )
        langfuse_context.update_current_observation(output={"scenario": "A"})
        return {**state, "_needs_invoice_details": False}

    # ── Scenario B ────────────────────────────────────────────────────────────
    if embedding_matched and state.get("embedding_dispute_id"):
        linked_id  = state["embedding_dispute_id"]
        similarity = state.get("embedding_similarity", 0.0)
        logger.info(
            f"[email_id={state['email_id']}] resolve: Scenario B — "
            f"embedding linked dispute_id={linked_id} (similarity={similarity})"
        )
        langfuse_context.update_current_observation(
            output={"scenario": "B", "linked_dispute_id": linked_id, "similarity": similarity}
        )

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
                "existing_dispute_id":    linked_id,
                "recent_episodes":        recent_episodes,
                "memory_summary":         memory_summary,
                "pending_questions":      pending_questions,
                "_needs_invoice_details": False,
            }

        return {**state, "existing_dispute_id": linked_id, "_needs_invoice_details": False}

    # ── Scenario E — existing_dispute_id already resolved by task dispatch ──
    # This handles follow-up emails where the task already matched via thread
    # headers or DISP token before the pipeline started. Even if no invoice is
    # matched in this email (e.g. very short reply body), we must NOT ask for
    # invoice details again — the dispute context is already known.
    if state.get("existing_dispute_id"):
        linked_id = state["existing_dispute_id"]
        logger.info(
            f"[email_id={state['email_id']}] resolve: Scenario E — "
            f"existing_dispute_id={linked_id} already set (follow-up reply) → "
            f"skip clarification, reuse dispute context"
        )
        langfuse_context.update_current_observation(
            output={"scenario": "E", "existing_dispute_id": linked_id}
        )
        return {**state, "_needs_invoice_details": False}

    # ── Scenario C ────────────────────────────────────────────────────────────
    logger.info(
        f"[email_id={state['email_id']}] resolve: Scenario C — "
        f"no invoice, no token, no embedding match → "
        f"will create dispute, assign FA, and ask customer for invoice details"
    )
    langfuse_context.update_current_observation(output={"scenario": "C"})
    return {
        **state,
        "existing_dispute_id":    None,
        "_needs_invoice_details": True,   # triggers invoice-request email + FA note
    }
