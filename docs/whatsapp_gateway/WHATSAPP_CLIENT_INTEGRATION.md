# WhatsApp Gateway — radius-module Client Integration

> This is the **client** side of the HobeRadius WhatsApp Gateway. The full
> architecture, data model, and Meta-side setup live in the License Panel repo:
> `radius-module-admin/docs/whatsapp_gateway/WHATSAPP_GATEWAY_ARCHITECTURE.md`.

`radius-module` is a **thin, signed client**. It does **not** talk to Meta and
does **not** store any Meta credentials. It only asks the License Panel (the
gateway) to enqueue WhatsApp messages on behalf of the network owner, over the
existing signed admin bridge.

---

## 1. How radius-module talks to radius-module-admin

All WhatsApp calls go through the existing **admin bridge** — the same signed,
per-license HMAC channel already used for identity sync, runtime contracts, and
Google-Drive status. There is **no new transport**.

`app/radius/services/admin_panel_client.py` → `AdminPanelClient` gains five
methods, each mirroring `fetch_google_drive_status()` exactly (HTTPS guard →
`_post_bridge_payload(path, payload=_license_check_payload({...}))` → parsed dict;
never raises, returns a safe dict on failure):

| Method | Panel endpoint (POST) |
|---|---|
| `get_whatsapp_status()` | `/api/integration/hoberadius/whatsapp/status` |
| `enqueue_whatsapp_message(payload)` | `/api/integration/hoberadius/whatsapp/messages/enqueue` |
| `send_whatsapp_test(phone, idempotency_key)` | `/api/integration/hoberadius/whatsapp/messages/test` |
| `sync_subscriber_preferences(subscribers)` | `/api/integration/hoberadius/whatsapp/subscriber-preferences/sync` |
| `get_message_status(idempotency_key)` | `/api/integration/hoberadius/whatsapp/messages/status` |

Every request carries the `license_key` + timestamp + nonce + HMAC `signature`
(via `_license_check_payload`) and the `X-HobeRadius-Admin-Secret` header. The
panel verifies the signature, resolves the customer from the license, applies its
own policy (service enabled, plan limits, opt-in, template approved, quiet hours),
and enqueues. The panel's drain worker performs the actual Meta send.

**Admin page:** `GET /admin/radius/whatsapp` (`app/radius/routes/whatsapp.py`)
renders the live status from `get_whatsapp_status()`, the local per-event toggles,
a test-message form, and a link to manage the connection in the panel portal. If
the bridge is unreachable it shows a clear pending card — it never errors.

---

## 2. No Meta token storage (hard rule)

The client **must never**:
- store a Meta access token, WABA id, app secret, verify token, or phone-number id
  in `tenant_settings`, any table, or any migration;
- call `graph.facebook.com` or any Meta endpoint directly;
- expose a Meta-token field in `/admin/radius/whatsapp`;
- log subscriber phone numbers or message bodies at INFO.

Credentials are entered **only** in the License Panel customer portal
(`/portal/whatsapp`), encrypted at rest there. A grep guard test in
`tests/test_whatsapp_bridge_client.py` enforces the no-Meta / no-token rule on the
client surface.

What the client *does* store (in `tenant_settings`): the local per-event send
toggles `whatsapp.send.{otp,expiry,quota,maintenance,password,portal}` (default
off) — these are radius-side gates only.

---

## 3. Supported events

Wired in `app/radius/services/whatsapp_notify.py` →
`notify_whatsapp(tenant_id, event_type, *, gate, recipient_phone, template_key,
idempotency_key, ...)`. Each call is **fire-and-forget**: it checks the local
`whatsapp.send.<gate>` toggle, then enqueues via the bridge inside a `try/except`
so a failure can never break the RADIUS flow.

| Event | Hook | gate | template_key | idempotency key |
|---|---|---|---|---|
| OTP / activation | `api/v1/accounts.py::accounts_create` | `otp` | `otp` | `otp:{tid}:{user}:{nonce}` |
| Password changed | `api/v1/accounts.py::accounts_reset_pw` | `password` | `password_changed` | `pwd:{tid}:{user}:{nonce}` |
| Near-expiry | `workers/dunning_worker.py::_run_for_tenant` (inside the once-per-day dedup) | `expiry` | `subscription_expiry_soon` | `exp:{tid}:{sid}:{date}` |
| Maintenance notice | `routes/tools.py` maintenance `notice` action | `maintenance` | `maintenance_notice` | `maint:{tid}:{run_id}:{sid}` |

Notes:
- **OTP**: account creation does not produce an OTP/activation code today, so no
  code is sent (the OTP body is omitted rather than faked). Wire variables here if
  a code is ever generated.
- **Password**: the new password is **never** included in the payload.
- **Quota**: intentionally not wired — the dunning worker has no quota-warning
  detection to hook. Add it when such detection exists.
- Failure isolation is covered by `tests/test_whatsapp_event_wiring.py` (a raising
  enqueue still yields a created account / successful reset / completing dunning tick).

---

## 4. Environment variables

The client needs **no WhatsApp/Meta env**. It reuses the existing admin-bridge
configuration:

- `license_admin_bridge.base_url` (DB setting) — the panel base URL the bridge
  posts to; also used to build the "manage in panel" portal link.
- The existing per-license integration secret / signing config already used by the
  bridge (no new secret).

All Meta/WhatsApp env (`WHATSAPP_FERNET_KEY`, `WHATSAPP_GRAPH_API_VERSION`, the
drain timer, etc.) lives on the **panel**, not here.

---

## 5. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `/admin/radius/whatsapp` shows «تعذّر جلب الحالة من لوحة التراخيص» | Bridge can't reach the panel, or HTTPS not satisfied. Check `license_admin_bridge.base_url` and that the panel is up. The page degrades gracefully — this is not fatal. |
| Status shows `disconnected` / `pending_setup` | The customer hasn't completed the panel portal wizard (`/portal/whatsapp`) — enter + validate Meta credentials there. |
| A toggle is on but no message arrives | The panel applies the authoritative policy. Check, in the panel: service enabled, plan limits not exceeded, an **approved** template for that `template_key`, subscriber opt-in, and quiet hours. Use the panel's message log. |
| `event_type_not_allowed` / `template_not_approved` from the bridge | Enable the event / map+approve the template in the panel (admin or portal). |
| Account creation / password reset / dunning seems unaffected by WhatsApp errors | Correct — by design. WhatsApp failures are logged (`whatsapp notify failed (non-fatal)`) and never propagate. Check the app log for these lines. |
| Duplicate messages | Should not happen — idempotency keys are stable and the panel dedups. If a subscriber has multiple triggers, verify the key inputs (date/nonce). |

Everything is mock-testable here without Meta: run
`python -m pytest -q tests/test_whatsapp_bridge_client.py tests/test_whatsapp_page.py tests/test_whatsapp_event_wiring.py`.
