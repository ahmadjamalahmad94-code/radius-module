# MikroTik Control Center — Phase K Plan

> Build a full remote-control surface for MikroTik routers inside
> the radius admin, so the operator **never has to log into the
> router's WinBox/WebFig directly**. Couples a VPN-based connection
> layer (so routers behind NAT just work) with a live monitoring +
> operations dashboard.

The plan follows the same execution rules as J8 (Flutter Windows
parity): documented up-front, one logical commit per step, mobile
admin unchanged, no `git add .`, strict verification after every
commit.

---

## North-star

- One operator opens `https://radius.vps/admin/radius/mt/<id>` and sees:
    * KPI strip: **CPU %, RAM, Temperature, Uptime, RouterOS version,
      Identity, Time, VPN status, Public IP**.
    * Live interface traffic chart (last 5 min).
    * Active hotspot + PPP users with per-user traffic + disconnect.
    * Recent log lines.
    * Quick actions: backup-now, reboot, identity edit, ping, traceroute.
- All without ever opening WinBox.
- All routers contacted via WireGuard tunnel (≥ K1) so NAT'd routers
  with dynamic IPs work the same as routers with static public IPs.

## Architecture

```
Browser ──HTTPS──> Flask admin ──MT-API──> WireGuard tunnel ──> MikroTik
                       │
                       └─── cache (60 s TTL for live stats so the
                            UI auto-refresh doesn't hammer the router)
```

## Scope split

Phase | What lands | Files | Commits
------|-----------|-------|--------
**K0** Inventory + plan         | This file + dependency check | docs/, requirements.txt | 1
**K1** VPN connection layer     | Schema migration + resolver + UI form + auto-config | nas table, services, templates | 5
**K2** MT client abstraction    | Connection multiplexer (direct vs VPN) + cache + error envelope | integration/mikrotik/* | 2
**K3** System stats             | resource / health / identity / clock endpoints | services/mikrotik_stats.py | 2
**K4** Interface + network      | interface list + monitor (SSE) + IP + routes | services/mikrotik_network.py | 2
**K5** Hotspot + PPP            | active users + disconnect + per-user traffic | services/mikrotik_users.py | 2
**K6** Queues + firewall        | simple queues + filter + NAT + address lists | services/mikrotik_traffic.py | 2
**K7** Logs + diagnostics       | log tail + ping + traceroute | services/mikrotik_diag.py | 2
**K8** Backup + reboot          | backup list/create/download + reboot button | services/mikrotik_admin.py | 2
**K9** Dashboard UI             | New `/mt/<id>/dashboard` page with KPIs + chart + lists | templates/radius/mt_dashboard.html | 3
**K10** Sub-pages UI            | interfaces / users / queues / logs / diagnostics | templates/radius/mt_*.html | 5
**K11** Tests                   | Mock MT client + endpoint contracts + UI smoke | tests/test_mikrotik_*.py | 3
**K12** Documentation           | OPERATOR_GUIDE + WIREGUARD_SETUP + README update | docs/ | 1

**Total: ≈ 32 commits over ~5-7 focused work days.**

---

## Phase K1 — VPN connection layer (foundation)

### K1.1 — schema migration

Add to `nas` table (and `mikrotik_configs` if separate):

```sql
ALTER TABLE nas ADD COLUMN connection_mode  TEXT    DEFAULT 'direct';
   -- 'direct' | 'vpn'
ALTER TABLE nas ADD COLUMN vpn_peer_address TEXT;
   -- e.g. '10.10.0.5'
ALTER TABLE nas ADD COLUMN vpn_interface    TEXT    DEFAULT 'wg0';
ALTER TABLE nas ADD COLUMN vpn_public_key   TEXT;
   -- WireGuard public key of the router (for the server-side peer table)
ALTER TABLE nas ADD COLUMN vpn_last_handshake_ts INTEGER;
   -- Updated by the VPN probe; 0 = never seen
```

Migration file: `app/radius/db/migrations/033_nas_vpn.sql`.

### K1.2 — connection resolver helper

In `app/radius/services/nas_service.py` (or wherever NAS rows are read):

```python
def resolve_connection_address(nas: dict) -> str:
    """Where does the admin dial when talking to this router?"""
    mode = (nas.get("connection_mode") or "direct").lower()
    if mode == "vpn" and nas.get("vpn_peer_address"):
        return nas["vpn_peer_address"]
    return nas["ip"]
```

Every site that currently reads `nas["ip"]` (search across
`integration/mikrotik/pool.py`, `mikrotik_adapter.py`, routes) is
updated to call `resolve_connection_address(nas)` instead.

### K1.3 — VPN status probe

`app/radius/services/vpn_probe.py` (new):

```python
def is_peer_alive(peer_ip: str, timeout_sec: float = 1.5) -> bool:
    """ICMP ping the WireGuard peer. Cached for 30 s per peer."""

def read_handshake_age(public_key: str) -> int | None:
    """Parse `wg show wg0 latest-handshakes` for this peer.
    Returns age in seconds, or None if never seen."""
```

Background worker (or cron-like task already in the project)
updates `nas.vpn_last_handshake_ts` every 60 s.

### K1.4 — UI form fields

In the NAS create/edit form template:

- Radio buttons: **اتصال مباشر** | **عبر WireGuard**
- When VPN selected: show two fields:
  - `vpn_peer_address` (placeholder `10.10.0.5`)
  - `vpn_public_key` (multi-line textarea, monospace)

### K1.5 — status chip + auto-config generator

- In NAS list / dashboard: small chip per row:
  - 🟢 **VPN ✓ متصل** (handshake age < 3 min)
  - 🟡 **VPN ⚠ بطيء** (3-10 min)
  - 🔴 **VPN ✗ مفصول** (> 10 min)
- New endpoint `GET /admin/radius/nas/<id>/wireguard-config`
  returns ready-to-paste config:
  ```
  /interface/wireguard add name=wg-radius listen-port=13231 \
      private-key="<paste server pub here>"
  /interface/wireguard/peers add interface=wg-radius \
      public-key="<server_pub>" endpoint-address=<VPS_IP> \
      endpoint-port=51820 allowed-address=10.10.0.0/24 \
      persistent-keepalive=25s
  /ip/address add interface=wg-radius address=<peer_ip>/24
  ```

---

## Phase K2 — MT client abstraction

### K2.1 — wrap existing client

The current `MikrotikClient` is fine. Add a thin **factory** that:

1. Reads `nas` row.
2. Calls `resolve_connection_address()`.
3. Returns a configured client.
4. Wraps every call in a try/except that surfaces a clean
   `RouterUnreachable` error to the UI instead of socket errors.

### K2.2 — TTL cache

Live stats endpoints would hammer the router on every UI refresh.
Add a per-router in-memory cache (60 s TTL for `system/resource`,
30 s for interface stats, etc.).

```python
class TTLCache:
    def get_or_fetch(key: str, fetcher: Callable, ttl: float)
```

Stored in the operations service. No Redis dependency — keep it
in-process so the deployment story stays "one VPS, one process".

---

## Phase K3 — System stats endpoints

| Endpoint | RouterOS command | Returns |
|----------|------------------|---------|
| `GET /api/v1/mikrotik/<nas_id>/system/resource` | `/system/resource/print` | CPU %, RAM total/free, version, board, architecture, total/free HDD |
| `GET /api/v1/mikrotik/<nas_id>/system/health` | `/system/health/print` | CPU temp, board temp, voltage, fan-speed (if available) |
| `GET /api/v1/mikrotik/<nas_id>/system/identity` | `/system/identity/print` | name |
| `GET /api/v1/mikrotik/<nas_id>/system/clock` | `/system/clock/print` | current date/time, timezone, gmt-offset |
| `GET /api/v1/mikrotik/<nas_id>/system/routerboard` | `/system/routerboard/print` | model, serial, firmware, current/upgrade |

All return JSON with a consistent envelope:

```json
{
  "data": { ... },
  "fetched_at": "2026-05-22T10:30:00Z",
  "cached": true,
  "router_id": 5,
  "router_name": "main-gw"
}
```

---

## Phase K4 — Interface + network

| Endpoint | Returns |
|----------|---------|
| `GET /api/v1/mikrotik/<id>/interfaces` | list with rx/tx bytes, packets, errors, link state, MAC, type, running |
| `GET /api/v1/mikrotik/<id>/interfaces/<name>/traffic` | rx/tx bps NOW + last-minute history |
| `GET /api/v1/mikrotik/<id>/interfaces/<name>/sse` | Server-Sent Events stream — pushes a sample every 2 s |
| `GET /api/v1/mikrotik/<id>/ip/addresses` | IP address table with interface |
| `GET /api/v1/mikrotik/<id>/routes` | routing table |

---

## Phase K5 — Hotspot + PPP active users

| Endpoint | Returns / Action |
|----------|------------------|
| `GET /api/v1/mikrotik/<id>/hotspot/active` | list of active hotspot sessions |
| `GET /api/v1/mikrotik/<id>/ppp/active` | list of active PPPoE/PPTP/L2TP |
| `POST /api/v1/mikrotik/<id>/hotspot/active/<id>/disconnect` | kicks user |
| `POST /api/v1/mikrotik/<id>/ppp/active/<id>/disconnect` | kicks user |
| `GET /api/v1/mikrotik/<id>/hotspot/active/<id>/traffic` | last-hour traffic samples |

---

## Phase K6 — Queues + firewall (read-mostly)

| Endpoint | Returns |
|----------|---------|
| `GET /api/v1/mikrotik/<id>/queues/simple` | simple queues list |
| `PUT /api/v1/mikrotik/<id>/queues/simple/<id>` | edit max-limit / disabled |
| `GET /api/v1/mikrotik/<id>/firewall/filter` | filter rules (read only — too dangerous to edit blind) |
| `GET /api/v1/mikrotik/<id>/firewall/nat` | NAT rules (read only) |
| `GET /api/v1/mikrotik/<id>/firewall/address-lists` | list + add/remove entries |

---

## Phase K7 — Logs + diagnostics

| Endpoint | Returns / Action |
|----------|------------------|
| `GET /api/v1/mikrotik/<id>/log?topics=…&limit=…` | recent log entries |
| `POST /api/v1/mikrotik/<id>/tools/ping` | { target, count } → ping result rows |
| `POST /api/v1/mikrotik/<id>/tools/traceroute` | { target } → traceroute hops |
| `POST /api/v1/mikrotik/<id>/tools/dns-resolve` | { name } → resolved IPs |

---

## Phase K8 — Backup + reboot

| Endpoint | Action |
|----------|--------|
| `GET /api/v1/mikrotik/<id>/files` | list files (incl. *.backup) |
| `POST /api/v1/mikrotik/<id>/system/backup/save` | create backup (saved on router) |
| `GET /api/v1/mikrotik/<id>/files/<name>/download` | stream file to admin VPS |
| `POST /api/v1/mikrotik/<id>/system/reboot` | reboot, requires confirmation |
| `POST /api/v1/mikrotik/<id>/system/identity/set` | rename router |

---

## Phase K9 — Dashboard UI

`/admin/radius/mt/<nas_id>/dashboard` — single-page control surface.

```
┌─────────────────────────────────────────────────────────────────┐
│ ← العودة لقائمة الراوترات        main-gw — CCR2004-1G   🟢 VPN  │
├─────────────────────────────────────────────────────────────────┤
│  CPU  18%   RAM  42%/2GB   TEMP  52°C   UPTIME  12d 3h          │
│  RouterOS 7.13.2     IP 10.10.0.5     محلي 16:42 (UTC+3)        │
├──────────────────────────────────┬──────────────────────────────┤
│  📈 حركة الواجهات (5 د الأخيرة)  │  👥 المتصلون                  │
│  ─────────────────────────────  │  ────────────────             │
│  ether1   ████████░░  120 Mbps  │  hotspot:                    │
│  ether2   ██░░░░░░░░   12 Mbps  │   d2-85104  · 250MB · 0:12   │
│  wg0      █░░░░░░░░░    5 Mbps  │   d2-18720  · 180MB · 0:08   │
│                                  │  pppoe:                       │
│                                  │   home-001  · 1.2GB · 02:45  │
├──────────────────────────────────┴──────────────────────────────┤
│  📋 سجل الأحداث الأخير                        [↻ تحديث]         │
│  16:41:23 hotspot: d2-85104 logged in via ether2                │
│  16:38:01 pppoe-out1: state=connected                            │
│  16:32:55 admin login via ssh from 192.168.88.10                 │
├─────────────────────────────────────────────────────────────────┤
│  ⚙️ إجراءات سريعة                                                │
│  [💾 backup الآن] [🔄 reboot] [🏷 تعديل الاسم] [🔍 ping] [📤 صدّر]│
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase K10 — Sub-pages

Linked from the dashboard, each is a focused panel:

- `/mt/<id>/interfaces` — full interface table + per-interface monitor
- `/mt/<id>/users` — hotspot + PPP combined view with disconnect
- `/mt/<id>/queues` — simple queues editor
- `/mt/<id>/firewall` — firewall + NAT viewer (read-only)
- `/mt/<id>/logs` — log viewer with filters
- `/mt/<id>/diagnostics` — ping + traceroute + DNS console

---

## Phase K11 — Tests

- `tests/test_mikrotik_client_mock.py` — mock client + cache TTL
- `tests/test_mikrotik_stats_endpoints.py` — system/resource etc.
- `tests/test_mikrotik_users_endpoints.py` — hotspot/active + disconnect
- `tests/test_vpn_probe.py` — handshake parsing + ping probe
- `tests/test_mt_dashboard_ui.py` — dashboard page renders + has key KPI markers

---

## Phase K12 — Documentation

- `docs/MIKROTIK_CONTROL_GUIDE.md` — operator's guide (what each
  page does, what each KPI means)
- `docs/WIREGUARD_SETUP.md` — server + router setup with copy-paste
  blocks for both sides
- Update `README.md` with link to the control center

---

## Strict rules during execution

1. **No `git add .`** — stage exact files only.
2. **One logical commit per step.** Naming `K1.1`, `K1.2`, …
3. **`python -m pytest -q tests/test_mikrotik_*.py` clean before each commit.**
4. **No mutation endpoints fire without a confirmation step.** Reboot,
   delete, set-identity all show a confirm modal.
5. **Cache every read.** 60 s default TTL — operator UI polls but
   the router doesn't sweat.
6. **Never expose passwords** in any JSON / log line. PPP secrets,
   hotspot user passwords stay server-side only.
7. **Preserve mobile-admin.** No mobile screen touched (web-only
   feature). The admin shell is Arabic RTL — keep all UI copy in
   Arabic.
8. **One router at a time.** Operator picks the router; bulk
   operations (e.g. backup-all) live in a separate later phase.

## Sequence of work

1. **Day 1**: K0 + K1.1–K1.5 (VPN foundation)
2. **Day 2**: K2 + K3 (client abstraction + system stats)
3. **Day 3**: K4 + K5 (network + users)
4. **Day 4**: K6 + K7 + K8 (traffic + diagnostics + admin)
5. **Day 5**: K9 (dashboard UI)
6. **Day 6**: K10 (sub-pages) + K11 (tests)
7. **Day 7**: K12 (docs) + polish

Each day is a chunk of 4-6 commits.

---

## Progress log

> Updated at the end of each work session so the next session can
> resume from the right spot without re-reading the whole plan.

### Session 1 — Day 1 + Day 2 (foundation + system stats)

**Status: K0 → K3 done. 7 commits. 40 tests. Zero failures.**

| Step | Commit | Files added |
|------|--------|-------------|
| K0   | `1d27826` | `docs/MIKROTIK_CONTROL_PLAN.md` |
| K1.1 | `f4dda38` | `app/radius/db/migrations/033_nas_vpn.sql` |
| K1.2 | `9edd98b` | `app/radius/services/nas_connection.py` + 9 tests |
| K1.3 | `dc2bad3` | `app/radius/services/vpn_probe.py` + 11 tests |
| K1.4 + K1.5 | `0bd935c` | `app/radius/services/wireguard_config.py` + 10 tests |
| K2   | `3667a3b` | `app/radius/services/mikrotik_admin_client.py` + 10 tests |
| K3   | `1a266ef` | `app/api/v1/mikrotik_control.py` + `__init__.py` registration |

### Session 2 — Day 3 + Day 4 (network surface → diagnostics)

**Status: K4 → K7 done. +8 commits. +35 tests (55 total). Zero failures.**

| Step | Commit | Files touched |
|------|--------|---------------|
| K4.1 | `e70a211` | `mikrotik_admin_client.py` (+4 fetchers) · `mikrotik_control.py` (+4 endpoints) · `__init__.py` comment · `test_mikrotik_admin_client.py` (+6 tests) |
| K4.2 | `afd1e5b` | `mikrotik_admin_client.py` (+`stream_interface_samples`) · `mikrotik_control.py` (+SSE endpoint) · `test_mikrotik_admin_client.py` (+4 tests) |
| fix  | `32700ba` | `mikrotik_control.py` — fix `fail()` positional-arg bug on the 404 branch (latent in K3+K4.1) |
| K5.1 | `20b41c7` | hotspot + PPP active reads (2 fetchers + 2 endpoints + 2 tests) |
| K5.2 | `635fbcf` | disconnect mutations + `_run_mutation` helper + `_audit_mutation` in routes (2 fetchers + 2 endpoints + 5 tests) |
| K6.1 | `a2a9c19` | simple queues read + safe `set` (allowlist of editable fields) (2 fetchers + 2 endpoints + 7 tests) |
| K6.2 | `80e3c0d` | firewall filter/nat (read-only) + address-list CRUD (5 fetchers + 4 endpoints + 8 tests) |
| K7.1 | `b9433fa` | log tail with topic + limit filters (1 fetcher + 1 endpoint + 4 tests) |
| K7.2 | `6144ff9` | ping + traceroute + dns-resolve (3 fetchers + 3 endpoints + 9 tests, all cache-bypass) |

### What ships today

The operator can already (manually) provision a router on VPN and
hit live stats:

1. Insert a `nas_devices` row with `connection_mode='vpn'` +
   `vpn_peer_address` + `vpn_public_key`.
2. Generate the router-side WG block via
   `wireguard_config.build_for_new_peer(...)` and paste into
   RouterOS terminal.
3. Paste the printed router pub-key back into the NAS row.
4. Append the server-side block to `/etc/wireguard/wg0.conf`,
   reload, and `wg show wg0` should show a handshake.
5. `curl -H "Authorization: Bearer <token>"
        https://radius.vps/api/v1/mikrotik/<id>/system/overview`
   returns CPU / RAM / temp / uptime / RouterOS version through
   the tunnel.

Note: there is no admin-side UI for steps 1, 3 or 4 yet. The
*endpoints* are production-ready; the *UI* lands in K9 / K10.

### Resume here — Session 3 starting point

Begin with **K8 (backup + reboot)**. This is the last endpoint
batch before the UI work (K9/K10). It pulls in two new shapes
the previous phases didn't:

1. **File download.** `GET /files/<name>/download` streams a
   .backup file from the router through the admin to the operator's
   browser. The wire client doesn't have a binary-fetch helper yet;
   K8.1 may need a small `/file/print` + `/tool/fetch` round-trip
   or an SSH/SCP side-channel (decide at the start of the session).
2. **Destructive mutations.** `reboot` and `identity/set` must
   refuse to fire without an explicit confirm token — pattern to
   reuse: include `confirm=true` in the JSON body, otherwise the
   endpoint returns 409 + an Arabic "confirmation required"
   message. UI confirmation modal lands in K9/K10.

Outstanding endpoint phases:

- **K8** Backup + reboot (≈2 commits)

Outstanding UI phases:

- **K9** Dashboard UI (≈3 commits)
    * `/admin/radius/mt/<id>/dashboard` route + template
    * KPI strip + live traffic chart + active users panel
    * Quick actions strip (backup / reboot / ping / identity)
- **K10** Sub-pages (≈5 commits) — one per surface

Outstanding misc:

- **K11** Tests (mock MT client + endpoint contracts + UI smoke,
  ≈3 commits)
- **K12** Documentation
  (`docs/MIKROTIK_CONTROL_GUIDE.md` + `docs/WIREGUARD_SETUP.md`
  + README update, ≈1 commit)

### Conventions to keep

- One logical commit per step. Commit messages start with the
  step label (`K4.1:`, `K4.2:`, …).
- Every new service file ships its own test file in the same
  commit. Aim for ≥ 80 % branch coverage on the new code.
- Every new endpoint returns the standard `MtResult` envelope
  via `mikrotik_admin_client.fetch_cached(...)`.
- Every mutation endpoint:
    1. Requires `require_api_token`.
    2. Calls `invalidate_cache(router_id, operation)` after
       success.
    3. Logs an audit event via the existing audit service.
    4. Has a UI confirmation step (K9/K10 modals).
- No `git add .`, no `--no-verify`, no force-push.
- Don't touch unrelated dirty files (the long-standing
  subscribers_repo / cards / sync_queue work-in-progress
  must stay untouched).
- Mobile admin remains unchanged — this is a web-only feature.
- Arabic copy throughout the UI (mirrors the rest of the admin).

### Useful one-liners for the next session

```bash
# Re-confirm the K-phase tests are green
python -m pytest \
  tests/test_nas_connection_resolver.py \
  tests/test_vpn_probe.py \
  tests/test_wireguard_config.py \
  tests/test_mikrotik_admin_client.py \
  -q

# Confirm all K3 → K7 routes are registered (25 rules expected)
python -c "
from app import create_app
app = create_app()
rules = [r for r in app.url_map.iter_rules()
         if 'mikrotik' in str(r) and '/<int:nas_id>/' in str(r)]
print(len(rules), 'routes')
for r in sorted(rules, key=str):
    print(' ', '|'.join(sorted(r.methods - {'HEAD','OPTIONS'})), str(r))
"

# Quick check that the migration applied on a fresh DB
python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.db.connection import db
    print([r['name'] for r in db().execute(
        'PRAGMA table_info(nas_devices)')
        if r['name'].startswith('vpn_') or
           r['name'] == 'connection_mode'])
"
```

---

## Onboarding-script firewall & SSTP gotchas (hard-won)

Two invariants the generated one-paste script MUST preserve
(`app/radius/services/router_onboarding_script.py`):

### 1. No broad forward accept above the Hotspot dynamic rules

The managed `hr-fw:` block is lifted to the **top of each chain** by the
move-to-top loop (so the mgmt path always has priority). RouterOS Hotspot
installs its OWN dynamic forward rules (`hs-unauth`, `hs-auth`) that drive the
captive portal. Therefore the `hr-fw:` block must **never** contain an
unconditional `chain=forward action=accept`: after the move-to-top it would
sit ABOVE `hs-unauth`, so unauthenticated clients get accepted before the
Hotspot can intercept — the captive portal never redirects and the login page
never appears (symptom: iPhone `captive.apple.com` shows *"server cannot be
found"*; disabling that one accept rule fixes it instantly).

Only **specific** forward accepts are allowed in the block (walled-garden,
mgmt-tunnel out-interface, RADIUS dst, DNS) plus the expired-pool reject.
Active subscribers get internet from RouterOS's implicit end-of-chain accept
(or the Hotspot's own `hs-auth`). Regression test:
`test_no_unconditional_forward_accept_in_managed_block`.

### 2. RouterOS 6 vs 7 SSTP command compatibility

RouterOS 6.x legacy rejects several SSTP-client properties that v7 accepts; an
unknown property fails the whole `add`, so `hr-sstp-mgmt` is never created (and
then the RADIUS route, whose gateway IS that interface, fails too). The
generator branches on the NAS's stored `ros_version` (`nas_devices.ros_version`,
`'6'/'7'/''`; unknown → v7):

| property                                   | v7  | v6 legacy |
|--------------------------------------------|-----|-----------|
| `verify-server-certificate=no`             | yes | yes       |
| `verify-server-address-from-certificate=no`| yes | **omit**  |
| `port=443`                                 | yes | **omit** (defaults to 443) |
| `keepalive-timeout=30`                     | yes | **omit**  |

On v7 `verify-server-address-from-certificate=no` is **required** (else the
default `=yes` re-verifies our IP against a name-CN self-signed cert and the
tunnel flaps). The RADIUS route is added only AFTER the interface exists
(guarded by `:if ([:len [/interface sstp-client find name="hr-sstp-mgmt"]] > 0)`)
so a v7 command mistakenly pasted on a v6 router never leaves an orphan route.
Regression tests: `test_v6_sstp_command_omits_unsupported_props`,
`test_v7_sstp_command_is_full`, `test_route_to_radius_added_only_after_interface_exists`.
