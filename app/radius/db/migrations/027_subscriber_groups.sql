-- ╔════════════════════════════════════════════════════════════════════╗
-- ║  Subscriber groups — bundle services for a set of subscribers        ║
-- ║                                                                      ║
-- ║  Operators classify subscribers into named groups (e.g. "VIP",       ║
-- ║  "Family-Pack", "Office-Hours") to apply shared rules in bulk:       ║
-- ║                                                                      ║
-- ║    • bandwidth_schedule_id  → link to a schedule from §22            ║
-- ║    • default_plan_id        → plan auto-assigned to new members      ║
-- ║    • default_auto_renewal   → renewal default for members            ║
-- ║    • working_days           → CSV (sat,sun,mon,...) for allowed days ║
-- ║                                                                      ║
-- ║  Subscribers link via subscribers.subscriber_group_id (nullable).    ║
-- ║  The legacy free-text `group` column on subscribers stays for now    ║
-- ║  for backward compatibility; new UI uses the FK exclusively.         ║
-- ╚════════════════════════════════════════════════════════════════════╝

CREATE TABLE IF NOT EXISTS subscriber_groups (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               INTEGER NOT NULL,
    name                    TEXT    NOT NULL,
    description             TEXT    NOT NULL DEFAULT '',

    -- service bindings (all optional; NULL = group does not enforce)
    bandwidth_schedule_id   INTEGER,
    default_plan_id         INTEGER,
    default_auto_renewal    INTEGER NOT NULL DEFAULT 1,   -- 1=on, 0=off
    working_days            TEXT    NOT NULL DEFAULT '',  -- CSV: sat,sun,mon,...

    -- metadata
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT,
    deleted_at              TEXT,

    UNIQUE (tenant_id, name)
);

CREATE INDEX IF NOT EXISTS idx_subscriber_groups_tenant
ON subscriber_groups(tenant_id, deleted_at);

-- ── Link subscribers ↔ groups (FK; new UI uses this column) ────────────
ALTER TABLE subscribers
ADD COLUMN subscriber_group_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_subscribers_group_id
ON subscribers(tenant_id, subscriber_group_id);
