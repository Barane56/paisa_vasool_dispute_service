from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Any


# ── Auth (CurrentUser only – no register/login here) ────────────────────────
class CurrentUser(BaseModel):
    user_id: int
    name: str
    email: str


# ── Common ───────────────────────────────────────────────────────────────────
class ErrorResponse(BaseModel):
    error: str
    detail: Optional[Any] = None
    status_code: int


class SuccessResponse(BaseModel):
    message: str
    data: Optional[Any] = None


class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    redis: str


# ── Invoice ──────────────────────────────────────────────────────────────────
class InvoiceResponse(BaseModel):
    invoice_id: int
    invoice_number: str
    invoice_url: str
    invoice_details: Any
    updated_at: datetime

    model_config = {"from_attributes": True}


class InvoiceListResponse(BaseModel):
    total: int
    items: List[InvoiceResponse]


class InvoiceUploadResponse(BaseModel):
    invoice_id: int
    invoice_number: str
    extracted_data: Any
    message: str


# ── Email ────────────────────────────────────────────────────────────────────
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


# ── Dispute Types ────────────────────────────────────────────────────────────
class DisputeTypeResponse(BaseModel):
    dispute_type_id: int
    reason_name: str
    description: str
    is_active: bool

    model_config = {"from_attributes": True}


class DisputeTypeCreate(BaseModel):
    reason_name: str = Field(min_length=1, max_length=100)
    description: str


# ── Disputes ─────────────────────────────────────────────────────────────────
class OpenQuestionResponse(BaseModel):
    question_id: int
    question_text: str
    status: str
    asked_at: datetime
    answered_at: Optional[datetime]

    model_config = {"from_attributes": True}


class AIAnalysisResponse(BaseModel):
    analysis_id: int
    predicted_category: str
    confidence_score: float
    ai_summary: str
    ai_response: Optional[str]
    auto_response_generated: bool
    memory_context_used: bool
    episodes_referenced: Optional[List[int]]
    created_at: datetime

    model_config = {"from_attributes": True}


class DisputeResponse(BaseModel):
    dispute_id: int
    email_id: int
    invoice_id: Optional[int]
    payment_detail_id: Optional[int]
    customer_id: str
    dispute_type: Optional[DisputeTypeResponse]
    status: str
    priority: str
    description: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DisputeDetailResponse(DisputeResponse):
    latest_analysis: Optional[AIAnalysisResponse] = None
    open_questions_count: int = 0
    assigned_to: Optional[str] = None


class DisputeListResponse(BaseModel):
    total: int
    items: List[DisputeResponse]


class DisputeStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None


class DisputeAssignRequest(BaseModel):
    user_id: int
    notes: Optional[str] = None


class DisputeAssignmentResponse(BaseModel):
    assignment_id: int
    dispute_id: int
    assigned_to: int
    assignee_name: str
    assigned_at: datetime
    status: str

    model_config = {"from_attributes": True}


class TimelineEpisodeResponse(BaseModel):
    episode_id: int
    actor: str
    episode_type: str
    content_text: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DisputeTimelineResponse(BaseModel):
    dispute_id: int
    customer_id: str
    status: str
    timeline: List[TimelineEpisodeResponse]
    pending_questions: int
    assigned_to: Optional[str]


class MemorySummaryResponse(BaseModel):
    summary_id: int
    dispute_id: int
    summary_text: str
    version: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class QuestionStatusUpdate(BaseModel):
    status: str  # ANSWERED or EXPIRED
    notes: Optional[str] = None
