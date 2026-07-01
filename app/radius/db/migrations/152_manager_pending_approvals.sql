-- High-value approval queue: manager actions above the owner-set threshold
-- (require_approval_above on the policy) don't execute immediately — they queue
-- here for the owner to approve (→ execute) or reject (→ discard).

CREATE TABLE IF NOT EXISTS manager_pending_approvals (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL DEFAULT 1,
  admin_id      INTEGER NOT NULL,           -- the requesting manager
  action_key    TEXT NOT NULL,              -- e.g. subscriber.loan
  amount_minor  INTEGER NOT NULL DEFAULT 0,
  payload_json  TEXT NOT NULL DEFAULT '{}', -- replay body for execution on approve
  summary       TEXT NOT NULL DEFAULT '',   -- human summary for the owner list
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected
  created_at    TEXT NOT NULL,
  decided_at    TEXT,
  decided_by    INTEGER,
  CHECK (status IN ('pending','approved','rejected'))
);

CREATE INDEX IF NOT EXISTS ix_manager_approvals_status
  ON manager_pending_approvals (tenant_id, status, id DESC);
