-- 102 — رسائل أخطاء صفحة الهوتسبوت (errors.txt الخاص بالميكروتك).
--
-- الميكروتك يولّد ملف hotspot/errors.txt يربط كل «مفتاح خطأ» داخلي
-- (مثل invalid-username) برسالة تُعرض للمشترك في صفحة الدخول مكان
-- $(error). هذا الجدول يخزّن النصوص العربية القابلة للتحرير لكل
-- مفتاح، فيبنى منها errors.txt ويُرفع للراوتر عند نشر صفحة الدخول.
--
--   tenant_id   — المستأجر (عزل متعدد المستأجرين).
--   router_id   — 0 = الافتراضي العام للمستأجر (المستخدم في v1).
--                 محجوز لتجاوز لكل راوتر مستقبلًا (>0). نستخدم 0 بدل
--                 NULL لأن SQLite يعتبر NULLs متمايزة في فهارس
--                 UNIQUE فيكسر الـ UPSERT على الصف العام.
--   error_key   — مفتاح الخطأ القياسي في راوتر أو إس
--                 (invalid-username, uptime-limit, ...).
--   message_ar  — النص المعروض للمشترك (يقبل متغيّرات راوتر أو إس
--                 مثل $(error-orig) و $(ip) و $(username)).
--   enabled     — 1 = يُكتب في errors.txt؛ 0 = يُترك للنص الأصلي
--                 من الراوتر (لا يُكتب السطر إطلاقًا).
--   updated_at  — ختم آخر تعديل (ISO-8601).
--
-- التهيئة الأولية: تُزرع المفاتيح القياسية بنصوص عربية افتراضية
-- جيّدة من repos/services عند أول فتح للصفحة (seed_defaults) بدل
-- INSERT ثابت هنا — فإضافة مفتاح جديد لا تحتاج migration آخر.

CREATE TABLE IF NOT EXISTS hotspot_error_messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id   INTEGER NOT NULL,
  router_id   INTEGER NOT NULL DEFAULT 0,
  error_key   TEXT NOT NULL,
  message_ar  TEXT NOT NULL DEFAULT '',
  enabled     INTEGER NOT NULL DEFAULT 1,
  updated_at  TEXT NOT NULL DEFAULT '',
  UNIQUE (tenant_id, router_id, error_key)
);

CREATE INDEX IF NOT EXISTS ix_hotspot_error_messages_tenant
  ON hotspot_error_messages (tenant_id, router_id);
