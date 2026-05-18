-- 001_tenants_admins — SaaS foundation
-- يبني tenants/roles/admins/memberships/settings — كل شيء بدون tenant_id يخصّ Tenant نفسه

CREATE TABLE tenants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    email           TEXT DEFAULT '',
    phone           TEXT DEFAULT '',
    currency        TEXT DEFAULT 'JOD',
    locale          TEXT DEFAULT 'ar',
    timezone        TEXT DEFAULT 'Asia/Amman',
    logo_url        TEXT DEFAULT '',
    primary_color   TEXT DEFAULT '#2BAACC',
    status          TEXT DEFAULT 'active',
    plan_tier       TEXT DEFAULT 'starter',
    max_subscribers INTEGER DEFAULT 200,
    max_nas         INTEGER DEFAULT 1,
    api_rpm         INTEGER DEFAULT 10,
    trial_ends_at   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);
CREATE INDEX idx_tenants_slug ON tenants(slug);

CREATE TABLE roles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    INTEGER,                            -- NULL = global system role
    name         TEXT NOT NULL,
    display_name TEXT DEFAULT '',
    description  TEXT DEFAULT '',
    permissions  TEXT NOT NULL DEFAULT '[]',          -- JSON array
    is_system    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_roles_tenant ON roles(tenant_id);
CREATE UNIQUE INDEX idx_roles_unique ON roles(COALESCE(tenant_id, 0), name);

CREATE TABLE admins (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    full_name       TEXT DEFAULT '',
    email           TEXT DEFAULT '',
    mobile          TEXT DEFAULT '',
    role_id         INTEGER,
    is_super_admin  INTEGER NOT NULL DEFAULT 0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_login_at   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE SET NULL
);

CREATE TABLE tenant_memberships (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    INTEGER NOT NULL,
    admin_id     INTEGER NOT NULL,
    role_id      INTEGER,
    status       TEXT NOT NULL DEFAULT 'active',
    invited_by   INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (admin_id)  REFERENCES admins(id)  ON DELETE CASCADE,
    FOREIGN KEY (role_id)   REFERENCES roles(id)   ON DELETE SET NULL
);
CREATE INDEX idx_memb_tenant ON tenant_memberships(tenant_id);
CREATE INDEX idx_memb_admin ON tenant_memberships(admin_id);
CREATE UNIQUE INDEX idx_memb_unique ON tenant_memberships(tenant_id, admin_id);

CREATE TABLE tenant_settings (
    tenant_id    INTEGER NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL DEFAULT '',
    updated_by   INTEGER DEFAULT 0,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, key),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
