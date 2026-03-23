"""
src/api/rest/routes/ar_documents.py
=====================================
AR Document Graph endpoints.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from src.data.clients.postgres import get_db
from src.core.services.ar_document_service import ARDocumentService, resolve_customer_scope
from src.schemas.common_schemas import CurrentUser
from src.api.rest.dependencies import get_current_user

router = APIRouter(prefix="/ar-documents", tags=["AR Documents"])


@router.get("")
async def list_documents_for_customer(
    customer_email: str          = None,
    db:             AsyncSession = Depends(get_db),
    current_user:   CurrentUser  = Depends(get_current_user),
):
    """
    List all AR documents for a given customer scope.
    Returns ARDocSummary objects (no keys/related) for fast picker UI.
    customer_email is required — scoped to that customer's graph partition.
    """
    if not customer_email:
        raise HTTPException(status_code=400, detail="customer_email query param is required")
    svc   = ARDocumentService(db)
    scope = resolve_customer_scope(customer_email)
    docs  = await svc.list_for_customer(scope)
    return docs


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file:           UploadFile       = File(...),
    doc_type:       str              = Form(...),
    customer_email: str              = Form(...),
    doc_date:       Optional[str]    = Form(None),
    db:             AsyncSession     = Depends(get_db),
    current_user:   CurrentUser      = Depends(get_current_user),
):
    """Upload an AR document (PO, Invoice, GRN, Payment, Contract, Credit Note)."""
    try:
        svc    = ARDocumentService(db)
        scope  = resolve_customer_scope(customer_email)
        result = await svc.upload_document(
            file           = file,
            doc_type       = doc_type.upper(),
            customer_scope = scope,
            doc_date       = doc_date,
            uploaded_by    = current_user.user_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{doc_id}")
async def get_document(
    doc_id:       int,
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    svc    = ARDocumentService(db)
    result = await svc.get_document(doc_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return result


@router.get("/{doc_id}/download")
async def download_ar_document(
    doc_id:       int,
    mode:         str          = "view",   # "view" | "save"
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """
    Serve the raw file for an AR document.

    Storage routing (mirrors dispute_document_service):
      - GCS-stored + ADC available  → 302 redirect to a 30-minute signed URL
      - GCS-stored + no ADC         → stream bytes directly from GCS
      - Local-stored                → stream bytes from disk

    mode=view → Content-Disposition: inline  (browser renders PDF/images in-tab)
    mode=save → Content-Disposition: attachment  (force download)
    """
    import mimetypes
    from fastapi.responses import StreamingResponse, RedirectResponse
    from fastapi import HTTPException
    import io

    svc = ARDocumentService(db)

    # ── Try GCS signed URL first (cheapest path for GCS-stored files) ────────
    try:
        signed_url = await svc.get_signed_url_if_gcs(doc_id, expiry_minutes=30)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"AR document {doc_id} not found")

    if signed_url:
        # GCS + ADC available: redirect, browser fetches directly from GCS
        return RedirectResponse(url=signed_url, status_code=302)

    # ── Stream bytes (GCS without ADC, or local storage) ─────────────────────
    try:
        file_bytes, filename = await svc.get_file_bytes(doc_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"AR document {doc_id} not found")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    media_type  = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    disposition = "inline" if mode == "view" else "attachment"

    return StreamingResponse(
        content    = io.BytesIO(file_bytes),
        media_type = media_type,
        headers    = {"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.get("/{doc_id}/related")
async def get_related_documents(
    doc_id:       int,
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """Return all documents connected to this one via shared reference keys."""
    svc = ARDocumentService(db)
    return await svc.get_related(doc_id)


class ManualKeyRequest(BaseModel):
    key_type:      str
    key_value_raw: str

@router.post("/{doc_id}/keys")
async def add_manual_key(
    doc_id:       int,
    body:         ManualKeyRequest,
    db:           AsyncSession = Depends(get_db),
    current_user: CurrentUser  = Depends(get_current_user),
):
    """FA manually adds or corrects a reference key on a document."""
    valid_types = {"po_number", "inv_number", "grn_number",
                   "contract_number", "payment_ref", "credit_note_number"}
    if body.key_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid key_type '{body.key_type}'")
    svc = ARDocumentService(db)
    return await svc.add_manual_key(doc_id, body.key_type, body.key_value_raw, current_user.user_id)
