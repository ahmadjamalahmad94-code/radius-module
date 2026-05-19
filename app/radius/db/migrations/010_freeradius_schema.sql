-- 010_freeradius_schema — جداول FreeRADIUS القياسية (rlm_sql_sqlite)
-- يقرأها FreeRADIUS مباشرة عبر mods/sql.
-- نُضيف tenant_id للعزل (multi-tenant SaaS).

CREATE TABLE radcheck (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    username    TEXT NOT NULL DEFAULT '',
    attribute   TEXT NOT NULL DEFAULT '',
    op          TEXT NOT NULL DEFAULT ':=',
    value       TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_radcheck_user ON radcheck(tenant_id, username);

CREATE TABLE radreply (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    username    TEXT NOT NULL DEFAULT '',
    attribute   TEXT NOT NULL DEFAULT '',
    op          TEXT NOT NULL DEFAULT '=',
    value       TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_radreply_user ON radreply(tenant_id, username);

CREATE TABLE radgroupcheck (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    groupname   TEXT NOT NULL DEFAULT '',
    attribute   TEXT NOT NULL DEFAULT '',
    op          TEXT NOT NULL DEFAULT ':=',
    value       TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_radgc_group ON radgroupcheck(tenant_id, groupname);

CREATE TABLE radgroupreply (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    groupname   TEXT NOT NULL DEFAULT '',
    attribute   TEXT NOT NULL DEFAULT '',
    op          TEXT NOT NULL DEFAULT '=',
    value       TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_radgr_group ON radgroupreply(tenant_id, groupname);

CREATE TABLE radusergroup (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    username    TEXT NOT NULL DEFAULT '',
    groupname   TEXT NOT NULL DEFAULT '',
    priority    INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_radug_user ON radusergroup(tenant_id, username);

-- جدول NAS للـ FreeRADIUS (clients) — يقرأه FreeRADIUS لاكتشاف الـ NAS المسموحة
-- يُملأ تلقائيًا من nas_devices عبر view أو trigger في translator.
CREATE TABLE nas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    nasname     TEXT NOT NULL,
    shortname   TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'other',
    ports       INTEGER,
    secret      TEXT NOT NULL DEFAULT '',
    server      TEXT,
    community   TEXT,
    description TEXT DEFAULT '',
    require_ma  TEXT NOT NULL DEFAULT 'auto',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_freerad_nas_name ON nas(nasname);
