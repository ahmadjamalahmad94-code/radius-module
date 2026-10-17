-- ════════════════════════════════════════════════════════════════════════
-- 173 — الكوتة اليوميّة: سقفٌ يوميٌّ يتجدّد منتصفَ الليل + ماذا يحدث بعده
--
-- حقول الكوتة اليوميّة على الباقة (daily_*_quota_mb) كانت **مخزَّنةً ولا
-- يقرأها المحرّك**: تُكتب في النموذج وتظهر في القائمة ولا تُنفَّذ أبدًا.
-- هذه الهجرة تُكمل العقد:
--   • سقفٌ يوميّ على الباقة وتجاوزٌ على المشترك.
--   • قرارُ ما بعد النفاد: قطع / تخفيف لسرعةٍ مخفَّضة / سرعةٌ مخصّصة.
--   • عدّادُ اليوم على صفّ المشترك مع ختمِ يومه، فالتجديد يقع من نفسه
--     عند أوّل حركةٍ بعد منتصف الليل المحلّيّ — بلا مهمّةٍ مجدولة تنسى.
--
-- اليوم محليٌّ لا UTC (المستأجر قد يكون Asia/Gaza) — يُحسب بـ
-- system_config.local_now(tenant_id).
-- ════════════════════════════════════════════════════════════════════════

-- ── الباقة: القرار بعد النفاد ──
ALTER TABLE access_plans ADD COLUMN daily_quota_action TEXT NOT NULL DEFAULT 'disconnect';
ALTER TABLE access_plans ADD COLUMN daily_quota_speed_down_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN daily_quota_speed_up_kbps INTEGER NOT NULL DEFAULT 0;

-- ── المشترك: تجاوزٌ فرديّ (0/'' = اتبع الباقة) ──
ALTER TABLE subscribers ADD COLUMN daily_quota_mb INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN daily_quota_action TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN daily_quota_speed_down_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN daily_quota_speed_up_kbps INTEGER NOT NULL DEFAULT 0;

-- ── عدّاد اليوم (يتجدّد ذاتيًّا بتغيّر الختم) ──
ALTER TABLE subscribers ADD COLUMN daily_used_bytes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN daily_usage_day TEXT NOT NULL DEFAULT '';
-- حالة الإنفاذ الحاليّة: '' طبيعيّ · 'throttled' مخفَّض · 'blocked' مقطوع
ALTER TABLE subscribers ADD COLUMN daily_quota_state TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_subs_daily_quota
    ON subscribers(tenant_id, daily_usage_day);
