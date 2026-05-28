-- Sprint 5 of the Network Operations family — TTL-gated remote
-- access sessions. See docs/network_operations/NETWORK_OPERATIONS_PLAN.md
-- for the full plan.
--
-- One row per «open this device for N minutes» request. The
-- planner adds a NAT dst-nat rule on the customer's MikroTik
-- that maps an unused port on the router's hr-wg interface to
-- the device's internal IP. The operator then reaches the
-- device by hitting that port on the router's WG IP from the
-- HobeRadius VPS (which is the only thing that can route into
-- hr-wg).
--
-- Every session has a HARD TTL — the cron worker
-- (services/network_device_monitor.py tick()) sweeps every
-- minute and closes any row whose expires_at has passed,
-- removing the NAT rule on the router. Defense in depth:
--   1. expires_at column — DB-enforced.
--   2. Router-side comment HOBE_REMOTE_ACCESS:<session_id>:
--      so cleanup is keyed by the same id even if the row is
--      gone from our DB.
--   3. Operator can hit «إغلاق الآن» any time.

CREATE TABLE IF NOT EXISTS remote_access_sessions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL,
  device_id       INTEGER NOT NULL,
  router_id       INTEGER NOT NULL,
  -- Admin user who requested the session. Free-text so we
  -- don't enforce an FK against admins until we settle that
  -- schema across the multi-tenant story.
  requested_by    TEXT    NOT NULL DEFAULT '',

  -- http / https / winbox / ssh — drives the default
  -- internal port + how the operator should hit the
  -- external port.
  protocol        TEXT    NOT NULL DEFAULT 'http',
  internal_ip     TEXT    NOT NULL,
  internal_port   INTEGER NOT NULL,
  -- External port on the router's hr-wg interface (1024-65535).
  -- Picked by the planner using a deterministic + collision-
  -- avoiding scheme (40000 + (device_id % 20000), bumped to
  -- the next free if taken).
  external_port   INTEGER NOT NULL,

  -- active | expired | closed | failed
  -- ‘failed’ means we couldn't add the NAT rule on the router.
  status          TEXT    NOT NULL DEFAULT 'active',

  -- ISO timestamps. expires_at is the TTL fence; the cron
  -- worker compares against `now`.
  created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
  expires_at      TEXT    NOT NULL,
  closed_at       TEXT    NOT NULL DEFAULT '',

  -- Audit metadata — operator IP at session open time. NOT
  -- used for ACLs; just for the audit trail.
  audit_ip        TEXT    NOT NULL DEFAULT '',
  -- Free-text reason / ticket reference.
  notes           TEXT    NOT NULL DEFAULT '',

  FOREIGN KEY (device_id) REFERENCES network_devices(id) ON DELETE CASCADE
);

-- Cron sweep: «every session that should have expired by now».
CREATE INDEX IF NOT EXISTS idx_remote_access_sessions_expire
  ON remote_access_sessions (status, expires_at);

-- Listing per-device: «what sessions exist (active or past) for
-- this device?»
CREATE INDEX IF NOT EXISTS idx_remote_access_sessions_device
  ON remote_access_sessions (device_id, created_at DESC);

-- Tenant scope (defense in depth — every read filters tenant_id
-- in the query too).
CREATE INDEX IF NOT EXISTS idx_remote_access_sessions_tenant
  ON remote_access_sessions (tenant_id, status);
