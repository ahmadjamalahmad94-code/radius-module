"""Combined management-ACL allow-list for the router-side service lockdown.

Both generated scripts bind WinBox/API/web with ``/ip service set <svc>
address=<list>``. RouterOS ``set`` **REPLACES** that value (it does not append),
so each script MUST emit BOTH management gateways or pasting one locks out the
other path:

  * the SSTP/RADIUS gateway inside the v6 management tunnel (e.g. ``10.50.0.1``),
    written by ``router_onboarding_script._section_service_lockdown``; and
  * the WireGuard management subnet (e.g. ``10.10.0.0/24``), written by
    ``mt_provisioner.render_wg_block``.

Whichever was pasted last used to win and silently break the other path (a live
router showed ``winbox address=10.50.0.1/32`` → WinBox-over-WireGuard refused).
This module resolves the two values (env-overridable, same sources the rest of
the system uses) so both scripts can emit the SAME combined, tunnel-only list.
Strictly tunnel-only — never the WAN, never ``0.0.0.0/0``.
"""
from __future__ import annotations

import ipaddress

from ..core import env_settings

#: WireGuard management subnet — mirrors wg_peer_manager.SUBNET_ENV/DEFAULT.
WG_SUBNET_ENV = "HOBERADIUS_WG_SUBNET"
WG_SUBNET_DEFAULT = "10.10.0.0/24"

#: v6 (SSTP/PPTP) management pool + the gateway inside it — mirrors
#: router_mgmt_tunnel.MGMT_POOL_*/MGMT_SERVER_IP_ENV.
MGMT_POOL_ENV = "HOBERADIUS_MGMT_TUNNEL_POOL"
MGMT_POOL_DEFAULT = "10.50.0.0/24"
MGMT_SERVER_IP_ENV = "HOBERADIUS_MGMT_TUNNEL_SERVER_IP"
SSTP_GATEWAY_FALLBACK = "10.50.0.1"


def wg_mgmt_subnet() -> str:
    """The WireGuard management subnet (canonical CIDR). Env-overridable; falls
    back to the default. Validated so only a real network ever reaches the ACL."""
    raw = str(env_settings.env(WG_SUBNET_ENV, WG_SUBNET_DEFAULT)
              or WG_SUBNET_DEFAULT).strip()
    try:
        return str(ipaddress.ip_network(raw, strict=False))
    except ValueError:
        return WG_SUBNET_DEFAULT


def sstp_mgmt_gateway() -> str:
    """The SSTP/RADIUS gateway IP inside the v6 management tunnel (the accel
    gateway the router talks to). Resolution matches
    ``router_mgmt_tunnel.load_config``: an explicit env override, else the first
    usable host of the management pool, else the constant fallback. Returns a
    bare IP (no prefix) — callers append ``/32``."""
    explicit = str(env_settings.env(MGMT_SERVER_IP_ENV, "") or "").strip()
    if explicit:
        try:
            return str(ipaddress.ip_address(explicit))
        except ValueError:
            pass
    pool_raw = str(env_settings.env(MGMT_POOL_ENV, MGMT_POOL_DEFAULT)
                   or MGMT_POOL_DEFAULT).strip()
    try:
        return str(next(ipaddress.ip_network(pool_raw, strict=False).hosts()))
    except (ValueError, StopIteration):
        return SSTP_GATEWAY_FALLBACK


def _dedupe(parts: "list[str]") -> str:
    """Join non-empty parts with ',' preserving order, dropping duplicates."""
    seen: set = set()
    out: list = []
    for p in parts:
        p = str(p or "").strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return ",".join(out)


def combined_acl(*, sstp_gateway_ip: str = "", wg_subnet: str = "",
                 wg_first: bool = False) -> str:
    """Build the combined WinBox/API/web allow-list: the SSTP/RADIUS gateway
    (``<ip>/32``) AND the WireGuard subnet — so re-pasting either script never
    removes the other management path.

    ``sstp_gateway_ip`` / ``wg_subnet`` let a caller pass the ACTUAL value it
    already knows in its own context; whatever is omitted is resolved here from
    env (the other path's constant). ``wg_first`` puts the WG subnet first (used
    by the WG block so its own path leads); default leads with the SSTP gateway
    (used by the SSTP onboarding script)."""
    gw = str(sstp_gateway_ip or "").strip() or sstp_mgmt_gateway()
    gw_cidr = f"{gw}/32"
    sub = str(wg_subnet or "").strip() or wg_mgmt_subnet()
    try:                                  # canonicalise a passed-in subnet
        sub = str(ipaddress.ip_network(sub, strict=False))
    except ValueError:
        sub = wg_mgmt_subnet()
    return _dedupe([sub, gw_cidr] if wg_first else [gw_cidr, sub])


#: The management services every generator must bind to the tunnel gateways.
MGMT_SERVICES = ("winbox", "api", "www")


def service_lockdown_lines(*, sstp_gateway_ip: str = "", wg_subnet: str = "",
                           wg_first: bool = False,
                           services: "tuple[str, ...]" = MGMT_SERVICES,
                           ) -> "list[str]":
    """THE single source of truth for the management-service ACL block.

    Returns the ``/ip service set <svc> address=<combined>`` lines binding
    WinBox/API/web to BOTH management gateways (WG subnet + SSTP/RADIUS gateway).
    EVERY generator that locks down management services (the WG block, the
    onboarding script, the wizard provisioning script, the setup-wizard-v3
    bootstrap) renders this block from here, so the both-gateways ACL can NEVER
    drift between the pages (a WG-only value once took a router offline). The
    `address=` form REPLACES, which is exactly why all callers must emit the
    same combined list. Strictly tunnel-only — never the WAN."""
    acl = combined_acl(sstp_gateway_ip=sstp_gateway_ip, wg_subnet=wg_subnet,
                       wg_first=wg_first)
    return [f"/ip service set {svc} address={acl}" for svc in services]


__all__ = [
    "WG_SUBNET_ENV", "WG_SUBNET_DEFAULT", "MGMT_POOL_ENV", "MGMT_POOL_DEFAULT",
    "MGMT_SERVER_IP_ENV", "SSTP_GATEWAY_FALLBACK", "MGMT_SERVICES",
    "wg_mgmt_subnet", "sstp_mgmt_gateway", "combined_acl",
    "service_lockdown_lines",
]
