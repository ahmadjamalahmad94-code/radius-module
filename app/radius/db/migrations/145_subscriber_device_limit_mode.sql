-- 145 — تجاوز per-subscriber لسلوك «بلوغ حدّ الأجهزة المسموحة».
--
-- حقل نصّي صغير يَحمل سلوك المشترك عند بلوغ عدد الأجهزة المسموحة
-- (device_count): فارغ = استخدم الافتراض العام (tenant_settings:
-- billing.device_limit_mode، الافتراض «reject»)، «reject» = رفض الجلسة
-- الجديدة برسالة «بلغت الحد الأقصى من الجلسات»، «replace» = فصل أقدم
-- جلسة نشطة (CoA Disconnect) والسماح بالجديدة.
--
-- يُنفَّذ في policy_engine._check_concurrent عبر services/device_limit.py.
ALTER TABLE subscribers ADD COLUMN device_limit_mode TEXT NOT NULL DEFAULT '';
