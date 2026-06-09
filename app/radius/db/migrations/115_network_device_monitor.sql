-- 115_network_device_monitor — «تتبع حالة الأجهزة» (Network Device Health Monitor)
--
-- قسم مستقل لمراقبة أجهزة الشبكة (APs/روابط/UniFi/LiteBeam/سويتشات) خلف
-- راوترات MikroTik. يدير: تسجيل الجهاز، تجهيز وصول الراوتر (IP/Gateway +
-- Hotspot IP-Binding + Netwatch)، استطلاع الحالة، والتنبيهات.
--
-- ملاحظة تسمية: الملف الخدمي القديم services/network_device_monitor.py يخصّ
-- ميزة «تابع أجهزة الشبكة» على جدول network_devices. هذه الجداول (ببادئة
-- network_device_monitor_) جديدة تمامًا ولا تتصادم معه. وحدات Python للميزة
-- الجديدة تأخذ بادئة device_health_ لتفادي تصادم أسماء الملفات.
--
-- قواعد التصميم:
--   * المدخل (interface_name) إلزامي — مفتاح النطاق هو
--     router_id + interface + network_cidr (نفس الـsubnet قد يلزم على أكثر من مدخل).
--   * منع تكرار الجهاز: فهرس فريد جزئي على (tenant_id, router_id, ip_address).
--   * لا أسرار خام؛ كل شيء tenant-scoped؛ حذف ناعم للسجلّ الرئيسي.

-- ─────────────────────────────────────────────────────────────────
-- 1. network_device_monitor_devices — السجل الرئيسي للأجهزة
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS network_device_monitor_devices (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id                       INTEGER NOT NULL DEFAULT 1,
    -- الراوتر الذي يتبع له الجهاز (nas_devices.id)
    router_id                       INTEGER NOT NULL,
    -- بيانات الجهاز
    name                            TEXT    NOT NULL DEFAULT '',
    -- النوع: ap | router | link | unifi | litebeam | switch | server | camera | other
    device_type                     TEXT    NOT NULL DEFAULT 'other',
    -- المدخل على الراوتر (إلزامي) — جزء من مفتاح النطاق
    interface_name                  TEXT    NOT NULL DEFAULT '',
    ip_address                      TEXT    NOT NULL DEFAULT '',
    -- محسوبة من IP + البادئة: 192.168.15.0/24
    network_cidr                    TEXT    NOT NULL DEFAULT '',
    -- بوابة الراوتر المقترحة: 192.168.15.254/24
    gateway_address                 TEXT    NOT NULL DEFAULT '',
    location                        TEXT    NOT NULL DEFAULT '',
    -- إعدادات متقدّمة
    subnet_prefix                   INTEGER NOT NULL DEFAULT 24,
    gateway_last_octet              INTEGER NOT NULL DEFAULT 254,
    ping_threshold_ms               INTEGER NOT NULL DEFAULT 80,
    netwatch_interval_sec           INTEGER NOT NULL DEFAULT 60,
    netwatch_timeout_sec            INTEGER NOT NULL DEFAULT 3,
    -- قناة التنبيه: telegram | sms | whatsapp | '' (افتراضي المستأجر)
    alert_channel                   TEXT    NOT NULL DEFAULT '',
    monitoring_enabled              INTEGER NOT NULL DEFAULT 1,
    -- الحالة: up|down|timeout|high_latency|unknown|disabled|apply_failed
    status                          TEXT    NOT NULL DEFAULT 'unknown',
    last_latency_ms                 REAL,
    last_checked_at                 TEXT,
    last_status_change_at           TEXT,
    last_down_at                    TEXT,
    last_up_at                      TEXT,
    consecutive_down_count          INTEGER NOT NULL DEFAULT 0,
    consecutive_high_latency_count  INTEGER NOT NULL DEFAULT 0,
    -- معرّف Netwatch على الراوتر (يُملأ عند apply في Phase 3)
    mikrotik_netwatch_id            TEXT    NOT NULL DEFAULT '',
    notes                           TEXT    NOT NULL DEFAULT '',
    created_at                      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at                      TEXT    NOT NULL DEFAULT (datetime('now')),
    -- حذف ناعم
    deleted_at                      TEXT,
    deleted_by                      TEXT    NOT NULL DEFAULT '',
    delete_reason                   TEXT    NOT NULL DEFAULT '',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_ndm_devices_tenant
    ON network_device_monitor_devices(tenant_id, deleted_at);
CREATE INDEX IF NOT EXISTS ix_ndm_devices_router
    ON network_device_monitor_devices(tenant_id, router_id);
CREATE INDEX IF NOT EXISTS ix_ndm_devices_status
    ON network_device_monitor_devices(tenant_id, status);
-- منع التكرار: نفس الراوتر + نفس IP لا يتكرّر بين الأحياء.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ndm_devices_router_ip
    ON network_device_monitor_devices(tenant_id, router_id, ip_address)
    WHERE ip_address <> '' AND deleted_at IS NULL;

-- ─────────────────────────────────────────────────────────────────
-- 2. network_device_monitor_network_scopes — نطاق الشبكة لكل مدخل
-- ─────────────────────────────────────────────────────────────────
-- المفتاح الحرج: router_id + interface + network_cidr. نفس الـsubnet على
-- مدخل آخر = سجل منفصل (مع تحذير غموض توجيه في طبقة الخطة).
CREATE TABLE IF NOT EXISTS network_device_monitor_network_scopes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    router_id           INTEGER NOT NULL,
    interface_name      TEXT    NOT NULL DEFAULT '',
    network_cidr        TEXT    NOT NULL DEFAULT '',
    gateway_address     TEXT    NOT NULL DEFAULT '',
    -- معرّف /ip/address على الراوتر (يُملأ عند apply)
    mikrotik_address_id TEXT    NOT NULL DEFAULT '',
    -- pending | already_present | applied | apply_failed
    apply_status        TEXT    NOT NULL DEFAULT 'pending',
    last_applied_at     TEXT,
    apply_error         TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ndm_scope_router_iface_net
    ON network_device_monitor_network_scopes(tenant_id, router_id, interface_name, network_cidr);
CREATE INDEX IF NOT EXISTS ix_ndm_scope_router_net
    ON network_device_monitor_network_scopes(tenant_id, router_id, network_cidr);

-- ─────────────────────────────────────────────────────────────────
-- 3. network_device_monitor_bindings — Hotspot IP-Binding (bypass)
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS network_device_monitor_bindings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    router_id           INTEGER NOT NULL,
    network_cidr        TEXT    NOT NULL DEFAULT '',
    -- bypassed | regular | blocked (الافتراضي bypassed)
    binding_type        TEXT    NOT NULL DEFAULT 'bypassed',
    mikrotik_binding_id TEXT    NOT NULL DEFAULT '',
    apply_status        TEXT    NOT NULL DEFAULT 'pending',
    last_applied_at     TEXT,
    apply_error         TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ndm_binding_router_net_type
    ON network_device_monitor_bindings(tenant_id, router_id, network_cidr, binding_type);

-- ─────────────────────────────────────────────────────────────────
-- 4. network_device_monitor_events — سجل أحداث تغيّر الحالة
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS network_device_monitor_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    device_id       INTEGER NOT NULL,
    -- up | down | timeout | high_latency | recovered | apply_failed | created | updated
    event_type      TEXT    NOT NULL DEFAULT '',
    previous_status TEXT    NOT NULL DEFAULT '',
    new_status      TEXT    NOT NULL DEFAULT '',
    latency_ms      REAL,
    message         TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_ndm_events_device_time
    ON network_device_monitor_events(tenant_id, device_id, created_at DESC);

-- ─────────────────────────────────────────────────────────────────
-- 5. network_device_monitor_alerts — سجل التنبيهات المُرسَلة (للـdedup)
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS network_device_monitor_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    device_id   INTEGER NOT NULL,
    -- down | high_latency | recovery
    alert_type  TEXT    NOT NULL DEFAULT '',
    channel     TEXT    NOT NULL DEFAULT '',
    -- sent | skipped | failed
    status      TEXT    NOT NULL DEFAULT '',
    sent_at     TEXT,
    -- مفتاح منع التكرار: device_id:alert_type:bucket
    dedup_key   TEXT    NOT NULL DEFAULT '',
    message     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_ndm_alerts_device_time
    ON network_device_monitor_alerts(tenant_id, device_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ndm_alerts_dedup
    ON network_device_monitor_alerts(tenant_id, dedup_key, created_at DESC);
