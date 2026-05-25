# Backup Upload Agent Foundation

P07 adds a backend-only backup upload foundation for the V40 admin bridge.
Existing local and Google Drive backup flows are not removed or changed.

## Flow

1. A local backup is created by the existing backup system.
2. The bridge finds the latest successful local backup run.
3. It records a local `BackupArtifact` with:
   - `backup_reference`
   - path
   - kind
   - size
   - SHA-256 checksum
   - local/upload status
4. The manual bridge route can upload metadata to V40.
5. Content upload is disabled by default.

## Manual Route

`POST /api/v1/system/admin-bridge/backups/upload-latest`

Defaults:

```json
{
  "dry_run": true,
  "include_content": false
}
```

Remote endpoint assumed for V40:

`POST /api/integration/hoberadius/backups/upload`

## Content Upload

Content upload requires both:

- request JSON: `"include_content": true`
- env: `HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_ENABLED=true`

The maximum content size is controlled by:

`HOBERADIUS_ADMIN_BACKUP_CONTENT_MAX_BYTES`

Default cap: 5 MiB.

When content is disabled or too large, the payload remains metadata-only and
records `content_omitted_reason`.

## Safety

- No restore execution in P07.
- No backup deletion.
- No admin-panel edits.
- No live RADIUS, MikroTik, FreeRADIUS, or CoA behavior changes.
- Upload attempts are sanitized before storage.
- Tests use mocked admin transport only.

## Retention Notes

The bridge records upload state but does not enforce retention or delete local
artifacts. Retention policy must remain controlled by existing backup
operations or a later dedicated retention prompt.
