-- 009_share_groups — مجموعات لمشاركة الباندويث/الكوتا بين مستخدمين متعدّدين

CREATE TABLE share_groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    shared_quota_mb INTEGER NOT NULL DEFAULT 0,   -- 0 = unlimited
    shared_speed_down_kbps INTEGER NOT NULL DEFAULT 0,
    shared_speed_up_kbps   INTEGER NOT NULL DEFAULT 0,
    max_members     INTEGER NOT NULL DEFAULT 0,   -- 0 = unlimited
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_sgrp_tenant ON share_groups(tenant_id);
CREATE UNIQUE INDEX idx_sgrp_unique ON share_groups(tenant_id, name);

CREATE TABLE share_group_members (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    group_id        INTEGER NOT NULL,
    subscriber_id   INTEGER NOT NULL,
    added_at        TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES share_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE
);
CREATE INDEX idx_sgm_group ON share_group_members(group_id);
CREATE UNIQUE INDEX idx_sgm_unique ON share_group_members(group_id, subscriber_id);
