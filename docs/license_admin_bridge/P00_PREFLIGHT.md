# P00 Preflight, Safety Lock, and Workspace Map

## Verdict

GO for Prompt 01 only.

The `radius-module` working tree is clean after the unrelated pre-existing
source/test/template work was isolated in:

```text
stash@{0}: On main: pre-p00-existing-radius-module-dirty-tree-before-v40-bridge
```

Do not apply or inspect that stash during the V40 bridge sequence unless the
operator explicitly asks for it later.

## Repository

```text
repo: C:\Users\Ahmad J Ahmad\Desktop\hub\radius-module
branch: main
```

P00 was run inside `radius-module` only. `radius-module-admin` was not modified.

## Dirty Tree Status

Initial P00 `git status --short` output:

```text
<clean>
```

There are no currently dirty tracked files and no currently untracked files in
the working tree.

## Dirty File Classification

Because the tree is clean, there are no active dirty files to classify.

Pre-P00 dirty source/test/template work was already parked in the named stash
above. It is intentionally excluded from this bridge sequence.

| Category | Current files | Verdict |
| --- | ---: | --- |
| safe docs | 0 | clean |
| safe tests | 0 | clean |
| risky runtime behavior | 0 | clean |
| unknown | 0 | clean |

## Required Risk Checks

P00 searched the current clean tree for the bridge-sensitive areas listed in the
prompt. Findings below describe pre-existing repository surfaces only; P00 did
not change them.

| Area | Current finding | P00 risk |
| --- | --- | --- |
| FreeRADIUS | Existing deployment/config/docs under `deploy/freeradius` and internal auth/accounting paths | unchanged |
| CoA/disconnect | Existing sessions/card/MikroTik disconnect and `radius_coa` references | unchanged |
| MikroTik live operations | Existing MikroTik control/backup/session operation routes and docs | unchanged |
| `sqlite_adapter.py` | Existing integration adapter path referenced by factory/docs | unchanged |
| payments/loans applying to RADIUS | Existing accounting service supports `apply_to_radius` request metadata and dry-run/no-apply paths | unchanged |

No dirty file currently touches these areas because the tree is clean.

## Existing License/Admin Bridge Surfaces

P00 searched `radius-module` for V40 bridge-related terms:

- `license/check`
- `integration/hoberadius`
- `capacity-contract`
- `service-activations`
- `backup-restore`
- `backups/upload`
- `HOBERADIUS_ADMIN_*`
- `INSTANCE_LICENSE_KEY`

Result:

- No implemented `AdminPanelClient` was found.
- No local license snapshot persistence was found.
- No local capacity contract persistence was found.
- No V40 usage, heartbeat, backup upload, restore poll, or service activation
  bridge implementation was found in `radius-module`.
- Existing mentions of license/capacity are roadmap/docs or unrelated static
  font license text.

This is expected before Prompt 01 and Prompt 02.

## Required Environment Variables To Define Later

P00 did not add runtime config. The following env vars are expected to be
designed and implemented in later prompts:

```text
HOBERADIUS_ADMIN_BRIDGE_ENABLED
HOBERADIUS_ADMIN_BASE_URL
HOBERADIUS_LICENSE_KEY
INSTANCE_LICENSE_KEY
HOBERADIUS_ADMIN_SHARED_SECRET
HOBERADIUS_ADMIN_TIMEOUT_SECONDS
HOBERADIUS_ADMIN_RETRY_COUNT
```

Exact names and compatibility aliases should be locked in Prompt 02.

## radius-module-admin Status

`radius-module-admin` is read-only reference for this sequence. P00 did not open,
edit, stage, or commit anything in that project.

Admin-side endpoint availability is not audited in P00. Prompt 01 must inspect
`radius-module-admin` read-only and record missing or ambiguous V40 endpoints in:

```text
docs/license_admin_bridge/CODEX_FOLLOWUPS.md
```

## Safety Lock

Safe to continue to Prompt 01 if the operator agrees with these conditions:

1. Start P01 from the clean tree.
2. Keep the pre-P00 stash parked and untouched.
3. Do not edit `radius-module-admin`.
4. Do not touch Flutter unless a later prompt explicitly allows it.
5. Keep each prompt scoped and committed separately.

## Recommended Next Step

GO for Prompt 01: V40 Contract Audit, read-only.

Prompt 01 should create the endpoint contract audit documentation and, if
needed, `CODEX_FOLLOWUPS.md` for admin-side gaps. It should not add runtime code.
