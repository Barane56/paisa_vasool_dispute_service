"""
Repositories – all DB access for the dispute resolution service.

NEW in this version
───────────────────
MemoryEpisodeRepository
  • upsert_embedding(episode_id, embedding)
      Saves the AI-summary embedding into content_embedding column.

  • search_similar_by_customer(customer_id, query_embedding, top_k, threshold)
      pgvector cosine similarity search scoped to a single customer_id.
      Joins dispute_memory_episode → dispute_master to scope by customer.
      Returns top-k episodes above the similarity threshold, ordered by score.

Embedding model swap
────────────────────
The vector dimension is read from settings.EMBEDDING_DIMS at query time.
When you upgrade from bge-small (384) to bge-base (768):
  1. settings.py  → EMBEDDING_MODEL / EMBEDDING_DIMS
  2. SQL          → ALTER TABLE dispute_memory_episode
                    ALTER COLUMN content_embedding TYPE vector(768);
  3. Re-embed existing episodes (optional backfill job)
Nothing in this file needs to change.
"""

from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, update, and_, text
import sqlalchemy as sa
from sqlalchemy.orm import selectinload
from sqlalchemy import select, func
from typing import List

from .base import BaseRepository
from src.data.models.postgres.models import (
    User, InvoiceData, PaymentDetail, MatchingPaymentInvoice,
    EmailInbox, EmailAttachment, DisputeType, DisputeMaster,
    DisputeAIAnalysis, AnalysisSupportingRef, DisputeAssignment,
    DisputeActivityLog, DisputeStatusHistory,
    DisputeMemoryEpisode, DisputeMemorySummary, DisputeOpenQuestion, UserRole,Role
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


class UserRoleRepository(BaseRepository[UserRole]):
    
    def __init__(self, db: AsyncSession):
        super().__init__(UserRole, db)

    async def get_all_fa(self) -> List[int]:

        # get admin role
        stmt = await self.db.execute(
            select(Role).where(Role.role_name == "admin")
        )
        admin_role = stmt.scalar_one_or_none()

        if not admin_role:
            raise Exception("Admin Role not found, Aborting")

        # get 10 random FA (users not having admin role)
        stmt = await self.db.execute(
            select(UserRole.user_id)
            .where(UserRole.role_id != admin_role.role_id)
            .order_by(func.random())
            .limit(10)
        )

        fa_ids = stmt.scalars().all()

        return fa_ids


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
        ).limit(1)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_all_by_invoice_number(self, invoice_number: str) -> List[PaymentDetail]:
        stmt = (
            select(PaymentDetail)
            .where(PaymentDetail.invoice_number == invoice_number)
            .order_by(PaymentDetail.payment_detail_id.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_all_by_customer(self, customer_id: str, limit: int = 50, offset: int = 0) -> tuple[List[PaymentDetail], int]:
        count_stmt = select(func.count()).select_from(PaymentDetail).where(
            PaymentDetail.customer_id == customer_id
        )
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar_one()
        stmt = (
            select(PaymentDetail)
            .where(PaymentDetail.customer_id == customer_id)
            .order_by(PaymentDetail.payment_detail_id.desc())
            .limit(limit).offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total

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

    async def get_by_dispute_token(self, token: str) -> Optional[DisputeMaster]:
        """Layer 1 match: look up dispute by its unique DISP-XXXXX token."""
        stmt = select(DisputeMaster).where(DisputeMaster.dispute_token == token)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_filtered(
        self,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        customer_id: Optional[str] = None,
        assigned_to: Optional[int] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[List[DisputeMaster], int]:
        from sqlalchemy import or_
        from src.data.models.postgres.models import DisputeType as DT
        filters = []
        if status:
            filters.append(DisputeMaster.status == status)
        if priority:
            filters.append(DisputeMaster.priority == priority)
        if customer_id:
            filters.append(DisputeMaster.customer_id == customer_id)

        base_stmt = select(DisputeMaster).options(selectinload(DisputeMaster.dispute_type))

        if search:
            q = f"%{search}%"
            base_stmt = base_stmt.outerjoin(DT, DT.dispute_type_id == DisputeMaster.dispute_type_id)
            filters.append(
                or_(
                    DisputeMaster.customer_id.ilike(q),
                    DisputeMaster.description.ilike(q),
                    func.cast(DisputeMaster.dispute_id, sa.String).ilike(q),
                    DT.reason_name.ilike(q),
                )
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
        return list(result.scalars().all()), total

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

    async def has_active_assignment(self, dispute_id: int) -> bool:
        stmt = (
            select(func.count(DisputeAssignment.assignment_id))
            .where(
                and_(
                    DisputeAssignment.dispute_id == dispute_id,
                    DisputeAssignment.status == "ACTIVE",
                )
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one() > 0

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
            .order_by(DisputeAssignment.assigned_at.desc())
            .limit(1)
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
        return list(reversed(result.scalars().all()))

    async def upsert_embedding(self, episode_id: int, embedding: List[float]) -> None:
        """
        Save the AI-summary embedding vector into content_embedding for an episode.
        Called after generate_ai_response produces the ai_summary.

        The vector dimension must match the column type (currently vector(384) for
        bge-small-en-v1.5). When upgrading models, run the SQL migration first, then
        update settings.EMBEDDING_DIMS — this method needs no changes.
        """
        stmt = (
            update(DisputeMemoryEpisode)
            .where(DisputeMemoryEpisode.episode_id == episode_id)
            .values(content_embedding=embedding)
        )
        await self.db.execute(stmt)
        await self.db.flush()

    async def search_similar_by_customer(
        self,
        customer_id: str,
        query_embedding: List[float],
        top_k: int = 5,
        threshold: float = 0.75,
    ) -> List[dict]:
        """
        pgvector cosine similarity search for episodes belonging to a customer.

        Flow:
          dispute_memory_episode
            JOIN dispute_master ON dispute_id        ← scopes to this customer
          WHERE customer_id = :customer_id
            AND content_embedding IS NOT NULL        ← skip un-embedded episodes
            AND 1 - (embedding <=> query) >= threshold
          ORDER BY similarity DESC
          LIMIT top_k

        Returns a list of dicts:
          {
            "episode_id":   int,
            "dispute_id":   int,
            "episode_type": str,
            "actor":        str,
            "content_text": str,       ← first 400 chars
            "similarity":   float,     ← cosine similarity 0→1
            "created_at":  datetime,
          }

        Threshold guide (cosine similarity):
          ≥ 0.90  → near-duplicate / same issue re-sent
          ≥ 0.80  → very likely same dispute topic
          ≥ 0.75  → probable match (default)
          ≥ 0.65  → loose / related topic
        """
        # Build the vector literal pgvector expects: '[0.1,0.2,...]'
        vec_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

        sql = text("""
            SELECT
                e.episode_id,
                e.dispute_id,
                e.episode_type,
                e.actor,
                e.content_text,
                e.created_at,
                1 - (e.content_embedding <=> CAST(:vec AS vector)) AS similarity
            FROM dispute_memory_episode e
            JOIN dispute_master d ON d.dispute_id = e.dispute_id
            WHERE d.customer_id   = :customer_id
              AND e.content_embedding IS NOT NULL
              AND 1 - (e.content_embedding <=> CAST(:vec AS vector)) >= :threshold
            ORDER BY similarity DESC
            LIMIT :top_k
        """)

        result = await self.db.execute(
            sql,
            {
                "vec":         vec_literal,
                "customer_id": customer_id,
                "threshold":   threshold,
                "top_k":       top_k,
            },
        )
        rows = result.mappings().all()

        return [
            {
                "episode_id":   row["episode_id"],
                "dispute_id":   row["dispute_id"],
                "episode_type": row["episode_type"],
                "actor":        row["actor"],
                "content_text": row["content_text"][:400],
                "similarity":   round(float(row["similarity"]), 4),
                "created_at":   row["created_at"],
            }
            for row in rows
        ]


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


class AnalysisSupportingRefRepository(BaseRepository[AnalysisSupportingRef]):
    def __init__(self, db: AsyncSession):
        super().__init__(AnalysisSupportingRef, db)

    async def get_by_analysis(self, analysis_id: int) -> List[AnalysisSupportingRef]:
        stmt = (
            select(AnalysisSupportingRef)
            .where(AnalysisSupportingRef.analysis_id == analysis_id)
            .order_by(AnalysisSupportingRef.ref_id.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_dispute_via_analysis(self, dispute_id: int) -> List[AnalysisSupportingRef]:
        stmt = (
            select(AnalysisSupportingRef)
            .join(DisputeAIAnalysis, AnalysisSupportingRef.analysis_id == DisputeAIAnalysis.analysis_id)
            .where(DisputeAIAnalysis.dispute_id == dispute_id)
            .order_by(AnalysisSupportingRef.ref_id.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def upsert_supporting_doc(
        self,
        analysis_id: int,
        reference_table: str,
        ref_id_value: int,
        context_note: str,
    ) -> AnalysisSupportingRef:
        stmt = select(AnalysisSupportingRef).where(
            and_(
                AnalysisSupportingRef.analysis_id == analysis_id,
                AnalysisSupportingRef.reference_table == reference_table,
                AnalysisSupportingRef.ref_id_value == ref_id_value,
            )
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.context_note = context_note
            await self.db.flush()
            return existing
        ref = AnalysisSupportingRef(
            analysis_id=analysis_id,
            reference_table=reference_table,
            ref_id_value=ref_id_value,
            context_note=context_note,
        )
        self.db.add(ref)
        await self.db.flush()
        return ref

    async def delete_ref(self, ref_id: int) -> bool:
        stmt = select(AnalysisSupportingRef).where(AnalysisSupportingRef.ref_id == ref_id)
        result = await self.db.execute(stmt)
        ref = result.scalar_one_or_none()
        if not ref:
            return False
        await self.db.delete(ref)
        await self.db.flush()
        return True

class DisputeRelationshipRepository(BaseRepository):
    """
    Manages explicit relationships between disputes (forks, batches, escalations).
    """

    def __init__(self, db: AsyncSession):
        from src.data.models.postgres.models import DisputeRelationship
        super().__init__(DisputeRelationship, db)

    async def create(
        self,
        source_dispute_id: int,
        target_dispute_id: int,
        relationship_type: str,
        context_note: Optional[str] = None,
        created_by: str = "SYSTEM",
    ):
        from src.data.models.postgres.models import DisputeRelationship
        rel = DisputeRelationship(
            source_dispute_id=source_dispute_id,
            target_dispute_id=target_dispute_id,
            relationship_type=relationship_type,
            context_note=context_note,
            created_by=created_by,
        )
        self.db.add(rel)
        await self.db.flush()
        return rel

    async def get_related_disputes(self, dispute_id: int) -> List:
        from src.data.models.postgres.models import DisputeRelationship
        from sqlalchemy import or_
        stmt = (
            select(DisputeRelationship)
            .where(
                or_(
                    DisputeRelationship.source_dispute_id == dispute_id,
                    DisputeRelationship.target_dispute_id == dispute_id,
                )
            )
            .order_by(DisputeRelationship.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def relationship_exists(self, source_id: int, target_id: int) -> bool:
        from src.data.models.postgres.models import DisputeRelationship
        from sqlalchemy import or_, and_
        stmt = select(DisputeRelationship).where(
            or_(
                and_(DisputeRelationship.source_dispute_id == source_id,
                     DisputeRelationship.target_dispute_id == target_id),
                and_(DisputeRelationship.source_dispute_id == target_id,
                     DisputeRelationship.target_dispute_id == source_id),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None