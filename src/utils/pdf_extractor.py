import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF bytes using pypdf."""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        texts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                texts.append(text.strip())
        return "\n\n".join(texts)
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        return ""


def extract_text_from_bytes(file_bytes: bytes, file_type: str) -> str:
    """Route extraction based on file type."""
    file_type = file_type.lower().strip(".")
    if file_type == "pdf":
        return extract_text_from_pdf(file_bytes)
    elif file_type in ("txt",):
        return file_bytes.decode("utf-8", errors="replace")
    elif file_type in ("csv",):
        return file_bytes.decode("utf-8", errors="replace")
    else:
        logger.warning(f"Unsupported file type for extraction: {file_type}")
        return ""
