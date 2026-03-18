-- =============================================================================
-- 0026_create_email_inbox_messages.sql
-- Idempotent: safe to re-run, skips everything that already exists.
-- =============================================================================

CREATE TABLE IF NOT EXISTS email_inbox_messages (
    message_id        SERIAL       PRIMARY KEY,
    mailbox_id        INTEGER      REFERENCES mailbox_credentials (mailbox_id) ON DELETE SET NULL,
    imap_uid          BIGINT,
    message_uid       VARCHAR(255),
    source            VARCHAR(20)  NOT NULL DEFAULT 'INBOUND',
    direction         VARCHAR(20)  NOT NULL DEFAULT 'INBOUND',
    sender_email      VARCHAR(150) NOT NULL,
    recipient_email   VARCHAR(150),
    subject           VARCHAR(255) NOT NULL,
    body_text         TEXT         NOT NULL,
    body_html         TEXT,
    received_at       TIMESTAMPTZ  NOT NULL,
    has_attachment    BOOLEAN      NOT NULL DEFAULT FALSE,
    in_reply_to_header VARCHAR(255),
    references_header  TEXT,
    dispute_id        INTEGER,
    email_inbox_id    INTEGER      REFERENCES email_inbox (email_id) ON DELETE SET NULL,
    processing_status VARCHAR(50)  NOT NULL DEFAULT 'RECEIVED',
    failure_reason    TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_mailbox_imap_uid UNIQUE (mailbox_id, imap_uid)
);

CREATE INDEX IF NOT EXISTS ix_email_inbox_messages_mailbox_id   ON email_inbox_messages (mailbox_id);
CREATE INDEX IF NOT EXISTS ix_email_inbox_messages_dispute_id   ON email_inbox_messages (dispute_id);
CREATE INDEX IF NOT EXISTS ix_email_inbox_messages_source       ON email_inbox_messages (source);
CREATE INDEX IF NOT EXISTS ix_email_inbox_messages_received_at  ON email_inbox_messages (received_at);
CREATE INDEX IF NOT EXISTS ix_email_inbox_messages_sender_email ON email_inbox_messages (sender_email);
CREATE INDEX IF NOT EXISTS ix_email_inbox_messages_in_reply_to  ON email_inbox_messages (in_reply_to_header);

-- Dispute FK (deferred so it works regardless of table creation order)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_email_msg_dispute_id'
    ) THEN
        ALTER TABLE email_inbox_messages
            ADD CONSTRAINT fk_email_msg_dispute_id
            FOREIGN KEY (dispute_id)
            REFERENCES dispute_master (dispute_id)
            ON DELETE SET NULL
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;
