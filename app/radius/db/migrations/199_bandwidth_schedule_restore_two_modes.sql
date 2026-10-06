-- ════════════════════════════════════════════════════════════════════════
-- 199 — «طريقة الرجوع» لجدول السرعة: خياران فقط
--
-- قرار المالك (متابعة 2026-10-06):
--   profile_default  «رجوع مباشر بدون فصل» — عند نهاية النافذة تُدفع السرعة
--                    الفعّالة للجلسة الحيّة (CoA) بلا فصل
--   disconnect       «فصل الجلسة» — تُفصل الجلسة فتعود بالمصادقة على سرعتها
-- كلّ قيمةٍ أخرى مخزّنة (keep_current «إبقاء آخر سرعة»، previous_value، manual،
-- فارغ…) لم يُطبّقها شيء ⇒ تصير «رجوع مباشر». ممتنع التكرار.
-- المُنفِّذ: app/radius/services/bandwidth_apply.apply_schedule_users_live.
-- ════════════════════════════════════════════════════════════════════════
UPDATE bandwidth_schedules
   SET restore_mode = CASE WHEN lower(trim(restore_mode)) = 'disconnect'
                           THEN 'disconnect' ELSE 'profile_default' END
 WHERE restore_mode IS NULL
    OR restore_mode NOT IN ('profile_default', 'disconnect');
