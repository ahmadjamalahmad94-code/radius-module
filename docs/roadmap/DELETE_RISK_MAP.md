# Delete Risk Map

R1B scope: inventory hard-delete paths only. No soft-delete/archive behavior is
implemented in this slice.

## Summary

Sensitive operational and financial-ish records currently have several hard
delete paths. Some deletes are safe infrastructure cleanup, but customer
requirements explicitly need recycle-bin, archive, void, reversal, or immutable
report behavior before accounting/distributor work grows on top of this code.

## Subscribers

| File/function | What it deletes | Risk | Future behavior | Suggested slice | Tests needed |
| --- | --- | --- | --- | --- | --- |
| `app/api/v1/accounts.py::accounts_delete` -> `UsersService.delete` | API subscriber account by username | High | Soft-delete/archive subscriber, preserve payments/accounting links | R2A subscriber archive | API delete hides from list, restore path, auth still rejects archived user |
| `app/radius/routes/users.py::user_delete` -> `UsersService.delete` | Web admin subscriber account | High | Same archive path as API | R2A subscriber archive | Web delete uses archive, no hard delete |
| `app/radius/services/users.py::delete` -> adapter | Service delete orchestration | High | Centralize archive + RADIUS cleanup as separate operation | R2A subscriber archive | Audit + RADIUS cleanup called once |
| `app/radius/db/repos/subscribers_repo.py::delete_subscriber` | Row from `subscribers` | Critical | Replace with `deleted_at`, `deleted_by`, `delete_reason`, status archived | R2A subscriber archive | FK data remains, deleted subscriber not listed by default |
| `app/radius/integration/*_adapter.py::delete_account` | Adapter-level subscriber deletion and remote cleanup | High | Local archive first, then remote disable/delete only where intended | R2B RADIUS cleanup split | Remote cleanup does not erase financial/audit history |

## Profiles / Offers / Speeds

| File/function | What it deletes | Risk | Future behavior | Suggested slice | Tests needed |
| --- | --- | --- | --- | --- | --- |
| `app/api/v1/profiles.py::profiles_delete` -> `PlansService.delete` | API access plan/profile | High | Disable/archive offer; block delete if referenced by subscribers/cards | R2C profile archive | Referenced plan cannot disappear from historical cards |
| `app/radius/routes/plans.py::plan_delete` -> `PlansService.delete` | Web admin access plan/profile | High | Same archive path as API | R2C profile archive | Web delete becomes archive/disable |
| `app/radius/services/plans.py::delete` -> adapter | Service delete orchestration | High | Separate business archive from RADIUS profile cleanup | R2C profile archive | Audit event differentiates archive vs remote cleanup |
| `app/radius/db/repos/plans_repo.py::delete_plan` | Row from `access_plans` | Critical | Add `archived_at`/`enabled=0` style behavior, keep FK history | R2C profile archive | Cards/subscribers keep plan reference or snapshot |
| `app/radius/db/repos/bandwidth_repo.py::delete` | Row from `bandwidth_profiles` | Medium | Archive if referenced by plans | R3A speed profile archive | Deleting referenced speed profile is blocked/archived |

## NAS / Servers / Integration Config

| File/function | What it deletes | Risk | Future behavior | Suggested slice | Tests needed |
| --- | --- | --- | --- | --- | --- |
| `app/api/v1/nas.py::nas_delete` -> `NasDevicesService.delete` | API NAS device | Medium | Soft-disable/archive NAS, preserve accounting references | R2D NAS archive | NAS removed from active lists but reports keep NAS address |
| `app/radius/routes/devices.py::device_delete` -> service | Web admin NAS device | Medium | Same archive path as API | R2D NAS archive | Web delete becomes archive |
| `app/radius/services/devices.py::delete` -> adapter | Service delete orchestration | Medium | Archive locally, cleanup FreeRADIUS client only when explicit | R2D NAS archive | Audit remote cleanup separately |
| `app/radius/db/repos/nas_repo.py::delete_nas` | Row from `nas_devices` | High | `enabled=0` + archive metadata | R2D NAS archive | Health/test ignores archived NAS |
| `app/api/v1/mikrotik.py::mt_delete` / `app/radius/routes/integrations.py` | MikroTik config row | Medium | Archive config; redact secrets in archive output | R3B integration archive | Archived config cannot be used for actions |
| `app/radius/db/repos/mikrotik_repo.py::delete` | Row from `mikrotik_configs` | Medium | Archive + disabled flag | R3B integration archive | No secret leak in archive views |
| `app/radius/db/repos/pools_repo.py::delete` | IP pool row | Medium | Disable/archive pool if referenced | R3C pools archive | Referenced pool remains visible in old plan history |

## Admins / Roles / Permissions

| File/function | What it deletes | Risk | Future behavior | Suggested slice | Tests needed |
| --- | --- | --- | --- | --- | --- |
| `app/api/v1/admins.py::admins_delete` -> `AdminsService.delete_admin` | API admin account | High | Disable/archive admin, keep audit actor identity | R2E admin archive | Existing audit logs still resolve actor label |
| `app/radius/services/admins.py::delete_admin` | Service admin deletion | High | Refuse self/super admin edge cases, archive normal admins | R2E admin archive | Cannot delete last super admin; archive emits audit |
| `app/radius/db/repos/admins_repo.py::delete_admin` | Row from `admins` | Critical | `enabled=0` + archived metadata | R2E admin archive | Audit actor id remains meaningful |
| `app/api/v1/admins.py::roles_delete` | API role delete | Medium | Archive/deactivate custom roles; system roles stay protected | R2F role archive | Assigned role cannot be hard-deleted |
| `app/radius/db/repos/admins_repo.py::delete_role` | Row from `roles` where non-system | Medium | Archive role and prevent assignment | R2F role archive | Existing admins keep role history or migration path |

## Cards / Batches / Vouchers

| File/function | What it deletes | Risk | Future behavior | Suggested slice | Tests needed |
| --- | --- | --- | --- | --- | --- |
| `app/radius/db/migrations/003_cards.sql` FK `cards.batch_id ON DELETE CASCADE` | Cards are deleted if batch row is hard-deleted | Critical | Never hard-delete batches; archive/cancel batch and revoke cards | R2G card batch archive | Batch cancellation preserves card checker history |
| `app/radius/services/cards.py::revoke_card` / `cards_repo.revoke_card` | Does not hard-delete; sets `revoked=1` | Good | Keep revoke/cancel semantics, add reason/actor later | R2G card batch archive | Checker shows revoked without password |
| `app/radius/routes/saas_modules.py::vch_revoke` / `vouchers_repo.revoke` | Voucher revoke only updates status | Good | Keep append-style status changes | R3D voucher/card unification | Voucher checker parity |
| `app/radius/db/repos/share_groups_repo.py::delete` | Share group row | Medium | Archive group, preserve member history | R3E share groups archive | Historical group membership report remains |
| `app/radius/db/repos/share_groups_repo.py::remove_member` | Share group membership row | Low/Medium | Record membership removal event | R3E share groups archive | Removal audit event |

## Sessions / Accounting / Audit / Logs

| File/function | What it deletes | Risk | Future behavior | Suggested slice | Tests needed |
| --- | --- | --- | --- | --- | --- |
| `app/radius/routes/tools.py::tool_maintenance` action `purge_radacct` | Stopped `radacct` rows older than cutoff | Critical for reports | Replace with archive/export policy; never purge financial/reporting data by default | R2H immutable reports | Purge disabled unless explicit backup/export path exists |
| `app/radius/routes/tools.py::tool_maintenance` action `purge_audit` | `audit_log` rows older than cutoff | High | Retention policy + export, no silent delete | R2H immutable reports | Audit retention requires admin confirmation |
| `app/radius/routes/tools.py::tool_maintenance` action `purge_sync_queue` | Completed sync queue rows | Low | Keep bounded cleanup, not customer-facing financial data | R3F maintenance policies | Cleanup only old completed items |
| `app/radius/routes/tools.py::tool_maintenance` action `purge_failed_webhooks` | Failed webhook delivery rows | Medium | Archive recent failures before cleanup | R3F maintenance policies | Recent failures preserved |
| `app/radius/db/repos/freeradius_repo.py::*delete*` | FreeRADIUS operational rows (`radcheck`, `radreply`, groups, NAS) | Medium | Keep as operational mirror cleanup, never primary business history | R2B RADIUS cleanup split | Business archive remains after FreeRADIUS cleanup |

## Other Operational Tables

| File/function | What it deletes | Risk | Future behavior | Suggested slice | Tests needed |
| --- | --- | --- | --- | --- | --- |
| `app/radius/db/repos/services_repo.py::delete` | Hardware/service item | Medium | Archive service item | R3G service archive | Archived item hidden from active lists |
| `app/radius/db/repos/webhooks_repo.py::delete_sub` | Webhook subscription | Low | Hard delete may be acceptable after audit event | R3F maintenance policies | Delete emits audit |

## Immediate Guardrails

- Do not add new financial hard-delete paths.
- Card batches should be cancelled/revoked, not deleted.
- Subscriber deletes should be redesigned before loan/accounting features.
- Reports must be append-only or retention-controlled with export/backup proof.
- The Card Checker should remain able to answer for revoked/expired cards.
