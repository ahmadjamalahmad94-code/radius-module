# Operations Event Feedback

P11 adds a local operations event log for the V40 bridge.

## Why Local First

The prompt does not provide a confirmed V40 admin event callback endpoint. To
avoid inventing admin behavior, P11 keeps the event stream local and records a
Codex follow-up for admin contract confirmation.

## Local Event Table

Events are stored in `license_admin_bridge_events` with:

- `tenant_id`
- `event_type`
- `severity`
- `status`
- `source`
- `reference`
- `event_key`
- `label_ar`
- sanitized `payload_json`
- `created_at`

`event_key` is optional. When provided, it is unique per tenant and makes event
recording idempotent.

## Event Labels

Local Arabic labels are provided for expected bridge events:

- license snapshot refreshed: `تم تحديث حالة الترخيص`
- capacity contract refreshed: `تم تحديث عقد السعة`
- usage report sent: `تم إرسال تقرير الاستخدام`
- health heartbeat sent: `تم إرسال نبض الحالة`
- backup upload success/failure
- restore request received/status changed
- service activation received/executed/failed
- accounting degraded

## Read API

`GET /api/v1/system/admin-bridge/events`

Returns:

- recent events;
- aggregate summary by type and severity;
- local admin-callback availability status.

The route is read-only and does not call V40.

## Safety Guarantees

- Event logging failure must not block business flows.
- Payloads are sanitized with the bridge masking helper.
- No RADIUS, MikroTik, FreeRADIUS, or CoA behavior is changed.
- No radius-module-admin files are edited.
- No remote event callback is sent until V40 confirms an endpoint contract.
