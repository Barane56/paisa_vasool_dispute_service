-- 0036_rename_dispute_tokens_pv.sql
-- ─────────────────────────────────────────────────────────────────────
-- Renames existing DISP-XXXXX dispute tokens to PV-XXXXX format.
-- New disputes will be created with PV- prefix going forward.
-- Old DISP- references in customer emails are still matched by the
-- resolve_token node (backward compatible regex).
-- ─────────────────────────────────────────────────────────────────────

UPDATE dispute_master
SET    dispute_token = 'PV-' || SUBSTRING(dispute_token FROM 6)
WHERE  dispute_token LIKE 'DISP-%';
