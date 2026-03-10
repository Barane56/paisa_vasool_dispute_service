from sqlalchemy import (
    Column, Enum, Integer, String, Text, Boolean, Numeric,
    TIMESTAMP, JSON, ForeignKey, Index, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Users  (read-only from dispute service perspective — managed by auth service)
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    user_id       = Column(Integer, primary_key=True, nullable=False)
    name          = Column(String(100), nullable=False)
    email         = Column(String(150), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    assignments    = relationship("DisputeAssignment",    back_populates="assignee",  lazy="select")
    activity_logs  = relationship("DisputeActivityLog",   back_populates="performer", lazy="select")
    status_history = relationship("DisputeStatusHistory", back_populates="performer", lazy="select")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_users_email", "email"),)

    def __repr__(self) -> str:
        return f"<User id={self.user_id} email={self.email}>"


# ---------------------------------------------------------------------------
# Refresh Token  (managed by auth service; read-only here)
# ---------------------------------------------------------------------------
class RefreshToken(Base):
    __tablename__ = "refresh_token"

    token_id      = Column(Integer, primary_key=True)
    user_id       = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    jti           = Column(Text, unique=True, nullable=False)
    refresh_token = Column(Text, unique=True, nullable=False)
    is_revoked    = Column(Boolean, nullable=False, default=False, server_default=text("FALSE"))
    expires_at    = Column(TIMESTAMP(timezone=True), nullable=False)
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="refresh_tokens")

    __table_args__ = (
        Index("ix_refresh_token_token",      "refresh_token"),
        Index("ix_refresh_token_jti",        "jti"),
        Index("ix_refresh_token_user_id",    "user_id"),
        Index("ix_refresh_token_expires_at", "expires_at"),
    )


# ---------------------------------------------------------------------------
# Invoice Data
# ---------------------------------------------------------------------------
class InvoiceData(Base):
    __tablename__ = "invoice_data"

    invoice_id      = Column(Integer, primary_key=True)
    invoice_number  = Column(String(100), nullable=False)
    invoice_url     = Column(Text, unique=True, nullable=False)
    invoice_details = Column(JSON, nullable=False)
    updated_at      = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    payment_matches = relationship("MatchingPaymentInvoice", back_populates="invoice", lazy="select")
    disputes        = relationship("DisputeMaster",          back_populates="invoice", lazy="select")

    __table_args__ = (Index("ix_invoice_data_invoice_number", "invoice_number"),)


# ---------------------------------------------------------------------------
# Payment Detail
# ---------------------------------------------------------------------------
class PaymentDetail(Base):
    __tablename__ = "payment_detail"

    payment_detail_id = Column(Integer, primary_key=True)
    customer_id       = Column(String(100), nullable=False)
    invoice_number    = Column(String(100), nullable=False)
    payment_url       = Column(Text, unique=True, nullable=False)
    payment_details   = Column(JSON, nullable=True)

    payment_matches = relationship("MatchingPaymentInvoice", back_populates="payment",        lazy="select")
    disputes        = relationship("DisputeMaster",          back_populates="payment_detail", lazy="select")

    __table_args__ = (
        Index("ix_payment_detail_customer_id",    "customer_id"),
        Index("ix_payment_detail_invoice_number", "invoice_number"),
    )


# ---------------------------------------------------------------------------
# Matching Payment Invoice
# ---------------------------------------------------------------------------
class MatchingPaymentInvoice(Base):
    __tablename__ = "matching_payment_invoice"

    match_id          = Column(Integer, primary_key=True)
    payment_detail_id = Column(Integer, ForeignKey("payment_detail.payment_detail_id", ondelete="RESTRICT"), nullable=False)
    invoice_id        = Column(Integer, ForeignKey("invoice_data.invoice_id",          ondelete="RESTRICT"), nullable=False)
    matched_amount    = Column(Numeric(12, 2), nullable=False)
    match_score       = Column(Numeric(5, 2),  nullable=False)
    match_status      = Column(String(50),     nullable=False)  # FULL / PARTIAL / FAILED
    created_at        = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    payment = relationship("PaymentDetail", back_populates="payment_matches", lazy="joined")
    invoice = relationship("InvoiceData",   back_populates="payment_matches", lazy="joined")

    __table_args__ = (
        Index("ix_match_payment_detail_id", "payment_detail_id"),
        Index("ix_match_invoice_id",        "invoice_id"),
        Index("ix_match_status",            "match_status"),
    )


# ---------------------------------------------------------------------------
# Email Inbox
# ---------------------------------------------------------------------------
class EmailInbox(Base):
    __tablename__ = "email_inbox"

    email_id           = Column(Integer, primary_key=True)
    sender_email       = Column(String(150), nullable=False)
    subject            = Column(String(255), nullable=False)
    body_text          = Column(Text,        nullable=False)
    received_at        = Column(TIMESTAMP(timezone=True), nullable=False)
    has_attachment     = Column(Boolean, default=False, server_default=text("FALSE"), nullable=False)
    processing_status  = Column(String(50), nullable=False)
    failure_reason     = Column(Text, nullable=True)
    created_at         = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    dispute_id         = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="SET NULL", use_alter=True, name="fk_email_inbox_dispute_id"), nullable=True)
    routing_confidence = Column(Numeric(5, 2), nullable=True)

    attachments  = relationship("EmailAttachment",      back_populates="email",        lazy="select", cascade="all, delete-orphan")
    dispute      = relationship("DisputeMaster",        back_populates="routed_emails", foreign_keys=[dispute_id], lazy="select")
    episodes     = relationship("DisputeMemoryEpisode", back_populates="source_email", lazy="select")

    __table_args__ = (
        Index("ix_email_inbox_sender_email",      "sender_email"),
        Index("ix_email_inbox_processing_status", "processing_status"),
        Index("ix_email_inbox_received_at",       "received_at"),
        Index("ix_email_inbox_dispute_id",        "dispute_id"),
    )


# ---------------------------------------------------------------------------
# Email Attachments
# ---------------------------------------------------------------------------
class EmailAttachment(Base):
    __tablename__ = "email_attachments"

    attachment_id  = Column(Integer, primary_key=True)
    email_id       = Column(Integer, ForeignKey("email_inbox.email_id", ondelete="CASCADE"), nullable=False)
    file_name      = Column(String(255), nullable=False)
    file_type      = Column(String(20),  nullable=False)
    extracted_text = Column(Text,        nullable=False)
    uploaded_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    email = relationship("EmailInbox", back_populates="attachments", lazy="joined")

    __table_args__ = (
        Index("ix_email_attachments_email_id",  "email_id"),
        Index("ix_email_attachments_file_type", "file_type"),
    )


# ---------------------------------------------------------------------------
# Dispute Type
# ---------------------------------------------------------------------------


# In src/data/models/postgres/models.py

from sqlalchemy import Column, Integer, String, Text, Boolean, Enum as SQLEnum
from src.constants.enums import SeverityLevel  # Import the enum

class DisputeType(Base):
    __tablename__ = "dispute_type"

    dispute_type_id = Column(Integer, primary_key=True)
    reason_name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    severity_level = Column(
        SQLEnum(SeverityLevel, name='severity_level_enum', create_constraint=True),
        nullable=True,  # Change to False after backfill if you want it required
        index=True
    )
    is_active = Column(Boolean, default=True, server_default=text("TRUE"), nullable=False)

    disputes = relationship("DisputeMaster", back_populates="dispute_type", lazy="select")

    __table_args__ = (
        Index("ix_dispute_type_reason_name", "reason_name"),
        Index("ix_dispute_type_is_active", "is_active"),
        Index("ix_dispute_type_severity_level", "severity_level"),
    )

    def __repr__(self) -> str:
        return f"<DisputeType id={self.dispute_type_id} name={self.reason_name} severity={self.severity_level}>"

# ---------------------------------------------------------------------------
# Dispute Master
# ---------------------------------------------------------------------------
class DisputeMaster(Base):
    __tablename__ = "dispute_master"

    dispute_id        = Column(Integer, primary_key=True)
    email_id          = Column(Integer, ForeignKey("email_inbox.email_id",             ondelete="RESTRICT"), nullable=False)
    invoice_id        = Column(Integer, ForeignKey("invoice_data.invoice_id",          ondelete="SET NULL"), nullable=True)
    payment_detail_id = Column(Integer, ForeignKey("payment_detail.payment_detail_id", ondelete="SET NULL"), nullable=True)
    customer_id       = Column(String(100), nullable=False)
    dispute_type_id   = Column(Integer, ForeignKey("dispute_type.dispute_type_id",     ondelete="RESTRICT"), nullable=False)
    status            = Column(String(50), nullable=False)
    priority          = Column(String(20), nullable=False, default="MEDIUM", server_default="MEDIUM")
    description       = Column(Text, nullable=False)
    dispute_token     = Column(String(32), unique=True, nullable=True)  # e.g. DISP-00042 for cross-thread matching
    # Self-referential FK: set when this dispute was forked out of an ongoing conversation
    parent_dispute_id = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="SET NULL", use_alter=True, name="fk_dispute_master_parent_id"), nullable=True)
    created_at        = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at        = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    source_email   = relationship("EmailInbox",    foreign_keys=[email_id],                   lazy="joined")
    routed_emails  = relationship("EmailInbox",    foreign_keys="EmailInbox.dispute_id",       back_populates="dispute", lazy="select")
    invoice        = relationship("InvoiceData",   back_populates="disputes",                  lazy="joined")
    payment_detail = relationship("PaymentDetail", back_populates="disputes",                  lazy="joined")
    dispute_type   = relationship("DisputeType",   back_populates="disputes",                  lazy="joined")
    ai_analyses    = relationship("DisputeAIAnalysis",    back_populates="dispute",            lazy="select", cascade="all, delete-orphan")
    episodes       = relationship("DisputeMemoryEpisode", back_populates="dispute",            lazy="select", cascade="all, delete-orphan", order_by="DisputeMemoryEpisode.created_at")
    memory_summary = relationship("DisputeMemorySummary", back_populates="dispute",            lazy="select", uselist=False, cascade="all, delete-orphan")
    assignments    = relationship("DisputeAssignment",    back_populates="dispute",            lazy="select", cascade="all, delete-orphan")
    open_questions = relationship("DisputeOpenQuestion",  back_populates="dispute",            lazy="select", cascade="all, delete-orphan")
    activity_logs  = relationship("DisputeActivityLog",   back_populates="dispute",            lazy="select", cascade="all, delete-orphan")
    status_history = relationship("DisputeStatusHistory", back_populates="dispute",            lazy="select", cascade="all, delete-orphan")
    # Relationships for context-shift forking
    forked_disputes = relationship("DisputeMaster",        foreign_keys="DisputeMaster.parent_dispute_id", lazy="select", back_populates="parent_dispute")
    parent_dispute  = relationship("DisputeMaster",        foreign_keys=[parent_dispute_id],               lazy="select", back_populates="forked_disputes", remote_side="DisputeMaster.dispute_id")
    relationships_as_source = relationship("DisputeRelationship", foreign_keys="DisputeRelationship.source_dispute_id", back_populates="source_dispute", lazy="select", cascade="all, delete-orphan")
    relationships_as_target = relationship("DisputeRelationship", foreign_keys="DisputeRelationship.target_dispute_id", back_populates="target_dispute", lazy="select", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_dispute_master_customer_id",     "customer_id"),
        Index("ix_dispute_master_status",          "status"),
        Index("ix_dispute_master_priority",        "priority"),
        Index("ix_dispute_master_dispute_type_id", "dispute_type_id"),
        Index("ix_dispute_master_created_at",      "created_at"),
        Index("ix_dispute_master_parent_dispute_id", "parent_dispute_id"),
    )


# ---------------------------------------------------------------------------
# Dispute AI Analysis
# ---------------------------------------------------------------------------
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

    dispute         = relationship("DisputeMaster",         back_populates="ai_analyses",  lazy="joined")
    supporting_refs = relationship("AnalysisSupportingRef", back_populates="ai_analysis",  lazy="select", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_dispute_ai_analysis_dispute_id",         "dispute_id"),
        Index("ix_dispute_ai_analysis_predicted_category", "predicted_category"),
    )


# ---------------------------------------------------------------------------
# Analysis Supporting Refs
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Dispute Memory Episode
# ---------------------------------------------------------------------------
class DisputeMemoryEpisode(Base):
    __tablename__ = "dispute_memory_episode"

    episode_id        = Column(Integer, primary_key=True)
    dispute_id        = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"),  nullable=False)
    episode_type      = Column(String(50), nullable=False)
    actor             = Column(String(50), nullable=False)
    content_text      = Column(Text, nullable=False)
    content_embedding = Column(VECTOR(384), nullable=True)
    email_id          = Column(Integer, ForeignKey("email_inbox.email_id", ondelete="SET NULL"), nullable=True)
    created_at        = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    dispute      = relationship("DisputeMaster", back_populates="episodes",    lazy="joined")
    source_email = relationship("EmailInbox",    back_populates="episodes",    lazy="select")
    open_questions_asked = relationship(
        "DisputeOpenQuestion", back_populates="asked_episode",
        foreign_keys="DisputeOpenQuestion.asked_in_episode_id", lazy="select",
    )
    open_questions_answered = relationship(
        "DisputeOpenQuestion", back_populates="answered_episode",
        foreign_keys="DisputeOpenQuestion.answered_in_episode_id", lazy="select",
    )
    memory_summaries = relationship("DisputeMemorySummary", back_populates="covered_up_to_episode", lazy="select")

    __table_args__ = (
        Index("ix_episode_dispute_id",   "dispute_id"),
        Index("ix_episode_episode_type", "episode_type"),
        Index("ix_episode_created_at",   "created_at"),
    )


# ---------------------------------------------------------------------------
# Dispute Memory Summary
# ---------------------------------------------------------------------------
class DisputeMemorySummary(Base):
    __tablename__ = "dispute_memory_summary"

    summary_id               = Column(Integer, primary_key=True)
    dispute_id               = Column(Integer, ForeignKey("dispute_master.dispute_id",         ondelete="CASCADE"),  unique=True, nullable=False)
    summary_text             = Column(Text, nullable=False)
    covered_up_to_episode_id = Column(Integer, ForeignKey("dispute_memory_episode.episode_id", ondelete="SET NULL"), nullable=True)
    version                  = Column(Integer, default=1, server_default=text("1"), nullable=False)
    updated_at               = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    dispute               = relationship("DisputeMaster",        back_populates="memory_summary",      lazy="joined")
    covered_up_to_episode = relationship("DisputeMemoryEpisode", back_populates="memory_summaries",    lazy="select")

    __table_args__ = (Index("ix_memory_summary_dispute_id", "dispute_id"),)


# ---------------------------------------------------------------------------
# Dispute Assignment
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Dispute Open Questions
# ---------------------------------------------------------------------------
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

    dispute          = relationship("DisputeMaster",        back_populates="open_questions",          lazy="joined")
    asked_episode    = relationship("DisputeMemoryEpisode", foreign_keys=[asked_in_episode_id],    back_populates="open_questions_asked",    lazy="select")
    answered_episode = relationship("DisputeMemoryEpisode", foreign_keys=[answered_in_episode_id], back_populates="open_questions_answered", lazy="select")

    __table_args__ = (
        Index("ix_open_questions_dispute_id", "dispute_id"),
        Index("ix_open_questions_status",     "status"),
    )


# ---------------------------------------------------------------------------
# Dispute Activity Log
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Dispute Status History
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Dispute Relationship
# ---------------------------------------------------------------------------
# Tracks explicit relationships between disputes created during a conversation:
#   FORKED_FROM      — this dispute was split out of an ongoing dispute thread
#   SAME_CUSTOMER_BATCH — multiple disputes raised in a single email
#   ESCALATION_OF    — this dispute is a formal escalation of another
#   RELATED          — general linkage (FA-created)
# ---------------------------------------------------------------------------
class DisputeRelationship(Base):
    __tablename__ = "dispute_relationship"

    relationship_id   = Column(Integer, primary_key=True)
    source_dispute_id = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"),  nullable=False)
    target_dispute_id = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"),  nullable=False)
    relationship_type = Column(
        Enum("FORKED_FROM", "SAME_CUSTOMER_BATCH", "ESCALATION_OF", "RELATED",
             name="dispute_relationship_type"),
        nullable=False,
    )
    context_note      = Column(Text, nullable=True)   # LLM-generated reason for the fork
    created_by        = Column(String(20), nullable=False, default="SYSTEM")  # SYSTEM | FA
    created_at        = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    source_dispute = relationship("DisputeMaster", foreign_keys=[source_dispute_id], back_populates="relationships_as_source", lazy="joined")
    target_dispute = relationship("DisputeMaster", foreign_keys=[target_dispute_id], back_populates="relationships_as_target", lazy="joined")

    __table_args__ = (
        Index("ix_dispute_rel_source", "source_dispute_id"),
        Index("ix_dispute_rel_target", "target_dispute_id"),
        Index("ix_dispute_rel_type",   "relationship_type"),
    )
