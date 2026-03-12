# invoice_models.py — InvoiceData, PaymentDetail, MatchingPaymentInvoice
from sqlalchemy import (
    Column, Integer, String, Text, Numeric,
    TIMESTAMP, JSON, ForeignKey, Index, func,
)
from sqlalchemy.orm import relationship
from .base import Base


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
