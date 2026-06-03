"""K1.2 — resolve which address the admin app dials when talking to
a NAS / MikroTik router, honouring the VPN connection-mode flag.

Single source of truth for "where does this router live?" Every
caller that previously did `nas["address"]` or
`nas["nasname"]` should pipe the row through
`resolve_connection_address(nas)` first.

Why
- A router behind NAT has no public IP we can dial. With WireGuard
  it has a stable private IP inside the VPN subnet (e.g.
  10.10.0.5), and that's what the admin app uses.
- A router with a dynamic public IP also benefits: the public
  address may change, but the VPN peer IP is fixed.
- Routers with static public IPs keep working unchanged
  (default `connection_mode='direct'`).
"""
from __future__ import annotations

from typing import Any, Mapping

_VPN_MODE = "vpn"
_DIRECT_MODE = "direct"


def _as_dict(nas: Any) -> dict:
    """Tolerate raw ``sqlite3.Row`` (and other Mapping-likes) — many callers
    pass a fetched row straight in. ``sqlite3.Row`` supports ``[]``/``keys()``
    but not ``.get()``, so coerce to a plain dict first."""
    if not nas:
        return {}
    if isinstance(nas, dict):
        return nas
    try:
        return dict(nas)
    except (TypeError, ValueError):
        return {}


def resolve_connection_address(nas: Mapping[str, Any]) -> str:
    """Return the address the admin should dial for this router.

    Accepts a row from either `nas_devices` (where the public IP
    sits in `address`) or `nas` (FreeRADIUS table, public IP in
    `nasname`). When `connection_mode = 'vpn'` AND a
    `vpn_peer_address` is set, returns the VPN peer IP. Otherwise
    falls back to the row's public address.

    Empty input → empty string (callers check before dialling).
    """
    nas = _as_dict(nas)
    if not nas:
        return ""
    mode = str(nas.get("connection_mode") or _DIRECT_MODE).strip().lower()
    if mode == _VPN_MODE:
        vpn_ip = str(nas.get("vpn_peer_address") or "").strip()
        if vpn_ip:
            return vpn_ip
    # Direct fall-back. Try both column names (nas_devices uses
    # `address`, FreeRADIUS `nas` table uses `nasname`).
    for key in ("address", "nasname", "ip", "host"):
        value = nas.get(key)
        if value:
            return str(value).strip()
    return ""


def resolve_connection_descriptor(nas: Mapping[str, Any]) -> dict:
    """Return a {address, mode, public_address, vpn_peer} bundle.

    Useful for diagnostic surfaces — shows both the dialled address
    AND the originally-configured public IP so operators can spot
    misconfigurations (e.g. VPN selected but no peer IP set).
    """
    nas = _as_dict(nas)
    mode = str(nas.get("connection_mode") or _DIRECT_MODE).strip().lower()
    public = ""
    for key in ("address", "nasname", "ip", "host"):
        value = nas.get(key)
        if value:
            public = str(value).strip()
            break
    vpn_peer = str(nas.get("vpn_peer_address") or "").strip()
    return {
        "address": resolve_connection_address(nas),
        "mode": mode if mode in {_DIRECT_MODE, _VPN_MODE} else _DIRECT_MODE,
        "public_address": public,
        "vpn_peer_address": vpn_peer,
        "vpn_interface": str(nas.get("vpn_interface") or "").strip(),
        "vpn_public_key": str(nas.get("vpn_public_key") or "").strip(),
        "vpn_last_handshake_ts": int(nas.get("vpn_last_handshake_ts") or 0),
        "vpn_assigned_ip": str(nas.get("vpn_assigned_ip") or "").strip(),
    }


def is_vpn_mode(nas: Mapping[str, Any]) -> bool:
    """True if this NAS is configured for VPN-based connection."""
    nas = _as_dict(nas)
    return str(nas.get("connection_mode") or _DIRECT_MODE).strip().lower() == _VPN_MODE


__all__ = [
    "resolve_connection_address",
    "resolve_connection_descriptor",
    "is_vpn_mode",
]
