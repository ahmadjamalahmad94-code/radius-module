# P01 Codex Master Architecture Handoff

## Prompt

Prompt 01 - Master Architecture, Domain Map, and Execution Contract.

## What Was Created

Created the documentation foundation for the HobeRadius Intelligent ISP Business
Operating System:

- `docs/business_os/MASTER_ARCHITECTURE.md`
- `docs/business_os/DOMAIN_MODEL.md`
- `docs/business_os/SECTION_MAP.md`
- `docs/business_os/EXECUTION_RULES.md`

This prompt is documentation/contract only. It introduced no migrations, no
runtime behavior, no UI code, no Flutter changes, and no RADIUS auth/accounting
changes.

## Confirmed Architecture

The architecture defines HobeRadius as a serious ISP Business Operating System
centered on:

- subscribers,
- card users,
- cards and batches,
- profiles and packages,
- wallets,
- immutable ledger,
- debts and loans,
- retail/wholesale pricing snapshots,
- revenue and profit shares,
- events and audit,
- notifications and campaigns,
- operations,
- approvals and requests,
- reports and archives,
- subscriber and card-user portals.

Core confirmed rules:

- backend is source of truth,
- Flutter and web are clients,
- financial ledger is immutable,
- pricing is snapshot-based,
- access is scope-aware,
- notifications are event-driven,
- dashboards must drill down to source records,
- financial records are never hard-deleted,
- audit-first design applies to sensitive actions.

## Files Changed

- `docs/business_os/MASTER_ARCHITECTURE.md`
- `docs/business_os/DOMAIN_MODEL.md`
- `docs/business_os/SECTION_MAP.md`
- `docs/business_os/EXECUTION_RULES.md`
- `docs/handoffs/P01_CODEX_CODEX_MASTER_ARCHITECTURE_HANDOFF.md`

## Verification

- `python -m compileall app`: passed.
- Pytest: not run because Prompt 01 is documentation/contract only and does not
  change Python, migrations, routes, APIs, UI, or runtime behavior.
- `git diff --check`: passed with line-ending warnings only.
- `git status --short`: unrelated pre-existing dirty files remain excluded from
  staging; new docs are under ignored `docs/*` paths and must be force-staged
  explicitly.

## Known Limitations

- No database tables exist yet for the Business OS model.
- No APIs exist yet for wallets, ledger, events, pricing snapshots, or revenue.
- No UI exists yet for the Business OS sections.
- No Flutter parity exists.
- This handoff is a contract for P02, not an implementation.

## Next Prompt

P02 Core Database and Engine Foundations.

P02 should read:

- `docs/business_os/MASTER_ARCHITECTURE.md`
- `docs/business_os/DOMAIN_MODEL.md`
- this handoff

P02 should implement additive database foundations for wallet, ledger, events,
pricing snapshots, ownership/scope foundations, revenue/profit foundations, and
minimal safe services.
