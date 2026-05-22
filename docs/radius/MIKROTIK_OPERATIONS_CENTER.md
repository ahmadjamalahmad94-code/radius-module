# MikroTik Operations Center — Operator Guide

This document describes what the operator can do from the
HobeRadius admin UI without ever opening WinBox. It covers the
features delivered in Phases O / P / Q / R (replaces the
pre-phase-O ad-hoc layout).

If you're looking for the *plan* (commit log + design rationale)
read [PHASE_OPQR_PLAN.md](PHASE_OPQR_PLAN.md). This file is for
the person sitting in front of the screen.

---

## 1. Fleet overview — `/admin/radius/mt`

Landing page for everything MikroTik. Shows one row per router
in `nas_devices` for the current tenant.

What ships in each row (Phase O):

| Column | Source | Refresh |
|--------|--------|---------|
| Sequential # | row position | server-rendered |
| الاسم + العنوان | `nas_devices.name` + `.address` | server-rendered |
| الاتصال | `connection_mode` (direct / vpn) badge | server-rendered |
| الحالة | from `/system/overview` poll | 30 s |
| Hotspot active | `/api/v1/.../counters` | 10 s |
| PPP active | same | 10 s |
| RX / TX إجمالي | same | 10 s |
| Toggle (enable/disable) | inline form, POST + CSRF | live |
| Checkbox (bulk-select) | HTML5 `form="mt-bulk-form"` | live |

Above the table:

- **Fleet summary cards** — total / connected / unreachable /
  partial / disabled. Counts aggregate the row states; no second
  N+1 endpoint.
- **Bulk action bar** — visible only when ≥1 checkbox is ticked.
  Buttons: تفعيل المحدّد / تعطيل المحدّد. POST → CSRF →
  per-row apply → audit log.

Routers with `enabled=0` show in a muted style, are excluded
from the counters poll, and don't count toward "unreachable."

---

## 2. Per-router dashboard — `/admin/radius/mt/<id>/dashboard`

Phase P put the K9 dashboard inside a tabbed shell. Tab state is
hash-routed (`#tab-<slug>`) so refresh + deep-link preserve the
active tab.

### 2.1 Tabs

| Tab | Slug | Source endpoint | Refresh |
|-----|------|-----------------|---------|
| نظرة عامة | `overview` | `/system/overview` | 10 s (always) |
| الواجهات | `interfaces` | `/interfaces` | 15 s (when active) |
| العناوين | `ips` | `/ip/addresses` | 30 s (when active) |
| المسارات | `routes` | `/routes` | 30 s (when active) |
| الجيران | `neighbors` | `/neighbors` | 30 s (when active) |
| الجلسات | `sessions` | `/hotspot/active` + `/ppp/active` | 10 s (when active) |
| السجلات | `logs` | `/log?topics=...&limit=250` | 5 s (when active) |
| التشخيص | `diagnostics` | `/health` | 30 s (when active) |

"When active" means the polling timer is only set after the
operator clicks the tab; switching away clears it. The overview
poll always runs.

### 2.2 Logs tab (P5)

Topic-chip strip filters server-side via the existing
`?topics=foo,bar` query string. Chips: الكل / أخطاء / تحذيرات /
حرجة / معلومات / نظام / جدار حماية / محاسبة / هوتسبوت / DHCP.
Multi-select = OR. Severity-tinted lines (warn=amber,
error=red, critical=red-on-red). A "إيقاف مؤقّت" checkbox
suspends polling without losing the chip selection.

### 2.3 Diagnostics tab (P7)

`/api/v1/.../health` runs four checks over cached K4 reader
output — no extra wire calls:

| Check | Severity on hit | Description |
|-------|-----------------|-------------|
| `duplicate_macs` | critical | Two physical interfaces share a MAC. Bridges/VLAN children excluded. |
| `loop_protect` | critical | Any port RouterOS auto-disabled with `loop-protect-status=disable-on-loop`. |
| `subnet_overlap` | warning | `/ip/address` rows with overlapping networks on *different* interfaces. |
| `flapping` | warning | Non-tunnel interface with `link-downs > 10`. |

Each signal renders as a severity-tinted row with an expandable
JSON evidence block.

### 2.4 Sessions tab (P6) — read-only

Hotspot + PPP active sessions in two sub-cards. Disconnect
endpoints (K5.2) exist but the UI doesn't surface them yet —
the panel explicitly says "للقراءة فقط" so a missing
disconnect button isn't read as a bug.

---

## 3. Network programming — `/admin/radius/mt/<id>/program`

Phase Q replaces "operator opens WinBox to configure hotspot."

### 3.1 Two kinds

Tabs at the top of the form switch between:

- **Hotspot** — Q1+Q2. Sets up pool + DHCP server + hotspot
  profile + hotspot server + walled-garden DNS entries on the
  chosen interface.
- **PPPoE** — Q3. Sets up pool + PPP profile + PPPoE server
  listener.

### 3.2 Plan generator (Q1, Q3)

Fill the form → submit → server validates + generates a planned
RouterOS script + a summary + warnings/risks against current
router state. **Nothing is sent to the router on this step.**

Risks (critical) include:
- The named interface doesn't exist on the router.
- The chosen CIDR overlaps with an existing IP/network.

Warnings (yellow) include:
- The interface already has an IP address on it.
- The interface is currently disabled.

### 3.3 Apply (Q2, Q3)

Below the generated script: a confirm checkbox + apply button.
The confirm checkbox is **disabled when risks are present** —
the operator must rework the spec before the apply path opens.

On submit:

1. Server re-validates the spec (never trusts the client).
2. Server re-runs the plan against current router state.
3. If no risks + confirm=1 → opens a wire session, runs each
   command in order, stops on first hard failure.
4. "Already exists" RouterOS rejects are counted as
   **skipped** so a second apply is idempotent.
5. Audit log: `mt.programming.<kind>.apply` with ok flag +
   per-step counts.

### 3.4 Unprogram (Q4)

Every command emitted by Q1/Q3 carries the literal comment
`hoberadius:hs` (hotspot) or `hoberadius:pppoe` (pppoe). Q4
walks the router for objects carrying that comment and
`/remove .id=...` each one.

**Dependency order matters** — RouterOS refuses to delete a
pool that's still referenced by a DHCP server. The walker goes
**leaf → root**:

  - Hotspot: walled-garden → hotspot → user-profile → profile →
    dhcp-server → network → `/ip/address` → `/ip/pool`.
  - PPPoE: pppoe-server → profile → pool.

Resource paths missing on a given RouterOS build (e.g.
`/ip/hotspot` on a CHR without the hotspot package) are
non-fatal — skipped and noted in the result.

Same confirm-checkbox + audit pattern as apply.

---

## 4. Hotspot login designer — `/admin/radius/mt/<id>/login-designer`

Phase R lets the operator brand the hotspot login page without
editing HTML.

### 4.1 Template library (R1)

Four curated templates:

| Slug | Name (ar) | Visual |
|------|-----------|--------|
| `classic` | الكلاسيكي | white box, light bg |
| `card` | بطاقة | raised card on gradient |
| `dark` | ليلي | dark panel |
| `minimal` | بسيط | borderless inputs |

Every template carries the RouterOS placeholders the runtime
needs (`$(link-login-only)`, `$(chap-id)`, `$(chap-challenge)`,
`$(error)`). A regression in any catalogue entry would break
credential submission; the test suite pins this.

### 4.2 Customizable variables

| Variable | Validator | Default |
|----------|-----------|---------|
| `TENANT_NAME` | brand-name regex | "Hoberadius WiFi" |
| `TENANT_LOGO_URL` | http(s) URL or `/path` | `/img/logo.png` |
| `WELCOME_TEXT` | 0-160 chars, no `<>{}` | Arabic welcome |
| `ACCENT_COLOR` | `#XXXXXX` hex | `#2563EB` |
| `BG_COLOR` | `#XXXXXX` hex | `#F8FAFC` |

The renderer does **not** HTML-escape — values land inside the
page itself (URLs, hex colours, brand text) so the validator is
the only thing keeping untrusted input out of the rendered
page.

### 4.3 Designer UI (R2)

Two-column layout: template picker + form on the left, live
preview iframe on the right. Tiny inline script reloads the
iframe on every keystroke. The preview strips RouterOS
`$(...)` tokens so the iframe doesn't show literal text.

Save: POST `/login-designer/save`. UPSERT into
`hotspot_designs` (one row per `(tenant_id, nas_id)`).

### 4.4 Deploy (R3)

POST `/login-designer/deploy` with `confirm=1`. Reads the
**saved design** (not the live form — operator must save first),
re-validates, then:

1. `/file/print` — does `hotspot/login.html` exist?
2. If yes → `/file/set [.id=...] contents=<html>`.
3. If no → `/file/add name=hotspot/login.html contents=<html>`.

Audit: `mt.login_designer.deploy` with path + byte count + ok
flag.

### 4.5 QR auto-login (R4)

Every rendered template gets an injected `<script>` that reads
`?u=<user>&p=<pwd>` from `location.search`, fills the form, and
submits. Missing keys → script returns early, manual login UX
is untouched.

`card_autologin_url(scheme, host, user, password)` builds the
URL a printed QR encodes. Short keys (`u`/`p`) keep the
encoded QR small; reserved characters in the credentials are
percent-encoded.

QR pixel rendering itself ships when a `qrcode` lib is added to
`requirements.txt` — the wiring is in place to call it.

---

## 5. Audit log actions added

Search the audit table for these action strings to see what
HobeRadius did vs the router:

- `mt.devices.toggle` — single-row enable/disable (O3)
- `mt.devices.bulk_toggle` — bulk operation (O3)
- `mt.programming.hotspot.apply` — Q2 apply
- `mt.programming.pppoe.apply` — Q3 apply
- `mt.programming.hotspot.unprogram` — Q4 cleanup
- `mt.programming.pppoe.unprogram` — Q4 cleanup
- `mt.login_designer.deploy` — R3 upload

Each entry carries the actor, nas_id, ok flag, per-step
summary, and error string if any.

---

## 6. Safety contracts (don't break these)

These are invariants the test suite locks in. If you're
extending the operations center, keep them holding:

1. **`nas_devices` is the only canonical router source.** The
   legacy `mikrotik_configs` table was dropped in N3 and the
   legacy `/admin/radius/mt` routes return 410 Gone (N2).
2. **No fake buttons.** Every button has a real backend or
   renders disabled with a `title=` explaining why.
3. **Dangerous operations need POST + CSRF + confirm.** Toggles,
   apply, unprogram, deploy, identity-set, reboot — all four
   layers.
4. **`hoberadius:<kind>` comment is the Q1↔Q4 contract.** Every
   `add` command Q1/Q3 emits must include `comment=` matching
   the corresponding `*_COMMENT` constant in
   `mt_programming.py`. Q4 unprogram filters by exact match.
5. **RouterOS placeholders are sacred.** A template that drops
   any of `ROUTEROS_REQUIRED` is unwire-able; `deploy_login`
   refuses to upload it.
6. **Tests run with `-p no:randomly`** because the per-test
   `del sys.modules` cleanup in the fixtures isn't reordering-safe.
7. **The audit log survives even if the router call fails.** The
   route layer writes the entry regardless of outcome so an
   incident review still has the trail.

---

## 7. What's not in scope yet

The directive scope landed in O/P/Q/R; these are explicitly
deferred:

- Session disconnect buttons in the Sessions tab. Backend
  (K5.2) is ready; UI needs a confirm-modal layer.
- QR pixel rendering. URL builder + JS injection ship; the
  encoder is one function call away once `qrcode` is added to
  requirements.
- Hotspot/PPPoE "set" (vs add) — Q2 only handles initial setup.
  Updating an existing setup is a future Q-step.
- IPv6 support in the programming planner. Validators
  IPv4-only today.
