"""
src/core/services/smtp_service.py
==================================
Sends email on behalf of the system using the mailbox's SMTP credentials.
All emails are sent FROM the mailbox address so:
  - Single point of contact for customers
  - Replies land back in the same IMAP mailbox we poll
  - FA display name appears in the From header

Threading model
---------------
Every outbound email sets:
  Message-ID  : <uuid@mailbox_domain>
  In-Reply-To : Message-ID of the inbound email being replied to (if any)
  References  : full ancestor chain (In-Reply-To + parent References)

When a customer replies, their email client sets:
  In-Reply-To : our Message-ID
  References  : our chain + our Message-ID

The IMAP poller reads those headers → matches to outbound_emails → resolves
the dispute_id without needing a DISP token in the body.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional, Tuple

from src.config.settings import settings
from src.core.services.imap_service import decode_password
from src.core.services.gcs_service import download_attachment as _gcs_download

logger = logging.getLogger(__name__)

ATTACHMENT_STORAGE_DIR = Path(getattr(settings, "ATTACHMENT_STORAGE_DIR", "/tmp/dispute_attachments"))


# ─────────────────────────────────────────────────────────────────────────────
# Message-ID generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_message_id(from_email: str) -> str:
    """Generate a globally-unique RFC-2822 Message-ID."""
    domain = from_email.split("@")[-1] if "@" in from_email else "dispute.system"
    return f"<{uuid.uuid4().hex}@{domain}>"


# ─────────────────────────────────────────────────────────────────────────────
# Build References chain
# ─────────────────────────────────────────────────────────────────────────────

def build_references_chain(
    in_reply_to_message_id: Optional[str],
    parent_references: Optional[str],
) -> Optional[str]:
    """
    Build the References header value for a new outgoing email.
    Concatenates the parent References chain with the In-Reply-To header.
    """
    parts: List[str] = []
    if parent_references:
        parts.extend(parent_references.split())
    if in_reply_to_message_id and in_reply_to_message_id not in parts:
        parts.append(in_reply_to_message_id)
    return " ".join(parts) if parts else None


# ─────────────────────────────────────────────────────────────────────────────
# SMTP connection test
# ─────────────────────────────────────────────────────────────────────────────

def test_smtp_connection(
    smtp_host: str,
    smtp_port: int,
    smtp_use_tls: bool,
    username: str,
    password_enc: str,
) -> Tuple[bool, str]:
    """Synchronously tests SMTP credentials. Returns (ok, message)."""
    try:
        password = decode_password(password_enc)
        if smtp_use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.ehlo()
                server.starttls(context=context)
                server.login(username, password)
        else:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as server:
                server.login(username, password)
        return True, "SMTP connection successful"
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed — check username/password"
    except (smtplib.SMTPException, OSError) as e:
        return False, f"SMTP error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Core send function
# ─────────────────────────────────────────────────────────────────────────────

def send_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_use_tls: bool,
    username: str,
    password_enc: str,
    from_address: str,           # mailbox email address — used bare in From header
    to_address: str,
    subject: str,
    body_html: str,
    body_text: str,
    message_id: str,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    attachment_paths: Optional[List[Tuple[str, str]]] = None,  # [(rel_path, filename), ...]
) -> None:
    """
    Send an email via SMTP.
    Raises smtplib.SMTPException or OSError on failure.
    attachment_paths: list of (relative_path_under_storage_dir, original_filename)
    """
    password = decode_password(password_enc)

    msg = MIMEMultipart("mixed")
    msg["From"]       = from_address   # bare address — e.g. ar@company.com
    msg["To"]         = to_address
    msg["Subject"]    = subject
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    # Attach body
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text, "plain", "utf-8"))
    alt.attach(MIMEText(body_html,  "html",  "utf-8"))
    msg.attach(alt)

    # Attach files
    for rel_path, filename in (attachment_paths or []):
        try:
            if settings.GCS_ENABLED:
                file_bytes = _gcs_download(rel_path)
            else:
                full_path = ATTACHMENT_STORAGE_DIR / rel_path
                if not full_path.exists():
                    logger.warning(f"Attachment not found, skipping: {full_path}")
                    continue
                with open(full_path, "rb") as f:
                    file_bytes = f.read()
        except Exception as att_err:
            logger.warning(f"Could not load attachment {rel_path}: {att_err}")
            continue
        part = MIMEBase("application", "octet-stream")
        part.set_payload(file_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    # Send
    if smtp_use_tls:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(username, password)
            server.sendmail(from_address, [to_address], msg.as_bytes())
    else:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(username, password)
            server.sendmail(from_address, [to_address], msg.as_bytes())

    logger.info(f"Email sent from={from_address} to={to_address} message_id={message_id}")
