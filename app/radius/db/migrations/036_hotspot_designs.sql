-- R2: persistent hotspot login-page designs per nas_devices row.
--
-- The R1 service ships the template library and renderer; R2
-- needs to remember which template a given router is currently
-- branded with and what values the operator filled in, so the
-- designer + deployer (R3) can reload that state across sessions.
--
-- Schema is intentionally narrow:
--   id           — surrogate.
--   tenant_id    — multi-tenant scope (matches every other table).
--   nas_id       — FK to nas_devices.id; one design per router.
--   template_slug — chosen template (classic/card/dark/minimal).
--   variables_json — JSON object {TENANT_NAME:"...", ...}.
--                   Validated by app.radius.services.hotspot_templates
--                   before insert; we still store as a text column
--                   so adding a new variable doesn't need another
--                   migration.
--   updated_at   — UTC ISO timestamp.

CREATE TABLE IF NOT EXISTS hotspot_designs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL,
  nas_id          INTEGER NOT NULL,
  template_slug   TEXT NOT NULL,
  variables_json  TEXT NOT NULL DEFAULT '{}',
  updated_at      TEXT NOT NULL DEFAULT '',
  UNIQUE (tenant_id, nas_id)
);
CREATE INDEX IF NOT EXISTS ix_hotspot_designs_nas
  ON hotspot_designs (nas_id);
