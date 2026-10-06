-- 210_ops_assistant — deterministic executor of the operations assistant
-- (docs/OPS_EXECUTOR.md). The model never writes here; the executor does.
--
--   ops_conversations     one per assistant conversation, bound to ONE tenant
--                         and ONE admin (the token's admin) — never shared.
--   ops_issued_choices    every id/username the executor showed in a CHOICES
--                         list (or a level-4 event) of that conversation; a
--                         proposal may only reference these.
--   ops_proposals         validated proposals awaiting / after confirmation;
--                         proposal_json never contains secrets (forbidden keys
--                         are rejected before storage), result_json is redacted.

CREATE TABLE IF NOT EXISTS ops_conversations (
    id          TEXT PRIMARY KEY,
    tenant_id   INTEGER NOT NULL,
    admin_id    INTEGER NOT NULL,
    event_json  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ops_conv_owner ON ops_conversations(tenant_id, admin_id);

CREATE TABLE IF NOT EXISTS ops_issued_choices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    tenant_id       INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    value           TEXT NOT NULL,
    source          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (conversation_id, kind, value)
);
CREATE INDEX IF NOT EXISTS idx_ops_choices_conv ON ops_issued_choices(conversation_id);

CREATE TABLE IF NOT EXISTS ops_proposals (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    tenant_id        INTEGER NOT NULL,
    admin_id         INTEGER NOT NULL,
    action           TEXT NOT NULL,
    mode             TEXT NOT NULL DEFAULT 'execute',
    proposal_json    TEXT NOT NULL,
    proposal_hash    TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    confirmed_at     TEXT,
    result_json      TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ops_prop_conv ON ops_proposals(conversation_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ops_prop_idem ON ops_proposals(tenant_id, idempotency_key);
