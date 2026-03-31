# email_schemas.py — Email Pydantic schemas
from datetime import datetime

from pydantic import BaseModel


class EmailAttachmentResponse(BaseModel):
    attachment_id: int
    file_name: str
    file_type: str
    uploaded_at: datetime
    model_config = {"from_attributes": True}


class EmailResponse(BaseModel):
    email_id: int
    sender_email: str
    subject: str
    body_text: str
    received_at: datetime
    has_attachment: bool
    processing_status: str
    failure_reason: str | None
    dispute_id: int | None
    routing_confidence: float | None
    attachments: list[EmailAttachmentResponse] = []
    model_config = {"from_attributes": True}


class EmailListResponse(BaseModel):
    total: int
    items: list[EmailResponse]


class EmailIngestResponse(BaseModel):
    email_id: int
    processing_status: str
    task_id: str
