-- =============================================================================
-- 0007_create_email_attachments.sql
-- Depends on: 0006_create_email_inbox.sql
-- =============================================================================

CREATE TABLE email_attachments (
    attachment_id  SERIAL       PRIMARY KEY,
    email_id       INT          NOT NULL REFERENCES email_inbox (email_id) ON DELETE CASCADE,
    file_name      VARCHAR(255) NOT NULL,
    file_type      VARCHAR(20)  NOT NULL,  -- pdf | csv | xlsx | txt
    extracted_text TEXT         NOT NULL,
    uploaded_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_email_attachments_email_id  ON email_attachments (email_id);
CREATE INDEX ix_email_attachments_file_type ON email_attachments (file_type);