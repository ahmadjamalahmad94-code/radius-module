"""mt_health — per-router risk-signal scanner.

Aggregates signals from existing admin-client readers (interfaces,
ip/addresses) into a structured report the diagnostics tab renders
as severity-tinted rows.

Each signal is one of three severities:
  - "critical": something is actively broken (bridge loop tripped,
                duplicate MAC inside a single L2 domain).
  - "warning":  configuration smell that doesn't break service now
                but invites incidents (overlapping subnets, high
                flap counter on a stable-looking interface).
  - "ok":       check ran and found nothing — shown so the operator
                sees the check happened rather than wondering if it
                silently skipped.

This service deliberately makes no new RouterOS calls beyond what
the K4 readers already cache; it reuses their cached output, so the
diagnostics tab is cheap to refresh.

Returns: { ok, signals: [Signal], summary: {critical, warning, ok} }
"""
from __future__ import annotations

import ipaddress
from typing import Any, Iterable, Mapping

from . import mikrotik_admin_client as mac


# Severity tokens — kept as strings (not an Enum) so the JSON
# envelope stays trivially serializable and the template can
# match them with a simple Jinja `==`.
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING  = "warning"
SEVERITY_OK       = "ok"


def _signal(kind: str, severity: str, message: str,
            evidence: Any = None) -> dict[str, Any]:
    """Build the per-row dict the UI renders. `evidence` is rendered
    behind an expander so the operator can see the underlying rows
    that produced the verdict."""
    return {
        "kind": kind,
        "severity": severity,
        "message": message,
        "evidence": evidence if evidence is not None else [],
    }


# ─── individual checks ──────────────────────────────────────────


def check_duplicate_macs(interfaces: list[dict]) -> dict[str, Any]:
    """Two physical interfaces with the same MAC almost always means
    the operator pasted a config or cloned a port wrong. Bridges,
    loopback, VLAN children, and dynamic interfaces legitimately
    share MACs with their parents — those families are filtered out
    so the check stays low-false-positive.
    """
    skip_types = {
        "bridge", "vlan", "bond", "vrrp", "lte", "wireguard",
        "vrf", "loopback", "ppp-out", "pppoe-out", "ovpn-in",
        "ovpn-out", "l2tp-in", "l2tp-out", "sstp-in", "sstp-out",
        "eoip-tunnel", "gre-tunnel", "ipip-tunnel",
    }
    by_mac: dict[str, list[str]] = {}
    for r in interfaces:
        mac_addr = (r.get("mac-address") or "").strip().lower()
        if not mac_addr or mac_addr == "00:00:00:00:00:00":
            continue
        if (r.get("type") or "").lower() in skip_types:
            continue
        by_mac.setdefault(mac_addr, []).append(r.get("name") or "?")
    dupes = {mac: names for mac, names in by_mac.items() if len(names) > 1}
    if not dupes:
        return _signal(
            "duplicate_macs", SEVERITY_OK,
            "لا توجد عناوين MAC مكرّرة على الواجهات الفيزيائية.",
        )
    evidence = [{"mac": mac, "interfaces": names}
                for mac, names in dupes.items()]
    return _signal(
        "duplicate_macs", SEVERITY_CRITICAL,
        f"تم رصد {len(dupes)} عنوان MAC مكرّر — تحقّق من الكونفغ.",
        evidence,
    )


def check_loop_protect(interfaces: list[dict]) -> dict[str, Any]:
    """RouterOS exposes `loop-protect-status=disable-on-loop` when
    its loop-protect actually tripped. If we see that on any port
    the L2 domain has a live loop right now — that's a critical
    finding."""
    tripped = []
    for r in interfaces:
        status = (r.get("loop-protect-status") or "").strip().lower()
        if status == "disable-on-loop":
            tripped.append(r.get("name") or "?")
    if not tripped:
        return _signal(
            "loop_protect", SEVERITY_OK,
            "لا يوجد loop-protect مفعّل بسبب اكتشاف حلقة.",
        )
    return _signal(
        "loop_protect", SEVERITY_CRITICAL,
        f"اكتُشفت حلقة على {len(tripped)} واجهة — RouterOS أوقفها تلقائيًا.",
        [{"interface": n} for n in tripped],
    )


def check_subnet_overlap(addresses: list[dict]) -> dict[str, Any]:
    """Flag the SAME IP range configured more than once on the SAME
    interface — a genuine, redundant duplicate the operator should clean
    up.

    The same range on *different* interfaces is **normal** (separate L2
    segments behind separate ports) and is NOT a problem — RouterOS
    routes each interface independently. The previous version warned on
    cross-interface overlap, which was a false positive (e.g.
    192.168.15.1/24 on ether4 + 192.168.15.254/24 on ether6 is fine).
    Now we only surface a real duplicate: the same network appearing
    twice (or more) on one interface."""
    parsed: list[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network,
                       str, str]] = []
    for r in addresses:
        addr_raw = (r.get("address") or "").strip()
        iface    = (r.get("interface") or "?").strip()
        if not addr_raw:
            continue
        try:
            net = ipaddress.ip_interface(addr_raw).network
        except (ValueError, TypeError):
            continue
        parsed.append((net, iface, addr_raw))

    # Group the configured addresses by (network, interface). Any group
    # with more than one address row is the same range added twice on the
    # same interface — the only real misconfiguration here.
    groups: dict[tuple[str, str], dict] = {}
    for net, iface, raw in parsed:
        key = (str(net), iface)
        g = groups.setdefault(
            key, {"interface": iface, "network": str(net), "addresses": []})
        g["addresses"].append(raw)

    dups = [g for g in groups.values() if len(g["addresses"]) > 1]
    if not dups:
        return _signal(
            "subnet_overlap", SEVERITY_OK,
            "لا توجد نطاقات IP مكرّرة على نفس الواجهة.",
        )
    return _signal(
        "subnet_overlap", SEVERITY_WARNING,
        f"{len(dups)} نطاق مكرّر على نفس الواجهة "
        "(نفس المدى مُضاف أكثر من مرّة لواجهة واحدة).",
        dups,
    )


def check_interface_flapping(interfaces: list[dict]) -> dict[str, Any]:
    """RouterOS exposes `link-downs` (counter) per interface — a
    high value on a port the operator considers stable is the
    classic flap signal. Flag any interface with > 10 link-downs
    that's currently running. Skip dynamic + tunnel families."""
    skip_types = {"loopback", "wireguard", "ppp-out", "pppoe-out",
                  "ovpn-out", "l2tp-out", "sstp-out"}
    flappers = []
    for r in interfaces:
        try:
            downs = int(r.get("link-downs") or "0")
        except (TypeError, ValueError):
            continue
        if downs <= 10:
            continue
        if (r.get("type") or "").lower() in skip_types:
            continue
        flappers.append({
            "name": r.get("name") or "?",
            "type": r.get("type") or "?",
            "link_downs": downs,
        })
    if not flappers:
        return _signal(
            "flapping", SEVERITY_OK,
            "لا توجد واجهات بعدّاد link-downs مرتفع.",
        )
    # Sort worst-first so the most-flapping port shows on top.
    flappers.sort(key=lambda f: -f["link_downs"])
    return _signal(
        "flapping", SEVERITY_WARNING,
        f"{len(flappers)} واجهة بعدّاد link-downs > 10.",
        flappers,
    )


# ─── public aggregator ─────────────────────────────────────────


def scan_router(nas: Mapping[str, Any]) -> dict[str, Any]:
    """Run every check against one router. Each reader is cached
    server-side already, so the cost is low even on rapid refresh.

    If a reader fails (router unreachable / API down) the report
    still comes back ok=True with an empty signals list and the
    fetch error surfaced in `fetch_errors` — the UI then knows to
    paint "couldn't scan" instead of "all green."
    """
    iface_res = mac.interface_list(nas)
    addr_res  = mac.ip_addresses(nas)

    fetch_errors: list[str] = []
    interfaces = iface_res.data if iface_res.ok else []
    addresses  = addr_res.data  if addr_res.ok  else []
    if not iface_res.ok:
        fetch_errors.append("interfaces: " + (iface_res.error or "fail"))
    if not addr_res.ok:
        fetch_errors.append("addresses: " + (addr_res.error or "fail"))

    signals = [
        check_duplicate_macs(interfaces),
        check_loop_protect(interfaces),
        check_subnet_overlap(addresses),
        check_interface_flapping(interfaces),
    ]
    summary = {
        SEVERITY_CRITICAL: sum(1 for s in signals
                                if s["severity"] == SEVERITY_CRITICAL),
        SEVERITY_WARNING:  sum(1 for s in signals
                                if s["severity"] == SEVERITY_WARNING),
        SEVERITY_OK:       sum(1 for s in signals
                                if s["severity"] == SEVERITY_OK),
    }
    return {
        "ok": True,
        "signals": signals,
        "summary": summary,
        "fetch_errors": fetch_errors,
    }


__all__ = [
    "SEVERITY_CRITICAL",
    "SEVERITY_WARNING",
    "SEVERITY_OK",
    "check_duplicate_macs",
    "check_loop_protect",
    "check_subnet_overlap",
    "check_interface_flapping",
    "scan_router",
]
