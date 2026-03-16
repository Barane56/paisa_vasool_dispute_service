"""
src/core/services/mailbox_service.py
"""
from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.services.imap_service import encode_password, test_mailbox_connection
from src.core.services.smtp_service import test_smtp_connection
from src.core.exceptions.errors import ResourceNotFoundError, ValidationError as PVValidationError
from src.data.repositories.mailbox_repository import MailboxRepository, EmailInboxMessageRepository
from src.data.models.postgres.mailbox_models import MailboxCredential, EmailInboxMessage

logger = logging.getLogger(__name__)


class MailboxService:
    def __init__(self, db: AsyncSession):
        self.repo     = MailboxRepository(db)
        self.msg_repo = EmailInboxMessageRepository(db)
        self.db       = db

    # ── CRUD ─────────────────────────────────────────────────────────────────

    async def add_mailbox(
        self,
        label: str,
        email_address: str,
        imap_host: str,
        imap_port: int,
        use_ssl: bool,
        password: str,
        smtp_host: Optional[str] = None,
        smtp_port: int = 587,
        smtp_use_tls: bool = True,
    ) -> MailboxCredential:
        existing = await self.repo.get_by_email(email_address)
        if existing:
            raise PVValidationError(f"Mailbox {email_address} already exists (id={existing.mailbox_id})")

        mb = MailboxCredential(
            label=label,
            email_address=email_address,
            imap_host=imap_host,
            imap_port=imap_port,
            use_ssl=use_ssl,
            password_enc=encode_password(password),
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_use_tls=smtp_use_tls,
        )
        self.db.add(mb)
        await self.db.commit()
        await self.db.refresh(mb)
        logger.info(f"Mailbox added: {email_address} (id={mb.mailbox_id})")
        return mb

    async def list_mailboxes(self) -> List[MailboxCredential]:
        return await self.repo.list_all()

    async def get_mailbox(self, mailbox_id: int) -> MailboxCredential:
        mb = await self.repo.get_by_id(mailbox_id)
        if not mb:
            raise ResourceNotFoundError("MailboxCredential", mailbox_id)
        return mb

    async def delete_mailbox(self, mailbox_id: int) -> None:
        if not await self.repo.delete(mailbox_id):
            raise ResourceNotFoundError("MailboxCredential", mailbox_id)
        await self.db.commit()

    async def pause_mailbox(self, mailbox_id: int) -> MailboxCredential:
        mb = await self.repo.set_paused(mailbox_id, True)
        if not mb:
            raise ResourceNotFoundError("MailboxCredential", mailbox_id)
        await self.db.commit()
        return mb

    async def unpause_mailbox(self, mailbox_id: int) -> MailboxCredential:
        mb = await self.repo.set_paused(mailbox_id, False)
        if not mb:
            raise ResourceNotFoundError("MailboxCredential", mailbox_id)
        await self.db.commit()
        return mb

    async def test_mailbox(self, mailbox_id: int) -> dict:
        """Tests both IMAP and SMTP connections. Used by frontend after adding."""
        mb = await self.get_mailbox(mailbox_id)

        imap_ok, imap_msg = test_mailbox_connection(
            imap_host=mb.imap_host,
            imap_port=mb.imap_port,
            use_ssl=mb.use_ssl,
            email_address=mb.email_address,
            password_enc=mb.password_enc,
        )
        smtp_ok, smtp_msg = test_smtp_connection(
            smtp_host=mb.effective_smtp_host,
            smtp_port=mb.smtp_port,
            smtp_use_tls=mb.smtp_use_tls,
            username=mb.email_address,
            password_enc=mb.password_enc,
        )
        combined_msg = f"IMAP: {imap_msg} | SMTP: {smtp_msg}"
        return {"imap_ok": imap_ok, "smtp_ok": smtp_ok, "message": combined_msg}

    # ── Inbox (simulated) ─────────────────────────────────────────────────────

    async def list_inbox(
        self,
        mailbox_id: Optional[int] = None,
        source: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[EmailInboxMessage]:
        return await self.msg_repo.list_inbox(
            mailbox_id=mailbox_id, source=source, limit=limit, offset=offset,
        )

    async def get_message(self, message_id: int) -> EmailInboxMessage:
        msg = await self.msg_repo.get_by_id(message_id)
        if not msg:
            raise ResourceNotFoundError("EmailInboxMessage", message_id)
        return msg

    async def list_messages_for_dispute(self, dispute_id: int) -> List[EmailInboxMessage]:
        return await self.msg_repo.list_for_dispute(dispute_id)
