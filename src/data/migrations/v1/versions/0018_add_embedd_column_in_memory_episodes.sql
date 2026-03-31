-- ============================================================
-- Migration: Enable pgvector and add content_embedding column
-- Run this once against your PostgreSQL database
--
-- Usage (psql):
--   psql -U postgres -d paisa_vasool -f migrate_add_vector.sql
-- ============================================================

-- Step 1: Enable the pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Step 2: Add content_embedding column to dispute_memory_episode
-- (only if it doesn't already exist)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'dispute_memory_episode'
          AND column_name = 'content_embedding'
    ) THEN
        ALTER TABLE dispute_memory_episode
        ADD COLUMN content_embedding vector(1536);

        RAISE NOTICE 'content_embedding column added successfully.';
    ELSE
        RAISE NOTICE 'content_embedding column already exists, skipping.';
    END IF;
END
$$;

-- Step 3: Optional index for similarity search (can skip if not using embeddings)
-- CREATE INDEX IF NOT EXISTS ix_episode_embedding
--     ON dispute_memory_episode
--     USING ivfflat (content_embedding vector_cosine_ops)
--     WITH (lists = 100);

SELECT 'Migration complete.' AS status;
