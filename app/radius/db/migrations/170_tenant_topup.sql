-- MT46 — لوحة شحن الشبكات (المزوّد).
--
-- رصيدٌ ماليّ لكل شبكة (يُتابعه المزوّد) + سجلّ حركات شحنٍ يُوثّق كل
-- عمليّة: رصيد، أيّام مدفوعة، أيّام مجانيّة، تمديد. التواريخ الفعليّة
-- (paid_until / trial_ends_at) تبقى على جدول tenants كما هي؛ هذا السجلّ
-- يُوثّق «من فعل ماذا ومتى» لا أكثر.
--
-- ⚠ جدولٌ على مستوى المنصّة لا الشبكة رغم عمود tenant_id: هو مِلك المزوّد
-- (سجلّ تحصيله من عملائه)، فيُستثنى من نسخ الشبكة الاحتياطيّة — انظر
-- _EXCLUDE في services/tenant_backup.py.

ALTER TABLE tenants ADD COLUMN credit_balance REAL NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS tenant_topup_ledger (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL,
  kind          TEXT    NOT NULL,          -- credit | paid_days | free_days
  amount        REAL    NOT NULL DEFAULT 0,   -- المبلغ (بعملة الشبكة)
  days          INTEGER NOT NULL DEFAULT 0,   -- الأيّام المُضافة
  balance_after REAL    NOT NULL DEFAULT 0,   -- الرصيد بعد الحركة (لقطة)
  note          TEXT    NOT NULL DEFAULT '',
  actor         TEXT    NOT NULL DEFAULT '',
  created_at    TEXT    NOT NULL DEFAULT '',
  CHECK (kind IN ('credit','paid_days','free_days'))
);

CREATE INDEX IF NOT EXISTS ix_topup_ledger_tenant
  ON tenant_topup_ledger (tenant_id, id DESC);
