# P10 Codex Handoff — Notifications and Campaigns

## Commit

This commit. The exact final hash is recorded in the execution report.

## Scope

Prompt 10 adds the notification and campaign engine foundation inside `radius-module` only. It does not touch `radius-module-admin`, Flutter, live provider integrations, live MikroTik execution, or RADIUS auth/accounting behavior.

## Implemented

- Added additive migration `060_notification_campaign_engine.sql`.
- Added notification/campaign service foundations:
  - notification templates,
  - notification preferences,
  - audience segments,
  - campaigns,
  - delivery records,
  - provider config placeholders,
  - queued-only provider abstraction.
- Added `NotificationCampaignService` with:
  - template rendering,
  - audience preview,
  - manual queued messaging,
  - campaign dry-run,
  - dry-run-only action coupling,
  - delivery log summaries.
- Added communications routes under `/admin/radius/communications`.
- Added templates for dashboard, templates, send-message, campaigns, deliveries, and audience builder.
- Registered the route module in the existing radius blueprint.
- Added focused tests in `tests/test_notification_campaigns.py`.

## Safety

- Default delivery provider is queued-only and performs no external send.
- No provider secrets are hardcoded or stored as plaintext fields.
- Action-coupled campaigns are dry-run only in this foundation.
- Existing RADIUS auth/accounting behavior was not modified.

## Verification

- `python -m compileall app`
- `python -m pytest tests/test_notification_campaigns.py -q`
- Additional combined regression commands are recorded in the execution report.

## Notes

- The table for campaign notifications is named `message_notifications` to avoid colliding with the existing admin `notifications` table from migration `007_meta.sql`.
- External SMS/WhatsApp/Telegram/email adapters are intentionally future work behind explicit provider configuration.
