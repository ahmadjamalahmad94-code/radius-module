# Postmortem — Phases K / L / M

> Issues we hit during the live build-out of the MikroTik control
> center + setup wizard + WireGuard auto-provisioning, with the
> root cause of each and what we did to prevent recurrence.
>
> **Use this doc as a checklist before any commercial release** —
> every section ends with a "Prevention" block listing what to
> verify in a fresh environment so subscribers never trip on
> these themselves.

Session: 2026-05-22 · 33 commits · live VPS validation on
RouterOS 7.20.6 (CCR1009-8G-1S) end-to-end.

---

## 1) Wrong positional args in `fail()` calls

**Symptom.** Hitting the "NAS not found" branch of any K3+/K4+
endpoint produced `TypeError: fail() got multiple values for
argument 'code'` instead of a clean 404 envelope. Latent — no
endpoint-level test exercised the branch.

**Root cause.** Helper signature in [responses.py](../app/api/responses.py)
is `fail(code: str, message: str = "", *, status: int = 400, ...)`.
Eleven handlers in [mikrotik_control.py](../app/api/v1/mikrotik_control.py)
called it as `fail("الراوتر غير موجود", code="not_found",
status=404)` — passing the Arabic message as positional `code`
AND a `code=` kwarg, which collides.

**Fix.** Commit `32700ba` switched every site to
`fail("not_found", "الراوتر غير موجود", status=404)`.

**Prevention.**
- Add `mypy --strict` (or ruff's `PLE1142`) to CI — would have
  caught this at write time.
- For any new endpoint, the contract test should include a
  404 case so the fail branch is actually exercised.
- Code review reminder: when a helper has positional + keyword
  params, never repeat the positional one by name.

---

## 2) Dashboard took 50 s to render when the router was offline

**Symptom.** Opening `/admin/radius/mt/<id>/dashboard` against an
unreachable router froze for ~50 s before the status pill turned
red.

**Root cause.** [mikrotik_control.mt_system_overview](../app/api/v1/mikrotik_control.py:mt_system_overview)
fired five sub-calls (resource / health / identity / clock /
routerboard) **sequentially** with a 10 s socket timeout each.
Worst case = 5 × 10 s.

**Fix.** Commit `50d6a4e` (L8):
- `ThreadPoolExecutor(max_workers=5)` runs the sub-calls in
  parallel — bottoms out at the slowest single timeout.
- Default per-NAS `timeout_sec` lowered 10 s → 3 s in
  [mikrotik_admin_client._build_router_cfg](../app/radius/services/mikrotik_admin_client.py).
- A unit test pins the parallelism: 5 stubs each `sleep(0.4)`
  must finish in <1.5 s.

**Prevention.**
- Treat any "5+ blocking calls in a row" as a smell — wrap in
  parallel executor.
- Default network timeouts to single digits at the wire-client
  layer; bump per-call only if a specific operation needs it.
- UI poll loops should never block the user for more than one
  router's worth of timeout, no matter how many sub-fetches.

---

## 3) Container could not read `/etc/wireguard/wg0.conf`

**Symptom.** First `docker exec hoberadius ls /etc/wireguard/wg0.conf`
returned `Permission denied`.

**Root cause.** `wg0.conf` is `0600 root:root` (it holds the
server private key). The HobeRadius container runs as `hr`
(uid 999) and can't read root-only files even via bind-mount.
Loosening the file mode was unacceptable — it would leak the
server's WG private key.

**Fix.** Commit `d319418` (M0c) split the WireGuard control
plane:
- `/etc/wireguard/wg0.conf` stays root-only — contains only the
  `[Interface]` block.
- New directory `/etc/hoberadius/wg-peers.d/` owned by `gid=999`,
  mode `0775`. Container writes one peer file per router here.
- Host-side `wg-reload.sh` (run by a systemd path-unit) merges
  the two before calling `wg syncconf`.

**Prevention.**
- For any new container that needs to manipulate host-managed
  config, bind-mount a **purpose-built sub-directory**, never
  the full secret-bearing directory.
- Match the container's user GID to a host group / numeric GID
  via the bind-mount, not by editing the secret file's perms.
- Document the gid expectation in `deploy/.env.example` and the
  cheat sheet so a new VPS install gets the same setup.

---

## 4) `wg-quick strip` rejects tempfile names

**Symptom.** `wg-reload.sh` failed silently in the systemd journal
with:

    wg-quick: The config file must be a valid interface name,
    followed by .conf

Every reload then fed `wg syncconf` an empty stdin (see #5).

**Root cause.** `wg-quick strip` requires its argument's basename
to match `^[a-zA-Z0-9_=+.-]{1,15}$`. Our tempfile names from
`mktemp -p /run wg-reload-XXXXXX.conf` produced strings like
`/run/wg-reload-AbCdEf.conf` — basename `wg-reload-AbCdEf` is
14 chars and matches the regex on paper, but the version of
wg-quick on Ubuntu 22.04 still rejected them.

**Fix.** Commit `5a484cb` (M0d) then `5f8f891` (M0e) replaced
the entire merge-and-strip pipeline. We no longer call wg-quick
strip; instead `wg-reload.sh` concatenates peer-only fragments
straight into a tempfile and passes it to `wg set` (see #5).

**Prevention.**
- Don't rely on wg-quick's CLI sub-commands for non-standard
  paths — they're tuned for `/etc/wireguard/<iface>.conf` only.
- Wire-level operations (`wg set`, `wg show`, `wg syncconf`)
  accept arbitrary paths and are the right primitives for
  programmatic peer management.

---

## 5) `wg syncconf` silently reset ListenPort + PrivateKey

**Symptom.** Every reload changed wg0's listening port to a new
random value (`35692`, `49889`, `36436`, `37402` …) and dropped
its private key. Result: `wg show wg0` printed only the random
port; no peers, no handshake possible.

**Root cause.** `wg syncconf <iface> <file>` treats the input
file as the **authoritative full configuration** for the
interface. When the file has no `[Interface]` section,
syncconf interprets that as "interface settings should be
default" — listen port goes to a random ephemeral and the
private key gets zeroed. The wg(8) docs say "only making changes
that are explicitly different" but in practice "missing field
in input" reads as "explicit difference = reset to default".

**Fix.** Commit `5f8f891` (M0e) abandoned syncconf for peer
operations:
- Snapshot existing peers with `wg show wg0 peers`.
- For each fragment in peers.d, call `wg set wg0 peer <pk>
  allowed-ips <ip> persistent-keepalive <n>` (incremental,
  never touches `[Interface]`).
- Remove peers that are bound to the interface but no longer
  have a fragment.

**Prevention.**
- **Never use `wg syncconf` with a peer-only file.** It's a
  full-config command.
- For peer add / remove / update, use `wg set` per peer.
- Operationally: if syncconf is unavoidable (e.g. bulk replace),
  always include the current `[Interface]` block in the input.

---

## 6) Rapid systemd .path triggers during file edits

**Symptom.** First run of `deploy.sh init-wg-reloader` (M0b)
fired the wg-reload service **5 times in 1 second** while we
were still rewriting `wg0.conf`. The interface ended up in a
broken state (lost private key + peers) and required
`ip link delete wg0; wg-quick up wg0` to recover.

**Root cause.** systemd `.path` units fire one event per write
syscall. `sed -i` and `cp` each emit several. The watcher saw 5
rapid changes against the file and tried to syncconf 5 times in
a row, each time getting a partial / mid-edit copy.

**Fix.** Two parts:
- `.path` unit now only watches `/etc/hoberadius/wg-peers.d/`
  (the directory the container owns). The root-owned wg0.conf
  is no longer watched at all — root edits there are rare, and
  the right reload after one is `systemctl restart wg-quick@wg0`,
  not our path-unit.
- `init-wg-reloader` step 7 explicitly tears down `wg0` and
  re-runs `wg-quick up wg0` to guarantee a clean state even
  after a prior bad reload (defensive idempotency).

**Prevention.**
- A `.path` unit should watch the narrowest possible scope.
- Any "watch + react" pipeline must be **idempotent** —
  running the reaction N times on the same input must produce
  the same result as one run.
- Container-side: do atomic writes (write to tempfile + rename)
  so the watcher only sees one "file replaced" event, never a
  partial state. Our `wg_peer_manager.provision_peer` uses
  `staging.write_text(...) ; staging.replace(target)` exactly
  for this reason.

---

## 7) Operator confused which NAS id to dashboard

**Symptom.** Opening `/admin/radius/mt/1/dashboard` and
`/admin/radius/mt/7/dashboard` returned 404. The operator had to
discover that only `id=5` was active.

**Root cause.** All other NAS rows had `deleted_at` set to a
non-NULL value. `_load_nas` correctly excludes soft-deleted rows.
There was no UI surface listing only the active rows by id.

**Fix.** Commit `e8b0d5d` (L5) added the **Operations Center**
at `/admin/radius/mt/operations` — a single page that lists every
active NAS with a deep-link to its dashboard. Commit `04dcd2c`
(L7) updated the sidebar to point operators here by default.

**Prevention.**
- Whenever a soft-deleted row exists, there must be a single
  page that shows the active set. Don't expose "you can go to
  /resource/<id> if you somehow know <id>" as the only entry
  point.
- L5's `enumerate(rows, start=1)` gives operators a sequential
  display number — the DB id stays stable in URLs but the human
  never has to remember it.

---

## 8) MikroTik EOF — empty `api_user` in the NAS row

**Symptom.** Dashboard for `mt-vpn` (id=5) showed
🔴 "الراوتر غير قابل للوصول" with `EOF — الراوتر أغلق الاتصال`.
TCP socket connected, then the router immediately closed the
session.

**Root cause.** The legacy [devices_test](../app/radius/routes/devices.py)
endpoint only does a 2 s TCP reachability check — it does NOT
log into the API. So an NAS row could pass "reachable" while
having an empty `api_user`. The K9 dashboard, which does a real
API login, failed at the very first MT-protocol step.

Diagnostic command on the host:

```bash
docker exec hoberadius python -c "
nas = dict(db().execute('SELECT * FROM nas_devices WHERE id=5').fetchone())
print('user:', nas.get('api_user'))    # printed empty
"
```

**Fix.** Operator opened `/admin/radius/devices/5/edit` and
filled `api_user` + `api_password`. No code change — the row was
half-filled.

**Prevention.**
- The legacy `/admin/radius/devices/new` form must validate
  `api_user` is non-empty before save. (Already does in the
  Phase L wizard — which we now prefer.)
- The Phase L wizard generates `api_user` / `api_password` per
  router so this class of bug is structurally impossible for new
  routers — there's no manual entry path for those fields.
- For pre-existing rows: add a one-shot data integrity script
  that flags NAS rows with empty `api_user` / `api_password` /
  `secret`.

---

## 9) MikroTik EOF — stale `/ip service set api address=...`

**Symptom.** Wizard-provisioned router (id=13, 10.10.0.3) showed
🔴 with the same `EOF` symptom even though every credential was
correct.

**Root cause.** The router had an existing
`/ip service set api address=10.10.0.2/32` from a prior manual
test. The wizard's RouterOS script enabled the API and set the
port but did NOT touch the address restriction. So the router
accepted TCP on 8728 but only allowed actual sessions from
10.10.0.2 — and HobeRadius dials from 10.10.0.1.

**Fix.** Commit `aff1955` (M3) added an optional
`api_allowed_address` parameter to `render_routeros_script`. The
wizard now passes the WG subnet (`10.10.0.0/24`) for every VPN
router, emitting an explicit:

    /ip service set api address=10.10.0.0/24

right after the `set api disabled=no` line. This overrides any
stale restriction and locks the API down to the tunnel subnet
at the same time (defense in depth).

**Prevention.**
- Any auto-generated script that configures a service must set
  **every** relevant attribute explicitly, never assume a
  default. "Enable + port" without "address" inherits whatever
  was there.
- For RouterOS specifically, the wizard now ships a complete
  service-config block. Future additions (e.g. lock down
  `/ip service set winbox address=`) follow the same pattern.

---

## 10) FreeRADIUS rejected every NAS — `unknown client`

**Symptom (worst log line of the session):**

    Error: Ignoring request to auth address * port 1812
    bound to server default from unknown client 10.10.0.3

FreeRADIUS received the packets from the router (TCP / UDP
worked, secrets matched) but didn't recognize the source IP.

**Root cause — three layers, all of which had to be fixed:**

1. **`read_clients = no` in `mods-enabled/sql`.** The config had
   a stale Arabic comment dating from before migration 010 was
   applied: "the `nas` table doesn't exist yet, so keep
   read_clients off." Migration 010 had since been applied but
   the flag was never flipped back.
2. **Default `client_query` expects 9 columns.** When we first
   enabled `read_clients = yes`, our query was
   `SELECT nasname, shortname, type, secret FROM nas` (4 cols).
   FreeRADIUS 3.2.x silently logged
   `Warning: SELECT returned too few fields. Please do not edit
   'client_query'` and dropped every client row.
3. **The wizard never wrote to the `nas` table.** Only
   `nas_devices` (the app's view). FreeRADIUS reads from `nas`,
   so even with read_clients on, the wizard-provisioned routers
   were invisible.

**Fix.** Commits `d3e3e77` + `a8184c9` (M4):
- `read_clients = yes`, `client_table = nas`.
- `client_query` returns all 9 expected columns, aliasing our
  `require_ma` to FR's `require_message_authenticator`.
- `mt_setup_create` now DELETE+INSERTs into `nas` in the same
  transaction that writes to `nas_devices`. The DELETE first
  step keeps the operation idempotent.

End-to-end validation: live Hotspot user `ahmad` from a real
phone successfully logged in 3 minutes after the M4 deploy.

**Prevention.**
- When a stale config flag is kept "off until X is done", commit
  a follow-up that flips it back **immediately after X is done**.
  Don't leave the toggle for someone else to find.
- Any feature that creates a NAS / client / device must write to
  **every** table the runtime reads — survey them at design time,
  not after the first auth packet gets ignored.
- For FreeRADIUS specifically: always use the full default
  `client_query` and alias DB columns to match what FR expects.
  Truncating the column list is a footgun.

---

## 11) WireGuard subnet too small (`/30`)

**Symptom.** Bootstrap state had `Address = 10.10.0.1/30` on
wg0 — only 2 usable IPs (server + one router). Adding a second
router via the wizard would have errored "subnet exhausted".

**Root cause.** Initial manual setup used `/30` (sufficient for
the first test peer mt-vpn at 10.10.0.2 and no one else).

**Fix.** Operator changed `Address = 10.10.0.1/24` in wg0.conf
and on the live interface (`ip address change` with the right
sequence). 254 usable hosts now.

**Prevention.**
- `deploy/.env.example` documents `HOBERADIUS_WG_SUBNET=10.10.0.0/24`
  as the default.
- Future `deploy.sh bootstrap-wg` command (suggested but not yet
  implemented) should write a `/24` wg0.conf from scratch.
- The Operations Center should surface "X / 254 IPs used" so
  operators know when they're approaching exhaustion.

---

## 12) Slugifier strips non-ASCII names

**Symptom (cosmetic).** Router named "سي سي ار تجريب 2" produced
a peer file at `/etc/hoberadius/wg-peers.d/2.conf` — only the
digit survived the slugifier. Wizard worked end-to-end, but the
filename is unhelpful for operators inspecting peers.d on the
host.

**Root cause.** `_slugify_router_name` keeps only
`[A-Za-z0-9._-]`, dropping every Arabic / non-Latin char.

**Fix.** Future enhancement (not in any commit yet): fall back
to `nas-<row_id>.conf` when the slug ends up too short or has
no Latin chars. Track as a polish item.

**Prevention.**
- Slugifier for cross-language apps should always have a numeric
  ID fallback.
- Document the slug → filename mapping in the wizard's "What
  gets generated?" aside so operators understand the file naming.

---

# Pre-Commercial-Release Checklist

Before shipping a new VPS install to a paying subscriber, walk
this list. Each item maps to one of the postmortems above.

## 1. Code-level sanity (catches issues #1, #2)

- [ ] `python -m pytest tests/test_mt_provisioner.py tests/test_mt_setup_routes.py tests/test_wg_peer_manager.py tests/test_mikrotik_admin_client.py tests/test_api_mikrotik_control_k8.py tests/test_mt_dashboard_ui.py -q`
      → all green
- [ ] `python -m compileall app -q` → no syntax errors
- [ ] grep for `fail(` with mixed positional + kwarg `code=`:

      grep -rn 'fail("[^"]*", code=' app/

      → should be empty

## 2. Host-side prerequisites (catches issues #3 → #6)

- [ ] WireGuard installed: `which wg wg-quick`
- [ ] `wg0.conf` has `Address = 10.10.0.1/24` (NOT `/30`)
- [ ] `/etc/hoberadius/wg-peers.d/` exists, gid=999, mode 0775
- [ ] `/usr/local/bin/wg-reload.sh` present + executable
- [ ] `systemctl is-active wg-reload.path` → `active`
- [ ] `wg show wg0` shows non-zero ListenPort + a public key
- [ ] UFW / iptables allows UDP 51820 inbound

Run with one command:

    sudo bash /opt/hoberadius/deploy/deploy.sh init-wg-reloader

The script enforces all of the above and migrates any legacy
peers in wg0.conf to peers.d.

## 3. Container ↔ host bind-mount (catches issue #3)

- [ ] `docker exec hoberadius id hr` returns `uid=999 gid=999`
- [ ] `docker exec -u hr hoberadius touch /etc/hoberadius/wg-peers.d/.canary`
      succeeds + cleanup: `docker exec -u hr hoberadius rm /etc/hoberadius/wg-peers.d/.canary`

## 4. .env required for WG auto-provisioning (catches issue #10)

- [ ] `HOBERADIUS_WG_SERVER_PUBKEY` set (from
      `cat /etc/wireguard/server_public.key`)
- [ ] `HOBERADIUS_WG_SERVER_ENDPOINT` set to `<public-ip>:51820`
- [ ] `HOBERADIUS_WG_SUBNET` matches wg0.conf (default `10.10.0.0/24`)
- [ ] `HOBERADIUS_API_TOKENS` set in production (otherwise UI
      cannot call `/api/v1/*`)
- [ ] `HOBERADIUS_INTERNAL_SECRET` set (FreeRADIUS ↔ Flask)

## 5. FreeRADIUS configuration (catches issue #10)

- [ ] `read_clients = yes` in `deploy/freeradius/mods-enabled/sql`
- [ ] `client_query` returns all 9 columns:
      id, nasname, shortname, type, secret, server, community,
      description, require_message_authenticator
- [ ] `nas` table exists with all those columns (migration 010
      applied)
- [ ] After deploying, `docker logs hoberadius-freeradius` shows
      no `SELECT returned too few fields` warnings

## 6. End-to-end smoke test on the live VPS (catches issue #10)

Pretend you are a new subscriber. Do this in order:

1. SSH in, run `sudo bash /opt/hoberadius/deploy/deploy.sh upgrade`.
2. Open `http://<vps-ip>/admin/radius/mt/setup`.
3. Type a router name + pick RouterOS 7, submit.
4. Copy the generated script. Paste into the RouterOS Terminal
   on a real device.
5. Wait 30 seconds. The dashboard must turn 🟢 with live KPIs.
6. Connect a phone to the router's hotspot. Try logging in with
   any username.
7. `docker logs hoberadius-freeradius` MUST show
   `(N) Auth: ... (from client <router-name> ...)` —
   NOT `Ignoring request from unknown client`.

If step 7 prints `unknown client`, jump back to section 5 of
this checklist.

## 7. Operational hygiene

- [ ] Run `nas_devices` integrity check — no rows with empty
      `api_user` / `api_password` (issue #8):

      docker exec hoberadius python -c "
      from app.radius.db.connection import db
      for r in db().execute(\"SELECT id, name FROM nas_devices WHERE (api_user IS NULL OR api_user='') AND (deleted_at IS NULL OR deleted_at='')\").fetchall():
          print('BROKEN', r['id'], r['name'])
      "

- [ ] Confirm the legacy `clients.conf` doesn't shadow the
      dynamic clients with conflicting secrets:

      docker exec hoberadius-freeradius cat /etc/freeradius/clients.conf

      Remove or update any stale `client mt_…` blocks if the
      same IP exists in the `nas` table.

- [ ] WG subnet capacity: `wg show wg0 | grep peer | wc -l`
      compared to subnet size. /24 holds 253 routers; bump to
      /23 well before reaching capacity.

---

# Architectural Lessons (one paragraph each)

**Split control planes by trust level, not by convenience.**
Issue #3 surfaced because we tried to give the Flask container
read-write on the host's WG private key file. The right answer
was two directories with different perms; the wrong answer was
loosening the secret file's perms. When the container needs
"some" access to a sensitive area, create a sibling sub-tree it
owns exclusively.

**Don't use full-config commands for partial-config operations.**
Issue #5 (`wg syncconf` resetting `[Interface]`) is the
canonical example. The same pattern shows up in DB migrations
(don't `DROP + RECREATE` when you mean `ALTER`), in K8s manifests
(don't `kubectl apply` a partial spec), and in firewall rules
(don't replace the chain when you mean to insert a rule).

**Every "create resource" flow must write to every downstream
table.** Issue #10 broke because the wizard wrote to
`nas_devices` (the app's truth) but FreeRADIUS reads `nas` (the
RADIUS truth). At design time, list every consumer of the
resource and ensure the writer hits them all.

**Cosmetic bugs are not low-priority bugs — they erode
operator trust.** Issue #12 (Arabic name → `2.conf`) doesn't
break anything functional, but an operator opening peers.d on
the host sees a confusing file name and starts wondering what
else might be off. Fix the slugifier before the second beta
customer asks.

**Test the unhappy paths.** Most of the bugs above (especially
#1, #8, #9, #10) were latent because we only tested the green
path. Every endpoint should have at least one "missing required
field" and one "invalid auth" test before it ships.

---

# Where the fixes live in git

| Issue | Commit | What |
|---|---|---|
| #1  | `32700ba` | fix(K3+K4): correct fail() positional args |
| #2  | `50d6a4e` | L8: 3 s timeout + parallel /system/overview |
| #3  | `d319418` | M0c: peers.d split architecture |
| #4  | `5a484cb` | M0d: drop wg-quick strip from reload |
| #5  | `5f8f891` | M0e: wg set (not syncconf) for peer ops |
| #6  | M0c + M0d | systemd path-unit narrowing |
| #7  | `e8b0d5d` | L5: Operations Center page |
| #8  | (operational) | docs only — see prevention |
| #9  | `aff1955` | M3: API restricted to WG subnet in script |
| #10 | `d3e3e77` + `a8184c9` | M4: read_clients=yes + full client_query + nas sync |
| #11 | (operational) | `.env.example` documents /24 default |
| #12 | (pending) | future polish — slugifier nas-<id> fallback |

Every fix has tests; every test was green at the time of merge.
