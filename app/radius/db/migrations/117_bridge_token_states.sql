-- Customer-side bridge-token state table.
-- One active row per tenant; the token value is ALWAYS stored Fernet-encrypted.
-- Never insert raw token values — use BridgeTokenSyncService exclusively.
CREATE TABLE IF NOT EXISTS bridge_token_states (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INT     NOT NULL DEFAULT 1,
    -- 'panel'  — token received from panel via runtime-contract pull
    -- 'local'  — token minted here and reported to the panel
    source      TEXT    NOT NULL DEFAULT 'local',
    -- Fernet ciphertext; empty string = no token established yet
    token_enc   TEXT    NOT NULL DEFAULT '',
    -- Last 4 chars of the plaintext only — safe for log lines
    token_hint  TEXT    NOT NULL DEFAULT '',
    -- Panel sequence/version tag received with the token; used for dedup
    panel_seq   TEXT    NOT NULL DEFAULT '',
    -- ISO-8601 UTC timestamp when the token was generated/issued
    issued_at   TEXT,
    -- ISO-8601 UTC timestamp of last successful panel report; NULL = not yet
    reported_at TEXT,
    -- 1 after the panel responded ok=true to our local-token report
    panel_acked INTEGER NOT NULL DEFAULT 0,
    -- 1 = currently active; 0 = superseded by a newer row
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bridge_token_states_tenant_active
    ON bridge_token_states (tenant_id, active, id DESC);
