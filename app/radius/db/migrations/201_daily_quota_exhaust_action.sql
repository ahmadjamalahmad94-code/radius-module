-- ════════════════════════════════════════════════════════════════════════
-- 201 — «ماذا يحدث بعد نفاد الكوتة اليوميّة؟» قرارٌ للباقة وللمشترك
--
-- الكوتة اليوميّة نفسُها مُنفَّذةٌ منذ quota_period (نوافذُ محلّيّة بخطّ
-- أساس، تتجدّد منتصفَ الليل بتوقيت المستأجر). الناقصُ كان **القرار**:
-- النفادُ يَرفض المصادقةَ دائمًا، ولا يملك المشغّلُ إلّا علمَ دفعةِ
-- البطاقات `on_quota_exhaust` (للبطاقات فقط، وبسرعةِ تخفيفٍ عامّةٍ
-- للمستأجر كلِّه، بلا رقمٍ لهذه الباقة أو ذاك المشترك).
--
-- الآن ثلاثةُ قرارات على الباقة، وتجاوزٌ فرديٌّ على المشترك:
--   disconnect   (الافتراضُ = السلوكُ التاريخيّ: رفضٌ حتّى منتصفِ الليل)
--   reduce_speed (سماحٌ بسرعةِ التخفيف العامّة)
--   custom_speed (سماحٌ بسرعةٍ يكتبها المشغّل بالميجابت — تُخزَّن كيلوبت)
--
-- 'on_quota_exhaust' لدفعات البطاقات يبقى كما هو ويَغلب عند تعارضٍ، فلا
-- يتغيّر سلوكُ بطاقةٍ قائمة.
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE access_plans ADD COLUMN daily_quota_action TEXT NOT NULL DEFAULT 'disconnect';
ALTER TABLE access_plans ADD COLUMN daily_quota_speed_down_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN daily_quota_speed_up_kbps INTEGER NOT NULL DEFAULT 0;

-- '' = اتبع الباقة
ALTER TABLE subscribers ADD COLUMN daily_quota_action TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN daily_quota_speed_down_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN daily_quota_speed_up_kbps INTEGER NOT NULL DEFAULT 0;
-- حالةُ الإنفاذ الحيّ: '' طبيعيّ · 'throttled' مخفَّض (كي لا يتكرّر CoA)
ALTER TABLE subscribers ADD COLUMN daily_quota_state TEXT NOT NULL DEFAULT '';
