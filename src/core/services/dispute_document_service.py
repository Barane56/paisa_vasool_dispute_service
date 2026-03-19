"""
src/core/services/dispute_document_service.py
==============================================
Handles FA-uploaded supporting documents for disputes.

Storage strategy:
  1. Try GCS if GCS_ENABLED=True
  2. If GCS fails (or disabled) → fall back to local filesystem
     under ATTACHMENT_STORAGE_DIR/dispute_docs/dispute_{id}/

Both upload and download paths handle the fallback transparently.
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import List

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.config.settings import settings
from src.core.exceptions.errors import ResourceNotFoundError
from src.data.models.postgres.dispute_models import DisputeDocument

logger = logging.getLogger(__name__)

STORAGE_DIR = Path(getattr(settings, "ATTACHMENT_STORAGE_DIR", "/tmp/dispute_attachments"))

# Prefix stored in file_path that tells us it's a GCS path vs local path
GCS_PREFIX   = "gcs:"   # file_path = "gcs:bucketprefix/attachments/..."
LOCAL_PREFIX = "local:" # file_path = "local:dispute_docs/dispute_1/uuid_name.pdf"


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)[:100]


def _store_local(file_bytes: bytes, dispute_id: int, safe_name: str) -> str:
    """Save bytes to local filesystem, return LOCAL_PREFIX path stored in DB."""
    local_dir = STORAGE_DIR / "dispute_docs" / f"dispute_{dispute_id}"
    local_dir.mkdir(parents=True, exist_ok=True)
    unique    = f"{uuid.uuid4().hex}_{safe_name}"
    (local_dir / unique).write_bytes(file_bytes)
    rel_path  = f"dispute_docs/dispute_{dispute_id}/{unique}"
    return f"{LOCAL_PREFIX}{rel_path}"


def _local_full_path(file_path: str) -> Path:
    """Convert a LOCAL_PREFIX path to absolute filesystem path."""
    rel = file_path.removeprefix(LOCAL_PREFIX)
    return STORAGE_DIR / rel


class DisputeDocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upload_document(
        self,
        dispute_id:   int,
        uploaded_by:  int,
        file:         UploadFile,
        display_name: str | None = None,
        notes:        str | None = None,
    ) -> DisputeDocument:
        file_bytes = await file.read()
        file_size  = len(file_bytes)
        safe_name  = _safe_filename(file.filename or "document")
        file_type  = file.content_type or "application/octet-stream"

        # ── Try GCS, fall back to local ───────────────────────────────────────
        file_path: str
        if settings.GCS_ENABLED:
            try:
                from src.core.services.gcs_service import async_upload_attachment, GCSUnavailable
                gcs_path = await async_upload_attachment(
                    file_bytes=file_bytes,
                    filename=safe_name,
                    folder=f"dispute_docs/dispute_{dispute_id}",
                )
                file_path = f"{GCS_PREFIX}{gcs_path}"
                logger.info(f"DisputeDocument stored in GCS: {gcs_path}")
            except (GCSUnavailable, Exception) as gcs_err:
                logger.warning(
                    f"GCS upload failed for dispute {dispute_id}, falling back to local: {gcs_err}"
                )
                file_path = _store_local(file_bytes, dispute_id, safe_name)
                logger.info(f"DisputeDocument stored locally: {file_path}")
        else:
            file_path = _store_local(file_bytes, dispute_id, safe_name)
            logger.info(f"DisputeDocument stored locally (GCS disabled): {file_path}")

        doc = DisputeDocument(
            dispute_id=dispute_id,
            uploaded_by=uploaded_by,
            file_name=safe_name,
            file_type=file_type,
            file_size=file_size,
            file_path=file_path,
            display_name=display_name or safe_name,
            notes=notes,
        )
        self.db.add(doc)
        await self.db.commit()
        await self.db.refresh(doc)
        logger.info(
            f"DisputeDocument record saved: doc_id={doc.document_id} "
            f"dispute={dispute_id} size={file_size}"
        )
        return doc

    async def list_documents(self, dispute_id: int) -> List[DisputeDocument]:
        result = await self.db.execute(
            select(DisputeDocument)
            .where(DisputeDocument.dispute_id == dispute_id)
            .order_by(DisputeDocument.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_document(self, document_id: int) -> DisputeDocument:
        result = await self.db.execute(
            select(DisputeDocument).where(DisputeDocument.document_id == document_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise ResourceNotFoundError(f"Document {document_id} not found")
        return doc

    async def get_download_url(self, doc: DisputeDocument) -> str:
        """
        Returns a URL the frontend can use to download the file.
        - GCS-stored + ADC available: returns signed URL (30 min)
        - GCS-stored + no ADC (local dev): falls back to API streaming endpoint
        - Local-stored: always returns API streaming endpoint
        """
        if doc.file_path.startswith(GCS_PREFIX):
            gcs_path = doc.file_path.removeprefix(GCS_PREFIX)
            try:
                from src.core.services.gcs_service import async_get_signed_url, GCSUnavailable, GCSCredentialsUnavailable
                return await async_get_signed_url(gcs_path, expiry_minutes=30)
            except Exception as exc:
                logger.warning(
                    f"Signed URL unavailable for doc {doc.document_id} "
                    f"(falling back to API streaming): {exc}"
                )
        # Local storage OR GCS signed URL unavailable — serve via API download endpoint
        return f"/dispute/api/v1/disputes/{doc.dispute_id}/documents/{doc.document_id}/download"

    async def get_file_bytes(self, doc: DisputeDocument) -> tuple[bytes, str]:
        """
        Fetch raw file bytes for streaming download.
        Returns (bytes, filename).
        Works for both GCS and local storage.
        """
        if doc.file_path.startswith(GCS_PREFIX):
            gcs_path = doc.file_path.removeprefix(GCS_PREFIX)
            try:
                from src.core.services.gcs_service import async_download_attachment
                data = await async_download_attachment(gcs_path)
                return data, doc.file_name
            except Exception as exc:
                logger.error(f"GCS byte download failed for doc {doc.document_id}: {exc}")
                raise ResourceNotFoundError(f"Could not retrieve file from GCS: {exc}")

        # Local path
        full = _local_full_path(doc.file_path)
        if not full.exists():
            raise ResourceNotFoundError(f"File not found on server: {full}")
        return full.read_bytes(), doc.file_name

    async def delete_document(self, document_id: int) -> None:
        doc = await self.get_document(document_id)

        if doc.file_path.startswith(GCS_PREFIX):
            gcs_path = doc.file_path.removeprefix(GCS_PREFIX)
            try:
                from src.core.services.gcs_service import _get_bucket
                _get_bucket().blob(gcs_path).delete()
                logger.info(f"GCS blob deleted: {gcs_path}")
            except Exception as exc:
                logger.warning(f"GCS delete failed (continuing): {exc}")
        elif doc.file_path.startswith(LOCAL_PREFIX):
            try:
                full = _local_full_path(doc.file_path)
                if full.exists():
                    full.unlink()
                    logger.info(f"Local file deleted: {full}")
            except Exception as exc:
                logger.warning(f"Local delete failed (continuing): {exc}")

        await self.db.delete(doc)
        await self.db.commit()
        logger.info(f"DisputeDocument DB record deleted: doc_id={document_id}")
