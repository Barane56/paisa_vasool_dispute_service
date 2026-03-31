-- =============================================================================
-- 0016_create_dispute_activity_log.sql
-- Depends on: 0001_create_users.sql
--             0008_create_dispute_master.sql
-- =============================================================================

CREATE TABLE dispute_activity_log (
    log_id       SERIAL       PRIMARY KEY,
    dispute_id   INT          NOT NULL REFERENCES dispute_master (dispute_id) ON DELETE CASCADE,
    action_type  VARCHAR(100) NOT NULL,
    performed_by INT          REFERENCES users (user_id) ON DELETE SET NULL,
    notes        TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_activity_log_dispute_id   ON dispute_activity_log (dispute_id);
CREATE INDEX ix_activity_log_performed_by ON dispute_activity_log (performed_by);
CREATE INDEX ix_activity_log_created_at   ON dispute_activity_log (created_at);
