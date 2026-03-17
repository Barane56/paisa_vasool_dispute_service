"""
src/api/rest/routes/gcs_test.py
================================
Open (no-auth) endpoints for testing GCS upload and download.
REMOVE or disable these before production deployment.
"""
from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import RedirectResponse

from src.core.services.gcs_service import upload_attachment, get_public_url, download_attachment
from src.config.settings import settings

router = APIRouter(prefix="/test/gcs", tags=["GCS Test (No Auth)"])


@router.post("/upload")
async def test_upload(file: UploadFile = File(...)):
    """
    Upload any file to GCS and return the public URL.
    No authentication required — FOR TESTING ONLY.

    Usage:
        curl -X POST http://localhost:8002/dispute/api/v1/test/gcs/upload \
             -F "file=@/path/to/your/file.pdf"
    """
    if not settings.GCS_ENABLED:
        raise HTTPException(status_code=503, detail="GCS is not enabled in settings.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    blob_path  = upload_attachment(file_bytes, file.filename or "test_file", folder="test")
    public_url = get_public_url(blob_path)

    return {
        "message":    "File uploaded successfully.",
        "file_name":  file.filename,
        "file_size":  len(file_bytes),
        "blob_path":  blob_path,
        "public_url": public_url,
        "note":       "Use the blob_path value to test the download endpoint.",
    }


@router.get("/download")
async def test_download(blob_path: str):
    """
    Redirect to the GCS public URL for a given blob_path.
    No authentication required — FOR TESTING ONLY.

    Usage:
        Open in browser:
        http://localhost:8002/dispute/api/v1/test/gcs/download?blob_path=studyguru/attachments/test/abc123_file.pdf

        Or curl:
        curl -L "http://localhost:8002/dispute/api/v1/test/gcs/download?blob_path=studyguru/attachments/test/abc123_file.pdf"
    """
    if not settings.GCS_ENABLED:
        raise HTTPException(status_code=503, detail="GCS is not enabled in settings.")

    if not blob_path.strip():
        raise HTTPException(status_code=400, detail="blob_path is required.")

    try:
        # Verify the file actually exists before redirecting
        download_attachment(blob_path)
    except Exception:
        raise HTTPException(status_code=404, detail=f"File not found in GCS: {blob_path}")

    public_url = get_public_url(blob_path)
    return RedirectResponse(url=public_url, status_code=302)
