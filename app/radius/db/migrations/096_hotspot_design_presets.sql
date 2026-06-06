-- 096 — «قوالب محفوظة» لمصمّم صفحة الدخول.
--
-- مكتبة مصغّرة لكل راوتر: المشغّل يحفظ مجموعة المتغيّرات الحالية
-- (بما فيها قوائم JSON للموزعين والعروض) باسم يختاره، ثم يعيد
-- تطبيقها لاحقًا دون إعادة إدخال. نفس فلسفة hotspot_designs لكن
-- بصفوف متعددة لكل (tenant, nas) مميّزة بالاسم.
--
--   name           — اسم القالب المحفوظ (يحدده المشغّل).
--   template_slug  — التصميم المختار وقت الحفظ.
--   variables_json — كل المتغيّرات (نص JSON) — تُفحص عبر
--                    validate_vars قبل الإدراج، وتبقى نصًا حتى لا
--                    تحتاج إضافة متغيّر جديد إلى migration آخر.

CREATE TABLE IF NOT EXISTS hotspot_design_presets (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL,
  nas_id          INTEGER NOT NULL,
  name            TEXT NOT NULL,
  template_slug   TEXT NOT NULL,
  variables_json  TEXT NOT NULL DEFAULT '{}',
  updated_at      TEXT NOT NULL DEFAULT '',
  UNIQUE (tenant_id, nas_id, name)
);
CREATE INDEX IF NOT EXISTS ix_hotspot_design_presets_nas
  ON hotspot_design_presets (tenant_id, nas_id);
