-- Repair legacy local databases whose bridge snapshot cache was created
-- before normalized_status existed. This table is a sanitized cache only, so
-- rebuilding it is safer than leaving old schemas to break dashboard reads.

DROP INDEX IF EXISTS ix_license_admin_bridge_snapshots_latest;
DROP INDEX IF EXISTS ix_license_admin_bridge_snapshots_status;
DROP TABLE IF EXISTS license_admin_bridge_snapshots;

CREATE TABLE license_admin_bridge_snapshots (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id           INTEGER NOT NULL DEFAULT 1,
  snapshot_type       TEXT NOT NULL,
  normalized_status   TEXT NOT NULL,
  source_url          TEXT NOT NULL DEFAULT '',
  payload_json        TEXT NOT NULL DEFAULT '{}',
  error_json          TEXT NOT NULL DEFAULT '{}',
  fetched_at          TEXT NOT NULL,
  stale_after_seconds INTEGER NOT NULL DEFAULT 86400,
  created_at          TEXT NOT NULL
);

CREATE INDEX ix_license_admin_bridge_snapshots_latest
  ON license_admin_bridge_snapshots (tenant_id, snapshot_type, id DESC);

CREATE INDEX ix_license_admin_bridge_snapshots_status
  ON license_admin_bridge_snapshots (tenant_id, normalized_status, id DESC);
