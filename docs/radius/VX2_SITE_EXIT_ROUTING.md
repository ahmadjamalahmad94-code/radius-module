# VX2 — Selected Sites VPS Exit Routing

> A surgical, destination-based routing feature: send specific
> domains / IPs / CIDRs out through a VPS WireGuard tunnel
> while every other destination continues using the original
> WAN.
>
> **NOT a full-network VPN.** **NEVER modifies the main
> routing table.** **NEVER routes all traffic.**

---

## What VX2 does

1. Stores VPS exit-node metadata (NO private keys).
2. Stores per-policy lists of destination targets
   (domains / IPv4 / CIDR).
3. Classifies pasted seed files into 7 named groups so the
   operator can include / exclude classes of destinations
   independently.
4. Generates an **idempotent, RouterOS v7** forward script
   that:
   - creates a dedicated routing table per policy,
   - installs a default route INSIDE that table only,
   - populates an address list with the chosen destinations,
   - installs one mangle `mark-routing` rule that picks the
     custom table for traffic to that address list,
   - optionally adds an output-chain mangle for the router's
     own probes,
   - optionally adds a failsafe `drop` rule that prevents
     selected traffic from leaking via WAN when the VPS
     tunnel is down (requires a known WAN interface-list),
   - cleanly cleans up its own prior managed entries before
     re-adding (re-runs are safe).
5. Generates a matching rollback script that removes only
   the managed-prefix rows.
6. Runs a multi-source safety check (permissions, router
   health, backup freshness, VPS sanity, plan correctness)
   before any wire I/O.
7. Audits every preview / apply attempt with success / partial
   / failed / blocked status.

## What VX2 does NOT do

- Does **NOT** route all internet traffic through the VPS.
- Does **NOT** create or modify a default route in the main
  routing table.
- Does **NOT** auto-apply imported targets — operator must
  pick groups, hit "preview", then explicitly confirm and
  apply.
- Does **NOT** store WireGuard private keys.
- Does **NOT** automatically provision a VPS.
- Does **NOT** automatically generate WireGuard keys.
- Does **NOT** support per-subscriber routing (every user
  on the router shares the policy).
- Does **NOT** offer a one-click rollback button — the
  rollback script is displayed for the operator to review
  and run manually via `/system/script/run` if needed.
- Does **NOT** modify FastTrack rules (FastTrack tuning is
  tenant-wide; see warning below).
- Does **NOT** support IPv6 in this phase.

---

## Supported inputs

| Type | Example | Notes |
|---|---|---|
| Domain | `speedtest.net`, `sub.example.com` | RFC-1035-ish: 1-63 char ASCII labels, 2+ labels, TLD with letter |
| IPv4 | `1.1.1.1` | Public address space only by default |
| CIDR | `1.2.3.0/24` | `1.2.3.4/24` normalises to the /24 |

## Rejected inputs

| Input | Why |
|---|---|
| `0.0.0.0/0` (any form) | Would route everything through VPS — refused unconditionally |
| `*.example.com` | Use the `include_subdomains` flag instead |
| `https://x` or `x/path` | URLs with scheme / path / query / fragment / whitespace are refused — paste hostname only |
| `localhost` (single-label) | VX2 routes only FQDNs |
| `10.0.0.0/8`, `192.168.x.y`, `127.x`, `169.254.x`, `224/4`, `240/4`, `0/8` | Private / reserved / multicast / unspecified — only `advanced_mode=True` opts in |
| `2001:db8::1` | IPv6 unsupported in VX2 |

---

## MikroTik requirements

- **RouterOS v7+** (the script uses `/routing table add ... fib`
  syntax that v6 lacks).
- A WireGuard tunnel between this router and the VPS is
  already configured and stable.
- Routing-table support (built-in on v7).
- `/ip/firewall/address-list`, `/ip/firewall/mangle`,
  `/ip/firewall/filter` available (every supported v7 model
  ships these).

## VPS requirements

- Public IP (operator records it in the platform).
- A WireGuard endpoint listening on a UDP port the router
  can reach.
- Linux `ip rule` / `iptables -t nat -A POSTROUTING -o <wan>
  -j MASQUERADE` (or equivalent) configured so packets that
  arrive over the WireGuard interface egress through the VPS
  WAN with the VPS public IP as source.
- IP forwarding enabled (`net.ipv4.ip_forward=1`).

VX2 does **NOT** configure the VPS side — only the MikroTik
side.

---

## Fail modes

| Mode | Behaviour | When to use |
|---|---|---|
| `block_when_vps_down` | If a known WAN interface-list is supplied, generates a `firewall filter` drop rule that drops traffic destined for the managed list when it tries to leave via WAN. Selected sites become unreachable instead of leaking the original IP. | **Default — recommended.** Choose this when the operator must NEVER expose the real IP. |
| `fallback_to_wan` | No drop rule. If the VPS tunnel goes down, the kernel routes via the main table → traffic exits via WAN → the original public IP is used. | Only when continued connectivity is more important than IP-anonymity. |

If `block_when_vps_down` is selected but no WAN interface-list
is supplied to the planner, the planner emits a warning AND
skips the drop rule — the operator must configure the WAN
interface-list and re-plan, or accept the leak risk.

---

## FastTrack warning

MikroTik **FastTrack** is a connection-tracking optimisation
that short-circuits established connections past the mangle
table. **A connection that gets fast-tracked BEFORE this
policy's mangle rule sees it will never receive the routing
mark — and will exit via the main WAN instead of the VPS.**

VX2 does **NOT** modify FastTrack automatically. The advisory
is surfaced in three places:

1. In `plan.warnings` on every generated plan.
2. In `safety.fasttrack_warning` on every safety evaluation.
3. As a yellow banner above the preview pane in the UI.

**Operator action**: in `/ip/firewall/filter`, either exclude
the VX2 address list from the FastTrack rule, or place the
VX2 mangle rule before the FastTrack rule.

## DNS / subdomain limitations

- The platform stores `include_subdomains=True` as a flag on
  each domain target, but the address list itself only
  contains the literal hostname (plus optionally `www.<host>`
  for root domains).
- Subdomain coverage requires the router to resolve DNS for
  clients (so the static-DNS helper populates the address
  list from the responses). When clients use external DNS
  (Google `8.8.8.8`, Cloudflare `1.1.1.1`, DoH, etc.), VX2
  has **no visibility** into the subdomain queries — coverage
  for `sub.example.com` is not guaranteed unless explicitly
  added as its own target.

## CDN limitations

Large brands sit on a CDN with hundreds of IP addresses and
constantly-rotating DNS. Putting `google.com` in the address
list adds the resolved IP at lookup time, but a second client
that resolves a moment later may hit a different IP that
isn't in the list. The `general_probe_sites` group is
disabled by default for this reason.

---

## Security model

1. **Permissions**: six new entries in `mt_permissions.ALL_PERMISSIONS`:
   - `site_exit.view` — read the page
   - `site_exit.manage` — create / edit policies and targets
   - `site_exit.preview` — render preview + scripts
   - `site_exit.apply` — execute on the router
   - `site_exit.override_backup_warning` — apply when no fresh backup exists
   - `site_exit.enable_risky_groups` — include `vpn_provider_pages` / `general_probe_sites` / `manual_review`

   `mikrotik.admin` implies only the **read-side** site-exit
   perms (view / manage / preview). Apply, override, and risky
   groups must be granted explicitly to the role.

2. **Backup expectations**: apply is **blocked** when the
   router has no recent backup, unless the operator holds
   `site_exit.override_backup_warning` AND explicitly ticks
   the override checkbox on apply.

3. **No secrets in the data path**:
   - `vps_exit_nodes` schema has no `private_key` column
     (proven by `test_no_private_key_column_anywhere`).
   - `vps_exit_nodes_repo.update()` allow-lists writable
     columns — a stray `private_key=...` kwarg is silently
     dropped.
   - `site_exit_scripts_repo.record()` refuses to store any
     body containing `private-key=`, `PrivateKey =`,
     `private_key=`, or `BEGIN PRIVATE KEY` (raises
     `SecretInScriptError`).
   - The renderer enforces the same tripwires before emitting
     bytes (`RenderSafetyError`).
   - Audit payloads carry the script hash + safety summary,
     never the script body in plain-text and never the VPS
     credentials.

4. **Cleanup / rollback only touch managed comments**: the
   script uses RouterOS regex `^HOBE_VX2_SITE_EXIT:<policy_id>:`
   — **anchored** with `^`, **terminated** with `:`. Unmanaged
   rules whose comment merely contains the tag are safe.

5. **Apply path NEVER calls `/remove` via the API**: only
   structured `add` operations cross the wire. Removes use the
   `[find comment~"..."]` script syntax which is not API-
   addressable — the operator must run the rollback script
   manually if needed. This eliminates the "broad-prefix
   remove" attack vector entirely.

---

## Audit behaviour

Every action emits a row in the existing audit log with
`target_type="site_exit_policy"`:

| Action | Severity | Result statuses |
|---|---|---|
| `site_exit.apply_attempted` | info / warning | `started`, `blocked` |
| `site_exit.apply_succeeded` | info | `success` |
| `site_exit.apply_failed` | critical | `failed`, `partial` |

Payload always includes: `policy_id`, `target_count`,
`fail_mode`, `safety` summary, `script_hash`,
`script_version_id`. Never includes WireGuard private keys,
the wire credentials, or the script body verbatim.

## Recovery behaviour

A failed / partial apply leaves the `site_exit_deployments`
row in `status=failed` with `last_error` populated, and
records a critical-severity `site_exit.apply_failed` audit
row. The standard Phase O recovery page
(`/admin/radius/recovery/<audit_id>`) renders an ordered
recovery checklist + nearest-backup card for that audit row
exactly as it does for any other partial/failed event.

The rollback script (always displayed in the UI even after
a failure) is the right tool to restore the router to its
prior managed state.

---

## Manual QA checklist

Run these on a test router, ideally one whose loss of
connectivity does not affect production users.

1. Open `/admin/radius/mt/<id>/site-exit` as a super admin
   → page renders with the policy form (no policy yet).
2. Open the same URL as an unauthorised admin → redirected
   to login or 403 depending on the auth state.
3. Open with `<id>=99999` → 404.
4. Create a VPS exit node (you'll need to do this from a
   site-exit-managing admin once that surface is wired —
   for now seed via SQL or REPL).
5. Create a policy.
6. Paste a seed file shaped like
   `add address=speedtest.net list=speedtest` (multiple
   lines) and click "تحليل" → import summary shows total /
   accepted / duplicates / invalid / manual_review counts.
7. Confirm groups appear with correct counts; `speedtest_measurement`,
   `public_ip_checkers`, `raw_ip_targets` are **checked**;
   `vpn_provider_pages`, `general_probe_sites`,
   `manual_review` are **unchecked**.
8. Save selected targets → flash message confirms inserted /
   updated counts.
9. Enter `WAN` as the WAN interface-list (matching your
   router's actual interface-list name).
10. Click "توليد معاينة" → forward + rollback scripts render.
11. Verify:
    - the route line carries `routing-table=HOBE_VX2_<id>`,
    - no `0.0.0.0/0` outside the custom table,
    - every managed line ends with `comment=HOBE_VX2_SITE_EXIT:<id>:...`,
    - rollback uses `[find comment~"^HOBE_VX2_SITE_EXIT:<id>:"]`.
12. Verify the **FastTrack warning** banner is visible.
13. Verify the **backup warning** is visible if no recent
    backup exists.
14. Tick all five confirmation checkboxes (UI checklist) and
    set the WAN interface-list field, then click apply.
15. Audit log shows `site_exit.apply_attempted` then
    `site_exit.apply_succeeded` (or `_failed`).
16. `site_exit_deployments` row for this policy shows
    `status=applied`, `last_audit_id` set, `last_error=""`.
17. From a client behind the router, browse a managed
    destination → curl `https://api.ipify.org` from another
    tab inside that destination → exit IP should be the
    VPS public IP.
18. Browse any **unmanaged** destination (e.g.
    `https://duckduckgo.com`) → exit IP should still be the
    original WAN public IP.
19. Bring the WireGuard tunnel down on the VPS side.
    - `fail_mode=block_when_vps_down`: managed destinations
      become unreachable (no leak).
    - `fail_mode=fallback_to_wan`: managed destinations exit
      via WAN with the original IP (intentional).
20. On a forced failure (e.g. wrong WAN interface-list name),
    open the matching `/admin/radius/recovery/<audit_id>`
    page and confirm the recovery checklist appears.

---

## Known limitations (acknowledged, intentional)

- **Single VPS per policy.** Multi-VPS / load-balancing is
  out of scope.
- **No live VPS health probe.** The `vps_exit_nodes.last_health_status`
  column is operator-managed for now; a future ticket can wire
  a worker that pings the tunnel and updates the value.
- **No background worker.** Plan + apply are synchronous in
  the request — fine for small target lists, will need a job
  runner if a policy ever holds thousands of targets.
- **No automatic rollback button.** The rollback script is
  displayed for the operator to run manually. A guarded
  one-click rollback is a future ticket.
- **No per-subscriber routing.** All users behind the router
  share the policy.

## Recommended next phase

- VX3: Wire a periodic worker that probes each VPS exit node
  (WireGuard handshake age + reachability) and updates
  `last_health_status` automatically. The safety check already
  reads this column — populating it would tighten the apply
  gate.
- VX4: Add a guarded one-click rollback that calls
  `apply_commands` against the same `[find by managed prefix]`
  logic, with explicit confirmation + audit.
- VX5: Multi-VPS policy — pick a different exit node per
  group or per subscriber.

— Generated at the end of VX2, 2026-05-22.
