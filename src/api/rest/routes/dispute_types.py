from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from src.data.clients.postgres import get_db
from src.core.services.dispute_type_service import DisputeTypeService
from src.api.rest.dependencies import get_current_user
from src.schemas.schemas import CurrentUser, DisputeTypeResponse, DisputeTypeCreate, SuccessResponse

router = APIRouter(prefix="/dispute-types", tags=["Dispute Types"])


@router.get("", response_model=List[DisputeTypeResponse])
async def list_dispute_types(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all active dispute types."""
    service = DisputeTypeService(db)
    return await service.list_active()


@router.get("/{type_id}", response_model=DisputeTypeResponse)
async def get_dispute_type(
    type_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a specific dispute type by ID."""
    service = DisputeTypeService(db)
    return await service.get_by_id(type_id)


@router.post("", response_model=DisputeTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_dispute_type(
    data: DisputeTypeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a new dispute type."""
    service = DisputeTypeService(db)
    return await service.create(data)


@router.delete("/{type_id}", response_model=SuccessResponse)
async def deactivate_dispute_type(
    type_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Deactivate a dispute type (soft delete)."""
    service = DisputeTypeService(db)
    await service.deactivate(type_id)
    return SuccessResponse(message=f"DisputeType {type_id} deactivated.")
