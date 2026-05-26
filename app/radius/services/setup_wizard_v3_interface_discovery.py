"""SW7+ — discover router interfaces via the live VPN tunnel.

After Step 3 succeeds (WireGuard handshake), the VPS can reach
the router at its assigned 10.10.0.x address. This service
uses that path + the operator-supplied API credentials to
list the actual physical/logical interfaces — so the Hotspot
card in Step 5 can show real checkboxes instead of guessing
at common names like ether2-5.

Two discovery paths:

  1. **api** — connects to <router_vpn_ip>:8728 via the
     established WireGuard tunnel using the MikrotikClient.
     Fast, accurate, but requires admin credentials.

  2. **paste** — operator runs `/interface print` on the
     router and pastes the output here. No credentials
     required. Regex parses `name=` and `type=` columns.

Returns a normalised list:

    [
      {"name": "ether1", "type": "ether", "running": true},
      {"name": "ether2", "type": "ether", "running": false},
      {"name": "wlan1",  "type": "wlan",  "running": true},
      ...
    ]
"""
from __future__ import annotations

import logging
import re
from typing import Any


_LOG = logging.getLogger(__name__)


# Interfaces the operator should NEVER select for hotspot —
# excluded from the result. These are management / loopback /
# tunnel interfaces.
_BLOCKED_TYPES = {"loopback", "wg", "wireguard", "vrrp"}
_BLOCKED_NAMES = {"hr-wg", "lo"}


class InterfaceDiscoveryError(Exception):
    """Raised when both API and paste-back discovery fail."""


def discover_via_api(
    *,
    router_vpn_ip: str,
    api_user: str,
    api_password: str,
    port: int = 8728,
    use_tls: bool = False,
    timeout: float = 8.0,
) -> list[dict[str, Any]]:
    """Connect to the router via the tunnel and query
    `/interface print`. Returns the filtered list."""
    if not router_vpn_ip:
        raise InterfaceDiscoveryError(
            "router_vpn_ip is required",
        )
    if not api_user:
        raise InterfaceDiscoveryError(
            "api_user is required for API discovery",
        )

    from ..integration.mikrotik.client import MikrotikClient
    from ..integration.mikrotik.errors import (
        AuthError, ConnectError, MikrotikError,
    )

    try:
        with MikrotikClient(
            host=router_vpn_ip,
            username=api_user,
            password=api_password or "",
            port=port,
            use_tls=use_tls,
            timeout=timeout,
        ) as mt:
            rows = list(mt.print_("/interface/print"))
    except AuthError as exc:
        raise InterfaceDiscoveryError(
            f"كلمة مرور API غير صحيحة: {exc}",
        ) from exc
    except ConnectError as exc:
        raise InterfaceDiscoveryError(
            f"تعذّر الاتصال بالراوتر عبر VPN ({router_vpn_ip}). "
            f"تأكّد أن handshake نجح في الخطوة 4: {exc}",
        ) from exc
    except MikrotikError as exc:
        raise InterfaceDiscoveryError(
            f"خطأ من الراوتر: {exc}",
        ) from exc

    return _normalise_rows(rows)


# Matches a `/interface print` row. RouterOS prints rows like:
#   0  R  name="ether1"  default-name="ether1"  type="ether"  mtu=1500 ...
#   1  X  name="hr-wg"  type="wg"  ...
# We capture name + type + the leading flags column.
_PASTE_ROW_RE = re.compile(
    r"^\s*\d+\s+([A-Z\-]*)\s+"
    r"name=\"?([^\"\s]+)\"?"
    r".*?type=\"?([^\"\s]+)\"?",
    re.MULTILINE,
)


def discover_via_paste(pasted_output: str) -> list[dict[str, Any]]:
    """Parse the pasted output of `/interface print` from
    MikroTik Terminal."""
    if not pasted_output or not pasted_output.strip():
        raise InterfaceDiscoveryError(
            "الإخراج المُلصق فارغ",
        )
    rows = []
    for m in _PASTE_ROW_RE.finditer(pasted_output):
        flags, name, type_ = m.group(1), m.group(2), m.group(3)
        rows.append({
            "name": name,
            "type": type_,
            "running": "R" in (flags or ""),
            "disabled": "X" in (flags or ""),
        })
    if not rows:
        # Fall back to a looser parser — some RouterOS versions
        # print differently (no flags, or columns rearranged).
        # Just look for `name="..."` markers.
        for m in re.finditer(r'name="([^"]+)"', pasted_output):
            rows.append({
                "name": m.group(1),
                "type": "unknown",
                "running": False,
                "disabled": False,
            })
    if not rows:
        raise InterfaceDiscoveryError(
            "لم أتمكّن من تحليل الإخراج. تأكّد من لصق نتيجة "
            "/interface print كاملةً من MikroTik Terminal.",
        )
    return _normalise_rows(rows)


def _normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter out interfaces operators shouldn't pick for
    hotspot, and stamp a stable shape."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        name = str(r.get("name") or "").strip()
        if not name or name in seen:
            continue
        type_ = str(r.get("type") or "").strip().lower()
        if type_ in _BLOCKED_TYPES:
            continue
        if name in _BLOCKED_NAMES:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "type": type_ or "unknown",
            "running": bool(r.get("running", False)),
            "disabled": bool(r.get("disabled", False)),
            # Helps the UI decide a sensible default — disable
            # selecting interfaces that are already down unless
            # the operator overrides.
            "recommended": (
                type_ in {"ether", "wlan", "sfp", "bridge"}
                and not r.get("disabled", False)
            ),
        })
    # Sort so eth/wlan come first (more likely user-facing),
    # then by name.
    type_order = {
        "ether": 0, "wlan": 1, "sfp": 2, "bridge": 3,
    }
    out.sort(
        key=lambda r: (
            type_order.get(r["type"], 9),
            r["name"],
        ),
    )
    return out


__all__ = [
    "discover_via_api",
    "discover_via_paste",
    "InterfaceDiscoveryError",
]
