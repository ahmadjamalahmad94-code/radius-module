-- 165 — TR-069: الاكتشاف التلقائيّ (Zero-touch) + الربط المسبق بالسيريال.
--
-- يكمّل نمط التسجيل بالرمز (164) بمسار «بلا لمس»: أي راوتر يتّصل بـ GenieACS
-- عبر عنوان ACS المشترك يظهر تلقائيًّا كجهاز «مكتشَف» (origin='discovered')،
-- ثم يُربط بمشترك — يدويًّا، أو تلقائيًّا إن سبق تسجيل سيريال الراوتر في
-- جدول الربط المسبق. additive فقط، tenant-scoped.

-- منشأ الجهاز: enrollment (رمز لكل جهاز) | discovered (بلا لمس، سجّل نفسه).
ALTER TABLE tr069_devices ADD COLUMN origin TEXT NOT NULL DEFAULT 'enrollment';

-- حالة الإنترنت خلف الراوتر (WAN/PPP) — مستقلّة عن اتصال الراوتر بـ ACS:
--   up = الراوتر متصل بـ ACS وله إنترنت · down = متصل بـ ACS لكن لا إنترنت
--   unknown = غير معروف/الراوتر مفصول عن ACS.
ALTER TABLE tr069_devices ADD COLUMN internet_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE tr069_devices ADD COLUMN wan_status TEXT NOT NULL DEFAULT '';
-- طوابع تغيّر الحالة (لكشف الانتقالات وإطلاق التنبيهات مرّة واحدة).
ALTER TABLE tr069_devices ADD COLUMN last_online_change_at TEXT;
ALTER TABLE tr069_devices ADD COLUMN last_internet_change_at TEXT;

-- ═══ الربط المسبق: سيريال → مشترك (يُطبَّق آليًّا عند أوّل اتصال) ═══
CREATE TABLE IF NOT EXISTS tr069_serial_bindings (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  serial_number   TEXT NOT NULL,
  radius_username TEXT NOT NULL DEFAULT '',            -- اسم PPPoE/RADIUS المراد ربطه
  subscriber_id   INTEGER,                             -- المشترك (اختياريّ)
  owner_admin_id  INTEGER,                             -- المالك المسؤول (للعزل)
  note            TEXT NOT NULL DEFAULT '',
  created_by      TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  updated_at      TEXT,
  deleted_at      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tr069_serial_bindings
  ON tr069_serial_bindings (tenant_id, serial_number)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_tr069_serial_bindings_owner
  ON tr069_serial_bindings (tenant_id, owner_admin_id);
