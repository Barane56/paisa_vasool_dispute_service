"""
src/core/services/gcs_service.py
=================================
GCS wrapper for dispute attachment storage.

GCS_ENABLED=True  → use Google Cloud Storage (production)
GCS_ENABLED=False → use local filesystem (dev / no GCS creds)

The google-cloud-storage library is purely synchronous (built on requests).
Wrapping functions in `async def` without run_in_executor would block the
event loop — so all blocking I/O is offloaded to a thread pool via
asyncio.get_event_loop().run_in_executor(None, ...).

Public async API:
  await async_upload_attachment(file_bytes, filename, folder) → str
  await async_download_attachment(blob_path)                  → bytes
  await async_get_signed_url(blob_path, expiry_minutes)       → str

Sync versions (upload_attachment, download_attachment, get_signed_url)
are still exported for the rare synchronous callers (e.g. imap_service
which is called from a Celery worker, not an async route).
"""
from __future__ import annotations

import asyncio
import datetime
import functools
import logging
import uuid
from pathlib import Path

from src.config.settings import settings

logger = logging.getLogger(__name__)

_bucket         = None
_storage_client = None
_gcs_init_failed = False   # once broken, stop retrying every call


class GCSUnavailable(Exception):
    """Raised when GCS is not reachable — callers must fall back to local storage."""


# Backward-compat alias used by older call sites
GCSCredentialsUnavailable = GCSUnavailable


# ──────────────────────────────────────────────────────────────────────────────
# Internal sync helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_bucket():
    global _bucket, _storage_client, _gcs_init_failed

    if _gcs_init_failed:
        raise GCSUnavailable("GCS previously failed to initialise — using local storage")

    if _bucket is not None:
        return _bucket

    try:
        from google.cloud import storage
    except ImportError as exc:
        _gcs_init_failed = True
        raise GCSUnavailable("Missing google-cloud-storage. Run: uv add google-cloud-storage") from exc

    try:
        _storage_client = storage.Client(project=settings.GCS_PROJECT_ID)
        _bucket = _storage_client.bucket(settings.GCS_BUCKET_NAME)
        logger.info(f"GCS bucket initialised: {settings.GCS_BUCKET_NAME}")
        return _bucket
    except Exception as exc:
        _gcs_init_failed = True
        logger.warning(f"GCS init failed (local fallback will be used): {exc}")
        raise GCSUnavailable(f"GCS client init failed: {exc}") from exc


def _sync_upload(file_bytes: bytes, filename: str, folder: str) -> str:
    safe_name   = Path(filename).name.replace(" ", "_")[:100]
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    blob_path   = f"{settings.GCS_BUCKET_PREFIX}/attachments/{folder}/{unique_name}"
    blob = _get_bucket().blob(blob_path)
    blob.upload_from_string(file_bytes, content_type="application/octet-stream")
    logger.info(f"GCS upload: {blob_path}")
    return blob_path


def _sync_download(blob_path: str) -> bytes:
    blob = _get_bucket().blob(blob_path)
    data = blob.download_as_bytes()
    logger.info(f"GCS download: {blob_path} ({len(data)} bytes)")
    return data


def _sync_signed_url(blob_path: str, expiry_minutes: int) -> str:
    _get_bucket()  # verify reachable first

    try:
        import google.auth
        import google.auth.transport.requests
        from google.auth import impersonated_credentials
        from google.auth.exceptions import DefaultCredentialsError

        try:
            source_credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        except DefaultCredentialsError as cred_err:
            raise GCSUnavailable(
                f"ADC not found — set GOOGLE_APPLICATION_CREDENTIALS. Original: {cred_err}"
            ) from cred_err

        source_credentials.refresh(google.auth.transport.requests.Request())

        target_sa = (settings.GCS_TARGET_SERVICE_ACCOUNT or "").strip()
        if target_sa:
            signing_credentials = impersonated_credentials.Credentials(
                source_credentials=source_credentials,
                target_principal=target_sa,
                target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
                lifetime=300,
            )
        else:
            signing_credentials = source_credentials

        blob = _get_bucket().blob(blob_path)
        url  = blob.generate_signed_url(
            expiration=datetime.timedelta(minutes=expiry_minutes),
            method="GET",
            credentials=signing_credentials,
            version="v4",
        )
        logger.info(f"GCS signed URL generated ({expiry_minutes}m): {blob_path}")
        return url

    except GCSUnavailable:
        raise
    except Exception as exc:
        raise GCSUnavailable(f"Signed URL generation failed: {exc}") from exc


# ──────────────────────────────────────────────────────────────────────────────
# Synchronous public API  (for Celery workers / non-async callers)
# ──────────────────────────────────────────────────────────────────────────────

def upload_attachment(file_bytes: bytes, filename: str, folder: str) -> str:
    """Sync upload — raises GCSUnavailable on failure."""
    return _sync_upload(file_bytes, filename, folder)


def download_attachment(blob_path: str) -> bytes:
    """Sync download — raises GCSUnavailable on failure."""
    return _sync_download(blob_path)


def get_signed_url(blob_path: str, expiry_minutes: int = 15) -> str:
    """Sync signed URL — raises GCSUnavailable if ADC not configured."""
    return _sync_signed_url(blob_path, expiry_minutes)


def get_public_url(blob_path: str) -> str:
    """Alias for get_signed_url — raises GCSUnavailable if ADC not configured."""
    return get_signed_url(blob_path)


# ──────────────────────────────────────────────────────────────────────────────
# Async public API  (for FastAPI route handlers / async services)
# All blocking I/O is offloaded to a thread pool via run_in_executor so the
# event loop is never blocked by a GCS network call.
# ──────────────────────────────────────────────────────────────────────────────

async def async_upload_attachment(file_bytes: bytes, filename: str, folder: str) -> str:
    """
    Non-blocking GCS upload.
    Raises GCSUnavailable on failure — callers must catch and use local fallback.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        functools.partial(_sync_upload, file_bytes, filename, folder),
    )


async def async_download_attachment(blob_path: str) -> bytes:
    """
    Non-blocking GCS download.
    Raises GCSUnavailable on failure.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        functools.partial(_sync_download, blob_path),
    )


async def async_get_signed_url(blob_path: str, expiry_minutes: int = 15) -> str:
    """
    Non-blocking signed URL generation.
    Raises GCSUnavailable if ADC not configured.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        functools.partial(_sync_signed_url, blob_path, expiry_minutes),
    )
