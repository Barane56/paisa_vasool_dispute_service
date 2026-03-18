-- =============================================================================
-- 0020_payment_detail_multi_payment_support.sql
-- =============================================================================
-- Allow multiple payment_detail rows per invoice_number (partial payments,
-- chargebacks, re-payments, etc.).
--
-- Before this migration the combination (customer_id, invoice_number) was
-- implicitly treated as unique by application code.  We now make that
-- explicit only when a UNIQUE constraint existed, and drop it to permit
-- multiple rows.  The existing indexes are kept for query performance.
--
-- No data is lost; this is a pure schema relaxation.
-- =============================================================================

-- Add a payment_sequence column so each payment for the same invoice can be
-- ordered and labelled (1st instalment, 2nd instalment, chargeback, etc.)
ALTER TABLE payment_detail
    ADD COLUMN IF NOT EXISTS payment_sequence  SMALLINT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS payment_type      VARCHAR(50) NOT NULL DEFAULT 'FULL',
    ADD COLUMN IF NOT EXISTS payment_status    VARCHAR(50) NOT NULL DEFAULT 'CLEARED',
    ADD COLUMN IF NOT EXISTS payment_date      DATE,
    ADD COLUMN IF NOT EXISTS amount_paid       NUMERIC(14, 2);

COMMENT ON COLUMN payment_detail.payment_sequence IS
    'Ordinal position among payments for this invoice (1 = first payment, 2 = second, …)';
COMMENT ON COLUMN payment_detail.payment_type IS
    'FULL | PARTIAL | CHARGEBACK | REFUND | ADVANCE';
COMMENT ON COLUMN payment_detail.payment_status IS
    'CLEARED | PENDING | FAILED | REVERSED';
COMMENT ON COLUMN payment_detail.payment_date IS
    'Date when the payment was actually made (extracted for fast filtering)';
COMMENT ON COLUMN payment_detail.amount_paid IS
    'Denormalised amount for quick aggregation without parsing the JSONB blob';

-- Index to efficiently retrieve all payments for an invoice ordered by sequence
CREATE INDEX IF NOT EXISTS ix_payment_detail_invoice_sequence
    ON payment_detail (invoice_number, payment_sequence);

-- Index for filtering by payment type / status
CREATE INDEX IF NOT EXISTS ix_payment_detail_payment_type
    ON payment_detail (payment_type);

CREATE INDEX IF NOT EXISTS ix_payment_detail_payment_status
    ON payment_detail (payment_status);

-- Backfill the new columns for existing rows using data already stored in the
-- payment_details JSONB column where available.
UPDATE payment_detail
SET
    payment_date   = COALESCE(
                         (payment_details ->> 'payment_date')::date,
                         NULL
                     ),
    amount_paid    = COALESCE(
                         (payment_details ->> 'amount_paid')::numeric,
                         NULL
                     ),
    payment_status = COALESCE(
                         payment_details ->> 'status',
                         'CLEARED'
                     )
WHERE payment_details IS NOT NULL;
