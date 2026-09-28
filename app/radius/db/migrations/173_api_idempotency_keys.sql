-- ════════════════════════════════════════════════════════════════════════
-- 173 — api_idempotency_keys: «ضغطتان = دفعتان» لم تعد ممكنة
--
-- حملة الضغط 2026-09-28: طلبا دفعٍ متطابقان (نقرٌ مزدوج، أو إعادة إرسالٍ بعد
-- مهلة) سجّلا دفعتين. نقاط المال في /api/v1 تقبل الآن ترويسة Idempotency-Key
-- (أو الحقل client_request_id): المفتاح نفسه خلال 24 ساعة يعيد النتيجة الأولى
-- دون كتابةٍ ثانية. الصفّ يُحجز «pending» قبل التنفيذ (قيد UNIQUE يمنع
-- التنفيذ المتوازي)، ويُحفظ الردّ عند النجاح، ويُحذف عند الفشل ليُعاد المحاولة.
-- ════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS api_idempotency_keys (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL DEFAULT 1,
    idem_key      TEXT NOT NULL,
    scope         TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',
    status_code   INTEGER,
    response_json TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (tenant_id, idem_key, scope)
);
CREATE INDEX IF NOT EXISTS ix_api_idempotency_created
    ON api_idempotency_keys (created_at);
