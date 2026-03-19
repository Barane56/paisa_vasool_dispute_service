# mailbox_repository.py — MailboxRepository, EmailInboxMessageRepository, EmailMessageAttachmentRepository
from typing import Optional, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .base import BaseRepository
from src.data.models.postgres.mailbox_models import (
    MailboxCredential, EmailInboxMessage, EmailMessageAttachment,
)


class MailboxRepository(BaseRepository[MailboxCredential]):
    def __init__(self, db: AsyncSession):
        super().__init__(MailboxCredential, db)

    async def get_by_id(self, mailbox_id: int, **kwargs) -> Optional[MailboxCredential]:
        stmt = select(MailboxCredential).where(MailboxCredential.mailbox_id == mailbox_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, email_address: str) -> Optional[MailboxCredential]:
        stmt = select(MailboxCredential).where(MailboxCredential.email_address == email_address)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> List[MailboxCredential]:
        stmt = select(MailboxCredential).order_by(MailboxCredential.created_at.desc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_active_for_polling(self) -> List[MailboxCredential]:
        """Returns mailboxes that are active and not paused — used by the beat task."""
        stmt = (
            select(MailboxCredential)
            .where(MailboxCredential.is_active == True)
            .where(MailboxCredential.is_paused == False)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def update_last_polled(self, mailbox_id: int, last_uid_seen: Optional[int] = None) -> None:
        from datetime import datetime, timezone
        values = {"last_polled_at": datetime.now(timezone.utc)}
        if last_uid_seen is not None:
            # Only advance the cursor — never regress it if a concurrent worker
            # already wrote a higher value
            await self.db.execute(
                update(MailboxCredential)
                .where(
                    MailboxCredential.mailbox_id == mailbox_id,
                    (MailboxCredential.last_uid_seen == None) |
                    (MailboxCredential.last_uid_seen < last_uid_seen),
                )
                .values(last_polled_at=datetime.now(timezone.utc), last_uid_seen=last_uid_seen)
            )
            return
        await self.db.execute(
            update(MailboxCredential)
            .where(MailboxCredential.mailbox_id == mailbox_id)
            .values(**values)
        )

    async def set_paused(self, mailbox_id: int, paused: bool) -> Optional[MailboxCredential]:
        await self.db.execute(
            update(MailboxCredential)
            .where(MailboxCredential.mailbox_id == mailbox_id)
            .values(is_paused=paused)
        )
        await self.db.flush()
        return await self.get_by_id(mailbox_id)

    async def delete(self, mailbox_id: int) -> bool:
        mb = await self.get_by_id(mailbox_id)
        if not mb:
            return False
        await self.db.delete(mb)
        await self.db.flush()
        return True


class EmailInboxMessageRepository(BaseRepository[EmailInboxMessage]):
    def __init__(self, db: AsyncSession):
        super().__init__(EmailInboxMessage, db)

    async def get_by_id(self, message_id: int, **kwargs) -> Optional[EmailInboxMessage]:
        stmt = (
            select(EmailInboxMessage)
            .options(selectinload(EmailInboxMessage.attachments))
            .where(EmailInboxMessage.message_id == message_id)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_imap_uid(self, mailbox_id: int, imap_uid: int) -> Optional[EmailInboxMessage]:
        stmt = select(EmailInboxMessage).where(
            EmailInboxMessage.mailbox_id == mailbox_id,
            EmailInboxMessage.imap_uid == imap_uid,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_for_dispute(self, dispute_id: int) -> List[EmailInboxMessage]:
        stmt = (
            select(EmailInboxMessage)
            .options(selectinload(EmailInboxMessage.attachments))
            .where(EmailInboxMessage.dispute_id == dispute_id)
            .order_by(EmailInboxMessage.received_at.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_inbox(
        self,
        mailbox_id: Optional[int] = None,
        source: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[EmailInboxMessage]:
        stmt = (
            select(EmailInboxMessage)
            .options(selectinload(EmailInboxMessage.attachments))
            .order_by(EmailInboxMessage.received_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if mailbox_id is not None:
            stmt = stmt.where(EmailInboxMessage.mailbox_id == mailbox_id)
        if source:
            stmt = stmt.where(EmailInboxMessage.source == source)
        return list((await self.db.execute(stmt)).scalars().all())

    async def update_status(self, message_id: int, status: str, failure_reason: Optional[str] = None) -> None:
        values = {"processing_status": status}
        if failure_reason:
            values["failure_reason"] = failure_reason
        await self.db.execute(
            update(EmailInboxMessage)
            .where(EmailInboxMessage.message_id == message_id)
            .values(**values)
        )

    async def link_dispute(self, message_id: int, dispute_id: int) -> None:
        await self.db.execute(
            update(EmailInboxMessage)
            .where(EmailInboxMessage.message_id == message_id)
            .values(dispute_id=dispute_id)
        )
