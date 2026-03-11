-- =============================================================================
-- 0003_create_invoice_data.sql
-- =============================================================================

CREATE TABLE invoice_data (
    invoice_id      SERIAL       PRIMARY KEY,
    invoice_number  VARCHAR(100) NOT NULL,
    invoice_url     TEXT         NOT NULL UNIQUE,
    invoice_details JSONB        NOT NULL,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_invoice_data_invoice_number ON invoice_data (invoice_number);