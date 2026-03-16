-- =============================================================================
-- 0031_add_threading_to_email_inbox_messages.sql
-- Idempotent: safe to re-run, skips everything that already exists.
-- NOTE: If running 0026 fresh, these columns are already included there.
--       This migration exists only for databases that ran the old 0026.
-- =============================================================================

ALTER TABLE email_inbox_messages
    ADD COLUMN IF NOT EXISTS in_reply_to_header VARCHAR(255),
    ADD COLUMN IF NOT EXISTS references_header  TEXT;

CREATE INDEX IF NOT EXISTS ix_email_inbox_messages_in_reply_to
    ON email_inbox_messages (in_reply_to_header);
