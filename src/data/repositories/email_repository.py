# email_repository.py — EmailRepository
from typing import Optional, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from .base import BaseRepository
from src.data.models.postgres import EmailInbox


class EmailRepository(BaseRepository[EmailInbox]):
    def __init__(self, db: AsyncSession):
        super().__init__(EmailInbox, db)

    async def get_by_id(self, email_id: int, **kwargs) -> Optional[EmailInbox]:
        stmt = select(EmailInbox).options(selectinload(EmailInbox.attachments)).where(EmailInbox.email_id == email_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_status(self, status: str, limit: int = 50, offset: int = 0) -> List[EmailInbox]:
        stmt = select(EmailInbox).where(EmailInbox.processing_status == status).order_by(EmailInbox.received_at.desc()).limit(limit).offset(offset)
        return list((await self.db.execute(stmt)).scalars().all())

    async def update_status(self, email_id: int, status: str, failure_reason: Optional[str] = None) -> None:
        values = {"processing_status": status}
        if failure_reason:
            values["failure_reason"] = failure_reason
        await self.db.execute(update(EmailInbox).where(EmailInbox.email_id == email_id).values(**values))

    async def get_by_sender(self, sender_email: str, limit: int = 20) -> List[EmailInbox]:
        stmt = select(EmailInbox).where(EmailInbox.sender_email == sender_email).order_by(EmailInbox.received_at.desc()).limit(limit)
        return list((await self.db.execute(stmt)).scalars().all())
