import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from src.data.repositories.repositories import (
    DisputeRepository, DisputeTypeRepository, DisputeAIAnalysisRepository,
    DisputeAssignmentRepository, MemoryEpisodeRepository,
    MemorySummaryRepository, OpenQuestionRepository, UserRepository,
)
from src.data.models.postgres.models import (
    DisputeAssignment, DisputeActivityLog, DisputeStatusHistory, DisputeOpenQuestion,
)
from src.core.exceptions import (
    DisputeNotFoundError, UserNotFoundError, AnalysisNotFoundError,
    SummaryNotFoundError, QuestionNotFoundError, DisputeTypeNotFoundError,
)
from src.schemas.schemas import (
    DisputeStatusUpdate, DisputeAssignRequest, DisputeTimelineResponse,
    TimelineEpisodeResponse, QuestionStatusUpdate,
)

logger = logging.getLogger(__name__)


class DisputeService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.dispute_repo = DisputeRepository(db)
        self.dtype_repo = DisputeTypeRepository(db)
        self.analysis_repo = DisputeAIAnalysisRepository(db)
        self.assign_repo = DisputeAssignmentRepository(db)
        self.ep_repo = MemoryEpisodeRepository(db)
        self.sum_repo = MemorySummaryRepository(db)
        self.q_repo = OpenQuestionRepository(db)
        self.user_repo = UserRepository(db)

    async def get_dispute(self, dispute_id: int):
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise DisputeNotFoundError(dispute_id)
        return dispute

    async def list_disputes(
        self,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        customer_id: Optional[str] = None,
        assigned_to: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ):
        return await self.dispute_repo.get_filtered(
            status=status,
            priority=priority,
            customer_id=customer_id,
            assigned_to=assigned_to,
            limit=limit,
            offset=offset,
        )

    async def update_status(self, dispute_id: int, data: DisputeStatusUpdate, performed_by: int):
        dispute = await self.get_dispute(dispute_id)
        old_status = dispute.status

        await self.dispute_repo.update_status(dispute_id, data.status)

        log = DisputeActivityLog(
            dispute_id=dispute_id,
            action_type="STATUS_CHANGED",
            performed_by=performed_by,
            notes=f"Status changed from {old_status} to {data.status}. {data.notes or ''}",
        )
        self.db.add(log)

        history = DisputeStatusHistory(
            dispute_id=dispute_id,
            action_type="STATUS_CHANGED",
            performed_by=performed_by,
            notes=f"{old_status} → {data.status}. {data.notes or ''}",
        )
        self.db.add(history)

        # Expire pending questions on closure
        if data.status in ("RESOLVED", "CLOSED"):
            await self.q_repo.expire_all_for_dispute(dispute_id)

        await self.db.commit()

    async def assign_dispute(self, dispute_id: int, data: DisputeAssignRequest, performed_by: int):
        await self.get_dispute(dispute_id)

        await self.assign_repo.deactivate_existing(dispute_id)

        user = await self.user_repo.get_by_id(data.user_id)
        if not user:
            raise UserNotFoundError(data.user_id)

        assignment = DisputeAssignment(
            dispute_id=dispute_id,
            assigned_to=data.user_id,
            status="ACTIVE",
        )
        self.db.add(assignment)

        log = DisputeActivityLog(
            dispute_id=dispute_id,
            action_type="ASSIGNED",
            performed_by=performed_by,
            notes=f"Assigned to {user.name}. {data.notes or ''}",
        )
        self.db.add(log)
        await self.db.commit()
        await self.db.refresh(assignment)
        return assignment, user

    async def get_my_disputes(self, user_id: int, limit: int, offset: int):
        return await self.dispute_repo.get_filtered(
            assigned_to=user_id,
            status=None,
            limit=limit,
            offset=offset,
        )

    async def get_timeline(self, dispute_id: int) -> DisputeTimelineResponse:
        dispute = await self.get_dispute(dispute_id)
        episodes = await self.ep_repo.get_episodes_for_dispute(dispute_id)
        pending_qs = await self.q_repo.get_pending_for_dispute(dispute_id)
        active_assignment = await self.assign_repo.get_active_assignment(dispute_id)

        timeline = [
            TimelineEpisodeResponse(
                episode_id=ep.episode_id,
                actor=ep.actor,
                episode_type=ep.episode_type,
                content_text=ep.content_text,
                created_at=ep.created_at,
            )
            for ep in episodes
        ]

        return DisputeTimelineResponse(
            dispute_id=dispute_id,
            customer_id=dispute.customer_id,
            status=dispute.status,
            timeline=timeline,
            pending_questions=len(pending_qs),
            assigned_to=active_assignment.assignee.email if active_assignment else None,
        )

    async def get_analysis(self, dispute_id: int):
        await self.get_dispute(dispute_id)
        analysis = await self.analysis_repo.get_latest_for_dispute(dispute_id)
        if not analysis:
            raise AnalysisNotFoundError(dispute_id)
        return analysis

    async def reanalyze(self, dispute_id: int):
        dispute = await self.get_dispute(dispute_id)
        episodes = await self.ep_repo.get_latest_n(dispute_id, n=1)
        if not episodes or not episodes[0].email_id:
            raise AnalysisNotFoundError(dispute_id)

        ep = episodes[0]
        from src.control.tasks import process_email_task
        task = process_email_task.delay(
            email_id=ep.email_id,
            sender_email="reanalysis@system",
            subject="Reanalysis trigger",
            body_text=ep.content_text,
            attachment_texts=[],
        )
        return task.id

    async def get_episodes(self, dispute_id: int):
        await self.get_dispute(dispute_id)
        return await self.ep_repo.get_episodes_for_dispute(dispute_id)

    async def get_summary(self, dispute_id: int):
        await self.get_dispute(dispute_id)
        summary = await self.sum_repo.get_for_dispute(dispute_id)
        if not summary:
            raise SummaryNotFoundError(dispute_id)
        return summary

    async def get_open_questions(self, dispute_id: int):
        await self.get_dispute(dispute_id)
        return await self.q_repo.get_all_for_dispute(dispute_id)

    async def update_question_status(
        self, dispute_id: int, question_id: int, data: QuestionStatusUpdate, performed_by: int
    ):
        question = await self.q_repo.get_by_id(question_id)
        if not question or question.dispute_id != dispute_id:
            raise QuestionNotFoundError(question_id)

        question.status = data.status
        if data.status == "ANSWERED":
            question.answered_at = datetime.now(timezone.utc)
        await self.db.commit()
        return question
