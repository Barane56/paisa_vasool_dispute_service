# mailbox_models.py
"""
MailboxCredential     — admin-managed IMAP inbox configs
EmailInboxMessage     — every real email (inbound + outbound mirror)
EmailMessageAttachment— inbound attachment files on filesystem
FASmtpCredential      — per-FA SMTP credentials for sending on their behalf
OutboundEmail         — record of every email our system sent
OutboundEmailAttachment— files attached to outbound emails
"""
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, BigInteger,
    TIMESTAMP, ForeignKey, Index, UniqueConstraint, func, text,
)
from sqlalchemy.orm import relationship
from .base import Base


class MailboxCredential(Base):
    __tablename__ = "mailbox_credentials"

    mailbox_id     = Column(Integer, primary_key=True)
    label          = Column(String(100), nullable=False)
    email_address  = Column(String(150), nullable=False, unique=True)
    # ── IMAP (receive) ────────────────────────────────────────────────────────
    imap_host      = Column(String(255), nullable=False)
    imap_port      = Column(Integer,     nullable=False, default=993)
    use_ssl        = Column(Boolean,     nullable=False, default=True, server_default=text("TRUE"))
    password_enc   = Column(Text,        nullable=False)   # base64-encoded; same creds for SMTP
    # ── SMTP (send) ───────────────────────────────────────────────────────────
    smtp_host      = Column(String(255), nullable=True)
    smtp_port      = Column(Integer,     nullable=False, default=587)
    smtp_use_tls   = Column(Boolean,     nullable=False, default=True, server_default=text("TRUE"))
    # ── State ─────────────────────────────────────────────────────────────────
    is_active      = Column(Boolean,     nullable=False, default=True,  server_default=text("TRUE"))
    is_paused      = Column(Boolean,     nullable=False, default=False, server_default=text("FALSE"))
    last_polled_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_uid_seen  = Column(BigInteger,  nullable=True)
    created_at     = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at     = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    fetched_emails = relationship("EmailInboxMessage", back_populates="mailbox", lazy="select")

    __table_args__ = (
        Index("ix_mailbox_credentials_email_address", "email_address"),
        Index("ix_mailbox_credentials_is_active",     "is_active"),
        Index("ix_mailbox_credentials_is_paused",     "is_paused"),
    )

    @property
    def effective_smtp_host(self) -> str:
        """Returns smtp_host if set, otherwise derives from imap_host."""
        if self.smtp_host:
            return self.smtp_host
        # Common derivation: imap.X.com → smtp.X.com
        if self.imap_host.lower().startswith("imap."):
            return "smtp." + self.imap_host[5:]
        return self.imap_host


class EmailInboxMessage(Base):
    """
    Stores every real email fetched from IMAP.
    source = 'INBOUND'  → arrived in admin mailbox from a customer
    source = 'OUTBOUND' → sent by our system on behalf of an FA (mirror copy)
    """
    __tablename__ = "email_inbox_messages"

    message_id      = Column(Integer, primary_key=True)
    mailbox_id      = Column(Integer, ForeignKey("mailbox_credentials.mailbox_id", ondelete="SET NULL"), nullable=True)
    imap_uid        = Column(BigInteger, nullable=True)
    message_uid     = Column(String(255), nullable=True)      # Message-ID header value
    source          = Column(String(20),  nullable=False, default="INBOUND")
    direction       = Column(String(20),  nullable=False, default="INBOUND")

    sender_email    = Column(String(150), nullable=False)
    recipient_email = Column(String(150), nullable=True)
    subject         = Column(String(255), nullable=False)
    body_text       = Column(Text,        nullable=False)
    body_html       = Column(Text,        nullable=True)
    received_at     = Column(TIMESTAMP(timezone=True), nullable=False)
    has_attachment  = Column(Boolean, default=False, server_default=text("FALSE"), nullable=False)

    # RFC-2822 threading headers — used to match customer replies to disputes
    in_reply_to_header = Column(String(255), nullable=True)
    references_header  = Column(Text,        nullable=True)

    dispute_id      = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="SET NULL", use_alter=True, name="fk_email_msg_dispute_id"), nullable=True)
    email_inbox_id  = Column(Integer, ForeignKey("email_inbox.email_id", ondelete="SET NULL"), nullable=True)

    processing_status = Column(String(50), nullable=False, default="RECEIVED")
    failure_reason    = Column(Text, nullable=True)
    created_at        = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    mailbox     = relationship("MailboxCredential", back_populates="fetched_emails", lazy="select")
    attachments = relationship("EmailMessageAttachment", back_populates="message", lazy="select", cascade="all, delete-orphan")
    dispute     = relationship("DisputeMaster", foreign_keys=[dispute_id], lazy="select")
    email_inbox = relationship("EmailInbox",    foreign_keys=[email_inbox_id], lazy="select")

    __table_args__ = (
        Index("ix_email_inbox_messages_mailbox_id",   "mailbox_id"),
        Index("ix_email_inbox_messages_dispute_id",   "dispute_id"),
        Index("ix_email_inbox_messages_source",       "source"),
        Index("ix_email_inbox_messages_received_at",  "received_at"),
        Index("ix_email_inbox_messages_sender_email", "sender_email"),
        Index("ix_email_inbox_messages_in_reply_to",  "in_reply_to_header"),
        UniqueConstraint("mailbox_id", "imap_uid", name="uq_mailbox_imap_uid"),
    )


class EmailMessageAttachment(Base):
    """Attachment from an inbound email. File stored on local filesystem."""
    __tablename__ = "email_message_attachments"

    attachment_id  = Column(Integer, primary_key=True)
    message_id     = Column(Integer, ForeignKey("email_inbox_messages.message_id", ondelete="CASCADE"), nullable=False)
    file_name      = Column(String(255), nullable=False)
    file_type      = Column(String(50),  nullable=False)
    file_size      = Column(BigInteger,  nullable=True)
    file_path      = Column(Text,        nullable=False)
    extracted_text = Column(Text,        nullable=True)
    created_at     = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    message = relationship("EmailInboxMessage", back_populates="attachments", lazy="select")

    __table_args__ = (
        Index("ix_email_msg_attachments_message_id", "message_id"),
        Index("ix_email_msg_attachments_file_type",  "file_type"),
    )


class OutboundEmail(Base):
    """
    Every email sent by our system on behalf of an FA.
    Threading headers enable matching customer replies back to disputes.
    """
    __tablename__ = "outbound_emails"

    outbound_id        = Column(Integer, primary_key=True)
    dispute_id         = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"),  nullable=False)
    sent_by_user_id    = Column(Integer, ForeignKey("users.user_id",             ondelete="SET NULL"), nullable=True)
    from_email          = Column(String(150), nullable=False)
    to_email            = Column(String(150), nullable=False)
    subject            = Column(String(255), nullable=False)
    body_html          = Column(Text, nullable=False)
    body_text          = Column(Text, nullable=False)
    # RFC-2822 threading
    message_id_header  = Column(String(255), unique=True, nullable=True)
    in_reply_to_header = Column(String(255), nullable=True)
    references_header  = Column(Text,        nullable=True)
    sent_at            = Column(TIMESTAMP(timezone=True), nullable=True)
    status             = Column(String(30), nullable=False, default="PENDING")
    failure_reason     = Column(Text, nullable=True)
    created_at         = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    dispute     = relationship("DisputeMaster",            lazy="select")
    sender      = relationship("User",                     lazy="joined")
    attachments = relationship("OutboundEmailAttachment",  back_populates="email", lazy="select", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_outbound_emails_dispute_id",      "dispute_id"),
        Index("ix_outbound_emails_sent_by_user_id", "sent_by_user_id"),
        Index("ix_outbound_emails_message_id",      "message_id_header"),
        Index("ix_outbound_emails_in_reply_to",     "in_reply_to_header"),
        Index("ix_outbound_emails_status",          "status"),
    )


class OutboundEmailAttachment(Base):
    """Files attached to outbound emails by FA when composing a reply."""
    __tablename__ = "outbound_email_attachments"

    attachment_id = Column(Integer, primary_key=True)
    outbound_id   = Column(Integer, ForeignKey("outbound_emails.outbound_id", ondelete="CASCADE"), nullable=False)
    file_name     = Column(String(255), nullable=False)
    file_type     = Column(String(50),  nullable=False)
    file_size     = Column(BigInteger,  nullable=True)
    file_path     = Column(Text,        nullable=False)
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    email = relationship("OutboundEmail", back_populates="attachments", lazy="select")

    __table_args__ = (Index("ix_outbound_attachments_outbound_id", "outbound_id"),)
