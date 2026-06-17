-- «استيراد مستخدمي المايكروتيك» (feat/mikrotik-user-import) — يونيو 2026.
-- إضافي/خامل: لا يلمس أي مسار حيّ قائم. نمط schema-heal.
--
-- (1) nas_devices.api_type — نوع واجهة إدارة المايكروتيك المفضّلة لجلب
--     المستخدمين: 'rest' (REST API، RouterOS v7+) | 'api' (الـAPI الثنائي
--     8728/8729) | 'auto' (جرّب REST ثم تراجع إلى API). منفصل عن سرّ
--     RADIUS (nas_devices.secret) وعن api_user/api_password القائمة.
--     ⚠️ SQLite لا يدعم ADD COLUMN IF NOT EXISTS؛ العمود جديد كليًّا.
ALTER TABLE nas_devices ADD COLUMN api_type TEXT NOT NULL DEFAULT 'auto';

-- (2) mikrotik_import_logs — سجلّ كل عملية استيراد: جهاز NAS، النوع
--     (hotspot|broadband)، المصدر، العدّادات (مستورد/متخطّى/محدّث/فاشل/
--     الإجمالي)، نمط التكرار، أخطاء لكل اسم مستخدم (JSON)، المدير الذي
--     بدأها، والطابع الزمني والحالة. لا يُخزَّن أي كلمة مرور خام.
CREATE TABLE IF NOT EXISTS mikrotik_import_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id      INTEGER NOT NULL DEFAULT 1,
    nas_id         INTEGER NOT NULL,
    nas_name       TEXT    NOT NULL DEFAULT '',
    import_type    TEXT    NOT NULL,            -- hotspot | broadband
    source         TEXT    NOT NULL DEFAULT '', -- /ip hotspot user | /ppp secret
    transport      TEXT    NOT NULL DEFAULT '', -- rest | api (المُستخدَم فعلًا)
    duplicate_mode TEXT    NOT NULL DEFAULT 'skip_existing',
    total_count    INTEGER NOT NULL DEFAULT 0,
    imported_count INTEGER NOT NULL DEFAULT 0,
    updated_count  INTEGER NOT NULL DEFAULT 0,
    skipped_count  INTEGER NOT NULL DEFAULT 0,
    failed_count   INTEGER NOT NULL DEFAULT 0,
    errors_json    TEXT    NOT NULL DEFAULT '[]',  -- [{"username","error"}, ...]
    status         TEXT    NOT NULL DEFAULT 'completed', -- completed|partial|failed
    message        TEXT    NOT NULL DEFAULT '',
    started_by     INTEGER NOT NULL DEFAULT 0,
    started_by_name TEXT   NOT NULL DEFAULT '',
    started_at     TEXT    NOT NULL DEFAULT '',
    finished_at    TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_mikrotik_import_logs_tenant
    ON mikrotik_import_logs (tenant_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_mikrotik_import_logs_nas
    ON mikrotik_import_logs (tenant_id, nas_id, id DESC);
