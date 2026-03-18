"""
src/api/rest/routes/mailboxes.py
=================================
Endpoints
---------
POST   /mailboxes                               add mailbox (IMAP + SMTP)
GET    /mailboxes                               list all
GET    /mailboxes/{id}                          single mailbox
DELETE /mailboxes/{id}                          delete
POST   /mailboxes/{id}/pause                    pause polling
POST   /mailboxes/{id}/unpause                  resume polling
GET    /mailboxes/{id}/test                     test IMAP + SMTP (for frontend validation)
GET    /mailboxes/{id}/messages                 messages for this mailbox

GET    /inbox/messages                          all messages (global inbox)
GET    /inbox/messages/{message_id}             single message + attachments
GET    /inbox/disputes/{dispute_id}/messages    all messages for a dispute (timeline)
GET    /inbox/attachments/{attachment_id}/download   serve inbound attachment

POST   /disputes/{dispute_id}/send-email        FA sends a reply (with optional attachments)
GET    /disputes/{dispute_id}/outbound          list sent emails for a dispute
GET    /outbound/{outbound_id}                  single sent email
GET    /outbound/attachments/{attachment_id}/download  serve outbound attachment
"""
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.clients.postgres import get_db
from src.api.rest.dependencies import get_current_user
from src.core.services.mailbox_service import MailboxService
from src.core.services.outbound_email_service import OutboundEmailService
from src.core.exceptions.errors import ResourceNotFoundError, ValidationError as PVValidationError
from src.schemas.mailbox_schemas import (
    MailboxCreateRequest, MailboxResponse, MailboxTestResponse,
    InboxMessageResponse, ComposeEmailRequest, OutboundEmailResponse,
)
from src.schemas.common_schemas import SuccessResponse
from src.schemas.schemas import CurrentUser
from src.config.settings import settings

router        = APIRouter(prefix="/mailboxes",  tags=["Mailboxes"])
inbox_router  = APIRouter(prefix="/inbox",      tags=["Inbox"])
send_router   = APIRouter(prefix="/disputes",   tags=["Send Email"])
outbox_router = APIRouter(prefix="/outbound",   tags=["Outbound Emails"])

STORAGE_DIR = Path(getattr(settings, "ATTACHMENT_STORAGE_DIR", "/tmp/dispute_attachments"))
from src.core.services.gcs_service import get_public_url as _gcs_url


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mb_svc(db: AsyncSession = Depends(get_db)) -> MailboxService:
    return MailboxService(db)

def _out_svc(db: AsyncSession = Depends(get_db)) -> OutboundEmailService:
    return OutboundEmailService(db)


# ═════════════════════════════════════════════════════════════════════════════
# Mailbox CRUD
# ═════════════════════════════════════════════════════════════════════════════

@router.post("", response_model=MailboxResponse, status_code=status.HTTP_201_CREATED)
async def add_mailbox(
    body: MailboxCreateRequest,
    svc:  MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Add a new IMAP/SMTP mailbox.
    Password is base64-encoded before storage and never returned in responses.
    smtp_host is optional — if omitted the system derives it from imap_host
    (e.g. imap.gmail.com → smtp.gmail.com).
    """
    try:
        return await svc.add_mailbox(
            label=body.label,
            email_address=str(body.email_address),
            imap_host=body.imap_host,
            imap_port=body.imap_port,
            use_ssl=body.use_ssl,
            password=body.password,
            smtp_host=body.smtp_host,
            smtp_port=body.smtp_port,
            smtp_use_tls=body.smtp_use_tls,
        )
    except PVValidationError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.get("", response_model=List[MailboxResponse])
async def list_mailboxes(
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await svc.list_mailboxes()


@router.get("/{mailbox_id}", response_model=MailboxResponse)
async def get_mailbox(
    mailbox_id: int,
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return await svc.get_mailbox(mailbox_id)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.delete("/{mailbox_id}", response_model=SuccessResponse)
async def delete_mailbox(
    mailbox_id: int,
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        await svc.delete_mailbox(mailbox_id)
        return SuccessResponse(message=f"Mailbox {mailbox_id} deleted.")
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{mailbox_id}/pause", response_model=MailboxResponse)
async def pause_mailbox(
    mailbox_id: int,
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return await svc.pause_mailbox(mailbox_id)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{mailbox_id}/unpause", response_model=MailboxResponse)
async def unpause_mailbox(
    mailbox_id: int,
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return await svc.unpause_mailbox(mailbox_id)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/{mailbox_id}/test", response_model=MailboxTestResponse)
async def test_mailbox(
    mailbox_id: int,
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Test both IMAP and SMTP connections for a mailbox.
    Returns imap_ok and smtp_ok separately so the frontend can show
    granular feedback when adding a new mailbox.
    """
    try:
        result = await svc.test_mailbox(mailbox_id)
        return MailboxTestResponse(mailbox_id=mailbox_id, **result)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/{mailbox_id}/messages", response_model=List[InboxMessageResponse])
async def list_mailbox_messages(
    mailbox_id: int,
    source: Optional[str] = Query(None, description="INBOUND | OUTBOUND"),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0,  ge=0),
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        await svc.get_mailbox(mailbox_id)
        return await svc.list_inbox(mailbox_id=mailbox_id, source=source, limit=limit, offset=offset)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


# ═════════════════════════════════════════════════════════════════════════════
# Inbox (global simulated inbox)
# ═════════════════════════════════════════════════════════════════════════════

@inbox_router.get("/messages", response_model=List[InboxMessageResponse])
async def list_all_messages(
    mailbox_id: Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0,  ge=0),
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await svc.list_inbox(mailbox_id=mailbox_id, source=source, limit=limit, offset=offset)


@inbox_router.get("/messages/{message_id}", response_model=InboxMessageResponse)
async def get_message(
    message_id: int,
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return await svc.get_message(message_id)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@inbox_router.get("/disputes/{dispute_id}/messages", response_model=List[InboxMessageResponse])
async def messages_for_dispute(
    dispute_id: int,
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    All inbound messages linked to a dispute.
    Used by the timeline to show email body + documents side by side.
    """
    return await svc.list_messages_for_dispute(dispute_id)


@inbox_router.get("/attachments/{attachment_id}/download")
async def download_inbound_attachment(
    attachment_id: int,
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Serve an inbound attachment file."""
    try:
        att = await svc.get_inbound_attachment(attachment_id)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

    if settings.GCS_ENABLED:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=_gcs_url(att.file_path), status_code=302)

    full_path = STORAGE_DIR / att.file_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Attachment file not found on server")
    return FileResponse(path=str(full_path), filename=att.file_name, media_type="application/octet-stream")


# ═════════════════════════════════════════════════════════════════════════════
# Send email (FA composes reply)
# ═════════════════════════════════════════════════════════════════════════════

@send_router.post("/{dispute_id}/send-email", response_model=OutboundEmailResponse, status_code=status.HTTP_201_CREATED)
async def send_dispute_email(
    dispute_id: int,
    # Multipart form so files can be attached
    to_email:            str         = Form(...),
    subject:             str         = Form(...),
    body_html:           str         = Form(...),
    body_text:           str         = Form(...),
    new_thread:          bool        = Form(False),
    attachments:         List[UploadFile] = File(default=[]),
    svc: OutboundEmailService = Depends(_out_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Send an email on behalf of the logged-in FA.

    new_thread=False (default): reply_to_message_id auto-resolved to last inbound
                                → In-Reply-To set → stays in same Gmail/Outlook thread
    new_thread=True:            reply_to_message_id forced to None
                                → no In-Reply-To → fresh thread in customer inbox
    """
    try:
        # Sentinel -1 tells compose_and_send to skip auto-resolve and send fresh
        reply_to = None if new_thread else None   # None triggers auto-resolve inside service
        if new_thread:
            # Pass a flag via a private kwarg — compose_and_send checks this
            outbound = await svc.compose_and_send(
                dispute_id=dispute_id,
                sent_by_user_id=current_user.user_id,
                to_email=to_email,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                reply_to_message_id=None,
                force_new_thread=True,
                attachments=attachments or [],
            )
        else:
            outbound = await svc.compose_and_send(
                dispute_id=dispute_id,
                sent_by_user_id=current_user.user_id,
                to_email=to_email,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                reply_to_message_id=None,   # auto-resolved to last inbound inside compose_and_send
                attachments=attachments or [],
            )
        return OutboundEmailResponse.from_orm_with_sender(outbound)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except PVValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@send_router.get("/{dispute_id}/outbound", response_model=List[OutboundEmailResponse])
async def list_outbound_for_dispute(
    dispute_id: int,
    svc: OutboundEmailService = Depends(_out_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all emails sent from our system for a dispute. Shows which FA sent each one."""
    emails = await svc.list_for_dispute(dispute_id)
    return [OutboundEmailResponse.from_orm_with_sender(e) for e in emails]


# ═════════════════════════════════════════════════════════════════════════════
# Outbound attachment download
# ═════════════════════════════════════════════════════════════════════════════

@outbox_router.get("/{outbound_id}", response_model=OutboundEmailResponse)
async def get_outbound_email(
    outbound_id: int,
    svc: MailboxService = Depends(_mb_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        email = await svc.get_outbound_email_by_id(outbound_id)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    return OutboundEmailResponse.from_orm_with_sender(email)


@outbox_router.get("/attachments/{attachment_id}/download")
async def download_outbound_attachment(
    attachment_id: int,
    svc: OutboundEmailService = Depends(_out_svc),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Serve an outbound attachment file (e.g. credit note PDF the FA attached)."""
    try:
        att = await svc.get_attachment(attachment_id)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

    if settings.GCS_ENABLED:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=_gcs_url(att.file_path), status_code=302)

    full_path = STORAGE_DIR / att.file_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Attachment file not found on server")
    return FileResponse(path=str(full_path), filename=att.file_name, media_type="application/octet-stream")
