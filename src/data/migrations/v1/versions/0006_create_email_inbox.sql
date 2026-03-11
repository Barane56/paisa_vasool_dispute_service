-- =============================================================================
-- 0006_create_email_inbox.sql
-- NOTE: dispute_id FK is intentionally omitted here.
--       It is added in 0008_create_dispute_master.sql after dispute_master
--       is created, resolving the circular dependency.
-- =============================================================================

CREATE TABLE email_inbox (
    email_id           SERIAL       PRIMARY KEY,
    sender_email       VARCHAR(150) NOT NULL,
    subject            VARCHAR(255) NOT NULL,
    body_text          TEXT         NOT NULL,
    received_at        TIMESTAMPTZ  NOT NULL,
    has_attachment     BOOLEAN      NOT NULL DEFAULT FALSE,
    processing_status  VARCHAR(50)  NOT NULL,  -- RECEIVED | PROCESSED | FAILED
    failure_reason     TEXT,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    dispute_id         INT,                    -- FK added in 0008_create_dispute_master.sql
    routing_confidence NUMERIC(5,2)
);

CREATE INDEX ix_email_inbox_sender_email      ON email_inbox (sender_email);
CREATE INDEX ix_email_inbox_processing_status ON email_inbox (processing_status);
CREATE INDEX ix_email_inbox_received_at       ON email_inbox (received_at);
CREATE INDEX ix_email_inbox_dispute_id        ON email_inbox (dispute_id);