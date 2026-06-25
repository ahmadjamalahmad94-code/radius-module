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
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..core import env_settings
from ..db.connection import checkpoint_wal, db
from ..db.repos import freeradius_repo
from .wg_peer_manager import _slugify_router_name  # identical slug behaviour

_LOG = logging.getLogger(__name__)


# ─── MSCHAP-v2 secret helpers ─────────────────────────────────────────────
#
# SSTP/PPTP on RouterOS authenticate with MSCHAP-v2, which the RADIUS server
# verifies from a REVERSIBLE secret — either ``Cleartext-Password`` (FreeRADIUS
# rlm_mschap derives the NT hash on the fly) or a precomputed ``NT-Password``
# (uppercase-hex MD4 of the UTF-16-LE password). A bcrypt/scrypt web-login hash
# is irreversible and CANNOT satisfy MSCHAP — that is exactly why the tunnel
# account must NOT reuse the admin web password hash.
#
# We store BOTH: Cleartext-Password (so the secret survives a password reset /
# is human-recoverable for the router script) AND NT-Password (so a server that
# refuses cleartext-in-DB policy still authenticates). NT-Password alone is
# enough for MSCHAP; Cleartext alone is enough too — writing both is belt-and-
# braces and matches how FreeRADIUS treats either as a "known good password".

#: RADIUS attribute names that represent an MSCHAP-compatible (reversible /
#: NT-derivable) secret. Anything else (Crypt-Password, SHA*-Password, …) is
#: NOT usable for MSCHAP-v2 and must be flagged.
MSCHAP_OK_ATTRS = ("Cleartext-Password", "NT-Password")


def _md4_pure(data: bytes) -> bytes:
    """Pure-Python MD4 (RFC 1320) — no OpenSSL dependency.

    OpenSSL 3 drops MD4 from the default provider, so ``hashlib.new("md4")``
    raises ``ValueError`` on many modern hosts. MSCHAP needs MD4, so we keep a
    small dependency-free implementation and only fast-path through hashlib
    when it actually works.
    """
    def lrot(x: int, n: int) -> int:
        x &= 0xFFFFFFFF
        return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF

    msg = bytearray(data)
    orig_len_bits = (8 * len(data)) & 0xFFFFFFFFFFFFFFFF
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg += struct.pack("<Q", orig_len_bits)

    a, b, c, d = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    for off in range(0, len(msg), 64):
        x = list(struct.unpack("<16I", msg[off:off + 64]))
        aa, bb, cc, dd = a, b, c, d

        def f(x, y, z): return (x & y) | (~x & z)
        def g(x, y, z): return (x & y) | (x & z) | (y & z)
        def h(x, y, z): return x ^ y ^ z

        for i in (0, 4, 8, 12):
            a = lrot(a + f(b, c, d) + x[i], 3)
            d = lrot(d + f(a, b, c) + x[i + 1], 7)
            c = lrot(c + f(d, a, b) + x[i + 2], 11)
            b = lrot(b + f(c, d, a) + x[i + 3], 19)
        for i in (0, 1, 2, 3):
            a = lrot(a + g(b, c, d) + x[i] + 0x5A827999, 3)
            d = lrot(d + g(a, b, c) + x[i + 4] + 0x5A827999, 5)
            c = lrot(c + g(d, a, b) + x[i + 8] + 0x5A827999, 9)
            b = lrot(b + g(c, d, a) + x[i + 12] + 0x5A827999, 13)
        for i in (0, 2, 1, 3):
            a = lrot(a + h(b, c, d) + x[i] + 0x6ED9EBA1, 3)
            d = lrot(d + h(a, b, c) + x[i + 8] + 0x6ED9EBA1, 9)
            c = lrot(c + h(d, a, b) + x[i + 4] + 0x6ED9EBA1, 11)
            b = lrot(b + h(c, d, a) + x[i + 12] + 0x6ED9EBA1, 15)

        a = (a + aa) & 0xFFFFFFFF
        b = (b + bb) & 0xFFFFFFFF
        c = (c + cc) & 0xFFFFFFFF
        d = (d + dd) & 0xFFFFFFFF
    return struct.pack("<4I", a, b, c, d)


def nt_password_hash(password: str) -> str:
    """NT hash = uppercase hex of MD4(UTF-16-LE pw) — the 32-hex digest itself.

    Uses the **pure-Python MD4 above unconditionally** — it does NOT touch
    ``hashlib``/OpenSSL. The deployed app container's OpenSSL has md4 disabled
    (``hashlib.new("md4")`` → ``unsupported hash type md4``), so any hashlib
    path would fail there. Pure Python always works. Empty password → "".
    """
    if not password:
        return ""
    return _md4_pure(password.encode("utf-16-le")).hex().upper()


def nt_password_attr_value(password: str) -> str:
    """The radcheck ``NT-Password`` VALUE for FreeRADIUS: ``0x`` + 32 hex.

    rlm_mschap reads ``NT-Password`` as the 16-byte hash; the ``0x`` prefix is
    what tells it the value is a hex-encoded hash. A bare 32-char hex string is
    mis-read as a literal 32-byte cleartext password → "Invalid user". Empty
    password → "" (no row written)."""
    h = nt_password_hash(password)
    return ("0x" + h) if h else ""


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


def _used_framed_ips(tenant_id: int) -> set[ipaddress.IPv4Address]:
    """Tunnel IPs ALREADY assigned in radreply (``Framed-IP-Address`` for any
    ``rtr-*`` account). This is the CANONICAL source of assigned tunnel IPs —
    every provisioned account has one here, whereas
    ``nas_devices.management_remote_address`` is only written by the onboarding
    route (NOT by ``ensure``/reconcile). Scanning radreply is what prevents two
    routers from both getting 10.50.0.2 when reconcile allocates."""
    rows = db().execute(
        "SELECT value FROM radreply "
        "WHERE tenant_id=? AND attribute='Framed-IP-Address' AND username LIKE ?",
        (int(tenant_id), TUNNEL_USER_PREFIX + "%"),
    ).fetchall()
    used: set[ipaddress.IPv4Address] = set()
    for r in rows:
        raw = str((r[0] if not isinstance(r, dict) else r.get("value")) or "").strip()
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
    """First free /32 in the pool, excluding the server IP + already-used IPs.

    Deterministic + stable + collision-free. "Used" is the union of:
      * ``radreply`` Framed-IP-Address of every ``rtr-*`` account (canonical —
        set by both provision AND ensure), and
      * ``nas_devices.management_remote_address`` (the onboarding 092 columns).
    Considering radreply is essential: ``ensure``/reconcile do not write the NAS
    column, so without it a second router would re-grab the first router's IP
    (the 10.50.0.2 collision). Once an IP is in radreply it is never re-handed,
    so each router keeps a UNIQUE, stable tunnel IP across re-provisions.
    """
    cfg = cfg or load_config()
    reserved = {cfg.server_ip}
    if used_ips is not None:
        reserved |= used_ips
    else:
        reserved |= _used_framed_ips(tenant_id)
        reserved |= _used_tunnel_ips(tenant_id)
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

    # RADIUS-authable account: MSCHAP-compatible secret (auth) + fixed Framed-IP
    # (stable IP). We write BOTH Cleartext-Password and NT-Password so SSTP/PPTP
    # MSCHAP-v2 authenticates regardless of the server's cleartext-in-DB policy.
    freeradius_repo.replace_user_check(
        int(tenant_id), username,
        [
            ("Cleartext-Password", ":=", password),
            ("NT-Password", ":=", nt_password_attr_value(password)),
        ],
    )
    freeradius_repo.replace_user_reply(
        int(tenant_id), username, [("Framed-IP-Address", ":=", str(tunnel_ip))],
    )
    # Flush WAL so the FreeRADIUS container's SQLite reader sees the new rows.
    checkpoint_wal()

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


def _check_value(rows: list[dict], attribute: str) -> Optional[str]:
    """First ``value`` for ``attribute`` among radcheck rows (case-sensitive
    on the canonical attribute name FreeRADIUS uses)."""
    for r in rows:
        if str(r.get("attribute") or "") == attribute:
            return str(r.get("value") or "")
    return None


def _parse_fr_expiration(value: str) -> Optional[datetime]:
    """Parse a FreeRADIUS ``Expiration`` value (e.g. '31 Dec 2026 23:59:00').

    Returns None if unparseable (treated as "no expiry" by the caller)."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d %b %Y %H:%M:%S", "%d %b %Y", "%b %d %Y %H:%M:%S", "%b %d %Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class TunnelRadiusStatus:
    """Snapshot of the tunnel account's RADIUS state — drives the UI
    "Synced to RADIUS" indicator and the test-login diagnostic."""

    username: str
    exists: bool                     # any radcheck row for this user
    has_cleartext: bool
    has_nt: bool
    mschap_compatible: bool          # at least one MSCHAP-usable secret present
    incompatible_secret: bool        # a non-MSCHAP secret (Crypt/SHA/…) is set
    disabled: bool                   # Auth-Type := Reject present
    expired: bool                    # Expiration present and in the past
    framed_ip: Optional[str]         # Framed-IP-Address from radreply
    cleartext: Optional[str]         # the stored cleartext (for script/copy)

    @property
    def synced(self) -> bool:
        """True iff the account would authenticate an SSTP/PPTP MSCHAP login:
        a usable secret, not disabled, not expired, with a fixed tunnel IP."""
        return (
            self.exists and self.mschap_compatible and not self.incompatible_secret
            and not self.disabled and not self.expired and bool(self.framed_ip)
        )

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "exists": self.exists,
            "has_cleartext": self.has_cleartext,
            "has_nt": self.has_nt,
            "mschap_compatible": self.mschap_compatible,
            "incompatible_secret": self.incompatible_secret,
            "disabled": self.disabled,
            "expired": self.expired,
            "framed_ip": self.framed_ip,
            "synced": self.synced,
        }


def tunnel_radius_status(router_name_or_user: str, *, tenant_id: int,
                         reveal_secret: bool = False) -> TunnelRadiusStatus:
    """Read-only: inspect the rtr- account's radcheck/radreply rows.

    ``reveal_secret`` controls whether the cleartext password is returned (the
    script/credentials page reveals it once; the status indicator does not)."""
    raw = (router_name_or_user or "").strip()
    username = raw if raw.startswith(TUNNEL_USER_PREFIX) else tunnel_username(raw)
    checks = freeradius_repo.list_user_check(int(tenant_id), username)
    replies = freeradius_repo.list_user_reply(int(tenant_id), username)

    cleartext = _check_value(checks, "Cleartext-Password")
    nt = _check_value(checks, "NT-Password")
    has_cleartext = cleartext is not None and cleartext != ""
    has_nt = nt is not None and nt != ""

    # Any password-like attribute that is NOT MSCHAP-usable → incompatible.
    incompatible = any(
        str(r.get("attribute") or "").endswith("-Password")
        and str(r.get("attribute") or "") not in MSCHAP_OK_ATTRS
        for r in checks
    )

    auth_type = _check_value(checks, "Auth-Type")
    disabled = (auth_type or "").strip().lower() == "reject"

    expired = False
    exp = _parse_fr_expiration(_check_value(checks, "Expiration") or "")
    if exp is not None:
        expired = exp < datetime.now(timezone.utc)

    framed_ip = _check_value(replies, "Framed-IP-Address")

    return TunnelRadiusStatus(
        username=username,
        exists=bool(checks),
        has_cleartext=has_cleartext,
        has_nt=has_nt,
        mschap_compatible=has_cleartext or has_nt,
        incompatible_secret=incompatible,
        disabled=disabled,
        expired=expired,
        framed_ip=framed_ip or None,
        cleartext=(cleartext if reveal_secret else None),
    )


@dataclass(frozen=True)
class EnsureResult:
    """Outcome of :func:`ensure_tunnel_radius_user`."""

    username: str
    tunnel_ip: ipaddress.IPv4Address
    password: str                    # the secret now in radcheck (cleartext)
    password_changed: bool           # True if a NEW password was written
    created: bool                    # True if the account did not exist before


def ensure_tunnel_radius_user(
    router_name_or_user: str, *, tenant_id: int,
    password: Optional[str] = None, cfg: Optional[MgmtTunnelConfig] = None,
) -> EnsureResult:
    """Idempotently guarantee an MSCHAP-ready rtr- account exists.

    Re-running router setup MUST NOT duplicate rows, churn the tunnel IP, or
    silently rotate a working password:

    * **IP** — reuses the account's existing Framed-IP if one is pinned;
      otherwise allocates the next stable free /32 (same as provisioning).
    * **Password** — if ``password`` is given, that value is written. If not,
      the existing Cleartext-Password is preserved; only a brand-new account
      (or one missing any usable secret) gets a freshly generated password.
    * **Secret form** — always (re)writes Cleartext-Password + NT-Password so
      an upgraded account becomes MSCHAP-compatible without operator action.

    Returns an :class:`EnsureResult` describing what changed.
    """
    raw = (router_name_or_user or "").strip()
    if not raw:
        raise RouterMgmtTunnelError("اسم الراوتر/المستخدم فارغ")
    username = raw if raw.startswith(TUNNEL_USER_PREFIX) else tunnel_username(raw)
    cfg = cfg or load_config()

    existing_checks = freeradius_repo.list_user_check(int(tenant_id), username)
    existing_reply = freeradius_repo.list_user_reply(int(tenant_id), username)
    created = not existing_checks and not existing_reply

    # Resolve the tunnel IP: keep the pinned one, else allocate a stable new one.
    framed = _check_value(existing_reply, "Framed-IP-Address")
    if framed:
        try:
            tunnel_ip = ipaddress.IPv4Address(framed.strip())
        except ValueError:
            tunnel_ip = allocate_tunnel_ip(int(tenant_id), cfg=cfg)
    else:
        tunnel_ip = allocate_tunnel_ip(int(tenant_id), cfg=cfg)

    # Resolve the password: explicit > existing cleartext > freshly generated.
    existing_cleartext = _check_value(existing_checks, "Cleartext-Password") or ""
    if password:
        new_password = password
        password_changed = (password != existing_cleartext)
    elif existing_cleartext:
        new_password = existing_cleartext
        password_changed = False
    else:
        new_password = _gen_password()
        password_changed = True

    # Preserve any non-secret check state (Auth-Type := Reject = disabled,
    # Expiration, Simultaneous-Use, Calling-Station-Id, …) so re-syncing never
    # silently re-enables a disabled account or clears an expiry. We only ever
    # rewrite the secret rows; everything else carries forward unchanged.
    # Drop ALL password-form attributes (Cleartext/NT/Crypt/SHA*/…): we rewrite
    # the MSCHAP secret authoritatively, so any pre-existing/incompatible secret
    # must not survive (that is exactly what makes reconcile "repair" work).
    preserved = [
        (r["attribute"], r["op"], r["value"])
        for r in existing_checks
        if not str(r.get("attribute") or "").endswith("-Password")
    ]
    freeradius_repo.replace_user_check(
        int(tenant_id), username,
        [
            ("Cleartext-Password", ":=", new_password),
            ("NT-Password", ":=", nt_password_attr_value(new_password)),
            *preserved,
        ],
    )
    freeradius_repo.replace_user_reply(
        int(tenant_id), username,
        [("Framed-IP-Address", ":=", str(tunnel_ip))],
    )
    checkpoint_wal()   # make rows visible to the FreeRADIUS SQLite reader
    _LOG.info(
        "v6 mgmt tunnel: ensured user=%s ip=%s created=%s pw_changed=%s",
        username, tunnel_ip, created, password_changed,
    )
    return EnsureResult(
        username=username, tunnel_ip=tunnel_ip, password=new_password,
        password_changed=password_changed, created=created,
    )


#: Diagnostic codes returned by :func:`diagnose_tunnel_login`, most-blocking
#: first. The UI maps each to a localized explanation + remediation.
DIAG_OK = "ok"
DIAG_INVALID_USER = "invalid_user"
DIAG_MISSING_SECRET = "missing_secret"
DIAG_MSCHAP_INCOMPATIBLE = "mschap_incompatible"
DIAG_DISABLED = "disabled"
DIAG_EXPIRED = "expired"
DIAG_NO_FRAMED_IP = "no_framed_ip"
DIAG_WRONG_PASSWORD = "wrong_password"


def diagnose_tunnel_login(
    router_name_or_user: str, *, tenant_id: int,
    password: Optional[str] = None,
) -> dict:
    """Backend for the "Test SSTP / RADIUS Login" button.

    Inspects the account's actual RADIUS state and returns the single most
    blocking reason an MSCHAP-v2 SSTP login would fail — deterministically,
    without sending live traffic (so it works in every deployment + tests).
    If ``password`` is supplied it is additionally checked against the stored
    Cleartext-Password (``wrong_password``).

    Returns ``{"code": <DIAG_*>, "ok": bool, "username": str,
               "status": <status dict>}``.
    """
    st = tunnel_radius_status(router_name_or_user, tenant_id=tenant_id)

    if not st.exists:
        code = DIAG_INVALID_USER
    elif st.incompatible_secret and not st.mschap_compatible:
        code = DIAG_MSCHAP_INCOMPATIBLE
    elif not st.mschap_compatible:
        code = DIAG_MISSING_SECRET
    elif st.disabled:
        code = DIAG_DISABLED
    elif st.expired:
        code = DIAG_EXPIRED
    elif not st.framed_ip:
        code = DIAG_NO_FRAMED_IP
    else:
        code = DIAG_OK

    # Password check only matters once the account is otherwise loginable.
    if code == DIAG_OK and password is not None:
        stored = tunnel_radius_status(
            router_name_or_user, tenant_id=tenant_id, reveal_secret=True
        ).cleartext or ""
        if password != stored:
            code = DIAG_WRONG_PASSWORD

    return {
        "code": code,
        "ok": code == DIAG_OK,
        "username": st.username,
        "status": st.to_dict(),
    }


# ─── Account management (enable/disable, expiry, list, reconcile) ─────────
#
# These back the dedicated SSTP/PPTP credential-management surface. Every edit
# preserves the MSCHAP secret + the fixed Framed-IP; only the targeted attribute
# changes. All are tenant-scoped and operate on the SAME radcheck/radreply store
# the live FreeRADIUS reads (tenant_id column is non-standard but the FreeRADIUS
# authorize_check_query keys on username only, so writing tenant_id=<panel tid>
# is correct as long as rtr- usernames stay globally unique — they do).

def _resolve_username(router_name_or_user: str) -> str:
    raw = (router_name_or_user or "").strip()
    return raw if raw.startswith(TUNNEL_USER_PREFIX) else tunnel_username(raw)


def _rewrite_check_attr(tenant_id: int, username: str, attribute: str,
                        value: Optional[str], *, op: str = ":=") -> None:
    """Set (``value`` given) or clear (``value is None``) a single radcheck
    attribute, preserving every other check row for the user."""
    rows = freeradius_repo.list_user_check(int(tenant_id), username)
    kept = [
        (r["attribute"], r["op"], r["value"])
        for r in rows if str(r.get("attribute") or "") != attribute
    ]
    if value is not None:
        kept.append((attribute, op, value))
    freeradius_repo.replace_user_check(int(tenant_id), username, kept)
    checkpoint_wal()   # make the change visible to the FreeRADIUS reader


def set_tunnel_enabled(router_name_or_user: str, *, tenant_id: int,
                       enabled: bool) -> None:
    """Enable/disable the account. Disable = add ``Auth-Type := Reject``
    (FreeRADIUS rejects before MSCHAP); enable = remove it. The secret stays so
    re-enabling needs no re-entry."""
    username = _resolve_username(router_name_or_user)
    _rewrite_check_attr(
        int(tenant_id), username, "Auth-Type",
        None if enabled else "Reject",
    )
    _LOG.info("v6 mgmt tunnel: set user=%s enabled=%s", username, enabled)


def set_tunnel_expiry(router_name_or_user: str, *, tenant_id: int,
                      expire_at: Optional[datetime]) -> None:
    """Set or clear the account expiry (radcheck ``Expiration`` in the format
    FreeRADIUS expects, e.g. ``31 Dec 2026 23:59:00``)."""
    username = _resolve_username(router_name_or_user)
    value = expire_at.strftime("%d %b %Y %H:%M:%S") if expire_at else None
    _rewrite_check_attr(int(tenant_id), username, "Expiration", value)
    _LOG.info("v6 mgmt tunnel: set user=%s expiry=%s", username, value or "—")


def list_tunnel_accounts(tenant_id: int) -> list[dict]:
    """Every rtr- management-tunnel account in this tenant's radcheck store,
    each enriched with its router linkage (from nas_devices) + RADIUS status.

    Drives the credential-management table. ``cleartext`` is included so the UI
    can offer a reveal toggle (the secret is MSCHAP-reversible by design)."""
    rows = db().execute(
        "SELECT DISTINCT username FROM radcheck "
        "WHERE tenant_id=? AND username LIKE ? ORDER BY username",
        (int(tenant_id), TUNNEL_USER_PREFIX + "%"),
    ).fetchall()
    # Map rtr- username → owning nas_devices row (via management_secret_ref or
    # derived name) for transport + router context.
    nas_rows = db().execute(
        "SELECT id, name, management_tunnel_type, management_secret_ref, "
        "       management_tunnel_interface_name, management_remote_address "
        "FROM nas_devices WHERE tenant_id=? "
        "  AND management_tunnel_type IN ('sstp_mgmt','pptp_mgmt') "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(tenant_id),),
    ).fetchall()
    by_user: dict[str, dict] = {}
    for n in nas_rows:
        n = dict(n)
        ref = str(n.get("management_secret_ref") or "").strip()
        key = ref if ref.startswith(TUNNEL_USER_PREFIX) else tunnel_username(n.get("name") or "")
        by_user[key] = n

    out: list[dict] = []
    for r in rows:
        username = r[0] if not isinstance(r, dict) else r.get("username")
        st = tunnel_radius_status(username, tenant_id=int(tenant_id),
                                  reveal_secret=True)
        nas = by_user.get(username, {})
        mtype = str(nas.get("management_tunnel_type") or "")
        out.append({
            "username": username,
            "transport": ("SSTP" if mtype == "sstp_mgmt"
                          else "PPTP" if mtype == "pptp_mgmt" else "—"),
            "nas_id": nas.get("id"),
            "router_name": nas.get("name") or "",
            "interface": nas.get("management_tunnel_interface_name") or "",
            "framed_ip": st.framed_ip,
            "password": st.cleartext or "",
            "status": st.to_dict(),
            "orphan": not bool(nas),   # account with no live nas_devices row
        })
    return out


@dataclass
class ReconcileReport:
    """Outcome of :func:`reconcile_tunnel_accounts` (boot-time backfill)."""

    created: list = None       # rtr- accounts that did not exist → freshly made
    repaired: list = None      # accounts that existed but were not MSCHAP-ready
    ok: list = None            # already complete → untouched

    def __post_init__(self):
        self.created = self.created or []
        self.repaired = self.repaired or []
        self.ok = self.ok or []

    @property
    def changed(self) -> int:
        return len(self.created) + len(self.repaired)

    def to_dict(self) -> dict:
        return {"created": self.created, "repaired": self.repaired,
                "ok": self.ok, "changed": self.changed}


def reconcile_tunnel_accounts(tenant_id: int,
                              cfg: Optional[MgmtTunnelConfig] = None
                              ) -> ReconcileReport:
    """One-shot, idempotent backfill: ensure every v6 SSTP/PPTP router has an
    MSCHAP-ready rtr- account in RADIUS.

    This is the permanent replacement for the manual `rtr-ccr4` SQL insert —
    run automatically at boot (see ``app/__init__._init_db``). A router that
    already has a complete, MSCHAP-compatible account is left **untouched** (no
    password churn). A router with a missing or incompatible account is
    (re)provisioned via :func:`ensure_tunnel_radius_user`; its freshly generated
    password is then visible/copyable from the credential-management UI so the
    operator can apply it on the router (we never push to the customer router).

    Safe to call repeatedly. Never raises for a single bad row — it logs and
    continues so one broken router cannot block boot.
    """
    try:
        cfg = cfg or load_config()
    except RouterMgmtTunnelError:
        # accel host/pool not configured yet → nothing to reconcile.
        return ReconcileReport()

    rows = db().execute(
        "SELECT id, name, management_secret_ref, management_tunnel_type "
        "FROM nas_devices WHERE tenant_id=? "
        "  AND management_tunnel_type IN ('sstp_mgmt','pptp_mgmt') "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(tenant_id),),
    ).fetchall()

    report = ReconcileReport()
    for row in rows:
        row = dict(row)
        ref = str(row.get("management_secret_ref") or "").strip()
        username = ref if ref.startswith(TUNNEL_USER_PREFIX) else tunnel_username(row.get("name") or "")
        if not username or username == TUNNEL_USER_PREFIX:
            continue
        try:
            st = tunnel_radius_status(username, tenant_id=int(tenant_id))
            if st.synced:
                report.ok.append(username)
                continue
            existed = st.exists
            ensure_tunnel_radius_user(username, tenant_id=int(tenant_id), cfg=cfg)
            (report.repaired if existed else report.created).append(username)
        except Exception:  # noqa: BLE001 — never let one router block boot
            _LOG.exception("reconcile: failed for user=%s", username)
    if report.changed:
        _LOG.info("v6 mgmt tunnel reconcile: created=%d repaired=%d ok=%d",
                  len(report.created), len(report.repaired), len(report.ok))
    return report


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
    checkpoint_wal()   # make the deletion visible to the FreeRADIUS reader
    if existed:
        _LOG.info("v6 mgmt tunnel: deprovisioned user=%s", username)
    return existed


__all__ = [
    "RouterMgmtTunnelError",
    "MgmtTunnelConfig",
    "TunnelProvisionResult",
    "TunnelRadiusStatus",
    "EnsureResult",
    "load_config",
    "allocate_tunnel_ip",
    "tunnel_username",
    "nt_password_hash",
    "nt_password_attr_value",
    "provision_tunnel",
    "ensure_tunnel_radius_user",
    "tunnel_radius_status",
    "diagnose_tunnel_login",
    "set_tunnel_enabled",
    "set_tunnel_expiry",
    "list_tunnel_accounts",
    "reconcile_tunnel_accounts",
    "ReconcileReport",
    "deprovision_tunnel",
    "TRANSPORT_SSTP",
    "TRANSPORT_PPTP",
    "TRANSPORTS",
    "MGMT_TYPE",
    "MSCHAP_OK_ATTRS",
    "SSTP_IFACE_NAME",
    "PPTP_IFACE_NAME",
    "PPTP_PORT",
    "DIAG_OK",
    "DIAG_INVALID_USER",
    "DIAG_MISSING_SECRET",
    "DIAG_MSCHAP_INCOMPATIBLE",
    "DIAG_DISABLED",
    "DIAG_EXPIRED",
    "DIAG_NO_FRAMED_IP",
    "DIAG_WRONG_PASSWORD",
]
