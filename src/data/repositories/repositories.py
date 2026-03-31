# repositories.py — backward-compatibility shim
# All repositories now live in separate files. This re-exports everything
# so existing imports like `from src.data.repositories.repositories import X`
# continue to work without modification.
from .dispute_repository import (
    AnalysisSupportingRefRepository,
    DisputeAIAnalysisRepository,
    DisputeAssignmentRepository,
    DisputeRelationshipRepository,
    DisputeRepository,
    DisputeTypeRepository,
)
from .email_repository import EmailRepository
from .invoice_repository import InvoiceRepository, PaymentRepository
from .mailbox_repository import EmailInboxMessageRepository, MailboxRepository
from .memory_repository import (
    MemoryEpisodeRepository,
    MemorySummaryRepository,
    OpenQuestionRepository,
)
from .user_repository import UserRepository, UserRoleRepository

__all__ = [
    "UserRepository",
    "UserRoleRepository",
    "InvoiceRepository",
    "PaymentRepository",
    "EmailRepository",
    "DisputeTypeRepository",
    "DisputeRepository",
    "DisputeAIAnalysisRepository",
    "DisputeAssignmentRepository",
    "AnalysisSupportingRefRepository",
    "DisputeRelationshipRepository",
    "MemoryEpisodeRepository",
    "MemorySummaryRepository",
    "OpenQuestionRepository",
    "MailboxRepository",
    "EmailInboxMessageRepository",
]
