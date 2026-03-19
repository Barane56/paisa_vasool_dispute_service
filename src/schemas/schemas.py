# schemas.py — backward-compatibility shim
# All schemas now live in separate files. This re-exports everything so that
# existing imports like `from src.schemas.schemas import X` keep working.
from .common_schemas  import CurrentUser, ErrorResponse, SuccessResponse, TaskResponse, HealthResponse
from .invoice_schemas import InvoiceResponse, InvoiceListResponse, InvoiceUploadResponse, PaymentDetailResponse, PaymentDetailListResponse, CustomerPaymentListResponse
from .email_schemas   import EmailAttachmentResponse, EmailResponse, EmailListResponse, EmailIngestResponse
from .dispute_schemas import (
    DisputeTypeResponse, DisputeTypeCreate,
    OpenQuestionResponse, AIAnalysisResponse,
    DisputeResponse, DisputeDetailResponse, DisputeListResponse,
    DisputeStatusUpdate, DisputeAssignRequest, DisputeAssignmentResponse,
    TimelineEpisodeResponse, DisputeTimelineResponse, TimelineAttachment,
    MemorySummaryResponse, QuestionStatusUpdate,
    SupportingRefResponse, SupportingRefCreate, SupportingRefListResponse,
    DraftEmailResponse,
    FADisputeCreate,
    DisputeDocumentResponse, DisputeDocumentListResponse,
)
