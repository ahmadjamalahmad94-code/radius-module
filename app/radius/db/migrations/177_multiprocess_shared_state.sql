-- ════════════════════════════════════════════════════════════════════════
-- 177 — state shared by ALL processes (leftover wave, 2026-09-28).
--
-- The panel now runs several gunicorn worker PROCESSES, the background
-- workers run in their own process, and FreeRADIUS auth has its own gunicorn.
-- Anything one process writes and another reads (or that must be global, like
-- a login lockout) can no longer live in a module-level dict. These tables are
-- that shared memory: small, keyed, pruned by expiry.
-- ════════════════════════════════════════════════════════════════════════

-- Router live state (nas_liveness): written by the reconciler in the worker
-- process, read by the panel (/online, dashboard) and the auth cap check.
CREATE TABLE IF NOT EXISTS nas_liveness_state (
    tenant_id   INTEGER NOT NULL,
    nas_ip      TEXT    NOT NULL,
    last_ok     REAL,
    last_fail   REAL,
    active      INTEGER NOT NULL DEFAULT 0,
    last_event  TEXT,
    PRIMARY KEY (tenant_id, nas_ip)
);

-- Background worker heartbeats (status page, /health, license health report).
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    name        TEXT PRIMARY KEY,
    last_beat   REAL NOT NULL,
    info_json   TEXT NOT NULL DEFAULT '{}'
);

-- Sliding-window events for lockouts / rate limits (one row per hit).
CREATE TABLE IF NOT EXISTS rate_events (
    scope   TEXT NOT NULL,
    key     TEXT NOT NULL,
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_events_scope_key_ts ON rate_events(scope, key, ts);

-- Exclusive operation leases (data reset, demo cleanup, reconcile …).
CREATE TABLE IF NOT EXISTS op_locks (
    name        TEXT PRIMARY KEY,
    holder      TEXT NOT NULL,
    expires_at  REAL NOT NULL
);

-- Small keyed blobs with expiry (job progress, one-time publish blobs, dedup).
CREATE TABLE IF NOT EXISTS shared_kv (
    ns          TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       BLOB,
    expires_at  REAL NOT NULL,
    PRIMARY KEY (ns, key)
);
CREATE INDEX IF NOT EXISTS idx_shared_kv_expires ON shared_kv(expires_at);
