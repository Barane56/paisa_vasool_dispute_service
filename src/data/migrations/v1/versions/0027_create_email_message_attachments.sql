-- =============================================================================
-- 0027_create_email_message_attachments.sql
-- Idempotent: safe to re-run, skips everything that already exists.
-- =============================================================================

CREATE TABLE IF NOT EXISTS email_message_attachments (
    attachment_id  SERIAL       PRIMARY KEY,
    message_id     INTEGER      NOT NULL REFERENCES email_inbox_messages (message_id) ON DELETE CASCADE,
    file_name      VARCHAR(255) NOT NULL,
    file_type      VARCHAR(50)  NOT NULL,
    file_size      BIGINT,
    file_path      TEXT         NOT NULL,
    extracted_text TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_email_msg_attachments_message_id ON email_message_attachments (message_id);
CREATE INDEX IF NOT EXISTS ix_email_msg_attachments_file_type  ON email_message_attachments (file_type);
