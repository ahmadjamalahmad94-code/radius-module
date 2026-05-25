-- V40 admin bridge backup upload foundation.
-- Tracks local backup artifacts and sanitized upload attempts.

CREATE TABLE IF NOT EXISTS license_admin_backup_artifacts (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id             INTEGER NOT NULL DEFAULT 1,
  backup_reference      TEXT NOT NULL,
  source_run_id         INTEGER,
  path                  TEXT NOT NULL,
  kind                  TEXT NOT NULL DEFAULT 'sqlite',
  size                  INTEGER NOT NULL DEFAULT 0,
  checksum_sha256       TEXT NOT NULL,
  upload_status         TEXT NOT NULL DEFAULT 'local_only',
  uploaded_to_admin_at  TEXT,
  metadata_json         TEXT NOT NULL DEFAULT '{}',
  created_at            TEXT NOT NULL,
  updated_at            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_license_admin_backup_reference
  ON license_admin_backup_artifacts (tenant_id, backup_reference);

CREATE INDEX IF NOT EXISTS ix_license_admin_backup_artifacts_latest
  ON license_admin_backup_artifacts (tenant_id, id DESC);

CREATE TABLE IF NOT EXISTS license_admin_backup_upload_attempts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id         INTEGER NOT NULL DEFAULT 1,
  artifact_id       INTEGER NOT NULL,
  dry_run           INTEGER NOT NULL DEFAULT 1,
  content_included  INTEGER NOT NULL DEFAULT 0,
  status            TEXT NOT NULL,
  payload_json      TEXT NOT NULL DEFAULT '{}',
  error_json        TEXT NOT NULL DEFAULT '{}',
  response_json     TEXT NOT NULL DEFAULT '{}',
  sent_at           TEXT,
  created_at        TEXT NOT NULL,
  FOREIGN KEY (artifact_id) REFERENCES license_admin_backup_artifacts(id)
);

CREATE INDEX IF NOT EXISTS ix_license_admin_backup_upload_attempts_latest
  ON license_admin_backup_upload_attempts (tenant_id, artifact_id, id DESC);
