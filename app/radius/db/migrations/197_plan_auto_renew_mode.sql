-- ════════════════════════════════════════════════════════════════════════
-- 197 — «تجديد تلقائي» صار نمطًا لكلّ باقة + سجلّ تجديدٍ لا يتكرّر
--
-- قرار المالك (2026-10-06): بدل مربّع auto_renew الصامت، نمطٌ واحد:
--   off      بدون (الافتراض)
--   debt     مسموح بالدين — يُجدَّد عند الانتهاء ويُسجَّل السعر دينًا على المشترك
--   balance  خصم من الرصيد المتاح — يُجدَّد فقط إن غطّى رصيدُه السعر، وإلّا يُترك
--            ينتهي ويُنبَّه المدراء
--   free     مجاني
-- القيم القديمة auto_renew=1 لم تكن تفعل شيئًا ⇒ تبدأ «بدون» (لا خصم مفاجئ).
--
-- plan_auto_renewals: مطالبةٌ واحدة لكلّ (مشترك، لحظة انتهاء) — المفتاح الأساسيّ
-- يمنع تجديد الفترة نفسها مرّتين حتى مع عمّالٍ متوازيين.
-- المصدر: app/radius/services/plan_lifecycle.py.
-- ════════════════════════════════════════════════════════════════════════
ALTER TABLE access_plans ADD COLUMN auto_renew_mode TEXT NOT NULL DEFAULT 'off';

CREATE TABLE IF NOT EXISTS plan_auto_renewals (
    tenant_id      INTEGER NOT NULL DEFAULT 1,
    subscriber_id  INTEGER NOT NULL,
    period_end     TEXT NOT NULL,
    username       TEXT NOT NULL DEFAULT '',
    plan_id        INTEGER,
    mode           TEXT NOT NULL DEFAULT '',
    result         TEXT NOT NULL DEFAULT 'pending',
    amount         REAL NOT NULL DEFAULT 0,
    currency       TEXT NOT NULL DEFAULT '',
    new_expire_at  TEXT,
    message        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    PRIMARY KEY (tenant_id, subscriber_id, period_end)
);

CREATE INDEX IF NOT EXISTS idx_plan_auto_renewals_user
    ON plan_auto_renewals(tenant_id, username);
