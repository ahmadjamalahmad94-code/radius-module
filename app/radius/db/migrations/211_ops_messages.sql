-- 211_ops_messages — the model-facing transcript of an operations-assistant
-- conversation (web chat, docs/OPS_EXECUTOR.md «Web chat & model wiring»).
--
-- One row per message exactly as it is sent to the model: role user /
-- assistant (one JSON proposal) / tool (CHOICES … / RESULT …). Built by the
-- app, never by the browser. Never contains a show_once password or a card
-- code: RESULT lines are the executor's redacted ``model_result``.
-- Reads are keyed by (conversation_id, tenant_id) of a conversation that was
-- already resolved for the CURRENT admin (ops_conversations).

CREATE TABLE IF NOT EXISTS ops_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    tenant_id       INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ops_messages_conv ON ops_messages(conversation_id, id);
