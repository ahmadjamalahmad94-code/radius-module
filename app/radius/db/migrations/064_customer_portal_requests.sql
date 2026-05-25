-- Customer portal request queue.
-- Additive only; no live RADIUS/MikroTik mutation is performed by this table.

CREATE TABLE IF NOT EXISTS customer_portal_requests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    requester_type      TEXT NOT NULL,
    requester_id        INTEGER NOT NULL,
    request_type        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    requested_minutes   INTEGER NOT NULL DEFAULT 0,
    reason              TEXT NOT NULL DEFAULT '',
    result_json         TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    CHECK (requester_type IN ('subscriber', 'card_user')),
    CHECK (request_type IN ('loan', 'renewal', 'support')),
    CHECK (status IN ('pending', 'auto_approved', 'requires_approval', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_customer_portal_requests_requester
ON customer_portal_requests(tenant_id, requester_type, requester_id, id DESC);
