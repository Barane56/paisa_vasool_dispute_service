"""
src/control/tasks.py
====================
All Celery tasks for the dispute service.

Task inventory
--------------
process_email_task          — existing: runs AI pipeline on a document-type email
process_live_email_task     — NEW: runs AI pipeline on a real IMAP-fetched email
poll_all_mailboxes_task     — NEW: beat task; polls every active mailbox
fetch_mailbox_emails_task   — NEW: per-mailbox fetch + enqueue
summarize_episodes_task     — existing: rolling memory summarisation
match_invoice_task          — existing: invoice-payment matching placeholder
"""
import asyncio
import logging

from src.control.celery_app import celery_app
from src.core.exceptions import TaskEnqueueError  # noqa: F401

logger = logging.getLogger(__name__)


def _flush_langfuse():
    try:
        from src.observability import langfuse_client
        if langfuse_client:
            langfuse_client.flush()
    except Exception as e:
        logger.debug(f"Langfuse flush skipped: {e}")


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# 1. process_email_task  (document-upload / PDF flow)
# ---------------------------------------------------------------------------

@celery_app.task(
    name="src.control.tasks.process_email_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="email_processing",
)
def process_email_task(self, email_id, sender_email, subject, body_text, attachment_texts):
    logger.info(f"[Task] process_email_task email_id={email_id}")

    async def _run():
        from src.data.clients.postgres import AsyncSessionLocal
        from src.control.agents.email_processing_agent import run_email_processing
        from src.handlers.http_clients.llm_client import get_llm_client
        async with AsyncSessionLocal() as session:
            result = await run_email_processing(
                email_id=email_id, sender_email=sender_email, subject=subject,
                body_text=body_text, attachment_texts=attachment_texts,
                db_session=session, llm_client=get_llm_client(),
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
        logger.error(f"[Task] process_email_task failed email_id={email_id}: {exc}", exc_info=True)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            async def _mark_failed():
                from src.data.clients.postgres import AsyncSessionLocal
                from src.data.repositories.repositories import EmailRepository
                async with AsyncSessionLocal() as session:
                    await EmailRepository(session).update_status(email_id, "FAILED", str(exc))
                    await session.commit()
            _run_async(_mark_failed())
            raise
    finally:
        _flush_langfuse()


# ---------------------------------------------------------------------------
# 2. process_live_email_task  (real IMAP email -> AI pipeline)
# ---------------------------------------------------------------------------

@celery_app.task(
    name="src.control.tasks.process_live_email_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="email_processing",
)
def process_live_email_task(self, message_id, existing_dispute_id=None):
    logger.info(f"[Task] process_live_email_task message_id={message_id} existing_dispute_id={existing_dispute_id}")

    async def _run():
        from datetime import datetime, timezone
        from sqlalchemy import update as sa_update
        from src.data.clients.postgres import AsyncSessionLocal
        from src.data.repositories.mailbox_repository import EmailInboxMessageRepository
        from src.data.models.postgres.email_models import EmailInbox, EmailAttachment
        from src.data.models.postgres.mailbox_models import EmailInboxMessage
        from src.control.agents.email_processing_agent import run_email_processing
        from src.handlers.http_clients.llm_client import get_llm_client

        # ── Phase 1: atomically claim the message ─────────────────────────────
        # Commit PROCESSING in its own transaction so every other session sees
        # it immediately. This prevents the recovery task and concurrent workers
        # from double-processing the same message.
        async with AsyncSessionLocal() as claim_session:
            claimed = await claim_session.execute(
                sa_update(EmailInboxMessage)
                .where(
                    EmailInboxMessage.message_id == message_id,
                    EmailInboxMessage.processing_status.in_(["RECEIVED", "PROCESSING"]),
                )
                .values(processing_status="PROCESSING")
                .returning(EmailInboxMessage.message_id)
            )
            if not claimed.fetchone():
                # Another worker already claimed or completed this message
                logger.info(f"[email_id={message_id}] Already claimed by another worker — skipping")
                return
            await claim_session.commit()

        # ── Phase 2: run the full pipeline in a fresh session ─────────────────
        async with AsyncSessionLocal() as session:
            msg = await EmailInboxMessageRepository(session).get_by_id(message_id)
            if not msg:
                raise Exception(f"EmailInboxMessage {message_id} not found")

            attachment_texts = [
                a.extracted_text for a in (msg.attachments or []) if a.extracted_text
            ]
            attachment_metadata = [
                {
                    "file_name": a.file_name,
                    "file_type": a.file_type,
                    "extracted_text": a.extracted_text or "",
                }
                for a in (msg.attachments or [])
            ]

            # Create legacy EmailInbox for pipeline compatibility
            email_record = EmailInbox(
                sender_email=msg.sender_email,
                subject=msg.subject,
                body_text=msg.body_text,
                received_at=msg.received_at or datetime.now(timezone.utc),
                has_attachment=msg.has_attachment,
                processing_status="RECEIVED",
            )

            if "google.com" in email_record.sender_email :
                # skipping google mails 
                logger.info(f"Skipping Email from google, {email_record.email_id}")
                return

            session.add(email_record)
            await session.flush()

            for a in (msg.attachments or []):
                session.add(EmailAttachment(
                    email_id=email_record.email_id,
                    file_name=a.file_name,
                    file_type=a.file_type,
                    extracted_text=a.extracted_text or "",
                ))
            await session.flush()

            await session.execute(
                sa_update(EmailInboxMessage)
                .where(EmailInboxMessage.message_id == message_id)
                .values(email_inbox_id=email_record.email_id)
            )

            result = await run_email_processing(
                email_id=email_record.email_id, sender_email=msg.sender_email,
                subject=msg.subject, body_text=msg.body_text,
                attachment_texts=attachment_texts, db_session=session,
                llm_client=get_llm_client(),
                attachment_metadata=attachment_metadata,
                existing_dispute_id=existing_dispute_id,
            )
            if result.get("error"):
                raise Exception(result["error"])

            dispute_id = result.get("dispute_id")
            if dispute_id:
                await session.execute(
                    sa_update(EmailInboxMessage)
                    .where(EmailInboxMessage.message_id == message_id)
                    .values(dispute_id=dispute_id, processing_status="PROCESSED")
                )
            await session.commit()
            return {"dispute_id": dispute_id, "analysis_id": result.get("analysis_id")}

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error(f"[Task] process_live_email_task failed message_id={message_id}: {exc}", exc_info=True)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            async def _mark_failed():
                from src.data.clients.postgres import AsyncSessionLocal
                from src.data.repositories.mailbox_repository import EmailInboxMessageRepository
                async with AsyncSessionLocal() as session:
                    await EmailInboxMessageRepository(session).update_status(message_id, "FAILED", str(exc))
                    await session.commit()
            _run_async(_mark_failed())
            raise
    finally:
        _flush_langfuse()


# ---------------------------------------------------------------------------
# 3. fetch_mailbox_emails_task  (per-mailbox IMAP fetch)
# ---------------------------------------------------------------------------

@celery_app.task(
    name="src.control.tasks.fetch_mailbox_emails_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="mailbox_polling",
    expires=55,   # discard if sitting in queue longer than 55s (just under poll interval)
)
def fetch_mailbox_emails_task(self, mailbox_id):
    logger.info(f"[Task] fetch_mailbox_emails_task mailbox_id={mailbox_id}")

    async def _run():
        from src.data.clients.postgres import AsyncSessionLocal
        from src.data.repositories.mailbox_repository import MailboxRepository, EmailInboxMessageRepository
        from src.data.models.postgres.mailbox_models import EmailInboxMessage, EmailMessageAttachment
        from src.data.repositories.repositories import DisputeRepository
        from src.core.services.imap_service import fetch_unseen_emails, extract_dispute_token
        from sqlalchemy import select
        from src.data.models.postgres.mailbox_models import OutboundEmail
        from src.data.models.postgres.user_models import User

        async with AsyncSessionLocal() as session:
            mb = await MailboxRepository(session).get_by_id(mailbox_id)
            if not mb or not mb.is_active or mb.is_paused:
                logger.info(f"[Task] Mailbox {mailbox_id} inactive/paused — skipping")
                return []

            emails, new_max_uid = fetch_unseen_emails(
                imap_host=mb.imap_host, imap_port=mb.imap_port,
                use_ssl=mb.use_ssl, email_address=mb.email_address,
                password_enc=mb.password_enc, last_uid_seen=mb.last_uid_seen,
                mailbox_id=mailbox_id,
            )
            logger.info(f"[Task] Fetched {len(emails)} emails for mailbox {mailbox_id}")

            msg_repo = EmailInboxMessageRepository(session)
            d_repo   = DisputeRepository(session)

            # Load all user emails for outbound detection
            all_users_result = await session.execute(select(User.email))
            all_user_emails  = {r[0].lower() for r in all_users_result.fetchall()}

            new_inbound_ids = []

            for ed in emails:
                existing = await msg_repo.get_by_imap_uid(mailbox_id, ed["imap_uid"])
                if existing:
                    continue

                sender_lower = ed.get("sender_email", "").lower()

                # ── Outbound detection ────────────────────────────────────────
                # ── Outbound detection ────────────────────────────────────────
                # An email is OUTBOUND if the sender is any FA/user in our system
                # OR if it's the mailbox address itself (sent copy).
                # Outbound emails are never AI-processed — no point running the
                # pipeline on replies we composed ourselves.
                is_outbound = (
                    sender_lower == mb.email_address.lower()
                    or sender_lower in all_user_emails
                )
                source = "OUTBOUND" if is_outbound else "INBOUND"

                # check for google health email or bootstrap emails
                # current skipping all emails from google.com 
                # they are conjesting the email pipeline unnecessaryly
                 
                is_from_google = "google.com" in sender_lower
                
                if is_from_google :
                    # skipping 
                    logger.info(f"Skipping email from Google")
                    continue 


                # ── Dispute resolution (3 layers) ─────────────────────────────
                resolved_dispute_id: Optional[int] = None

                # Layer 1: In-Reply-To / References → match against outbound Message-IDs
                in_reply_to = ed.get("in_reply_to_header")
                references  = ed.get("references_header", "") or ""
                if in_reply_to or references:
                    # Collect all Message-IDs in the thread chain
                    candidate_msg_ids = set()
                    if in_reply_to:
                        candidate_msg_ids.add(in_reply_to.strip())
                    for mid in references.split():
                        candidate_msg_ids.add(mid.strip())

                    if candidate_msg_ids:
                        ob_result = await session.execute(
                            select(OutboundEmail.dispute_id)
                            .where(OutboundEmail.message_id_header.in_(candidate_msg_ids))
                            .limit(1)
                        )
                        row = ob_result.first()
                        if row:
                            resolved_dispute_id = row[0]
                            logger.info(
                                f"[Task] Matched via thread headers → dispute_id={resolved_dispute_id}"
                            )

                # Layer 2: DISP-XXXXX token in body
                if not resolved_dispute_id:
                    dispute_token = extract_dispute_token(ed["body_text"])
                    if dispute_token:
                        dispute = await d_repo.get_by_dispute_token(dispute_token)
                        if dispute:
                            resolved_dispute_id = dispute.dispute_id
                            logger.info(
                                f"[Task] Matched via DISP token {dispute_token} → dispute_id={resolved_dispute_id}"
                            )

                msg = EmailInboxMessage(
                    mailbox_id=mailbox_id,
                    imap_uid=ed["imap_uid"],
                    message_uid=ed.get("message_uid"),
                    source=source,
                    direction=source,
                    sender_email=ed["sender_email"],
                    recipient_email=ed.get("recipient_email"),
                    subject=ed["subject"],
                    body_text=ed["body_text"],
                    body_html=ed.get("body_html"),
                    received_at=ed["received_at"],
                    has_attachment=ed["has_attachment"],
                    in_reply_to_header=ed.get("in_reply_to_header"),
                    references_header=ed.get("references_header"),
                    processing_status="RECEIVED",
                )

                if resolved_dispute_id:
                    msg.dispute_id        = resolved_dispute_id
                    msg.processing_status = "LINKED" if is_outbound else "RECEIVED"

                session.add(msg)
                await session.flush()

                for a in ed.get("attachments", []):
                    session.add(EmailMessageAttachment(
                        message_id=msg.message_id,
                        file_name=a["file_name"],
                        file_type=a["file_type"],
                        file_size=a.get("file_size"),
                        file_path=a["file_path"],
                        extracted_text=a.get("extracted_text"),
                    ))

                # Only queue AI processing for INBOUND without a resolved dispute yet
                if source == "INBOUND":
                    new_inbound_ids.append((msg.message_id, resolved_dispute_id))

            await MailboxRepository(session).update_last_polled(mailbox_id, new_max_uid)
            await session.commit()
            return new_inbound_ids

    try:
        new_pairs = _run_async(_run())
        for mid, already_resolved_dispute_id in (new_pairs or []):
            # Every inbound email runs the full AI pipeline so a response
            # is generated for follow-ups too, not just first-contact emails.
            # existing_dispute_id tells the pipeline which dispute to attach to.
            process_live_email_task.delay(mid, already_resolved_dispute_id)
            logger.info(
                f"[Task] Enqueued process_live_email_task message_id={mid} "
                f"existing_dispute_id={already_resolved_dispute_id}"
            )
    except Exception as exc:
        logger.error(f"[Task] fetch_mailbox_emails_task failed mailbox_id={mailbox_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 3b. link_reply_to_dispute_task
#     For inbound follow-up emails already matched to a dispute via headers —
#     adds a memory episode so it appears on the timeline.
# ---------------------------------------------------------------------------

@celery_app.task(
    name="src.control.tasks.link_reply_to_dispute_task",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    queue="email_processing",
)
def link_reply_to_dispute_task(self, message_id: int, dispute_id: int):
    """
    A customer follow-up reply was already matched to a dispute via thread headers.
    Create a memory episode so it appears on the timeline.
    """
    logger.info(f"[Task] link_reply_to_dispute_task message_id={message_id} dispute_id={dispute_id}")

    async def _run():
        from src.data.clients.postgres import AsyncSessionLocal
        from src.data.repositories.mailbox_repository import EmailInboxMessageRepository
        from src.data.models.postgres.memory_models import DisputeMemoryEpisode
        from src.config.settings import settings

        async with AsyncSessionLocal() as session:
            msg = await EmailInboxMessageRepository(session).get_by_id(message_id)
            if not msg:
                return

            # Guard: skip if an episode already exists for this exact email_id + dispute
            # Prevents duplicate episodes if this task is queued twice for the same message
            from sqlalchemy import select as sa_select
            existing_ep = await session.execute(
                sa_select(DisputeMemoryEpisode.episode_id)
                .where(
                    DisputeMemoryEpisode.dispute_id == dispute_id,
                    DisputeMemoryEpisode.email_id   == msg.email_inbox_id,
                    DisputeMemoryEpisode.episode_type == "CUSTOMER_REPLY",
                )
                .limit(1)
            )
            if existing_ep.fetchone():
                logger.info(f"[Task] Episode already exists for message_id={message_id} — skipping duplicate")
                return

            episode = DisputeMemoryEpisode(
                dispute_id=dispute_id,
                episode_type="CUSTOMER_REPLY",
                actor="CUSTOMER",
                content_text=f"[Follow-up email]\nFrom: {msg.sender_email}\nSubject: {msg.subject}\n\n{msg.body_text[:2000]}",
                email_id=msg.email_inbox_id,
            )
            session.add(episode)
            await session.commit()
            logger.info(f"[Task] Episode created for follow-up message_id={message_id}")

            # Trigger summarisation if threshold reached
            from src.data.repositories.repositories import MemoryEpisodeRepository
            async with AsyncSessionLocal() as s2:
                ep_count = len(await MemoryEpisodeRepository(s2).get_episodes_for_dispute(dispute_id, limit=200))
                if ep_count % settings.EPISODE_SUMMARIZE_THRESHOLD == 0:
                    summarize_episodes_task.delay(dispute_id)

    try:
        _run_async(_run())
    except Exception as exc:
        logger.error(f"[Task] link_reply_to_dispute_task failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 4. poll_all_mailboxes_task  (Celery beat — fires on schedule)
# ---------------------------------------------------------------------------

@celery_app.task(
    name="src.control.tasks.poll_all_mailboxes_task",
    queue="mailbox_polling",
)
def poll_all_mailboxes_task():
    logger.info("[Beat] poll_all_mailboxes_task firing")

    async def _get_ids():
        from src.data.clients.postgres import AsyncSessionLocal
        from src.data.repositories.mailbox_repository import MailboxRepository
        async with AsyncSessionLocal() as session:
            mbs = await MailboxRepository(session).list_active_for_polling()
            return [mb.mailbox_id for mb in mbs]

    try:
        for mid in _run_async(_get_ids()):
            fetch_mailbox_emails_task.delay(mid)
            logger.info(f"[Beat] Dispatched fetch task for mailbox_id={mid}")
    except Exception as exc:
        logger.error(f"[Beat] poll_all_mailboxes_task error: {exc}", exc_info=True)


# ---------------------------------------------------------------------------
# 5. summarize_episodes_task
# ---------------------------------------------------------------------------

@celery_app.task(
    name="src.control.tasks.summarize_episodes_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="memory",
)
def summarize_episodes_task(self, dispute_id):
    logger.info(f"[Task] Summarizing episodes for dispute_id={dispute_id}")

    async def _run():
        from src.data.clients.postgres import AsyncSessionLocal
        from src.data.repositories.repositories import MemoryEpisodeRepository, MemorySummaryRepository
        from src.data.models.postgres.models import DisputeMemorySummary
        from src.handlers.http_clients.llm_client import get_llm_client

        async with AsyncSessionLocal() as session:
            ep_repo  = MemoryEpisodeRepository(session)
            sum_repo = MemorySummaryRepository(session)
            episodes = await ep_repo.get_episodes_for_dispute(dispute_id, limit=50)
            if not episodes:
                return

            existing_summary = await sum_repo.get_for_dispute(dispute_id)
            existing_text    = existing_summary.summary_text if existing_summary else None
            llm = get_llm_client()
            episode_dicts = [{"actor": ep.actor, "content_text": ep.content_text} for ep in episodes]
            new_summary_text = await llm.summarize_episodes(episode_dicts, existing_text)
            last_episode = episodes[-1]

            if existing_summary:
                existing_summary.summary_text             = new_summary_text
                existing_summary.covered_up_to_episode_id = last_episode.episode_id
                existing_summary.version                  += 1
            else:
                session.add(DisputeMemorySummary(
                    dispute_id=dispute_id,
                    summary_text=new_summary_text,
                    covered_up_to_episode_id=last_episode.episode_id,
                    version=1,
                ))
            await session.commit()

    try:
        _run_async(_run())
    except Exception as exc:
        logger.error(f"[Task] Summarization failed dispute_id={dispute_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        _flush_langfuse()


# ---------------------------------------------------------------------------
# 6. match_invoice_task  (placeholder)
# ---------------------------------------------------------------------------

@celery_app.task(name="src.control.tasks.match_invoice_task", queue="matching")
def match_invoice_task(invoice_id, payment_detail_id):
    logger.info(f"[Task] Matching invoice_id={invoice_id} with payment_id={payment_detail_id}")


# ---------------------------------------------------------------------------
# 7. recover_stuck_emails_task
#    Runs every 5 minutes via beat. Finds emails saved to DB but never
#    processed (processing_status="RECEIVED" for > 2 minutes) and re-queues
#    them. Guards against the crash window between session.commit() and
#    the .delay() calls in fetch_mailbox_emails_task.
# ---------------------------------------------------------------------------

@celery_app.task(name="src.control.tasks.recover_stuck_emails_task", queue="mailbox_polling")
def recover_stuck_emails_task():
    from datetime import datetime, timezone, timedelta

    async def _run():
        from sqlalchemy import select, update as sa_update
        from src.data.clients.postgres import AsyncSessionLocal
        from src.data.models.postgres.mailbox_models import EmailInboxMessage

        # Pick up emails stuck in RECEIVED (> 2 min) or PROCESSING (> 10 min — worker killed)
        now = datetime.now(timezone.utc)
        received_cutoff   = now - timedelta(minutes=2)
        processing_cutoff = now - timedelta(minutes=10)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(
                    EmailInboxMessage.message_id,
                    EmailInboxMessage.dispute_id,
                    EmailInboxMessage.source,
                )
                .where(
                    EmailInboxMessage.source == "INBOUND",
                    (
                        (EmailInboxMessage.processing_status == "RECEIVED") &
                        (EmailInboxMessage.received_at <= received_cutoff)
                    ) | (
                        (EmailInboxMessage.processing_status == "PROCESSING") &
                        (EmailInboxMessage.received_at <= processing_cutoff)
                    ),
                )
                .order_by(EmailInboxMessage.received_at.asc())
                .limit(50)   # process at most 50 at a time to avoid thundering herd
            )
            stuck = result.fetchall()

            if not stuck:
                return 0

            logger.info(f"[Recovery] Found {len(stuck)} stuck INBOUND email(s) — re-queuing")

            # Atomically claim all stuck rows in one UPDATE ... RETURNING
            # so a concurrent recovery worker can't double-queue any of them
            stuck_ids = [row.message_id for row in stuck]
            claimed_result = await session.execute(
                sa_update(EmailInboxMessage)
                .where(
                    EmailInboxMessage.message_id.in_(stuck_ids),
                    EmailInboxMessage.processing_status.in_(["RECEIVED", "PROCESSING"]),
                )
                .values(processing_status="PROCESSING")
                .returning(EmailInboxMessage.message_id, EmailInboxMessage.dispute_id)
            )
            claimed_rows = claimed_result.fetchall()
            await session.commit()

            if not claimed_rows:
                return 0

            for row in claimed_rows:
                if row.dispute_id:
                    link_reply_to_dispute_task.delay(row.message_id, row.dispute_id)
                    logger.info(f"[Recovery] Re-queued link_reply message_id={row.message_id} dispute_id={row.dispute_id}")
                else:
                    process_live_email_task.delay(row.message_id)
                    logger.info(f"[Recovery] Re-queued process_live_email message_id={row.message_id}")

            return len(claimed_rows)

    try:
        count = _run_async(_run())
        logger.info(f"[Recovery] recover_stuck_emails_task done — re-queued {count} email(s)")
    except Exception as exc:
        logger.error(f"[Recovery] recover_stuck_emails_task failed: {exc}", exc_info=True)
