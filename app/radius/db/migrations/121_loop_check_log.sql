-- سجل فحوصات اللوب (يونيو 2026) — صفّ لكل فحص (يدوي من الصفحة أو دوري
-- من loop_probe_poller). يجيب عن: كم فحص جرى؟ متى؟ ماذا وجد كل فحص؟
-- التفاصيل لكل منفذ تُخزَّن JSON في details_json:
--   [{"iface","status","is_loop","address","server"}, ...]
CREATE TABLE IF NOT EXISTS router_loop_checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL DEFAULT 1,
    router_id     INTEGER NOT NULL,
    source        TEXT    NOT NULL DEFAULT 'manual',  -- manual | poller
    ok            INTEGER NOT NULL DEFAULT 1,         -- 0 = تعذّرت القراءة من الراوتر
    error         TEXT    NOT NULL DEFAULT '',
    ports_total   INTEGER NOT NULL DEFAULT 0,
    loops_found   INTEGER NOT NULL DEFAULT 0,
    rules_missing INTEGER NOT NULL DEFAULT 0,
    details_json  TEXT    NOT NULL DEFAULT '[]',
    created_at    TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_router_loop_checks_router
    ON router_loop_checks (tenant_id, router_id, id DESC);
