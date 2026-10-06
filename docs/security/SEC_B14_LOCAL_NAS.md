# SEC B-14 follow-up: server-local RADIUS sources

Branch `agent/sec-b14-local-nas`, based on `release/security-rc1` (9f0ca2b7). Local only: nothing pushed or deployed.
Builds on `SEC_FIX_B14.md` (migration 201, `services/nas_tenant.py`).

## 1. What the pre-deploy detection found (client20, 2026-10-06, read-only)

`tools/sec_b14_detect.sql` D6 listed three RADIUS sources from the last 7 days that no router row claims. client20 has 2 tenants. Tenant 2 (`acme`, created 2026-09-27) has 0 subscribers, 0 routers, 0 cards and 0 sessions.

| Source | Rows (7 d) | What it is | Evidence |
|---|---|---|---|
| `127.0.0.1` | 960 sessions, 360 users, all closed, tenant 1 | **The round-6 stress test's Python RADIUS client**, run on the host on 2026-10-01 between 10:09 and 10:18. Not accel, not a health probe, not the app. | Every `acctsessionid` starts with `r6ops-`. The round-6 report says the client used `127.0.0.1:1812/1813` with the local client secret and `NAS-IP-Address=10.10.0.1`. The `radpostauth` rows of that run carry `nas=10.10.0.1`. FreeRADIUS runs with `network_mode: host`, and `clients.conf` ships `client localhost { ipaddr = 127.0.0.1 }`. Neither container healthcheck sends RADIUS: one is `pgrep`, the other is an HTTP `_health`. |
| `10.99.99.1` | 20 sessions, 20 users, **14 open**, tenant 1 | **Synthetic demo rows written straight into SQLite**, never through RADIUS. No interface, route or FreeRADIUS client has this address. | Timestamps are ISO `…T…Z`, which FreeRADIUS never writes. Session ids look like `demo-N` and unique ids like `demo-u-N`. A host cron, `/etc/cron.d/hr-demo-keepalive` → `/usr/local/bin/hr-demo-keepalive.sh` (2026-10-01), runs `sqlite3 … UPDATE radacct SET acctupdatetime=…, octets += random()` every minute. That keeps 14 phantom "online" sessions alive. The app log shows `find_all_nas_for_sessions: skipping session demo-N on NAS 10.99.99.1` every few seconds. |
| `10.50.0.1` | 4 sessions, 1 user (`rtr-*`), tenant 1 | **The host's own accel-ppp** (SSTP/PPTP management tunnels). | `10.50.0.1/32` sits on `lo` and is the `ppp1` local end. `/etc/accel-ppp.conf` `[radius]` has `nas-ip-address=10.50.0.1`, `server=127.0.0.1`, and `nas-identifier=accel-mgmt`. accel binds its RADIUS socket to `nas-ip-address`, and the installer comment says the same ("accel talks RADIUS … FROM the gateway IP"). FreeRADIUS has a client `accel_local_sstp { ipaddr = 10.50.0.1 }` in `freeradius-clients-wizard/accel-local-sstp.conf`. The rows have `NAS-Port-Type=Virtual`, `Called-Station-Id` = the host's public IP, and the router `Gr3` (tenant 1, `10.50.0.2`). Only `rtr-*` accounting came through it. `rtr-*` auth uses the `sql` path, not `/internal/auth`, so no data-connection subscriber used it. |

In the app, accel's local NAS has **no `nas_devices` row and no tenant setting**. It exists only as the FreeRADIUS client file written by `deploy/accel-ppp/install-accel-selfsigned.sh` (step 7b). Nothing in the app reads `rtr-*` rows from `radacct`.

Also noticed: client20's `nas_devices` id 1 (`MT-HQ-Core`) has `address = 10.10.0.1`, which is the server's own `wg0` address.

## 2. The two problems

1. **Fail-closed hits the server itself.** On a multi-network server, B-14 rejects and quarantines every source that no live router row claims. That includes the host's accel-ppp gateway and anything on loopback. Data-connection subscribers served by the local accel would be cut off.
2. **Hijack.** In 201 any tenant can create a router row whose address is `127.0.0.1` or the accel gateway. The view then gives that tenant **every** session and login from the server's own NAS.

## 3. The fix (migration 202)

**An explicit, operator-only registry: `radius_local_nas`.**

The table has the columns `ip`, `purpose`, `tenant_id`, `service`, `note`, and the two timestamps. It is written only by `tools/radius_local_nas.py`. No app route or repo writes it, and a test enforces that.

| purpose | Meaning | Accounting | Login (`/internal/auth`) |
|---|---|---|---|
| `nas` + tenant | A local NAS serving **one** network | That tenant | Evaluated in that tenant |
| `mgmt` | Local NAS carrying only router management tunnels (`rtr-*`) | `radius_unattributed`, reason `local_mgmt`, still ACKed | Reject + quarantine `local_mgmt` |
| `probe` | Health probe / smoke test | **Nothing written** (0 rows → noop → ACK) | Reject "Health probe": no `radpostauth`, no quarantine, no webhook, no fail2ban count |

The view and fallback rules:

- **`radius_source_tenant` is rebuilt** as registry rows `UNION ALL` router claims. Router claims drop:
  - loopback addresses (`127.0.0.0/8`, `::1`, `0.0.0.0`);
  - every registered address.

  **A tenant can no longer claim the server's own addresses.** The view has a new column, `local_purpose`.
- **No implicit "loopback = tenant 1".**
  - The only-tenant fallback (`radius_sole_tenant`) now applies only to addresses **not** in the registry. A registered address is never guessed, so a `nas` entry whose tenant was deleted fails closed with `local_nas`.
  - Unregistered sources keep the 201 rule exactly. On a single-network server that is the only tenant (unchanged, loopback included). On a multi-network server it is quarantine.
- **FreeRADIUS `mods-enabled/sql`:**
  - the 18 fallback sub-selects gain `WHERE NOT EXISTS (… radius_local_nas …)`;
  - the 3 quarantine INSERTs record `local_<purpose>` as the reason and skip `probe`.

  No other query text changed.
- **Flask `nas_tenant.resolve_source_tenant`** applies the same rule. `internal_auth` short-circuits `probe`. A parity test runs both sides against the same cases.
- **`tools/sec_b14_backfill.py`** now handles local sources:
  - router claims on loopback or registered addresses never "own" rows, so a hijacking row cannot pull tenant 1's loopback history;
  - a registry `nas` entry owns its rows from its `created_at`;
  - `mgmt` and `probe` own nothing (verdict `local_no_tenant`).

  It still works on a DB from before migration 202.
- **`tools/sec_b14_detect.sql` D6** now classifies each unclaimed source. It is still read-only and still works before 201 and 202:

  | Class | Meaning |
  |---|---|
  | `local_loopback` | Loopback source |
  | `local_accel_mgmt_tunnel` | Only `rtr-*` users |
  | `not_via_freeradius` | Every row has ISO timestamps, so the panel wrote it itself |
  | `UNKNOWN` | Anything else. This is the only class that is a true stranger. |

  The new D7 (commented out, needs 202) lists the registry and the sources neither a router nor the registry owns.
- `install-accel-selfsigned.sh` prints a reminder to register the accel gateway. It does **not** register it automatically, because which network the gateway serves is an owner decision.

On client20, D6 now prints `127.0.0.1 local_loopback`, `10.99.99.1 not_via_freeradius` and `10.50.0.1 local_accel_mgmt_tunnel`. There are no `UNKNOWN` rows.

### Limits (documented, not solved here)

- **One local accel cannot serve the data connections of several networks.** Usernames are not unique across tenants, so attributing by username would reopen B-14. Each network would need its own accel instance or gateway address, registered as `nas` + its tenant.
- The view protects only **loopback and registered** host addresses. Another host address (for example `wg0` 10.10.0.1) is protected only once it is registered. Register every source the host itself sends RADIUS from.
- `127.0.0.1` and `172.16.0.0/12` (docker) are still FreeRADIUS clients in the shipped `clients.conf`. On a multi-network server they are now quarantined unless registered.

## 4. Tests

`tests/test_sec_b14_local_nas.py` has 14 tests. They run the shipped FreeRADIUS queries on SQLite with rlm_sql chain semantics, plus `/internal/auth`:

- local accel attributed to its configured tenant, including the same username in both tenants;
- management-only accel belongs to no tenant;
- an unknown non-local source is still rejected and quarantined while the registry is populated;
- an unregistered loopback on a multi-network server fails closed;
- tenant 2's router rows on `127.0.0.1`, `127.0.0.53/32`, `::1` and the registered accel gateway capture nothing;
- a probe creates no session, no quarantine and no login log, on both a multi-network and a single-network server;
- single-network server without a registry is unchanged, even with a loopback router row;
- Python ↔ FreeRADIUS parity with all registry cases, a deleted tenant included;
- CLI validation;
- the registry has no app writer;
- the backfill and D6 agree with the rule.

One existing test changed: `test_python_resolver_agrees_with_the_freeradius_expression`. Its regex had to follow the fallback expression, which is now longer. Its assertions are unchanged.

| | Base 9f0ca2b7 | This branch |
|---|---|---|
| New tests (commit e7bdf012) | 11 failed, 3 passed | 14 passed |
| B-14 + 24 related RADIUS / auth / accounting / NAS / CoA / device-limit / migration modules | 370 passed | 370 passed + 14 new = 384 passed, 0 failed |

**Not verified:** `radiusd -XC` with the new SQL. It is the same as for 201, so run it in the image before deploy.

## 5. What the owner must do on client20 before activating B-14

1. **Decide about tenant 2 (`acme`).** It is empty. If it is a leftover, removing it makes client20 single-network again, and B-14 then changes nothing there (see SEC_FIX_B14 §7.2). The steps below are needed only if client20 stays multi-network.
2. **Register the accel gateway** after deploy, once migration 202 has applied:
   ```
   docker exec hoberadius python /app/tools/radius_local_nas.py --db /app/instance/hoberadius.db \
       set 10.50.0.1 --purpose mgmt --service accel-ppp
   ```
   Use `--purpose nas --tenant 1` instead if the local accel will serve tenant 1's data connections (transport `vps_accel`). It cannot serve both tenants.
3. **127.0.0.1**: nothing real uses it today; only the 2026-10-01 load test did. Leave it unregistered, which means fail closed. Register it as `probe` only if a future smoke test or monitor sends RADIUS from loopback.
4. **10.99.99.1**: not a RADIUS source, so B-14 is unaffected. However, the host cron `hr-demo-keepalive` keeps 14 fake open sessions alive in tenant 1 (online count, session guard, CoA warnings). Remove the cron and the `demo-*` rows when the demo is over. That is an owner decision; nothing was touched.
5. After deploy, run D6/D7 again. `UNKNOWN` must be empty, and D5 (quarantine) should show only the expected `local_mgmt` rows.

## 6. Rollback

Revert commit 266405ab. To also remove the schema:

```
DROP VIEW radius_source_tenant;
DROP TABLE radius_local_nas;
DELETE FROM _migrations WHERE name='202_sec_b14_radius_local_nas.sql';
```

Then re-run migration 201's `CREATE VIEW radius_source_tenant`.
