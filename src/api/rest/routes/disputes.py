"""
src/api/rest/routes/disputes.py
================================
Thin route layer — all business/query logic lives in DisputeService.
Routes only handle HTTP concerns: extract params, call service, return response.
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from src.data.clients.postgres import get_db
from src.core.services.dispute_service import DisputeService
from src.core.services.draft_email_service import generate_draft_email
from src.api.rest.dependencies import get_current_user
from src.schemas.schemas import (
    CurrentUser, DisputeListResponse, DisputeDetailResponse, DisputeResponse,
    DisputeStatusUpdate, DisputeAssignRequest, DisputeTimelineResponse,
    AIAnalysisResponse, MemorySummaryResponse, TimelineEpisodeResponse,
    OpenQuestionResponse, QuestionStatusUpdate, SuccessResponse, TaskResponse,
    DraftEmailResponse,
)

router = APIRouter(prefix="/disputes", tags=["Disputes"])


@router.get("", response_model=DisputeListResponse)
async def list_disputes(
    status: Optional[str] = Query(None, description="OPEN/UNDER_REVIEW/RESOLVED/CLOSED"),
    priority: Optional[str] = Query(None, description="LOW/MEDIUM/HIGH"),
    customer_id: Optional[str] = Query(None),
    assigned_to: Optional[int] = Query(None, description="Filter by assigned user_id"),
    search: Optional[str] = Query(None, description="Search by customer_id, description, dispute_id, or type"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List disputes with filters. Returns enriched items in a single request."""
    service = DisputeService(db)
    enriched, total = await service.get_enriched_list(
        status=status, priority=priority, customer_id=customer_id,
        assigned_to=assigned_to, search=search, limit=limit, offset=offset,
    )
    return DisputeListResponse(total=total, items=enriched)


@router.get("/bulk-detail", response_model=List[DisputeDetailResponse])
async def bulk_get_dispute_detail(
    ids: str = Query(..., description="Comma-separated dispute_ids e.g. ?ids=1,2,3"),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch enriched detail for multiple disputes in one request."""
    id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    return await DisputeService(db).get_bulk_enriched(id_list)


@router.get("/my", response_model=DisputeListResponse)
async def get_my_disputes(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all disputes assigned to the logged-in associate."""
    service = DisputeService(db)
    enriched, total = await service.get_enriched_list(
        assigned_to=current_user.user_id, limit=limit, offset=offset,
    )
    return DisputeListResponse(total=total, items=enriched)


@router.get("/{dispute_id}", response_model=DisputeDetailResponse)
async def get_dispute(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get full detail of a dispute."""
    return await DisputeService(db).get_enriched_detail(dispute_id)


@router.patch("/{dispute_id}/status", response_model=SuccessResponse)
async def update_dispute_status(
    dispute_id: int,
    data: DisputeStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update the status of a dispute."""
    await DisputeService(db).update_status(dispute_id, data, current_user.user_id)
    return SuccessResponse(message=f"Dispute {dispute_id} status updated to {data.status}")


@router.post("/{dispute_id}/assign", response_model=SuccessResponse, status_code=status.HTTP_201_CREATED)
async def assign_dispute(
    dispute_id: int,
    data: DisputeAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Assign or reassign a dispute to a finance associate."""
    assignment, user = await DisputeService(db).assign_dispute(dispute_id, data, current_user.user_id)
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
    """Full chronological timeline — episodes, pending questions, assignee."""
    return await DisputeService(db).get_timeline(dispute_id)


@router.get("/{dispute_id}/analysis", response_model=AIAnalysisResponse)
async def get_dispute_analysis(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the latest AI analysis for a dispute."""
    return await DisputeService(db).get_analysis(dispute_id)


@router.post("/{dispute_id}/reanalyze", response_model=TaskResponse)
async def reanalyze_dispute(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Trigger re-analysis of a dispute."""
    task_id = await DisputeService(db).reanalyze(dispute_id)
    return TaskResponse(task_id=task_id, status="QUEUED", message="Re-analysis queued")


@router.get("/{dispute_id}/episodes", response_model=List[TimelineEpisodeResponse])
async def get_dispute_episodes(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all memory episodes in chronological order."""
    episodes = await DisputeService(db).get_episodes(dispute_id)
    return [
        TimelineEpisodeResponse(
            episode_id=ep.episode_id, actor=ep.actor,
            episode_type=ep.episode_type, content_text=ep.content_text,
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
    """Get the rolling memory summary for a dispute."""
    return await DisputeService(db).get_summary(dispute_id)


@router.get("/{dispute_id}/open-questions", response_model=List[OpenQuestionResponse])
async def get_open_questions(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all questions asked to the customer."""
    questions = await DisputeService(db).get_open_questions(dispute_id)
    return [
        OpenQuestionResponse(
            question_id=q.question_id, question_text=q.question_text,
            status=q.status, asked_at=q.created_at, answered_at=q.answered_at,
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
    await DisputeService(db).update_question_status(dispute_id, question_id, data, current_user.user_id)
    return SuccessResponse(message=f"Question {question_id} marked as {data.status}")


@router.post("/{dispute_id}/draft-email", response_model=DraftEmailResponse)
async def draft_email_reply(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Generate an AI email draft for a dispute using Groq (llama-3.3-70b-versatile).

    Reads the full conversation timeline and dispute metadata, then produces
    a professional draft body the FA can review and edit before sending.

    Returns:
      - draft_body        — the ready-to-edit email body text
      - suggested_subject — a pre-filled subject line
      - customer_id       — so the frontend can pre-fill the To field
    """
    service = DisputeService(db)
    dispute = await service.get_dispute(dispute_id)

    # Get latest AI summary if available
    ai_summary = None
    try:
        analysis = await service.get_analysis(dispute_id)
        ai_summary = analysis.ai_summary
    except Exception:
        pass

    dispute_type_name = dispute.dispute_type.reason_name if dispute.dispute_type else None

    draft_body = await generate_draft_email(
        dispute_id=dispute_id,
        db=db,
        customer_id=dispute.customer_id,
        dispute_type=dispute_type_name,
        status=dispute.status,
        priority=dispute.priority,
        ai_summary=ai_summary,
    )

    suggested_subject = (
        f"Re: Dispute #{dispute_id} – {dispute_type_name or 'Your Dispute'}"
    )

    return DraftEmailResponse(
        dispute_id=dispute_id,
        draft_body=draft_body,
        customer_id=dispute.customer_id,
        suggested_subject=suggested_subject,
    )
