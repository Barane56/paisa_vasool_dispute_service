-- =============================================================================
-- Migration V3: Resize content_embedding — vector(384) → vector(768)
-- =============================================================================
-- Reason:       Upgrading embedding model from BAAI/bge-small-en-v1.5 (384 dims)
--               to BAAI/bge-base-en-v1.5 (768 dims) for higher-quality semantic
--               similarity search on dispute memory episodes.
-- Applies to:   dispute_memory_episode
-- Depends on:   V1 (initial schema — creates dispute_memory_episode)
-- Safe to run:  Idempotent — checks current column dimension before altering.
--               Old 384-dim vectors are nulled out (they are incompatible with
--               the new column type and must be re-embedded anyway).
-- Post-steps:   See bottom of file.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Drop the existing pgvector IVFFlat index on content_embedding
--    (the index must be dropped before the column type can change;
--     we recreate it afterwards at the new dimension)
-- -----------------------------------------------------------------------------
DROP INDEX IF EXISTS ix_episode_embedding;


-- -----------------------------------------------------------------------------
-- 2. Null out all existing 384-dim embeddings
--    They are dimensionally incompatible with vector(768) and cannot be cast.
--    The application will re-embed episodes lazily as they are accessed, or
--    you can run the optional backfill query in the post-steps section below.
-- -----------------------------------------------------------------------------
UPDATE dispute_memory_episode
SET    content_embedding = NULL
WHERE  content_embedding IS NOT NULL;


-- -----------------------------------------------------------------------------
-- 3. Alter the column type from vector(384) to vector(768)
-- -----------------------------------------------------------------------------
ALTER TABLE dispute_memory_episode
    ALTER COLUMN content_embedding TYPE vector(768);

COMMENT ON COLUMN dispute_memory_episode.content_embedding IS
    'Semantic embedding of content_text produced by BAAI/bge-base-en-v1.5 '
    '(768 dimensions). Used for pgvector cosine similarity search scoped by '
    'customer_id. NULL when the episode has not yet been embedded. '
    'Upgraded from bge-small-en-v1.5 (384 dims) in migration V3.';


-- -----------------------------------------------------------------------------
-- 4. Recreate the IVFFlat index for cosine similarity search at 768 dims
--    lists=100 is a safe default for up to ~1 M rows; tune upward as data grows
--    (rule of thumb: lists ≈ sqrt(row_count))
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_episode_embedding
    ON dispute_memory_episode
    USING ivfflat (content_embedding vector_cosine_ops)
    WITH (lists = 100)
    WHERE content_embedding IS NOT NULL;

COMMENT ON INDEX ix_episode_embedding IS
    'IVFFlat index for cosine similarity search on 768-dim embeddings. '
    'Rebuilt in V3 after column resize from 384 to 768 dims.';


-- =============================================================================
-- POST-MIGRATION STEPS (run manually after deploying application code)
-- =============================================================================
--
-- A. Verify the column type changed successfully:
--
--      SELECT column_name, udt_name
--      FROM   information_schema.columns
--      WHERE  table_name  = 'dispute_memory_episode'
--        AND  column_name = 'content_embedding';
--
--      Expected: udt_name = 'vector'  (pgvector reports the base type; confirm
--      dims with \d dispute_memory_episode in psql — should show vector(768))
--
-- B. Confirm all old embeddings were cleared:
--
--      SELECT COUNT(*) FROM dispute_memory_episode WHERE content_embedding IS NOT NULL;
--      -- Expected: 0  (immediately after migration, before any re-embedding)
--
-- C. Optional backfill — re-embed all existing episodes:
--    The application re-embeds new AI_RESPONSE / AI_ACKNOWLEDGEMENT episodes
--    automatically. For historical episodes you can trigger a Celery task or
--    run a one-off script that calls llm_client.embed() on each content_text
--    and calls MemoryEpisodeRepository.upsert_embedding().
--
-- D. Update settings.py (already done in V3 settings change):
--      EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
--      EMBEDDING_DIMS  = 768
--
-- E. Remove the now-stale EMBEDDING_DIMENSIONS = 1536 field from settings.py
--    if not already done (it is unused — all code reads EMBEDDING_DIMS).
--
-- =============================================================================
