from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from src.data.clients.postgres import get_db
from src.core.services.email_service import EmailService
from src.api.rest.dependencies import get_current_user
from src.schemas.schemas import (
    CurrentUser, EmailIngestResponse, EmailResponse, EmailListResponse,
)

router = APIRouter(prefix="/emails", tags=["Emails"])


@router.post("/ingest", response_model=EmailIngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_email(
    file: UploadFile = File(..., description="Email as PDF file"),
    sender_email: str = Form(...),
    subject: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Upload an email as a PDF. The system will:
    1. Extract text from the PDF
    2. Store it as an email_inbox record
    3. Queue a background LangGraph job (Groq) to process it:
       - intelligently extract invoice data
       - identify matching invoice & payment in DB
       - classify the email (DISPUTE / CLARIFICATION)
       - generate an AI auto-response if possible
    
    Returns immediately with a task_id to track progress.
    """
    file_bytes = await file.read()
    service = EmailService(db)
    return await service.ingest_email_pdf(
        file_bytes=file_bytes,
        file_name=file.filename,
        sender_email=sender_email,
        subject=subject,
    )


@router.get("", response_model=EmailListResponse)
async def list_emails(
    status: Optional[str] = Query(None, description="RECEIVED/PROCESSING/PROCESSED/FAILED"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all ingested emails, optionally filtered by processing status."""
    service = EmailService(db)
    items, total = await service.list_emails(status=status, limit=limit, offset=offset)
    return EmailListResponse(total=total, items=items)


@router.get("/{email_id}", response_model=EmailResponse)
async def get_email(
    email_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a specific email with its attachments and routing info."""
    service = EmailService(db)
    return await service.get_email(email_id)


@router.get("/task/{task_id}/status")
async def get_task_status(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Check the status of an email processing Celery task."""
    from src.control.celery_app import celery_app
    task = celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": task.status,
        "result": task.result if task.ready() else None,
    }
