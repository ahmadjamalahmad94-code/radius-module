#!/usr/bin/env python3
"""HobeRadius management-tunnel ABUSE PREVENTION — host rule generator.

Stdlib-only (like ``deploy/accel-ppp/accel_conf_gen.py``) so the installer can
run it on the host with a bare ``python3`` and no Flask. It emits the EXACT
host commands for two layers, so the security-critical logic is unit-testable
even though the commands themselves only run on the live VPS:

  Layer 1 — bandwidth cap (WireGuard): ``tc`` HTB on the wg interface. SSTP/PPTP
            is capped in-band via accel's shaper + RADIUS Filter-Id (see
            router_mgmt_tunnel / accel_conf_gen) — NOT here.

  Layer 2 — management-only confinement: a dedicated iptables chain
            ``HR-MGMT-CONFINE`` hooked into FORWARD (and DOCKER-USER, so it runs
            before Docker's own FORWARD accept). It DROPS any traffic the
            customer router tries to ROUTE OUT through us (the abuse) while
            leaving every real management path open:
              • replies to provider-initiated flows  (ESTABLISHED,RELATED)
              • provider → router  (panel API 8728/8729, WinBox forward 8291,
                CoA 3799) — matched by destination = a tunnel subnet
              • RADIUS auth/acct/CoA from the router TERMINATES on the host
                (INPUT chain), so it is never touched by this FORWARD confinement.

Design notes / safety:
  * We only ever DROP in the FORWARD path. INPUT (RADIUS to the host) is never
    filtered here, so auth/acct/CoA cannot break.
  * ESTABLISHED,RELATED is matched FIRST, so the return traffic of any
    provider-initiated management connection is always allowed.
  * Idempotent: the chain is flushed and the jumps are delete-then-(re)inserted,
    so re-running is a no-op.

Usage:
    confine_rules_gen.py rules            # print the iptables+tc commands
    confine_rules_gen.py install          # apply them (root)
    confine_rules_gen.py uninstall        # remove the chain + tc qdiscs
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import subprocess
import sys

# ── defaults (overridable by env / CLI) ─────────────────────────────────────
ENV_ACCEL_POOL = "HOBERADIUS_MGMT_TUNNEL_POOL"
ENV_WG_POOL = "HOBERADIUS_WG_MGMT_POOL"
ENV_WG_IFACE = "HOBERADIUS_WG_MGMT_IFACE"
ENV_RATE_MBPS = "HOBERADIUS_MGMT_TUNNEL_RATE_MBPS"

DEFAULT_ACCEL_POOL = "10.50.0.0/24"
DEFAULT_WG_POOL = "10.10.0.0/24"
DEFAULT_WG_IFACE = "wg0"
DEFAULT_RATE_MBPS = 10

CHAIN = "HR-MGMT-CONFINE"
# iptables comment tag so our jumps are findable/removable idempotently.
TAG = "hr-mgmt-confine"


def _valid_cidr(value: str) -> str:
    """Return canonical CIDR or raise — guards against command injection (only a
    real network can ever reach the iptables argv)."""
    return str(ipaddress.ip_network(str(value).strip(), strict=False))


def _iface(value: str) -> str:
    v = str(value or "").strip()
    if not v or not all(c.isalnum() or c in "-_." for c in v):
        raise ValueError(f"invalid interface name: {value!r}")
    return v


def _rate_mbps(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RATE_MBPS
    if n <= 0:
        return 0
    return min(n, 1000)


# ── Layer 2: the confinement chain (the safety-critical part) ───────────────
def confine_chain_rules(accel_pool: str, wg_pool: str) -> list[list[str]]:
    """The ordered rules INSIDE the HR-MGMT-CONFINE chain, as iptables argv
    (without the leading 'iptables'). Order matters — established first, then
    allow provider→router, then drop router-initiated forwarding."""
    a = _valid_cidr(accel_pool)
    w = _valid_cidr(wg_pool)
    return [
        # 1) replies to anything the provider opened (API/WinBox/CoA) — allow.
        ["-A", CHAIN, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED",
         "-j", "RETURN"],
        # 2) provider → router (panel API, WinBox stream-forward, CoA): allow.
        ["-A", CHAIN, "-d", a, "-j", "RETURN"],
        ["-A", CHAIN, "-d", w, "-j", "RETURN"],
        # 3) the abuse: router trying to route general traffic OUT through us.
        ["-A", CHAIN, "-s", a, "-j", "DROP"],
        ["-A", CHAIN, "-s", w, "-j", "DROP"],
        # 4) anything else: leave to the host/Docker rules.
        ["-A", CHAIN, "-j", "RETURN"],
    ]


def confine_install_cmds(accel_pool: str, wg_pool: str) -> list[list[str]]:
    """Full idempotent iptables program: (re)create the chain, fill it, and hook
    it into FORWARD + DOCKER-USER exactly once."""
    cmds: list[list[str]] = []
    # (re)create + flush the chain (idempotent)
    cmds.append(["iptables", "-N", CHAIN])          # may already exist
    cmds.append(["iptables", "-F", CHAIN])
    for r in confine_chain_rules(accel_pool, wg_pool):
        cmds.append(["iptables"] + r)
    # hook: delete any old jump then insert at the top of FORWARD + DOCKER-USER.
    for parent in ("FORWARD", "DOCKER-USER"):
        cmds.append(["iptables", "-D", parent, "-j", CHAIN])   # idempotent cleanup
        cmds.append(["iptables", "-I", parent, "1", "-j", CHAIN])
    return cmds


def confine_uninstall_cmds() -> list[list[str]]:
    cmds: list[list[str]] = []
    for parent in ("FORWARD", "DOCKER-USER"):
        cmds.append(["iptables", "-D", parent, "-j", CHAIN])
    cmds.append(["iptables", "-F", CHAIN])
    cmds.append(["iptables", "-X", CHAIN])
    return cmds


# ── Layer 1 (WireGuard only): tc HTB cap on the wg interface ────────────────
def wg_tc_cmds(iface: str, rate_mbps: int) -> list[list[str]]:
    """tc commands to cap the WireGuard mgmt interface. WG has no per-peer RADIUS
    shaping, so we cap the interface: egress via HTB (traffic to the routers) and
    ingress via a policer (traffic from the routers — the passthrough an abuser
    would push). 0 disables (returns no commands)."""
    if rate_mbps <= 0:
        return []
    i = _iface(iface)
    rate = f"{rate_mbps}mbit"
    return [
        # egress shaping (to routers): HTB, everything in one capped default class
        ["tc", "qdisc", "replace", "dev", i, "root", "handle", "1:", "htb",
         "default", "10"],
        ["tc", "class", "replace", "dev", i, "parent", "1:", "classid", "1:10",
         "htb", "rate", rate, "ceil", rate],
        # ingress policing (from routers): drop anything above the cap
        ["tc", "qdisc", "replace", "dev", i, "handle", "ffff:", "ingress"],
        ["tc", "filter", "replace", "dev", i, "parent", "ffff:", "protocol", "all",
         "prio", "1", "u32", "match", "u32", "0", "0", "police", "rate", rate,
         "burst", "100k", "drop", "flowid", ":1"],
    ]


def wg_tc_uninstall_cmds(iface: str) -> list[list[str]]:
    i = _iface(iface)
    return [
        ["tc", "qdisc", "del", "dev", i, "root"],
        ["tc", "qdisc", "del", "dev", i, "handle", "ffff:", "ingress"],
    ]


# ── orchestration ───────────────────────────────────────────────────────────
def _settings(args) -> dict:
    env = os.environ
    return {
        "accel_pool": _valid_cidr(getattr(args, "accel_pool", None)
                                  or env.get(ENV_ACCEL_POOL) or DEFAULT_ACCEL_POOL),
        "wg_pool": _valid_cidr(getattr(args, "wg_pool", None)
                               or env.get(ENV_WG_POOL) or DEFAULT_WG_POOL),
        "wg_iface": _iface(getattr(args, "wg_iface", None)
                           or env.get(ENV_WG_IFACE) or DEFAULT_WG_IFACE),
        "rate_mbps": _rate_mbps(getattr(args, "rate_mbps", None)
                                or env.get(ENV_RATE_MBPS) or DEFAULT_RATE_MBPS),
    }


def all_install_cmds(s: dict) -> list[list[str]]:
    return confine_install_cmds(s["accel_pool"], s["wg_pool"]) + \
        wg_tc_cmds(s["wg_iface"], s["rate_mbps"])


def _run(cmds: list[list[str]], *, allow_fail_substr: tuple = ()) -> int:
    rc = 0
    for cmd in cmds:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            print("ok:", " ".join(cmd))
        except FileNotFoundError:
            print("MISSING TOOL:", cmd[0], file=sys.stderr)
            rc = 1
        except subprocess.CalledProcessError as exc:
            # some commands are idempotent cleanups that are expected to fail
            # when there's nothing to remove (e.g. -D of an absent jump, -N of an
            # existing chain). Treat those as benign.
            blob = (exc.stderr or "") + (exc.stdout or "")
            benign = any(x in blob for x in (
                "No chain/target/match", "Chain already exists",
                "does a matching rule exist", "Cannot delete", "RTNETLINK"))
            if benign or any(s in " ".join(cmd) for s in allow_fail_substr):
                print("skip:", " ".join(cmd), "—", blob.strip()[:80])
            else:
                print("FAIL:", " ".join(cmd), "—", blob.strip()[:200],
                      file=sys.stderr)
                rc = 1
    return rc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="HobeRadius mgmt-tunnel confinement")
    p.add_argument("action", choices=["rules", "install", "uninstall"])
    p.add_argument("--accel-pool", dest="accel_pool")
    p.add_argument("--wg-pool", dest="wg_pool")
    p.add_argument("--wg-iface", dest="wg_iface")
    p.add_argument("--rate-mbps", dest="rate_mbps")
    args = p.parse_args(argv)
    s = _settings(args)

    if args.action == "rules":
        for cmd in all_install_cmds(s):
            print(" ".join(cmd))
        return 0
    if args.action == "install":
        return _run(all_install_cmds(s))
    if args.action == "uninstall":
        return _run(confine_uninstall_cmds() + wg_tc_uninstall_cmds(s["wg_iface"]))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
