-- 097 — «تصاميم خاصة» مرفوعة لمصمّم صفحة الدخول.
--
-- يرفع المدير ملف HTML (أو ZIP يحوي login.html) كتصميم خاص به،
-- فيظهر في معرض التصاميم بجانب تصاميم المكتبة ويُعامل مثلها تمامًا
-- (معاينة / حفظ / نشر / تحميل حزمة ZIP) عبر slug خاص بالشكل
-- custom:<id>.
--
-- التخزين على مستوى المستأجر (tenant) لا الراوتر — التصميم المرفوع
-- مرة واحدة يصبح متاحًا لكل راوترات الحساب، مثل المكتبة المدمجة.
--
--   name        — اسم التصميم كما يظهر في المعرض (يحدده الرافع).
--   html        — مصدر الصفحة كاملًا. يُفحص قبل الإدراج:
--                 placeholders راوتر أو إس الإجبارية موجودة
--                 ($(link-login-only) و$(chap-id) و$(chap-challenge)
--                 و$(error))، وجود </body> (لحقن الإضافات)،
--                 والحجم ≤ 2MB. متغيّرات {{VARS}} اختيارية —
--                 إن وُجدت تُستبدل عند العرض، وإلا تُترك الصفحة كما هي.
--   updated_at  — UPSERT على (tenant_id, name): الرفع بنفس الاسم يحدّث.

CREATE TABLE IF NOT EXISTS hotspot_custom_templates (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id   INTEGER NOT NULL,
  name        TEXT NOT NULL,
  html        TEXT NOT NULL,
  updated_at  TEXT NOT NULL DEFAULT '',
  UNIQUE (tenant_id, name)
);
CREATE INDEX IF NOT EXISTS ix_hotspot_custom_templates_tenant
  ON hotspot_custom_templates (tenant_id);
