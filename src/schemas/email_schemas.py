# email_schemas.py — Email Pydantic schemas
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


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
    failure_reason: Optional[str]
    dispute_id: Optional[int]
    routing_confidence: Optional[float]
    attachments: List[EmailAttachmentResponse] = []
    model_config = {"from_attributes": True}


class EmailListResponse(BaseModel):
    total: int
    items: List[EmailResponse]


class EmailIngestResponse(BaseModel):
    email_id: int
    processing_status: str
    task_id: str
