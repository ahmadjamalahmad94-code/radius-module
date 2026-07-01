-- 153 — فصل سلوك «عدد الأجهزة المسموحة» بين الكروت والمشتركين.
--
-- قرار المالك: «طرد الجلسات أو الرفض، خليه منفصل للكروت والمشتركين». كان
-- المفتاح الموحَّد ``billing.device_limit_mode`` يُطبَّق على الجميع. الآن لكلّ
-- نوع حسابٍ إعدادٌ عامّ مستقلّ:
--   • مشتركون: ``device_limit.subscribers.mode`` + ``device_limit.subscribers.count``
--   • كروت:    ``device_limit.cards.mode``       + ``device_limit.cards.count``
-- ويَتجاوزها التجاوز الفرديّ (subscribers.device_limit_mode للمشترك،
-- card_batches.device_limit_mode للدفعة).
--
-- (1) تجاوز per-batch لسلوك الكروت — نظير subscribers.device_limit_mode
--     (migration 145). فارغ = اتبع الإعداد العام للكروت.
ALTER TABLE card_batches ADD COLUMN device_limit_mode TEXT NOT NULL DEFAULT '';

-- (2) الحفاظ على السلوك الحاليّ: انسخ القيمة الموحَّدة القديمة (إن ضُبطت لأيّ
--     مستأجر) إلى المفتاحين المُنفصلين، فلا يَتغيّر شيء عند الترقية. idempotent
--     (NOT EXISTS يمنع التكرار لو أُعيد التشغيل).
INSERT INTO tenant_settings (tenant_id, key, value, updated_by, updated_at)
SELECT s.tenant_id, 'device_limit.subscribers.mode', s.value, 0, datetime('now')
  FROM tenant_settings s
 WHERE s.key = 'billing.device_limit_mode'
   AND NOT EXISTS (
       SELECT 1 FROM tenant_settings t
        WHERE t.tenant_id = s.tenant_id
          AND t.key = 'device_limit.subscribers.mode');

INSERT INTO tenant_settings (tenant_id, key, value, updated_by, updated_at)
SELECT s.tenant_id, 'device_limit.cards.mode', s.value, 0, datetime('now')
  FROM tenant_settings s
 WHERE s.key = 'billing.device_limit_mode'
   AND NOT EXISTS (
       SELECT 1 FROM tenant_settings t
        WHERE t.tenant_id = s.tenant_id
          AND t.key = 'device_limit.cards.mode');
