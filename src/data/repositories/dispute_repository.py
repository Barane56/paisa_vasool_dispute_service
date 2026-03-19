# dispute_repository.py — DisputeTypeRepository, DisputeRepository,
#                         DisputeAIAnalysisRepository, DisputeAssignmentRepository,
#                         AnalysisSupportingRefRepository, DisputeRelationshipRepository
from datetime import datetime, timezone
from typing import Optional, List
import sqlalchemy as sa
from sqlalchemy import select, update, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from .base import BaseRepository
from src.data.models.postgres import (
    DisputeType, DisputeMaster, DisputeAssignment,
    DisputeAIAnalysis, AnalysisSupportingRef, DisputeRelationship,
)
from src.data.models.postgres.dispute_models import DisputeNewMessage


class DisputeTypeRepository(BaseRepository[DisputeType]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeType, db)

    async def get_by_id(self, type_id: int, **kwargs) -> Optional[DisputeType]:
        result = await self.db.execute(select(DisputeType).where(DisputeType.dispute_type_id == type_id))
        return result.scalar_one_or_none()

    async def get_active_types(self) -> List[DisputeType]:
        result = await self.db.execute(select(DisputeType).where(DisputeType.is_active == True))
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Optional[DisputeType]:
        result = await self.db.execute(select(DisputeType).where(DisputeType.reason_name == name))
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
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_dispute_token(self, token: str) -> Optional[DisputeMaster]:
        result = await self.db.execute(select(DisputeMaster).where(DisputeMaster.dispute_token == token))
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
        filters = []
        if status:      filters.append(DisputeMaster.status == status)
        if priority:    filters.append(DisputeMaster.priority == priority)
        if customer_id: filters.append(DisputeMaster.customer_id == customer_id)

        base_stmt = select(DisputeMaster).options(selectinload(DisputeMaster.dispute_type))

        if search:
            q = f"%{search}%"
            base_stmt = base_stmt.outerjoin(DisputeType, DisputeType.dispute_type_id == DisputeMaster.dispute_type_id)
            filters.append(or_(
                DisputeMaster.customer_id.ilike(q),
                DisputeMaster.description.ilike(q),
                func.cast(DisputeMaster.dispute_id, sa.String).ilike(q),
                DisputeType.reason_name.ilike(q),
            ))

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

        total = (await self.db.execute(select(func.count()).select_from(base_stmt.subquery()))).scalar_one()
        stmt = base_stmt.order_by(DisputeMaster.created_at.desc()).limit(limit).offset(offset)
        return list((await self.db.execute(stmt)).scalars().all()), total

    async def get_by_customer(self, customer_id: str) -> List[DisputeMaster]:
        stmt = (
            select(DisputeMaster)
            .where(and_(
                DisputeMaster.customer_id == customer_id,
                DisputeMaster.status.in_(["OPEN", "UNDER_REVIEW"]),
            ))
            # FA_MANUAL disputes first — they are explicitly waiting for customer response.
            # Within same source, most recently updated first.
            .order_by(
                sa.case(
                    (DisputeMaster.source == "FA_MANUAL", 0),
                    else_=1
                ),
                DisputeMaster.updated_at.desc(),
            )
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def update_status(self, dispute_id: int, status: str) -> None:
        await self.db.execute(
            update(DisputeMaster).where(DisputeMaster.dispute_id == dispute_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )


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
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_all_for_dispute(self, dispute_id: int) -> List[DisputeAIAnalysis]:
        stmt = select(DisputeAIAnalysis).where(DisputeAIAnalysis.dispute_id == dispute_id).order_by(DisputeAIAnalysis.created_at.desc())
        return list((await self.db.execute(stmt)).scalars().all())


class DisputeAssignmentRepository(BaseRepository[DisputeAssignment]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeAssignment, db)

    async def has_active_assignment(self, dispute_id: int) -> bool:
        stmt = select(func.count(DisputeAssignment.assignment_id)).where(and_(
            DisputeAssignment.dispute_id == dispute_id, DisputeAssignment.status == "ACTIVE",
        ))
        return (await self.db.execute(stmt)).scalar_one() > 0

    async def get_active_assignment(self, dispute_id: int) -> Optional[DisputeAssignment]:
        stmt = (
            select(DisputeAssignment)
            .options(selectinload(DisputeAssignment.assignee))
            .where(and_(DisputeAssignment.dispute_id == dispute_id, DisputeAssignment.status == "ACTIVE"))
            .order_by(DisputeAssignment.assigned_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def deactivate_existing(self, dispute_id: int) -> None:
        await self.db.execute(
            update(DisputeAssignment)
            .where(and_(DisputeAssignment.dispute_id == dispute_id, DisputeAssignment.status == "ACTIVE"))
            .values(status="REASSIGNED")
        )


class AnalysisSupportingRefRepository(BaseRepository[AnalysisSupportingRef]):
    def __init__(self, db: AsyncSession):
        super().__init__(AnalysisSupportingRef, db)

    async def get_by_analysis(self, analysis_id: int) -> List[AnalysisSupportingRef]:
        stmt = select(AnalysisSupportingRef).where(AnalysisSupportingRef.analysis_id == analysis_id).order_by(AnalysisSupportingRef.ref_id.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_by_dispute_via_analysis(self, dispute_id: int) -> List[AnalysisSupportingRef]:
        stmt = (
            select(AnalysisSupportingRef)
            .join(DisputeAIAnalysis, AnalysisSupportingRef.analysis_id == DisputeAIAnalysis.analysis_id)
            .where(DisputeAIAnalysis.dispute_id == dispute_id)
            .order_by(AnalysisSupportingRef.ref_id.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def upsert_supporting_doc(self, analysis_id: int, reference_table: str, ref_id_value: int, context_note: str) -> AnalysisSupportingRef:
        stmt = select(AnalysisSupportingRef).where(and_(
            AnalysisSupportingRef.analysis_id == analysis_id,
            AnalysisSupportingRef.reference_table == reference_table,
            AnalysisSupportingRef.ref_id_value == ref_id_value,
        ))
        existing = (await self.db.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.context_note = context_note
            await self.db.flush()
            return existing
        ref = AnalysisSupportingRef(analysis_id=analysis_id, reference_table=reference_table, ref_id_value=ref_id_value, context_note=context_note)
        self.db.add(ref)
        await self.db.flush()
        return ref

    async def delete_ref(self, ref_id: int) -> bool:
        ref = (await self.db.execute(select(AnalysisSupportingRef).where(AnalysisSupportingRef.ref_id == ref_id))).scalar_one_or_none()
        if not ref:
            return False
        await self.db.delete(ref)
        await self.db.flush()
        return True


class DisputeRelationshipRepository(BaseRepository[DisputeRelationship]):
    """Manages explicit relationships between disputes (forks, batches, escalations)."""

    def __init__(self, db: AsyncSession):
        super().__init__(DisputeRelationship, db)

    async def create(self, source_dispute_id: int, target_dispute_id: int, relationship_type: str, context_note: Optional[str] = None, created_by: str = "SYSTEM") -> DisputeRelationship:
        rel = DisputeRelationship(source_dispute_id=source_dispute_id, target_dispute_id=target_dispute_id, relationship_type=relationship_type, context_note=context_note, created_by=created_by)
        self.db.add(rel)
        await self.db.flush()
        return rel

    async def get_related_disputes(self, dispute_id: int) -> List[DisputeRelationship]:
        stmt = select(DisputeRelationship).where(or_(
            DisputeRelationship.source_dispute_id == dispute_id,
            DisputeRelationship.target_dispute_id == dispute_id,
        )).order_by(DisputeRelationship.created_at.desc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def relationship_exists(self, source_id: int, target_id: int) -> bool:
        stmt = select(DisputeRelationship).where(or_(
            and_(DisputeRelationship.source_dispute_id == source_id, DisputeRelationship.target_dispute_id == target_id),
            and_(DisputeRelationship.source_dispute_id == target_id, DisputeRelationship.target_dispute_id == source_id),
        ))
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None


class DisputeNewMessageRepository:
    """
    Manages the dispute_new_message table.
    One row per dispute — upserted when a CUSTOMER episode arrives,
    cleared when an FA marks the dispute as read.
    """
    def __init__(self, db: AsyncSession):
        self.db = db

    async def set_new_message(self, dispute_id: int) -> None:
        """Mark a dispute as having a new unread customer message."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        stmt = pg_insert(DisputeNewMessage).values(
            dispute_id=dispute_id,
            has_new_message=True,
            arrived_at=now,
            updated_at=now,
        ).on_conflict_do_update(
            index_elements=["dispute_id"],
            set_={"has_new_message": True, "arrived_at": now, "updated_at": now},
        )
        await self.db.execute(stmt)

    async def clear_new_message(self, dispute_id: int) -> None:
        """FA has read the dispute — clear the flag."""
        from datetime import datetime, timezone
        stmt = (
            update(DisputeNewMessage)
            .where(DisputeNewMessage.dispute_id == dispute_id)
            .values(has_new_message=False, updated_at=datetime.now(timezone.utc))
        )
        await self.db.execute(stmt)

    async def get_all_unread(self) -> list[int]:
        """Return dispute_ids that have unread customer messages."""
        result = await self.db.execute(
            select(DisputeNewMessage.dispute_id)
            .where(DisputeNewMessage.has_new_message == True)
        )
        return [row.dispute_id for row in result.all()]

    async def get_for_dispute(self, dispute_id: int) -> bool:
        """Return True if this dispute has an unread customer message."""
        row = (await self.db.execute(
            select(DisputeNewMessage.has_new_message)
            .where(DisputeNewMessage.dispute_id == dispute_id)
        )).scalar_one_or_none()
        return bool(row)
