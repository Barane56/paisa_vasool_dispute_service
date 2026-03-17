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
    search: Optional[str] = Query(None, description="Search by customer_id, description, dispute_id, or type"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    List disputes with filters. Returns enriched items (analysis, assignment,
    open question count, new-message flag) in a single request — no follow-up
    calls needed from the frontend.
    """
    from sqlalchemy import select as sa_select, and_ as sa_and_, func as sa_func
    from sqlalchemy.orm import selectinload, joinedload
    from src.data.models.postgres.models import (
        DisputeMaster, DisputeAIAnalysis, DisputeAssignment, DisputeOpenQuestion,
    )
    from src.data.models.postgres.memory_models import DisputeMemoryEpisode
    from src.schemas.dispute_schemas import AIAnalysisResponse

    service = DisputeService(db)
    items, total = await service.list_disputes(
        status=status,
        priority=priority,
        customer_id=customer_id,
        assigned_to=assigned_to,
        search=search,
        limit=limit,
        offset=offset,
    )
    if not items:
        return DisputeListResponse(total=total, items=[])

    id_list = [d.dispute_id for d in items]

    # ── Latest AI analysis per dispute ────────────────────────────────────────
    max_analysis_sq = (
        sa_select(
            DisputeAIAnalysis.dispute_id,
            sa_func.max(DisputeAIAnalysis.analysis_id).label("max_id"),
        )
        .where(DisputeAIAnalysis.dispute_id.in_(id_list))
        .group_by(DisputeAIAnalysis.dispute_id)
        .subquery()
    )
    analysis_rows = list((await db.execute(
        sa_select(DisputeAIAnalysis).join(max_analysis_sq, sa_and_(
            DisputeAIAnalysis.dispute_id == max_analysis_sq.c.dispute_id,
            DisputeAIAnalysis.analysis_id == max_analysis_sq.c.max_id,
        ))
    )).scalars().all())
    analysis_map = {a.dispute_id: a for a in analysis_rows}

    # ── Active assignment per dispute ─────────────────────────────────────────
    assign_rows = list((await db.execute(
        sa_select(DisputeAssignment)
        .options(joinedload(DisputeAssignment.assignee))
        .where(sa_and_(
            DisputeAssignment.dispute_id.in_(id_list),
            DisputeAssignment.status == "ACTIVE",
        ))
        .order_by(DisputeAssignment.assigned_at.desc())
    )).scalars().all())
    assign_map: dict = {}
    for a in assign_rows:
        if a.dispute_id not in assign_map:
            assign_map[a.dispute_id] = a

    # ── Pending questions count per dispute ───────────────────────────────────
    q_rows = list((await db.execute(
        sa_select(
            DisputeOpenQuestion.dispute_id,
            sa_func.count(DisputeOpenQuestion.question_id).label("cnt"),
        )
        .where(sa_and_(
            DisputeOpenQuestion.dispute_id.in_(id_list),
            DisputeOpenQuestion.status == "PENDING",
        ))
        .group_by(DisputeOpenQuestion.dispute_id)
    )).all())
    q_count_map = {row.dispute_id: row.cnt for row in q_rows}

    # ── Latest episode actor per dispute ──────────────────────────────────────
    max_ep_sq = (
        sa_select(
            DisputeMemoryEpisode.dispute_id,
            sa_func.max(DisputeMemoryEpisode.episode_id).label("max_ep_id"),
        )
        .where(DisputeMemoryEpisode.dispute_id.in_(id_list))
        .group_by(DisputeMemoryEpisode.dispute_id)
        .subquery()
    )
    ep_rows = list((await db.execute(
        sa_select(DisputeMemoryEpisode.dispute_id, DisputeMemoryEpisode.actor)
        .join(max_ep_sq, sa_and_(
            DisputeMemoryEpisode.dispute_id == max_ep_sq.c.dispute_id,
            DisputeMemoryEpisode.episode_id == max_ep_sq.c.max_ep_id,
        ))
    )).all())
    ep_actor_map = {row.dispute_id: row.actor for row in ep_rows}

    # ── Assemble ──────────────────────────────────────────────────────────────
    enriched = []
    for d in items:
        raw_a = analysis_map.get(d.dispute_id)
        latest_analysis = None
        if raw_a:
            try:
                latest_analysis = AIAnalysisResponse.model_validate(raw_a)
            except Exception:
                pass
        active_assign = assign_map.get(d.dispute_id)
        enriched.append(DisputeDetailResponse(
            dispute_id=d.dispute_id,
            email_id=d.email_id,
            invoice_id=d.invoice_id,
            payment_detail_id=d.payment_detail_id,
            customer_id=d.customer_id,
            dispute_type=d.dispute_type,
            status=d.status,
            priority=d.priority,
            description=d.description,
            created_at=d.created_at,
            updated_at=d.updated_at,
            latest_analysis=latest_analysis,
            open_questions_count=q_count_map.get(d.dispute_id, 0),
            assigned_to=active_assign.assignee.email if active_assign else None,
            has_new_customer_message=(ep_actor_map.get(d.dispute_id) == "CUSTOMER"),
        ))
    return DisputeListResponse(total=total, items=enriched)


@router.get("/bulk-detail", response_model=List[DisputeDetailResponse])
async def bulk_get_dispute_detail(
    ids: str = Query(..., description="Comma-separated list of dispute_ids"),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Fetch full detail (analysis, open_questions_count, assigned_to,
    has_new_customer_message) for multiple disputes in a single request.
    Replaces N individual GET /disputes/{id} calls — one round-trip to DB.
    ids = comma-separated integers e.g. ?ids=1,2,3,4
    """
    import asyncio
    from sqlalchemy import select as sa_select, and_ as sa_and_
    from src.data.models.postgres.models import DisputeAIAnalysis, DisputeAssignment, DisputeOpenQuestion
    from src.data.models.postgres.memory_models import DisputeMemoryEpisode
    from src.data.models.postgres.models import DisputeMaster
    from sqlalchemy.orm import selectinload, joinedload

    try:
        id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    except ValueError:
        id_list = []
    if not id_list:
        return []

    service = DisputeService(db)

    # ── 1. Latest AI analysis per dispute (one query) ─────────────────────────
    # Use a subquery to get max analysis_id per dispute, then join
    from sqlalchemy import func as sa_func
    max_analysis_sq = (
        sa_select(
            DisputeAIAnalysis.dispute_id,
            sa_func.max(DisputeAIAnalysis.analysis_id).label("max_id"),
        )
        .where(DisputeAIAnalysis.dispute_id.in_(id_list))
        .group_by(DisputeAIAnalysis.dispute_id)
        .subquery()
    )
    analysis_stmt = (
        sa_select(DisputeAIAnalysis)
        .join(max_analysis_sq, sa_and_(
            DisputeAIAnalysis.dispute_id == max_analysis_sq.c.dispute_id,
            DisputeAIAnalysis.analysis_id == max_analysis_sq.c.max_id,
        ))
    )
    analysis_rows = list((await db.execute(analysis_stmt)).scalars().all())
    analysis_map: dict = {a.dispute_id: a for a in analysis_rows}

    # ── 2. Active assignment per dispute (one query) ──────────────────────────
    assign_stmt = (
        sa_select(DisputeAssignment)
        .options(joinedload(DisputeAssignment.assignee))
        .where(sa_and_(
            DisputeAssignment.dispute_id.in_(id_list),
            DisputeAssignment.status == "ACTIVE",
        ))
        .order_by(DisputeAssignment.assigned_at.desc())
    )
    assign_rows = list((await db.execute(assign_stmt)).scalars().all())
    # Keep only the most recent active assignment per dispute
    assign_map: dict = {}
    for a in assign_rows:
        if a.dispute_id not in assign_map:
            assign_map[a.dispute_id] = a

    # ── 3. Pending open questions count per dispute (one query) ───────────────
    q_count_stmt = (
        sa_select(
            DisputeOpenQuestion.dispute_id,
            sa_func.count(DisputeOpenQuestion.question_id).label("cnt"),
        )
        .where(sa_and_(
            DisputeOpenQuestion.dispute_id.in_(id_list),
            DisputeOpenQuestion.status == "PENDING",
        ))
        .group_by(DisputeOpenQuestion.dispute_id)
    )
    q_rows = list((await db.execute(q_count_stmt)).all())
    q_count_map: dict = {row.dispute_id: row.cnt for row in q_rows}

    # ── 4. Latest episode actor per dispute (one query) ───────────────────────
    max_ep_sq = (
        sa_select(
            DisputeMemoryEpisode.dispute_id,
            sa_func.max(DisputeMemoryEpisode.episode_id).label("max_ep_id"),
        )
        .where(DisputeMemoryEpisode.dispute_id.in_(id_list))
        .group_by(DisputeMemoryEpisode.dispute_id)
        .subquery()
    )
    latest_ep_stmt = (
        sa_select(DisputeMemoryEpisode.dispute_id, DisputeMemoryEpisode.actor)
        .join(max_ep_sq, sa_and_(
            DisputeMemoryEpisode.dispute_id == max_ep_sq.c.dispute_id,
            DisputeMemoryEpisode.episode_id == max_ep_sq.c.max_ep_id,
        ))
    )
    ep_rows = list((await db.execute(latest_ep_stmt)).all())
    ep_actor_map: dict = {row.dispute_id: row.actor for row in ep_rows}

    # ── 5. Fetch base dispute records (one query) ─────────────────────────────
    disp_stmt = (
        sa_select(DisputeMaster)
        .options(selectinload(DisputeMaster.dispute_type))
        .where(DisputeMaster.dispute_id.in_(id_list))
        .order_by(DisputeMaster.created_at.desc())
    )
    disputes = list((await db.execute(disp_stmt)).scalars().all())

    # ── 6. Assemble response ──────────────────────────────────────────────────
    from src.schemas.dispute_schemas import AIAnalysisResponse
    results = []
    for d in disputes:
        raw_analysis = analysis_map.get(d.dispute_id)
        latest_analysis = None
        if raw_analysis:
            try:
                latest_analysis = AIAnalysisResponse.model_validate(raw_analysis)
            except Exception:
                pass

        active_assign = assign_map.get(d.dispute_id)
        results.append(DisputeDetailResponse(
            dispute_id=d.dispute_id,
            email_id=d.email_id,
            invoice_id=d.invoice_id,
            payment_detail_id=d.payment_detail_id,
            customer_id=d.customer_id,
            dispute_type=d.dispute_type,
            status=d.status,
            priority=d.priority,
            description=d.description,
            created_at=d.created_at,
            updated_at=d.updated_at,
            latest_analysis=latest_analysis,
            open_questions_count=q_count_map.get(d.dispute_id, 0),
            assigned_to=active_assign.assignee.email if active_assign else None,
            has_new_customer_message=(ep_actor_map.get(d.dispute_id) == "CUSTOMER"),
        ))
    return results


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

    # Detect if the latest episode is a CUSTOMER message (new unread message)
    has_new_customer_message = False
    try:
        from src.data.repositories.memory_repository import MemoryEpisodeRepository
        ep_repo = MemoryEpisodeRepository(db)
        latest_eps = await ep_repo.get_latest_n(dispute_id, n=1)
        if latest_eps and latest_eps[0].actor == "CUSTOMER":
            has_new_customer_message = True
    except Exception:
        pass

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
        has_new_customer_message=has_new_customer_message,
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