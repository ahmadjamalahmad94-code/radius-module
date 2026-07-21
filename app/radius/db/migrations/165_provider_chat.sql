-- MT32 — شات بين لوحة المزوّد وكل شبكة (تواصل مع العملاء).
--
-- خيطٌ واحد لكل شبكة (tenant_id) — لا حاجة لجدول خيوط مستقلّ: الخيط هو
-- الشبكة نفسها. الرسائل طرفاها:
--   provider = مزوّد الاستضافة (المالك الرئيسي في لوحة المزوّد)
--   network  = مدير الشبكة (من داخل لوحته)
--
-- عزل: كل استعلام مُقيَّد بـtenant_id. الشبكة ترى خيطها فقط؛ والمزوّد يرى
-- الكل. لا يُدرَج هذا الجدول في النسخ الاحتياطية للشبكة (مراسلات مع
-- المزوّد لا بيانات تشغيل) — انظر _EXCLUDE في services/tenant_backup.py.

CREATE TABLE IF NOT EXISTS provider_chat_messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id   INTEGER NOT NULL,
  sender      TEXT    NOT NULL,              -- provider | network
  sender_name TEXT    NOT NULL DEFAULT '',   -- اسم المُرسِل للعرض
  body        TEXT    NOT NULL DEFAULT '',
  created_at  TEXT    NOT NULL DEFAULT '',
  read_by_provider INTEGER NOT NULL DEFAULT 0,
  read_by_network  INTEGER NOT NULL DEFAULT 0,
  CHECK (sender IN ('provider','network'))
);

CREATE INDEX IF NOT EXISTS ix_provider_chat_tenant
  ON provider_chat_messages (tenant_id, id);
CREATE INDEX IF NOT EXISTS ix_provider_chat_unread_provider
  ON provider_chat_messages (tenant_id, read_by_provider);
CREATE INDEX IF NOT EXISTS ix_provider_chat_unread_network
  ON provider_chat_messages (tenant_id, read_by_network);
