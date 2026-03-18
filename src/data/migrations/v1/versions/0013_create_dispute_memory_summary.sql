-- =============================================================================
-- 0013_create_dispute_memory_summary.sql
-- Depends on: 0008_create_dispute_master.sql
--             0012_create_dispute_memory_episode.sql
-- =============================================================================

CREATE TABLE dispute_memory_summary (
    summary_id               SERIAL      PRIMARY KEY,
    dispute_id               INT         NOT NULL UNIQUE REFERENCES dispute_master        (dispute_id) ON DELETE CASCADE,
    summary_text             TEXT        NOT NULL,
    covered_up_to_episode_id INT         REFERENCES dispute_memory_episode (episode_id)  ON DELETE SET NULL,
    version                  INT         NOT NULL DEFAULT 1,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_memory_summary_dispute_id ON dispute_memory_summary (dispute_id);