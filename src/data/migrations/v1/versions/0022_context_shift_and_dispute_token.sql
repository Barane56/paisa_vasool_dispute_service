-- =============================================================================
-- Migration V2: Context-Shift Detection & Dispute Token Support
-- =============================================================================
-- Applies to:  dispute_master, dispute_relationship
-- Depends on:  V1 (initial schema)
-- Safe to run: idempotent guards on every DDL statement
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. dispute_master — add dispute_token (Layer 1 cross-thread matching)
-- -----------------------------------------------------------------------------
ALTER TABLE dispute_master
    ADD COLUMN IF NOT EXISTS dispute_token VARCHAR(32) UNIQUE;

COMMENT ON COLUMN dispute_master.dispute_token IS
    'Unique human-readable token (e.g. DISP-00042) embedded in every outbound '
    'email so customers can reference it across threads and email addresses. '
    'Generated immediately after INSERT as DISP-{dispute_id:05d}.';

CREATE UNIQUE INDEX IF NOT EXISTS ix_dispute_master_dispute_token
    ON dispute_master (dispute_token)
    WHERE dispute_token IS NOT NULL;


-- -----------------------------------------------------------------------------
-- 2. dispute_master — add parent_dispute_id (context-shift fork link)
-- -----------------------------------------------------------------------------
ALTER TABLE dispute_master
    ADD COLUMN IF NOT EXISTS parent_dispute_id INTEGER
        REFERENCES dispute_master (dispute_id)
        ON DELETE SET NULL
        DEFERRABLE INITIALLY DEFERRED;

COMMENT ON COLUMN dispute_master.parent_dispute_id IS
    'Set when this dispute was automatically forked out of an ongoing conversation '
    'because the customer raised a new, distinct issue. Points to the original dispute.';

CREATE INDEX IF NOT EXISTS ix_dispute_master_parent_dispute_id
    ON dispute_master (parent_dispute_id)
    WHERE parent_dispute_id IS NOT NULL;


-- -----------------------------------------------------------------------------
-- 3. dispute_relationship — relationship type enum + table
-- -----------------------------------------------------------------------------

-- Create the enum type only if it does not already exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type WHERE typname = 'dispute_relationship_type'
    ) THEN
        CREATE TYPE dispute_relationship_type AS ENUM (
            'FORKED_FROM',         -- dispute was split out of an ongoing thread
            'SAME_CUSTOMER_BATCH', -- raised in the same email alongside other issues
            'ESCALATION_OF',       -- same issue but customer escalated
            'RELATED'              -- loosely related, tracked separately
        );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS dispute_relationship (
    relationship_id   SERIAL        PRIMARY KEY,
    source_dispute_id INTEGER       NOT NULL
        REFERENCES dispute_master (dispute_id) ON DELETE CASCADE,
    target_dispute_id INTEGER       NOT NULL
        REFERENCES dispute_master (dispute_id) ON DELETE CASCADE,
    relationship_type dispute_relationship_type NOT NULL,
    context_note      TEXT,                  -- LLM-generated explanation for audit trail
    created_by        VARCHAR(20)   NOT NULL DEFAULT 'SYSTEM',  -- SYSTEM | FA
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    -- Prevent duplicate relationships in both directions
    CONSTRAINT uq_dispute_relationship UNIQUE (source_dispute_id, target_dispute_id),
    -- Prevent a dispute from relating to itself
    CONSTRAINT chk_no_self_relationship CHECK (source_dispute_id <> target_dispute_id)
);

COMMENT ON TABLE dispute_relationship IS
    'Explicit directed relationships between disputes. '
    'A FORKED_FROM row means source was created because the conversation on target shifted topic. '
    'Query both source_dispute_id and target_dispute_id to find all related disputes.';

CREATE INDEX IF NOT EXISTS ix_dispute_rel_source
    ON dispute_relationship (source_dispute_id);

CREATE INDEX IF NOT EXISTS ix_dispute_rel_target
    ON dispute_relationship (target_dispute_id);

CREATE INDEX IF NOT EXISTS ix_dispute_rel_type
    ON dispute_relationship (relationship_type);


-- -----------------------------------------------------------------------------
-- 4. dispute_activity_log — ensure CONTEXT_SHIFT action types are documented
-- -----------------------------------------------------------------------------
-- (No DDL change needed — action_type is VARCHAR(100), all values accepted.
--  Values introduced by this migration:
--    CONTEXT_SHIFT_DETECTED  — logged on parent when AI detects a shift
--    CONTEXT_SHIFT_FORK      — logged on parent when a fork is created
--    FORKED_FROM_DISPUTE     — logged on the new forked dispute
-- )


-- -----------------------------------------------------------------------------
-- 5. Back-fill dispute_token for any existing disputes that lack one
-- -----------------------------------------------------------------------------
UPDATE dispute_master
SET    dispute_token = 'DISP-' || LPAD(dispute_id::TEXT, 5, '0')
WHERE  dispute_token IS NULL;
