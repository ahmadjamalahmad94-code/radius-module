-- R2 operations foundation: distributors, scoped batches, schedules,
-- print templates, and backup run tracking.
-- Additive only. No destructive schema changes.

CREATE TABLE distributors (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    admin_id          INTEGER,
    name              TEXT NOT NULL,
    display_name      TEXT NOT NULL DEFAULT '',
    email             TEXT NOT NULL DEFAULT '',
    phone             TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'active',
    permissions_json  TEXT NOT NULL DEFAULT '[]',
    scope_json        TEXT NOT NULL DEFAULT '{}',
    balance           REAL NOT NULL DEFAULT 0,
    credit_limit      REAL NOT NULL DEFAULT 0,
    debt_balance      REAL NOT NULL DEFAULT 0,
    created_by        TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX idx_distributors_name ON distributors(tenant_id, name);
CREATE INDEX idx_distributors_status ON distributors(tenant_id, status);

CREATE TABLE distributor_ledger_entries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    distributor_id    INTEGER NOT NULL,
    entry_type        TEXT NOT NULL,
    direction         TEXT NOT NULL DEFAULT 'debit',
    amount            REAL NOT NULL,
    currency          TEXT NOT NULL DEFAULT 'JOD',
    related_type      TEXT NOT NULL DEFAULT '',
    related_id        INTEGER,
    status            TEXT NOT NULL DEFAULT 'posted',
    notes             TEXT NOT NULL DEFAULT '',
    created_by        TEXT NOT NULL DEFAULT '',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (distributor_id) REFERENCES distributors(id) ON DELETE RESTRICT
);
CREATE INDEX idx_dist_ledger_dist_date
ON distributor_ledger_entries(tenant_id, distributor_id, created_at);

CREATE TABLE card_batch_assignments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    batch_id          INTEGER NOT NULL,
    distributor_id    INTEGER NOT NULL,
    assigned_by       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'assigned',
    notes             TEXT NOT NULL DEFAULT '',
    assigned_at       TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (batch_id) REFERENCES card_batches(id) ON DELETE RESTRICT,
    FOREIGN KEY (distributor_id) REFERENCES distributors(id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX idx_batch_assignment_unique
ON card_batch_assignments(tenant_id, batch_id);
CREATE INDEX idx_batch_assignment_distributor
ON card_batch_assignments(tenant_id, distributor_id, status);

ALTER TABLE card_batches ADD COLUMN assigned_to TEXT NOT NULL DEFAULT '';
ALTER TABLE card_batches ADD COLUMN distributor_id INTEGER;

ALTER TABLE access_plans ADD COLUMN service_scope TEXT NOT NULL DEFAULT 'both';
ALTER TABLE access_plans ADD COLUMN loan_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN max_loan_minutes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN speed_override_allowed INTEGER NOT NULL DEFAULT 0;

ALTER TABLE payment_transactions ADD COLUMN distributor_id INTEGER;
CREATE INDEX idx_payments_distributor
ON payment_transactions(tenant_id, distributor_id);

CREATE TABLE bandwidth_schedules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    plan_id           INTEGER NOT NULL,
    name              TEXT NOT NULL,
    starts_at_time    TEXT NOT NULL,
    ends_at_time      TEXT NOT NULL,
    speed_down_kbps   INTEGER NOT NULL DEFAULT 0,
    speed_up_kbps     INTEGER NOT NULL DEFAULT 0,
    cir_down_kbps     INTEGER NOT NULL DEFAULT 0,
    cir_up_kbps       INTEGER NOT NULL DEFAULT 0,
    restore_mode      TEXT NOT NULL DEFAULT 'profile_default',
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_by        TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES access_plans(id) ON DELETE RESTRICT
);
CREATE INDEX idx_bandwidth_schedules_plan
ON bandwidth_schedules(tenant_id, plan_id, enabled);

CREATE TABLE bandwidth_schedule_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    schedule_id       INTEGER NOT NULL,
    action            TEXT NOT NULL,
    status            TEXT NOT NULL,
    message           TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (schedule_id) REFERENCES bandwidth_schedules(id) ON DELETE RESTRICT
);
CREATE INDEX idx_bandwidth_schedule_logs
ON bandwidth_schedule_logs(tenant_id, schedule_id, created_at);

CREATE TABLE card_print_templates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    name              TEXT NOT NULL,
    orientation       TEXT NOT NULL DEFAULT 'portrait',
    cards_per_row     INTEGER NOT NULL DEFAULT 2,
    cards_per_column  INTEGER NOT NULL DEFAULT 5,
    page_size         TEXT NOT NULL DEFAULT 'A4',
    show_qr           INTEGER NOT NULL DEFAULT 1,
    username_x        REAL NOT NULL DEFAULT 0,
    username_y        REAL NOT NULL DEFAULT 0,
    password_x        REAL NOT NULL DEFAULT 0,
    password_y        REAL NOT NULL DEFAULT 0,
    qr_x              REAL NOT NULL DEFAULT 0,
    qr_y              REAL NOT NULL DEFAULT 0,
    font_size         INTEGER NOT NULL DEFAULT 12,
    color             TEXT NOT NULL DEFAULT '#1f2937',
    layout_json       TEXT NOT NULL DEFAULT '{}',
    created_by        TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_card_print_templates_name
ON card_print_templates(tenant_id, name);

CREATE TABLE backup_jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    name              TEXT NOT NULL,
    schedule          TEXT NOT NULL DEFAULT 'manual',
    target            TEXT NOT NULL DEFAULT 'local',
    enabled           INTEGER NOT NULL DEFAULT 1,
    last_status       TEXT NOT NULL DEFAULT 'never_run',
    last_run_at       TEXT,
    last_message      TEXT NOT NULL DEFAULT '',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_backup_jobs_name ON backup_jobs(tenant_id, name);

CREATE TABLE backup_run_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,
    job_id            INTEGER,
    status            TEXT NOT NULL,
    path              TEXT NOT NULL DEFAULT '',
    message           TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES backup_jobs(id) ON DELETE SET NULL
);
CREATE INDEX idx_backup_run_logs
ON backup_run_logs(tenant_id, created_at);
