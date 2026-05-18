-- 004_billing — invoices, vouchers, recharges, payment gateways

CREATE TABLE payment_gateways (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,
    config_json   TEXT DEFAULT '{}',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_pg_unique ON payment_gateways(tenant_id, name);

CREATE TABLE invoices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL,
    invoice_number      TEXT NOT NULL,
    subscriber_id       INTEGER NOT NULL,
    username            TEXT NOT NULL,
    amount              REAL NOT NULL DEFAULT 0,
    admin_id            INTEGER DEFAULT 0,
    plan_id             INTEGER,
    plan_name           TEXT DEFAULT '',
    service_type        TEXT DEFAULT 'Hotspot',
    router_id           INTEGER,
    direction           TEXT NOT NULL DEFAULT 'charge',
    balance_before      REAL DEFAULT 0,
    balance_after       REAL DEFAULT 0,
    recharged_on        TEXT,
    expiration_at       TEXT,
    payment_method      TEXT DEFAULT 'cash',
    payment_gateway_id  INTEGER,
    status              TEXT NOT NULL DEFAULT 'paid',
    note                TEXT DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES access_plans(id) ON DELETE SET NULL,
    FOREIGN KEY (router_id) REFERENCES nas_devices(id) ON DELETE SET NULL,
    FOREIGN KEY (payment_gateway_id) REFERENCES payment_gateways(id) ON DELETE SET NULL
);
CREATE INDEX idx_inv_tenant ON invoices(tenant_id);
CREATE INDEX idx_inv_sub ON invoices(subscriber_id);
CREATE INDEX idx_inv_status ON invoices(tenant_id, status);
CREATE INDEX idx_inv_date ON invoices(tenant_id, created_at);
CREATE UNIQUE INDEX idx_inv_unique ON invoices(tenant_id, invoice_number);

CREATE TABLE vouchers (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id                INTEGER NOT NULL,
    code                     TEXT NOT NULL,
    amount                   REAL NOT NULL,
    plan_id                  INTEGER,
    status                   TEXT NOT NULL DEFAULT 'active',
    used_by_subscriber_id    INTEGER,
    used_at                  TEXT,
    expire_at                TEXT,
    generated_by             INTEGER DEFAULT 0,
    created_at               TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES access_plans(id) ON DELETE SET NULL,
    FOREIGN KEY (used_by_subscriber_id) REFERENCES subscribers(id) ON DELETE SET NULL
);
CREATE INDEX idx_vch_tenant ON vouchers(tenant_id);
CREATE INDEX idx_vch_status ON vouchers(tenant_id, status);
CREATE UNIQUE INDEX idx_vch_unique ON vouchers(tenant_id, code);

CREATE TABLE subscriber_recharges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    subscriber_id   INTEGER NOT NULL,
    username        TEXT NOT NULL,
    plan_id         INTEGER NOT NULL,
    plan_name       TEXT DEFAULT '',
    recharged_at    TEXT NOT NULL,
    expiration_at   TEXT,
    status          TEXT NOT NULL DEFAULT 'completed',
    payment_method  TEXT DEFAULT '',
    router_id       INTEGER,
    service_type    TEXT DEFAULT 'Hotspot',
    admin_id        INTEGER DEFAULT 0,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES access_plans(id) ON DELETE RESTRICT,
    FOREIGN KEY (router_id) REFERENCES nas_devices(id) ON DELETE SET NULL
);
CREATE INDEX idx_rech_tenant ON subscriber_recharges(tenant_id);
CREATE INDEX idx_rech_sub ON subscriber_recharges(subscriber_id);
