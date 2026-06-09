"""device_health_planner — network calculation + idempotent MikroTik plan.

Pure, side-effect-free logic for «تتبع حالة الأجهزة»:

  • compute_network(ip, prefix, gateway_last_octet)
        192.168.15.10 /24 octet 254  →
        network_cidr=192.168.15.0/24, gateway_address=192.168.15.254/24
  • build_plan(...) — given the device parameters and (optionally) the live
    router state (addresses / bindings / netwatch lists), produce an
    idempotent plan: every intended MikroTik change marked `create`,
    `already_present`, or `planned` (no live state yet), plus warnings
    (e.g. same subnet on more than one interface → routing ambiguity).

No MikroTik I/O happens here — the caller fetches state via
device_health_mikrotik and hands the lists in. That keeps this unit-testable
with plain dicts and guarantees Phase 1/2 never mutate a router.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Mapping, Optional, Sequence


class NetworkCalcError(ValueError):
    """Raised for an invalid IP / prefix / gateway octet."""


# ── network calculation ────────────────────────────────────────

def compute_network(
    ip_address: str,
    subnet_prefix: int = 24,
    gateway_last_octet: int = 254,
) -> dict:
    """Return the network + gateway for a device IP.

    gateway_last_octet is interpreted as an OFFSET from the network address
    (for a /24 that is the familiar last octet, e.g. .254). Raises
    NetworkCalcError on any invalid input.
    """
    raw = str(ip_address or "").strip()
    if not raw:
        raise NetworkCalcError("عنوان IP مطلوب.")
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise NetworkCalcError(f"عنوان IP غير صالح: {raw}") from exc
    if not isinstance(ip, ipaddress.IPv4Address):
        raise NetworkCalcError("يُدعم IPv4 فقط حاليًا.")
    try:
        prefix = int(subnet_prefix)
    except (TypeError, ValueError) as exc:
        raise NetworkCalcError("بادئة الشبكة غير صالحة.") from exc
    if not (1 <= prefix <= 32):
        raise NetworkCalcError("بادئة الشبكة يجب أن تكون بين 1 و32.")
    try:
        octet = int(gateway_last_octet)
    except (TypeError, ValueError) as exc:
        raise NetworkCalcError("آخر أوكتت للبوابة غير صالح.") from exc

    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    gw_int = int(net.network_address) + octet
    if octet < 0 or gw_int > int(net.broadcast_address):
        raise NetworkCalcError(
            f"آخر أوكتت للبوابة ({octet}) خارج نطاق الشبكة {net}.")
    gateway = ipaddress.IPv4Address(gw_int)
    return {
        "ip_address":      str(ip),
        "prefix":          prefix,
        "network_cidr":    str(net),                 # 192.168.15.0/24
        "network_address": str(net.network_address),
        "broadcast":       str(net.broadcast_address),
        "gateway_ip":      str(gateway),             # 192.168.15.254
        "gateway_address": f"{gateway}/{prefix}",    # 192.168.15.254/24
        "host_in_network": ip in net.hosts() or ip == net.network_address,
    }


def _network_of(address: str) -> Optional[str]:
    """network_cidr of a RouterOS address string like '192.168.15.254/24'.
    Returns None if unparseable (defensive against odd router output)."""
    a = str(address or "").strip()
    if not a:
        return None
    try:
        if "/" in a:
            return str(ipaddress.ip_network(a, strict=False))
        # bare IP — treat as /32 host
        return str(ipaddress.ip_network(f"{a}/32", strict=False))
    except ValueError:
        return None


def _addr_ip(address: str) -> Optional[str]:
    a = str(address or "").strip()
    if not a:
        return None
    try:
        return str(ipaddress.ip_interface(a).ip) if "/" in a else str(ipaddress.ip_address(a))
    except ValueError:
        return None


# ── plan builder ───────────────────────────────────────────────

def build_plan(
    *,
    interface_name: str,
    ip_address: str,
    subnet_prefix: int = 24,
    gateway_last_octet: int = 254,
    netwatch_interval_sec: int = 60,
    netwatch_timeout_sec: int = 3,
    binding_type: str = "bypassed",
    router_state: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    device_id: Optional[int] = None,
) -> dict:
    """Build the idempotent MikroTik plan.

    router_state (when provided) is {addresses, bindings, netwatch} — each a
    list of RouterOS rows. When None, the plan is intended-only (Phase 1
    dry-run): items are marked `planned` and duplicate detection is deferred.

    Returns {ok, valid, error, network, items[], warnings[], live}.
    """
    iface = str(interface_name or "").strip()
    if not iface:
        return {"ok": False, "valid": False,
                "error": "المدخل (interface) إلزامي.",
                "items": [], "warnings": [], "live": router_state is not None}
    try:
        net = compute_network(ip_address, subnet_prefix, gateway_last_octet)
    except NetworkCalcError as exc:
        return {"ok": False, "valid": False, "error": str(exc),
                "items": [], "warnings": [], "live": router_state is not None}

    warnings: list[str] = []
    if net["gateway_ip"] == net["ip_address"]:
        warnings.append(
            "بوابة الراوتر تساوي عنوان الجهاز — اختر آخر أوكتت مختلفًا.")

    live = router_state is not None
    addresses = list((router_state or {}).get("addresses") or [])
    bindings = list((router_state or {}).get("bindings") or [])
    netwatch = list((router_state or {}).get("netwatch") or [])

    # ── /ip/address ──
    addr_action = "planned"
    addr_note = ""
    if live:
        # already_present when an address row on the SAME interface carries the
        # same network (or the exact gateway address). Detect routing
        # ambiguity when our network exists on a DIFFERENT interface.
        same_iface_net = False
        other_ifaces: set[str] = set()
        for row in addresses:
            row_net = _network_of(row.get("address", ""))
            row_iface = str(row.get("interface") or "").strip()
            if row_net == net["network_cidr"]:
                if row_iface == iface:
                    same_iface_net = True
                elif row_iface:
                    other_ifaces.add(row_iface)
        addr_action = "already_present" if same_iface_net else "create"
        if other_ifaces:
            warnings.append(
                "نفس الشبكة %s موجودة على مدخل آخر (%s) — غموض توجيه محتمل."
                % (net["network_cidr"], "، ".join(sorted(other_ifaces))))

    items: list[dict] = [{
        "kind": "ip_address",
        "action": addr_action,
        "title": "عنوان IP/بوابة على المدخل",
        "address": net["gateway_address"],
        "interface": iface,
        "command": (
            f'/ip/address/add address={net["gateway_address"]} '
            f'interface={iface} comment="managed-by device-health"'),
        "note": addr_note,
    }]

    # ── /ip/hotspot/ip-binding ──
    bind_action = "planned"
    if live:
        present = False
        for row in bindings:
            row_net = _network_of(row.get("address", ""))
            row_type = str(row.get("type") or "").strip().lower()
            if row_net == net["network_cidr"] and row_type == binding_type:
                present = True
                break
        bind_action = "already_present" if present else "create"
    items.append({
        "kind": "ip_binding",
        "action": bind_action,
        "title": "تجاوز Hotspot (IP-Binding) للشبكة",
        "address": net["network_cidr"],
        "binding_type": binding_type,
        "command": (
            f'/ip/hotspot/ip-binding/add address={net["network_cidr"]} '
            f'type={binding_type} comment="managed-by device-health"'),
        "note": "",
    })

    # ── /tool/netwatch ──
    nw_action = "planned"
    if live:
        present = any(
            _addr_ip(row.get("host", "")) == net["ip_address"]
            for row in netwatch
        )
        nw_action = "already_present" if present else "create"
    cid = f" device_id={int(device_id)}" if device_id else ""
    items.append({
        "kind": "netwatch",
        "action": nw_action,
        "title": "مراقبة Netwatch لعنوان الجهاز",
        "host": net["ip_address"],
        "interval_sec": int(netwatch_interval_sec),
        "timeout_sec": int(netwatch_timeout_sec),
        "command": (
            f'/tool/netwatch/add host={net["ip_address"]} type=simple '
            f'interval={_hms(netwatch_interval_sec)} '
            f'timeout={_hms(netwatch_timeout_sec)} '
            f'comment="managed-by device-health{cid}"'),
        "note": "",
    })

    return {
        "ok": True,
        "valid": True,
        "error": "",
        "network": net,
        "items": items,
        "warnings": warnings,
        "live": live,
    }


def _hms(seconds: int) -> str:
    s = max(1, int(seconds))
    h, rem = divmod(s, 3600)
    m, s2 = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s2:02d}"


# ── ping summarisation (shared by test_ping + poller) ──────────

import re as _re  # noqa: E402


def parse_router_time_ms(text: str) -> Optional[float]:
    """Parse RouterOS ping time tokens ('2ms', '500us', '1s200ms') → ms."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    total = 0.0
    found = False
    for value, unit in _re.findall(r"(\d+(?:\.\d+)?)(us|ms|s)", s):
        found = True
        v = float(value)
        total += v / 1000.0 if unit == "us" else (v * 1000.0 if unit == "s" else v)
    if found:
        return round(total, 3)
    try:
        return float(s)  # bare number → assume ms
    except ValueError:
        return None


def summarize_ping(rows: list, threshold_ms: int) -> tuple[str, Optional[float]]:
    """Reduce /ping !re rows to (status, avg_latency_ms).
    No replies / all timeouts → ('down', None); avg over threshold →
    ('high_latency', avg); else ('up', avg)."""
    latencies: list[float] = []
    for r in rows or []:
        t = str(r.get("time") or "").strip()
        if r.get("status") and not t:
            continue  # 'timeout' / 'host unreachable' rows
        ms = parse_router_time_ms(t)
        if ms is not None:
            latencies.append(ms)
    if not latencies:
        return "down", None
    avg = sum(latencies) / len(latencies)
    if avg > float(threshold_ms or 80):
        return "high_latency", round(avg, 1)
    return "up", round(avg, 1)
