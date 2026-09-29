-- ════════════════════════════════════════════════════════════════════════
-- 179 — subscriber_quota_state (was 178 on agent/fix2-plansreports; renumbered
--        at integration: money's 178_api_idempotency_request_hash took 178): فترة الكوتة وإضافاتها (إعادة اختبار R04 N6)
--
-- كانت «إضافة كوتة» تُكتب سقفًا دائمًا على المشترك (1024 + 100 = 1124) فيبقى
-- بعد الترقية إلى 20 GB، وبعد الانتقال لباقةٍ بلا كوتة، وبعد التجديد. وكان
-- الاستهلاك يُعدّ من radacct «منذ الأزل» فلا يعيد التجديدُ الكوتة، ولا تُعيد
-- «استعادة الكوتة اليوميّة» شيئًا. هذا الجدول يحفظ لكلّ مشترك:
--   • بداية الفترة الحاليّة (تغيير العرض / التجديد) + خطّ أساس البايتات عندها،
--   • آخر «استعادة يوميّة» + خطّ أساسها،
--   • إضافات الكوتة ضمن الفترة (قيمة التجاوز قبل أوّل إضافة وبعد آخرها لتُزال
--     عند بداية فترةٍ جديدة)، وإضافات اليوم/الشهر لكوتات الباقة اليوميّة/الشهريّة.
-- المصدر: app/radius/services/quota_period.py
-- ════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS subscriber_quota_state (
    tenant_id        INTEGER NOT NULL DEFAULT 1,
    subscriber_id    INTEGER NOT NULL,
    period_start     TEXT,
    period_base_in   INTEGER NOT NULL DEFAULT 0,
    period_base_out  INTEGER NOT NULL DEFAULT 0,
    period_reason    TEXT NOT NULL DEFAULT '',
    daily_reset_at   TEXT,
    daily_base_in    INTEGER NOT NULL DEFAULT 0,
    daily_base_out   INTEGER NOT NULL DEFAULT 0,
    topup_mb         INTEGER NOT NULL DEFAULT 0,
    topup_before     TEXT,
    topup_after      TEXT,
    window_topups    TEXT NOT NULL DEFAULT '{}',
    updated_at       TEXT,
    PRIMARY KEY (tenant_id, subscriber_id)
);
