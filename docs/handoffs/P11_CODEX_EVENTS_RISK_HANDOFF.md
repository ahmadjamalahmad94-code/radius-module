# P11 Codex Handoff — Events, Audit, Risk

## Commit

This commit. The exact final hash is recorded in the execution report.

## Scope

Prompt 11 adds an Events/Risk/Investigation Center inside `radius-module` only. It reuses the existing append-only `business_events` stream and adds fraud/risk review structures without modifying RADIUS auth/accounting behavior.

## Implemented

- Added additive migration `061_events_risk_center.sql`.
- Added `EventsRiskCenterService` for:
  - event filtering by category, severity, actor, target, date, and correlation ID,
  - event detail and entity timeline,
  - fraud flag creation,
  - investigation tracking,
  - risk rule scans for negative wallets, repeated failed logins, repeated loans, discounts, and revenue/ledger mismatch.
- Added web routes under `/admin/radius/events`.
- Added templates for:
  - events list,
  - event detail,
  - risk center,
  - security events,
  - investigations.
- Added focused tests in `tests/test_events_risk_center.py`.

## Safety

- Source events are append-only and no delete route was added.
- Risk scans create review flags only; they do not repair, reverse, or mutate router/RADIUS/accounting state.
- Existing audit log routes and legacy audit tables remain untouched.

## Verification

- `python -m compileall app`
- `python -m pytest tests/test_events_risk_center.py -q`
- Additional combined regression commands are recorded in the execution report.

## Notes

- The Business OS event categories remain constrained by the existing `business_events` CHECK constraint. Product-facing categories such as card users/managers are represented through target types and existing categories.
