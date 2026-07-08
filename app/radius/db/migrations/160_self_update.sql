-- 160 — Self-update (opt-in, per-customer) audit trail.
--
-- The canonical HOST signal is the on-disk marker file
-- (/var/lib/hoberadius/update-request.json); this table is the in-panel
-- AUDIT LOG of who requested what and how it turned out — so the owner can
-- see update history in the DB even after the marker files are rotated.
--
-- The "latest available version" cache lives in tenant_settings
-- (self_update.* keys), not here, because it is a single mutable snapshot.

CREATE TABLE IF NOT EXISTS self_update_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL DEFAULT 1,
    event         TEXT    NOT NULL,          -- 'requested' | 'check' | 'status'
    from_version  TEXT    DEFAULT '',
    to_version    TEXT    DEFAULT '',
    state         TEXT    DEFAULT '',        -- running|success|failed (status events)
    requested_by  INTEGER DEFAULT 0,         -- admin id (0 = system/worker)
    actor         TEXT    DEFAULT '',        -- admin display name
    detail        TEXT    DEFAULT '',        -- freeform / JSON
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_self_update_events_tenant
    ON self_update_events(tenant_id, created_at DESC);
