# Phase O / P / Q / R — Full WinBox Replacement Plan

> The operator should never have to open WinBox again. Every
> setting / view / programming task lives in the web admin,
> Arabic-RTL, fully tested, with proper rollback on failure.
>
> Target scale: 1-10 routers per customer.

This doc is the **architectural contract** for the four phases.
Each phase is broken into 3-6 commits; every commit ships its
own tests; nothing is merged until tests are green and the
feature actually works against a live MikroTik (see the
end-to-end smoke section).

---

## Phase O — Super Operations Center (NAS-level)

The current Operations Center (L5) is a flat list with a wizard
button. Phase O turns it into the command surface for fleet
overview.

### O1 — Counters endpoint + service

New service `app/radius/services/mt_counters.py` aggregating per-
router:
- active hotspot sessions count
- active PPP sessions count
- bytes-in / bytes-out totals from `/interface/print` summed
- last handshake age (for VPN routers)
- last successful auth timestamp (from FreeRADIUS logs or
  RADIUS table query)

New endpoint `GET /api/v1/mikrotik/<nas_id>/counters` returning
the standard `MtResult` envelope with all fields. Cached
`TTL_ACTIVE_USERS` (10s).

Tests: mock the wire client, assert each counter is parsed
correctly + the envelope shape matches the K3 family.

### O2 — Operations Center surfaces live counters

Update `app/templates/radius/mt_operations.html` to add three
new columns per row:
- 👥 **active users** (hotspot + ppp combined)
- 📊 **traffic** (bytes-in / bytes-out, K/M/G human-readable)
- 🕐 **last seen** (relative time since last successful API
  ping)

These are populated by a small JS poll loop that calls the O1
endpoint for every visible router every 10s. The page table
gets a "حالة" pill column too (green/amber/red based on
last-seen age).

Tests: UI markers `data-mt-row-counters` per row + a JS smoke
test that the poll cadence is honored.

### O3 — Bulk enable/disable + inline edit

Add per-row toggle buttons:
- **مفعّل / معطّل** (POST to `/admin/radius/devices/<id>/toggle`)
- **حذف** (soft-delete via existing devices service)

Plus a checkbox column + bulk-action bar at the top ("تفعيل
المحدّد" / "تعطيل المحدّد"). All actions audited.

Tests: bulk toggle moves rows between enabled/disabled
correctly; checkboxes survive page refresh via URL params.

---

## Phase P — Per-Router Management Tabs (WinBox replacement)

The current `/admin/radius/mt/<id>/dashboard` is a single
scrollable page with KPIs + traffic + active users. Phase P
turns it into a **tabbed control surface**: System / Interfaces
/ IP / Routes / Neighbors / Logs / Network Programmer.

The K4-K7 backend already provides most data; Phase P is
mostly UI work plus a few new endpoints for missing pieces
(neighbors, link-speed, loop detection).

### P1 — Interfaces tab (link speed + per-port stats)

Surface `/api/v1/mikrotik/<id>/interfaces` (K4.1) in a real
table:
- name · type · running · MAC
- link-rate (1Gbps / 100Mbps / down) — derived from
  `/interface/ethernet/print` (new endpoint `/interfaces/
  ethernet`)
- rx-byte / tx-byte cumulative + per-second from K4.2 SSE
- rx-error / tx-error / rx-drop counters
- per-row "📈 monitor" button → opens K4.2 sparkline modal

New endpoint `GET /api/v1/mikrotik/<id>/interfaces/ethernet`
that returns `/interface/ethernet/print` rows (includes
`speed`, `auto-negotiation`, `cable-settings`, `default-name`).

### P2 — Neighbors tab

New endpoint `GET /api/v1/mikrotik/<id>/neighbors` →
`/ip/neighbor/print`. Returns identity / IP / MAC / interface /
platform / version for each discovered neighbor.

Tab renders this as a table with "Filter by interface" dropdown.

### P3 — IP / Routes tabs

Pure UI for the existing K4 endpoints
(`/ip/addresses`, `/routes`). Two simple tables; auto-refresh
every 30s.

### P4 — Logs tab

Real-time viewer over the K7.1 `/log` endpoint:
- Topic filter chips (info / error / firewall / system / hotspot
  / ppp / dhcp)
- Auto-scroll-to-bottom toggle
- Highlight ERROR / WARN lines
- Search box (client-side filter on visible lines)
- "Pause" button (stops auto-refresh so the operator can read)

### P5 — Loop detection

New endpoint `GET /api/v1/mikrotik/<id>/diagnostics/loops` that
fires three checks server-side:
1. Bridge port duplicates — `/interface/bridge/port/print` rows
   where same MAC appears on multiple ports.
2. STP topology change rate — `/interface/bridge/monitor` once,
   if `topology-changes` is > threshold per minute → flag.
3. Address overlap — `/ip/address/print` rows where two IPs
   are in overlapping subnets.

Returns a list of "warnings" (severity + interface + reason).
Tab displays each as a callout with an Arabic explanation +
suggested fix.

---

## Phase Q — Network Auto-Programming

The killer feature: pick interface → pick mode (hotspot or
broadband) → fill 3 fields → done. The wizard applies a
complete RouterOS config bundle and verifies each step.

### Q1 — Hotspot programmer wizard

`POST /api/v1/mikrotik/<id>/network/program/hotspot` body:

```json
{
  "interface": "ether2",
  "ip_subnet": "192.168.88.0/24",
  "dns_servers": "8.8.8.8,8.8.4.4",
  "pool_name": "hs-pool-ether2",
  "hotspot_name": "guest-wifi",
  "confirm": true
}
```

What it does in order (each step verified, rollback on fail):

1. `/ip/pool add name=<pool_name> ranges=<derived from subnet>`
2. `/ip/address add interface=<iface> address=<subnet first IP>`
3. `/ip/dhcp-server/network add address=<subnet> dns-server=<dns>`
4. `/ip/hotspot/setup` (or its programmatic equivalent)
5. `/ip/firewall/nat add chain=srcnat action=masquerade
   out-interface=<wan-iface>` (auto-detect WAN, fallback to
   user input)
6. `/ip/firewall/filter add chain=forward in-interface=<iface>
   action=accept`

Returns the full transcript of executed commands + state.

### Q2 — Broadband (PPPoE) programmer

`POST /api/v1/mikrotik/<id>/network/program/broadband` —
similar shape, applies:
1. `/ip/pool add ...` (client pool)
2. `/ppp/profile add ...` (default profile)
3. `/interface/pppoe-server/server add interface=<iface>
   service-name=<...> profile=<...>`
4. NAT masquerade for the client pool's range
5. Use RADIUS for AAA (so the wizard's existing nas_devices
   row covers PPP auth).

### Q3 — Unprogramme

Reverse of Q1/Q2: removes the pool / address / hotspot setup /
NAT rule that was added by `interface=<iface>`. Identified by
comments — every Q-added rule gets `comment="HobeRadius-Q-<iface>"`
so the unprogramme step can find + delete safely.

### Q4 — UI wizard

`/admin/radius/mt/<id>/network/program` — three-step form:
1. Pick interface (dropdown from K4 `/interfaces`)
2. Pick mode (hotspot / broadband)
3. Fill subnet + DNS + name
4. Preview the generated RouterOS commands as a code block
5. Confirm → execute → show transcript

---

## Phase R — Hotspot Login Page Designer

A live preview designer where the admin picks one of N
templates, uploads a logo, sets WiFi name + colors, and
deploys to one-or-many routers.

### R1 — Template library

3 Arabic-RTL templates shipped in
`app/static/hotspot_templates/`:
- `coffee-shop` — warm, image-rich
- `office` — minimal, professional
- `isp-branded` — large logo center, brand colors

Each template = a directory containing `login.html`, `error.html`,
`status.html`, `logout.html` + CSS + placeholder images.
Variables like `{{logo_url}}`, `{{wifi_name}}`, `{{brand_color}}`
get substituted at deploy time.

### R2 — Designer UI

`/admin/radius/hotspot-designer` page:
- Left pane: form (template picker, name, logo upload, brand
  color picker)
- Right pane: live iframe preview of the rendered template
- Bottom: "Deploy to..." multi-select of routers

### R3 — Deploy mechanism

For each selected router, the designer:
1. Renders every template file with the form values.
2. Uploads each rendered file via `/file/print` + the wire
   client's file write (a small new helper since K8.1b proved
   binary download isn't supported — this is the upload
   direction).
3. Sets `/ip/hotspot/profile set [find] html-directory=
   hoberadius-<router-id>`.

If upload fails on any file, the deploy step aborts and reports.

### R4 — Logo asset management

Logos uploaded to HobeRadius admin live in
`/app/instance/hotspot_logos/<router-id>/<filename>`. On
re-deploy, the same logo is re-pushed. A "remove logo" button
clears it from both server and router.

---

## Execution rules (every phase)

1. **One commit per sub-step.** O1, O2, O3 are independent;
   so are P1..P5 and Q1..Q4 and R1..R4.
2. **Every commit ships its own tests.** Coverage targets:
   - Service-layer: ≥ 80% branch coverage on new code.
   - Route-layer: at least one happy path + one error path per
     endpoint.
3. **Live VPS smoke test at the end of each phase.** Operator
   walks the new feature end-to-end on the production VPS
   against the real mt-vpn router. Logs captured.
4. **Postmortem updated in real time.** Any new gotcha gets a
   new entry in `POSTMORTEM_PHASE_K_L_M.md` immediately.
5. **No fake UI.** Every button calls a real endpoint. Every
   table row reflects a real DB state.
6. **No silent failures.** Every error surfaces in the
   standard envelope (`{ok:false, error, took_ms, ...}`) with
   an Arabic message.
7. **Mobile admin untouched.** Phase L's discipline holds —
   this is a web-only iteration.

---

## Stopping criteria

After each commit:
- `python -m pytest -q` is green.
- `python -m compileall app -q` produces no errors.
- `git diff --check` is clean.
- The commit message follows the established format (Phase
  prefix + short title + body explaining "what + why").

End-of-session checklist:
- Every Phase O / P / Q / R sub-step is either committed +
  pushed OR documented as "deferred to next session" in
  POSTMORTEM_PHASE_K_L_M.md with a Phase X tag.
