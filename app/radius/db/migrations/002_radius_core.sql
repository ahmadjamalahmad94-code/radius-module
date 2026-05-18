-- 002_radius_core — NAS, bandwidth, pools, plans, subscribers

CREATE TABLE nas_devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    name            TEXT NOT NULL,
    shortname       TEXT DEFAULT '',
    address         TEXT NOT NULL,
    secret          TEXT NOT NULL DEFAULT '',
    vendor          TEXT NOT NULL DEFAULT 'mikrotik',
    nas_type        TEXT NOT NULL DEFAULT 'hotspot',
    ports           INTEGER DEFAULT 0,
    snmp_community  TEXT DEFAULT '',
    auth_port       INTEGER DEFAULT 1812,
    acct_port       INTEGER DEFAULT 1813,
    coa_port        INTEGER DEFAULT 3799,
    api_port        INTEGER DEFAULT 8728,
    api_user        TEXT DEFAULT '',
    api_password    TEXT DEFAULT '',
    api_use_tls     INTEGER NOT NULL DEFAULT 0,
    location        TEXT DEFAULT '',
    coordinates     TEXT DEFAULT '',
    monitoring_enabled INTEGER NOT NULL DEFAULT 1,
    description     TEXT DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_seen_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_nas_tenant ON nas_devices(tenant_id);
CREATE UNIQUE INDEX idx_nas_unique ON nas_devices(tenant_id, name);

CREATE TABLE bandwidth_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    name            TEXT NOT NULL,
    rate_down       INTEGER NOT NULL DEFAULT 0,
    rate_down_unit  TEXT NOT NULL DEFAULT 'Kbps',
    rate_up         INTEGER NOT NULL DEFAULT 0,
    rate_up_unit    TEXT NOT NULL DEFAULT 'Kbps',
    burst           TEXT DEFAULT '',
    priority        INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_bw_tenant ON bandwidth_profiles(tenant_id);
CREATE UNIQUE INDEX idx_bw_unique ON bandwidth_profiles(tenant_id, name);

CREATE TABLE ip_pools (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    pool_name       TEXT NOT NULL,
    range_ip        TEXT NOT NULL,
    local_ip        TEXT DEFAULT '',
    router_id       INTEGER,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (router_id) REFERENCES nas_devices(id) ON DELETE SET NULL
);
CREATE INDEX idx_pool_tenant ON ip_pools(tenant_id);
CREATE UNIQUE INDEX idx_pool_unique ON ip_pools(tenant_id, pool_name);

CREATE TABLE access_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    name            TEXT NOT NULL,
    code            TEXT DEFAULT '',
    plan_type       TEXT NOT NULL DEFAULT 'time',
    service_type    TEXT NOT NULL DEFAULT 'Hotspot',
    typebp          TEXT DEFAULT 'Limited',
    limit_type      TEXT DEFAULT 'Time_Limit',
    duration_value     INTEGER DEFAULT 0,
    duration_unit      TEXT DEFAULT 'Mins',
    duration_minutes   INTEGER DEFAULT 0,
    validity_value     INTEGER DEFAULT 0,
    validity_unit      TEXT DEFAULT 'Days',
    validity_days      INTEGER DEFAULT 0,
    max_daily_minutes  INTEGER DEFAULT 0,
    max_weekly_minutes INTEGER DEFAULT 0,
    max_monthly_minutes INTEGER DEFAULT 0,
    session_timeout_sec INTEGER DEFAULT 0,
    idle_timeout_sec   INTEGER DEFAULT 0,
    data_value         INTEGER DEFAULT 0,
    data_unit          TEXT DEFAULT 'MB',
    quota_total_mb     INTEGER DEFAULT 0,
    quota_daily_mb     INTEGER DEFAULT 0,
    quota_monthly_mb   INTEGER DEFAULT 0,
    quota_reset_strategy TEXT DEFAULT 'rolling',
    bandwidth_id       INTEGER,
    speed_up_kbps      INTEGER DEFAULT 0,
    speed_down_kbps    INTEGER DEFAULT 0,
    burst_up_kbps      INTEGER DEFAULT 0,
    burst_down_kbps    INTEGER DEFAULT 0,
    burst_threshold_kbps INTEGER DEFAULT 0,
    burst_time_sec     INTEGER DEFAULT 0,
    burst_raw          TEXT DEFAULT '',
    concurrent_sessions INTEGER DEFAULT 1,
    address_pool       TEXT DEFAULT '',
    framed_pool        TEXT DEFAULT '',
    pool_id            INTEGER,
    vlan_id            INTEGER DEFAULT 0,
    ipv6_pool          TEXT DEFAULT '',
    bind_mac           INTEGER NOT NULL DEFAULT 0,
    bind_ip            INTEGER NOT NULL DEFAULT 0,
    force_mac_address  INTEGER NOT NULL DEFAULT 0,
    allowed_devices_count INTEGER DEFAULT 1,
    allowed_days       TEXT DEFAULT '["mon","tue","wed","thu","fri","sat","sun"]',
    allowed_hours_from TEXT DEFAULT '',
    allowed_hours_to   TEXT DEFAULT '',
    on_login           TEXT DEFAULT '',
    on_logout          TEXT DEFAULT '',
    auto_renew         INTEGER NOT NULL DEFAULT 0,
    router_ids         TEXT DEFAULT '[]',
    price_card         REAL DEFAULT 0,
    price_bulk         REAL DEFAULT 0,
    price              REAL DEFAULT 0,
    currency           TEXT DEFAULT 'JOD',
    plan_tier          TEXT DEFAULT 'Personal',
    prepaid            INTEGER NOT NULL DEFAULT 1,
    project            TEXT DEFAULT '',
    description        TEXT DEFAULT '',
    enabled            INTEGER NOT NULL DEFAULT 1,
    priority           INTEGER DEFAULT 100,
    color              TEXT DEFAULT '#2BAACC',
    created_at         TEXT NOT NULL,
    updated_at         TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (bandwidth_id) REFERENCES bandwidth_profiles(id) ON DELETE SET NULL,
    FOREIGN KEY (pool_id) REFERENCES ip_pools(id) ON DELETE SET NULL
);
CREATE INDEX idx_plans_tenant ON access_plans(tenant_id);
CREATE INDEX idx_plans_enabled ON access_plans(tenant_id, enabled);
CREATE UNIQUE INDEX idx_plans_unique ON access_plans(tenant_id, name);

CREATE TABLE subscribers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    username        TEXT NOT NULL,
    password        TEXT NOT NULL DEFAULT '',
    user_type       TEXT NOT NULL DEFAULT 'subscriber',
    service_type    TEXT NOT NULL DEFAULT 'Hotspot',
    plan_id         INTEGER,
    photo_url       TEXT DEFAULT '/user.default.jpg',
    pppoe_username  TEXT DEFAULT '',
    pppoe_password  TEXT DEFAULT '',
    pppoe_ip        TEXT DEFAULT '',
    full_name       TEXT DEFAULT '',
    father_name     TEXT DEFAULT '',
    mobile          TEXT DEFAULT '',
    email           TEXT DEFAULT '',
    address         TEXT DEFAULT '',
    city            TEXT DEFAULT '',
    district        TEXT DEFAULT '',
    state           TEXT DEFAULT '',
    zip             TEXT DEFAULT '',
    coordinates     TEXT DEFAULT '',
    national_id     TEXT DEFAULT '',
    account_type    TEXT DEFAULT 'Personal',
    balance         REAL DEFAULT 0,
    auto_renewal    INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'enabled',
    manager_id      INTEGER,
    group_name      TEXT DEFAULT '',
    pool            TEXT DEFAULT '',
    first_login_at  TEXT,
    expire_at       TEXT,
    last_login_at   TEXT,
    last_seen_at    TEXT,
    mac_lock        TEXT,
    static_ip       TEXT,
    vlan_id         INTEGER DEFAULT 0,
    override_concurrent INTEGER DEFAULT 0,
    used_seconds    INTEGER NOT NULL DEFAULT 0,
    used_bytes_in   INTEGER NOT NULL DEFAULT 0,
    used_bytes_out  INTEGER NOT NULL DEFAULT 0,
    online_count    INTEGER NOT NULL DEFAULT 0,
    beneficiary_ref TEXT DEFAULT '',
    card_batch_id   INTEGER,
    remark          TEXT DEFAULT '',
    created_by      INTEGER DEFAULT 0,
    updated_by      INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES access_plans(id) ON DELETE SET NULL,
    FOREIGN KEY (manager_id) REFERENCES admins(id) ON DELETE SET NULL
);
CREATE INDEX idx_subs_tenant ON subscribers(tenant_id);
CREATE INDEX idx_subs_status ON subscribers(tenant_id, status);
CREATE INDEX idx_subs_plan ON subscribers(tenant_id, plan_id);
CREATE INDEX idx_subs_expire ON subscribers(tenant_id, expire_at);
CREATE UNIQUE INDEX idx_subs_unique ON subscribers(tenant_id, username);

CREATE TABLE subscriber_fields (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL,
    subscriber_id INTEGER NOT NULL,
    field_name    TEXT NOT NULL,
    field_value   TEXT DEFAULT '',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE
);
CREATE INDEX idx_subfld ON subscriber_fields(subscriber_id);
