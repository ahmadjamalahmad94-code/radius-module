-- سجل فحوصات «تتبع حالة الأجهزة» (يونيو 2026) — صفّ لكل دورة فحص
-- (يدوية بزر «فحص الكل» أو دورية من device_health_poll_worker).
-- يجيب عن: كم فحص جرى؟ متى؟ وماذا وجد كل فحص؟ مع تفاصيل كل جهاز JSON:
--   [{"device_id","name","status","latency_ms"}, ...]
CREATE TABLE IF NOT EXISTS network_device_health_checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL DEFAULT 1,
    source        TEXT    NOT NULL DEFAULT 'manual',  -- manual | poller
    ok            INTEGER NOT NULL DEFAULT 1,
    error         TEXT    NOT NULL DEFAULT '',
    scanned       INTEGER NOT NULL DEFAULT 0,
    up_count      INTEGER NOT NULL DEFAULT 0,
    down_count    INTEGER NOT NULL DEFAULT 0,
    high_latency  INTEGER NOT NULL DEFAULT 0,
    unknown_count INTEGER NOT NULL DEFAULT 0,
    changed       INTEGER NOT NULL DEFAULT 0,
    alerts        INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    details_json  TEXT    NOT NULL DEFAULT '[]',
    created_at    TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_network_device_health_checks_tenant
    ON network_device_health_checks (tenant_id, id DESC);
