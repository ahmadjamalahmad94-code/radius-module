-- Real restore apply: remember the verified candidate backup file and when
-- the destructive restore was actually applied. P08 only recorded readiness;
-- these columns let `apply_restore` perform the real online DB swap and audit
-- the moment it landed.

ALTER TABLE license_admin_restore_requests
  ADD COLUMN candidate_path TEXT;

ALTER TABLE license_admin_restore_requests
  ADD COLUMN applied_at TEXT;

ALTER TABLE license_admin_restore_requests
  ADD COLUMN applied_by TEXT NOT NULL DEFAULT '';
