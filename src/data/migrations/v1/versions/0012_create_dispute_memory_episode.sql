-- =============================================================================
-- 0012_create_dispute_memory_episode.sql
-- Depends on: 0008_create_dispute_master.sql
--             0006_create_email_inbox.sql
-- =============================================================================

-- Requires pgvector extension for the VECTOR column
-- enable pgvector
-- CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE dispute_memory_episode (
    episode_id        SERIAL      PRIMARY KEY,
    dispute_id        INT         NOT NULL REFERENCES dispute_master (dispute_id) ON DELETE CASCADE,
    episode_type      VARCHAR(50) NOT NULL,
    -- CUSTOMER_EMAIL | AI_RESPONSE | ASSOCIATE_RESPONSE | ATTACHMENT_PARSED | STATUS_CHANGE | CLARIFICATION_ASKED
    actor             VARCHAR(50) NOT NULL,  -- CUSTOMER | AI | ASSOCIATE
    content_text      TEXT        NOT NULL,
    -- content_embedding VECTOR(1536),          -- populated async via pgvector
    email_id          INT         REFERENCES email_inbox (email_id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_episode_dispute_id   ON dispute_memory_episode (dispute_id);
CREATE INDEX ix_episode_episode_type ON dispute_memory_episode (episode_type);
CREATE INDEX ix_episode_created_at   ON dispute_memory_episode (created_at);