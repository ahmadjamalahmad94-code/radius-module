# Prompt 08 — Cards, Batches, Retail/Wholesale Pricing, Manager Costing Handoff

## Commit

This commit. The exact final hash is recorded in the execution report.

## What changed

- Added additive pricing/costing migration:
  - retail/wholesale/min/max-discount fields on marketplace packages
  - allowed manager/distributor ID lists
  - `card_batch_financial_costs` for batch financial records
- Added `CardPricingService` for:
  - package pricing updates
  - immutable price snapshot creation
  - costed card batch creation
  - manager-wallet wholesale-cost debit
  - Business OS ledger, revenue, and event records
  - read-only cards summary metrics
- Added web routes:
  - `GET /admin/radius/card-pricing`
  - `POST /admin/radius/card-pricing/packages/<id>`
  - `POST /admin/radius/card-pricing/batches`
  - `GET /admin/radius/card-pricing/batches/<id>`
  - `GET /admin/radius/card-pricing/summary.json`
- Added pricing and batch-financial templates.

## Safety notes

- Existing card batch UI files already had unrelated dirty changes and were intentionally not edited or staged.
- No live RADIUS change was introduced.
- No destructive deletion route was added.
- The costing path creates a financial batch record; card generation remains in the existing card subsystem.

## Tests run

- `python -m compileall app` — passed
- `python -m pytest tests/test_card_pricing_accounting.py -q` — 5 passed
- `python -m pytest tests/test_card_pricing_accounting.py tests/test_card_users_marketplace.py tests/test_finance_center_web.py tests/test_business_os_core_foundations.py -q` — 20 passed
- `git diff --check` scoped to Prompt 08 files — passed; Git emitted existing LF-to-CRLF warnings

## Remaining risks

- The new costing route does not generate card credentials; it intentionally leaves card generation in the existing safe card flow.
- Distributor margin is surfaced as summary placeholders until distributor-specific profit-share rules are finalized.
- SQLite migrations are additive, but the `ALTER TABLE` statements assume migration order 057 then 058.
