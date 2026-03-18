from fastapi import APIRouter, Depends, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from src.data.clients.postgres import get_db
from src.core.services.invoice_service import InvoiceService
from src.api.rest.dependencies import get_current_user
from src.schemas.schemas import (
    CurrentUser, InvoiceResponse, InvoiceListResponse, InvoiceUploadResponse,
)

router = APIRouter(prefix="/invoices", tags=["Invoices"])


@router.post("/upload", response_model=InvoiceUploadResponse)
async def upload_invoice(
    file: UploadFile = File(..., description="Invoice PDF"),
    invoice_url: str = Form(..., description="Public URL or storage path for this invoice"),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Upload an invoice PDF.

    The system will:
    1. Extract raw text from the PDF
    2. Send the text to **Groq** which intelligently extracts:
       - invoice_number, dates, vendor/customer, line_items, totals, currency, etc.
    3. Store the extracted data as JSON in `invoice_details`
    4. Use the extracted `invoice_number` as the lookup key for future email matching
    """
    file_bytes = await file.read()
    service = InvoiceService(db)
    return await service.upload_and_extract(
        file_bytes=file_bytes,
        file_name=file.filename,
        invoice_url=invoice_url,
    )


@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all invoices stored in the system."""
    service = InvoiceService(db)
    items, total = await service.list_invoices(limit=limit, offset=offset)
    return InvoiceListResponse(total=total, items=items)


@router.get("/by-number/{invoice_number}", response_model=InvoiceResponse)
async def get_invoice_by_number(
    invoice_number: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Look up an invoice by its invoice number."""
    service = InvoiceService(db)
    return await service.get_by_number(invoice_number)


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get an invoice by its database ID."""
    service = InvoiceService(db)
    return await service.get_invoice(invoice_id)
