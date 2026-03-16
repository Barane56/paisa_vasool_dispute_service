-- =============================================================================
-- 0028_add_smtp_to_mailbox_credentials.sql
-- Idempotent: ADD COLUMN IF NOT EXISTS handles re-runs safely.
-- =============================================================================

ALTER TABLE mailbox_credentials
    ADD COLUMN IF NOT EXISTS smtp_host    VARCHAR(255),
    ADD COLUMN IF NOT EXISTS smtp_port    INTEGER NOT NULL DEFAULT 587,
    ADD COLUMN IF NOT EXISTS smtp_use_tls BOOLEAN NOT NULL DEFAULT TRUE;
