-- Card OFFERS — super-admin-defined commercial templates with a per-manager
-- visibility allow-list (opt-in).
--
-- An OFFER is owned by the super-admin and carries the commercial terms:
--   * duration_minutes  — the locked time the generated cards are valid for
--   * wholesale_minor   — سعر الجملة, charged against the sub-admin's balance
--                         when they generate a package from the offer
--   * selling_minor     — سعر البيع, the sub-admin's resale price
-- When a SUB-ADMIN creates a package (card_batch) from an offer the price+time
-- are injected from these columns and locked server-side; only generation
-- params (count / code length / charset / type) stay editable.
--
-- VISIBILITY is an explicit per-manager allow-list (card_offer_visibility).
-- A manager not on the list must not see or use the offer anywhere. Default is
-- NOT shared (opt-in) — the safe default. The super-admin always has full
-- access regardless of the allow-list.
--
-- Additive only; no data migration.

CREATE TABLE IF NOT EXISTS card_offers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    name            TEXT    NOT NULL,
    plan_id         INTEGER,                              -- optional plan link
    duration_minutes INTEGER NOT NULL DEFAULT 0,          -- locked card validity
    wholesale_minor INTEGER NOT NULL DEFAULT 0,           -- سعر الجملة (× 100)
    selling_minor   INTEGER NOT NULL DEFAULT 0,           -- سعر البيع   (× 100)
    currency        TEXT    NOT NULL DEFAULT 'JOD',
    active          INTEGER NOT NULL DEFAULT 1,
    notes           TEXT    NOT NULL DEFAULT '',
    created_by      TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS ix_card_offers_active
    ON card_offers (tenant_id, active, id DESC);

-- Per-manager (sub-admin) allow-list. One row = one manager may see/use the
-- offer. Absence of any row = visible only to the super-admin (opt-in default).
CREATE TABLE IF NOT EXISTS card_offer_visibility (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    offer_id    INTEGER NOT NULL,
    admin_id    INTEGER NOT NULL,                         -- the sub-admin (manager)
    created_at  TEXT    NOT NULL,
    UNIQUE (offer_id, admin_id),
    FOREIGN KEY (offer_id) REFERENCES card_offers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_card_offer_visibility_admin
    ON card_offer_visibility (tenant_id, admin_id, offer_id);
