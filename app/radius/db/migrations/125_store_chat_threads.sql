-- شات المتجر: حالة المحادثة على مستوى الخيط (لا الرسالة) + ذاكرة التذكير.
-- يلزمها تذكير «بانتظار ردّ»: «مُجاب» = ردّ المدير (رسالة admin) أو ضبط حالة
-- (resolved) من المدير. لا يوجد عمود حالة على مستوى الخيط في migration 109
-- (الذي يحمل read_by_admin لكل رسالة فقط)، فنُضيفه هنا.
--
--   status      open | resolved   (افتراضي open؛ رسالة زبون جديدة تُعيده open)
--   status_by   اسم المدير الذي ضبط الحالة
--   status_at   وقت الضبط
--   reminded_at آخر وقت أُرسل فيه تذكير «بانتظار ردّ» لهذا الخيط (إزالة تكرار
--               التذكير: لا نُذكّر مجددًا إلا برسالة زبون أحدث من هذا الوقت)

CREATE TABLE IF NOT EXISTS store_chat_threads (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL DEFAULT 1,
  card_user_id  INTEGER NOT NULL,
  status        TEXT NOT NULL DEFAULT 'open',
  status_by     TEXT NOT NULL DEFAULT '',
  status_at     TEXT NOT NULL DEFAULT '',
  reminded_at   TEXT NOT NULL DEFAULT '',
  updated_at    TEXT NOT NULL DEFAULT '',
  UNIQUE (tenant_id, card_user_id),
  CHECK (status IN ('open','resolved'))
);

CREATE INDEX IF NOT EXISTS ix_store_chat_threads_status
  ON store_chat_threads (tenant_id, status);
