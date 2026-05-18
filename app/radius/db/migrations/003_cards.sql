-- 003_cards — card batches + individual cards

CREATE TABLE card_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    batch_code      TEXT NOT NULL,
    package_name    TEXT DEFAULT '',
    plan_id         INTEGER NOT NULL,
    count           INTEGER NOT NULL DEFAULT 0,
    generated       INTEGER NOT NULL DEFAULT 0,
    used            INTEGER NOT NULL DEFAULT 0,
    price_per_card  REAL DEFAULT 0,
    price_bulk      REAL DEFAULT 0,
    total_quota_mb  INTEGER DEFAULT 0,
    username_prefix TEXT DEFAULT '',
    username_suffix TEXT DEFAULT '',
    username_length INTEGER DEFAULT 8,
    include_batch_number INTEGER NOT NULL DEFAULT 0,
    password_length INTEGER DEFAULT 6,
    password_charset TEXT DEFAULT 'digits',
    expire_at       TEXT,
    validity_after_first_login_days INTEGER DEFAULT 0,
    count_by_seconds INTEGER NOT NULL DEFAULT 0,
    count_from_first_connect INTEGER NOT NULL DEFAULT 1,
    on_quota_exhaust TEXT DEFAULT 'stop',
    switch_to_mac_on_connect INTEGER NOT NULL DEFAULT 0,
    lock_to_mac_on_close INTEGER NOT NULL DEFAULT 0,
    phone_only_login INTEGER NOT NULL DEFAULT 0,
    service_name    TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    manager_id      INTEGER DEFAULT 0,
    created_by      TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES access_plans(id) ON DELETE RESTRICT
);
CREATE INDEX idx_batch_tenant ON card_batches(tenant_id);
CREATE UNIQUE INDEX idx_batch_unique ON card_batches(tenant_id, batch_code);

CREATE TABLE cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    batch_id        INTEGER NOT NULL,
    username        TEXT NOT NULL,
    password        TEXT NOT NULL,
    plan_id         INTEGER NOT NULL,
    used            INTEGER NOT NULL DEFAULT 0,
    first_used_at   TEXT,
    used_by_mac     TEXT DEFAULT '',
    used_by_subscriber_id INTEGER,
    expire_at       TEXT,
    revoked         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (batch_id) REFERENCES card_batches(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES access_plans(id) ON DELETE RESTRICT,
    FOREIGN KEY (used_by_subscriber_id) REFERENCES subscribers(id) ON DELETE SET NULL
);
CREATE INDEX idx_cards_tenant ON cards(tenant_id);
CREATE INDEX idx_cards_batch ON cards(batch_id);
CREATE INDEX idx_cards_used ON cards(tenant_id, used);
CREATE UNIQUE INDEX idx_cards_username ON cards(tenant_id, username);
