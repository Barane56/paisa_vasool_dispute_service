from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_
from sqlalchemy.orm import selectinload

from .base import BaseRepository
from src.data.models.postgres.models import (
    User, InvoiceData, PaymentDetail, MatchingPaymentInvoice,
    EmailInbox, EmailAttachment, DisputeType, DisputeMaster,
    DisputeAIAnalysis, AnalysisSupportingRef, DisputeAssignment,
    DisputeActivityLog, DisputeStatusHistory,
    DisputeMemoryEpisode, DisputeMemorySummary, DisputeOpenQuestion,
)


class UserRepository(BaseRepository[User]):
    def __init__(self, db: AsyncSession):
        super().__init__(User, db)

    async def get_by_id(self, user_id: int, **kwargs) -> Optional[User]:
        stmt = select(User).where(User.user_id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        stmt = select(User).where(User.email == email)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()


class InvoiceRepository(BaseRepository[InvoiceData]):
    def __init__(self, db: AsyncSession):
        super().__init__(InvoiceData, db)

    async def get_by_id(self, invoice_id: int, **kwargs) -> Optional[InvoiceData]:
        stmt = select(InvoiceData).where(InvoiceData.invoice_id == invoice_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_invoice_number(self, invoice_number: str) -> Optional[InvoiceData]:
        stmt = select(InvoiceData).where(InvoiceData.invoice_number == invoice_number)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def search_by_number_fuzzy(self, query: str) -> List[InvoiceData]:
        stmt = select(InvoiceData).where(
            InvoiceData.invoice_number.ilike(f"%{query}%")
        ).limit(10)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_all_paginated(self, limit: int = 20, offset: int = 0) -> tuple[List[InvoiceData], int]:
        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(InvoiceData)
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar_one()
        stmt = select(InvoiceData).order_by(InvoiceData.invoice_id.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total


class PaymentRepository(BaseRepository[PaymentDetail]):
    def __init__(self, db: AsyncSession):
        super().__init__(PaymentDetail, db)

    async def get_by_id(self, payment_id: int, **kwargs) -> Optional[PaymentDetail]:
        stmt = select(PaymentDetail).where(PaymentDetail.payment_detail_id == payment_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_customer_and_invoice(self, customer_id: str, invoice_number: str) -> Optional[PaymentDetail]:
        stmt = select(PaymentDetail).where(
            and_(
                PaymentDetail.customer_id == customer_id,
                PaymentDetail.invoice_number == invoice_number,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_customer(self, customer_id: str) -> List[PaymentDetail]:
        stmt = select(PaymentDetail).where(PaymentDetail.customer_id == customer_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


class EmailRepository(BaseRepository[EmailInbox]):
    def __init__(self, db: AsyncSession):
        super().__init__(EmailInbox, db)

    async def get_by_id(self, email_id: int, **kwargs) -> Optional[EmailInbox]:
        stmt = (
            select(EmailInbox)
            .options(selectinload(EmailInbox.attachments))
            .where(EmailInbox.email_id == email_id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_status(self, status: str, limit: int = 50, offset: int = 0) -> List[EmailInbox]:
        stmt = (
            select(EmailInbox)
            .where(EmailInbox.processing_status == status)
            .order_by(EmailInbox.received_at.desc())
            .limit(limit).offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_status(self, email_id: int, status: str, failure_reason: Optional[str] = None) -> None:
        values = {"processing_status": status}
        if failure_reason:
            values["failure_reason"] = failure_reason
        stmt = update(EmailInbox).where(EmailInbox.email_id == email_id).values(**values)
        await self.db.execute(stmt)

    async def get_by_sender(self, sender_email: str, limit: int = 20) -> List[EmailInbox]:
        stmt = (
            select(EmailInbox)
            .where(EmailInbox.sender_email == sender_email)
            .order_by(EmailInbox.received_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


class DisputeTypeRepository(BaseRepository[DisputeType]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeType, db)

    async def get_by_id(self, type_id: int, **kwargs) -> Optional[DisputeType]:
        stmt = select(DisputeType).where(DisputeType.dispute_type_id == type_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_types(self) -> List[DisputeType]:
        stmt = select(DisputeType).where(DisputeType.is_active == True)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Optional[DisputeType]:
        stmt = select(DisputeType).where(DisputeType.reason_name == name)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()


class DisputeRepository(BaseRepository[DisputeMaster]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeMaster, db)

    async def get_by_id(self, dispute_id: int, **kwargs) -> Optional[DisputeMaster]:
        stmt = (
            select(DisputeMaster)
            .options(
                selectinload(DisputeMaster.dispute_type),
                selectinload(DisputeMaster.assignments).selectinload(DisputeAssignment.assignee),
                selectinload(DisputeMaster.ai_analyses),
            )
            .where(DisputeMaster.dispute_id == dispute_id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_filtered(
        self,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        customer_id: Optional[str] = None,
        assigned_to: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[List[DisputeMaster], int]:
        from sqlalchemy import func
        filters = []
        if status:
            filters.append(DisputeMaster.status == status)
        if priority:
            filters.append(DisputeMaster.priority == priority)
        if customer_id:
            filters.append(DisputeMaster.customer_id == customer_id)

        base_stmt = select(DisputeMaster).options(
            selectinload(DisputeMaster.dispute_type)
        )
        if assigned_to:
            base_stmt = base_stmt.join(
                DisputeAssignment,
                and_(
                    DisputeAssignment.dispute_id == DisputeMaster.dispute_id,
                    DisputeAssignment.assigned_to == assigned_to,
                    DisputeAssignment.status == "ACTIVE",
                )
            )
        if filters:
            base_stmt = base_stmt.where(and_(*filters))

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = base_stmt.order_by(DisputeMaster.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())
        return items, total

    async def get_by_customer(self, customer_id: str) -> List[DisputeMaster]:
        stmt = select(DisputeMaster).where(
            and_(
                DisputeMaster.customer_id == customer_id,
                DisputeMaster.status.in_(["OPEN", "UNDER_REVIEW"]),
            )
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_status(self, dispute_id: int, status: str) -> None:
        from datetime import datetime, timezone
        stmt = (
            update(DisputeMaster)
            .where(DisputeMaster.dispute_id == dispute_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )
        await self.db.execute(stmt)


class DisputeAIAnalysisRepository(BaseRepository[DisputeAIAnalysis]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeAIAnalysis, db)

    async def get_latest_for_dispute(self, dispute_id: int) -> Optional[DisputeAIAnalysis]:
        stmt = (
            select(DisputeAIAnalysis)
            .options(selectinload(DisputeAIAnalysis.supporting_refs))
            .where(DisputeAIAnalysis.dispute_id == dispute_id)
            .order_by(DisputeAIAnalysis.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_for_dispute(self, dispute_id: int) -> List[DisputeAIAnalysis]:
        stmt = (
            select(DisputeAIAnalysis)
            .where(DisputeAIAnalysis.dispute_id == dispute_id)
            .order_by(DisputeAIAnalysis.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


class DisputeAssignmentRepository(BaseRepository[DisputeAssignment]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeAssignment, db)

    async def get_active_assignment(self, dispute_id: int) -> Optional[DisputeAssignment]:
        stmt = (
            select(DisputeAssignment)
            .options(selectinload(DisputeAssignment.assignee))
            .where(
                and_(
                    DisputeAssignment.dispute_id == dispute_id,
                    DisputeAssignment.status == "ACTIVE",
                )
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def deactivate_existing(self, dispute_id: int) -> None:
        stmt = (
            update(DisputeAssignment)
            .where(
                and_(
                    DisputeAssignment.dispute_id == dispute_id,
                    DisputeAssignment.status == "ACTIVE",
                )
            )
            .values(status="REASSIGNED")
        )
        await self.db.execute(stmt)


class MemoryEpisodeRepository(BaseRepository[DisputeMemoryEpisode]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeMemoryEpisode, db)

    async def get_episodes_for_dispute(self, dispute_id: int, limit: int = 50) -> List[DisputeMemoryEpisode]:
        stmt = (
            select(DisputeMemoryEpisode)
            .where(DisputeMemoryEpisode.dispute_id == dispute_id)
            .order_by(DisputeMemoryEpisode.created_at.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_for_dispute(self, dispute_id: int) -> int:
        from sqlalchemy import func
        stmt = select(func.count()).where(DisputeMemoryEpisode.dispute_id == dispute_id)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def get_latest_n(self, dispute_id: int, n: int = 5) -> List[DisputeMemoryEpisode]:
        stmt = (
            select(DisputeMemoryEpisode)
            .where(DisputeMemoryEpisode.dispute_id == dispute_id)
            .order_by(DisputeMemoryEpisode.created_at.desc())
            .limit(n)
        )
        result = await self.db.execute(stmt)
        episodes = list(result.scalars().all())
        return list(reversed(episodes))


class MemorySummaryRepository(BaseRepository[DisputeMemorySummary]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeMemorySummary, db)

    async def get_for_dispute(self, dispute_id: int) -> Optional[DisputeMemorySummary]:
        stmt = select(DisputeMemorySummary).where(DisputeMemorySummary.dispute_id == dispute_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()


class OpenQuestionRepository(BaseRepository[DisputeOpenQuestion]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeOpenQuestion, db)

    async def get_pending_for_dispute(self, dispute_id: int) -> List[DisputeOpenQuestion]:
        stmt = (
            select(DisputeOpenQuestion)
            .where(
                and_(
                    DisputeOpenQuestion.dispute_id == dispute_id,
                    DisputeOpenQuestion.status == "PENDING",
                )
            )
            .order_by(DisputeOpenQuestion.created_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_all_for_dispute(self, dispute_id: int) -> List[DisputeOpenQuestion]:
        stmt = (
            select(DisputeOpenQuestion)
            .where(DisputeOpenQuestion.dispute_id == dispute_id)
            .order_by(DisputeOpenQuestion.created_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, question_id: int, **kwargs) -> Optional[DisputeOpenQuestion]:
        stmt = select(DisputeOpenQuestion).where(DisputeOpenQuestion.question_id == question_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def expire_all_for_dispute(self, dispute_id: int) -> None:
        stmt = (
            update(DisputeOpenQuestion)
            .where(
                and_(
                    DisputeOpenQuestion.dispute_id == dispute_id,
                    DisputeOpenQuestion.status == "PENDING",
                )
            )
            .values(status="EXPIRED")
        )
        await self.db.execute(stmt)
