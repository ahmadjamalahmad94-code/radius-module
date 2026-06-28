-- 143_admins_must_change_password — force a password change on first login for
-- panel admins CREATED centrally by the licensing panel.
--
-- The licensing owner can now create a customer's panel admin from the customer
-- file (feat/panel-admins-full-mgmt). The initial password is set ONCE centrally
-- (delivered as a werkzeug hash over the signed bridge, never plaintext); this
-- flag forces that admin to choose a new password the first time they log in.
-- Cleared the moment they change their password locally (/account/password).
--
-- Additive only; SQLite has no ADD COLUMN IF NOT EXISTS, so this file must be
-- applied exactly once (the runner guarantees that). Existing admins default to
-- 0 — they are never forced to change.

ALTER TABLE admins ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
