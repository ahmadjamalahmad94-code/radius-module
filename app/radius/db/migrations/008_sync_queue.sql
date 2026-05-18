-- 008_sync_queue — طابور المزامنة مع MikroTik

CREATE TABLE sync_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    router_id       INTEGER,                     -- NULL = كل المتاحة للـ tenant
    kind            TEXT NOT NULL,               -- subscriber_upsert / subscriber_delete / plan_upsert / plan_delete / pool_upsert / disconnect / reset_password
    entity_id       INTEGER,                     -- مرجع داخلي (sub.id / plan.id ...)
    entity_key      TEXT DEFAULT '',             -- username/plan_name/pool_name لتسهيل البحث
    payload_json    TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued / syncing / done / failed
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT DEFAULT '',
    last_router_id  INTEGER,                     -- آخر router فشل
    next_attempt_at TEXT NOT NULL,
    completed_at    TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (router_id) REFERENCES mikrotik_configs(id) ON DELETE SET NULL
);
CREATE INDEX idx_sq_pickup ON sync_queue(status, next_attempt_at);
CREATE INDEX idx_sq_tenant_date ON sync_queue(tenant_id, created_at DESC);
CREATE INDEX idx_sq_entity ON sync_queue(tenant_id, kind, entity_key);
