-- =============================================================================
-- 0015_create_dispute_open_questions.sql
-- Depends on: 0008_create_dispute_master.sql
--             0012_create_dispute_memory_episode.sql
-- =============================================================================

CREATE TABLE dispute_open_questions (
    question_id            SERIAL      PRIMARY KEY,
    dispute_id             INT         NOT NULL REFERENCES dispute_master        (dispute_id)  ON DELETE CASCADE,
    asked_in_episode_id    INT         REFERENCES dispute_memory_episode (episode_id) ON DELETE SET NULL,
    question_text          TEXT        NOT NULL,
    status                 VARCHAR(30) NOT NULL DEFAULT 'PENDING',  -- PENDING | ANSWERED | EXPIRED
    answered_in_episode_id INT         REFERENCES dispute_memory_episode (episode_id) ON DELETE SET NULL,
    answered_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_open_questions_dispute_id ON dispute_open_questions (dispute_id);
CREATE INDEX ix_open_questions_status     ON dispute_open_questions (status);
