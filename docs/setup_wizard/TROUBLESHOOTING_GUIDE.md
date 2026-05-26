# Setup Wizard Troubleshooting

Quick reference for common failures. For deep history of how
each one bit us and was fixed, see
[POSTMORTEM_V3_REBUILD_SESSION.md](./POSTMORTEM_V3_REBUILD_SESSION.md)
and [REMOTE_TUNNEL_POSTMORTEM.md](../network_policy_center/REMOTE_TUNNEL_POSTMORTEM.md).

---

## Common blocked states (paste-back diagnostics)

| Code | Meaning | Inspect |
|------|---------|---------|
| `feature_flag_disabled` | Live apply is intentionally off | `env \| grep HOBERADIUS_NPC_DISABLE_LIVE` |
| `dry_run_required` | Generate a dry-run before apply | — |
| `confirmation_required` | Paste the exact confirmation phrase | — |
| `probe_unavailable` | Use pasted output mode or configure read-only probes | — |
| `route_missing` | Inspect `/ip route print detail` on router | — |
| `radius_server_unreachable` | Confirm VPN reachability + UDP 1812/1813 | `/tool ping 10.10.0.1` from router |
| `vpn_not_handshaking` | First WG handshake never happened | `/interface wireguard peers print detail` |
| `wrong_public_endpoint` | UDP endpoint typo or NAT blocking | `nc -zvu <endpoint> 51820` from router |
| `firewall_blocking_udp` | ISP / upstream NAT blocks UDP 51820 | — |
| `radius_secret_mismatch` | Router and server have different secrets | `/radius print detail` |

Support bundles mask secrets and include the latest
diagnostics, operations, and snapshot summary.

---

## Symptom → most likely cause (newest first)

### v3 wizard "Next" button does nothing, no console errors

→ **Browser cache**. The new template loaded but old JS is
running. The version query string on the script tag should
prevent this; if you see `?v=...` matching the latest commit
in DevTools Network tab but JS still old, try Incognito.

Files: `app/templates/radius/setup_wizard_v3.html` —
look for `?v=YYYYMMDDx` on the `<script>` tag.

### `500 Internal Server Error` on `POST /admin/radius/setup-wizard-v3/runs`

→ **Migration not applied** (probably 076). Logs will show
`sqlite3.OperationalError: table setup_wizard_runs has no
column named state_json`. Run:

```bash
docker exec hoberadius python -c "
from app.radius.db import run_pending_migrations
n = run_pending_migrations()
print(f'applied {n} migrations')
"
```

If `n=0` and the column truly missing, the migration runner
is reading the wrong DB path — check `HOBERADIUS_DB_PATH`.

### RADIUS auth dropped: "Received packet from 172.18.0.1 ... from client docker"

→ **Docker bridge SNAT-ed the router's source IP.** The MT
router sends from 10.10.0.x (over WG), but the docker bridge's
`MASQUERADE -s 10.10.0.0/24 ! -o wg0` rule rewrites the
source to 172.18.0.1 before freeradius sees it. The fallback
`client docker_network` catch-all matches but its legacy
`secret=testing123` doesn't match the wizard's per-run
unique secret → drop.

**Permanent fix:** commit after issue #19 puts freeradius on
`network_mode: host` so it sees real source IPs. Combined
with `127.0.0.1:8000:8000` on hoberadius, freeradius's rest
module reaches the policy endpoint without docker DNS.

After `git pull && docker compose ... up -d --build`, a
fresh login should produce:
```
Auth: Login OK: [<user>] (from client router-<run> port 0)
```

NOT `from client docker`. If you still see "from client
docker" lines, double-check:
```bash
docker inspect hoberadius-freeradius --format '{{.HostConfig.NetworkMode}}'
```

Should print `host` — if it prints `default` or a bridge
name, the compose change didn't take effect (try
`--force-recreate freeradius`).

### `Failed to add duplicate client ...` after wizard run

→ **Wizard allocated an IP already used by a hardcoded
clients.conf entry.** Common pattern: legacy
`mt_vpn_10_10_0_2` block conflicts with a new wizard run
that got 10.10.0.2 from the allocator.

**Recovery (no rebuild):**
```bash
docker exec hoberadius-freeradius sed -i \
  '/^client mt_vpn_10_10_0_2 {/,/^}$/d' \
  /etc/freeradius/clients.conf
docker compose -f /opt/hoberadius/deploy/docker-compose.yml restart freeradius
```

**Permanent fix:** commit after issue #18 removed the
conflicting block from `deploy/freeradius/clients.conf`.
After `git pull && up -d --build freeradius` you won't hit
this again. Future wizard runs are authoritative for the
10.10.0.0/24 VPN range.

If you find other `client X { ipaddr=10.10.0.Y ... }` blocks
in clients.conf that overlap with the wizard's VPN range,
move them to a `wizard-run-<id>.conf` (or delete them and
re-run the wizard for that router).

### Subscriber login hangs with "no response" / `(0) No reply from server`

→ **FreeRADIUS is in a crash loop.** Check:

```bash
docker logs hoberadius-freeradius --tail 20
```

If you see `[entrypoint] freeradius exited with code 0, restarting in 1s`
repeating every second, run:

```bash
docker exec hoberadius-freeradius freeradius -X 2>&1 | head -40
```

The actual error is usually:

```
Unable to open file "/data/freeradius-clients-wizard/*.conf":
No such file or directory
```

**Root cause:** FreeRADIUS 3.x `$INCLUDE` does NOT support
wildcards — `*.conf` is interpreted as a literal filename.
The fix is to use the directory form (trailing slash):

```
$INCLUDE /data/freeradius-clients-wizard/
```

**Recovery (without rebuild):**
```bash
docker exec hoberadius-freeradius sed -i \
  's|\$INCLUDE /data/freeradius-clients-wizard/\*\.conf|$INCLUDE /data/freeradius-clients-wizard/|' \
  /etc/freeradius/clients.conf
docker compose -f /opt/hoberadius/deploy/docker-compose.yml restart freeradius
```

**Permanent fix:** commit after issue #17 already updated
`deploy/freeradius/clients.conf` to use the directory form.
After `git pull && up -d --build freeradius` you won't hit
this again.

### `/import` line vanishes after `/tool fetch` succeeded

→ **`/tool fetch` progress output ate the next pasted
line.** When fetch shows its multi-line status / downloaded
/ duration block, MikroTik Terminal consumes input from the
paste buffer the same way it would a keystroke. The
`/import` line pasted right after fetch gets silently
absorbed and never executes.

**Fix:** join both commands with a `;` on ONE line:

```
/tool fetch url="..." mode=http dst-path="hr-setup.rsc"; /import file-name="hr-setup.rsc"
```

The wizard renders this format by default. If you see two
separate lines in an older `?v=...` cache, hard-refresh.

### Lines silently missing from pasted script (e.g. `/user add` or `/radius add` never executed)

→ **MikroTik Terminal buffer overflow on long lines.** When
the pasted text contains lines longer than ~200 characters
(the `/user add ...` and `/radius add ...` lines easily go
over 200), the terminal silently drops them. The empty prompt
right where the line should be is the tell — like:

```
> /user remove [find where name="hr-api-37"]
>                                            ← MISSING /user add
> /ip service enable api
```

**Fix.** Use the green "🚀 fetch &amp; import" command pair
shown in the wizard's Step 3 result card. It's two short
lines that download the .rsc file and import it
atomically — no paste-length limit applies.

If you absolutely must paste raw (no internet on router yet),
break long commands into multiple shorter ones, e.g.:

```
/user add name=X password=Y group=full
/user set [find where name=X] comment="..."
```

instead of one long line.

### `HOBERADIUS_PUBLIC_KEY=` is empty in MikroTik output

→ **`:local` doesn't survive pasted line boundaries** in
RouterOS Terminal. Each pasted line runs in its own
interactive scope, so a local variable set on one line is
gone by the next. Fixed in `af32e69` by inlining the read
into the `:put` statement using the `.` concat operator.

If you see this again, look at the script renderer in
`app/radius/services/setup_wizard_v3.py::_render_unified_script`
and make sure no `:local X` is referenced on a different
line as `$X`.

### Step 3 toast: `تعذّر توليد سكربت الربط`

→ **Response key mismatch**. The route returns the script
under key `script`, but older JS reads `script_body` (or
vice versa). Fixed in `362115e` to read both keys.

Check `app/static/js/setup_wizard_v3.js::generateVpnScript`
and ensure it reads `data.script || data.script_body`.

### Step 1 → 2 toast: `router_type must be hotspot/pppoe/mixed`

→ **Radio value mismatch**. The template's `<input
value="...">` doesn't match the service's whitelist. Fixed
in `6650a53` (changed `hybrid` → `mixed`).

Whitelist lives in `setup_wizard_v3.py::start_new_run`
(search `must be`). The radio values in
`setup_wizard_v3.html` must match exactly.

### `cannot generate script from state collecting`

→ **State transition skipped.** The wizard tried to call
`generate-script` before `router-info` was submitted. The
service requires `state in (PLANNING, AWAITING_HANDSHAKE)`.

In the JS, ensure `submitRouterInfo()` resolves before
allowing the operator to click "توليد سكربت الربط".

### Fleet page TTL countdown shows but doesn't tick

→ JS computes the countdown on initial render. Refresh the
fleet page or click "تحديث" to re-render. Future
improvement: setInterval re-render.

### Emergency reset button does nothing

→ Browser cache (same fix as the Next button). The
`emergency-reset/preview` GET should fire when you click —
check the Network tab. If no request, hard refresh or open
Incognito.

### Wizard reserves IP but operator abandons → ghost reservation

→ **TTL reclaimer takes care of this automatically** every
5 minutes. The row gets `lifecycle_state='abandoned'`, IP
released, peer file deleted, audit row written. To force
immediate cleanup:

```
POST /admin/radius/setup-wizard/fleet/reclaim-expired
```

or click "🧹 تنظيف المنتهية" on the fleet page.

---

## Deploy gotchas

### Code change didn't take effect after `restart`

→ **Use `--build`, not `restart`** — ALWAYS — for any change
to files under `app/` (Python, templates, JS, CSS). The
running image was built before the change. `restart` reuses
the same image; only `--build` rebuilds with the new code:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

**Quick test** to confirm the new code is actually in the
container:

```bash
# pick a unique string from your latest commit
docker exec hoberadius grep -c "SOME_NEW_STRING" /app/app/...
```

If the count is `0`, the container has stale code.

For asset-only changes a bind-mount on `app/templates/` and
`app/static/` in `docker-compose.yml` would let `restart`
work, but be aware: production builds typically copy code
into the image rather than bind-mount.

### `git pull` says "would clobber local changes"

→ Operator hand-edited a config file. Stash or check out the
pristine version:

```bash
git stash
git pull
git stash pop   # resolve conflicts if any
```

Never `git pull --force` without reviewing the local diff.

### After `--build`, container starts but health-check fails

→ Migration probably failed mid-way. Check:

```bash
docker logs hoberadius --tail 100 | grep -E "migration|ERROR"
```

If a migration is half-applied, the DB might be in an
inconsistent state. Worst case: restore from backup
(`/opt/hoberadius/instance/*.db.bak`).

---

## Lifecycle states (router_provisioning_registry)

```
reserved → script_generated → waiting_router_key
                              ↓
                              router_key_received → peer_pending
                                                    ↓
                                                    peer_ready → vpn_verified
                                                                 ↓
                                                                 radius_pending → radius_verified
                                                                                  ↓
                                                                                  api_pending → api_verified
                                                                                                ↓
                                                                                                fully_onboarded → retired

Any state can transition to:  failed, retired
Failed can recover to:        script_generated, waiting_router_key, peer_pending, peer_ready, retired
```

**Permanent states** (TTL reclaimer never touches):
`vpn_verified`, `radius_pending`, `api_pending`,
`api_verified`, `fully_onboarded`, `retired`.

---

## Where to look first when something goes wrong

| Layer | Path |
|-------|------|
| Browser DevTools | F12 → Console + Network tabs |
| Wizard route handlers | `app/radius/routes/setup_wizard_v3.py` |
| Wizard state machine | `app/radius/services/setup_wizard_v3.py` |
| Phase planners | `app/radius/services/setup_wizard_*_phase_planner.py` |
| Diagnostics catalogue | `app/radius/services/setup_wizard_diagnostics.py` |
| Container logs | `docker logs hoberadius --tail 100` |
| WG peers state | `ls /etc/hoberadius/wg-peers.d/` |
| Audit trail | `audit_log` table — `action LIKE 'setup_wizard_%'` |
| Lifecycle events | `router_lifecycle_events` table |

---

## Postmortems index

- [V3 Rebuild Session](./POSTMORTEM_V3_REBUILD_SESSION.md) —
  this session (SW1-SW7, TTL reclaimer, emergency reset,
  multi-stage UI).
- [Remote Tunnel](../network_policy_center/REMOTE_TUNNEL_POSTMORTEM.md) —
  the thirteen things that went wrong on first remote-access
  deployment.
- [Phase K/L/M](../radius/POSTMORTEM_PHASE_K_L_M.md) —
  earlier MikroTik control center / WireGuard auto-provisioning
  build-out.
