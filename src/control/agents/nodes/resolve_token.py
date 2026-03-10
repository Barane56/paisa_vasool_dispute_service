"""
src/control/agents/nodes/resolve_token.py
==========================================
Layer 1 — Structured Identifier (Dispute Token) Matching.

Every outbound email embeds a token like [DISP-78923] in the subject and footer.
When a customer replies (even in a brand-new thread from a different address),
this node scans the incoming subject + body for that token and immediately
links the email to the correct existing dispute — no NLP, no guessing.

If no token is found the state passes through unchanged and the downstream
layers (invoice match → embedding search → manual queue) take over.
"""

from __future__ import annotations
import re
import logging

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState

logger = logging.getLogger(__name__)

# Matches DISP-12345 anywhere in text (case-insensitive)
_TOKEN_RE = re.compile(r"\bDISP-(\d+)\b", re.IGNORECASE)


def _extract_token(text: str) -> str | None:
    """Return the first DISP-XXXXX token found in *text*, or None."""
    m = _TOKEN_RE.search(text)
    return m.group(0).upper() if m else None


@observe(name="node_resolve_token")
async def node_resolve_token(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Scan subject + body (+ attachments) for a DISP-XXXXX token.
    If found, look it up in the DB and short-circuit to the matched dispute.
    """
    search_text = " ".join([
        state.get("subject", ""),
        state.get("body_text", ""),
        *state.get("attachment_texts", []),
    ])

    token = _extract_token(search_text)

    if not token:
        logger.info(
            f"[email_id={state['email_id']}] resolve_token: no DISP token found — "
            "falling through to downstream layers"
        )
        langfuse_context.update_current_observation(output={"token_found": False})
        return {**state, "token_matched_dispute_id": None}

    logger.info(
        f"[email_id={state['email_id']}] resolve_token: found token={token}"
    )

    if not db_session:
        langfuse_context.update_current_observation(
            output={"token_found": True, "token": token, "db_available": False}
        )
        return {**state, "token_matched_dispute_id": None}

    from src.data.repositories.repositories import DisputeRepository, MemoryEpisodeRepository, MemorySummaryRepository, OpenQuestionRepository

    dispute_repo = DisputeRepository(db_session)
    dispute      = await dispute_repo.get_by_dispute_token(token)

    if not dispute:
        logger.warning(
            f"[email_id={state['email_id']}] resolve_token: token={token} not found in DB — "
            "may be stale or tampered"
        )
        langfuse_context.update_current_observation(
            output={"token_found": True, "token": token, "db_match": False}
        )
        return {**state, "token_matched_dispute_id": None}

    dispute_id = dispute.dispute_id
    logger.info(
        f"[email_id={state['email_id']}] resolve_token: TOKEN MATCH → dispute_id={dispute_id}"
    )

    # Reload memory so downstream nodes have full context
    recent_episodes    = []
    memory_summary     = state.get("memory_summary")
    pending_questions  = []

    ep_repo    = MemoryEpisodeRepository(db_session)
    recent_eps = await ep_repo.get_latest_n(dispute_id, n=5)
    recent_episodes = [
        {"actor": ep.actor, "type": ep.episode_type, "text": ep.content_text[:400]}
        for ep in recent_eps
    ]

    sum_repo    = MemorySummaryRepository(db_session)
    summary_obj = await sum_repo.get_for_dispute(dispute_id)
    if summary_obj:
        memory_summary = summary_obj.summary_text

    q_repo     = OpenQuestionRepository(db_session)
    pending_qs = await q_repo.get_pending_for_dispute(dispute_id)
    pending_questions = [
        {"question_id": q.question_id, "text": q.question_text}
        for q in pending_qs
    ]

    langfuse_context.update_current_observation(
        output={
            "token_found":  True,
            "token":        token,
            "db_match":     True,
            "dispute_id":   dispute_id,
        }
    )

    return {
        **state,
        "token_matched_dispute_id": dispute_id,
        "existing_dispute_id":      dispute_id,
        "recent_episodes":          recent_episodes,
        "memory_summary":           memory_summary,
        "pending_questions":        pending_questions,
        # Carry over customer_id from the original dispute if not yet known
        "customer_id": state.get("customer_id") or dispute.customer_id,
    }
