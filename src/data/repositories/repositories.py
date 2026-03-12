# repositories.py — backward-compatibility shim
# All repositories now live in separate files. This re-exports everything
# so existing imports like `from src.data.repositories.repositories import X`
# continue to work without modification.
from .user_repository    import UserRepository, UserRoleRepository
from .invoice_repository import InvoiceRepository, PaymentRepository
from .email_repository   import EmailRepository
from .dispute_repository import (
    DisputeTypeRepository, DisputeRepository,
    DisputeAIAnalysisRepository, DisputeAssignmentRepository,
    AnalysisSupportingRefRepository, DisputeRelationshipRepository,
)
from .memory_repository  import MemoryEpisodeRepository, MemorySummaryRepository, OpenQuestionRepository

__all__ = [
    "UserRepository", "UserRoleRepository",
    "InvoiceRepository", "PaymentRepository",
    "EmailRepository",
    "DisputeTypeRepository", "DisputeRepository",
    "DisputeAIAnalysisRepository", "DisputeAssignmentRepository",
    "AnalysisSupportingRefRepository", "DisputeRelationshipRepository",
    "MemoryEpisodeRepository", "MemorySummaryRepository", "OpenQuestionRepository",
]
