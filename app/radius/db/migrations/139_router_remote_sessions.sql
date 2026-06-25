-- feat/winbox-remote-access — time-boxed, source-IP-locked remote access to a
-- router's WinBox (and structurally SSH/web) over the SSTP management tunnel.
--
-- Each row is one on-demand managed port-forward: PANEL_PUBLIC_IP:public_port
-- → tunnel_ip:dst_port, realised as an nginx-stream block (with a source-IP
-- allow/deny) on the already-published 51000-51199 host range. Secure by
-- default: opened by an admin, locked to their public IP, auto-closed at
-- expires_at, released on close. Full audit (opened_by / source_ip /
-- timestamps).
CREATE TABLE IF NOT EXISTS router_remote_sessions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL DEFAULT 1,
  router_id     INTEGER NOT NULL,
  service       TEXT    NOT NULL DEFAULT 'winbox',   -- winbox | ssh | web (future)
  public_port   INTEGER NOT NULL,                    -- allocated 51000-51199
  tunnel_ip     TEXT    NOT NULL DEFAULT '',         -- router Framed-IP (10.50.0.x)
  dst_port      INTEGER NOT NULL DEFAULT 8291,       -- router-side mgmt port
  source_ip     TEXT    NOT NULL DEFAULT '',         -- admin public IP (forward lock)
  opened_by     TEXT    NOT NULL DEFAULT '',         -- admin username
  opened_at     TEXT    NOT NULL,
  expires_at    TEXT    NOT NULL,                    -- absolute auto-close time
  last_seen_at  TEXT    NOT NULL DEFAULT '',
  closed_at     TEXT    NOT NULL DEFAULT '',
  status        TEXT    NOT NULL DEFAULT 'active',    -- active | closed | expired
  close_reason  TEXT    NOT NULL DEFAULT '',
  always_on     INTEGER NOT NULL DEFAULT 0            -- 1 = persistent (owner opt-in)
);

CREATE INDEX IF NOT EXISTS idx_rras_status_expire
  ON router_remote_sessions (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_rras_router
  ON router_remote_sessions (router_id, status);
-- one active forward per public port (the allocator is the source of truth; this
-- is a belt-and-braces guard against a double-open race).
CREATE UNIQUE INDEX IF NOT EXISTS idx_rras_active_port
  ON router_remote_sessions (public_port) WHERE status = 'active';
