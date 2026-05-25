# P04 Codex Permission Audit Handoff

## What Changed

- Added Business OS permission constants for finance, wallets, ledger, subscribers, card users, cards, managers, distributors, notifications, campaigns, events, reports, speed control, and approvals.
- Added ownership scope vocabulary for company, branch, manager, distributor, subscriber, and card user.
- Added `ScopeResolver` to resolve lightweight actor contracts into allowed owner scopes.
- Added `LimitPolicy` with conservative defaults for free days, loan count, discounts, batch creation, wallet debit, and approval thresholds.
- Added `SafetyGateService` to combine permission checks with limit policy results.
- Added `AuditGuard` to record sensitive Business OS actions into the event stream and optionally into the legacy audit service.

## Safety Notes

- This prompt did not retrofit existing routes or alter existing enforcement paths.
- No existing RADIUS authentication/accounting behavior was changed.
- No UI or Flutter files were touched.
- The foundation is additive and intended for future Business OS features to adopt explicitly.
- Sensitive audit events are recorded under the existing `system` event category with `business_os.*` event keys because the current event category vocabulary does not include a separate `audit` category.

## Tests Added

- `tests/test_business_os_access_foundations.py`

Coverage:
- permission catalog contains required keys
- scope resolver maps actors to owner scopes
- limit policy flags limits and approval thresholds
- safety gate blocks missing permissions and applies limit checks
- audit guard records a durable business event

## Verification

- `python -m compileall app`
- `python -m pytest tests/test_business_os_access_foundations.py -q`
- `python -m pytest tests/test_api_auth_security.py tests/test_api_dashboard.py tests/test_business_os_api_contracts.py -q`
- `git diff --check`
- `git status --short`

## Next Prompt

P05 should build the Finance Center backend and web UI using the wallet, ledger, event, pricing snapshot, permission, and safety foundations.
