"""
src/control/agents/nodes/embed_and_search.py
"""

from __future__ import annotations
import logging

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState

logger = logging.getLogger(__name__)


@observe(name="node_embed_and_search")
async def node_embed_and_search(
    state: EmailProcessingState, llm_client=None, db_session=None
) -> EmailProcessingState:
    """
    Only runs when no invoice was matched (cold mail path).
    Embeds the email description and searches past episodes via pgvector cosine similarity.
    resolve_dispute_link uses the result to confirm or reject the candidate dispute.
    """
    defaults = {
        **state,
        "similar_episodes":     [],
        "embedding_matched":    False,
        "embedding_dispute_id": None,
        "embedding_similarity": 0.0,
    }

    if state.get("matched_invoice_id"):
        logger.info(f"[email_id={state['email_id']}] embed_and_search skipped: invoice matched")
        return defaults

    text_to_embed = state.get("description", "").strip() or state.get("body_text", "").strip()
    customer_id   = state.get("customer_id")

    if not text_to_embed or not customer_id or not llm_client or not db_session:
        logger.warning(f"[email_id={state['email_id']}] embed_and_search skipped: missing inputs")
        return defaults

    from src.data.repositories.repositories import MemoryEpisodeRepository
    from src.config.settings import settings

    embedding = await llm_client.embed(text_to_embed)
    if not embedding:
        logger.warning(f"[email_id={state['email_id']}] Embedding returned None")
        return defaults

    ep_repo     = MemoryEpisodeRepository(db_session)
    similar_eps = await ep_repo.search_similar_by_customer(
        customer_id=customer_id,
        query_embedding=embedding,
        top_k=5,
        threshold=settings.EPISODE_SIMILARITY_THRESHOLD,
    )

    langfuse_context.update_current_observation(
        output={
            "embedding_dims":  len(embedding),
            "matches_found":   len(similar_eps),
            "best_similarity": similar_eps[0]["similarity"] if similar_eps else None,
        }
    )

    if similar_eps:
        best = similar_eps[0]
        logger.info(
            f"[email_id={state['email_id']}] Best match: "
            f"dispute_id={best['dispute_id']}, similarity={best['similarity']}"
        )
        return {
            **state,
            "similar_episodes":     similar_eps,
            "embedding_matched":    True,
            "embedding_dispute_id": best["dispute_id"],
            "embedding_similarity": best["similarity"],
        }

    logger.info(
        f"[email_id={state['email_id']}] No matches above "
        f"threshold={settings.EPISODE_SIMILARITY_THRESHOLD}"
    )
    return defaults
