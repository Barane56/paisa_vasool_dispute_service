from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from src.data.clients.postgres import get_db
from src.core.services.dispute_service import DisputeService
from src.api.rest.dependencies import get_current_user
from src.schemas.schemas import (
    CurrentUser, DisputeListResponse, DisputeDetailResponse, DisputeResponse,
    DisputeStatusUpdate, DisputeAssignRequest, DisputeTimelineResponse,
    AIAnalysisResponse, MemorySummaryResponse, TimelineEpisodeResponse,
    OpenQuestionResponse, QuestionStatusUpdate, SuccessResponse, TaskResponse,
)

router = APIRouter(prefix="/disputes", tags=["Disputes"])


@router.get("", response_model=DisputeListResponse)
async def list_disputes(
    status: Optional[str] = Query(None, description="OPEN/UNDER_REVIEW/RESOLVED/CLOSED"),
    priority: Optional[str] = Query(None, description="LOW/MEDIUM/HIGH"),
    customer_id: Optional[str] = Query(None),
    assigned_to: Optional[int] = Query(None, description="Filter by assigned user_id"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all disputes with optional filters."""
    service = DisputeService(db)
    items, total = await service.list_disputes(
        status=status,
        priority=priority,
        customer_id=customer_id,
        assigned_to=assigned_to,
        limit=limit,
        offset=offset,
    )
    return DisputeListResponse(total=total, items=items)


@router.get("/my", response_model=DisputeListResponse)
async def get_my_disputes(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all disputes currently assigned to the logged-in associate."""
    service = DisputeService(db)
    items, total = await service.get_my_disputes(current_user.user_id, limit, offset)
    return DisputeListResponse(total=total, items=items)


@router.get("/{dispute_id}", response_model=DisputeDetailResponse)
async def get_dispute(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get full detail of a dispute including latest analysis and open question count."""
    service = DisputeService(db)
    dispute = await service.get_dispute(dispute_id)

    latest_analysis = None
    try:
        latest_analysis = await service.get_analysis(dispute_id)
    except Exception:
        pass

    pending_qs = await service.get_open_questions(dispute_id)
    active_assign = await service.assign_repo.get_active_assignment(dispute_id)

    return DisputeDetailResponse(
        dispute_id=dispute.dispute_id,
        email_id=dispute.email_id,
        invoice_id=dispute.invoice_id,
        payment_detail_id=dispute.payment_detail_id,
        customer_id=dispute.customer_id,
        dispute_type=dispute.dispute_type,
        status=dispute.status,
        priority=dispute.priority,
        description=dispute.description,
        created_at=dispute.created_at,
        updated_at=dispute.updated_at,
        latest_analysis=latest_analysis,
        open_questions_count=len([q for q in pending_qs if q.status == "PENDING"]),
        assigned_to=active_assign.assignee.email if active_assign else None,
    )


@router.patch("/{dispute_id}/status", response_model=SuccessResponse)
async def update_dispute_status(
    dispute_id: int,
    data: DisputeStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update the status of a dispute. Triggers open question expiry on RESOLVED/CLOSED."""
    service = DisputeService(db)
    await service.update_status(dispute_id, data, current_user.user_id)
    return SuccessResponse(message=f"Dispute {dispute_id} status updated to {data.status}")


@router.post("/{dispute_id}/assign", response_model=SuccessResponse, status_code=status.HTTP_201_CREATED)
async def assign_dispute(
    dispute_id: int,
    data: DisputeAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Assign or reassign a dispute to a finance associate."""
    service = DisputeService(db)
    assignment, user = await service.assign_dispute(dispute_id, data, current_user.user_id)
    return SuccessResponse(
        message=f"Dispute {dispute_id} assigned to {user.name}",
        data={"assignment_id": assignment.assignment_id, "assigned_to": user.email},
    )


@router.get("/{dispute_id}/timeline", response_model=DisputeTimelineResponse)
async def get_dispute_timeline(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Full chronological timeline — all memory episodes (customer emails,
    AI responses, associate replies), pending question count, and assignee.
    Primary endpoint for finance associates to review a dispute.
    """
    service = DisputeService(db)
    return await service.get_timeline(dispute_id)


@router.get("/{dispute_id}/analysis", response_model=AIAnalysisResponse)
async def get_dispute_analysis(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the latest Groq AI analysis for a dispute."""
    service = DisputeService(db)
    return await service.get_analysis(dispute_id)


@router.post("/{dispute_id}/reanalyze", response_model=TaskResponse)
async def reanalyze_dispute(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Trigger re-analysis of a dispute via Groq (e.g. after new info is available)."""
    service = DisputeService(db)
    task_id = await service.reanalyze(dispute_id)
    return TaskResponse(task_id=task_id, status="QUEUED", message="Re-analysis queued")


# ── Memory endpoints ──────────────────────────────────────────────────────────

@router.get("/{dispute_id}/episodes", response_model=List[TimelineEpisodeResponse])
async def get_dispute_episodes(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all memory episodes for a dispute in chronological order."""
    service = DisputeService(db)
    episodes = await service.get_episodes(dispute_id)
    return [
        TimelineEpisodeResponse(
            episode_id=ep.episode_id,
            actor=ep.actor,
            episode_type=ep.episode_type,
            content_text=ep.content_text,
            created_at=ep.created_at,
        )
        for ep in episodes
    ]


@router.get("/{dispute_id}/summary", response_model=MemorySummaryResponse)
async def get_dispute_summary(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the current Groq-generated rolling memory summary for a dispute."""
    service = DisputeService(db)
    return await service.get_summary(dispute_id)


@router.get("/{dispute_id}/open-questions", response_model=List[OpenQuestionResponse])
async def get_open_questions(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all questions asked to the customer with their current answer status."""
    service = DisputeService(db)
    questions = await service.get_open_questions(dispute_id)
    return [
        OpenQuestionResponse(
            question_id=q.question_id,
            question_text=q.question_text,
            status=q.status,
            asked_at=q.created_at,
            answered_at=q.answered_at,
        )
        for q in questions
    ]


@router.patch("/{dispute_id}/open-questions/{question_id}", response_model=SuccessResponse)
async def update_question_status(
    dispute_id: int,
    question_id: int,
    data: QuestionStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Manually mark a pending question as ANSWERED or EXPIRED."""
    service = DisputeService(db)
    await service.update_question_status(dispute_id, question_id, data, current_user.user_id)
    return SuccessResponse(message=f"Question {question_id} marked as {data.status}")
