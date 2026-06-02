-- Smart Alerts engine — router metric push + per-router thresholds.
--
-- Routers sit behind NAT, so the server usually can't reach them. The proven
-- pattern (same as «دفع DHCP») is the ROUTER pushing data outbound over HTTPS.
-- A small /system scheduler agent posts interface RX/TX + uptime to
-- /api/v1/routers/<id>/metrics/ingest every ~2 min. The server stores a
-- rolling sample log + a denormalised per-router heartbeat row, then evaluates
-- thresholds and raises alerts (offline / high-traffic / high-usage / loop).
--
-- These tables are NEW and self-contained — nas_devices and the gated
-- network_device_monitor are intentionally NOT touched.

CREATE TABLE IF NOT EXISTS router_metric_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    router_id       INTEGER NOT NULL,
    reported_at     TEXT    NOT NULL DEFAULT '',   -- router clock (informational)
    uptime_seconds  INTEGER,
    interfaces_json TEXT    NOT NULL DEFAULT '[]',  -- [{name, rx_bytes, tx_bytes}]
    recorded_at     TEXT    NOT NULL               -- server clock (authoritative)
);

CREATE INDEX IF NOT EXISTS ix_router_metric_samples_router
    ON router_metric_samples (tenant_id, router_id, id DESC);

-- One row per router: heartbeat (last_push_at) + pointer to the last sample.
CREATE TABLE IF NOT EXISTS router_metric_state (
    tenant_id      INTEGER NOT NULL DEFAULT 1,
    router_id      INTEGER NOT NULL,
    last_push_at   TEXT    NOT NULL DEFAULT '',
    last_sample_id INTEGER,
    PRIMARY KEY (tenant_id, router_id)
);

-- Per-router thresholds. NULL columns fall back to the tenant-global defaults
-- stored in tenant_settings under network.alerts.* (merged in the service).
CREATE TABLE IF NOT EXISTS router_alert_settings (
    tenant_id          INTEGER NOT NULL DEFAULT 1,
    router_id          INTEGER NOT NULL,
    enabled            INTEGER NOT NULL DEFAULT 1,  -- 0 = mute this router
    offline_after_min  INTEGER,                     -- no push for N min → offline
    normal_speed_mbps  INTEGER,                     -- per-interface rate above → high_traffic
    normal_usage_gb    INTEGER,                     -- usage over window above → high_usage
    usage_window       TEXT,                        -- 'day' | 'month'
    updated_at         TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, router_id)
);
