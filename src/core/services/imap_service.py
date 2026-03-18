"""
src/core/services/imap_service.py
==================================
Handles all IMAP interaction:
  - Testing mailbox connectivity
  - Fetching unseen emails (INBOX) by UID
  - Parsing email parts (body text, html, attachments)
  - Storing attachments to the local filesystem
  - Extracting text from various file types (pdf, csv, xlsx, images, etc.)
"""
from __future__ import annotations

import base64
import email
import imaplib
import logging
import mimetypes
import os
import re
import uuid
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config.settings import settings

logger = logging.getLogger(__name__)

# ── Storage dir ───────────────────────────────────────────────────────────────
ATTACHMENT_STORAGE_DIR = Path(getattr(settings, "ATTACHMENT_STORAGE_DIR", "/tmp/dispute_attachments"))
ATTACHMENT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# GCS — imported lazily so the module loads even without google-cloud-storage installed
from src.core.services.gcs_service import upload_attachment as _gcs_upload, get_public_url as _gcs_url

# ── Dispute-token regex ───────────────────────────────────────────────────────
DISPUTE_TOKEN_RE = re.compile(r"\bDISP-([A-Z0-9]{8,32})\b", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# Password helpers
# ─────────────────────────────────────────────────────────────────────────────

def encode_password(plain: str) -> str:
    """Base64-encode a plain-text password for storage."""
    return base64.b64encode(plain.encode("utf-8")).decode("ascii")


def decode_password(encoded: str) -> str:
    """Decode a base64-encoded password for use."""
    return base64.b64decode(encoded.encode("ascii")).decode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Connectivity test
# ─────────────────────────────────────────────────────────────────────────────

def test_mailbox_connection(
    imap_host: str,
    imap_port: int,
    use_ssl: bool,
    email_address: str,
    password_enc: str,
) -> Tuple[bool, str]:
    """
    Synchronously tests an IMAP connection.
    Returns (ok: bool, message: str).
    """
    try:
        password = decode_password(password_enc)
        if use_ssl:
            conn = imaplib.IMAP4_SSL(imap_host, imap_port)
        else:
            conn = imaplib.IMAP4(imap_host, imap_port)
        conn.login(email_address, password)
        conn.select("INBOX", readonly=True)
        conn.logout()
        return True, "Connection successful"
    except imaplib.IMAP4.error as e:
        return False, f"IMAP authentication/protocol error: {e}"
    except OSError as e:
        return False, f"Network error connecting to {imap_host}:{imap_port}: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Header decoding
# ─────────────────────────────────────────────────────────────────────────────

def _decode_header_value(raw: Optional[str]) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


# ─────────────────────────────────────────────────────────────────────────────
# Attachment text extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_text_from_attachment(file_bytes: bytes, filename: str, mime_type: str) -> str:
    """
    Best-effort text extraction for common attachment types.
    Gracefully returns a placeholder if extraction fails.
    """
    ext = Path(filename).suffix.lower().lstrip(".")

    # ── PDF ──────────────────────────────────────────────────────────────────
    if ext == "pdf" or "pdf" in mime_type:
        try:
            from src.utils.pdf_extractor import extract_text_from_bytes
            return extract_text_from_bytes(file_bytes, "pdf") or "[PDF: no extractable text]"
        except Exception as e:
            logger.warning(f"PDF extraction failed for {filename}: {e}")
            return "[PDF: extraction error]"

    # ── CSV ──────────────────────────────────────────────────────────────────
    if ext == "csv" or "csv" in mime_type:
        try:
            import io
            import csv
            reader = csv.reader(io.StringIO(file_bytes.decode("utf-8", errors="replace")))
            rows = list(reader)
            # First 50 rows as tab-separated
            return "\n".join("\t".join(row) for row in rows[:50])
        except Exception as e:
            logger.warning(f"CSV extraction failed for {filename}: {e}")
            return "[CSV: extraction error]"

    # ── Excel ─────────────────────────────────────────────────────────────────
    if ext in ("xlsx", "xls") or "spreadsheet" in mime_type or "excel" in mime_type:
        try:
            import io
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            lines = []
            for sheet in wb.worksheets:
                lines.append(f"[Sheet: {sheet.title}]")
                for row in sheet.iter_rows(max_row=50, values_only=True):
                    lines.append("\t".join(str(c) if c is not None else "" for c in row))
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Excel extraction failed for {filename}: {e}")
            return "[Excel: extraction error — install openpyxl]"

    # ── Plain text / markdown ─────────────────────────────────────────────────
    if ext in ("txt", "md", "json", "xml", "html") or "text/" in mime_type:
        return file_bytes.decode("utf-8", errors="replace")[:8000]

    # ── Images (describe as placeholder — Groq can't read images currently) ──
    if ext in ("png", "jpg", "jpeg", "gif", "webp", "tiff", "bmp") or "image/" in mime_type:
        return f"[Image attachment: {filename} — visual content, cannot extract text with current LLM]"

    return f"[Attachment: {filename} ({mime_type}) — unsupported type for text extraction]"


# ─────────────────────────────────────────────────────────────────────────────
# Save attachment to filesystem
# ─────────────────────────────────────────────────────────────────────────────

def _save_attachment(file_bytes: bytes, original_filename: str, mailbox_id: int) -> str:
    """
    Upload attachment bytes to GCS.
    Returns the GCS blob path stored in DB as file_path.
    """
    if settings.GCS_ENABLED:
        return _gcs_upload(file_bytes, original_filename, folder=f"inbound/mailbox_{mailbox_id}")
    # Local fallback (GCS disabled)
    safe_name = re.sub(r"[^\w.\-]", "_", original_filename)[:100]
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    subdir = ATTACHMENT_STORAGE_DIR / str(mailbox_id)
    subdir.mkdir(parents=True, exist_ok=True)
    full_path = subdir / unique_name
    full_path.write_bytes(file_bytes)
    return str(Path(str(mailbox_id)) / unique_name)


# ─────────────────────────────────────────────────────────────────────────────
# Email parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_email_message(
    raw_bytes: bytes,
    mailbox_id: int,
) -> Dict[str, Any]:
    """
    Parse a raw RFC-2822 email message.
    Returns a dict with keys:
      message_uid, sender_email, recipient_email, subject,
      body_text, body_html, received_at, has_attachment,
      attachments: [{file_name, file_type, file_size, file_path, extracted_text}]
    """
    msg = email.message_from_bytes(raw_bytes)

    # Headers
    message_uid   = msg.get("Message-ID", "").strip()
    sender_email  = email.utils.parseaddr(_decode_header_value(msg.get("From", "")))[1]
    recipient_raw = msg.get("To") or msg.get("Delivered-To") or ""
    recipient_email = email.utils.parseaddr(_decode_header_value(recipient_raw))[1]
    subject       = _decode_header_value(msg.get("Subject", "(no subject)"))

    # Date
    date_str = msg.get("Date", "")
    try:
        received_at = email.utils.parsedate_to_datetime(date_str)
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
    except Exception:
        received_at = datetime.now(timezone.utc)

    body_text  = ""
    body_html  = ""
    attachments: List[Dict] = []

    for part in msg.walk():
        ct = part.get_content_type()
        cd = part.get("Content-Disposition", "")
        filename = part.get_filename()

        if filename:
            # It's an attachment
            filename = _decode_header_value(filename)
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    mime_type = ct or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    extracted = _extract_text_from_attachment(payload, filename, mime_type)
                    rel_path  = _save_attachment(payload, filename, mailbox_id)
                    attachments.append({
                        "file_name":      filename,
                        "file_type":      Path(filename).suffix.lower().lstrip(".") or mime_type,
                        "file_size":      len(payload),
                        "file_path":      rel_path,
                        "extracted_text": extracted,
                    })
            except Exception as e:
                logger.warning(f"Failed to process attachment {filename}: {e}")
            continue

        if ct == "text/plain" and not body_text:
            payload = part.get_payload(decode=True)
            if payload:
                body_text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        elif ct == "text/html" and not body_html:
            payload = part.get_payload(decode=True)
            if payload:
                body_html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")

    # Fallback: strip HTML for body_text if no plain part
    if not body_text and body_html:
        body_text = re.sub(r"<[^>]+>", " ", body_html)
        body_text = re.sub(r"\s+", " ", body_text).strip()

    return {
        "message_uid":      message_uid,
        "sender_email":     sender_email,
        "recipient_email":  recipient_email,
        "subject":          subject,
        "body_text":        body_text[:10000],
        "body_html":        body_html[:20000] if body_html else None,
        "received_at":      received_at,
        "has_attachment":   bool(attachments),
        "attachments":      attachments,
        # RFC-2822 threading
        "in_reply_to_header": _decode_header_value(msg.get("In-Reply-To", "")).strip() or None,
        "references_header":  _decode_header_value(msg.get("References", "")).strip()  or None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main fetch function
# ─────────────────────────────────────────────────────────────────────────────

def fetch_unseen_emails(
    imap_host: str,
    imap_port: int,
    use_ssl: bool,
    email_address: str,
    password_enc: str,
    last_uid_seen: Optional[int],
    mailbox_id: int,
    batch_size: int = 20,
) -> Tuple[List[Dict], Optional[int]]:
    """
    Connect to IMAP, fetch unseen emails newer than last_uid_seen.
    Returns (list_of_parsed_emails, new_max_uid).
    Each parsed email dict: message_uid, sender_email, recipient_email, subject,
      body_text, body_html, received_at, has_attachment, attachments, imap_uid.
    """
    password = decode_password(password_enc)
    results: List[Dict] = []
    new_max_uid: Optional[int] = last_uid_seen

    try:
        if use_ssl:
            conn = imaplib.IMAP4_SSL(imap_host, imap_port)
        else:
            conn = imaplib.IMAP4(imap_host, imap_port)

        conn.login(email_address, password)
        conn.select("INBOX", readonly=False)

        # Search for UNSEEN messages; if we have a last_uid, use UID SEARCH for efficiency
        if last_uid_seen:
            status, data = conn.uid("search", None, f"UID {last_uid_seen + 1}:*")
        else:
            status, data = conn.uid("search", None, "UNSEEN")

        if status != "OK" or not data[0]:
            conn.logout()
            return results, new_max_uid

        uid_list = data[0].split()
        # Limit to batch_size to avoid huge spikes
        uid_list = uid_list[-batch_size:]

        for uid_bytes in uid_list:
            uid = int(uid_bytes)
            if last_uid_seen and uid <= last_uid_seen:
                continue

            status, msg_data = conn.uid("fetch", str(uid), "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
            if not raw:
                continue

            try:
                parsed = _parse_email_message(raw, mailbox_id)
                parsed["imap_uid"] = uid
                results.append(parsed)
                if new_max_uid is None or uid > new_max_uid:
                    new_max_uid = uid
            except Exception as e:
                logger.error(f"Failed parsing email UID {uid}: {e}", exc_info=True)

        conn.logout()
    except Exception as e:
        logger.error(f"IMAP fetch error for mailbox {email_address}: {e}", exc_info=True)

    return results, new_max_uid


# ─────────────────────────────────────────────────────────────────────────────
# Dispute token extractor
# ─────────────────────────────────────────────────────────────────────────────

def extract_dispute_token(text: str) -> Optional[str]:
    """
    Extract DISP-XXXXXXXXX token from email body.
    Returns the full token string e.g. 'DISP-A1B2C3D4' or None.
    """
    m = DISPUTE_TOKEN_RE.search(text or "")
    if m:
        return f"DISP-{m.group(1).upper()}"
    return None
