-- 091_company_inventory_expenses.sql
-- Internal "Company Inventory & Expenses" notebook for the Finance
-- section. THREE independent tables. They do NOT reference or affect
-- the accounting ledger, payments, customer/distributor balances,
-- card sales, subscriptions, revenue, or profit. Costs/amounts here
-- are informational only.
-- Idempotent: CREATE TABLE IF NOT EXISTS so re-running is a no-op.

-- ── Catalogue of company inventory items ──────────────────────────
CREATE TABLE IF NOT EXISTS company_inventory_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    name                TEXT NOT NULL,
    category            TEXT NOT NULL DEFAULT '',
    unit                TEXT NOT NULL DEFAULT '',
    low_stock_threshold REAL,                 -- NULL = no low-stock alert
    notes               TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_company_inv_items_tenant
    ON company_inventory_items(tenant_id, is_active);

-- One active item name per tenant (case-sensitive match in service).
CREATE UNIQUE INDEX IF NOT EXISTS uq_company_inv_items_name
    ON company_inventory_items(tenant_id, name);

-- ── Stock movements (incoming / usage / adjustment) ───────────────
CREATE TABLE IF NOT EXISTS company_inventory_movements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    item_id             INTEGER NOT NULL,
    movement_type       TEXT NOT NULL,        -- 'incoming' | 'usage' | 'adjustment'
    quantity            REAL NOT NULL,        -- always stored positive for
                                              -- incoming/usage; adjustment may
                                              -- be signed.
    unit_cost           REAL,                 -- informational only
    total_cost          REAL,                 -- informational only
    supplier            TEXT,
    reference           TEXT,
    usage_reason        TEXT,
    location            TEXT,
    technician          TEXT,
    related_customer_id INTEGER,              -- informational only; never used
                                              -- for any financial calculation
    movement_date       TEXT NOT NULL,
    notes               TEXT,
    created_by_admin_id INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES company_inventory_items(id)
);

CREATE INDEX IF NOT EXISTS idx_company_inv_moves_item
    ON company_inventory_movements(tenant_id, item_id);
CREATE INDEX IF NOT EXISTS idx_company_inv_moves_type_date
    ON company_inventory_movements(tenant_id, movement_type, movement_date);

-- ── Company operating expenses (not inventory-related) ────────────
CREATE TABLE IF NOT EXISTS company_expenses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    title               TEXT NOT NULL,
    category            TEXT NOT NULL DEFAULT '',
    amount              REAL NOT NULL,        -- informational only
    expense_date        TEXT NOT NULL,
    paid_to             TEXT,
    payment_method      TEXT,
    reference           TEXT,
    notes               TEXT,
    created_by_admin_id INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_company_expenses_tenant_date
    ON company_expenses(tenant_id, expense_date);
CREATE INDEX IF NOT EXISTS idx_company_expenses_category
    ON company_expenses(tenant_id, category);
