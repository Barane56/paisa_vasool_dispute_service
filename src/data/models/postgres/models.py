# models.py — backward-compatibility shim
# All models now live in separate files. This file re-exports everything
# so existing imports like `from src.data.models.postgres.models import X`
# continue to work without modification.
from .base import Base
from .user_models    import Role, User, UserRole, RefreshToken
from .invoice_models import InvoiceData, PaymentDetail, MatchingPaymentInvoice
from .email_models   import EmailInbox, EmailAttachment
from .dispute_models import (
    DisputeType, DisputeMaster, DisputeRelationship,
    DisputeAIAnalysis, AnalysisSupportingRef,
    DisputeAssignment, DisputeOpenQuestion,
    DisputeActivityLog, DisputeStatusHistory,
)
from .memory_models   import DisputeMemoryEpisode, DisputeMemorySummary
from .mailbox_models  import (
    MailboxCredential, EmailInboxMessage, EmailMessageAttachment,
    OutboundEmail, OutboundEmailAttachment,
)

__all__ = [
    "Base",
    "Role", "User", "UserRole", "RefreshToken",
    "InvoiceData", "PaymentDetail", "MatchingPaymentInvoice",
    "EmailInbox", "EmailAttachment",
    "DisputeType", "DisputeMaster", "DisputeRelationship",
    "DisputeAIAnalysis", "AnalysisSupportingRef",
    "DisputeAssignment", "DisputeOpenQuestion",
    "DisputeActivityLog", "DisputeStatusHistory",
    "DisputeMemoryEpisode", "DisputeMemorySummary",
    "MailboxCredential", "EmailInboxMessage", "EmailMessageAttachment",
    "OutboundEmail", "OutboundEmailAttachment",
]
