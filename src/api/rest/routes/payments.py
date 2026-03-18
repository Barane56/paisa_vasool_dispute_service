from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from src.data.clients.postgres import get_db
from src.data.repositories.repositories import PaymentRepository
from src.api.rest.dependencies import get_current_user
from src.schemas.schemas import (
    CurrentUser,
    PaymentDetailResponse,
    PaymentDetailListResponse,
    CustomerPaymentListResponse,
)
from src.core.exceptions import PaymentNotFoundError

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.get("/by-invoice/{invoice_number}", response_model=PaymentDetailListResponse)
async def get_payments_by_invoice(
    invoice_number: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Return **all** payment detail records linked to the given invoice number.

    A single invoice may have multiple partial payments, chargebacks,
    or re-payment attempts — this endpoint returns the complete list.
    """
    repo = PaymentRepository(db)
    items = await repo.get_all_by_invoice_number(invoice_number)
    return PaymentDetailListResponse(
        invoice_number=invoice_number,
        total=len(items),
        items=items,
    )


@router.get("/by-customer/{customer_id}", response_model=CustomerPaymentListResponse)
async def get_payments_by_customer(
    customer_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Return paginated payment detail records for a customer across all invoices.
    Useful for reviewing a customer's complete payment history.
    """
    repo = PaymentRepository(db)
    items, total = await repo.get_all_by_customer(customer_id, limit=limit, offset=offset)
    return CustomerPaymentListResponse(
        customer_id=customer_id,
        total=total,
        items=items,
    )


@router.get("/{payment_detail_id}", response_model=PaymentDetailResponse)
async def get_payment_detail(
    payment_detail_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a single payment detail record by its ID."""
    repo = PaymentRepository(db)
    payment = await repo.get_by_id(payment_detail_id)
    if not payment:
        raise PaymentNotFoundError(payment_detail_id)
    return payment
