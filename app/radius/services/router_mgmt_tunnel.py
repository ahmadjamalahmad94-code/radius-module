"""v6 — Router MANAGEMENT tunnel over SSTP/PPTP (accel-ppp on the VPS).

RouterOS 6 has no WireGuard, so the always-on management tunnel (router →
RADIUS server, giving the server a STABLE internal IP to reach the router
for RADIUS auth + CoA) is served by **accel-ppp** on the RADIUS VPS:
SSTP :443 (default) / PPTP :1723, authenticating SSTP/PPTP logins against
the LOCAL FreeRADIUS, CoA/DAE on :3799.

This module is the v6 mirror of :mod:`wg_peer_manager`:

    wg_peer_manager                     router_mgmt_tunnel (this file)
    ─────────────────────────────────   ────────────────────────────────
    keypair (X25519)                 →   tunnel credential (user+password)
    peer file = the WG "store"       →   radcheck/radreply = the RADIUS store
    allocate_next_ip (peers.d scan)  →   allocate_tunnel_ip (nas_devices scan)
    provision_peer()                 →   provision_tunnel()
    deprovision_peer()               →   deprovision_tunnel()

The per-router tunnel account is a **RADIUS-authenticatable** user written
the SAME way the system creates every RADIUS-authable entity: radcheck
(``Cleartext-Password``) + radreply (``Framed-IP-Address`` = the router's
fixed tunnel IP). accel hands the router that fixed IP, so the server can
always dial it for CoA. The account uses a reserved ``rtr-`` username
namespace and has **no** ``subscribers``/``cards`` row, so it never shows
up in the subscriber list.

⚠️ AUTH-REALM ASSUMPTION (flagged, not guessed): this assumes accel's
SSTP/PPTP auth resolves against the standard radcheck/radreply tables
(rlm_sql PAP) — the canonical "RADIUS-authable entity" store in this
system. The Python :func:`policy_engine.authorize` path is scoped to
MikroTik hotspot/PPP subscriber+card auth and would reject a non-subscriber
``rtr-`` user (``user_not_found``); if accel is wired through that path
instead, the account must additionally be recognised there — a server-side
FreeRADIUS/accel realm decision outside this panel.

Persistence of the per-router tunnel state (type / fixed IP / interface /
credential reference) uses migration 092's prepared ``management_*`` columns
on ``nas_devices`` — written by the route layer (mirrors how the WG path
writes nas_devices after :func:`wg_peer_manager.provision_peer`).
"""
from __future__ import annotations

import ipaddress
import logging
import secrets
import string
from dataclasses import dataclass
from typing import Optional

from ..core import env_settings
from ..db.connection import db
from ..db.repos import freeradius_repo
from .wg_peer_manager import _slugify_router_name  # identical slug behaviour

_LOG = logging.getLogger(__name__)


# ─── Configuration (DB → env → default, via env_settings.env) ────────────

ACCEL_HOST_ENV = "HOBERADIUS_ACCEL_SERVER_HOST"
ACCEL_HOST_DEFAULT = "187.77.70.18"

ACCEL_SSTP_PORT_ENV = "HOBERADIUS_ACCEL_SSTP_PORT"
ACCEL_SSTP_PORT_DEFAULT = 443

#: PPTP control port is fixed by the protocol (TCP/1723) — not configurable.
PPTP_PORT = 1723

MGMT_POOL_ENV = "HOBERADIUS_MGMT_TUNNEL_POOL"
MGMT_POOL_DEFAULT = "10.50.0.0/24"

#: The server's own IP inside the management pool — what the router dials for
#: RADIUS once the tunnel is up (the accel gateway). Default = first host.
MGMT_SERVER_IP_ENV = "HOBERADIUS_MGMT_TUNNEL_SERVER_IP"

#: Reserved username namespace for router management tunnel accounts. Distinct
#: from subscribers/cards so they never pollute the subscriber list.
TUNNEL_USER_PREFIX = "rtr-"

#: RouterOS-side interface names (ASCII, stable).
SSTP_IFACE_NAME = "hr-sstp-mgmt"
PPTP_IFACE_NAME = "hr-pptp-mgmt"

TRANSPORT_SSTP = "sstp"
TRANSPORT_PPTP = "pptp"
TRANSPORTS = (TRANSPORT_SSTP, TRANSPORT_PPTP)

#: nas_devices.management_tunnel_type values for each transport.
MGMT_TYPE = {TRANSPORT_SSTP: "sstp_mgmt", TRANSPORT_PPTP: "pptp_mgmt"}

_PASSWORD_LEN = 24
# Password alphabet: ASCII without RouterOS-special chars (`"`, `\`, space,
# backtick) and without `;`/newline — safe to embed in the quoted script line.
_PW_ALPHABET = string.ascii_letters + string.digits + "-._~"


class RouterMgmtTunnelError(ValueError):
    """Invalid input / unprovisionable v6 management tunnel."""


@dataclass(frozen=True)
class MgmtTunnelConfig:
    """Server-side knobs the v6 tunnel script + allocation depend on."""

    accel_host: str
    sstp_port: int
    pool: ipaddress.IPv4Network
    server_ip: ipaddress.IPv4Address


def load_config() -> MgmtTunnelConfig:
    """Read tunnel config from settings (DB → env → default).

    Raises :class:`RouterMgmtTunnelError` on an unusable pool/server IP so a
    wizard run that would produce a broken script errors early.
    """
    accel_host = str(env_settings.env(ACCEL_HOST_ENV, ACCEL_HOST_DEFAULT) or "").strip()
    try:
        sstp_port = int(env_settings.env(ACCEL_SSTP_PORT_ENV, ACCEL_SSTP_PORT_DEFAULT))
    except (TypeError, ValueError):
        sstp_port = ACCEL_SSTP_PORT_DEFAULT

    pool_str = str(env_settings.env(MGMT_POOL_ENV, MGMT_POOL_DEFAULT) or "").strip()
    try:
        pool = ipaddress.ip_network(pool_str, strict=False)
    except ValueError as exc:
        raise RouterMgmtTunnelError(
            f"{MGMT_POOL_ENV}={pool_str!r} ليس شبكة IPv4 صالحة: {exc}"
        ) from exc
    if not isinstance(pool, ipaddress.IPv4Network):
        raise RouterMgmtTunnelError(f"مجمّع الإدارة يجب أن يكون IPv4، وصلنا {pool_str!r}")

    server_ip_str = str(env_settings.env(MGMT_SERVER_IP_ENV, "") or "").strip()
    if not server_ip_str:
        # Default to the first usable host of the pool (the accel gateway).
        server_ip = next(pool.hosts())
    else:
        try:
            server_ip = ipaddress.IPv4Address(server_ip_str)
        except ValueError as exc:
            raise RouterMgmtTunnelError(
                f"{MGMT_SERVER_IP_ENV}={server_ip_str!r} ليس عنوان IPv4 صالحًا: {exc}"
            ) from exc
    if server_ip not in pool:
        raise RouterMgmtTunnelError(
            f"عنوان خادم الإدارة {server_ip} خارج المجمّع {pool}"
        )
    if not accel_host:
        raise RouterMgmtTunnelError(
            f"{ACCEL_HOST_ENV} غير مضبوط — اضبط عنوان خادم accel (مثل {ACCEL_HOST_DEFAULT})."
        )
    return MgmtTunnelConfig(
        accel_host=accel_host, sstp_port=sstp_port, pool=pool, server_ip=server_ip,
    )


# ─── Stable tunnel-IP allocation ─────────────────────────────────────────

def _used_tunnel_ips(tenant_id: int) -> set[ipaddress.IPv4Address]:
    """Tunnel IPs already pinned to a v6 management tunnel (092 columns)."""
    rows = db().execute(
        "SELECT management_remote_address FROM nas_devices "
        "WHERE tenant_id=? AND management_tunnel_type IN ('sstp_mgmt','pptp_mgmt') "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(tenant_id),),
    ).fetchall()
    used: set[ipaddress.IPv4Address] = set()
    for r in rows:
        raw = str((r[0] if not isinstance(r, dict) else r.get("management_remote_address")) or "").strip()
        if not raw:
            continue
        try:
            used.add(ipaddress.IPv4Address(raw))
        except ValueError:
            continue
    return used


def allocate_tunnel_ip(
    tenant_id: int, *, cfg: Optional[MgmtTunnelConfig] = None,
    used_ips: Optional[set] = None,
) -> ipaddress.IPv4Address:
    """First free /32 in the pool, excluding the server IP + pinned IPs.

    Deterministic + stable: once a router's IP is persisted (092 columns) it
    is in ``used`` and never re-handed, so the router keeps the SAME tunnel IP
    across re-provisions of other routers (mirrors WG ``allocate_next_ip``).
    """
    cfg = cfg or load_config()
    reserved = {cfg.server_ip}
    reserved |= (used_ips if used_ips is not None else _used_tunnel_ips(tenant_id))
    for candidate in cfg.pool.hosts():
        if candidate not in reserved:
            return candidate
    raise RouterMgmtTunnelError(
        f"مجمّع الإدارة {cfg.pool} ممتلئ — كل العناوين مُسنَدة"
    )


# ─── Credential ──────────────────────────────────────────────────────────

def tunnel_username(router_name: str) -> str:
    """Deterministic, RADIUS-safe tunnel identity (``rtr-<slug>``)."""
    return TUNNEL_USER_PREFIX + _slugify_router_name(router_name)


def _gen_password() -> str:
    return "".join(secrets.choice(_PW_ALPHABET) for _ in range(_PASSWORD_LEN))


@dataclass(frozen=True)
class TunnelProvisionResult:
    """Everything the wizard needs to write nas_devices + render the script."""

    router_name: str
    slug: str
    transport: str                 # sstp | pptp
    tunnel_username: str
    tunnel_password: str           # shown ONCE; the canonical copy is in radcheck
    tunnel_ip: ipaddress.IPv4Address
    accel_host: str
    sstp_port: int
    pptp_port: int
    pool: ipaddress.IPv4Network
    server_ip: ipaddress.IPv4Address      # server's IP inside the tunnel
    interface: str

    @property
    def mgmt_tunnel_type(self) -> str:
        return MGMT_TYPE[self.transport]

    @property
    def port(self) -> int:
        return self.sstp_port if self.transport == TRANSPORT_SSTP else self.pptp_port

    def to_dict(self) -> dict:
        return {
            "router_name": self.router_name,
            "slug": self.slug,
            "transport": self.transport,
            "tunnel_username": self.tunnel_username,
            "tunnel_ip": str(self.tunnel_ip),
            "accel_host": self.accel_host,
            "port": self.port,
            "pool": str(self.pool),
            "server_ip": str(self.server_ip),
            "interface": self.interface,
            "mgmt_tunnel_type": self.mgmt_tunnel_type,
        }


# ─── Provision / deprovision ─────────────────────────────────────────────

def provision_tunnel(
    router_name: str, *, transport: str = TRANSPORT_SSTP,
    tenant_id: int, cfg: Optional[MgmtTunnelConfig] = None,
) -> TunnelProvisionResult:
    """Allocate a stable IP + credential and write the RADIUS-authable user.

    Writes radcheck (``Cleartext-Password``) + radreply
    (``Framed-IP-Address`` = the fixed tunnel IP) — the same store the system
    uses for every RADIUS-authable entity. The caller persists the per-router
    state into nas_devices' 092 ``management_*`` columns.
    """
    transport = (transport or TRANSPORT_SSTP).strip().lower()
    if transport not in TRANSPORTS:
        raise RouterMgmtTunnelError(
            f"نقل غير مدعوم: {transport!r} (المدعوم: {TRANSPORTS})"
        )
    cfg = cfg or load_config()
    slug = _slugify_router_name(router_name)
    username = TUNNEL_USER_PREFIX + slug
    tunnel_ip = allocate_tunnel_ip(tenant_id, cfg=cfg)
    password = _gen_password()
    iface = SSTP_IFACE_NAME if transport == TRANSPORT_SSTP else PPTP_IFACE_NAME

    # RADIUS-authable account: password (auth) + fixed Framed-IP (stable IP).
    freeradius_repo.replace_user_check(
        int(tenant_id), username, [("Cleartext-Password", ":=", password)],
    )
    freeradius_repo.replace_user_reply(
        int(tenant_id), username, [("Framed-IP-Address", ":=", str(tunnel_ip))],
    )

    _LOG.info(
        "v6 mgmt tunnel: provisioned router=%r transport=%s user=%s ip=%s",
        router_name, transport, username, tunnel_ip,
    )
    return TunnelProvisionResult(
        router_name=router_name, slug=slug, transport=transport,
        tunnel_username=username, tunnel_password=password, tunnel_ip=tunnel_ip,
        accel_host=cfg.accel_host, sstp_port=cfg.sstp_port, pptp_port=PPTP_PORT,
        pool=cfg.pool, server_ip=cfg.server_ip, interface=iface,
    )


def deprovision_tunnel(router_name_or_user: str, *, tenant_id: int) -> bool:
    """Remove the tunnel RADIUS account (radcheck/radreply/radusergroup).

    Accepts a router name or the full ``rtr-`` username. Returns True if a
    radcheck row existed for it.
    """
    raw = (router_name_or_user or "").strip()
    if not raw:
        return False
    username = raw if raw.startswith(TUNNEL_USER_PREFIX) else tunnel_username(raw)
    existed = bool(freeradius_repo.list_user_check(int(tenant_id), username))
    freeradius_repo.delete_user(int(tenant_id), username)
    if existed:
        _LOG.info("v6 mgmt tunnel: deprovisioned user=%s", username)
    return existed


__all__ = [
    "RouterMgmtTunnelError",
    "MgmtTunnelConfig",
    "TunnelProvisionResult",
    "load_config",
    "allocate_tunnel_ip",
    "tunnel_username",
    "provision_tunnel",
    "deprovision_tunnel",
    "TRANSPORT_SSTP",
    "TRANSPORT_PPTP",
    "TRANSPORTS",
    "MGMT_TYPE",
    "SSTP_IFACE_NAME",
    "PPTP_IFACE_NAME",
    "PPTP_PORT",
]
