# Prompt 06 — Subscriber 360 Financial Lifecycle Handoff

## Commit

This commit. The exact final hash is recorded in the execution report.

## What changed

- Added a read-only `Subscriber360Service` that aggregates subscriber overview, financials, usage sessions, services, devices/MACs, timeline, notes, login events, Business OS events, wallet balances, payments, loans, and ledger entries.
- Added pure renewal lifecycle calculations for partial renewal, discount renewal, debt-supported renewal, and loan-day deductions. Renewal previews explicitly return `applied_to_radius=false`.
- Added a simple loan policy engine supporting profile rules, subscriber overrides, loan day sequences such as `[2, 1]`, count limits, cooldown blocking, and approval-required flags.
- Added Subscriber 360 routes:
  - `GET /admin/radius/subscribers`
  - `GET /admin/radius/subscribers/<id>`
  - `GET /admin/radius/users/<username>/360`
  - `POST /admin/radius/subscribers/<id>/renewal-preview`
- Added a new Subscriber 360 template with tabs for Overview, Financial, Usage & Sessions, Services, Devices/MACs, Timeline, Messages, Notes, and Login Events.

## Safety notes

- No RADIUS auth/accounting behavior was changed.
- No live RADIUS activation is called from renewal preview.
- No MikroTik or server automation was added.
- Existing `/admin/radius/users/<username>/profile` remains available.
- Existing accounting payment/loan creation flows remain unchanged.

## Tests run

- `python -m compileall app` — passed
- `python -m pytest tests/test_subscriber_360.py -q` — 5 passed
- `python -m pytest tests/test_subscriber_360.py tests/test_finance_center_web.py tests/test_business_os_api_contracts.py tests/test_business_os_access_foundations.py -q` — 19 passed
- `git diff --check` — passed; Git emitted existing LF-to-CRLF warnings

## Remaining risks

- Subscriber messages are represented as an empty slice until a dedicated message source is selected.
- The new route is intentionally read-mostly; actual renewals still need to go through existing payment/accounting workflows.
- Wallet filtering uses the existing `WalletService.list_wallets` API and filters owner IDs in service code because the wallet list API currently filters owner type but not owner ID.
