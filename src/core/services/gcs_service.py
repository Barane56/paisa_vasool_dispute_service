"""
src/core/services/gcs_service.py
=================================
GCS wrapper for dispute attachment storage.

Storage mode is controlled by GCS_ENABLED in settings (.env):
  GCS_ENABLED=True   → use Google Cloud Storage (production)
  GCS_ENABLED=False  → use local filesystem (development / no GCS creds)

All public functions fall back to raising GCSUnavailable when GCS init
fails — callers catch this and use local storage instead.
"""
from __future__ import annotations

import uuid
import datetime
import logging
from pathlib import Path

from src.config.settings import settings

logger = logging.getLogger(__name__)

_bucket = None
_storage_client = None
_gcs_init_failed = False   # once we know GCS is broken, stop retrying


class GCSUnavailable(Exception):
    """GCS is not available — caller should use local storage fallback."""


def _get_bucket():
    global _bucket, _storage_client, _gcs_init_failed

    # If we already know GCS is broken, skip immediately
    if _gcs_init_failed:
        raise GCSUnavailable("GCS previously failed to initialise — using local storage")

    if _bucket is not None:
        return _bucket

    try:
        from google.cloud import storage
        from google.auth.exceptions import DefaultCredentialsError
    except ImportError as exc:
        _gcs_init_failed = True
        raise GCSUnavailable(
            "Missing dependency 'google-cloud-storage'. Run: uv add google-cloud-storage"
        ) from exc

    try:
        _storage_client = storage.Client(project=settings.GCS_PROJECT_ID)
        _bucket = _storage_client.bucket(settings.GCS_BUCKET_NAME)
        logger.info(f"GCS bucket initialised: {settings.GCS_BUCKET_NAME}")
        return _bucket
    except Exception as exc:
        _gcs_init_failed = True
        logger.warning(
            f"GCS init failed (will use local storage fallback): {exc}"
        )
        raise GCSUnavailable(f"GCS client init failed: {exc}") from exc


# Keep backward compat alias
GCSCredentialsUnavailable = GCSUnavailable


def upload_attachment(file_bytes: bytes, filename: str, folder: str) -> str:
    """
    Upload to GCS. Raises GCSUnavailable if GCS is not reachable.
    Callers must catch and fall back to local storage.
    """
    safe_name   = Path(filename).name.replace(" ", "_")[:100]
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    blob_path   = f"{settings.GCS_BUCKET_PREFIX}/attachments/{folder}/{unique_name}"

    blob = _get_bucket().blob(blob_path)
    blob.upload_from_string(file_bytes, content_type="application/octet-stream")
    logger.info(f"GCS upload: {blob_path}")
    return blob_path


def download_attachment(blob_path: str) -> bytes:
    """
    Download bytes from GCS. Raises GCSUnavailable if GCS is not reachable.
    """
    blob = _get_bucket().blob(blob_path)
    data = blob.download_as_bytes()
    logger.info(f"GCS download: {blob_path} ({len(data)} bytes)")
    return data


def get_signed_url(blob_path: str, expiry_minutes: int = 15) -> str:
    """
    Generate a short-lived signed URL.
    Raises GCSUnavailable if ADC credentials are not configured.
    Callers must catch and fall back to API byte-streaming.
    """
    # First make sure the bucket is reachable (raises GCSUnavailable if not)
    _get_bucket()

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
                "Application Default Credentials not found. "
                "Set GOOGLE_APPLICATION_CREDENTIALS or run "
                "'gcloud auth application-default login'. "
                f"Original: {cred_err}"
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
        logger.info(f"GCS signed URL generated for {blob_path} (expires {expiry_minutes}m)")
        return url

    except GCSUnavailable:
        raise
    except Exception as exc:
        raise GCSUnavailable(f"Signed URL generation failed: {exc}") from exc


def get_public_url(blob_path: str) -> str:
    """
    Returns a signed URL. Raises GCSUnavailable if ADC missing.
    Callers must catch and fall back to byte streaming.
    """
    return get_signed_url(blob_path)
