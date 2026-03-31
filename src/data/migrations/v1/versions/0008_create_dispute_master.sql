-- =============================================================================
-- 0008_create_dispute_master.sql
-- Depends on: 0003_create_invoice_data.sql
--             0004_create_payment_detail.sql
--             0005_create_dispute_type.sql
--             0006_create_email_inbox.sql
--
-- Also closes the circular FK: email_inbox.dispute_id → dispute_master
-- =============================================================================

CREATE TABLE dispute_master (
    dispute_id        SERIAL       PRIMARY KEY,
    email_id          INT          NOT NULL REFERENCES email_inbox    (email_id)          ON DELETE RESTRICT,
    invoice_id        INT                   REFERENCES invoice_data   (invoice_id)         ON DELETE SET NULL,
    payment_detail_id INT                   REFERENCES payment_detail (payment_detail_id)  ON DELETE SET NULL,
    customer_id       VARCHAR(100) NOT NULL,
    dispute_type_id   INT          NOT NULL REFERENCES dispute_type   (dispute_type_id)    ON DELETE RESTRICT,
    status            VARCHAR(50)  NOT NULL,  -- OPEN | UNDER_REVIEW | RESOLVED | CLOSED
    priority          VARCHAR(20)  NOT NULL DEFAULT 'MEDIUM',  -- LOW | MEDIUM | HIGH
    description       TEXT         NOT NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_dispute_master_customer_id     ON dispute_master (customer_id);
CREATE INDEX ix_dispute_master_status          ON dispute_master (status);
CREATE INDEX ix_dispute_master_priority        ON dispute_master (priority);
CREATE INDEX ix_dispute_master_dispute_type_id ON dispute_master (dispute_type_id);
CREATE INDEX ix_dispute_master_created_at      ON dispute_master (created_at);

-- Close the circular FK now that dispute_master exists
ALTER TABLE email_inbox
    ADD CONSTRAINT fk_email_inbox_dispute_id
    FOREIGN KEY (dispute_id)
    REFERENCES dispute_master (dispute_id)
    ON DELETE SET NULL;
