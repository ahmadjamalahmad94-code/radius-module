-- 116_primary_admin_super_backfill.sql
-- أمان وصول (إصلاح Broken Access Control): «المدير الرئيسي = وصول كامل دائماً».
--
-- المالك/المدير الرئيسي = أصغر معرّف admin غير محذوف. لو أُلغي علمه
-- is_super_admin سهوًا (تعديل يدوي أو مزامنة ترخيص جعلته non-owner) يُحجب
-- المالك عن أقسامه بعد تفعيل الحراسة الخادمية، ويصبح خارج الحوكمة. نعيد
-- ضبط العلم له هنا دفاعًا في العمق (إضافةً للحلّ على مستوى الجلسة في
-- auth/session_helpers._resolve_is_super).
--
-- idempotent:
--   * لا كتابة إن كان المالك super أصلًا (الحالة الطبيعية بعد البذر).
--   * لا صفّ مستهدف على قاعدة جديدة فارغة — البذر لاحقًا يُنشئ المالك
--     super من الأساس، فالـ MIN(id) هنا = NULL ولا يطابق شيئًا.
UPDATE admins
   SET is_super_admin = 1
 WHERE id = (SELECT MIN(id) FROM admins WHERE deleted_at IS NULL)
   AND is_super_admin <> 1;
