# models.py — backward-compatibility shim
# All models now live in separate files. This file re-exports everything
# so existing imports like `from src.data.models.postgres.models import X`
# continue to work without modification.
from .base import Base
from .dispute_models import (
    AnalysisSupportingRef,
    DisputeActivityLog,
    DisputeAIAnalysis,
    DisputeAssignment,
    DisputeMaster,
    DisputeOpenQuestion,
    DisputeRelationship,
    DisputeStatusHistory,
    DisputeType,
)
from .email_models import EmailAttachment, EmailInbox
from .invoice_models import InvoiceData, MatchingPaymentInvoice, PaymentDetail
from .mailbox_models import (
    EmailInboxMessage,
    EmailMessageAttachment,
    MailboxCredential,
    OutboundEmail,
    OutboundEmailAttachment,
)
from .memory_models import DisputeMemoryEpisode, DisputeMemorySummary
from .user_models import RefreshToken, Role, User, UserRole

__all__ = [
    "Base",
    "Role",
    "User",
    "UserRole",
    "RefreshToken",
    "InvoiceData",
    "PaymentDetail",
    "MatchingPaymentInvoice",
    "EmailInbox",
    "EmailAttachment",
    "DisputeType",
    "DisputeMaster",
    "DisputeRelationship",
    "DisputeAIAnalysis",
    "AnalysisSupportingRef",
    "DisputeAssignment",
    "DisputeOpenQuestion",
    "DisputeActivityLog",
    "DisputeStatusHistory",
    "DisputeMemoryEpisode",
    "DisputeMemorySummary",
    "MailboxCredential",
    "EmailInboxMessage",
    "EmailMessageAttachment",
    "OutboundEmail",
    "OutboundEmailAttachment",
]
