# dispute_schemas.py — Dispute-domain Pydantic schemas
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List


class DisputeTypeResponse(BaseModel):
    dispute_type_id: int
    reason_name: str
    description: str
    is_active: bool
    model_config = {"from_attributes": True}


class DisputeTypeCreate(BaseModel):
    reason_name: str = Field(min_length=1, max_length=100)
    description: str


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
    has_new_customer_message: bool = False   # True when latest episode is from CUSTOMER


class DisputeListResponse(BaseModel):
    total: int
    items: List[DisputeDetailResponse]


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


class TimelineAttachment(BaseModel):
    """A single attachment linked to a timeline episode."""
    attachment_id: int
    file_name: str
    file_type: str
    download_url: str          # ready-to-use URL path for the frontend
    source: str                # "inbound" | "outbound"


class TimelineEpisodeResponse(BaseModel):
    episode_id: int
    actor: str
    actor_name: Optional[str] = None   # populated for ASSOCIATE episodes (FA real name)
    episode_type: str
    content_text: str
    created_at: datetime
    attachments: List[TimelineAttachment] = []
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


class SupportingRefResponse(BaseModel):
    ref_id: int
    analysis_id: int
    reference_table: str
    ref_id_value: int
    context_note: str
    model_config = {"from_attributes": True}


class SupportingRefCreate(BaseModel):
    analysis_id: int
    reference_table: str = Field(..., description="Table name, e.g. 'payment_detail', 'invoice_data', 'email_attachments'")
    ref_id_value: int = Field(..., description="Primary key value in reference_table")
    context_note: str = Field(..., description="Why this document supports the analysis")


class SupportingRefListResponse(BaseModel):
    dispute_id: int
    total: int
    items: List[SupportingRefResponse]
