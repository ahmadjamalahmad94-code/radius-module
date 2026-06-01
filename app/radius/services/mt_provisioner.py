"""L1 — Auto-provisioner for MikroTik onboarding.

Generates the credentials the setup wizard needs (api_user,
api_password, radius_secret) and renders a RouterOS script that the
operator pastes into Terminal/WinBox to finish provisioning.

The wizard flow:

1. Admin opens `/admin/radius/mt/setup` and types a router name +
   picks RouterOS major version (6 or 7).
2. Server calls `generate_credentials()`, writes a row into
   `nas_devices` with everything filled, and renders the matching
   script via `render_routeros_script()`.
3. Admin copies the script into the router. Within ~3 seconds the
   router has an `hr-XXXXXX` user with API permission, /ip service
   api enabled, and the RADIUS server pre-configured to point back
   at this HobeRadius instance.
4. Admin clicks "Test now" on the same wizard page; the existing
   K3 `/system/overview` endpoint validates the live connection.

No third-party SSH/automation library is involved — the script
runs on the router under the admin's session, so the only secret
that ever leaves this server is the freshly generated, randomly
generated `hr-` user (rotatable at any time).
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from typing import Optional
from typing import Optional

from . import routeros_caps


# Constants chosen to balance entropy against RouterOS field
# length limits. RouterOS accepts passwords up to ~64 chars; 32
# URL-safe characters is ~190 bits of entropy.
_API_USER_PREFIX = "hr-"
_API_USER_SUFFIX_LEN = 6
_API_PASSWORD_LEN = 32
_RADIUS_SECRET_LEN = 32

# Suffix character set: lowercase letters + digits. Avoids RouterOS
# escaping headaches with dashes/underscores inside usernames.
_USER_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits

# Password / secret alphabet: URL-safe ASCII without the few chars
# RouterOS Terminal treats specially (`"`, `\`, backtick, space).
_SAFE_ALPHABET = (
    string.ascii_letters + string.digits + "-._~"
)


def _rand(alphabet: str, length: int) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_credentials() -> dict:
    """Return a fresh dict of credentials. Each call yields a new
    set — the wizard records them in the DB row exactly once."""
    return {
        "api_user": _API_USER_PREFIX + _rand(_USER_SUFFIX_ALPHABET, _API_USER_SUFFIX_LEN),
        "api_password": _rand(_SAFE_ALPHABET, _API_PASSWORD_LEN),
        "radius_secret": _rand(_SAFE_ALPHABET, _RADIUS_SECRET_LEN),
    }


# RouterOS major versions we explicitly support. The script bodies
# are nearly identical today; the split exists so future divergence
# (e.g. RouterOS 7-only commands like `/ipv6/firewall/raw`) can land
# in one branch without touching the other.
SUPPORTED_ROS_VERSIONS = ("6", "7")


def render_routeros_script(
    *,
    nas_name: str,
    api_user: str,
    api_password: str,
    radius_secret: str,
    server_ip: str,
    ros_version: str,
    api_port: int = 8728,
    coa_port: int = 3799,
    wg_block: Optional[str] = None,
    api_allowed_address: Optional[str] = None,
) -> str:
    """Build the copy-paste RouterOS script for one router.

    `server_ip` is the address the router will use to reach this
    HobeRadius instance. If the router sits behind NAT and the
    server has a public IP, that's the public IP. If they're on the
    same LAN, that's the LAN IP. The wizard pre-fills it from the
    request host (or env `HOBERADIUS_PUBLIC_IP`); the admin can
    edit it in the form.

    Raises ValueError on unsupported `ros_version` so the route
    layer can surface a clean 400 to the wizard instead of
    silently rendering a wrong script.
    """
    if ros_version not in SUPPORTED_ROS_VERSIONS:
        raise ValueError(
            f"unsupported RouterOS version: {ros_version!r} "
            f"(supported: {SUPPORTED_ROS_VERSIONS})"
        )
    if not nas_name or not api_user or not api_password or not radius_secret:
        raise ValueError("missing required field for script render")
    if not server_ip:
        raise ValueError("server_ip is required for the RADIUS block")

    # Use single quotes around the script's own strings; RouterOS
    # accepts both quote styles but our `_SAFE_ALPHABET` excludes
    # double quotes so `"..."` is always safe here.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if ros_version == "7":
        body = _SCRIPT_TEMPLATE_V7
    else:
        body = _SCRIPT_TEMPLATE_V6

    # If the caller wants the API exposed only on a specific
    # subnet (typical for VPN-mode routers: lock it to the WG
    # tunnel), emit the extra `set api address=` line; otherwise
    # leave the API open to every interface (the legacy default).
    api_address_line = ""
    if api_allowed_address:
        api_address_line = (
            f"/ip service set api address={api_allowed_address}"
        )

    rendered = body.format(
        nas_name=nas_name,
        api_user=api_user,
        api_password=api_password,
        radius_secret=radius_secret,
        server_ip=server_ip,
        api_port=api_port,
        coa_port=coa_port,
        generated_at=now,
        api_address_line=api_address_line,
    )
    if wg_block:
        # Prepend so the WG tunnel comes up BEFORE the router tries
        # to reach the RADIUS server through it (the rest of the
        # script uses server_ip which is the WG-side IP).
        rendered = wg_block.rstrip() + "\n\n" + rendered
    return rendered


def render_wg_block(
    *,
    nas_name: str,
    router_private_key: str,
    server_pubkey: str,
    server_endpoint: str,
    allowed_subnet: str,
    router_tunnel_ip: str,
    keepalive_sec: int = 25,
    wg_iface: str = "hr-wg",
    ros_version: str = "7",
) -> str:
    """RouterOS-side WireGuard setup block.

    Only meaningful for RouterOS 7+ — v6 has no native WireGuard
    support, so callers must NOT render this for ros_version='6'.
    Raises ValueError in that case so a downstream bug surfaces
    early instead of producing a no-op script.
    """
    # Central capability check: WireGuard is RouterOS 7+ only. Behaviour is
    # identical to the historical `ros_version != "7"` guard for the
    # supported versions ("6"/"7"), but now sourced from routeros_caps so
    # every caller agrees on the version→capability matrix.
    if not routeros_caps.supports_wireguard(ros_version):
        raise ValueError(
            f"WireGuard block requires RouterOS 7+, got {ros_version!r}"
        )
    if not router_private_key or not server_pubkey or not server_endpoint:
        raise ValueError("WireGuard block needs full credentials")
    # Endpoint is `host:port`. Split for RouterOS's separate args.
    if ":" not in server_endpoint:
        raise ValueError(
            f"server_endpoint must be host:port — got {server_endpoint!r}"
        )
    host, _, port = server_endpoint.rpartition(":")
    if not port.isdigit():
        raise ValueError(
            f"server_endpoint port must be numeric — got {server_endpoint!r}"
        )

    return _WG_BLOCK_TEMPLATE_V7.format(
        nas_name=nas_name,
        wg_iface=wg_iface,
        router_private_key=router_private_key,
        server_pubkey=server_pubkey,
        endpoint_host=host,
        endpoint_port=port,
        allowed_subnet=allowed_subnet,
        router_tunnel_ip=router_tunnel_ip,
        keepalive_sec=keepalive_sec,
    )


_WG_BLOCK_TEMPLATE_V7 = """# ── WireGuard tunnel (RouterOS 7+) ─────────────────────────────
# 0a) Create the WG interface with the router-side private key.
/interface/wireguard add name={wg_iface} private-key="{router_private_key}" \\
    comment="HobeRadius tunnel for {nas_name}"

# 0b) Add HobeRadius (the VPS) as the only peer.
/interface/wireguard/peers add interface={wg_iface} \\
    public-key="{server_pubkey}" \\
    endpoint-address={endpoint_host} endpoint-port={endpoint_port} \\
    allowed-address={allowed_subnet} \\
    persistent-keepalive={keepalive_sec}s

# 0c) Bind the tunnel IP that HobeRadius allocated for this router.
/ip/address add interface={wg_iface} address={router_tunnel_ip}
"""


# ─── Script templates ────────────────────────────────────────────


_SCRIPT_TEMPLATE_V7 = """# HobeRadius — auto-provisioning script (RouterOS 7.x)
# Router      : {nas_name}
# Generated   : {generated_at}
# Paste this whole block into RouterOS Terminal (or run line-by-line).

# 1) API user (group with just enough policy for the admin client).
/user group add name=hr-api policy=read,write,api,test,winbox,sniff,sensitive,reboot
/user add name={api_user} password="{api_password}" group=hr-api comment="HobeRadius API"

# 2) Enable RouterOS API on the standard port.
/ip service set api disabled=no port={api_port}
{api_address_line}

# 3) Point RADIUS at this HobeRadius instance.
/radius add address={server_ip} service=hotspot,ppp,login \\
    secret="{radius_secret}" \\
    authentication-port=1812 accounting-port=1813 timeout=3s comment="HobeRadius"

# 4) Accept Change-of-Authorization on the same port the server uses.
/radius incoming set accept=yes port={coa_port}

# 5) Tell hotspot + PPP profiles to use RADIUS.
/ip hotspot profile set [find] use-radius=yes
/ppp aaa set use-radius=yes

:put "HobeRadius provisioning done — back to the wizard and click Test."
"""


_SCRIPT_TEMPLATE_V6 = """# HobeRadius — auto-provisioning script (RouterOS 6.x)
# Router      : {nas_name}
# Generated   : {generated_at}
# Paste this whole block into RouterOS Terminal (or run line-by-line).

# 1) API user (group with just enough policy for the admin client).
/user group add name=hr-api policy=read,write,api,test,winbox,sniff,sensitive,reboot
/user add name={api_user} password="{api_password}" group=hr-api comment="HobeRadius API"

# 2) Enable RouterOS API on the standard port.
/ip service set api disabled=no port={api_port}
{api_address_line}

# 3) Point RADIUS at this HobeRadius instance.
/radius add address={server_ip} service=hotspot,ppp,login \\
    secret="{radius_secret}" \\
    authentication-port=1812 accounting-port=1813 timeout=3s comment="HobeRadius"

# 4) Accept Change-of-Authorization on the same port the server uses.
/radius incoming set accept=yes port={coa_port}

# 5) Tell hotspot + PPP profiles to use RADIUS.
/ip hotspot profile set [find] use-radius=yes
/ppp aaa set use-radius=yes

:put "HobeRadius provisioning done - back to the wizard and click Test."
"""


__all__ = [
    "generate_credentials",
    "render_routeros_script",
    "render_wg_block",
    "SUPPORTED_ROS_VERSIONS",
]
