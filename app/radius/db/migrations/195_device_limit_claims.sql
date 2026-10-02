-- ════════════════════════════════════════════════════════════════════════
-- 195 — device_limit_claims: حجزُ مقعدِ جهازٍ لحظةَ Access-Accept (zero-w1 L1)
--
-- حدُّ الأجهزة يُعدّ من جلسات radacct المفتوحة، وبين Access-Accept وAcct-Start
-- نافذةٌ (~ثانية) يمرّ فيها كلُّ متزامن: بطاقةٌ حدُّها جهازٌ واحد قُبلت من 8
-- أجهزةٍ في أوّل دخول (round 6: 30/30). الآن يُحجز المقعدُ ذرّيًّا (BEGIN
-- IMMEDIATE: عدُّ الجلسات الحيّة + الحجوزات الحديثة ثمّ الإدراج) قبل القبول؛
-- الحجزُ يسقط بعد مهلةٍ قصيرة أو حين تظهر جلسةُ الجهاز نفسه في radacct.
-- المصدر: app/radius/services/device_limit.py (claim_slot).
-- ════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS device_limit_claims (
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    username    TEXT NOT NULL,
    device_key  TEXT NOT NULL,
    claimed_at  TEXT NOT NULL,
    PRIMARY KEY (tenant_id, username, device_key)
);

CREATE INDEX IF NOT EXISTS idx_device_limit_claims_at
    ON device_limit_claims(claimed_at);
