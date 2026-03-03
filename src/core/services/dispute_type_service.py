import logging
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.repositories.repositories import DisputeTypeRepository
from src.data.models.postgres.models import DisputeType
from src.core.exceptions import DisputeTypeNotFoundError, AlreadyExistsError
from src.schemas.schemas import DisputeTypeCreate

logger = logging.getLogger(__name__)


class DisputeTypeService:
    def __init__(self, db: AsyncSession):
        self.repo = DisputeTypeRepository(db)
        self.db = db

    async def list_active(self):
        return await self.repo.get_active_types()

    async def get_by_id(self, type_id: int):
        dtype = await self.repo.get_by_id(type_id)
        if not dtype:
            raise DisputeTypeNotFoundError(type_id)
        return dtype

    async def create(self, data: DisputeTypeCreate) -> DisputeType:
        existing = await self.repo.get_by_name(data.reason_name)
        if existing:
            raise AlreadyExistsError("DisputeType", "reason_name", data.reason_name)
        dtype = DisputeType(reason_name=data.reason_name, description=data.description)
        self.db.add(dtype)
        await self.db.commit()
        await self.db.refresh(dtype)
        return dtype

    async def deactivate(self, type_id: int) -> DisputeType:
        dtype = await self.get_by_id(type_id)
        dtype.is_active = False
        await self.db.commit()
        return dtype
