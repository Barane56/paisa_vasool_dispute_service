from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
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
    customer_email: str | None = Query(None, description="FA customer email — enforces ownership check"),
    db: AsyncSession             = Depends(get_db),
    current_user: CurrentUser    = Depends(get_current_user),
):
    """
    Look up an invoice by its invoice number.

    When `customer_email` is supplied (FA dispute creation flow), the invoice
    is only returned if the payment record's customer_id matches the sender's
    email or domain — same ownership logic used by the agent pipeline.
    This prevents FAs from accidentally anchoring a dispute to an invoice that
    belongs to a different customer.
    """
    from src.data.repositories.invoice_repository import PaymentRepository
    from src.control.agents.nodes.identify_invoice import _check_invoice_ownership
    from fastapi import HTTPException

    service = InvoiceService(db)
    invoice = await service.get_by_number(invoice_number)

    # No ownership filter requested — return as-is
    if not customer_email:
        return invoice

    # Verify the invoice belongs to the given customer_email / their domain
    pay_repo = PaymentRepository(db)
    payments = await pay_repo.get_all_by_invoice_number(invoice.invoice_number)
    invoice_customer_id = payments[0].customer_id if payments else None

    if invoice_customer_id:
        is_verified, reason = _check_invoice_ownership(
            invoice_customer_id=invoice_customer_id,
            sender_email=customer_email,
        )
        if not is_verified:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Invoice {invoice_number} does not belong to {customer_email}. "
                    "You can only anchor disputes to invoices owned by this customer."
                ),
            )
    # If no payment record exists yet → no ownership check possible, allow through
    return invoice


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get an invoice by its database ID."""
    service = InvoiceService(db)
    return await service.get_invoice(invoice_id)
