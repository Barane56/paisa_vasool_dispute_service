"""
src/core/services/gcs_service.py
=================================
Thin GCS wrapper for dispute attachment storage.
Uploads bytes  → returns blob path stored in DB.
Downloads bytes → used when attaching files to outbound SMTP.
Signed URL     → short-lived download link returned to frontend (15 min).

Bucket uses Uniform Bucket-Level Access (UBA) so per-object ACLs are
disabled. Public access is via signed URLs — no allUsers IAM grant needed.
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


def _get_bucket():
    global _bucket, _storage_client
    if _bucket is not None:
        return _bucket
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'google-cloud-storage'. "
            "Run: uv add google-cloud-storage"
        ) from exc

    _storage_client = storage.Client(project=settings.GCS_PROJECT_ID)
    _bucket = _storage_client.bucket(settings.GCS_BUCKET_NAME)
    logger.info(f"GCS bucket initialised: {settings.GCS_BUCKET_NAME}")
    return _bucket


def upload_attachment(file_bytes: bytes, filename: str, folder: str) -> str:
    """
    Upload bytes to GCS under:
      {GCS_BUCKET_PREFIX}/attachments/{folder}/{uuid}_{filename}

    Returns the blob path — stored in DB as file_path.
    folder examples:
      "inbound/mailbox_1"   for emails received
      "outbound/dispute_3"  for FA-sent attachments
    """
    safe_name   = Path(filename).name.replace(" ", "_")[:100]
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    blob_path   = f"{settings.GCS_BUCKET_PREFIX}/attachments/{folder}/{unique_name}"

    blob = _get_bucket().blob(blob_path)
    blob.upload_from_string(file_bytes, content_type="application/octet-stream")
    # No make_public() — bucket has UBA enabled, per-object ACLs are disabled.

    logger.info(f"GCS upload: {blob_path}")
    return blob_path


def download_attachment(blob_path: str) -> bytes:
    """Download bytes from GCS by blob path (as stored in DB).
    Used internally by smtp_service when attaching files to outbound emails.
    """
    blob = _get_bucket().blob(blob_path)
    data = blob.download_as_bytes()
    logger.info(f"GCS download: {blob_path} ({len(data)} bytes)")
    return data


def get_signed_url(blob_path: str, expiry_minutes: int = 15) -> str:
    """
    Generate a short-lived signed URL for a GCS object.
    Valid for `expiry_minutes` (default 15). After expiry the link returns 403.

    Uses service account impersonation if GCS_TARGET_SERVICE_ACCOUNT is set,
    otherwise falls back to the default credentials (works on Cloud Run with
    the Compute Engine SA having iam.serviceAccountTokenCreator role).
    """
    try:
        import google.auth
        import google.auth.transport.requests
        from google.auth import impersonated_credentials

        # Get source credentials
        source_credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        # Refresh so we have a valid token
        source_credentials.refresh(google.auth.transport.requests.Request())

        # If a target SA is configured, impersonate it for signing
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

    except Exception as exc:
        logger.error(f"Failed to generate signed URL for {blob_path}: {exc}")
        raise


def get_public_url(blob_path: str) -> str:
    """
    Returns a signed URL (preferred) or falls back to the plain GCS URL.
    This is the function called by the download endpoints in mailboxes.py.
    """
    return get_signed_url(blob_path)