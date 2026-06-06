-- مختبر الدفع الإلكتروني — جلسات الدفع (checkouts) عبر مزوّدي الدفع.
-- الأساس المشترك لكل المزوّدين: المحاكاة (mock_wallet) اليوم، وبوابات
-- حقيقية لاحقًا (جوال باي / إي-سداد / بنك فلسطين / بال باي / لحظة) بعد
-- توقيع الاتفاقيات. المبالغ تُخزَّن بأصغر وحدة عملة (أغورة/سنت) كأعداد
-- صحيحة — amount_minor — لتفادي أخطاء الفاصلة العائمة في المال.
-- otp_hash: لا يُخزَّن رمز التحقق نصًا أبدًا — sha256 فقط (المحاكاة تكشف
-- الرمز في لوحة المختبر عبر metadata_json بوسم demo واضح).

CREATE TABLE IF NOT EXISTS payment_checkouts (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id            INTEGER NOT NULL,
  provider             TEXT    NOT NULL,
  reference            TEXT    NOT NULL,
  subscriber_username  TEXT,
  amount_minor         INTEGER NOT NULL,
  currency             TEXT    NOT NULL DEFAULT 'ILS',
  status               TEXT    NOT NULL DEFAULT 'pending',
  otp_hash             TEXT,
  otp_expires_at       TEXT,
  created_at           TEXT    NOT NULL,
  paid_at              TEXT,
  metadata_json        TEXT    NOT NULL DEFAULT '{}',
  CHECK(amount_minor > 0),
  CHECK(status IN ('pending', 'otp_sent', 'paid', 'failed', 'expired'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_checkouts_reference
  ON payment_checkouts (reference);

CREATE INDEX IF NOT EXISTS ix_payment_checkouts_tenant_status
  ON payment_checkouts (tenant_id, status, id DESC);

CREATE INDEX IF NOT EXISTS ix_payment_checkouts_tenant_created
  ON payment_checkouts (tenant_id, id DESC);
