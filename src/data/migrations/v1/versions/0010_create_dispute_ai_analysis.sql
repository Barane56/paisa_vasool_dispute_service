-- =============================================================================
-- 0010_create_dispute_ai_analysis.sql
-- Depends on: 0008_create_dispute_master.sql
-- =============================================================================

CREATE TABLE dispute_ai_analysis (
    analysis_id             SERIAL       PRIMARY KEY,
    dispute_id              INT          NOT NULL REFERENCES dispute_master (dispute_id) ON DELETE CASCADE,
    predicted_category      VARCHAR(100) NOT NULL,
    confidence_score        NUMERIC(5,2) NOT NULL,
    ai_summary              TEXT         NOT NULL,
    ai_response             TEXT,
    auto_response_generated BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    memory_context_used     BOOLEAN      NOT NULL DEFAULT FALSE,
    episodes_referenced     INT[]
);

CREATE INDEX ix_dispute_ai_analysis_dispute_id         ON dispute_ai_analysis (dispute_id);
CREATE INDEX ix_dispute_ai_analysis_predicted_category ON dispute_ai_analysis (predicted_category);