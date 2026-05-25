-- V40 admin bridge restore polling foundation.
-- Records requested restores and local safety state only.

CREATE TABLE IF NOT EXISTS license_admin_restore_requests (
  id                         INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                  INTEGER NOT NULL DEFAULT 1,
  reference                  TEXT NOT NULL,
  requested_backup_reference TEXT NOT NULL,
  status                     TEXT NOT NULL,
  received_at                TEXT NOT NULL,
  approved_by_admin_panel    INTEGER NOT NULL DEFAULT 0,
  local_snapshot_path        TEXT,
  checksum_verified          INTEGER NOT NULL DEFAULT 0,
  result_message             TEXT NOT NULL DEFAULT '',
  payload_json               TEXT NOT NULL DEFAULT '{}',
  created_at                 TEXT NOT NULL,
  updated_at                 TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_license_admin_restore_reference
  ON license_admin_restore_requests (tenant_id, reference);

CREATE INDEX IF NOT EXISTS ix_license_admin_restore_latest
  ON license_admin_restore_requests (tenant_id, id DESC);
