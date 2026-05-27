-- Extend router_backups (created in migration 041) with the
-- columns needed to keep the .backup file content + a manifest
-- snapshot inside HobeRadius, so the operator can restore from
-- the server in an emergency instead of using Winbox.
--
-- Background:
-- Migration 041 created router_backups with metadata only; the
-- file itself was expected to live on disk via storage_path.
-- That model is fine for archival but doesn't help an operator
-- whose router is unreachable. We now want the binary captured
-- in DB AND a JSON manifest that documents exactly what was on
-- the router when the backup was taken (identity, hotspot
-- servers + profiles, broadband, DHCP, firewall counts, WG
-- peers, …) — so the operator can read «what was on this
-- router at <date>?» without restoring.
--
-- Additive only. Existing rows get sensible defaults.

ALTER TABLE router_backups
  ADD COLUMN file_blob BLOB;

ALTER TABLE router_backups
  ADD COLUMN router_filename TEXT NOT NULL DEFAULT '';

ALTER TABLE router_backups
  ADD COLUMN manifest_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE router_backups
  ADD COLUMN manifest_summary TEXT NOT NULL DEFAULT '';

ALTER TABLE router_backups
  ADD COLUMN router_status TEXT NOT NULL DEFAULT 'on_router';

ALTER TABLE router_backups
  ADD COLUMN restored_at TEXT NOT NULL DEFAULT '';

ALTER TABLE router_backups
  ADD COLUMN restored_by TEXT NOT NULL DEFAULT '';

-- Faster lookup by router_filename when re-syncing on-router state.
CREATE INDEX IF NOT EXISTS ix_router_backups_filename
  ON router_backups (tenant_id, router_id, router_filename);
