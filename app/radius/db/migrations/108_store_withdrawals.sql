-- المتجر المتقدّم: طلبات سحب الرصيد (ثقة الزبون). الزبون يطلب سحبًا
-- (اسمه + رقم الحساب الذي نحوّل إليه + المبلغ). المدير ينفّذ التحويل
-- يدويًا ثم يؤكّد فيُخصم الرصيد آليًا (خدمة الرصيد، لا سحب أكثر من
-- الرصيد، idempotent) — لا حركة مال تلقائية قبل تأكيد المدير.

CREATE TABLE IF NOT EXISTS withdrawal_requests (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id             INTEGER NOT NULL DEFAULT 1,
  card_user_id          INTEGER NOT NULL,
  payee_name            TEXT NOT NULL DEFAULT '',     -- اسم صاحب الحساب المستلِم
  payee_account         TEXT NOT NULL DEFAULT '',     -- رقم الحساب الذي نحوّل إليه
  method                TEXT NOT NULL DEFAULT '',     -- قناة التحويل (اختياري)
  amount_minor          INTEGER NOT NULL DEFAULT 0,
  currency              TEXT NOT NULL DEFAULT '',
  status                TEXT NOT NULL DEFAULT 'pending',
  admin_note            TEXT NOT NULL DEFAULT '',
  wallet_transaction_id INTEGER,                       -- حركة الخصم (أثر تدقيق + idempotency)
  created_at            TEXT NOT NULL,
  resolved_by           TEXT NOT NULL DEFAULT '',
  resolved_at           TEXT,
  CHECK (status IN ('pending','confirmed','rejected'))
);

CREATE INDEX IF NOT EXISTS ix_withdrawal_requests_status
  ON withdrawal_requests (tenant_id, status, id DESC);
CREATE INDEX IF NOT EXISTS ix_withdrawal_requests_user
  ON withdrawal_requests (tenant_id, card_user_id, id DESC);
