-- O7 — Backup-before-risk awareness.
--
-- Adds a `reason` label to router_backups so operators can
-- distinguish a manual snapshot from a "taken before
-- programming" or "taken before recovery" backup.
--
-- Allowed values (string, enforced at the repo layer):
--   manual                 — operator clicked save
--   scheduled              — future cron job
--   before_dangerous       — taken automatically before any
--                            destructive op (future)
--   before_programming     — taken before a Q2 apply (future)
--   before_recovery        — taken before a restore attempt
--
-- Default = 'manual' to keep existing rows + manual-save path
-- unchanged.
ALTER TABLE router_backups
  ADD COLUMN reason TEXT NOT NULL DEFAULT 'manual';
