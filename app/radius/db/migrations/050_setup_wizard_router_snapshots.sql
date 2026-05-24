-- Wave D: setup wizard router inventory snapshots.
-- Read-only inventory cache; secrets must be sanitized before insertion.

CREATE TABLE IF NOT EXISTS setup_wizard_router_snapshots (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  wizard_run_id       INTEGER NOT NULL,
  tenant_id           INTEGER NOT NULL DEFAULT 1,
  source              TEXT NOT NULL DEFAULT 'pasted',
  identity_json       TEXT NOT NULL DEFAULT '{}',
  interfaces_json     TEXT NOT NULL DEFAULT '[]',
  addresses_json      TEXT NOT NULL DEFAULT '[]',
  routes_json         TEXT NOT NULL DEFAULT '[]',
  pools_json          TEXT NOT NULL DEFAULT '[]',
  nat_json            TEXT NOT NULL DEFAULT '[]',
  radius_json         TEXT NOT NULL DEFAULT '[]',
  hotspot_json        TEXT NOT NULL DEFAULT '[]',
  ppp_json            TEXT NOT NULL DEFAULT '[]',
  wireguard_json      TEXT NOT NULL DEFAULT '[]',
  risk_report_json    TEXT NOT NULL DEFAULT '{}',
  raw_summary_json    TEXT NOT NULL DEFAULT '{}',
  created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_setup_wizard_router_snapshots_run
  ON setup_wizard_router_snapshots (tenant_id, wizard_run_id, id DESC);
