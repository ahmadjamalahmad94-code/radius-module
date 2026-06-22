-- ════════════════════════════════════════════════════════════════════════
-- ربط تيليجرام بضغطة واحدة (feat/telegram-one-click-connect)
-- يستبدل خطوة «انسخ معرّف المحادثة يدويًّا»: المستخدم يضغط «اربط تيليجرام»،
-- يمسح QR أو يضغط الرابط العميق، يضغط START في تيليجرام، فيلتقط الخادم
-- chat_id تلقائيًّا عبر getUpdates ويُخزّنه في الربط (إدارة) أو على ملف
-- المشترك (بوّابة المشترك). إنشاء البوت (BotFather→توكن) يبقى لمرّة واحدة.
--
-- telegram_link_codes:   رمز ربط لمرّة واحدة (كود قصير) مربوط بالمستأجر،
--                        وللمشتركين بمعرّف المشترك. status: pending→linked|expired.
-- telegram_poll_state:   إزاحة getUpdates لكل بوت (مستأجر) كي لا تتكرّر
--                        معالجة التحديثات بين نوافذ الربط المتعاقبة.
-- subscribers.telegram_*: chat_id + اسم الحساب المُلتقط لكل مشترك (Phase 2 hook).
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS telegram_link_codes (
  code           TEXT PRIMARY KEY,                 -- رمز قصير عشوائي (آمن للروابط)
  tenant_id      INTEGER NOT NULL DEFAULT 1,
  target         TEXT NOT NULL DEFAULT 'admin',    -- admin (ربط المستأجر) | subscriber
  subscriber_id  INTEGER NOT NULL DEFAULT 0,       -- لهدف المشترك فقط
  status         TEXT NOT NULL DEFAULT 'pending',  -- pending | linked | expired | cancelled
  chat_id        TEXT NOT NULL DEFAULT '',         -- مُلتقط عند الربط
  account_name   TEXT NOT NULL DEFAULT '',         -- اسم/معرّف الحساب المتّصل (للعرض)
  created_at     TEXT NOT NULL DEFAULT '',
  expires_at     TEXT NOT NULL DEFAULT '',         -- نهاية نافذة الربط (ISO)
  linked_at      TEXT NOT NULL DEFAULT '',
  CHECK (target IN ('admin','subscriber')),
  CHECK (status IN ('pending','linked','expired','cancelled'))
);

-- البحث عن الرموز المعلّقة لمستأجر أثناء معالجة دفعة getUpdates.
CREATE INDEX IF NOT EXISTS ix_telegram_link_codes_pending
  ON telegram_link_codes (tenant_id, status);

-- إزاحة getUpdates لكل بوت/مستأجر (offset = last_update_id + 1).
CREATE TABLE IF NOT EXISTS telegram_poll_state (
  tenant_id       INTEGER PRIMARY KEY,
  last_update_id  INTEGER NOT NULL DEFAULT 0,
  updated_at      TEXT NOT NULL DEFAULT ''
);

-- chat_id لكل مشترك (هدف المشترك). SQLite بلا ADD COLUMN IF NOT EXISTS،
-- وهذه الأعمدة جديدة على هذا الفرع فالإضافة آمنة لمرّة واحدة عبر الـrunner.
ALTER TABLE subscribers ADD COLUMN telegram_chat_id      TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN telegram_account_name TEXT NOT NULL DEFAULT '';
