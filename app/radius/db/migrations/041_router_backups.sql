-- S8.1 — Router backup metadata storage.
--
-- File contents live on disk (S8.2's action writes them there).
-- This table tracks: who saved, when, what file, what type, what
-- checksum. Operators search/filter via this index; download
-- routes use storage_path to stream the actual bytes.
--
-- Backup types — kept open-ended:
--   export-text   — /export from RouterOS (human-readable .rsc)
--   binary        — /system/backup/save (.backup file)
--
-- Sensitive flag — when set, the download path requires
-- PERM_BACKUP (not just PERM_VIEW). Binary backups CAN contain
-- credentials so they default to sensitive=1.

CREATE TABLE IF NOT EXISTS router_backups (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL DEFAULT 1,
  router_id     INTEGER NOT NULL,
  backup_type   TEXT NOT NULL,
  filename      TEXT NOT NULL,
  storage_path  TEXT NOT NULL DEFAULT '',
  size_bytes    INTEGER NOT NULL DEFAULT 0,
  checksum      TEXT NOT NULL DEFAULT '',
  sensitive     INTEGER NOT NULL DEFAULT 1,
  notes         TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'success',
  error_message TEXT NOT NULL DEFAULT '',
  created_by    INTEGER,
  created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_router_backups_router
  ON router_backups (tenant_id, router_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_router_backups_tenant
  ON router_backups (tenant_id, created_at DESC);
