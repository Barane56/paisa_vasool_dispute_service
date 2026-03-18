-- =============================================================================
-- 0005_create_dispute_type.sql
-- =============================================================================

CREATE TABLE dispute_type (
    dispute_type_id SERIAL       PRIMARY KEY,
    reason_name     VARCHAR(100) NOT NULL UNIQUE,
    description     TEXT         NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_dispute_type_reason_name ON dispute_type (reason_name);
CREATE INDEX ix_dispute_type_is_active   ON dispute_type (is_active);
