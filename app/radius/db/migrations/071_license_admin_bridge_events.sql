-- V40 admin bridge local operations event log.
-- P11 keeps events local because no canonical V40 event callback endpoint is
-- confirmed yet.

CREATE TABLE IF NOT EXISTS license_admin_bridge_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id    INTEGER NOT NULL DEFAULT 1,
  event_type   TEXT NOT NULL,
  severity     TEXT NOT NULL DEFAULT 'info',
  status       TEXT NOT NULL DEFAULT 'recorded',
  source       TEXT NOT NULL DEFAULT 'radius-module',
  reference    TEXT NOT NULL DEFAULT '',
  event_key    TEXT,
  label_ar     TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_license_admin_bridge_events_key
  ON license_admin_bridge_events (tenant_id, event_key)
  WHERE event_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_license_admin_bridge_events_latest
  ON license_admin_bridge_events (tenant_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_license_admin_bridge_events_type
  ON license_admin_bridge_events (tenant_id, event_type, id DESC);
