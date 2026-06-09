-- المتجر المتقدّم: محافظ استلام المدير (إعدادات مركزية) + طلبات إيداع
-- الزبائن (دفع شبه آلي). الزبون يحوّل يدويًا لمحفظة المدير ويرفع وصلًا،
-- ثم يؤكّد المدير فيُضاف الرصيد آليًا (خدمة الرصيد) — لا حركة مال تلقائية.

-- قنوات/محافظ الاستلام التي يعرضها المتجر للزبون. كلها بيانات المدير
-- (مركزية للمستأجر): جوالي باي / بنك / PalPay مع رقم الحساب وQR.
CREATE TABLE IF NOT EXISTS store_payment_methods (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id      INTEGER NOT NULL DEFAULT 1,
  method         TEXT NOT NULL DEFAULT 'other',   -- jawaly_pay | bank | palpay | other
  label          TEXT NOT NULL DEFAULT '',        -- اسم العرض («جوالي باي»…)
  account_name   TEXT NOT NULL DEFAULT '',        -- اسم صاحب الحساب المستلِم
  account_number TEXT NOT NULL DEFAULT '',         -- الرقم/الآيبان المعروض للنسخ
  instructions   TEXT NOT NULL DEFAULT '',        -- تعليمات إضافية للزبون
  qr_image_path  TEXT NOT NULL DEFAULT '',         -- مسار صورة QR (اختياري) تحت uploads
  active         INTEGER NOT NULL DEFAULT 1,
  sort_order     INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_store_payment_methods_active
  ON store_payment_methods (tenant_id, active, sort_order, id);

-- طلبات الإيداع: الزبون يدّعي تحويلًا (طريقة + جواله + المرجع + اسم
-- المحوِّل + المبلغ + صورة الوصل). المدير يتحقّق ثم: confirmed (يُضاف
-- المبلغ المدّعى)، adjusted (يُضاف مبلغ مختلف فعلي)، أو rejected.
-- المبالغ بالوحدات الصغرى (minor) اتساقًا مع بقية المال في النظام.
CREATE TABLE IF NOT EXISTS deposit_requests (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  card_user_id           INTEGER NOT NULL,
  method                 TEXT NOT NULL DEFAULT '',   -- نسخة method وقت الطلب
  payment_method_id      INTEGER,                     -- مرجع store_payment_methods
  payer_phone            TEXT NOT NULL DEFAULT '',
  reference              TEXT NOT NULL DEFAULT '',    -- الرقم المرجعي للدفعة
  payer_name             TEXT NOT NULL DEFAULT '',
  amount_claimed_minor   INTEGER NOT NULL DEFAULT 0,
  receipt_image_path     TEXT NOT NULL DEFAULT '',
  status                 TEXT NOT NULL DEFAULT 'pending',
  confirmed_amount_minor INTEGER,                     -- المبلغ المُضاف فعليًا عند الحسم
  currency               TEXT NOT NULL DEFAULT '',
  admin_note             TEXT NOT NULL DEFAULT '',
  wallet_transaction_id  INTEGER,                     -- حركة الائتمان (أثر تدقيق + idempotency)
  created_at             TEXT NOT NULL,
  resolved_by            TEXT NOT NULL DEFAULT '',
  resolved_at            TEXT,
  CHECK (status IN ('pending','confirmed','adjusted','rejected'))
);

CREATE INDEX IF NOT EXISTS ix_deposit_requests_status
  ON deposit_requests (tenant_id, status, id DESC);
CREATE INDEX IF NOT EXISTS ix_deposit_requests_user
  ON deposit_requests (tenant_id, card_user_id, id DESC);
