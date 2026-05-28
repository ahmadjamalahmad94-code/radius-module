-- sync_queue.router_id is a routing hint. The old mikrotik_configs table was
-- removed in migration 035, so keeping a foreign key to it makes inserts fail
-- on existing SQLite databases with: no such table: main.mikrotik_configs.

PRAGMA foreign_keys=OFF;

DROP INDEX IF EXISTS idx_sq_pickup;
DROP INDEX IF EXISTS idx_sq_tenant_date;
DROP INDEX IF EXISTS idx_sq_entity;

CREATE TABLE sync_queue_rebuilt (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    router_id       INTEGER,
    kind            TEXT NOT NULL,
    entity_id       INTEGER,
    entity_key      TEXT DEFAULT '',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'queued',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT DEFAULT '',
    last_router_id  INTEGER,
    next_attempt_at TEXT NOT NULL,
    completed_at    TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

INSERT INTO sync_queue_rebuilt (
    id, tenant_id, router_id, kind, entity_id, entity_key, payload_json,
    status, attempts, last_error, last_router_id, next_attempt_at,
    completed_at, created_at
)
SELECT
    id, tenant_id, router_id, kind, entity_id, entity_key, payload_json,
    status, attempts, last_error, last_router_id, next_attempt_at,
    completed_at, created_at
FROM sync_queue;

DROP TABLE sync_queue;
ALTER TABLE sync_queue_rebuilt RENAME TO sync_queue;

CREATE INDEX idx_sq_pickup ON sync_queue(status, next_attempt_at);
CREATE INDEX idx_sq_tenant_date ON sync_queue(tenant_id, created_at DESC);
CREATE INDEX idx_sq_entity ON sync_queue(tenant_id, kind, entity_key);

PRAGMA foreign_keys=ON;
