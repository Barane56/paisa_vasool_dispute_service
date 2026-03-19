-- =============================================================================
-- 0030_create_outbound_email_attachments.sql
-- Idempotent: safe to re-run, skips everything that already exists.
-- Files attached to outbound emails by the FA.
-- =============================================================================

CREATE TABLE IF NOT EXISTS outbound_email_attachments (
    attachment_id  SERIAL       PRIMARY KEY,
    outbound_id    INTEGER      NOT NULL REFERENCES outbound_emails (outbound_id) ON DELETE CASCADE,
    file_name      VARCHAR(255) NOT NULL,
    file_type      VARCHAR(50)  NOT NULL,
    file_size      BIGINT,
    file_path      TEXT         NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_outbound_attachments_outbound_id ON outbound_email_attachments (outbound_id);
