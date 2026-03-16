-- =============================================================================
-- 0025_create_mailbox_credentials.sql
-- Idempotent: safe to re-run, skips everything that already exists.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mailbox_credentials (
    mailbox_id      SERIAL       PRIMARY KEY,
    label           VARCHAR(100) NOT NULL,
    email_address   VARCHAR(150) NOT NULL UNIQUE,
    imap_host       VARCHAR(255) NOT NULL,
    imap_port       INTEGER      NOT NULL DEFAULT 993,
    use_ssl         BOOLEAN      NOT NULL DEFAULT TRUE,
    password_enc    TEXT         NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    is_paused       BOOLEAN      NOT NULL DEFAULT FALSE,
    last_polled_at  TIMESTAMPTZ,
    last_uid_seen   BIGINT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_mailbox_credentials_email_address ON mailbox_credentials (email_address);
CREATE INDEX IF NOT EXISTS ix_mailbox_credentials_is_active     ON mailbox_credentials (is_active);
CREATE INDEX IF NOT EXISTS ix_mailbox_credentials_is_paused     ON mailbox_credentials (is_paused);
