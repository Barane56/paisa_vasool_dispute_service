"""
Invoice service – handles PDF upload, Groq-powered data extraction,
and storage into invoice_data table.
"""

import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.repositories.repositories import InvoiceRepository
from src.data.models.postgres.models import InvoiceData
from src.utils.pdf_extractor import extract_text_from_bytes
from src.core.exceptions import (
    FileTooLargeError, UnsupportedFileTypeError,
    InvoiceExtractionError, InvoiceNotFoundError,
)
from src.config.settings import settings
from src.schemas.schemas import InvoiceUploadResponse

logger = logging.getLogger(__name__)


class InvoiceService:
    def __init__(self, db: AsyncSession):
        self.repo = InvoiceRepository(db)
        self.db = db

    async def upload_and_extract(
        self,
        file_bytes: bytes,
        file_name: str,
        invoice_url: str,
    ) -> InvoiceUploadResponse:
        """
        1. Validate file
        2. Extract raw text from PDF
        3. Send text to Groq to extract structured invoice data
        4. Store in invoice_data table (invoice_details = Groq JSON)
        5. Return structured response
        """
        # Validate size
        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise FileTooLargeError(settings.MAX_UPLOAD_SIZE_MB)

        # Validate type
        file_ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        if file_ext not in settings.ALLOWED_FILE_TYPES:
            raise UnsupportedFileTypeError(file_ext, settings.ALLOWED_FILE_TYPES)

        # Extract raw text
        raw_text = extract_text_from_bytes(file_bytes, file_ext)
        if not raw_text.strip():
            raise InvoiceExtractionError("No extractable text found in the uploaded PDF.")

        # Groq extraction
        from src.handlers.http_clients.llm_client import get_llm_client
        llm = get_llm_client()
        try:
            extracted_data = await llm.extract_invoice_data(raw_text)
        except Exception as e:
            raise InvoiceExtractionError(str(e))

        invoice_number = extracted_data.get("invoice_number")
        if not invoice_number:
            # Use filename as fallback
            invoice_number = file_name.rsplit(".", 1)[0]
            logger.warning(f"Could not extract invoice_number; using filename: {invoice_number}")

        # Check for duplicate
        existing = await self.repo.get_by_invoice_number(invoice_number)
        if existing:
            logger.info(f"Invoice {invoice_number} already exists (id={existing.invoice_id}). Updating details.")
            existing.invoice_details = extracted_data
            await self.db.commit()
            await self.db.refresh(existing)
            return InvoiceUploadResponse(
                invoice_id=existing.invoice_id,
                invoice_number=invoice_number,
                extracted_data=extracted_data,
                message="Invoice already existed; details updated with Groq extraction.",
            )

        # Store new invoice
        invoice = InvoiceData(
            invoice_number=invoice_number,
            invoice_url=invoice_url,
            invoice_details=extracted_data,
        )
        self.db.add(invoice)
        await self.db.commit()
        await self.db.refresh(invoice)

        logger.info(f"Invoice stored: id={invoice.invoice_id}, number={invoice_number}")

        return InvoiceUploadResponse(
            invoice_id=invoice.invoice_id,
            invoice_number=invoice_number,
            extracted_data=extracted_data,
            message="Invoice uploaded and data extracted successfully.",
        )

    async def get_invoice(self, invoice_id: int):
        invoice = await self.repo.get_by_id(invoice_id)
        if not invoice:
            raise InvoiceNotFoundError(invoice_id)
        return invoice

    async def list_invoices(self, limit: int = 20, offset: int = 0):
        return await self.repo.get_all_paginated(limit, offset)

    async def get_by_number(self, invoice_number: str):
        invoice = await self.repo.get_by_invoice_number(invoice_number)
        if not invoice:
            raise InvoiceNotFoundError(invoice_number)
        return invoice
