-- 0035_create_dispute_documents.sql
-- ────────────────────────────────────
-- Stores supporting documents manually uploaded by Finance Associates.
-- These are separate from email attachments (email_message_attachments)
-- and outbound email attachments (outbound_email_attachments).
-- FA can upload any file type — PDFs, images, spreadsheets, etc.
-- Files stored in GCS (or local fallback). Download via signed URL.

CREATE TABLE IF NOT EXISTS dispute_documents (
    document_id   SERIAL        PRIMARY KEY,
    dispute_id    INT           NOT NULL REFERENCES dispute_master(dispute_id) ON DELETE CASCADE,
    uploaded_by   INT           NOT NULL REFERENCES users(user_id)             ON DELETE RESTRICT,
    file_name     VARCHAR(255)  NOT NULL,
    file_type     VARCHAR(100)  NOT NULL,   -- MIME type e.g. application/pdf
    file_size     BIGINT        NULL,       -- bytes
    file_path     TEXT          NOT NULL,   -- GCS blob path or local path
    display_name  VARCHAR(255)  NULL,       -- optional human-readable label FA can set
    notes         TEXT          NULL,       -- optional notes about this document
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_dispute_documents_dispute_id   ON dispute_documents (dispute_id);
CREATE INDEX IF NOT EXISTS ix_dispute_documents_uploaded_by  ON dispute_documents (uploaded_by);
CREATE INDEX IF NOT EXISTS ix_dispute_documents_created_at   ON dispute_documents (created_at);
