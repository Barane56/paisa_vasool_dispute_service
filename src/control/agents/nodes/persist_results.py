"""
src/control/agents/nodes/persist_results.py
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState

logger = logging.getLogger(__name__)


@observe(name="node_persist_results")
async def node_persist_results(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Saves dispute, analysis, memory episodes (with embeddings), open questions,
    supporting docs, email routing, and auto-assignment.
    """
    if not db_session:
        return state

    from src.data.repositories.repositories import (
        DisputeTypeRepository, DisputeRepository, EmailRepository,
        MemoryEpisodeRepository, OpenQuestionRepository, UserRepository,
        AnalysisSupportingRefRepository, DisputeAssignmentRepository,
    )
    from src.data.models.postgres.models import (
        DisputeMaster, DisputeAIAnalysis, DisputeType,
        DisputeMemoryEpisode, DisputeOpenQuestion,
        DisputeActivityLog, DisputeAssignment, EmailInbox,
    )
    from sqlalchemy import update as sa_update

    try:
        # 1. Resolve or create dispute type ────────────────────────────────────
        dtype_repo   = DisputeTypeRepository(db_session)
        dispute_type = await dtype_repo.get_by_name(
            state.get("dispute_type_name") or "General Clarification"
        )

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
                logger.info(
                    f"[email_id={state['email_id']}] Created new dispute type: "
                    f"{dispute_type.reason_name}"
                )
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

        dispute_id         = state.get("existing_dispute_id")
        primary_payment_id = state["matched_payment_ids"][0] if state.get("matched_payment_ids") else None

        # 2. Create or reuse dispute ────────────────────────────────────────────
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
                f"payments={state.get('matched_payment_ids')}"
            )
        else:
            dispute = await DisputeRepository(db_session).get_by_id(dispute_id)
            if dispute and not dispute.payment_detail_id and primary_payment_id:
                dispute.payment_detail_id = primary_payment_id
            log = DisputeActivityLog(
                dispute_id=dispute_id,
                action_type="FOLLOW_UP_EMAIL_RECEIVED",
                notes=f"Follow-up email: {state['subject'][:100]}",
            )
            db_session.add(log)

        # 3. AI analysis ────────────────────────────────────────────────────────
        analysis = DisputeAIAnalysis(
            dispute_id=dispute_id,
            predicted_category=state.get("dispute_type_name") or "General Clarification",
            confidence_score=state.get("confidence_score", 0.0),
            ai_summary=state.get("ai_summary", ""),
            ai_response=state.get("ai_response"),
            auto_response_generated=state.get("auto_response_generated", False),
            memory_context_used=state.get("memory_context_used", False),
            episodes_referenced=[
                int(x) for x in (state.get("episodes_referenced") or [])
                if str(x).lstrip("-").isdigit()
            ],
        )
        db_session.add(analysis)
        await db_session.flush()

        # 3a. Supporting docs ───────────────────────────────────────────────────
        ref_repo = AnalysisSupportingRefRepository(db_session)
        if state.get("matched_invoice_id"):
            await ref_repo.upsert_supporting_doc(
                analysis_id=analysis.analysis_id,
                reference_table="invoice_data",
                ref_id_value=state["matched_invoice_id"],
                context_note=(
                    f"Invoice {state.get('matched_invoice_number', state['matched_invoice_id'])} "
                    f"— primary supporting document"
                ),
            )
        for pid in state.get("matched_payment_ids", []):
            await ref_repo.upsert_supporting_doc(
                analysis_id=analysis.analysis_id,
                reference_table="payment_detail",
                ref_id_value=pid,
                context_note=f"Payment {pid} — supporting document",
            )

        # 4. Customer email episode ─────────────────────────────────────────────
        email_episode = DisputeMemoryEpisode(
            dispute_id=dispute_id,
            episode_type="CUSTOMER_EMAIL",
            actor="CUSTOMER",
            content_text=f"Subject: {state['subject']}\n\n{state['body_text'][:1000]}",
            email_id=state["email_id"],
        )
        db_session.add(email_episode)
        await db_session.flush()

        # 5. AI response episode ────────────────────────────────────────────────
        ai_episode = None
        if state.get("ai_response"):
            ep_type    = "AI_RESPONSE" if state.get("auto_response_generated") else "AI_ACKNOWLEDGEMENT"
            ai_episode = DisputeMemoryEpisode(
                dispute_id=dispute_id,
                episode_type=ep_type,
                actor="AI",
                content_text=state["ai_response"],
                email_id=state["email_id"],
            )
            db_session.add(ai_episode)
            await db_session.flush()

            answered_ids = state.get("_answers_pending_questions", [])
            if answered_ids:
                q_repo = OpenQuestionRepository(db_session)
                for qid in answered_ids:
                    q = await q_repo.get_by_id(qid)
                    if q and q.status == "PENDING":
                        q.status                 = "ANSWERED"
                        q.answered_in_episode_id = ai_episode.episode_id
                        q.answered_at            = datetime.now(timezone.utc)

        # 5a. Embed ai_summary ──────────────────────────────────────────────────
        ai_summary_text = state.get("ai_summary", "").strip()
        if ai_episode and ai_summary_text:
            from src.handlers.http_clients.llm_client import get_llm_client
            try:
                embedding = await get_llm_client().embed(ai_summary_text)
                if embedding:
                    ep_repo = MemoryEpisodeRepository(db_session)
                    await ep_repo.upsert_embedding(ai_episode.episode_id, embedding)
                    logger.info(
                        f"[email_id={state['email_id']}] Saved embedding "
                        f"(dims={len(embedding)}) on episode_id={ai_episode.episode_id}"
                    )
            except Exception as emb_err:
                logger.warning(
                    f"[email_id={state['email_id']}] Embedding save failed (non-fatal): {emb_err}"
                )

        # 6. FA open questions ──────────────────────────────────────────────────
        for item in state.get("questions_to_ask", []):
            # LLM sometimes returns dicts {"question_id": ..., "question_text": ...}
            # instead of plain strings — handle both shapes safely
            if isinstance(item, dict):
                question_text = item.get("question_text") or item.get("text") or str(item)
            else:
                question_text = str(item)
            db_session.add(DisputeOpenQuestion(
                dispute_id=dispute_id,
                asked_in_episode_id=email_episode.episode_id,
                question_text=question_text,
                status="PENDING",
            ))

        # 7. Email routing ──────────────────────────────────────────────────────
        email_repo = EmailRepository(db_session)
        await email_repo.update_status(state["email_id"], "PROCESSED")
        await db_session.execute(
            sa_update(EmailInbox)
            .where(EmailInbox.email_id == state["email_id"])
            .values(
                dispute_id=dispute_id,
                routing_confidence=state.get("routing_confidence", 0.0),
            )
        )

        # 8. Auto-assign — only if no active assignment exists ──────────────────
        if not state.get("auto_response_generated"):
            assign_repo       = DisputeAssignmentRepository(db_session)
            active_assignment = await assign_repo.get_active_assignment(dispute_id)
            if not active_assignment:
                user_repo = UserRepository(db_session)
                all_users = await user_repo.get_all(limit=10)
                if all_users:
                    db_session.add(DisputeAssignment(
                        dispute_id=dispute_id,
                        assigned_to=all_users[0].user_id,
                        status="ACTIVE",
                    ))
                    logger.info(
                        f"[email_id={state['email_id']}] Auto-assigned dispute_id={dispute_id} "
                        f"to user_id={all_users[0].user_id}"
                    )
            else:
                logger.info(
                    f"[email_id={state['email_id']}] Skipped auto-assign: "
                    f"dispute_id={dispute_id} already assigned"
                )

        await db_session.commit()

        # 9. Summarisation trigger ──────────────────────────────────────────────
        from src.config.settings import settings
        ep_repo  = MemoryEpisodeRepository(db_session)
        ep_count = await ep_repo.count_for_dispute(dispute_id)
        if ep_count >= settings.EPISODE_SUMMARIZE_THRESHOLD:
            from src.control.tasks import summarize_episodes_task
            summarize_episodes_task.delay(dispute_id)

        langfuse_context.update_current_observation(
            output={
                "dispute_id":  dispute_id,
                "analysis_id": analysis.analysis_id,
                "is_new_dispute": state.get("existing_dispute_id") is None,
            }
        )

        return {**state, "dispute_id": dispute_id, "analysis_id": analysis.analysis_id}

    except Exception as e:
        logger.error(f"Persist error email_id={state['email_id']}: {e}", exc_info=True)
        await db_session.rollback()
        try:
            await EmailRepository(db_session).update_status(state["email_id"], "FAILED", str(e))
            await db_session.commit()
        except Exception:
            pass
        return {**state, "error": str(e)}
