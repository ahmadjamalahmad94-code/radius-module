-- MT37 — كبح تخمين كلمات المرور على تسجيل الدخول.
--
-- كان النظام يُسجّل محاولات الدخول ولا يَحدّها: مهاجمٌ يُجرّب بلا سقف.
-- هذا الجدول يَحمل عدّاد الإخفاق لكل «نطاق» (عنوان IP حاليًّا) ووقت
-- انتهاء الحجب، فيَصمد القيد عبر إعادة تشغيل الخدمة — بخلاف عدّادٍ في
-- الذاكرة يُصفّره المهاجم بإسقاط العملية.
--
-- السياسة (قرار المالك 2026-07-21): تأخير تصاعديّ بلا قفل حساب دائم.
-- بعد N إخفاقات يُحجب النطاق مدّةً تتضاعف مع كل جولة حجب، بسقف. لم
-- نَقفل الحساب باسمه عمدًا: مهاجمٌ يعرف اسم المالك يستطيع عندها حبسه
-- خارج لوحته متى شاء (حجب خدمة).
--
-- ⚠ جدول منصّة لا جدول شبكة: لا عمود tenant_id فيه، فلا يلتقطه كشف
-- جداول الشبكة في tenant_backup.

CREATE TABLE IF NOT EXISTS login_throttle (
  scope         TEXT    PRIMARY KEY,          -- 'ip:<addr>'
  fail_count    INTEGER NOT NULL DEFAULT 0,   -- إخفاقات الجولة الحالية
  block_level   INTEGER NOT NULL DEFAULT 0,   -- كم مرّة حُجب من قبل (يُضاعف المدّة)
  blocked_until TEXT    NOT NULL DEFAULT '',  -- ISO؛ فارغ = غير محجوب
  last_fail_at  TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_login_throttle_blocked
  ON login_throttle (blocked_until);
