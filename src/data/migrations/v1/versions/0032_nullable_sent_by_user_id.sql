-- 0032_nullable_sent_by_user_id.sql
-- Allow sent_by_user_id to be NULL on outbound_emails.
-- NULL means the email was sent by the AI (auto-response), not a human FA.

ALTER TABLE outbound_emails
    ALTER COLUMN sent_by_user_id DROP NOT NULL;

ALTER TABLE outbound_emails
    DROP CONSTRAINT IF EXISTS outbound_emails_sent_by_user_id_fkey;

ALTER TABLE outbound_emails
    ADD CONSTRAINT outbound_emails_sent_by_user_id_fkey
        FOREIGN KEY (sent_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL;
