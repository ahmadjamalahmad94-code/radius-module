-- 005_support — tickets, services (hardware), inbox messages

CREATE TABLE tickets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    subscriber_id     INTEGER NOT NULL,
    subject           TEXT NOT NULL,
    category          TEXT NOT NULL DEFAULT 'general',
    priority          TEXT NOT NULL DEFAULT 'normal',
    status            TEXT NOT NULL DEFAULT 'open',
    assignee_admin_id INTEGER,
    body              TEXT DEFAULT '',
    attachments_json  TEXT DEFAULT '[]',
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    closed_at         TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE,
    FOREIGN KEY (assignee_admin_id) REFERENCES admins(id) ON DELETE SET NULL
);
CREATE INDEX idx_tk_tenant ON tickets(tenant_id);
CREATE INDEX idx_tk_status ON tickets(tenant_id, status);
CREATE INDEX idx_tk_sub ON tickets(subscriber_id);

CREATE TABLE ticket_replies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    INTEGER NOT NULL,
    ticket_id    INTEGER NOT NULL,
    body         TEXT NOT NULL,
    author_type  TEXT NOT NULL,
    author_id    INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);
CREATE INDEX idx_tkr_ticket ON ticket_replies(ticket_id);

CREATE TABLE services (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    subscriber_id   INTEGER NOT NULL,
    name            TEXT NOT NULL,
    serial          TEXT DEFAULT '',
    mac             TEXT DEFAULT '',
    type            TEXT NOT NULL DEFAULT 'router',
    rent_per_month  REAL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'given',
    given_at        TEXT,
    returned_at     TEXT,
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE
);
CREATE INDEX idx_svc_tenant ON services(tenant_id);
CREATE INDEX idx_svc_sub ON services(subscriber_id);

CREATE TABLE inbox_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    subscriber_id     INTEGER NOT NULL,
    subject           TEXT NOT NULL,
    body              TEXT DEFAULT '',
    type              TEXT NOT NULL DEFAULT 'in_app',
    read_at           TEXT,
    sent_by_admin_id  INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE
);
CREATE INDEX idx_inbox_sub ON inbox_messages(subscriber_id);
