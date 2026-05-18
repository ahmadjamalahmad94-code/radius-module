-- 007_meta — api tokens, webhooks, notifications, scheduled tasks

CREATE TABLE api_tokens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL,
    name          TEXT NOT NULL,
    token_hash    TEXT NOT NULL,
    scopes_json   TEXT DEFAULT '[]',
    last_used_at  TEXT,
    expires_at    TEXT,
    revoked       INTEGER NOT NULL DEFAULT 0,
    created_by    INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_tok_tenant ON api_tokens(tenant_id);
CREATE UNIQUE INDEX idx_tok_hash ON api_tokens(token_hash);

CREATE TABLE webhook_subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    target_url      TEXT NOT NULL,
    secret          TEXT NOT NULL DEFAULT '',
    enabled_events_json TEXT DEFAULT '[]',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_wh_tenant ON webhook_subscriptions(tenant_id);

CREATE TABLE webhook_deliveries (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id             INTEGER NOT NULL,
    subscription_id       INTEGER NOT NULL,
    event                 TEXT NOT NULL,
    event_id              TEXT NOT NULL,
    payload_json          TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'queued',
    attempts              INTEGER NOT NULL DEFAULT 0,
    last_status_code      INTEGER DEFAULT 0,
    last_response_excerpt TEXT DEFAULT '',
    next_attempt_at       TEXT,
    created_at            TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscription_id) REFERENCES webhook_subscriptions(id) ON DELETE CASCADE
);
CREATE INDEX idx_wd_status ON webhook_deliveries(status, next_attempt_at);
CREATE INDEX idx_wd_tenant ON webhook_deliveries(tenant_id, created_at DESC);

CREATE TABLE notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL,
    admin_id    INTEGER NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'info',
    link        TEXT DEFAULT '',
    read_at     TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE
);
CREATE INDEX idx_notif_admin ON notifications(admin_id, read_at, created_at DESC);

CREATE TABLE scheduled_tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id      INTEGER NOT NULL,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL,
    cron_expr      TEXT NOT NULL,
    last_run_at    TEXT,
    next_run_at    TEXT,
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_sched_next ON scheduled_tasks(enabled, next_run_at);

-- Mikrotik connections per tenant (was MikrotikConfigStore in-memory)
CREATE TABLE mikrotik_configs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL,
    name          TEXT NOT NULL,
    host          TEXT NOT NULL,
    port          INTEGER NOT NULL DEFAULT 8728,
    username      TEXT NOT NULL DEFAULT 'admin',
    password      TEXT NOT NULL DEFAULT '',
    use_tls       INTEGER NOT NULL DEFAULT 0,
    verify_tls    INTEGER NOT NULL DEFAULT 1,
    timeout_sec   INTEGER NOT NULL DEFAULT 10,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_status   TEXT DEFAULT '',
    last_seen_at  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_mt_tenant ON mikrotik_configs(tenant_id);
