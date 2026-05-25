-- Events, audit, investigation, risk, and fraud foundations.
-- Additive only. Source events remain append-only and are never deleted here.

CREATE TABLE IF NOT EXISTS fraud_flags (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    flag_key            TEXT NOT NULL,
    severity            TEXT NOT NULL DEFAULT 'warning',
    status              TEXT NOT NULL DEFAULT 'open',
    entity_type         TEXT NOT NULL DEFAULT '',
    entity_id           INTEGER,
    event_id            INTEGER,
    risk_score          INTEGER NOT NULL DEFAULT 0,
    summary             TEXT NOT NULL DEFAULT '',
    evidence_json       TEXT NOT NULL DEFAULT '{}',
    correlation_id      TEXT NOT NULL DEFAULT '',
    created_by          TEXT NOT NULL DEFAULT 'risk_engine',
    created_at          TEXT NOT NULL,
    resolved_at         TEXT,
    CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    CHECK (status IN ('open', 'investigating', 'resolved', 'false_positive'))
);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_status
ON fraud_flags(tenant_id, status, severity, id DESC);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_entity
ON fraud_flags(tenant_id, entity_type, entity_id, id DESC);

CREATE TABLE IF NOT EXISTS investigations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    title               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',
    severity            TEXT NOT NULL DEFAULT 'warning',
    entity_type         TEXT NOT NULL DEFAULT '',
    entity_id           INTEGER,
    opened_by           TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    linked_events_json  TEXT NOT NULL DEFAULT '[]',
    linked_flags_json   TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    closed_at           TEXT,
    CHECK (status IN ('open', 'in_review', 'closed')),
    CHECK (severity IN ('info', 'warning', 'error', 'critical'))
);
CREATE INDEX IF NOT EXISTS idx_investigations_status
ON investigations(tenant_id, status, severity, id DESC);
CREATE INDEX IF NOT EXISTS idx_investigations_entity
ON investigations(tenant_id, entity_type, entity_id, id DESC);
