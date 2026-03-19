# dispute_models.py — DisputeType, DisputeMaster, DisputeRelationship,
#                     DisputeAIAnalysis, AnalysisSupportingRef,
#                     DisputeAssignment, DisputeOpenQuestion,
#                     DisputeActivityLog, DisputeStatusHistory
from sqlalchemy import (
    Column, Enum, Integer, String, Text, Boolean, Numeric, BigInteger,
    TIMESTAMP, ForeignKey, Index, func, text,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from .base import Base
from src.constants.enums import SeverityLevel


class DisputeType(Base):
    __tablename__ = "dispute_type"

    dispute_type_id = Column(Integer, primary_key=True)
    reason_name     = Column(String(255), unique=True, nullable=False)
    description     = Column(Text, nullable=True)
    severity_level  = Column(
        SQLEnum(SeverityLevel, name="severity_level_enum", create_constraint=True),
        nullable=True, index=True,
    )
    is_active = Column(Boolean, default=True, server_default=text("TRUE"), nullable=False)

    disputes = relationship("DisputeMaster", back_populates="dispute_type", lazy="select")

    __table_args__ = (
        Index("ix_dispute_type_reason_name",    "reason_name"),
        Index("ix_dispute_type_is_active",      "is_active"),
        Index("ix_dispute_type_severity_level", "severity_level"),
    )

    def __repr__(self) -> str:
        return f"<DisputeType id={self.dispute_type_id} name={self.reason_name} severity={self.severity_level}>"


class DisputeMaster(Base):
    __tablename__ = "dispute_master"

    dispute_id        = Column(Integer, primary_key=True)
    email_id          = Column(Integer, ForeignKey("email_inbox.email_id",             ondelete="RESTRICT"), nullable=True)   # nullable for FA-created disputes
    invoice_id        = Column(Integer, ForeignKey("invoice_data.invoice_id",          ondelete="SET NULL"), nullable=True)
    payment_detail_id = Column(Integer, ForeignKey("payment_detail.payment_detail_id", ondelete="SET NULL"), nullable=True)
    customer_id       = Column(String(100), nullable=False)
    dispute_type_id   = Column(Integer, ForeignKey("dispute_type.dispute_type_id",     ondelete="RESTRICT"), nullable=False)
    status            = Column(String(50), nullable=False)
    priority          = Column(String(20), nullable=False, default="MEDIUM", server_default="MEDIUM")
    description       = Column(Text, nullable=False)
    source            = Column(String(20), nullable=False, default="EMAIL", server_default="EMAIL")  # EMAIL | FA_MANUAL
    dispute_token     = Column(String(32), unique=True, nullable=True)
    parent_dispute_id = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="SET NULL", use_alter=True, name="fk_dispute_master_parent_id"), nullable=True)
    created_at        = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at        = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    source_email            = relationship("EmailInbox",           foreign_keys=[email_id],                                                 lazy="joined")
    routed_emails           = relationship("EmailInbox",           foreign_keys="EmailInbox.dispute_id",                back_populates="dispute",           lazy="select")
    invoice                 = relationship("InvoiceData",          back_populates="disputes",                           lazy="joined")
    payment_detail          = relationship("PaymentDetail",        back_populates="disputes",                           lazy="joined")
    dispute_type            = relationship("DisputeType",          back_populates="disputes",                           lazy="joined")
    ai_analyses             = relationship("DisputeAIAnalysis",    back_populates="dispute",                            lazy="select", cascade="all, delete-orphan")
    episodes                = relationship("DisputeMemoryEpisode", back_populates="dispute",                            lazy="select", cascade="all, delete-orphan", order_by="DisputeMemoryEpisode.created_at")
    memory_summary          = relationship("DisputeMemorySummary", back_populates="dispute",                            lazy="select", uselist=False, cascade="all, delete-orphan")
    assignments             = relationship("DisputeAssignment",    back_populates="dispute",                            lazy="select", cascade="all, delete-orphan")
    open_questions          = relationship("DisputeOpenQuestion",  back_populates="dispute",                            lazy="select", cascade="all, delete-orphan")
    activity_logs           = relationship("DisputeActivityLog",   back_populates="dispute",                            lazy="select", cascade="all, delete-orphan")
    status_history          = relationship("DisputeStatusHistory", back_populates="dispute",                            lazy="select", cascade="all, delete-orphan")
    forked_disputes         = relationship("DisputeMaster",        foreign_keys="DisputeMaster.parent_dispute_id",      lazy="select", back_populates="parent_dispute")
    parent_dispute          = relationship("DisputeMaster",        foreign_keys=[parent_dispute_id],                    lazy="select", back_populates="forked_disputes", remote_side="DisputeMaster.dispute_id")
    relationships_as_source = relationship("DisputeRelationship",  foreign_keys="DisputeRelationship.source_dispute_id", back_populates="source_dispute", lazy="select", cascade="all, delete-orphan")
    relationships_as_target = relationship("DisputeRelationship",  foreign_keys="DisputeRelationship.target_dispute_id", back_populates="target_dispute", lazy="select", cascade="all, delete-orphan")
    new_message_flag        = relationship("DisputeNewMessage",      back_populates="dispute",                             lazy="select", uselist=False, cascade="all, delete-orphan")
    supporting_documents    = relationship("DisputeDocument",          back_populates="dispute",                             lazy="select", cascade="all, delete-orphan", order_by="DisputeDocument.created_at")

    __table_args__ = (
        Index("ix_dispute_master_customer_id",       "customer_id"),
        Index("ix_dispute_master_status",            "status"),
        Index("ix_dispute_master_priority",          "priority"),
        Index("ix_dispute_master_dispute_type_id",   "dispute_type_id"),
        Index("ix_dispute_master_created_at",        "created_at"),
        Index("ix_dispute_master_parent_dispute_id", "parent_dispute_id"),
    )


class DisputeRelationship(Base):
    """
    Tracks explicit relationships between disputes:
      FORKED_FROM         — split out of an ongoing dispute thread
      SAME_CUSTOMER_BATCH — multiple disputes raised in a single email
      ESCALATION_OF       — formal escalation of another dispute
      RELATED             — general linkage (FA-created)
    """
    __tablename__ = "dispute_relationship"

    relationship_id   = Column(Integer, primary_key=True)
    source_dispute_id = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"), nullable=False)
    target_dispute_id = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"), nullable=False)
    relationship_type = Column(
        Enum("FORKED_FROM", "SAME_CUSTOMER_BATCH", "ESCALATION_OF", "RELATED", name="dispute_relationship_type"),
        nullable=False,
    )
    context_note = Column(Text,       nullable=True)
    created_by   = Column(String(20), nullable=False, default="SYSTEM")  # SYSTEM | FA
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    source_dispute = relationship("DisputeMaster", foreign_keys=[source_dispute_id], back_populates="relationships_as_source", lazy="joined")
    target_dispute = relationship("DisputeMaster", foreign_keys=[target_dispute_id], back_populates="relationships_as_target", lazy="joined")

    __table_args__ = (
        Index("ix_dispute_rel_source", "source_dispute_id"),
        Index("ix_dispute_rel_target", "target_dispute_id"),
        Index("ix_dispute_rel_type",   "relationship_type"),
    )


class DisputeAIAnalysis(Base):
    __tablename__ = "dispute_ai_analysis"

    analysis_id             = Column(Integer, primary_key=True)
    dispute_id              = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"), nullable=False)
    predicted_category      = Column(String(100), nullable=False)
    confidence_score        = Column(Numeric(5, 2), nullable=False)
    ai_summary              = Column(Text, nullable=False)
    ai_response             = Column(Text, nullable=True)
    auto_response_generated = Column(Boolean, default=False, server_default=text("FALSE"), nullable=False)
    created_at              = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    memory_context_used     = Column(Boolean, default=False, server_default=text("FALSE"), nullable=False)
    episodes_referenced     = Column(ARRAY(Integer), nullable=True)

    dispute         = relationship("DisputeMaster",         back_populates="ai_analyses", lazy="joined")
    supporting_refs = relationship("AnalysisSupportingRef", back_populates="ai_analysis", lazy="select", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_dispute_ai_analysis_dispute_id",         "dispute_id"),
        Index("ix_dispute_ai_analysis_predicted_category", "predicted_category"),
    )


class AnalysisSupportingRef(Base):
    __tablename__ = "analysis_supporting_refs"

    ref_id          = Column(Integer, primary_key=True)
    analysis_id     = Column(Integer, ForeignKey("dispute_ai_analysis.analysis_id", ondelete="CASCADE"), nullable=False)
    reference_table = Column(Text,    nullable=False)
    ref_id_value    = Column(Integer, nullable=False)
    context_note    = Column(Text,    nullable=False)

    ai_analysis = relationship("DisputeAIAnalysis", back_populates="supporting_refs", lazy="joined")

    __table_args__ = (
        Index("ix_analysis_refs_analysis_id",     "analysis_id"),
        Index("ix_analysis_refs_reference_table", "reference_table"),
    )


class DisputeAssignment(Base):
    __tablename__ = "dispute_assignment"

    assignment_id = Column(Integer, primary_key=True)
    dispute_id    = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"),  nullable=False)
    assigned_to   = Column(Integer, ForeignKey("users.user_id",             ondelete="RESTRICT"), nullable=False)
    assigned_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    status        = Column(String(50), nullable=False)

    dispute  = relationship("DisputeMaster", back_populates="assignments", lazy="joined")
    assignee = relationship("User",          back_populates="assignments", lazy="joined")

    __table_args__ = (
        Index("ix_assignment_dispute_id",  "dispute_id"),
        Index("ix_assignment_assigned_to", "assigned_to"),
        Index("ix_assignment_status",      "status"),
    )


class DisputeOpenQuestion(Base):
    __tablename__ = "dispute_open_questions"

    question_id            = Column(Integer, primary_key=True)
    dispute_id             = Column(Integer, ForeignKey("dispute_master.dispute_id",         ondelete="CASCADE"),  nullable=False)
    asked_in_episode_id    = Column(Integer, ForeignKey("dispute_memory_episode.episode_id", ondelete="SET NULL"), nullable=True)
    question_text          = Column(Text, nullable=False)
    status                 = Column(String(30), nullable=False, default="PENDING", server_default="PENDING")
    answered_in_episode_id = Column(Integer, ForeignKey("dispute_memory_episode.episode_id", ondelete="SET NULL"), nullable=True)
    answered_at            = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at             = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    dispute          = relationship("DisputeMaster",        back_populates="open_questions",                                               lazy="joined")
    asked_episode    = relationship("DisputeMemoryEpisode", foreign_keys=[asked_in_episode_id],    back_populates="open_questions_asked",    lazy="select")
    answered_episode = relationship("DisputeMemoryEpisode", foreign_keys=[answered_in_episode_id], back_populates="open_questions_answered", lazy="select")

    __table_args__ = (
        Index("ix_open_questions_dispute_id", "dispute_id"),
        Index("ix_open_questions_status",     "status"),
    )


class DisputeActivityLog(Base):
    __tablename__ = "dispute_activity_log"

    log_id       = Column(Integer, primary_key=True)
    dispute_id   = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"),  nullable=False)
    action_type  = Column(String(100), nullable=False)
    performed_by = Column(Integer, ForeignKey("users.user_id",             ondelete="SET NULL"), nullable=True)
    notes        = Column(Text, nullable=True)
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    dispute   = relationship("DisputeMaster", back_populates="activity_logs", lazy="joined")
    performer = relationship("User",          back_populates="activity_logs", lazy="select")

    __table_args__ = (
        Index("ix_activity_log_dispute_id",   "dispute_id"),
        Index("ix_activity_log_performed_by", "performed_by"),
        Index("ix_activity_log_created_at",   "created_at"),
    )


class DisputeStatusHistory(Base):
    __tablename__ = "dispute_status_history"

    log_id       = Column(Integer, primary_key=True)
    dispute_id   = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"),  nullable=False)
    action_type  = Column(String(100), nullable=False)
    performed_by = Column(Integer, ForeignKey("users.user_id",             ondelete="SET NULL"), nullable=True)
    notes        = Column(Text, nullable=True)
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    dispute   = relationship("DisputeMaster", back_populates="status_history", lazy="joined")
    performer = relationship("User",          back_populates="status_history", lazy="select")

    __table_args__ = (
        Index("ix_status_history_dispute_id",   "dispute_id"),
        Index("ix_status_history_performed_by", "performed_by"),
        Index("ix_status_history_created_at",   "created_at"),
    )


class DisputeNewMessage(Base):
    """
    One row per dispute. has_new_message=True means the customer sent a message
    that no FA or AI has responded to yet.
    Written by the agent (persist_results) when a CUSTOMER episode is saved.
    Cleared by PATCH /disputes/{id}/mark-read called from the frontend.
    """
    __tablename__ = "dispute_new_message"

    dispute_id      = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"), primary_key=True)
    has_new_message = Column(Boolean, nullable=False, default=True, server_default=text("TRUE"))
    arrived_at      = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at      = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    dispute = relationship("DisputeMaster", back_populates="new_message_flag", lazy="select")


class DisputeDocument(Base):
    """
    Supporting document manually uploaded by a Finance Associate.
    Separate from email_message_attachments (inbound email files)
    and outbound_email_attachments (FA reply files).
    """
    __tablename__ = "dispute_documents"

    document_id  = Column(Integer, primary_key=True)
    dispute_id   = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"),  nullable=False)
    uploaded_by  = Column(Integer, ForeignKey("users.user_id",             ondelete="RESTRICT"), nullable=False)
    file_name    = Column(String(255), nullable=False)
    file_type    = Column(String(100), nullable=False)
    file_size    = Column(BigInteger,  nullable=True)
    file_path    = Column(Text,        nullable=False)
    display_name = Column(String(255), nullable=True)
    notes        = Column(Text,        nullable=True)
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    dispute  = relationship("DisputeMaster", back_populates="supporting_documents", lazy="select")
    uploader = relationship("User",          lazy="joined", foreign_keys=[uploaded_by])

    __table_args__ = (
        Index("ix_dispute_documents_dispute_id",  "dispute_id"),
        Index("ix_dispute_documents_uploaded_by", "uploaded_by"),
        Index("ix_dispute_documents_created_at",  "created_at"),
    )
