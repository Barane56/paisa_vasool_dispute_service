-- =============================================================================
-- 0000_bootstrap.sql
-- Creates the schema_migrations tracking table.
-- This must run before any other migration.
-- =============================================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(255) PRIMARY KEY,
    description TEXT         NOT NULL,
    applied_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);