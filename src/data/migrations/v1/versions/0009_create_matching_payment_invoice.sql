-- =============================================================================
-- 0009_create_matching_payment_invoice.sql
-- Depends on: 0003_create_invoice_data.sql
--             0004_create_payment_detail.sql
-- =============================================================================

CREATE TABLE matching_payment_invoice (
    match_id          SERIAL        PRIMARY KEY,
    payment_detail_id INT           NOT NULL REFERENCES payment_detail (payment_detail_id) ON DELETE RESTRICT,
    invoice_id        INT           NOT NULL REFERENCES invoice_data   (invoice_id)        ON DELETE RESTRICT,
    matched_amount    NUMERIC(12,2) NOT NULL,
    match_score       NUMERIC(5,2)  NOT NULL,
    match_status      VARCHAR(50)   NOT NULL,  -- FULL | PARTIAL | FAILED
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX ix_match_payment_detail_id ON matching_payment_invoice (payment_detail_id);
CREATE INDEX ix_match_invoice_id        ON matching_payment_invoice (invoice_id);
CREATE INDEX ix_match_status            ON matching_payment_invoice (match_status);