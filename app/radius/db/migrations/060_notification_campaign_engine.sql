-- Notification and campaign engine foundation.
-- Additive only: no provider secrets, no external sends, no destructive changes.

CREATE TABLE IF NOT EXISTS notification_templates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    template_key        TEXT NOT NULL,
    title               TEXT NOT NULL,
    channel             TEXT NOT NULL DEFAULT 'internal',
    subject             TEXT NOT NULL DEFAULT '',
    body                TEXT NOT NULL,
    variables_json      TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'active',
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    UNIQUE(tenant_id, template_key)
);
CREATE INDEX IF NOT EXISTS idx_notification_templates_tenant
ON notification_templates(tenant_id, status);

CREATE TABLE IF NOT EXISTS notification_preferences (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    recipient_type      TEXT NOT NULL,
    recipient_id        INTEGER NOT NULL DEFAULT 0,
    channel             TEXT NOT NULL,
    enabled             INTEGER NOT NULL DEFAULT 1,
    quiet_hours_json    TEXT NOT NULL DEFAULT '{}',
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    UNIQUE(tenant_id, recipient_type, recipient_id, channel)
);

CREATE TABLE IF NOT EXISTS audience_segments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    segment_key         TEXT NOT NULL,
    title               TEXT NOT NULL,
    filters_json        TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'active',
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    UNIQUE(tenant_id, segment_key)
);
CREATE INDEX IF NOT EXISTS idx_audience_segments_tenant
ON audience_segments(tenant_id, status);

CREATE TABLE IF NOT EXISTS message_campaigns (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    campaign_key        TEXT NOT NULL,
    title               TEXT NOT NULL,
    campaign_type       TEXT NOT NULL DEFAULT 'manual',
    channel             TEXT NOT NULL DEFAULT 'internal',
    template_id         INTEGER,
    audience_json       TEXT NOT NULL DEFAULT '{}',
    actions_json        TEXT NOT NULL DEFAULT '[]',
    dry_run_json        TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'draft',
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    UNIQUE(tenant_id, campaign_key),
    FOREIGN KEY(template_id) REFERENCES notification_templates(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_message_campaigns_tenant
ON message_campaigns(tenant_id, status, created_at);

CREATE TABLE IF NOT EXISTS message_notifications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    notification_type   TEXT NOT NULL DEFAULT 'manual',
    channel             TEXT NOT NULL DEFAULT 'internal',
    recipient_type      TEXT NOT NULL,
    recipient_id        INTEGER NOT NULL DEFAULT 0,
    template_id         INTEGER,
    campaign_id         INTEGER,
    subject             TEXT NOT NULL DEFAULT '',
    body                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'queued',
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    FOREIGN KEY(template_id) REFERENCES notification_templates(id) ON DELETE SET NULL,
    FOREIGN KEY(campaign_id) REFERENCES message_campaigns(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_recipient
ON message_notifications(tenant_id, recipient_type, recipient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_status
ON message_notifications(tenant_id, status, created_at);

CREATE TABLE IF NOT EXISTS message_deliveries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    notification_id     INTEGER NOT NULL,
    campaign_id         INTEGER,
    channel             TEXT NOT NULL,
    provider_key        TEXT NOT NULL DEFAULT 'null',
    recipient_address   TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'queued',
    provider_message_id TEXT NOT NULL DEFAULT '',
    error_message       TEXT NOT NULL DEFAULT '',
    retry_count         INTEGER NOT NULL DEFAULT 0,
    result_json         TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    sent_at             TEXT,
    delivered_at        TEXT,
    read_at             TEXT,
    FOREIGN KEY(notification_id) REFERENCES message_notifications(id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES message_campaigns(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_message_deliveries_status
ON message_deliveries(tenant_id, status, created_at);

CREATE TABLE IF NOT EXISTS notification_provider_configs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    provider_key        TEXT NOT NULL,
    channel             TEXT NOT NULL,
    display_name        TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'placeholder',
    config_json         TEXT NOT NULL DEFAULT '{}',
    secret_ref          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    UNIQUE(tenant_id, provider_key, channel)
);
