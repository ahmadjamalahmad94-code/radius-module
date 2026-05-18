-- 006_logs — audit, radacct, radpostauth, bandwidth_usage_daily

CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    INTEGER NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    target_type  TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    ip_address   TEXT DEFAULT '',
    user_agent   TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_audit_tenant_date ON audit_log(tenant_id, created_at DESC);
CREATE INDEX idx_audit_target ON audit_log(tenant_id, target_type, target_id);

CREATE TABLE radacct (
    radacctid          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id          INTEGER NOT NULL,
    acctsessionid      TEXT NOT NULL DEFAULT '',
    acctuniqueid       TEXT NOT NULL DEFAULT '',
    username           TEXT NOT NULL DEFAULT '',
    groupname          TEXT DEFAULT '',
    realm              TEXT DEFAULT '',
    nasipaddress       TEXT NOT NULL DEFAULT '',
    nasportid          TEXT DEFAULT '',
    nasporttype        TEXT DEFAULT '',
    acctstarttime      TEXT,
    acctupdatetime     TEXT,
    acctstoptime       TEXT,
    acctinterval       INTEGER DEFAULT 0,
    acctsessiontime    INTEGER DEFAULT 0,
    acctauthentic      TEXT DEFAULT '',
    connectinfo_start  TEXT DEFAULT '',
    connectinfo_stop   TEXT DEFAULT '',
    acctinputoctets    INTEGER DEFAULT 0,
    acctoutputoctets   INTEGER DEFAULT 0,
    calledstationid    TEXT DEFAULT '',
    callingstationid   TEXT DEFAULT '',
    acctterminatecause TEXT DEFAULT '',
    servicetype        TEXT DEFAULT '',
    framedprotocol     TEXT DEFAULT '',
    framedipaddress    TEXT DEFAULT '',
    framedipv6address  TEXT DEFAULT '',
    class              TEXT DEFAULT '',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_acct_user ON radacct(tenant_id, username, acctstarttime DESC);
CREATE INDEX idx_acct_session ON radacct(acctsessionid);
CREATE INDEX idx_acct_date ON radacct(tenant_id, acctstarttime DESC);

CREATE TABLE radpostauth (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id  INTEGER NOT NULL,
    username   TEXT NOT NULL DEFAULT '',
    pass       TEXT NOT NULL DEFAULT '',
    reply      TEXT NOT NULL DEFAULT '',
    authdate   TEXT NOT NULL,
    class      TEXT DEFAULT '',
    nas        TEXT DEFAULT '',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_pauth_user ON radpostauth(tenant_id, username, authdate DESC);

CREATE TABLE bandwidth_usage_daily (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    subscriber_id   INTEGER NOT NULL,
    day             TEXT NOT NULL,
    bytes_in        INTEGER NOT NULL DEFAULT 0,
    bytes_out       INTEGER NOT NULL DEFAULT 0,
    sessions_count  INTEGER NOT NULL DEFAULT 0,
    peak_mbps       REAL DEFAULT 0,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_bwd_unique ON bandwidth_usage_daily(tenant_id, subscriber_id, day);
