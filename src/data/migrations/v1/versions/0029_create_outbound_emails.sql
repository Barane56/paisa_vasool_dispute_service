-- =============================================================================
-- 0029_create_outbound_emails.sql
-- Idempotent: safe to re-run, skips everything that already exists.
-- Tracks every email our system sends on behalf of an FA.
-- Stores Message-ID / References headers for full thread tracking.
-- =============================================================================

CREATE TABLE IF NOT EXISTS outbound_emails (
    outbound_id         SERIAL       PRIMARY KEY,
    dispute_id          INTEGER      NOT NULL REFERENCES dispute_master (dispute_id) ON DELETE CASCADE,
    sent_by_user_id     INTEGER      NOT NULL REFERENCES users (user_id) ON DELETE RESTRICT,
    from_email          VARCHAR(150) NOT NULL,
    to_email            VARCHAR(150) NOT NULL,
    subject             VARCHAR(255) NOT NULL,
    body_html           TEXT         NOT NULL,
    body_text           TEXT         NOT NULL,
    message_id_header   VARCHAR(255) UNIQUE,
    in_reply_to_header  VARCHAR(255),
    references_header   TEXT,
    sent_at             TIMESTAMPTZ,
    status              VARCHAR(30)  NOT NULL DEFAULT 'PENDING',
    failure_reason      TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_outbound_emails_dispute_id       ON outbound_emails (dispute_id);
CREATE INDEX IF NOT EXISTS ix_outbound_emails_sent_by_user_id  ON outbound_emails (sent_by_user_id);
CREATE INDEX IF NOT EXISTS ix_outbound_emails_message_id       ON outbound_emails (message_id_header);
CREATE INDEX IF NOT EXISTS ix_outbound_emails_in_reply_to      ON outbound_emails (in_reply_to_header);
CREATE INDEX IF NOT EXISTS ix_outbound_emails_status           ON outbound_emails (status);
