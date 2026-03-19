"""
src/schemas/mailbox_schemas.py
"""
from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, EmailStr, Field


# ── Mailbox ───────────────────────────────────────────────────────────────────

class MailboxCreateRequest(BaseModel):
    label:         str      = Field(..., min_length=1, max_length=100)
    email_address: EmailStr
    # IMAP
    imap_host:     str      = Field(..., min_length=1)
    imap_port:     int      = Field(993, ge=1, le=65535)
    use_ssl:       bool     = True
    password:      str      = Field(..., min_length=1)   # plain; encoded before storage
    # SMTP (optional — defaults derived from IMAP if omitted)
    smtp_host:     Optional[str] = None
    smtp_port:     int      = Field(587, ge=1, le=65535)
    smtp_use_tls:  bool     = True


class MailboxResponse(BaseModel):
    mailbox_id:     int
    label:          str
    email_address:  str
    imap_host:      str
    imap_port:      int
    use_ssl:        bool
    smtp_host:      Optional[str]
    smtp_port:      int
    smtp_use_tls:   bool
    is_active:      bool
    is_paused:      bool
    last_polled_at: Optional[datetime]
    last_uid_seen:  Optional[int]
    created_at:     datetime

    class Config:
        from_attributes = True


class MailboxTestResponse(BaseModel):
    mailbox_id: int
    imap_ok:    bool
    smtp_ok:    bool
    message:    str


# ── Inbound message schemas ───────────────────────────────────────────────────

class AttachmentResponse(BaseModel):
    attachment_id:  int
    file_name:      str
    file_type:      str
    file_size:      Optional[int]
    file_path:      str
    created_at:     datetime

    class Config:
        from_attributes = True


class InboxMessageResponse(BaseModel):
    message_id:         int
    mailbox_id:         Optional[int]
    imap_uid:           Optional[int]
    source:             str
    direction:          str
    sender_email:       str
    recipient_email:    Optional[str]
    subject:            str
    body_text:          str
    body_html:          Optional[str]
    received_at:        datetime
    has_attachment:     bool
    dispute_id:         Optional[int]
    email_inbox_id:     Optional[int]
    processing_status:  str
    in_reply_to_header: Optional[str]
    references_header:  Optional[str]
    attachments:        List[AttachmentResponse] = []
    created_at:         datetime

    class Config:
        from_attributes = True


# ── Outbound compose ──────────────────────────────────────────────────────────

class ComposeEmailRequest(BaseModel):
    """Body for POST /disputes/{id}/send-email"""
    to_email:    EmailStr
    subject:     str         = Field(..., min_length=1, max_length=255)
    body_html:   str         = Field(..., min_length=1)
    body_text:   str         = Field(..., min_length=1)
    # Optional: reply to a specific inbound message (sets In-Reply-To + References)
    reply_to_message_id: Optional[int] = None


class OutboundAttachmentResponse(BaseModel):
    attachment_id: int
    file_name:     str
    file_type:     str
    file_size:     Optional[int]
    created_at:    datetime

    class Config:
        from_attributes = True


class OutboundEmailResponse(BaseModel):
    outbound_id:        int
    dispute_id:         int
    sent_by_user_id:    Optional[int] = None
    sent_by_name:       Optional[str] = None
    from_email:         str
    to_email:           str
    subject:            str
    body_html:          str
    body_text:          str
    message_id_header:  Optional[str]
    in_reply_to_header: Optional[str]
    references_header:  Optional[str]
    sent_at:            Optional[datetime]
    status:             str
    failure_reason:     Optional[str]
    attachments:        List[OutboundAttachmentResponse] = []
    created_at:         datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_sender(cls, obj) -> "OutboundEmailResponse":
        """Use this instead of model_validate when you have the ORM object."""
        data = cls.model_validate(obj)
        if obj.sent_by_user_id is None:
            data.sent_by_name = "AI"
        elif obj.sender:
            data.sent_by_name = obj.sender.name
        return data
