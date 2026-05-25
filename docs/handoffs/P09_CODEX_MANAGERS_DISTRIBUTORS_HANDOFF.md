# P09 Codex Handoff — Managers and Distributors

## Commit

This commit. The exact final hash is recorded in the execution report.

## Scope

Prompt 09 adds a Business OS operational layer for managers and distributors inside `radius-module` only. It does not touch `radius-module-admin`, Flutter, live MikroTik execution, or RADIUS auth/accounting behavior.

## Implemented

- Added additive migration `059_manager_distributor_policies.sql`.
- Added `ManagerDistributorOpsService` for:
  - manager/distributor policies,
  - permission checks,
  - credit/loan limit checks,
  - wallet recharge,
  - pending subscriber creation without RADIUS activation,
  - scoped subscribers/cards/batches/events,
  - manager profit share summaries.
- Added routes under `/admin/radius/business-operators`.
- Added templates:
  - `business_operators.html`
  - `business_operator_profile.html`
- Registered the routes in the existing radius blueprint.
- Added focused regression tests in `tests/test_manager_distributor_ops.py`.

## Safety

- No live apply paths were introduced.
- Subscriber creation through this service remains `status=pending` and reports `applied_to_radius=False`.
- Card batch costing reuses the existing card pricing service and manager wallet deduction flow.
- Existing RADIUS auth/accounting logic was not modified.

## Verification

- `python -m compileall app`
- `python -m pytest tests/test_manager_distributor_ops.py -q`
- Additional combined regression commands are recorded in the execution report.

## Notes

- Distribution-level operational actions are represented in the same service surface, but tests focus on manager flows because the existing card costing service currently charges manager wallets.
- Existing unrelated dirty files were intentionally excluded from staging.
