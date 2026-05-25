# P02 Core Database and Engine Foundations Handoff

## Prompt

Prompt 02 - Core Database Foundations: Wallet, Ledger, Events, Pricing
Snapshots.

## What Changed

Added the first additive Business OS database and service foundation for:

- wallets,
- wallet transactions,
- immutable ledger entries,
- price snapshots,
- business events,
- revenue record foundation,
- profit share foundation,
- archive snapshot foundation,
- approval request foundation.

No UI was added. No live RADIUS behavior was changed. Existing RADIUS
auth/accounting paths were not rewritten.

## Migration Number

- `056_business_os_core_foundations.sql`

## Tables Created

- `wallets`
- `wallet_transactions`
- `ledger_entries`
- `price_snapshots`
- `business_events`
- `revenue_records`
- `profit_shares`
- `archive_snapshots`
- `approval_requests`

All tables are additive. Financial money fields in the new Business OS tables
use integer minor units such as `amount_minor`, `balance_minor`, and
`retail_price_minor` to avoid floating-point accounting drift.

## Services Added

- `WalletService`
  - create wallet
  - credit wallet
  - debit wallet
  - writes wallet transactions, ledger entries, and business events
- `LedgerService`
  - write immutable ledger entries
  - records financial business events
- `EventService`
  - append-only business event recording
  - filtered event listing
- `PricingSnapshotService`
  - captures immutable pricing snapshots
  - records financial business events

Service module:

- `app/radius/services/business_os_finance.py`

## Tests

Added:

- `tests/test_business_os_core_foundations.py`

Coverage:

- migration creates required tables,
- wallet create/credit/debit,
- invalid amount and negative balance protection,
- immutable ledger entry write,
- event recording and filtering,
- price snapshot capture.

## Verification Results

- `python -m compileall app`: passed.
- `python -m pytest tests/test_business_os_core_foundations.py -q`: passed,
  6 passed, 1155 warnings in 7.01s.
- `python -m pytest tests/test_accounting_loans_foundation.py -q`: passed,
  7 passed, 57 warnings in 4.23s.
- `python -m pytest tests/test_api_dashboard.py tests/test_api_tools.py -q`:
  passed, 6 passed, 6 warnings in 3.84s.
- `git diff --check`: passed with line-ending warnings only.
- `git status --short`: unrelated pre-existing dirty files remain excluded from
  staging.

## Known Limitations

- P02 does not expose API routes; P03 owns API contracts.
- P02 does not retrofit existing accounting flows into the new Business OS
  ledger. Existing accounting remains untouched.
- Revenue and profit share tables are foundations only; full calculation flows
  are scheduled for later prompts.
- Approval and archive tables are foundations only; workflow behavior is
  scheduled for later prompts.
- Scope enforcement is not complete yet; P04 owns permission/scope/audit gates.

## Next Prompt

P03 API Contracts and Service Layer.

P03 should read:

- `docs/business_os/MASTER_ARCHITECTURE.md`
- `docs/business_os/DOMAIN_MODEL.md`
- `docs/handoffs/P01_CODEX_CODEX_MASTER_ARCHITECTURE_HANDOFF.md`
- this handoff

P03 should expose stable internal/backend JSON API contracts for wallets,
ledger, revenue, events, pricing snapshots, and business summary without
building the full UI.
