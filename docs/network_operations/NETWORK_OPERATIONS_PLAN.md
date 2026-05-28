# Network Operations Services — Plan + Contracts

> **Status:** Phase 0 (contracts locked) — Sprint 1 starting.
> **Owner:** HobeRadius core.
> **Last updated:** 2026-05-28.

This document is the **single source of truth** for the Network
Operations service family inside the «خدماتي» tab. Before
shipping any new entry in this family — service planner, route,
template, or DB table — open this file first and confirm:

  1. The service name + service_type matches the table below.
  2. The MikroTik comment prefix matches the table below.
  3. The cleanup script removes EXACTLY what the install script
     adds — keyed by the comment prefix.
  4. Any router-side resource has an audit row in
     `network_device_audit` (sprint 2).

---

## Why this family exists

The «خدماتي» tab already manages 6 router-side services
(hotspot / broadband / block-sites / open-sites / public-ip /
remote-access). Those are **per-router service flows**.

This family is different — it manages the **network around a
router**: APs, switches, NVRs, cameras, servers, and the
operator's ability to reach them. The operator is no longer
just configuring «one MikroTik» — they're running a fleet
where the MikroTik is the gateway and other devices live
behind it.

The use cases:

  * «Where are my APs? Are they online right now?»
  * «AP at the customer site just went down — alert me on Telegram.»
  * «I'm traveling — I need to log into the AP at site #3.»
  * «Bind this AP to a static IP so I can always reach it.»
  * «Scan the LAN — what devices showed up that I don't know about?»

---

## Foundational rule

> **Inventory before action.** Nothing in this family touches a
> device that isn't registered in `network_devices` first.
> No anonymous remote-access, no ad-hoc port forwards, no
> unsigned alerts.

Why: «remote device access» without inventory + permissions +
audit + TTL turns into a security hole. The plan deliberately
puts Device Registry first and Remote Access last.

---

## Service catalogue (service_type values)

| service_type             | label (ar)              | sprint | router-side rules? |
|--------------------------|-------------------------|--------|--------------------|
| `network_device_watch`   | تابع أجهزة الشبكة       | 1+2    | none (HobeRadius-side ping only) |
| `network_device_bypass`  | تجهيز جهاز للإدارة      | 3      | DHCP lease + IP-binding + address-list |
| `network_ip_scan`        | مسح الشبكة              | 4      | none (uses MikroTik ARP / DHCP / IP-Neighbors print) |
| `remote_device_access`   | فتح جهاز عن بُعد        | 5      | NAT dst-nat + filter allow (TTL-gated) |
| `router_netwatch_alerts` | تنبيهات MikroTik        | 6      | `/tool netwatch` + scripts → HobeRadius webhook |

The `service_type` string is what the inventory endpoint
returns and what the «خدماتي» card carries — keep it stable
across renames.

---

## MikroTik comment prefixes

Every row this family creates on a router carries one of these
comment prefixes. The cleanup script ALWAYS keys off the
prefix — never a name match.

| prefix                                  | belongs to                |
|-----------------------------------------|---------------------------|
| `HOBE_NET_DEVICE:<device_id>:`          | reserved (no router rows yet) |
| `HOBE_DEVICE_BYPASS:<device_id>:<role>` | sprint 3 — DHCP / binding / list |
| `HOBE_REMOTE_ACCESS:<session_id>:<role>`| sprint 5 — NAT + filter   |
| `HOBE_NETWATCH:<watch_id>:<role>`       | sprint 6 — netwatch + script |

Where `<role>` is a short qualifier so a single delete can
remove the whole group: `dhcp-lease`, `ip-binding`,
`address-list`, `dst-nat`, `filter-allow`, `netwatch`,
`up-script`, `down-script`.

**Cleanup contract:**

```
/ip ... remove [find comment~"^<PREFIX>"]
```

The `^` anchor is mandatory. Without it, a comment like
`note about HOBE_DEVICE_BYPASS:25:dhcp` (operator-written)
would be matched and wiped.

---

## DB schema overview

### `network_devices` (sprint 1)

The registry. One row per managed device.

| column            | type        | notes |
|-------------------|-------------|-------|
| `id`              | INTEGER PK  | auto |
| `tenant_id`       | INTEGER     | scoped per tenant |
| `router_id`       | INTEGER     | FK → `nas_devices.id`, the router this device sits behind |
| `name`            | TEXT        | operator-facing label («AP الطابق الأول») |
| `device_type`     | TEXT        | one of `ap`, `router`, `switch`, `camera`, `nvr`, `server`, `other` |
| `ip_address`      | TEXT        | internal LAN IP (e.g. 192.168.1.10) |
| `mac_address`     | TEXT        | optional, used by sprint 3 (bypass) |
| `location`        | TEXT        | operator note («السطح») |
| `management_port` | INTEGER     | default 80 — what to open for sprint 5 |
| `notes`           | TEXT        | free text |
| `is_critical`     | INTEGER (bool) | 1 → alert escalates |
| `watch_enabled`   | INTEGER (bool) | 1 → ping job will poll |
| `alert_enabled`   | INTEGER (bool) | 1 → telegram on flip |
| `last_status`     | TEXT        | `up` / `down` / `unknown` (sprint 2) |
| `last_checked_at` | TEXT        | ISO timestamp (sprint 2) |
| `last_latency_ms` | REAL        | last successful RTT (sprint 2) |
| `created_at`      | TEXT        | ISO |
| `updated_at`      | TEXT        | ISO |

Indexes:
  * `(tenant_id, router_id)` — sidebar/list queries
  * `(tenant_id, watch_enabled)` — the cron worker scans this

### `network_device_checks` (sprint 2)

Append-only ping history. One row per probe.

| column          | type    | notes |
|-----------------|---------|-------|
| `id`            | INTEGER PK | auto |
| `device_id`     | INTEGER | FK → `network_devices.id` |
| `checked_at`    | TEXT    | ISO |
| `status`        | TEXT    | `up` / `down` / `unknown` |
| `latency_ms`    | REAL    | NULL on down |
| `error_message` | TEXT    | short reason on down |
| `source`        | TEXT    | `backend_ping` / `router_netwatch` |

Retention: keep last 7 days, then aggregate to hourly. (Sprint
2 — to be revisited based on volume.)

### `remote_access_sessions` (sprint 5)

| column        | type    | notes |
|---------------|---------|-------|
| `id`          | INTEGER PK | auto |
| `device_id`   | INTEGER | FK → `network_devices.id` |
| `router_id`   | INTEGER | FK → `nas_devices.id` |
| `requested_by`| INTEGER | admin id |
| `protocol`    | TEXT    | `http` / `https` / `winbox` |
| `internal_ip` | TEXT    | mirror of device.ip_address at session time |
| `internal_port` | INTEGER | port we're forwarding to |
| `external_port` | INTEGER | VPS-side port we opened |
| `status`      | TEXT    | `active` / `expired` / `closed` / `failed` |
| `expires_at`  | TEXT    | ISO |
| `created_at`  | TEXT    | ISO |
| `closed_at`   | TEXT    | ISO |
| `audit_ip`    | TEXT    | source IP of the admin browser |

---

## Sprint order (do not skip ahead)

1. **Sprint 1** — Device Registry + manual «فحص الآن».
   No periodic polling, no alerts, no router rules.
2. **Sprint 2** — Backend ping cron + check history + health tiers.
   Telegram alerts (cooldown / dedup baked in).
3. **Sprint 3** — `network_device_bypass` service planner
   (DHCP lease + IP-binding + address-list).
4. **Sprint 4** — `network_ip_scan` tool (read-only against
   ARP / DHCP / IP-Neighbors).
5. **Sprint 5** — `remote_device_access` (TTL-gated sessions).
6. **Sprint 6** — `router_netwatch_alerts` (router-side
   netwatch → webhook).

Each sprint ends with:
  * green parse check (Jinja + Python + JS + CSS braces),
  * single focused commit,
  * pause for operator review.

---

## Safety boundaries (apply to every sprint)

  * **Tenant scope** — every query filters by `tenant_id`.
    Cross-tenant access is an instant 403.
  * **Comment-prefix delete** — never delete a row whose
    comment doesn't start with our prefix.
  * **TTL by default** — anything that opens an attack
    surface (sprint 5 sessions, sprint 6 webhooks) gets a
    finite expiry, enforced by both DB and router-side
    scheduler.
  * **Audit log** — every state-changing call goes through
    `get_audit_service()` with the actor + action +
    target.
  * **Read-only first** — sprints 1+4 don't touch the
    router at all (or only `/print`). Write-side sprints
    (3, 5, 6) are explicitly later in the order.

---

## Out of scope (deliberate)

  * SMS provider integration — out until a paying customer
    asks for it (cost ≠ trivial). Schema leaves room.
  * Layer-7 monitoring (HTTP/HTTPS content checks). Ping +
    TCP probe is plenty for «is it on the network».
  * Multi-router devices (a single switch reachable from two
    HobeRadius routers). One device → one router until proven
    otherwise.
  * Auto-recovery actions (reboot router when AP down N
    times). Operator-driven only for now.

---

## File map

```
docs/network_operations/
└── NETWORK_OPERATIONS_PLAN.md             ← this file

app/radius/db/migrations/
├── 081_network_devices.sql                ← sprint 1
├── 082_network_device_checks.sql          ← sprint 2
└── 083_remote_access_sessions.sql         ← sprint 5

app/radius/db/repos/
├── network_devices_repo.py                ← sprint 1
└── remote_access_sessions_repo.py         ← sprint 5

app/radius/services/
├── network_device_monitor.py              ← sprint 2 (cron worker)
├── network_device_bypass_planner.py       ← sprint 3
├── network_ip_scan.py                     ← sprint 4
├── remote_device_access.py                ← sprint 5
└── router_netwatch_planner.py             ← sprint 6

app/radius/routes/
├── network_devices.py                     ← sprints 1+2+4
└── remote_access_sessions.py              ← sprint 5

app/templates/radius/
├── network_devices_list.html              ← sprint 1
├── network_devices_form.html              ← sprint 1
└── network_devices_scan.html              ← sprint 4
```
