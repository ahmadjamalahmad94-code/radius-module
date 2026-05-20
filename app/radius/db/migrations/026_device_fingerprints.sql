-- ╔════════════════════════════════════════════════════════════════════╗
-- ║  Device fingerprinting from MikroTik DHCP leases                     ║
-- ║                                                                      ║
-- ║  Every time a client gets a DHCP lease, MikroTik records:            ║
-- ║    • host-name        e.g. "Redmi-Note-12-Pro"                       ║
-- ║    • active-client-id (vendor class / DHCP option 60)                ║
-- ║                       e.g. "android-dhcp-11"                         ║
-- ║                                                                      ║
-- ║  We pull `/ip/dhcp-server/lease/print` on a background timer, parse  ║
-- ║  those two raw values into structured fields (os, version, brand,    ║
-- ║  model) keyed by MAC, and cache them here. The Card Checker, the     ║
-- ║  subscribers list, the MAC-lock picker, and the public devices API   ║
-- ║  all read from this table — never re-query MT for display.           ║
-- ║                                                                      ║
-- ║  Raw fields are preserved exactly as MT returned them so the parser  ║
-- ║  can be improved later without re-fetching from the router.          ║
-- ╚════════════════════════════════════════════════════════════════════╝

CREATE TABLE IF NOT EXISTS device_fingerprints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       TEXT    NOT NULL,
    mac             TEXT    NOT NULL,          -- normalized lower-case AA:BB:CC:DD:EE:FF

    -- raw values from MikroTik (never overwrite with empty on refresh)
    hostname        TEXT    NOT NULL DEFAULT '',
    dhcp_class_id   TEXT    NOT NULL DEFAULT '',

    -- parsed values (best-effort; '' when unknown)
    os_family       TEXT    NOT NULL DEFAULT '',  -- android | ios | windows | macos | linux | other
    os_version      TEXT    NOT NULL DEFAULT '',  -- "11", "12", "15.4", ...
    device_brand    TEXT    NOT NULL DEFAULT '',  -- xiaomi | samsung | apple | huawei | ...
    device_model    TEXT    NOT NULL DEFAULT '',  -- "Redmi-Note-12-Pro"

    -- last-seen context
    ip_address      TEXT    NOT NULL DEFAULT '',
    nas_id          INTEGER,                       -- source router (FK soft, no constraint)

    first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at    TEXT    NOT NULL DEFAULT (datetime('now')),

    UNIQUE(tenant_id, mac)
);

CREATE INDEX IF NOT EXISTS idx_devfp_tenant_mac     ON device_fingerprints(tenant_id, mac);
CREATE INDEX IF NOT EXISTS idx_devfp_tenant_seen    ON device_fingerprints(tenant_id, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_devfp_tenant_os      ON device_fingerprints(tenant_id, os_family);
