-- Sprint 1 of the Network Operations family — Device Registry
-- foundation. See docs/network_operations/NETWORK_OPERATIONS_PLAN.md
-- for the full plan + per-column intent.
--
-- This table is the inventory for everything the operator
-- manages BEHIND a HobeRadius-managed MikroTik: APs,
-- switches, NVRs, cameras, servers. Subsequent sprints layer
-- behaviour on top of these rows:
--   • Sprint 2 — backend cron pings every row where
--     watch_enabled=1, writes results to network_device_checks
--     (created in migration 082).
--   • Sprint 3 — `network_device_bypass` service emits DHCP
--     lease + IP-binding + address-list rows on the router,
--     tagged with HOBE_DEVICE_BYPASS:<id>:<role>.
--   • Sprint 5 — `remote_device_access` opens TTL-gated NAT
--     forwards, referencing this row by device_id.
--
-- Foundational rule: every Network-Ops action operates on a
-- registered device. No anonymous remote access, no ad-hoc
-- bypass rules.

CREATE TABLE IF NOT EXISTS network_devices (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id         INTEGER NOT NULL,
  router_id         INTEGER NOT NULL,

  -- Operator-facing label (e.g. «AP الطابق الأول»). Required.
  name              TEXT    NOT NULL,

  -- One of: ap, router, switch, camera, nvr, server, other.
  -- Free text — front end validates the value, DB keeps it
  -- forgiving so a future type doesn't need a migration.
  device_type       TEXT    NOT NULL DEFAULT 'other',

  -- Internal LAN address as seen by the gateway router. Stored
  -- as TEXT (IPv4 dotted-quad today; ready for IPv6 later).
  ip_address        TEXT    NOT NULL DEFAULT '',

  -- Optional — used by the sprint-3 bypass planner to write
  -- DHCP lease + IP-binding rows. Stored lower-case with
  -- colons (e.g. "aa:bb:cc:dd:ee:ff"); UI/backend normalises.
  mac_address       TEXT    NOT NULL DEFAULT '',

  -- Free-text location note («السطح», «المكتب الرئيسي»).
  location          TEXT    NOT NULL DEFAULT '',

  -- Default management port — what the sprint-5 remote-access
  -- service will forward to. 80 covers WebFig + most AP web
  -- UIs; operator can override per session for WinBox (8291)
  -- or HTTPS (443).
  management_port   INTEGER NOT NULL DEFAULT 80,

  notes             TEXT    NOT NULL DEFAULT '',

  -- Marks a device whose outage matters enough to escalate
  -- (sprint 2 — alert cadence is shorter for criticals).
  is_critical       INTEGER NOT NULL DEFAULT 0,

  -- Sprint 2 toggles. watch_enabled gates the cron poll;
  -- alert_enabled gates the Telegram fan-out. Off by default
  -- so adding a device is harmless until the operator opts in.
  watch_enabled     INTEGER NOT NULL DEFAULT 0,
  alert_enabled     INTEGER NOT NULL DEFAULT 0,

  -- Last sampled state — populated by the sprint-2 monitor.
  -- One of: up, down, unknown. Unknown is the initial value
  -- so the UI doesn't show «down» for never-polled devices.
  last_status       TEXT    NOT NULL DEFAULT 'unknown',
  last_checked_at   TEXT    NOT NULL DEFAULT '',
  last_latency_ms   REAL,

  created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
  updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),

  FOREIGN KEY (router_id) REFERENCES nas_devices(id) ON DELETE CASCADE
);

-- List/sidebar queries: «show me every device behind router N
-- for tenant T».
CREATE INDEX IF NOT EXISTS idx_network_devices_tenant_router
  ON network_devices (tenant_id, router_id);

-- Sprint-2 cron worker scan: «every device the operator asked
-- us to watch». Partial would be ideal but SQLite's WHERE on
-- index needs >=3.8.0 — leave plain for portability.
CREATE INDEX IF NOT EXISTS idx_network_devices_watch
  ON network_devices (tenant_id, watch_enabled);
