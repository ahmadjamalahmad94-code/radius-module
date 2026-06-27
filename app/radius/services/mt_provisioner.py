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

import re
import secrets
import string
import ipaddress
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
    tunnel_block: Optional[str] = None,
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
    # Prepend the tunnel setup (WG for v7, SSTP/PPTP mgmt for v6) so the
    # tunnel comes up BEFORE the router tries to reach the RADIUS server
    # through it (the rest of the script uses server_ip = the tunnel-side IP).
    prepend = wg_block or tunnel_block
    if prepend:
        rendered = prepend.rstrip() + "\n\n" + rendered
    return rendered


# ─── v6 management-tunnel blocks (SSTP / PPTP over accel-ppp) ─────────────
#
# RouterOS 6 has no WireGuard, so the management tunnel is an SSTP (default)
# or PPTP (fallback) client dialing accel-ppp on the RADIUS VPS. accel hands
# the router a fixed Framed-IP from the management pool, so the server can
# always reach it for CoA. These mirror `render_wg_block` for v7. Injection
# safety reuses data_connection's `_safe_quoted` (same helper the subscriber
# SSTP scripts use). `add-default-route=no` keeps it a MANAGEMENT tunnel —
# only RADIUS/CoA traffic crosses it, never the router's default route.


def render_sstp_mgmt_block(
    *, nas_name: str, accel_host: str, username: str, password: str,
    port: int = 443, iface: str = "hr-sstp-mgmt",
) -> str:
    """RouterOS-side SSTP management-tunnel client block (v6 default).

    `verify-server-certificate=no` — the accel server uses a self-signed
    cert for the management tunnel.

    `profile=default` (NOT `default-encryption`): SSTP is already wrapped in
    TLS, so asking PPP to add MPPE on top makes RouterOS emit
    ``ccp: failed to get flags`` / ``ppp_unit_send: short write`` and the link
    never settles. `default` (no PPP-layer encryption) is the correct profile
    for an SSTP transport — confirmed against the live ccr4 incident.
    """
    from . import data_connection as _dc

    if not accel_host or not username or not password:
        raise ValueError("SSTP mgmt block needs accel_host + credentials")
    host = _dc._safe_quoted(accel_host, field="connect-to")
    name = _dc._safe_quoted(iface, field="name")
    user = _dc._safe_quoted(username, field="user")
    pw = _dc._safe_quoted(password, field="password")
    cmt = _dc.ascii_comment(f"HobeRadius mgmt tunnel for {nas_name}",
                            fallback="HobeRadius mgmt tunnel")
    return (
        "# ── SSTP management tunnel (RouterOS 6.x) ──────────────────────\n"
        "# Dials accel-ppp on the RADIUS VPS; accel hands this router a fixed\n"
        "# tunnel IP so the server can always reach it for RADIUS + CoA.\n"
        "# profile=default (NOT default-encryption): SSTP is already TLS; PPP\n"
        "# MPPE on top breaks the link (ccp/short-write). verify cert=no: the\n"
        "# accel server uses a self-signed certificate. verify-server-address-\n"
        "# from-certificate=no: we dial by IP and the cert CN is a name — leaving\n"
        "# it at the RouterOS default (=yes) makes the periodic address re-check\n"
        "# fail and FLAP the tunnel (confirmed live on ccr5: 49 Link Downs).\n"
        f'/interface sstp-client add name={name} connect-to={host} port={int(port)} '
        f'user="{user}" password="{pw}" profile=default '
        f'verify-server-certificate=no verify-server-address-from-certificate=no '
        f'add-default-route=no disabled=no keepalive-timeout=30 '
        f'comment="{cmt}"\n'
    )


def render_pptp_mgmt_block(
    *, nas_name: str, accel_host: str, username: str, password: str,
    iface: str = "hr-pptp-mgmt",
) -> str:
    """RouterOS-side PPTP management-tunnel client block (v6 fallback)."""
    from . import data_connection as _dc

    if not accel_host or not username or not password:
        raise ValueError("PPTP mgmt block needs accel_host + credentials")
    host = _dc._safe_quoted(accel_host, field="connect-to")
    name = _dc._safe_quoted(iface, field="name")
    user = _dc._safe_quoted(username, field="user")
    pw = _dc._safe_quoted(password, field="password")
    cmt = _dc.ascii_comment(f"HobeRadius mgmt tunnel for {nas_name}",
                            fallback="HobeRadius mgmt tunnel")
    return (
        "# ── PPTP management tunnel (RouterOS 6.x — fallback) ───────────\n"
        "# PPTP has NO TLS server certificate, so there is no verify-server-\n"
        "# certificate / verify-server-address-from-certificate to set (the SSTP\n"
        "# flapping cause does not exist here). keepalive-timeout=30 for parity.\n"
        f'/interface pptp-client add name={name} connect-to={host} '
        f'user="{user}" password="{pw}" profile=default-encryption '
        f'add-default-route=no disabled=no keepalive-timeout=30 comment="{cmt}"\n'
    )


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
    mgmt_server_ip: str = "",
) -> str:
    """RouterOS-side WireGuard setup block.

    Only meaningful for RouterOS 7+ — v6 has no native WireGuard
    support, so callers must NOT render this for ros_version='6'.
    Raises ValueError in that case so a downstream bug surfaces
    early instead of producing a no-op script.

    ``mgmt_server_ip`` is HobeRadius' OWN address inside the WireGuard
    subnet — the source the router sees when the panel reaches it over
    the tunnel (e.g. for the «تفعيل WinBox» forward). The block binds
    WinBox/API/web to exactly that gateway (never the WAN) AND opens the
    WG interface in the input firewall so those services are actually
    reachable over the tunnel. When omitted it defaults to the first host
    of ``allowed_subnet`` (the conventional WG server IP)."""
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

    # HobeRadius' own WG address — the source the router sees over the tunnel.
    # Validate it so only a real IP is ever written into the service/firewall
    # restriction (injection-safe). Fall back to the subnet's first host.
    gw = str(mgmt_server_ip or "").strip()
    if not gw:
        try:
            gw = str(next(ipaddress.ip_network(allowed_subnet, strict=False).hosts()))
        except (ValueError, StopIteration):
            gw = allowed_subnet.split("/")[0]
    gw = str(ipaddress.ip_address(gw))  # raises ValueError on garbage

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
        mgmt_server_ip=gw,
    )


_WG_BLOCK_TEMPLATE_V7 = """# ── HobeRadius WireGuard tunnel (RouterOS 7+) — FULLY IDEMPOTENT ────────
# Re-paste this block any number of times: it converges to exactly ONE clean
# state. It first WIPES every object it owns (our firewall rule + ALL peers and
# addresses on the interface — this also clears the setup wizard's peer) and
# THEN re-creates a single clean set. Nothing piles up. (Duplicate WG peers with
# the same public key but conflicting allowed-address break crypto-routing, so
# WinBox's reply can't return over the tunnel → "remote host closed the
# connection". Wiping-before-add is what prevents that.)

# 0) Wipe duplicates from any prior paste (this block OR the setup wizard).
#    Match by interface/comment so the wizard's peer/address are cleared too.
/ip firewall filter remove [find comment="hr-wg-mgmt"]
/interface/wireguard/peers remove [find interface="{wg_iface}"]
/ip address remove [find interface="{wg_iface}"]

# 0a) Create the WG interface ONLY if missing — a re-paste keeps the existing
#     interface (and the router's private key) rather than churning it.
:if ([:len [/interface wireguard find name="{wg_iface}"]]=0) do={{/interface wireguard add name="{wg_iface}" private-key="{router_private_key}" comment="HobeRadius tunnel for {nas_name}"}}

# 0b) Add HobeRadius (the VPS) as the ONE peer (after the wipe above).
/interface/wireguard/peers add interface={wg_iface} \\
    public-key="{server_pubkey}" \\
    endpoint-address={endpoint_host} endpoint-port={endpoint_port} \\
    allowed-address={allowed_subnet} \\
    persistent-keepalive={keepalive_sec}s

# 0c) Bind the tunnel IP HobeRadius allocated (after the wipe above). The /24
#     prefix gives a connected route back to the VPS gateway over the tunnel.
/ip/address add interface={wg_iface} address={router_tunnel_ip}

# 0d) Restrict WinBox/API/web to the WireGuard tunnel — and ONLY it (never the
#     WAN). The panel reaches this router from inside {allowed_subnet} (its WG
#     gateway is {mgmt_server_ip}); allowing the whole tunnel subnet is robust
#     to the exact source the router sees over wg0 (the panel's nginx-stream
#     forward egresses wg0 SNAT'd into this subnet). This is what lets «تفعيل
#     WinBox» (the panel's port-forward over WG) actually connect.
/ip service set winbox address={allowed_subnet}
/ip service set api address={allowed_subnet}
/ip service set www address={allowed_subnet}

# 0e) Permit the management tunnel in the input firewall so the services above
#     are reachable over WireGuard. Idempotent (removed in step 0; re-added here
#     and lifted to the top, above any input drop).
/ip firewall filter add chain=input in-interface={wg_iface} src-address={allowed_subnet} \\
    action=accept comment="hr-wg-mgmt"
/ip firewall filter move [find comment="hr-wg-mgmt"] destination=0
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


# ─── Section line-ranges for the «شرح الكود» panels ──────────────────────────
#
# The onboarding page's praised treatment makes every «شرح الكود» row a live
# table-of-contents entry: clicking it scrolls + flashes that section's lines in
# the code card (see code_card.html `cc.code_explain` + code_card.js `doJump`).
# That only works when each explanation item carries an accurate, 1-based
# inclusive `start_line`/`end_line` for the script the page renders. The other
# script pages (mt_setup_script / sstp_credentials / wg_details) build their
# explanations inline, so they need the same ranges — computed from the rendered
# text (never hard-coded) so they stay correct if a template changes.

_SECTION_MARKER_RE = re.compile(r"^#\s*([0-9]+[a-z]?)\)")


def script_section_lines(script: str) -> dict:
    """Map each numbered section marker in a rendered RouterOS script to its
    1-based inclusive ``(start_line, end_line)``.

    A section starts at a comment whose text begins with a marker like ``# 1)``
    or ``# 0a)`` and runs until the line before the next such marker (or the last
    non-empty line for the final marker). Returns ``{marker: (start, end)}`` —
    e.g. ``{"0a": (2, 4), "0b": (6, 11), "1": (15, 17)}``. Presentation-only.
    """
    lines = (script or "").split("\n")
    marks = []  # (0-based line index, marker key)
    for i, ln in enumerate(lines):
        m = _SECTION_MARKER_RE.match(ln.strip())
        if m:
            marks.append((i, m.group(1)))
    nonempty = [i for i, l in enumerate(lines) if l.strip()]
    last = nonempty[-1] if nonempty else max(len(lines) - 1, 0)
    out: dict = {}
    for idx, (li, key) in enumerate(marks):
        end_i = (marks[idx + 1][0] - 1) if idx + 1 < len(marks) else last
        out[key] = (li + 1, max(end_i + 1, li + 1))
    return out


def block_command_line(block: str) -> int:
    """1-based line number of the first RouterOS command (``/...``) in a block.

    The SSTP/PPTP management blocks are a header of comment lines followed by a
    single ``/interface ...-client add ...`` command, so credential/encryption
    explanations all point at this one command line. Falls back to 1.
    """
    for i, ln in enumerate((block or "").split("\n"), start=1):
        if ln.lstrip().startswith("/"):
            return i
    return 1


def block_line_count(block: str) -> int:
    """1-based count of rendered lines for a code card (matches how
    ``code_card`` splits on ``\\n``). Used as an explanation's ``end_line`` when
    a whole block is a single logical section (e.g. the accel-ppp config)."""
    return max(len((block or "").split("\n")), 1)


__all__ = [
    "generate_credentials",
    "render_routeros_script",
    "render_wg_block",
    "render_sstp_mgmt_block",
    "render_pptp_mgmt_block",
    "script_section_lines",
    "block_command_line",
    "block_line_count",
    "SUPPORTED_ROS_VERSIONS",
]
