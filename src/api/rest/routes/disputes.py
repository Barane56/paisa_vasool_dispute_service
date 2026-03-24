"""
src/api/rest/routes/disputes.py
================================
Thin route layer — all business/query logic lives in DisputeService.
Routes only handle HTTP concerns: extract params, call service, return response.
"""
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import RedirectResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from src.data.clients.postgres import get_db
from src.core.services.dispute_service import DisputeService
from src.core.services.draft_email_service import generate_draft_email
from src.core.services.dispute_document_service import DisputeDocumentService
from src.api.rest.dependencies import get_current_user
from src.schemas.schemas import (
    CurrentUser, DisputeListResponse, DisputeDetailResponse, DisputeResponse,
    DisputeStatusUpdate, DisputeAssignRequest, DisputeTimelineResponse,
    AIAnalysisResponse, MemorySummaryResponse, TimelineEpisodeResponse,
    OpenQuestionResponse, QuestionStatusUpdate, SuccessResponse, TaskResponse,
    DraftEmailResponse,
    FADisputeCreate,
    DisputeDocumentResponse, DisputeDocumentListResponse,
)

router = APIRouter(prefix="/disputes", tags=["Disputes"])


@router.get("", response_model=DisputeListResponse)
async def list_disputes(
    status: Optional[str] = Query(None, description="OPEN/UNDER_REVIEW/RESOLVED/CLOSED/UNVERIFIED"),
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
    List all incidents (disputes + clarifications).

    Admin    → sees every record regardless of assignment, with no default filters.
    FA       → sees only disputes assigned to them by default.
               Pass assigned_to=0 explicitly to see unassigned ones too.
    """
    service = DisputeService(db)
    is_admin = current_user.role == "admin"

    # Admin: no assignment filter unless explicitly requested
    # FA:    restrict to their own assignments unless overridden
    effective_assigned_to = assigned_to if is_admin else (assigned_to or current_user.user_id)

    enriched, total = await service.get_enriched_list(
        status=status,
        priority=priority,
        customer_id=customer_id,
        assigned_to=None if is_admin and assigned_to is None else effective_assigned_to,
        search=search,
        limit=limit,
        offset=offset,
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

    # Pass the FA's real name so the draft sounds personal
    fa_name = current_user.name if hasattr(current_user, 'name') else None

    draft_body = await generate_draft_email(
        dispute_id=dispute_id,
        db=db,
        customer_id=dispute.customer_id,
        dispute_type=dispute_type_name,
        status=dispute.status,
        priority=dispute.priority,
        ai_summary=ai_summary,
        fa_name=fa_name,
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


@router.patch("/{dispute_id}/mark-read", response_model=SuccessResponse)
async def mark_dispute_read(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    FA has opened and read the dispute — clears the new message flag.
    Called by the frontend when the dispute modal is opened.
    """
    from src.data.repositories.dispute_repository import DisputeNewMessageRepository
    await DisputeNewMessageRepository(db).clear_new_message(dispute_id)
    await db.commit()
    return SuccessResponse(message=f"Dispute {dispute_id} marked as read")


# ═══════════════════════════════════════════════════════════════════════════════
# FA Manual Dispute Creation
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/create", response_model=DisputeDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_dispute_manually(
    data: FADisputeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Finance Associate creates a dispute manually — no inbound email required.

    - Pick an existing dispute_type_id from GET /dispute-types, OR
    - Leave dispute_type_id null and provide custom_type_name to auto-create
      a new dispute type on the fly.

    The dispute is auto-assigned to the creating FA and set to OPEN.
    """
    service = DisputeService(db)
    dispute = await service.create_fa_dispute(
        customer_id=data.customer_id,
        dispute_type_id=data.dispute_type_id,
        custom_type_name=data.custom_type_name,
        custom_type_desc=data.custom_type_desc,
        priority=data.priority,
        description=data.description,
        invoice_id=data.invoice_id,
        created_by=current_user.user_id,
        customer_email=data.customer_email,
        ar_document_id=data.ar_document_id,
    )
    return await service.get_enriched_detail(dispute.dispute_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Dispute Supporting Documents  (FA-uploaded files)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{dispute_id}/documents",
    response_model=DisputeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_dispute_document(
    dispute_id:   int,
    file:         UploadFile  = File(..., description="Any file — PDF, image, spreadsheet, etc."),
    display_name: str | None  = Form(None, description="Human-readable label for this document"),
    notes:        str | None  = Form(None, description="Why this document is relevant"),
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """
    Upload a supporting document to a dispute.
    Accepts any file type. Stored in GCS (or local fallback).
    Returns a download_url valid for 30 minutes (re-fetch to get a fresh one).
    """
    # Verify dispute exists
    service = DisputeService(db)
    await service.get_dispute(dispute_id)

    doc_service = DisputeDocumentService(db)
    doc = await doc_service.upload_document(
        dispute_id=dispute_id,
        uploaded_by=current_user.user_id,
        file=file,
        display_name=display_name,
        notes=notes,
    )
    return DisputeDocumentResponse(
        document_id=doc.document_id,
        dispute_id=doc.dispute_id,
        uploaded_by=doc.uploaded_by,
        uploader_name=doc.uploader.name if doc.uploader else None,
        file_name=doc.file_name,
        file_type=doc.file_type,
        file_size=doc.file_size,
        display_name=doc.display_name,
        notes=doc.notes,
        download_url=await doc_service.get_download_url(doc),
        created_at=doc.created_at,
    )


@router.get("/{dispute_id}/documents", response_model=DisputeDocumentListResponse)
async def list_dispute_documents(
    dispute_id:   int,
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """List all FA-uploaded supporting documents for a dispute."""
    service = DisputeService(db)
    await service.get_dispute(dispute_id)

    doc_service = DisputeDocumentService(db)
    docs = await doc_service.list_documents(dispute_id)
    items = [
        DisputeDocumentResponse(
            document_id=d.document_id,
            dispute_id=d.dispute_id,
            uploaded_by=d.uploaded_by,
            uploader_name=d.uploader.name if d.uploader else None,
            file_name=d.file_name,
            file_type=d.file_type,
            file_size=d.file_size,
            display_name=d.display_name,
            notes=d.notes,
            download_url=await doc_service.get_download_url(d),
            created_at=d.created_at,
        )
        for d in docs
    ]
    return DisputeDocumentListResponse(dispute_id=dispute_id, total=len(items), items=items)


@router.get("/{dispute_id}/documents/{document_id}/download")
async def download_dispute_document(
    dispute_id:   int,
    document_id:  int,
    mode:         str         = Query("save", description="'view' to open inline, 'save' to force download"),
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """
    Serve a supporting document.
    mode=view  → Content-Disposition: inline  (browser renders PDF/image in tab)
    mode=save  → Content-Disposition: attachment  (browser downloads)
    - GCS: try signed URL redirect first, fall back to byte streaming
    - Local: always stream bytes directly
    """
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse
    import io, mimetypes

    doc_service = DisputeDocumentService(db)
    doc = await doc_service.get_document(document_id)

    if doc.dispute_id != dispute_id:
        raise HTTPException(status_code=404, detail="Document not found for this dispute")

    disposition = "inline" if mode == "view" else "attachment"

    # Resolve MIME type — stored file_type may be wrong or octet-stream
    mime = doc.file_type or "application/octet-stream"
    if mime == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(doc.file_name)
        if guessed:
            mime = guessed

    # Try signed URL for GCS paths (signed URLs always force download in browser)
    # For view mode we skip redirect and always stream so we control Content-Disposition
    from src.core.services.dispute_document_service import GCS_PREFIX
    if doc.file_path.startswith(GCS_PREFIX) and mode == "save":
        try:
            from src.core.services.gcs_service import get_signed_url
            gcs_path = doc.file_path.removeprefix(GCS_PREFIX)
            url = get_signed_url(gcs_path, expiry_minutes=30)
            return RedirectResponse(url=url, status_code=302)
        except Exception:
            pass  # Fall through to byte streaming

    # Stream bytes — works for both local and GCS (fallback)
    try:
        data, filename = await doc_service.get_file_bytes(doc)
        return StreamingResponse(
            io.BytesIO(data),
            media_type=mime,
            headers={
                "Content-Disposition": f'{disposition}; filename="{filename}"',
                "Content-Length": str(len(data)),
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/{dispute_id}/documents/{document_id}", response_model=SuccessResponse)
async def delete_dispute_document(
    dispute_id:   int,
    document_id:  int,
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """Delete a supporting document. Removes from GCS/local and DB."""
    doc_service = DisputeDocumentService(db)
    doc = await doc_service.get_document(document_id)

    if doc.dispute_id != dispute_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Document not found for this dispute")

    await doc_service.delete_document(document_id)
    return SuccessResponse(message=f"Document {document_id} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# AR Document Graph — per-dispute linked documents
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/{dispute_id}/ar-documents")
async def get_dispute_ar_documents(
    dispute_id:   int,
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """
    Return all AR documents linked to this dispute (PO, GRN, Invoice, Payment, etc.)
    with their extracted reference keys and connected graph documents.
    Only shows documents relevant to this specific dispute — not all customer docs.
    """
    from src.core.services.ar_document_service import ARDocumentService
    ar_svc = ARDocumentService(db)
    return await ar_svc.get_ar_documents_for_dispute(dispute_id)


from pydantic import BaseModel as _BM2

class AnchorUpdateRequest(_BM2):
    doc_id:         int
    customer_email: Optional[str] = None   # scope override; defaults to dispute.customer_id


@router.put("/{dispute_id}/ar-documents/anchor")
async def update_dispute_ar_anchor(
    dispute_id:   int,
    body:         AnchorUpdateRequest,
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """
    Replace the anchor AR document for a dispute.

    Clears all currently linked AR documents and re-walks the graph from
    the newly selected anchor document, linking the full chain.
    Returns the updated linked document list.
    """
    from fastapi import HTTPException
    from src.core.services.ar_document_service import ARDocumentService, resolve_customer_scope
    from src.data.repositories.repositories import DisputeRepository

    # Resolve customer scope: prefer explicit override, fall back to dispute's customer_id
    scope: str
    if body.customer_email:
        scope = resolve_customer_scope(body.customer_email)
    else:
        dispute = await DisputeRepository(db).get_by_id(dispute_id)
        if not dispute:
            raise HTTPException(status_code=404, detail="Dispute not found")
        scope = resolve_customer_scope(dispute.customer_id)

    try:
        ar_svc = ARDocumentService(db)
        result = await ar_svc.replace_anchor_document(
            dispute_id     = dispute_id,
            new_doc_id     = body.doc_id,
            user_id        = current_user.user_id,
            customer_scope = scope,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# Fork Recommendations  (AI-suggested case splits — FA decides)
# ═══════════════════════════════════════════════════════════════════════════════

from pydantic import BaseModel as _BaseModel   # local alias avoids conflict with schemas

class ForkRecommendationAction(_BaseModel):
    action:           str             # "ACCEPT" | "DISMISS"
    dispute_type_id:  Optional[int]   = None
    custom_type_name: Optional[str]   = None
    custom_type_desc: Optional[str]   = None
    description:      Optional[str]   = None
    priority:         str             = "MEDIUM"
    customer_email:   Optional[str]   = None
    ar_document_id:   Optional[int]   = None


@router.get("/{dispute_id}/fork-recommendations")
async def get_fork_recommendations(
    dispute_id:   int,
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """Return all PENDING fork recommendations for this dispute."""
    from src.core.services.dispute_service import ForkRecommendationService
    return await ForkRecommendationService(db).list_pending(dispute_id)


@router.post("/{dispute_id}/fork-recommendations/{recommendation_id}/action")
async def action_fork_recommendation(
    dispute_id:        int,
    recommendation_id: int,
    body:              ForkRecommendationAction,
    db:                AsyncSession = Depends(get_db),
    current_user:      CurrentUser  = Depends(get_current_user),
):
    """
    ACCEPT or DISMISS a fork recommendation.
    ACCEPT  → creates a new dispute linked as FORKED_FROM, auto-assigns FA.
    DISMISS → hides the recommendation permanently.
    """
    from fastapi import HTTPException
    from src.core.services.dispute_service import ForkRecommendationService
    from src.core.exceptions import DisputeNotFoundError

    svc = ForkRecommendationService(db)
    action = (body.action or "").upper()

    try:
        if action == "DISMISS":
            return await svc.dismiss(dispute_id, recommendation_id, current_user.user_id)

        if action == "ACCEPT":
            return await svc.accept(
                dispute_id        = dispute_id,
                recommendation_id = recommendation_id,
                user_id           = current_user.user_id,
                dispute_type_id   = body.dispute_type_id,
                custom_type_name  = body.custom_type_name,
                custom_type_desc  = body.custom_type_desc,
                description       = body.description or "",
                priority          = body.priority,
                customer_email    = body.customer_email,
                ar_document_id    = body.ar_document_id,
            )

        raise HTTPException(status_code=400, detail="action must be ACCEPT or DISMISS")

    except DisputeNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
