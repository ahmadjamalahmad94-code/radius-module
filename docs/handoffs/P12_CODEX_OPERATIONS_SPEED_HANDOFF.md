# P12 Codex Handoff - Operations Center and Speed Control Center

## Commit

Recorded in Git with prompt commit `feat: add operations speed control center`.

## Scope

Prompt 12 added a read-only Operations Center and dry-run Speed Control Center inside `radius-module`.

## Implemented

- Added `speed_control_policies` migration for pending/dry-run speed policies.
- Added `OperationsSpeedCenterService` for:
  - online user and active `radacct` summaries
  - NAS/RADIUS/VPN/API health signals
  - blocked emergency action placeholders
  - speed multiplier previews
  - safe preset previews
  - dry-run policy persistence with `applied_to_radius=false`
  - business event audit record creation
- Added `/admin/radius/operations` and `/admin/radius/operations/speed-control`.
- Added Operations and Speed Control templates using the existing Hub UI partials.
- Added focused regression tests for preview math, preset defaults, CoA impact indication, audit events, and route rendering.

## Safety Notes

- No live CoA push was added.
- No MikroTik write path was added.
- No RADIUS/auth/accounting behavior was changed.
- Emergency actions are placeholders only.
- Saved speed policies are dry-run records with `applied_to_radius=false`.

## Tests

Run after implementation:

```powershell
python -m compileall app
python -m pytest tests/test_operations_speed_center.py -q
python -m pytest tests/test_operations_speed_center.py tests/test_events_risk_center.py tests/test_business_os_core_foundations.py -q
git diff --check
git status --short
```

## Remaining Risks

- Real speed enforcement is not implemented; future work must integrate only through a guarded, audited CoA/RADIUS policy service.
- Health signals remain DB-derived and do not perform live network probes.
- Emergency actions require a separate safety-reviewed execution engine before they can do anything beyond display.
