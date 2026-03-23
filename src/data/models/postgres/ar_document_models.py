"""
src/data/models/postgres/ar_document_models.py
===============================================
SQLAlchemy ORM models for the AR document graph.
"""
from __future__ import annotations
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, Date,
    ForeignKey, TIMESTAMP, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from src.data.models.postgres.base import Base


class ARDocument(Base):
    __tablename__ = "ar_documents"

    doc_id         = Column(Integer, primary_key=True)
    customer_scope = Column(String(255), nullable=False)
    doc_type       = Column(String(20),  nullable=False)   # PO|INVOICE|GRN|PAYMENT|CONTRACT|CREDIT_NOTE
    doc_date       = Column(Date,        nullable=True)
    status         = Column(String(20),  nullable=False, default="ACTIVE")
    file_path      = Column(Text,        nullable=True)
    raw_text       = Column(Text,        nullable=True)
    uploaded_by    = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    created_at     = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    keys            = relationship("ARDocumentKey",    back_populates="document",
                                   cascade="all, delete-orphan", lazy="select")
    uploader        = relationship("User",             foreign_keys=[uploaded_by], lazy="joined")
    dispute_links   = relationship("DisputeARDocument", back_populates="document",
                                   cascade="all, delete-orphan", lazy="select")

    __table_args__ = (
        Index("ix_ar_docs_scope",   "customer_scope"),
        Index("ix_ar_docs_type",    "doc_type"),
        Index("ix_ar_docs_created", "created_at"),
    )


class ARDocumentKey(Base):
    __tablename__ = "ar_document_keys"

    key_id         = Column(Integer, primary_key=True)
    doc_id         = Column(Integer, ForeignKey("ar_documents.doc_id", ondelete="CASCADE"), nullable=False)
    key_type       = Column(String(50),  nullable=False)
    key_value_raw  = Column(Text,        nullable=False)
    key_value_norm = Column(Text,        nullable=False)
    confidence     = Column(Float,       nullable=False, default=1.0)
    source         = Column(String(10),  nullable=False, default="regex")  # regex|llm|manual
    verified       = Column(Boolean,     nullable=False, default=False)
    created_at     = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    document = relationship("ARDocument", back_populates="keys", lazy="select")

    __table_args__ = (
        UniqueConstraint("doc_id", "key_type", "key_value_norm", name="uq_doc_key"),
        Index("ix_ark_type_norm", "key_type", "key_value_norm"),
        Index("ix_ark_doc_id",    "doc_id"),
    )


class DisputeARDocument(Base):
    """
    Link table — tracks which AR documents (graph nodes) are attached to a dispute.
    Populated by the email pipeline (via ar_document_chain) and manual FA case creation.
    This is what the 'Docs' tab queries — only shows docs relevant to this specific dispute,
    not all documents for the customer.
    """
    __tablename__ = "dispute_ar_documents"

    id           = Column(Integer, primary_key=True)
    dispute_id   = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"),  nullable=False)
    doc_id       = Column(Integer, ForeignKey("ar_documents.doc_id",       ondelete="CASCADE"),  nullable=False)
    linked_by    = Column(Integer, ForeignKey("users.user_id",             ondelete="SET NULL"), nullable=True)   # NULL = agent-linked
    context_note = Column(Text,    nullable=True)   # e.g. "anchor document" / "graph chain: inv+po match"
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    dispute  = relationship("DisputeMaster", foreign_keys=[dispute_id], lazy="select")
    document = relationship("ARDocument",    foreign_keys=[doc_id],     back_populates="dispute_links", lazy="joined")

    __table_args__ = (
        UniqueConstraint("dispute_id", "doc_id", name="uq_dispute_ar_doc"),
        Index("ix_dispute_ar_doc_dispute_id", "dispute_id"),
        Index("ix_dispute_ar_doc_doc_id",     "doc_id"),
    )
