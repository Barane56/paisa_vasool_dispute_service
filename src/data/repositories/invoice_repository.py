# invoice_repository.py — InvoiceRepository, PaymentRepository
from typing import Optional, List
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from .base import BaseRepository
from src.data.models.postgres import InvoiceData, PaymentDetail


class InvoiceRepository(BaseRepository[InvoiceData]):
    def __init__(self, db: AsyncSession):
        super().__init__(InvoiceData, db)

    async def get_by_id(self, invoice_id: int, **kwargs) -> Optional[InvoiceData]:
        result = await self.db.execute(select(InvoiceData).where(InvoiceData.invoice_id == invoice_id))
        return result.scalar_one_or_none()

    async def get_by_invoice_number(self, invoice_number: str) -> Optional[InvoiceData]:
        result = await self.db.execute(select(InvoiceData).where(InvoiceData.invoice_number == invoice_number))
        return result.scalar_one_or_none()

    async def search_by_number_fuzzy(self, query: str) -> List[InvoiceData]:
        stmt = select(InvoiceData).where(InvoiceData.invoice_number.ilike(f"%{query}%")).limit(10)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_all_paginated(self, limit: int = 20, offset: int = 0) -> tuple[List[InvoiceData], int]:
        total = (await self.db.execute(select(func.count()).select_from(InvoiceData))).scalar_one()
        stmt = select(InvoiceData).order_by(InvoiceData.invoice_id.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total


class PaymentRepository(BaseRepository[PaymentDetail]):
    def __init__(self, db: AsyncSession):
        super().__init__(PaymentDetail, db)

    async def get_by_id(self, payment_id: int, **kwargs) -> Optional[PaymentDetail]:
        result = await self.db.execute(select(PaymentDetail).where(PaymentDetail.payment_detail_id == payment_id))
        return result.scalar_one_or_none()

    async def get_by_customer_and_invoice(self, customer_id: str, invoice_number: str) -> Optional[PaymentDetail]:
        stmt = select(PaymentDetail).where(
            and_(PaymentDetail.customer_id == customer_id, PaymentDetail.invoice_number == invoice_number)
        ).limit(1)
        return (await self.db.execute(stmt)).scalars().first()

    async def get_all_by_invoice_number(self, invoice_number: str) -> List[PaymentDetail]:
        stmt = select(PaymentDetail).where(PaymentDetail.invoice_number == invoice_number).order_by(PaymentDetail.payment_detail_id.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_all_by_customer(self, customer_id: str, limit: int = 50, offset: int = 0) -> tuple[List[PaymentDetail], int]:
        total = (await self.db.execute(select(func.count()).select_from(PaymentDetail).where(PaymentDetail.customer_id == customer_id))).scalar_one()
        stmt = select(PaymentDetail).where(PaymentDetail.customer_id == customer_id).order_by(PaymentDetail.payment_detail_id.desc()).limit(limit).offset(offset)
        return list((await self.db.execute(stmt)).scalars().all()), total

    async def get_by_customer(self, customer_id: str) -> List[PaymentDetail]:
        result = await self.db.execute(select(PaymentDetail).where(PaymentDetail.customer_id == customer_id))
        return list(result.scalars().all())
