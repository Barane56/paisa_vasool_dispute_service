"""
src/core/services/outbound_email_service.py
============================================
Orchestrates composing and sending emails on behalf of an FA via the
dispute's mailbox. Stores a full audit record with threading headers.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.core.exceptions.errors import ResourceNotFoundError, ValidationError as PVValidationError
from src.core.services.imap_service import decode_password
from src.core.services.smtp_service import (
    generate_message_id, build_references_chain, send_email, test_smtp_connection,
)
from src.data.models.postgres.mailbox_models import (
    OutboundEmail, OutboundEmailAttachment, EmailInboxMessage,
)
from src.data.repositories.mailbox_repository import (
    MailboxRepository, EmailInboxMessageRepository,
)
from src.data.repositories.repositories import DisputeRepository

logger = logging.getLogger(__name__)

ATTACHMENT_STORAGE_DIR = Path(getattr(settings, "ATTACHMENT_STORAGE_DIR", "/tmp/dispute_attachments"))
OUTBOUND_SUBDIR = ATTACHMENT_STORAGE_DIR / "outbound"
OUTBOUND_SUBDIR.mkdir(parents=True, exist_ok=True)

from src.core.services.gcs_service import upload_attachment as _gcs_upload, get_public_url as _gcs_url


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)[:100]


class OutboundEmailService:
    def __init__(self, db: AsyncSession):
        self.db          = db
        self.mb_repo     = MailboxRepository(db)
        self.msg_repo    = EmailInboxMessageRepository(db)
        self.disp_repo   = DisputeRepository(db)

    # ── Find the last inbound message for threading ───────────────────────────

    async def _get_last_inbound_message_id(self, dispute_id: int) -> Optional[int]:
        """
        Returns the message_id of the most recent INBOUND EmailInboxMessage
        linked to this dispute — either directly via EmailInboxMessage.dispute_id
        or via the legacy EmailInbox.dispute_id linkage.

        This is used to set In-Reply-To / References so our reply lands in the
        same thread in the customer's inbox.
        """
        from sqlalchemy import select
        from src.data.models.postgres.mailbox_models import EmailInboxMessage
        from src.data.models.postgres.models import EmailInbox

        # Primary lookup: direct dispute_id on EmailInboxMessage
        result = await self.db.execute(
            select(EmailInboxMessage.message_id)
            .where(
                EmailInboxMessage.dispute_id == dispute_id,
                EmailInboxMessage.source == "INBOUND",
            )
            .order_by(EmailInboxMessage.received_at.desc())
            .limit(1)
        )
        row = result.first()
        if row:
            return row[0]

        # Fallback: find via legacy EmailInbox.dispute_id → email_inbox_id join
        # Handles cases where dispute_id is on EmailInbox but not yet on EmailInboxMessage
        result2 = await self.db.execute(
            select(EmailInboxMessage.message_id)
            .join(EmailInbox, EmailInbox.email_id == EmailInboxMessage.email_inbox_id)
            .where(
                EmailInbox.dispute_id == dispute_id,
                EmailInboxMessage.source == "INBOUND",
            )
            .order_by(EmailInboxMessage.received_at.desc())
            .limit(1)
        )
        row2 = result2.first()
        return row2[0] if row2 else None

    # ── Find the right mailbox for a dispute ─────────────────────────────────

    async def _get_mailbox_for_dispute(self, dispute_id: int):
        """
        Find the mailbox where the original email for this dispute arrived.
        Falls back to the first active mailbox if not determinable.
        """
        from sqlalchemy import select
        from src.data.models.postgres.mailbox_models import EmailInboxMessage

        # Try to find the inbound message linked to this dispute
        result = await self.db.execute(
            select(EmailInboxMessage)
            .where(EmailInboxMessage.dispute_id == dispute_id)
            .where(EmailInboxMessage.source == "INBOUND")
            .order_by(EmailInboxMessage.received_at.asc())
            .limit(1)
        )
        inbound = result.scalar_one_or_none()
        if inbound and inbound.mailbox_id:
            mb = await self.mb_repo.get_by_id(inbound.mailbox_id)
            if mb and mb.is_active:
                return mb

        # Fallback: first active mailbox
        mailboxes = await self.mb_repo.list_active_for_polling()
        if not mailboxes:
            raise PVValidationError("No active mailbox configured. Add a mailbox before sending.")
        return mailboxes[0]

    # ── Save uploaded attachment files ────────────────────────────────────────

    async def _save_upload(self, file: UploadFile, outbound_id: int) -> dict:
        safe_name   = _safe_filename(file.filename or "attachment")
        file_bytes  = await file.read()
        ext         = Path(file.filename or "").suffix.lower().lstrip(".") or "bin"

        if settings.GCS_ENABLED:
            blob_path = _gcs_upload(file_bytes, file.filename or safe_name,
                                    folder=f"outbound/dispute_{outbound_id}")
        else:
            # Local fallback
            unique_name = f"{uuid.uuid4().hex}_{safe_name}"
            subdir = OUTBOUND_SUBDIR / str(outbound_id)
            subdir.mkdir(parents=True, exist_ok=True)
            (subdir / unique_name).write_bytes(file_bytes)
            blob_path = str(Path("outbound") / str(outbound_id) / unique_name)

        return {
            "file_name": file.filename or safe_name,
            "file_type": ext,
            "file_size": len(file_bytes),
            "file_path": blob_path,
        }

    # ── Main compose + send ───────────────────────────────────────────────────

    async def compose_and_send(
        self,
        *,
        dispute_id:          int,
        sent_by_user_id:     Optional[int],
        to_email:            str,
        subject:             str,
        body_html:           str,
        body_text:           str,
        reply_to_message_id: Optional[int] = None,
        force_new_thread:    bool = False,
        attachments:         Optional[List[UploadFile]] = None,
        # Optional override for AI-agent sends — uses dedicated agent SMTP
        # credentials instead of the mailbox credentials.
        # Pass a dict with keys: smtp_host, smtp_port, smtp_use_tls,
        # username, password_enc, from_address.
        override_smtp_credentials: Optional[dict] = None,
    ) -> OutboundEmail:
        """
        Compose an email, save it, upload attachments, then send via SMTP.
        Creates an OutboundEmail record regardless of send success/failure.
        """
        # 1. Validate dispute exists
        dispute = await self.disp_repo.get_by_id(dispute_id)
        if not dispute:
            raise ResourceNotFoundError("Dispute", dispute_id)

        # 2. Get mailbox
        mb = await self._get_mailbox_for_dispute(dispute_id)

        # 3. Resolve threading headers
        # force_new_thread=True → FA explicitly chose "New Thread" → skip auto-resolve
        # so no In-Reply-To is set and the email starts a fresh conversation in inbox.
        # Otherwise auto-resolve to last inbound so reply stays in the same thread.
        if not force_new_thread and reply_to_message_id is None:
            reply_to_message_id = await self._get_last_inbound_message_id(dispute_id)

        in_reply_to_header: Optional[str] = None
        references_header:  Optional[str] = None

        if reply_to_message_id:
            orig = await self.msg_repo.get_by_id(reply_to_message_id)
            if orig:
                msg_id = orig.message_uid

                # Some email clients don't send a Message-ID header so message_uid
                # is NULL. Fall back to the last outbound email's message_id_header
                # so we can still thread correctly.
                if not msg_id:
                    from sqlalchemy import select as _sa_select
                    from src.data.models.postgres.mailbox_models import OutboundEmail as _OB
                    _ob_row = await self.db.execute(
                        _sa_select(_OB.message_id_header)
                        .where(
                            _OB.dispute_id == dispute_id,
                            _OB.status == "SENT",
                            _OB.message_id_header.isnot(None),
                        )
                        .order_by(_OB.created_at.desc())
                        .limit(1)
                    )
                    _ob = _ob_row.first()
                    msg_id = _ob[0] if _ob else None

                if msg_id:
                    in_reply_to_header = msg_id
                    references_header  = build_references_chain(
                        in_reply_to_message_id=msg_id,
                        parent_references=orig.references_header,
                    )

        # 4. Generate our Message-ID
        our_message_id = generate_message_id(mb.email_address)

        # 5. Create OutboundEmail record (PENDING)
        # When override_smtp_credentials are provided (AI-agent sends), the email
        # is sent FROM the agent address; we still record the mailbox's address
        # in from_email so replies thread back to the correct inbox.
        send_from_address = (
            override_smtp_credentials["from_address"]
            if override_smtp_credentials
            else mb.email_address
        )
        outbound = OutboundEmail(
            dispute_id=dispute_id,
            sent_by_user_id=sent_by_user_id,
            from_email=send_from_address,
            to_email=to_email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            message_id_header=our_message_id,
            in_reply_to_header=in_reply_to_header,
            references_header=references_header,
            status="PENDING",
        )
        self.db.add(outbound)
        await self.db.flush()  # get outbound_id for attachment paths

        # 6. Save attachments
        att_paths: List[tuple] = []
        for file in (attachments or []):
            if not file.filename:
                continue
            att_info = await self._save_upload(file, outbound.outbound_id)
            att = OutboundEmailAttachment(
                outbound_id=outbound.outbound_id,
                file_name=att_info["file_name"],
                file_type=att_info["file_type"],
                file_size=att_info["file_size"],
                file_path=att_info["file_path"],
            )
            self.db.add(att)
            att_paths.append((att_info["file_path"], att_info["file_name"]))

        await self.db.flush()

        # 7. Send via SMTP
        # If override credentials are provided (AI-agent auto-responses), use
        # them; otherwise fall back to the mailbox's own credentials.
        if override_smtp_credentials:
            smtp_host    = override_smtp_credentials["smtp_host"]
            smtp_port    = override_smtp_credentials["smtp_port"]
            smtp_use_tls = override_smtp_credentials["smtp_use_tls"]
            smtp_user    = override_smtp_credentials["username"]
            smtp_pass    = override_smtp_credentials["password_enc"]
            smtp_from    = override_smtp_credentials["from_address"]
        else:
            smtp_host    = mb.effective_smtp_host
            smtp_port    = mb.smtp_port
            smtp_use_tls = mb.smtp_use_tls
            smtp_user    = mb.email_address
            smtp_pass    = mb.password_enc
            smtp_from    = mb.email_address

        try:
            send_email(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_use_tls=smtp_use_tls,
                username=smtp_user,
                password_enc=smtp_pass,
                from_address=smtp_from,
                to_address=to_email,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                message_id=our_message_id,
                in_reply_to=in_reply_to_header,
                references=references_header,
                attachment_paths=att_paths,
            )
            outbound.status  = "SENT"
            outbound.sent_at = datetime.now(timezone.utc)
            logger.info(f"Outbound email sent dispute_id={dispute_id} to={to_email}")
        except Exception as e:
            outbound.status         = "FAILED"
            outbound.failure_reason = str(e)
            logger.error(f"Failed sending email dispute_id={dispute_id}: {e}", exc_info=True)

        # Write a FA_REPLY episode only for human sends so it appears on the
        # timeline. AI auto-response episodes are written by persist_results
        # to avoid double-counting.
        if sent_by_user_id is not None:
            from src.data.models.postgres.memory_models import DisputeMemoryEpisode
            episode = DisputeMemoryEpisode(
                dispute_id=dispute_id,
                episode_type="FA_REPLY",
                actor="ASSOCIATE",
                content_text=body_text,
            )
            self.db.add(episode)

        await self.db.commit()

        # Reload with relationships eagerly so Pydantic serialisation never
        # hits a lazy-load outside the async greenlet
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import selectinload, joinedload
        result = await self.db.execute(
            sa_select(OutboundEmail)
            .options(
                selectinload(OutboundEmail.attachments),
                joinedload(OutboundEmail.sender),
            )
            .where(OutboundEmail.outbound_id == outbound.outbound_id)
        )
        return result.scalar_one()

    # ── List outbound for a dispute ───────────────────────────────────────────

    async def list_for_dispute(self, dispute_id: int) -> List[OutboundEmail]:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload, joinedload
        result = await self.db.execute(
            select(OutboundEmail)
            .options(
                selectinload(OutboundEmail.attachments),
                joinedload(OutboundEmail.sender),
            )
            .where(OutboundEmail.dispute_id == dispute_id)
            .order_by(OutboundEmail.created_at.asc())
        )
        return list(result.scalars().all())

    # ── Serve attachment file ─────────────────────────────────────────────────

    async def get_attachment(self, attachment_id: int) -> OutboundEmailAttachment:
        from sqlalchemy import select
        result = await self.db.execute(
            select(OutboundEmailAttachment)
            .where(OutboundEmailAttachment.attachment_id == attachment_id)
        )
        att = result.scalar_one_or_none()
        if not att:
            raise ResourceNotFoundError("OutboundEmailAttachment", attachment_id)
        return att

    # ── Test mailbox SMTP ─────────────────────────────────────────────────────

    async def test_mailbox_smtp(self, mailbox_id: int) -> dict:
        mb = await self.mb_repo.get_by_id(mailbox_id)
        if not mb:
            raise ResourceNotFoundError("MailboxCredential", mailbox_id)
        ok, msg = test_smtp_connection(
            smtp_host=mb.effective_smtp_host,
            smtp_port=mb.smtp_port,
            smtp_use_tls=mb.smtp_use_tls,
            username=mb.email_address,
            password_enc=mb.password_enc,
        )
        return {"success": ok, "message": msg}
