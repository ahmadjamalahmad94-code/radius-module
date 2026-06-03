# Electronic Cards — Purchases File + Sale Modes (instant vs inventory)

Branch: `feature/ecards-purchases-and-packages` · Migration: **095** (additive) ·
Scope: the electronic-cards marketplace (`/admin/radius/card-marketplace`).

## Why
Previously every customer purchase **minted a brand-new 1-card batch + card row**,
so selling 1000 cards produced a 1000-row flat, endless "recent purchases" list,
and there was no way to sell pre-printed booth/exhibition stock. This feature adds:

- **A) Per-offer "purchases file"** — the cards sold under each offer, grouped and
  paginated, with full per-card detail.
- **B) Two sale modes per offer** — *instant* (mint on sale, the old behaviour) and
  *inventory* (deduct from a pre-loaded stock pool).

## Data model (migration 095, additive, tenant-scoped)
| Table | Added | Meaning |
|---|---|---|
| `card_marketplace_packages` | `sale_mode` (`'instant'`\|`'inventory'`, default `instant`) | per-offer mode |
| | `inventory_total`, `inventory_sold` | O(1) counters; **remaining = total − sold** |
| `card_batches` | `package_id` (FK, nullable) + index | links a stock/minted batch to its offer |
| `cards` | `purchase_id` (nullable, partial-unique) | `NULL` = in stock; set when sold (no double-sell) |
| `card_user_purchases` | index `(tenant_id, package_id, id DESC)` | fast per-offer file + global panel |

No table rewrites; existing offers default to `instant` → zero behaviour change on deploy.

## A) Purchases file
- Service: `CardUsersMarketplaceService.purchases_file(package_id, page, per_page)` →
  paginated rows joined to the card (username/password), buyer (`card_users`),
  price, datetime, status, and **download/upload aggregated from `radacct`**; plus
  the stored counters (sold/remaining/total) as the single source of truth.
- Global panel: `recent_purchases(page, per_page)` (adds the offer name).
- Route: `GET /card-marketplace/packages/<id>/file` →
  `card_marketplace_package_file.html`, the **first adopter** of the unified
  `_components/table.html::hub_table` + `pagination.html::hub_pagination`.

## B) Sale modes
- **Default + override:** a section-wide default (tenant setting
  `cards.default_sale_mode`) that new offers inherit, plus a per-offer override.
  Helpers: `set_default_sale_mode`, `set_package_sale_mode`; routes
  `POST /card-marketplace/default-mode` and `…/packages/<id>/mode`.
- **Inventory stock:** `add_inventory_stock(package_id, cards=… | count=…)` builds a
  package-linked batch and bumps `inventory_total`. The web action
  `POST /card-marketplace/packages/<id>/inventory` either generates `N` cards **or
  imports an Excel/CSV/PDF** of pre-made `username/password` rows by **reusing the
  existing `app/radius/services/cards_import_engine.py` `parse()`** (the same engine
  the card-batch import uses — not reinvented).
- **Sale (`purchase_package`):** branches on `sale_mode`:
  - *instant* → `_generate_card_for_package` (mint).
  - *inventory* → `_claim_inventory_card`: an **atomic guarded claim** of the next
    free stock card (`UPDATE cards SET purchase_id … WHERE purchase_id IS NULL` +
    rowcount check) and `inventory_sold += 1` — two concurrent buyers can never grab
    the same card; out-of-stock is checked **before** charging.

## Orphan-card safety (note on "one transaction")
The finance services (`wallets.debit/credit`, `ledger.write_entry`,
`events.record_event`) each open their own `transaction()` on the shared
thread-local SQLite connection, so a single wrapping transaction across the whole
purchase is not possible without refactoring them. Instead `purchase_package` uses a
**payment-first compensation (saga)**: it debits first, then mints/claims the card and
writes the records; on **any** failure it **refunds the debit and undoes the card**
(`_discard_minted_card` / `_release_inventory_card`). Net effect: no orphan card and
no charged-but-no-card. This also fixed the prior "card created outside the financial
txn" finding. The sold counter is now the stored `inventory_sold` everywhere
(resolves the marketplace-vs-pricing sold mismatch).

## Tests
`tests/test_qa_ecards_inventory.py` (11): stock add (generate + import, service +
route), atomic claim / no-double-sell, counters, out-of-stock-no-charge, section
default + per-offer override, paginated purchases file, global recent panel, page
renders. Instant flow unchanged (`tests/test_card_users_marketplace.py`: 8).

## Commits
`251e96e7` migration · `d2d210e6` service · `9b747b7c` read-side ·
`47fd4172` routes + purchases-file UI · `7a1dd54f` marketplace UI ·
`c62c3b87` import-route test.
