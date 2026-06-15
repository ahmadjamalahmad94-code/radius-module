-- «الحظر والتحكم بالدخول» (feat/access-control-blocking) — يونيو 2026.
-- إضافي/خامل: لا يلمس أي جدول قائم. نمط schema-heal (CREATE TABLE IF NOT
-- EXISTS) آمن للتشغيل المتكرّر.
--
-- ⚠️ تنبيه ترقيم: فرع feat/data-connection-oneclick استخدم أيضًا الرقم 123
-- (123_data_connection.sql) على فرعه. الجدولان مختلفان تمامًا فلا تصادم
-- وظيفي؛ لكن إن دُمج الفرعان معًا أعِد ترقيم أحدهما (مثلًا هذا إلى 124) كي
-- لا يتكرّر البادئة الرقمية. (راجع نمط _MIGRATION_ALIASES في الـrunner.)

-- (1) access_blocks — قائمة الحظر الموحّدة. صفّ لكل حظر فعّال أو ملغى.
--   block_type يحدّد النطاق المستهدف:
--     subscriber | group | plan | card_batch         (محدّد، target = القيمة)
--     all_subscribers | all_hotspot | all_cards | all_pppoe  (شامل، target فارغ)
--     ip | mac                                        (طبقة منفصلة، target = العنوان)
--   duration_mode:
--     permanent     — حتى الرفع اليدوي
--     daily_window  — نافذة يومية متكرّرة [window_start, window_end] (تدعم العبور
--                     بعد منتصف الليل، مثل 16:00→08:00)
--     until         — حتى expires_at ثم ينتهي تلقائيًا
--   source: manual (يدوي) | auto (تلقائي من محاولات الفشل المتكرّرة).
CREATE TABLE IF NOT EXISTS access_blocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL DEFAULT 1,
    block_type    TEXT    NOT NULL,
    target        TEXT    NOT NULL DEFAULT '',   -- فارغ للنطاقات الشاملة
    reason        TEXT    NOT NULL DEFAULT '',
    duration_mode TEXT    NOT NULL DEFAULT 'permanent',
    window_start  TEXT    NOT NULL DEFAULT '',    -- 'HH:MM' لـ daily_window
    window_end    TEXT    NOT NULL DEFAULT '',    -- 'HH:MM'
    expires_at    TEXT    NOT NULL DEFAULT '',    -- ISO لـ until
    source        TEXT    NOT NULL DEFAULT 'manual',
    active        INTEGER NOT NULL DEFAULT 1,
    created_by    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT '',
    updated_at    TEXT    NOT NULL DEFAULT '',
    cleared_at    TEXT    NOT NULL DEFAULT '',
    cleared_by    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_access_blocks_tenant_active
    ON access_blocks (tenant_id, active, block_type);

-- (2) login_failure_tracker — عدّاد محاولات الدخول الفاشلة (fail2ban).
--   صفّ لكل محاولة فاشلة (IP + MAC + username + الوقت). يُقرأ ضمن نافذة
--   زمنية لتقرير الحظر التلقائي، ويُنظَّف دوريًا. tenant-scoped.
CREATE TABLE IF NOT EXISTS login_failure_tracker (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    ip          TEXT    NOT NULL DEFAULT '',
    mac         TEXT    NOT NULL DEFAULT '',
    username    TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_login_failure_ip
    ON login_failure_tracker (tenant_id, ip, created_at);
CREATE INDEX IF NOT EXISTS ix_login_failure_mac
    ON login_failure_tracker (tenant_id, mac, created_at);
