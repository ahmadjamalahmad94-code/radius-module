# Restore Poll and Safe Restore Workflow

P08 adds a safe restore polling foundation from the V40 admin bridge. It does
not perform destructive restore automatically.

## Remote Contracts

Poll:

`POST /api/integration/hoberadius/backup-restore/poll`

Status callback:

`POST /api/integration/hoberadius/backup-restore/<reference>/status`

## Local States

- `received`
- `local_snapshot_pending`
- `local_snapshot_created`
- `download_pending`
- `checksum_failed`
- `ready_for_manual_apply`
- `applying`
- `completed`
- `failed`

## Manual Routes

Poll once:

`POST /api/v1/system/admin-bridge/restore/poll`

Create a local pre-restore snapshot:

`POST /api/v1/system/admin-bridge/restore/<reference>/snapshot`

## Safety Gates

- Restore requests are recorded locally and idempotently.
- A local pre-restore snapshot is required before apply can proceed.
- Candidate backup checksum must match the expected checksum.
- Destructive apply is blocked unless
  `HOBERADIUS_ADMIN_RESTORE_APPLY_ENABLED=true`.
- P08 still does not implement destructive apply; enabling the flag returns a
  clear `restore_apply_not_implemented_in_p08` result.

## What Is Live

- Polling mocked/real admin endpoint if bridge config is enabled.
- Local request persistence.
- Local SQLite pre-restore snapshot creation.
- Checksum verification helper.
- Status callback helper.

## What Is Not Live

- Automatic DB overwrite.
- File restore.
- One-click restore.
- Admin-panel edits.
- Flutter UI.
