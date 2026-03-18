# user_repository.py — UserRepository, UserRoleRepository
from typing import Optional, List
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from .base import BaseRepository
from src.data.models.postgres import User, UserRole, Role


class UserRepository(BaseRepository[User]):
    def __init__(self, db: AsyncSession):
        super().__init__(User, db)

    async def get_by_id(self, user_id: int, **kwargs) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()


class UserRoleRepository(BaseRepository[UserRole]):
    def __init__(self, db: AsyncSession):
        super().__init__(UserRole, db)

    async def get_all_fa(self) -> List[int]:
        """Return up to 10 random finance-associate user IDs (non-admin)."""
        stmt = await self.db.execute(select(Role).where(Role.role_name == "admin"))
        admin_role = stmt.scalar_one_or_none()
        if not admin_role:
            raise Exception("Admin Role not found, Aborting")

        stmt = await self.db.execute(
            select(UserRole.user_id)
            .where(UserRole.role_id != admin_role.role_id)
            .order_by(func.random())
            .limit(10)
        )
        return stmt.scalars().all()
