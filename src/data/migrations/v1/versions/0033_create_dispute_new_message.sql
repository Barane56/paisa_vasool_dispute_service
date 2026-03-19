-- 0033_create_dispute_new_message.sql
-- ─────────────────────────────────────
-- Tracks whether a dispute has a new unread customer message.
-- Set to TRUE by the agent when a CUSTOMER episode is written.
-- Set to FALSE by the frontend/FA when they open the dispute.
--
-- One row per dispute, upserted by the agent — not a log table.

CREATE TABLE IF NOT EXISTS dispute_new_message (
    dispute_id   INTEGER      PRIMARY KEY
                              REFERENCES dispute_master(dispute_id) ON DELETE CASCADE,
    has_new_message BOOLEAN   NOT NULL DEFAULT TRUE,
    arrived_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),   -- when the latest customer message arrived
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_dispute_new_message_has_new
    ON dispute_new_message (has_new_message)
    WHERE has_new_message = TRUE;
