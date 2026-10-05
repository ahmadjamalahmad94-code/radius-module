# SEC FIX B-14: RADIUS accounting and login attempts written to tenant 1

Branch `agent/sec-fix-b14`, based on `agent/sec-integration` (36646db3). Local only: nothing pushed or deployed.
References: `BYPASS_PATHS.md` B-14 and B-10, `THREAT_MODEL.md` I3 / AS3 / AS4.

## 1. The bug

| Path | Before | Effect on a multi-network server |
|---|---|---|
| `deploy/freeradius/mods-enabled/sql`, accounting Start, Interim fallback, Stop fallback | `INSERT … SELECT 1, …`: the literal tenant 1 | Every network's sessions were stored as tenant 1's. Tenant 1's online, sessions and usage pages and the `/api/v1/accounting/*` endpoints showed them, and the other networks saw none of their own. |
| `mods-enabled/sql`, post-auth (shipped but not called) | `VALUES (1, …, '%{User-Password}', …)` | It was latent, but a single config line would have activated it. |
| `/api/v1/internal/auth` (`_resolve_tenant_id`) | Tenant taken from the in-packet `NAS-IP-Address`, falling back to tenant 1 | The attribute is written by the router itself and is usually a LAN address, so the login was **decided against tenant 1's users**. A shared username with tenant 1's password got `Accept` on tenant 2's router. The attempt, including the attempted password, was logged in tenant 1, and fail2ban counted it in tenant 1. A router could also claim another tenant by spoofing the attribute. |
| `/api/v1/internal/postauth` | The same resolver | Tenant 1's webhooks received other networks' `session.authorized` and `session.rejected` events. |

These were reproduced by `tests/test_sec_b14_radius_tenant_attribution.py` in commit `eed515ab`. All 5 tests fail on the base commit and pass after the fix.

## 2. The fix

**Source of truth.** The tenant comes from the packet's UDP source, `Packet-Src-IP-Address`. FreeRADIUS answers only registered clients whose shared secret matched, so the source identifies the router and cannot be chosen by it. A router on a management tunnel sources RADIUS from its tunnel IP, so the source is matched against the same columns the client files are keyed on (`freeradius_translator._radius_source_ip`), on live routers only (`enabled = 1 AND deleted_at IS NULL`):

- `management_remote_address` (SSTP or PPTP);
- `vpn_peer_address` (WireGuard, with any `/32` removed);
- `address`.

The rule is defined once, in migration `196_sec_b14_radius_source_tenant.sql`:

- **`radius_source_tenant`** view: maps each address to its tenant. `tenant_id` is NULL when **two** tenants claim the address.
- **`radius_sole_tenant`** view: the server's only tenant, or NULL when there are several.
- **`radius_unattributed`** table: quarantine, operator-only. It is not tenant-scoped and has **no password column**. It is keyed on (kind, source, session, user) and updated in place, and it is pruned after 30 days (`log_retention`).

Both FreeRADIUS SQL and Flask (`app/radius/services/nas_tenant.py`) read these views. A parity test runs both against the same cases.

| Situation | Accounting | Login |
|---|---|---|
| Address of exactly one tenant's live router | That tenant | Evaluated and logged in that tenant |
| One-tenant server, unknown address (localhost, docker, hand-written clients) | The only tenant (unchanged) | The only tenant (unchanged) |
| Multi-network server, unknown address | `radius_unattributed`, still ACKed | **Access-Reject "Unknown NAS"**, quarantined, no `radpostauth` row, no webhook |
| Address claimed by two tenants | `radius_unattributed` (`ambiguous_nas`) | Reject, quarantined |

**Safe-behaviour decision:** an unknown router is not trusted on a multi-network server. Its packets are quarantined and its logins are rejected. Attributing them to any tenant would repeat the leak. Dropping accounting silently would make the NAS retransmit forever, so the packet is stored in quarantine and acknowledged.

Other changes:

- **The `mods-enabled/sql` UPDATEs are tenant-scoped.** These are Interim, Stop and Accounting-On/Off, plus every `NOT EXISTS` idempotency check. A NAS address reused by another tenant therefore can't touch the previous tenant's rows, even when MikroTik restarts its session counters and repeats `Acct-Session-Id`. A test covers this.
- **`mods-enabled/rest`** now sends `Packet-Src-IP-Address` in both authorize and post-auth. Flask falls back to `NAS-IP-Address` only when that field is missing (an older rest config). On a one-tenant server the fallback ends at the only tenant either way.
- **Legacy SQL `nas` table.** It is deliberately **not** used. Its `tenant_id` column defaults to 1, which is the bug again. The hosting branch uses it as a fallback (see §7).

## 3. Tests

- `tests/test_sec_b14_radius_tenant_attribution.py` (16 tests). It runs the shipped FreeRADIUS queries for real against SQLite, with rlm_sql chain semantics (the next `query` runs only when the previous one changed 0 rows). It also runs `/internal/auth` and `/internal/postauth`, and tenant 1's APIs `/api/v1/accounting/online` and `/api/v1/tools/radius-log`. Cases:
  - reproduction;
  - the same username in two tenants, both ways;
  - `NAS-IP-Address` spoofed to tenant 1's router;
  - NAS address reused after deletion: the zombie row, a repeated session id and Accounting-On;
  - an address claimed by two tenants;
  - a disabled router of another tenant;
  - a renamed router;
  - SSTP address and `/32`;
  - unknown router on a multi-network server: accounting, auth and webhook;
  - one-tenant server unchanged, including the old rest body;
  - Python and SQL parity;
  - retention.
- `tests/test_sec_b14_backfill.py` (5 tests): the dry-run, apply, undo and rollback; a single-tenant no-op; the detection SQL is read-only and selects no `pass`; the tool is not wired into the app.
- Regression run of the related modules: see the final report of this task.

**Not verified:** a real FreeRADIUS 3.2.5 binary loading the new config (`radiusd -XC`). The SQL was executed on SQLite 3.45. The image's libsqlite (Ubuntu jammy, 3.37) supports views and `ON CONFLICT … DO UPDATE` (3.24 or later). Run `radiusd -XC` in the image before any deploy.

## 4. Are single-tenant servers affected?

**No cross-tenant exposure is possible with one tenant.** Every row belongs to the only network anyway. After the fix, a one-tenant server behaves exactly as before: unknown sources still map to its only tenant, and nothing is quarantined or rejected.

One edge case: if the only tenant's id is **not 1** (tenant 1 was deleted), FreeRADIUS rows used to be orphaned under tenant 1 and invisible. They are now attributed correctly.

Run `tools/sec_b14_detect.sql` D1 per server. `tenants_total = 1` means nothing to migrate.

## 5. Migration plan for existing mis-attributed rows

**Nothing runs automatically.** Migration 196 only creates the two views and the empty quarantine table. It changes no existing row.

1. **Detect (read-only, any time, before or after deploy):**
   `sqlite3 -readonly /data/hoberadius.db < tools/sec_b14_detect.sql`
   - D1: number of tenants.
   - D2: address-to-tenant map, including addresses claimed by two tenants.
   - D3: `radacct` stored tenant against owning tenant.
   - D4: `radpostauth` stored tenant against the `nas` attribute. Indicative only. It selects no `pass`.
   - D6 (**pre-deploy on multi-network servers**): RADIUS clients and recent sources that no live router claims. These will be rejected or quarantined after deploy, so register them on the right network first.
2. **Back up the DB.**
3. **Dry-run:** `python tools/sec_b14_backfill.py --db /data/hoberadius.db`. It opens the DB read-only and prints counts by verdict. It never prints usernames or passwords.
4. **Apply:** add `--apply --undo-file /data/sec_b14_undo.json`. This runs as one transaction, and the undo file is written first and never overwritten. A `radacct` row is moved from tenant 1 to tenant T only when all of these hold:
   - its source address is claimed by exactly one other tenant's live router;
   - it started after that router was created, so an address reused after tenant 1 dropped it stays put;
   - T has no twin row for the same session, NAS and user.

   Twins, `unknown_nas`, `ambiguous_nas` and `before_router_existed` rows stay where they are and are reported.
5. **`radpostauth`** is **not** moved, because it never stored the source address. Optional, irreversible, and an owner decision: `--redact-suspect-attempt-passwords` sets `pass` to `***` on tenant-1 attempts whose `nas` is not one of tenant 1's routers. Retention deletes `radpostauth` after 30 days and closed `radacct` after 90 days, so the historical exposure also expires on its own.
6. **Rollback of step 4:** `--rollback /data/sec_b14_undo.json` (dry-run), then add `--apply`. Redacted passwords cannot be restored, by design.

**Transition note (multi-network servers).** A tenant-2 session that was open at deploy time and stored under tenant 1 is not updated by tenant 2's next Interim. Updates are tenant-scoped, so the Interim fallback opens a fresh tenant-2 row instead. The old tenant-1 row stays open until a stale-session close (reconciler or stale cleanup) ends it, and the backfill reports it as `twin_in_target` rather than moving it. That avoids a double row, which would double-count usage and the device limit.

## 6. Behaviour change and rollback

**Behaviour change.** It applies only on servers with two or more tenants:

- logins from a router whose source address no live router row claims, or which two tenants claim, are **rejected** ("Unknown NAS");
- their accounting goes to `radius_unattributed`;
- management-tunnel accounting from accel-ppp (`rtr-*` users), if accel-ppp sends any from an address no router claims, is quarantined there, because it belongs to no network (`rtr-*` authorization uses the `sql` path and is not affected);
- the login tenant now comes from the packet source, not `NAS-IP-Address`.

One-tenant servers: no change.

**Rollback of the code:** revert commit `993860ec` and the migration-plan commit, then rebuild the image as usual.

- The views and the empty table that migration 196 leaves behind are harmless.
- To remove them: `DROP VIEW radius_source_tenant; DROP VIEW radius_sole_tenant; DROP TABLE radius_unattributed; DELETE FROM _migrations WHERE name='196_sec_b14_radius_source_tenant.sql';`
- Reverting brings the tenant-1 leak back.

## 7. Owner decisions

1. **Unknown router on a multi-network server:** reject and quarantine (implemented), or keep accepting under some tenant. The latter is not recommended.
2. **Count tenants for the "only tenant" fallback.** The fallback currently counts **all** rows in `tenants`, including suspended ones. A server that is single-network in practice but has a leftover demo or second tenant row counts as multi-network, so its unregistered clients get quarantined. Run D1 and D6 before deploying.
3. **Disabled routers** are ignored for attribution, the same as for the RADIUS client files. Should a disabled router's address still count?
4. **Whether to run the backfill** on each multi-network server, and whether to redact the suspect attempt passwords (irreversible).
5. **Quarantine visibility:** there is no UI. It is read only via SQL (D5). Decide whether to add a super-admin page.
6. **Hosting branch `trial/multi-tenant-vps`** (A8). It derives the tenant from `nas_devices.address` only, then falls back to the legacy `nas` table, then to **1**. As a result:
   - tunnel routers whose `address` is public are still mis-attributed there;
   - unknown routers still go to tenant 1.

   This fix needs a separate port and review for that branch.
7. Run `radiusd -XC` in the image before deploying (not done here).
