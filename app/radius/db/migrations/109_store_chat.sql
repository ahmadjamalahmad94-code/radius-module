-- المتجر المتقدّم: شات خفيف بين الزبون (صفحة المتجر) والمدير (لوحة
-- سوق البطاقات). رسائل نصية + إرفاق صورة (وصل/مشكلة). التحديث بـ
-- polling خفيف (لا websockets) عبر الاستعلام بمعرّف آخر رسالة.

CREATE TABLE IF NOT EXISTS store_chat_messages (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id        INTEGER NOT NULL DEFAULT 1,
  card_user_id     INTEGER NOT NULL,
  sender           TEXT NOT NULL,                -- customer | admin
  body             TEXT NOT NULL DEFAULT '',
  image_path       TEXT NOT NULL DEFAULT '',     -- صورة مرفقة (اختياري) تحت uploads
  admin_actor      TEXT NOT NULL DEFAULT '',     -- اسم المدير المرسِل (للرسائل الإدارية)
  read_by_admin    INTEGER NOT NULL DEFAULT 0,
  read_by_customer INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  CHECK (sender IN ('customer','admin'))
);

CREATE INDEX IF NOT EXISTS ix_store_chat_thread
  ON store_chat_messages (tenant_id, card_user_id, id);
CREATE INDEX IF NOT EXISTS ix_store_chat_unread_admin
  ON store_chat_messages (tenant_id, read_by_admin, id);
