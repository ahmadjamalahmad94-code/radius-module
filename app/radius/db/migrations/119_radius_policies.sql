-- RadiusPolicy persistence. The integration adapters declared a policy CRUD
-- contract (list/upsert/delete) but the SQLite adapter no-opped it (returned
-- [] / echoed the input / did nothing). This table makes the CRUD real and
-- tenant-scoped so policies actually survive a round trip.

CREATE TABLE IF NOT EXISTS radius_policies (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id   INTEGER NOT NULL DEFAULT 1,
  name        TEXT    NOT NULL,
  policy_type TEXT    NOT NULL DEFAULT '',
  params_json TEXT    NOT NULL DEFAULT '{}',
  enabled     INTEGER NOT NULL DEFAULT 1,
  priority    INTEGER NOT NULL DEFAULT 100,
  description TEXT    NOT NULL DEFAULT '',
  created_at  TEXT    NOT NULL DEFAULT '',
  updated_at  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_radius_policies_name
  ON radius_policies (tenant_id, name);

CREATE INDEX IF NOT EXISTS ix_radius_policies_priority
  ON radius_policies (tenant_id, priority, id);
