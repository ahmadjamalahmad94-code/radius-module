# Customer Requirements Traceability

Statuses:

- `existing`: verified in code and tests/routes.
- `partial`: real support exists, but not enough for the customer requirement.
- `missing`: no verified implementation found.
- `dangerous-to-implement-now`: would require destructive or broad changes.
- `needs-design`: requires product/data model decisions first.

| Requirement | Status | Existing support | Missing backend | Missing API | Missing UI | Tests needed | Recommended slice |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Subscriber loan/credit system | partial | Real loans API/service/table exists with grant, list, get, settle, actor/duration/value/reason, and apply result fields | VPS acceptance for actual RADIUS activation, approval workflow, richer limits | Existing `/api/v1/loans` | Web/Flutter finance screens exist; final UX acceptance remains | loan grant/settle/apply tests plus VPS acceptance | S3/S8 |
| Payment settlement reducing duration/value/debt | partial | Payments, settlements, ledger entries, void/reversal behavior, and dry-run/live apply fields exist | Period-close rules, final customer settlement semantics, VPS acceptance | Existing payments/ledger endpoints | Web/Flutter finance screens exist; snapshot UX remains | ledger invariants and VPS apply tests | S8 |
| Recycle bin / soft delete | partial | Soft-delete/archive columns and recycle-bin archive/restore APIs exist for core domains; lifecycle metadata exists | Finish old delete-path audit, recycle UX acceptance, no automatic purge for sensitive data | Existing `/api/v1/recycle-bin` archive/restore | Web/Flutter recycle screens exist | old-delete regression tests and restore acceptance | S2/S5 |
| Financial records never hard-deleted | partial | Ledger void/reversal APIs exist; payments void creates reversal behavior | Period-close and immutable snapshot/export UX | Existing `/api/v1/ledger/void`, `/api/v1/payments/<id>/void` | Finance UI exists; immutable report snapshot UX remains | no-hard-delete financial tests | S8 |
| Direct profile/offer/speed control | partial | AccessPlan has speed/quota/CIR fields; profiles API CUD exists; tools page can set speeds | Apply-now semantics, sync logs, no-disconnect capability detection | apply profile changes, sync status | clearer plan speed editor/logs | adapter sync tests | R2 |
| Hotspot/broadband offer type | partial | `service_type`, `hotspot_enabled`, `ppp_enabled` exist in plans/API | Enforcement semantics in policy/router sync | expose validation/status | UI explanation | policy tests | R2 |
| Pool selection | partial | `ip_pools`, `pool_id`, `address_pool`, `framed_pool` exist | Consistent enforcement across adapter modes | profile patch already partial | plan UI exists | policy/router tests | R2 |
| Daily/monthly quota | partial | plan and subscriber quota columns exist | Accounting enforcement/reporting completeness | profile/account patch exists | forms exist | quota policy tests | R2 |
| Loan eligibility per offer | missing | No loan eligibility field verified | Add offer-level loan settings | profile fields/endpoints | plan form field | loan eligibility tests | R2 |
| Time-based bandwidth schedule | partial | Schedule CRUD, effective resolver, Web/Flutter screens, and dry-run apply exist | Live apply acceptance on VPS/NAS and automatic worker/revert proof | Existing `/api/v1/bandwidth-schedules` and `/effective` | Web/Flutter schedule editors exist | overlap/timezone tests plus VPS live-apply tests | S7 |
| Pressure-based bandwidth policy | missing | No pressure metrics policy found | Load metrics, thresholds, auto apply/revert | policy endpoints | monitoring UI | policy safety tests | R4 |
| Daily/monthly/yearly sales | partial | Ledger-based report endpoints exist for sales periods and are visible in Web/Flutter | Immutable snapshot/export UX and customer acceptance of date-range filters | Existing `/api/v1/reports/sales/*` | Web/Flutter reports exist | aggregation and snapshot/export tests | S8 |
| Card sales | partial | Card/batch prices plus ledger-based card-sales endpoint exist | Final sales ownership acceptance and export UX | Existing `/api/v1/reports/card-sales` | Web/Flutter reports exist | card sales aggregation tests | S8 |
| Partial payments | partial | Payments API supports real payment rows, ledger posting, and proportional/dry-run apply behavior | VPS activation acceptance and period-close semantics | Existing `/api/v1/payments` | Web/Flutter payment forms exist | partial payment/apply tests | S8 |
| Discounts/custom prices | partial | plan/card prices exist | Discount/custom price record and audit | payment/activation endpoints | UI controls | discount tests | R2 |
| Distributor debt/profit | partial | Distributor APIs, assigned batch ownership, settlement entry, and distributor-debts report foundation exist | Deeper profit/debt acceptance and final accounting rules | Existing `/api/v1/distributors`, `/api/v1/reports/distributor-debts` | Web/Flutter distributors/reports exist | debt/profit tests | S9 |
| Managers/distributors usernames/passwords | partial | `admins`, `roles`, distributors, memberships, API/admin UI, and Flutter screens exist | Ongoing enforcement coverage for every new sensitive endpoint | scoped endpoints exist | distributor assignment UI exists | permission tests | S9 |
| Custom permissions | partial | `roles.permissions` and permission catalog exist | Enforcement gaps on API/write paths | no shape change | roles UI exists | permission enforcement tests | R2 |
| Scoped visibility | partial | tenant and distributor scope helpers/enforcement exist on core APIs | Final pass for every sensitive new endpoint | scoped filters exist | scoped UI exists | data isolation tests | S9 |
| Assigned card batches | partial | `card_batches.manager_id`, distributor assignment endpoint, and distributor batch list exist | Final sales ownership acceptance | existing assign/list endpoints | Web/Flutter assignment UI exists | batch ownership tests | S9 |
| Card batch status counts | partial | Batch lifecycle counters, original/available/used/expired/archived/pending fields, and CSV exports exist | Real Excel/PDF export and final UX acceptance | batch detail/list stats exist | status/counts shown in Web/Flutter | batch stats tests | S4/S11 |
| Reports from date to date | partial | ledger and operational report APIs exist with basic filters | Snapshot/export UX and richer pinned filters | report endpoints exist | Web/Flutter report pages exist | range/export tests | S8/S11 |
| Immutable financial reports | partial | ledger-derived reports and financial report snapshot storage exist | snapshot/export UX and period-close policy | report endpoints exist | UI still incomplete | immutability/export tests | S8 |
| Card checker | existing | checker service/API joins batch/plan/session/accounting and hides password | VPS CoA disconnect acceptance | existing checker/action endpoints | Web/Flutter card operations consoles exist | card state and no-password tests | S3/VPS |
| Card printing templates | partial | print template storage/API and preview exist | real PDF/export renderer | existing print-template endpoints | Web/Flutter template screens exist | template/render/export tests | S11 |
| Google Drive backup | missing | local backup API/UI is real; Drive connect intentionally returns 501 | real OAuth/storage integration | planned Drive endpoints disabled | Web/Flutter show disabled state | Drive integration tests once implemented | S10 |
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
