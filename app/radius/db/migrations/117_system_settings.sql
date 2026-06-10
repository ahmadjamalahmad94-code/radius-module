-- مخزن إعدادات النظام/البيئة (instance-level): مفتاح→قيمة قابلة للتحرير من
-- لوحة الإدارة بدل متغيّرات البيئة. علم is_secret يميّز القيم المشفّرة.
-- نمط schema-heal: CREATE TABLE IF NOT EXISTS (آمن للتشغيل المتكرّر).
CREATE TABLE IF NOT EXISTS system_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT    NOT NULL DEFAULT '',
    is_secret   INTEGER NOT NULL DEFAULT 0,
    updated_by  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT
);
