"""mt_interface_safety — S4.1 interface danger classifier.

Pure functions over RouterOS rows that answer the question:
"Is it safe to configure THIS interface for hotspot/PPPoE
programming?"

We avoid certainty. The output is a risk level — `blocked`,
`high`, `medium`, `low`, or `unknown` — plus a short Arabic
reason the operator sees. S4.2 wires this into the programming
planner so dangerous picks halt the apply path.

Signals we look at:
  - `name`       — case-insensitive substring on "wan", "wg",
                   "wireguard", "uplink", "internet"
  - `type`       — RouterOS native type (wireguard/ether/bridge...)
  - `routes`     — does this iface carry the default route?
  - `addresses`  — does it own an IP inside the WG management
                   subnet (HOBERADIUS_WG_SUBNET)?
  - `comment`    — operator-written hint (hoberadius / management)

Risk levels — sort order: lower number = safer.

We're cautious by design: when in doubt, we don't say "low" —
we say "unknown" and let the operator confirm.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


# ─── Risk taxonomy ────────────────────────────────────────────


RISK_BLOCKED = "blocked"  # never allow programming on this iface
RISK_HIGH    = "high"     # require an explicit override (S4.2)
RISK_MEDIUM  = "medium"   # require confirmation
RISK_LOW     = "low"      # proceed if no other signal disagrees
RISK_UNKNOWN = "unknown"  # not enough signal — default to caution

_ORDER = {
    RISK_BLOCKED: 4,
    RISK_HIGH:    3,
    RISK_MEDIUM:  2,
    RISK_UNKNOWN: 1,
    RISK_LOW:     0,
}


def _worse(a: str, b: str) -> str:
    """Return whichever level is *more* dangerous. Used to
    fold multiple independent signals into one verdict."""
    return a if _ORDER.get(a, 0) >= _ORDER.get(b, 0) else b


# ─── Result type ──────────────────────────────────────────────


@dataclass
class InterfaceRisk:
    interface: str
    risk: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": self.interface,
            "risk": self.risk,
            "reasons": list(self.reasons),
        }


# ─── Signal helpers ───────────────────────────────────────────


_NAME_FRAGMENTS_WAN = ("wan", "uplink", "internet")
_NAME_FRAGMENTS_WG  = ("wg", "wireguard")
_NAME_FRAGMENTS_MGMT = ("mgmt", "management", "hoberadius")


def _name_hits(name: str, fragments: Iterable[str]) -> bool:
    low = (name or "").lower()
    return any(f in low for f in fragments)


def _is_wireguard_interface(iface: Mapping[str, Any]) -> bool:
    t = (iface.get("type") or "").lower()
    if t == "wireguard":
        return True
    return _name_hits(iface.get("name") or "", _NAME_FRAGMENTS_WG)


def _carries_default_route(name: str, routes: Iterable[Mapping]) -> bool:
    """Any /ip/route row with dst-address=0.0.0.0/0 + this
    interface as the gateway-interface = the WAN by definition.
    RouterOS exposes the resolved gateway interface as
    `gateway-interface` after lookup."""
    name_l = (name or "").lower()
    for r in routes or ():
        dst = (r.get("dst-address") or "").strip()
        if dst not in ("0.0.0.0/0", "::/0"):
            continue
        # Disabled / inactive default routes don't matter.
        if str(r.get("disabled")) == "true":
            continue
        # If `gateway` is text matching the interface name
        # exactly, or `gateway-interface` does, this iface is
        # the uplink.
        gw_iface = (r.get("gateway-interface")
                    or r.get("gateway") or "").lower()
        if name_l and gw_iface and (
            name_l == gw_iface or name_l in gw_iface
        ):
            return True
    return False


def _has_wg_subnet_address(addresses: Iterable[Mapping],
                            iface_name: str,
                            wg_subnet: str | None) -> bool:
    """Any /ip/address on this iface inside the HOBERADIUS_WG
    subnet → this is the management interface."""
    if not wg_subnet:
        return False
    try:
        wg_net = ipaddress.IPv4Network(wg_subnet, strict=False)
    except (ipaddress.AddressValueError,
            ipaddress.NetmaskValueError, ValueError):
        return False
    name_l = (iface_name or "").lower()
    for a in addresses or ():
        if (a.get("interface") or "").lower() != name_l:
            continue
        addr_raw = (a.get("address") or "").strip()
        if not addr_raw:
            continue
        try:
            net = ipaddress.ip_interface(addr_raw).network
        except (ValueError, TypeError):
            continue
        if net.version != wg_net.version:
            continue
        if wg_net.overlaps(net):
            return True
    return False


def _comment_says_management(comment: str) -> bool:
    low = (comment or "").lower()
    return any(f in low for f in _NAME_FRAGMENTS_MGMT)


# ─── Public classifier ────────────────────────────────────────


def classify_interface(
    iface: Mapping[str, Any],
    *,
    routes: Iterable[Mapping] | None = None,
    addresses: Iterable[Mapping] | None = None,
    wg_subnet: str | None = None,
) -> InterfaceRisk:
    """Risk-classify one interface.

    Caller passes the interface row + the router's route table +
    address table. Anything not provided is treated as absent
    (don't fabricate signals).

    Returns an `InterfaceRisk` carrying the verdict and a list of
    reasons (operator-facing Arabic) so the UI can render them.
    """
    name = (iface.get("name") or "").strip()
    routes = list(routes or ())
    addresses = list(addresses or ())
    if wg_subnet is None:
        wg_subnet = (os.environ.get("HOBERADIUS_WG_SUBNET")
                     or "").strip() or None

    verdict = RISK_UNKNOWN
    reasons: list[str] = []

    # (1) WireGuard / management interface — BLOCKED.
    if _is_wireguard_interface(iface):
        verdict = _worse(verdict, RISK_BLOCKED)
        reasons.append("هذه واجهة WireGuard أو إدارة — لا يجوز "
                       "إعادة برمجتها لأنها قناة وصولنا للراوتر.")
    if _has_wg_subnet_address(addresses, name, wg_subnet):
        verdict = _worse(verdict, RISK_BLOCKED)
        reasons.append(
            f"الواجهة تحمل عنوانًا داخل شبكة الإدارة "
            f"({wg_subnet}) — قطعها سيُنهي اتصالنا بالراوتر."
        )

    # (2) Carries the default route — almost certainly WAN.
    if _carries_default_route(name, routes):
        verdict = _worse(verdict, RISK_HIGH)
        reasons.append("الواجهة تحمل المسار الافتراضي — هي مزوّد "
                       "الإنترنت (WAN).")

    # (3) Name says WAN/uplink/internet — high suspicion.
    if _name_hits(name, _NAME_FRAGMENTS_WAN):
        verdict = _worse(verdict, RISK_HIGH)
        reasons.append("اسم الواجهة يوحي بأنها WAN.")

    # (4) Operator labelled it as management — block.
    if _comment_says_management(iface.get("comment") or ""):
        verdict = _worse(verdict, RISK_BLOCKED)
        reasons.append("تعليق الواجهة يصفها كأداة إدارة.")

    # (5) Otherwise: if we have a useful signal that it's a
    # plain LAN-style port (ether without default route, no
    # management hints) and it carries no addresses, treat as
    # `low`. With addresses it's still `unknown` so the operator
    # confirms.
    if verdict == RISK_UNKNOWN:
        t = (iface.get("type") or "").lower()
        has_addr_on_iface = any(
            (a.get("interface") or "").lower() == name.lower()
            for a in addresses
        )
        if t in {"ether", "vlan", "bridge"} and not has_addr_on_iface:
            verdict = RISK_LOW
            reasons.append("واجهة محلية بلا عنوان مُعطى — "
                           "مرشّحة مقبولة للبرمجة.")

    return InterfaceRisk(interface=name or "?", risk=verdict,
                          reasons=reasons)


def classify_many(
    interfaces: Iterable[Mapping[str, Any]],
    *,
    routes: Iterable[Mapping] | None = None,
    addresses: Iterable[Mapping] | None = None,
    wg_subnet: str | None = None,
) -> list[InterfaceRisk]:
    """Convenience for the diagnostics tab — classify every
    interface in one call."""
    routes_list = list(routes or ())
    addr_list = list(addresses or ())
    return [
        classify_interface(
            i, routes=routes_list,
            addresses=addr_list, wg_subnet=wg_subnet,
        )
        for i in (interfaces or ())
    ]


__all__ = [
    "RISK_BLOCKED", "RISK_HIGH", "RISK_MEDIUM",
    "RISK_LOW", "RISK_UNKNOWN",
    "InterfaceRisk",
    "classify_interface",
    "classify_many",
]
