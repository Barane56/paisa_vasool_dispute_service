-- =============================================================================
-- 0017_create_dispute_status_history.sql
-- Depends on: 0001_create_users.sql
--             0008_create_dispute_master.sql
-- =============================================================================

CREATE TABLE dispute_status_history (
    log_id       SERIAL       PRIMARY KEY,
    dispute_id   INT          NOT NULL REFERENCES dispute_master (dispute_id) ON DELETE CASCADE,
    action_type  VARCHAR(100) NOT NULL,
    performed_by INT          REFERENCES users (user_id) ON DELETE SET NULL,
    notes        TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_status_history_dispute_id   ON dispute_status_history (dispute_id);
CREATE INDEX ix_status_history_performed_by ON dispute_status_history (performed_by);
CREATE INDEX ix_status_history_created_at   ON dispute_status_history (created_at);