# email_models.py — EmailInbox, EmailAttachment
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Numeric,
    TIMESTAMP, ForeignKey, Index, func, text,
)
from sqlalchemy.orm import relationship
from .base import Base


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

    attachments = relationship("EmailAttachment",      back_populates="email",         lazy="select", cascade="all, delete-orphan")
    dispute     = relationship("DisputeMaster",        back_populates="routed_emails",  foreign_keys=[dispute_id], lazy="select")
    episodes    = relationship("DisputeMemoryEpisode", back_populates="source_email",  lazy="select")

    __table_args__ = (
        Index("ix_email_inbox_sender_email",      "sender_email"),
        Index("ix_email_inbox_processing_status", "processing_status"),
        Index("ix_email_inbox_received_at",       "received_at"),
        Index("ix_email_inbox_dispute_id",        "dispute_id"),
    )


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
