-- =============================================================================
-- 0014_create_dispute_assignment.sql
-- Depends on: 0001_create_users.sql
--             0008_create_dispute_master.sql
-- =============================================================================

CREATE TABLE dispute_assignment (
    assignment_id SERIAL      PRIMARY KEY,
    dispute_id    INT         NOT NULL REFERENCES dispute_master (dispute_id) ON DELETE CASCADE,
    assigned_to   INT         NOT NULL REFERENCES users          (user_id)    ON DELETE RESTRICT,
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        VARCHAR(50) NOT NULL  -- ACTIVE | REASSIGNED | COMPLETED
);

CREATE INDEX ix_assignment_dispute_id  ON dispute_assignment (dispute_id);
CREATE INDEX ix_assignment_assigned_to ON dispute_assignment (assigned_to);
CREATE INDEX ix_assignment_status      ON dispute_assignment (status);