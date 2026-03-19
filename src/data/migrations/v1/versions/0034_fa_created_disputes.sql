-- 0034_fa_created_disputes.sql
-- ─────────────────────────────
-- Allows FA to create disputes manually (not triggered by an inbound email).
-- Makes email_id nullable and adds a source column to distinguish origin.

-- Make email_id nullable — FA-created disputes have no source email
ALTER TABLE dispute_master
    ALTER COLUMN email_id DROP NOT NULL;

-- Track how the dispute was created
ALTER TABLE dispute_master
    ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'EMAIL';
-- EMAIL     = created by the agent from an inbound customer email (existing behaviour)
-- FA_MANUAL = created manually by a Finance Associate

COMMENT ON COLUMN dispute_master.source IS 'EMAIL | FA_MANUAL';

CREATE INDEX IF NOT EXISTS ix_dispute_master_source ON dispute_master (source);
