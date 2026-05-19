# Customer Requirements Traceability

Statuses:

- `existing`: verified in code and tests/routes.
- `partial`: real support exists, but not enough for the customer requirement.
- `missing`: no verified implementation found.
- `dangerous-to-implement-now`: would require destructive or broad changes.
- `needs-design`: requires product/data model decisions first.

| Requirement | Status | Existing support | Missing backend | Missing API | Missing UI | Tests needed | Recommended slice |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Subscriber loan/credit system | missing | Subscribers have `balance`; users can be extended via `accounts_extend` and web `users_extend` | Loan table, settlement table, max limits, approval state, actor/duration/value/reason | grant loan, settle loan, list loan history | Subscriber loan panel | loan grant/settle accounting tests | R2 |
| Payment settlement reducing duration/value/debt | needs-design | `invoices` have amount/status/direction; subscriber recharges exist | Append-only ledger and settlement rules | settlement endpoints | Payment/settlement UI | ledger invariants | R2 |
| Recycle bin / soft delete | dangerous-to-implement-now | Cards have `revoked`; vouchers can be revoked | Soft-delete fields and archive policy for subscribers/cards/batches/plans/admins/NAS; financial void/reversal | restore/archive endpoints or backward-compatible delete response | Trash/recycle views | delete behavior regression tests | R1B |
| Financial records never hard-deleted | partial | `invoices_repo` updates status; no delete repo found for invoices | Ledger immutability policy and void/reversal rows | void/reversal endpoints | Financial correction UI | no-hard-delete financial tests | R2 |
| Direct profile/offer/speed control | partial | AccessPlan has speed/quota/CIR fields; profiles API CUD exists; tools page can set speeds | Apply-now semantics, sync logs, no-disconnect capability detection | apply profile changes, sync status | clearer plan speed editor/logs | adapter sync tests | R2 |
| Hotspot/broadband offer type | partial | `service_type`, `hotspot_enabled`, `ppp_enabled` exist in plans/API | Enforcement semantics in policy/router sync | expose validation/status | UI explanation | policy tests | R2 |
| Pool selection | partial | `ip_pools`, `pool_id`, `address_pool`, `framed_pool` exist | Consistent enforcement across adapter modes | profile patch already partial | plan UI exists | policy/router tests | R2 |
| Daily/monthly quota | partial | plan and subscriber quota columns exist | Accounting enforcement/reporting completeness | profile/account patch exists | forms exist | quota policy tests | R2 |
| Loan eligibility per offer | missing | No loan eligibility field verified | Add offer-level loan settings | profile fields/endpoints | plan form field | loan eligibility tests | R2 |
| Time-based bandwidth schedule | missing | `allowed_hours_from/to`, `offer_hours_from/to` exist; no schedule table/job | Schedules, resolver, worker, audit | schedule CRUD/status | schedule editor | overlapping/timezone tests | R3 |
| Pressure-based bandwidth policy | missing | No pressure metrics policy found | Load metrics, thresholds, auto apply/revert | policy endpoints | monitoring UI | policy safety tests | R4 |
| Daily/monthly/yearly sales | partial | Dashboard metrics and invoices exist | Ledger/report aggregation | report endpoints by period | report pages | aggregation tests | R2/R3 |
| Card sales | partial | Cards/batches have prices and generated/used counts | Sales ownership and ledger posting | card sales reports | card sales report UI | sales tests | R2 |
| Partial payments | missing | Invoice amount/status only | Payment allocation records | payment endpoints | payment forms | partial payment tests | R2 |
| Discounts/custom prices | partial | plan/card prices exist | Discount/custom price record and audit | payment/activation endpoints | UI controls | discount tests | R2 |
| Distributor debt/profit | missing | admins/roles exist; card batches have `manager_id` | Distributor entity/scope/debt/profit ledger | distributor endpoints | distributor screens | debt/profit tests | R2 |
| Managers/distributors usernames/passwords | partial | `admins`, `roles`, memberships, API/admin UI exist | distributor-specific scope rules and assigned batch ownership | scoped endpoints | distributor assignment UI | permission tests | R2 |
| Custom permissions | partial | `roles.permissions` and permission catalog exist | Enforcement gaps on API/write paths | no shape change | roles UI exists | permission enforcement tests | R2 |
| Scoped visibility | missing | tenant scoping exists | manager/distributor scoping | scoped filters | scoped UI | data isolation tests | R2 |
| Assigned card batches | partial | `card_batches.manager_id` exists | assignment flow and sales ownership | assign/reassign endpoint | assignment UI | batch ownership tests | R2 |
| Card batch status counts | partial | `card_batches.count/generated/used/status`; card `used/revoked/expire_at` | status taxonomy and derived counters | batch detail stats | status colors/counts | batch stats tests | R1B/R2 |
| Reports from date to date | partial | audit/accounting endpoints have limited list behavior; reports routes exist | report query service for financial periods | date-range report endpoints | reports pages | range tests | R2/R3 |
| Immutable financial reports | missing | none verified | report snapshots or ledger-derived immutable reports | report snapshot endpoints | immutable report UI | immutability tests | R3 |
| Card checker | missing | `cards_repo.get_card_by_username`; card list/get endpoints | checker service joining batch/plan/subscriber/session/accounting | checker endpoint | checker page | card state tests | R1B |
| Card printing templates | missing | card generate/list pages exist | template table/storage/render service | template CRUD/render | print designer | template/render tests | R3 |
| Google Drive backup | missing | deploy backup scripts exist, no Drive integration | scheduler, Drive auth, status table | backup status/config | backup settings | backup status tests | R4 |
| Online users list | existing | `sessions_online`, web `/online`, adapter list_online, accounting puller | richer state colors/type filters if needed | existing `/api/v1/sessions/online` | existing `/admin/radius/online` | state color tests | R1B/R2 |
| Card online vs subscriber online | partial | OnlineSession has `user_type`; web supports `type=card` links | ensure adapter marks card/user type reliably | maybe filter param | UI exists partially | adapter classification tests | R1B |
| NAS/server management | existing | NAS migrations, repo/service, web and API CUD, test endpoint | soft-delete and deeper health history | existing `/api/v1/nas` | devices pages exist | no-hard-delete tests later | R1B/R2 |
| Offer advanced options | partial | many plan fields exist in migration 012/types/API/forms | semantics/enforcement for every option | profile API mostly present | plan form exists | policy enforcement tests | R2 |

## Verified Hard-Delete Risks

- `subscribers_repo.delete_subscriber` executes `DELETE FROM subscribers`.
- `plans_repo.delete_plan` executes `DELETE FROM access_plans`.
- `nas_repo.delete_nas` executes `DELETE FROM nas_devices`.
- `admins_repo.delete_admin` executes `DELETE FROM admins`.
- `admins_repo.delete_role` executes `DELETE FROM roles`.
- Other module repos delete pools, bandwidth profiles, services, share groups,
  MikroTik configs, webhook subscriptions, and maintenance can delete old audit
  rows.

These must not be rewritten broadly in R1A. They need a planned additive
soft-delete migration and compatibility strategy.
