-- Migration 0037: AR Document Graph
-- Adds ar_documents and ar_document_keys tables.
-- Adds primary_doc_id to dispute_master (nullable, backward-compatible).

-- Document type enum
DO $$ BEGIN
  CREATE TYPE ar_doc_type AS ENUM (
    'PO', 'INVOICE', 'GRN', 'PAYMENT', 'CONTRACT', 'CREDIT_NOTE'
  );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS ar_documents (
    doc_id         SERIAL PRIMARY KEY,
    customer_scope TEXT         NOT NULL,
    doc_type       ar_doc_type  NOT NULL,
    doc_date       DATE,
    status         TEXT         NOT NULL DEFAULT 'ACTIVE',
    file_path      TEXT,
    raw_text       TEXT,
    uploaded_by    INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_ar_docs_scope      ON ar_documents (customer_scope);
CREATE INDEX IF NOT EXISTS ix_ar_docs_type        ON ar_documents (doc_type);
CREATE INDEX IF NOT EXISTS ix_ar_docs_created     ON ar_documents (created_at DESC);

CREATE TABLE IF NOT EXISTS ar_document_keys (
    key_id          SERIAL PRIMARY KEY,
    doc_id          INTEGER NOT NULL REFERENCES ar_documents(doc_id) ON DELETE CASCADE,
    key_type        TEXT    NOT NULL,
    key_value_raw   TEXT    NOT NULL,
    key_value_norm  TEXT    NOT NULL,
    confidence      FLOAT   NOT NULL DEFAULT 1.0,
    source          TEXT    NOT NULL DEFAULT 'regex',   -- regex | llm | manual
    verified        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_doc_key UNIQUE (doc_id, key_type, key_value_norm)
);

CREATE INDEX IF NOT EXISTS ix_ark_type_norm  ON ar_document_keys (key_type, key_value_norm);
CREATE INDEX IF NOT EXISTS ix_ark_doc_id     ON ar_document_keys (doc_id);

-- Add primary_doc_id to dispute_master (nullable — existing disputes keep NULL)
ALTER TABLE dispute_master
  ADD COLUMN IF NOT EXISTS primary_doc_id INTEGER
    REFERENCES ar_documents(doc_id) ON DELETE SET NULL;

COMMENT ON TABLE ar_documents IS
  'Source AR documents uploaded by FAs: PO, Invoice, GRN, Payment, Contract, Credit Note';
COMMENT ON TABLE ar_document_keys IS
  'Extracted reference keys from AR documents — shared key_value_norm creates graph edges';
