# WhatsApp Integration — Audit Report (radius-module)

**Scope:** read-only audit of the WhatsApp service in `radius-module` (not `radius-module-app`). No code changed.
**Date:** 2026-06-03

---

## 1. Current status (summary)

There are **two separate WhatsApp paths** in this codebase, with different maturity:

**Path A — "Thin client / signed bridge" (the NEW, official WhatsApp messaging).**
This is the primary, intended design. `radius-module` does **not** talk to Meta and stores **no** Meta credentials. It only asks the License Panel (`radius-module-admin`) over the existing signed HMAC admin-bridge to enqueue/send WhatsApp messages. The panel owns the WhatsApp Cloud API credentials and performs the real send. On the radius side this is **fully implemented, wired, and tested** — but it is **non-functional until the panel side and the bridge config are in place** (HTTPS panel URL + license key + shared secret + the customer connecting WhatsApp in the panel portal).

**Path B — "Comms Hub / generic HTTP gateway" (older notification system).**
A self-contained engine that fans business events out to channels (`sms`, `whatsapp`, `telegram`) by calling a **tenant-supplied HTTP gateway URL** (e.g. `https://gw.example.com/send?to={phone}&text={msg}`). This is **fully implemented and self-functional** (real sends, config UI, quota, delivery log) and does **not** depend on the panel. It is *not* the official WhatsApp Cloud API — it works only if the operator plugs in their own WhatsApp HTTP gateway.

So: the integration is **wired up in code and well-engineered**, but **not yet sending in production** through the official path because that depends on the panel/bridge being configured and the panel-side endpoints existing.

---

## 2. What works (verified in code)

**Path A — bridge / thin client**
- Admin page `GET /admin/radius/whatsapp` (`app/radius/routes/whatsapp.py`), registered in `blueprint.py`. Renders live connection/usage status from the panel, per-event ON/OFF toggles, a test form, a "cloud test", and a "manage in panel" link. Degrades gracefully (never 500s) when the bridge is down.
- Bridge client `AdminPanelClient` (`app/radius/services/admin_panel_client.py`) implements 6 signed WhatsApp methods: `get_whatsapp_status`, `enqueue_whatsapp_message`, `send_whatsapp_test`, `send_whatsapp_cloud_test`, `sync_subscriber_preferences`, `get_message_status`. All HMAC-signed, HTTPS-guarded, never raise.
- Event helper `notify_whatsapp()` (`app/radius/services/whatsapp_notify.py`) — fire-and-forget, double-gated (local `whatsapp.send.<event>` toggle + bridge), fail-safe (swallows all errors, never breaks the RADIUS flow).
- Event hooks actually wired: account creation + password change (`app/api/v1/accounts.py`), near-expiry (`app/workers/dunning_worker.py`), maintenance notice (`app/radius/routes/tools.py`).
- Security hard rules enforced: no Meta token/WABA/secret stored anywhere; no `graph.facebook.com` calls; phone numbers / message bodies never logged at INFO. A grep-guard test enforces this.
- Tests present and passing-by-design: `test_whatsapp_bridge_client.py`, `test_whatsapp_page.py`, `test_whatsapp_event_wiring.py`, `test_whatsapp_status_passthrough_p7.py`.
- Documentation: `docs/whatsapp_gateway/WHATSAPP_CLIENT_INTEGRATION.md` (clear and accurate).

**Path B — comms hub / generic gateway**
- Provider `GenericHttpProvider` + `http_send` (`app/radius/services/comms_providers.py`): real `urllib` GET/POST send, 8s timeout, 2xx = success, best-effort provider-message-id extraction, never raises.
- Event engine `notify_event()` (`app/radius/services/notifications_engine.py`): 17 predefined Arabic-templated events, per-event channel selection, `{var}` substitution. Wired into dunning, the webhook dispatcher (`app/webhooks/dispatcher.py`), and the network device monitor.
- Config UI: `GET/POST /communications/channels` + a live test endpoint (`app/radius/routes/communications.py`) — the operator can enter the gateway URL/method per channel and send a test.
- Delivery logging: `message_deliveries` rows via `NotificationCampaignService`.
- Quota / billing accounting: `comms_quota.py` (`admin_quota` mode checks balance before send, consumes 1 unit only on confirmed success; `self_api` is unlimited) with a ledger.

---

## 3. What's missing / incomplete (prioritized)

**P0 — blocks the official path from working at all**
1. **Panel-side dependency is external and unverified here.** Path A only works if `radius-module-admin` implements the 6 `/api/integration/hoberadius/whatsapp/*` endpoints and the customer completes the Meta connection in `/portal/whatsapp`. None of that lives in `radius-module`; it cannot be confirmed from this repo. This is the single biggest "is it production-ready" gate.
2. **Bridge must be configured.** Requires `license_admin_bridge.{base_url(HTTPS),license_key,shared_secret,enabled}`. Until set, every WhatsApp call returns `https_required` / `config_missing` / `disabled` and nothing sends. There is no in-product check that surfaces "WhatsApp can't work because the bridge isn't configured" on the WhatsApp page itself beyond the generic pending card.

**P1 — functional gaps in the official path**
3. **No delivery-status feedback loop.** `get_message_status()` and `sync_subscriber_preferences()` are implemented and tested but **called nowhere** in app code — there is no UI, no poller, and no inbound webhook/callback receiver for Meta delivery/read receipts. Operators can enqueue but cannot see per-message delivery state on the radius side.
4. **OTP event sends no code.** Per the doc, account creation produces no OTP/activation code, so the `otp` event fires with no code in the body. If WhatsApp OTP-on-login is a goal, the OTP generation + variable wiring does not exist yet.
5. **`quota` event toggle is exposed in the UI but not wired** to any trigger (no quota-warning detection feeds it). The toggle does nothing today.
6. **Per-subscriber opt-in is referenced but not enforced locally.** The UI and docs say messages go only to opted-in subscribers and `sync_subscriber_preferences` exists, but there is no local opt-in field/flow shown to be capturing/storing it; enforcement is assumed to be on the panel.

**P2 — comms-hub (Path B) gaps**
7. **No retry/queue drain.** Sends are synchronous and fire-and-forget. A failed `http_send` marks the delivery `failed` with no automatic retry or backoff; "queued" rows are not drained by any worker on this path.
8. **No rate limiting / throttling.** `comms_quota` caps total volume in `admin_quota` mode, but there is no per-second/per-minute rate limit or anti-flood on outbound HTTP sends.
9. **No delivery callback for the generic gateway.** Success is inferred purely from the HTTP 2xx of the send call; there is no webhook to receive the gateway's later delivery status.
10. **Channel naming mismatch vs. the spec phrase.** The requested alert channels are "رسالة نصية / واتساب / بريد إلكتروني" (SMS / WhatsApp / **Email**), but the engine's channels are `sms`, `whatsapp`, `telegram` — **there is no Email channel**. Telegram exists instead of Email. Worth confirming which is intended.

**P3 — polish**
11. Two parallel WhatsApp systems (bridge vs. generic gateway) with overlapping purpose may confuse operators (two different "WhatsApp" surfaces). Consider clarifying in UI which one is authoritative.
12. No end-to-end integration test against a live panel/gateway (only mocked unit tests) — expected for an audit, but note it before go-live.

No `TODO`/`FIXME`/stub markers or hardcoded secrets were found in the WhatsApp files; the code is mature, defensive, and consistently fail-safe.

---

## 4. Recommended plan to finish (short)

1. **Verify/finish the panel side** (`radius-module-admin`): confirm the 6 WhatsApp bridge endpoints and the `/portal/whatsapp` Meta connection wizard exist and work. This is the gating item — do it first.
2. **Configure & validate the bridge** in a staging tenant (HTTPS base URL, license key, shared secret, enable), then use the WhatsApp page's "test" and "cloud test" buttons to confirm an end-to-end real send.
3. **Close the delivery-status loop:** wire `get_message_status()` into the deliveries view (or a small poller), and decide whether to accept Meta delivery/read webhooks (panel-side) and surface them.
4. **Decide OTP scope:** if WhatsApp login OTP is required, implement OTP generation and pass the code as a template variable; otherwise drop the `otp` toggle to avoid empty sends.
5. **Wire or hide the `quota` event** so no UI toggle is a no-op.
6. **Confirm the channel set:** if Email is genuinely required ("بريد إلكتروني"), add an email channel/provider; otherwise correct the spec wording to SMS/WhatsApp/Telegram.
7. **For Path B (if kept for self-hosted gateways):** add retry/backoff for failed sends and a basic outbound rate limit.

---

## 5. Key files / functions

- `app/radius/routes/whatsapp.py` — admin page + test routes (`whatsapp_page`, `whatsapp_settings`, `whatsapp_test`, `whatsapp_cloud_test`); `WHATSAPP_EVENTS`.
- `app/radius/services/whatsapp_notify.py` — `notify_whatsapp()` (the gated, fail-safe event sender).
- `app/radius/services/admin_panel_client.py` — `AdminPanelClient` WhatsApp bridge methods + HMAC signing (`_license_check_payload`, `sign_admin_bridge_payload`, `_post_bridge_payload`).
- `app/radius/services/comms_providers.py` — `GenericHttpProvider`, `http_send`, `load/save_channel_config`, `provider_for_channel`.
- `app/radius/services/notifications_engine.py` — `notify_event()`, event registry, `_send_http_channel` / `_send_telegram`.
- `app/radius/services/notification_campaigns.py` — `NotificationCampaignService.queue_notification`, `_provider_for`, delivery rows.
- `app/radius/services/comms_quota.py` — quota balance / consume / credit / ledger.
- `app/radius/routes/communications.py` — channel config UI + test, deliveries, quota, notifications settings.
- Hooks: `app/api/v1/accounts.py`, `app/workers/dunning_worker.py`, `app/radius/routes/tools.py`, `app/webhooks/dispatcher.py`, `app/radius/services/network_device_monitor.py`.
- Docs: `docs/whatsapp_gateway/WHATSAPP_CLIENT_INTEGRATION.md`.
- Tests: `tests/test_whatsapp_bridge_client.py`, `test_whatsapp_page.py`, `test_whatsapp_event_wiring.py`, `test_whatsapp_status_passthrough_p7.py`, `test_notifications_engine.py`, `test_comms_quota.py`, `test_comms_e2e.py`.
