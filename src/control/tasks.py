import asyncio
import logging
from src.control.celery_app import celery_app
from src.core.exceptions import TaskEnqueueError

logger = logging.getLogger(__name__)


def _flush_langfuse():
    """Force-flush Langfuse trace buffer. Safe no-op if Langfuse is not configured."""
    try:
        from src.observability import langfuse_client
        if langfuse_client:
            langfuse_client.flush()
    except Exception as e:
        logger.debug(f"Langfuse flush skipped: {e}")


def _run_async(coro):
    """Run an async coroutine in a sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@celery_app.task(
    name="src.control.tasks.process_email_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="email_processing",
)
def process_email_task(
    self,
    email_id: int,
    sender_email: str,
    subject: str,
    body_text: str,
    attachment_texts: list,
):
    """
    Main Celery task: runs the LangGraph email processing pipeline (Groq edition).
    """
    logger.info(f"[Task] Processing email_id={email_id}")

    async def _run():
        from src.data.clients.postgres import AsyncSessionLocal
        from src.control.agents.email_processing_agent import run_email_processing
        from src.handlers.http_clients.llm_client import get_llm_client

        async with AsyncSessionLocal() as session:
            result = await run_email_processing(
                email_id=email_id,
                sender_email=sender_email,
                subject=subject,
                body_text=body_text,
                attachment_texts=attachment_texts,
                db_session=session,
                llm_client=get_llm_client(),
            )
            if result.get("error"):
                raise Exception(result["error"])
            return {
                "dispute_id": result.get("dispute_id"),
                "analysis_id": result.get("analysis_id"),
                "classification": result.get("classification"),
                "auto_response_generated": result.get("auto_response_generated"),
                "groq_extracted_invoice_number": (result.get("groq_extracted") or {}).get("invoice_number"),
            }

    try:
        result = _run_async(_run())
        return result
    except Exception as exc:
        logger.error(f"[Task] Failed processing email_id={email_id}: {exc}", exc_info=True)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            async def _mark_failed():
                from src.data.clients.postgres import AsyncSessionLocal
                from src.data.repositories.repositories import EmailRepository
                async with AsyncSessionLocal() as session:
                    repo = EmailRepository(session)
                    await repo.update_status(email_id, "FAILED", str(exc))
                    await session.commit()
            _run_async(_mark_failed())
            raise
    finally:
        _flush_langfuse()


@celery_app.task(
    name="src.control.tasks.summarize_episodes_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="memory",
)
def summarize_episodes_task(self, dispute_id: int):
    """Generates/updates a rolling memory summary for the dispute."""
    logger.info(f"[Task] Summarizing episodes for dispute_id={dispute_id}")

    async def _run():
        from src.data.clients.postgres import AsyncSessionLocal
        from src.data.repositories.repositories import MemoryEpisodeRepository, MemorySummaryRepository
        from src.data.models.postgres.models import DisputeMemorySummary
        from src.handlers.http_clients.llm_client import get_llm_client

        async with AsyncSessionLocal() as session:
            ep_repo = MemoryEpisodeRepository(session)
            sum_repo = MemorySummaryRepository(session)

            episodes = await ep_repo.get_episodes_for_dispute(dispute_id, limit=50)
            if not episodes:
                return

            existing_summary = await sum_repo.get_for_dispute(dispute_id)
            existing_text = existing_summary.summary_text if existing_summary else None

            llm = get_llm_client()
            episode_dicts = [
                {"actor": ep.actor, "content_text": ep.content_text}
                for ep in episodes
            ]
            new_summary_text = await llm.summarize_episodes(episode_dicts, existing_text)

            last_episode = episodes[-1]

            if existing_summary:
                existing_summary.summary_text = new_summary_text
                existing_summary.covered_up_to_episode_id = last_episode.episode_id
                existing_summary.version += 1
            else:
                summary = DisputeMemorySummary(
                    dispute_id=dispute_id,
                    summary_text=new_summary_text,
                    covered_up_to_episode_id=last_episode.episode_id,
                    version=1,
                )
                session.add(summary)

            await session.commit()
            logger.info(f"[Task] Summary updated for dispute_id={dispute_id}")

    try:
        _run_async(_run())
    except Exception as exc:
        logger.error(f"[Task] Summarization failed for dispute_id={dispute_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        _flush_langfuse()


@celery_app.task(
    name="src.control.tasks.match_invoice_task",
    queue="matching",
)
def match_invoice_task(invoice_id: int, payment_detail_id: int):
    """Placeholder for invoice-payment matching task."""
    logger.info(f"[Task] Matching invoice_id={invoice_id} with payment_id={payment_detail_id}")
    pass



def _run_async(coro):
    """Run an async coroutine in a sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@celery_app.task(
    name="src.control.tasks.process_email_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="email_processing",
)
def process_email_task(
    self,
    email_id: int,
    sender_email: str,
    subject: str,
    body_text: str,
    attachment_texts: list,
):
    """
    Main Celery task: runs the LangGraph email processing pipeline (Groq edition).
    """
    logger.info(f"[Task] Processing email_id={email_id}")

    async def _run():
        from src.data.clients.postgres import AsyncSessionLocal
        from src.control.agents.email_processing_agent import run_email_processing
        from src.handlers.http_clients.llm_client import get_llm_client

        async with AsyncSessionLocal() as session:
            result = await run_email_processing(
                email_id=email_id,
                sender_email=sender_email,
                subject=subject,
                body_text=body_text,
                attachment_texts=attachment_texts,
                db_session=session,
                llm_client=get_llm_client(),
            )
            if result.get("error"):
                raise Exception(result["error"])
            return {
                "dispute_id": result.get("dispute_id"),
                "analysis_id": result.get("analysis_id"),
                "classification": result.get("classification"),
                "auto_response_generated": result.get("auto_response_generated"),
                "groq_extracted_invoice_number": (result.get("groq_extracted") or {}).get("invoice_number"),
            }

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error(f"[Task] Failed processing email_id={email_id}: {exc}", exc_info=True)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            async def _mark_failed():
                from src.data.clients.postgres import AsyncSessionLocal
                from src.data.repositories.repositories import EmailRepository
                async with AsyncSessionLocal() as session:
                    repo = EmailRepository(session)
                    await repo.update_status(email_id, "FAILED", str(exc))
                    await session.commit()
            _run_async(_mark_failed())
            raise


@celery_app.task(
    name="src.control.tasks.summarize_episodes_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="memory",
)
def summarize_episodes_task(self, dispute_id: int):
    """Generates/updates a rolling memory summary for the dispute."""
    logger.info(f"[Task] Summarizing episodes for dispute_id={dispute_id}")

    async def _run():
        from src.data.clients.postgres import AsyncSessionLocal
        from src.data.repositories.repositories import MemoryEpisodeRepository, MemorySummaryRepository
        from src.data.models.postgres.models import DisputeMemorySummary
        from src.handlers.http_clients.llm_client import get_llm_client

        async with AsyncSessionLocal() as session:
            ep_repo = MemoryEpisodeRepository(session)
            sum_repo = MemorySummaryRepository(session)

            episodes = await ep_repo.get_episodes_for_dispute(dispute_id, limit=50)
            if not episodes:
                return

            existing_summary = await sum_repo.get_for_dispute(dispute_id)
            existing_text = existing_summary.summary_text if existing_summary else None

            llm = get_llm_client()
            episode_dicts = [
                {"actor": ep.actor, "content_text": ep.content_text}
                for ep in episodes
            ]
            new_summary_text = await llm.summarize_episodes(episode_dicts, existing_text)

            last_episode = episodes[-1]

            if existing_summary:
                existing_summary.summary_text = new_summary_text
                existing_summary.covered_up_to_episode_id = last_episode.episode_id
                existing_summary.version += 1
            else:
                summary = DisputeMemorySummary(
                    dispute_id=dispute_id,
                    summary_text=new_summary_text,
                    covered_up_to_episode_id=last_episode.episode_id,
                    version=1,
                )
                session.add(summary)

            await session.commit()
            logger.info(f"[Task] Summary updated for dispute_id={dispute_id}")

    try:
        _run_async(_run())
    except Exception as exc:
        logger.error(f"[Task] Summarization failed for dispute_id={dispute_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    name="src.control.tasks.match_invoice_task",
    queue="matching",
)
def match_invoice_task(invoice_id: int, payment_detail_id: int):
    """Placeholder for invoice-payment matching task."""
    logger.info(f"[Task] Matching invoice_id={invoice_id} with payment_id={payment_detail_id}")
    pass
