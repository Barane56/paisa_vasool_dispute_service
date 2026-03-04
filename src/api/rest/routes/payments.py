from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.clients.postgres import get_db
from src.data.repositories.repositories import PaymentRepository
from src.api.rest.dependencies import get_current_user
from src.schemas.schemas import CurrentUser, PaymentDetailResponse
from src.core.exceptions import PaymentNotFoundError

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.get("/{payment_detail_id}", response_model=PaymentDetailResponse)
async def get_payment_detail(
    payment_detail_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a payment detail record by its ID."""
    repo = PaymentRepository(db)
    payment = await repo.get_by_id(payment_detail_id)
    if not payment:
        raise PaymentNotFoundError(payment_detail_id)
    return payment
