# Management-tunnel abuse prevention

Stops a customer from using the **management tunnel** (SSTP / PPTP / WireGuard)
to pass general/internet traffic — stealing the provider's bandwidth or
bypassing billing. Two layers, both with sane defaults, the rate configurable
from the panel (Settings → network → **حدّ سرعة نفق الإدارة (Mbps)**,
`HOBERADIUS_MGMT_TUNNEL_RATE_MBPS`, default **10**).

## Layer 1 — low bandwidth cap

A 10 Mbps cap is plenty for RADIUS / CoA / WinBox / router-API, but useless for
data passthrough.

- **SSTP / PPTP (accel-ppp):** in-band, no host script. The panel writes the cap
  into `/etc/accel-ppp.conf` `[shaper]` (`rate-limit=<kbit>/<kbit>`, HTB both
  ways) **and** provisions each `rtr-*` RADIUS account with
  `Filter-Id = "<kbit>/<kbit>"` (accel's shaper reads it per session). Applied
  automatically to new routers (`provision_tunnel`) and to existing ones on boot
  (`reconcile_rate_caps`). Changing the rate in the UI + re-running the accel
  installer regenerates the config.
- **WireGuard (`wg0`):** WG has **no per-peer RADIUS shaping**, so we cap the
  interface with `tc` HTB (egress to routers) + an ingress policer (traffic from
  routers). See the flag below.

## Layer 2 — management-only confinement (host firewall)

A dedicated iptables chain `HR-MGMT-CONFINE`, hooked into `FORWARD` **and**
`DOCKER-USER` (so it runs before Docker's FORWARD accept). It DROPS anything a
customer router tries to **route out through us**, while leaving every real
management path open:

| Path | How it stays allowed |
|---|---|
| RADIUS auth/acct/CoA (router → host :1812/1813/3799) | Terminates on the host = **INPUT** chain — this confinement only touches FORWARD, so it's never filtered. |
| Panel → router API (8728/8729), WinBox forward (8291), CoA to router (3799) | `-d <tunnel-subnet> → RETURN` (provider-initiated to the routers). |
| Replies to any of the above | `ESTABLISHED,RELATED → RETURN` (matched first). |
| **Customer routing internet traffic in** | `-s <tunnel-subnet> → DROP`. |

Covers both `10.50.0.0/24` (accel) and `10.10.0.0/24` (WireGuard).

## Install (host, root)

```bash
cd /opt/hoberadius/deploy/mgmt-confinement
sudo ./install-mgmt-confinement.sh        # reads the UI rate from the panel container
```

Idempotent — safe on every deploy. Make it boot-persistent the same way you
persist the accel/WinBox rules (e.g. a oneshot systemd unit or `iptables-save`).

Preview the exact commands without applying:

```bash
python3 confine_rules_gen.py rules
```

Uninstall: `python3 confine_rules_gen.py uninstall`.

## Verify (lab) — do this once on the live VPS

```bash
iptables -L HR-MGMT-CONFINE -n -v          # chain present, hit counters move
tc -s qdisc show dev wg0                    # htb + ingress qdiscs present
```

Then confirm management still works AND passthrough is blocked:

- ✅ RADIUS auth still succeeds (a router authenticates over the tunnel).
- ✅ Panel dashboard reads + WinBox forward + a CoA still work on a live router.
- ✅ From a test router, a default route via the tunnel → internet is **dropped**.
- ✅ A bulk transfer over the tunnel is capped at ~the configured Mbps.

## ⚠️ Flags (owner decisions / lab verification)

1. **WG cap is per-interface (aggregate), not per-peer.** `tc` on `wg0` caps the
   whole interface; all WG mgmt routers share it. That's fine for blocking
   passthrough (the firewall already drops it) and keeping mgmt traffic small,
   but it is **not** a per-router fair-share. Per-peer needs a `tc` HTB class +
   `u32` filter per `/32` — addable later if you run many WG routers.
2. **Host firewall is high-stakes.** The rules are designed fail-safe (only DROP
   in FORWARD; INPUT/RADIUS untouched; established + provider→router allowed
   first), and the logic is unit-tested (`tests/test_mgmt_confinement.py`), but
   iptables/tc can't run in CI. **Verify on the live box** with the checklist
   above before relying on it.
3. **Boot persistence is yours to wire** (systemd oneshot / iptables-save) — the
   installer applies rules to the running kernel; it does not install a unit.
4. **`tc` requires `sch_htb` + `cls_u32`** kernel modules (present on stock
   Ubuntu/Debian). If the WG host is minimal, install `iproute2` and ensure the
   modules load.
