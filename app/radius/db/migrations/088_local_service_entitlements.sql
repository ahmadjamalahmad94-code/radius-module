-- Local service entitlements created by approved runtime-side service requests.
-- These rows only update the local runtime contract. They do not configure
-- MikroTik, WireGuard, Linux tc, CoA, or any live gateway.

CREATE TABLE IF NOT EXISTS service_request_links (
  id                         INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                  INTEGER NOT NULL DEFAULT 1,
  ticket_id                  INTEGER NOT NULL,
  subscriber_id              INTEGER NOT NULL,
  service_key                TEXT NOT NULL,
  service_label              TEXT NOT NULL DEFAULT '',
  request_type               TEXT NOT NULL DEFAULT 'activation',
  latest_payment_request_id  INTEGER,
  decision                   TEXT NOT NULL DEFAULT 'created',
  status                     TEXT NOT NULL DEFAULT 'open',
  created_at                 TEXT NOT NULL,
  updated_at                 TEXT NOT NULL,
  UNIQUE(tenant_id, ticket_id),
  FOREIGN KEY(ticket_id) REFERENCES tickets(id),
  FOREIGN KEY(latest_payment_request_id) REFERENCES payment_requests(id)
);

CREATE INDEX IF NOT EXISTS ix_service_request_links_payment
  ON service_request_links (tenant_id, latest_payment_request_id);

CREATE INDEX IF NOT EXISTS ix_service_request_links_service
  ON service_request_links (tenant_id, service_key, status);

CREATE TABLE IF NOT EXISTS local_service_entitlements (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id      INTEGER NOT NULL DEFAULT 1,
  service_key    TEXT NOT NULL,
  service_label  TEXT NOT NULL DEFAULT '',
  enabled        INTEGER NOT NULL DEFAULT 1,
  status         TEXT NOT NULL DEFAULT 'active',
  source_type    TEXT NOT NULL DEFAULT '',
  source_id      INTEGER,
  ticket_id      INTEGER,
  subscriber_id  INTEGER,
  config_json    TEXT NOT NULL DEFAULT '{}',
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  CHECK(enabled IN (0, 1)),
  CHECK(status IN ('active', 'suspended', 'disabled', 'expired')),
  UNIQUE(tenant_id, service_key),
  FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);

CREATE INDEX IF NOT EXISTS ix_local_service_entitlements_status
  ON local_service_entitlements (tenant_id, status, service_key);
