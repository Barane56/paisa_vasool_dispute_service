-- =============================================================================
-- 0011_create_analysis_supporting_refs.sql
-- Depends on: 0010_create_dispute_ai_analysis.sql
-- =============================================================================

CREATE TABLE analysis_supporting_refs (
    ref_id          SERIAL  PRIMARY KEY,
    analysis_id     INT     NOT NULL REFERENCES dispute_ai_analysis (analysis_id) ON DELETE CASCADE,
    reference_table TEXT    NOT NULL,
    ref_id_value    INT     NOT NULL,
    context_note    TEXT    NOT NULL
);

CREATE INDEX ix_analysis_refs_analysis_id     ON analysis_supporting_refs (analysis_id);
CREATE INDEX ix_analysis_refs_reference_table ON analysis_supporting_refs (reference_table);