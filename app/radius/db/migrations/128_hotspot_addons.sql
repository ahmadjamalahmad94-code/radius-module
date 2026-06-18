-- 128_hotspot_addons.sql
-- إطار «الإضافات الاختيارية» لمصمّم صفحة الدخول (P1).
--
-- كل تصميم/قالب محفوظ يكتسب عمود addons_json: خريطة JSON
-- {addon_key: {"enabled": bool, "config": {...}}} تخزّن أي الإضافات
-- مفعّلة وإعداداتها. النموذج ذو السطحين (pre-login splash + post-login
-- redirect) والمحرّك يقرآن هذا العمود لحقن الأجزاء وتجميع نطاقات
-- walled-garden. الافتراضي '{}' فالتصاميم القائمة تعمل بلا تغيير.
--
-- يُضاف العمود لجدولي التصميم الحالي (per-router) والقوالب المحفوظة
-- (presets) كي تحمل الـpresets إعداد الإضافات أيضًا.

ALTER TABLE hotspot_designs
  ADD COLUMN addons_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE hotspot_design_presets
  ADD COLUMN addons_json TEXT NOT NULL DEFAULT '{}';
