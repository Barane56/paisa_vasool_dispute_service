-- =============================================================================
-- 0004_create_payment_detail.sql
-- =============================================================================

CREATE TABLE payment_detail (
    payment_detail_id SERIAL       PRIMARY KEY,
    customer_id       VARCHAR(100) NOT NULL,
    invoice_number    VARCHAR(100) NOT NULL,
    payment_url       TEXT         NOT NULL UNIQUE,
    payment_details   JSONB
);

CREATE INDEX ix_payment_detail_customer_id    ON payment_detail (customer_id);
CREATE INDEX ix_payment_detail_invoice_number ON payment_detail (invoice_number);
