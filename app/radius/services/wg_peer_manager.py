"""M1 — WireGuard peer manager.

Owns everything the Flask app does to provision and de-provision
WireGuard peers on the host's wg0 interface — without ever
touching `/etc/wireguard/wg0.conf` directly.

The split control plane (set up in M0c..M0e):

  HobeRadius container  ──writes──>  /etc/hoberadius/wg-peers.d/*.conf
                                            │
                                            ▼   (systemd path-unit)
                                     wg-reload.path
                                            │
                                            ▼
                                     wg-reload.service
                                            │
                                            ▼   (`wg set wg0 peer ...`)
                                       live wg0 interface

This module's job is just the top arrow: write one peer file per
router, with the right credentials + a freshly-allocated tunnel
IP, and let the host-side reloader push it onto the wire.

Public surface used by the L3 / M2 wizard:

    provision_peer(name)                  -> dict
        full pipeline: keypair + IP + write peer file. Returns
        everything the wizard needs to render the RouterOS
        script and write the matching nas_devices row.

    deprovision_peer(name)                -> None
        deletes the peer file (the host reloader picks up the
        delete and removes the peer from wg0).

    list_managed_peers()                  -> list[dict]
        scans peers.d/*.conf and returns parsed metadata. Useful
        for the Operations Center to show 'auto-provisioned via
        wizard' badges + IP allocation status.
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


_LOG = logging.getLogger(__name__)


# ─── Configuration (read once per call from env) ─────────────────


PEERS_DIR_ENV = "HOBERADIUS_WG_PEERS_DIR"
PEERS_DIR_DEFAULT = "/etc/hoberadius/wg-peers.d"

SUBNET_ENV = "HOBERADIUS_WG_SUBNET"
SUBNET_DEFAULT = "10.10.0.0/24"

SERVER_IP_ENV = "HOBERADIUS_WG_SERVER_IP"
SERVER_IP_DEFAULT = "10.10.0.1"

SERVER_PUBKEY_ENV = "HOBERADIUS_WG_SERVER_PUBKEY"
SERVER_ENDPOINT_ENV = "HOBERADIUS_WG_SERVER_ENDPOINT"
INTERFACE_ENV = "HOBERADIUS_WG_INTERFACE"
INTERFACE_DEFAULT = "wg0"

DEFAULT_KEEPALIVE_SEC = 25


@dataclass(frozen=True)
class WgConfig:
    """Server-side knobs the wizard's WG block depends on."""

    peers_dir: Path
    subnet: ipaddress.IPv4Network
    server_ip: ipaddress.IPv4Address
    server_pubkey: str
    server_endpoint: str
    interface: str


def load_config() -> WgConfig:
    """Pull settings from the environment.

    Raises ValueError when a required field is missing rather than
    silently falling back — a wizard run that would produce
    unusable output should error early with a clear message.
    """
    peers_dir = Path(os.environ.get(PEERS_DIR_ENV) or PEERS_DIR_DEFAULT)
    subnet_str = (os.environ.get(SUBNET_ENV) or SUBNET_DEFAULT).strip()
    server_ip_str = (os.environ.get(SERVER_IP_ENV) or SERVER_IP_DEFAULT).strip()
    server_pubkey = (os.environ.get(SERVER_PUBKEY_ENV) or "").strip()
    server_endpoint = (os.environ.get(SERVER_ENDPOINT_ENV) or "").strip()
    interface = (os.environ.get(INTERFACE_ENV) or INTERFACE_DEFAULT).strip()

    try:
        subnet = ipaddress.ip_network(subnet_str, strict=False)
    except ValueError as exc:
        raise ValueError(
            f"{SUBNET_ENV}={subnet_str!r} is not a valid IPv4 network: {exc}"
        ) from exc
    if not isinstance(subnet, ipaddress.IPv4Network):
        raise ValueError(f"only IPv4 subnets supported, got {subnet_str!r}")

    try:
        server_ip = ipaddress.IPv4Address(server_ip_str)
    except ValueError as exc:
        raise ValueError(
            f"{SERVER_IP_ENV}={server_ip_str!r} is not a valid IPv4 address: {exc}"
        ) from exc
    if server_ip not in subnet:
        raise ValueError(
            f"server IP {server_ip} is outside subnet {subnet}"
        )

    if not server_pubkey:
        raise ValueError(
            f"{SERVER_PUBKEY_ENV} is not set — populate it from "
            "/etc/wireguard/server_public.key on the host."
        )
    if not server_endpoint:
        raise ValueError(
            f"{SERVER_ENDPOINT_ENV} is not set — should look like "
            "'<public-ip>:<listen-port>' (e.g. '187.77.70.18:51820')."
        )

    return WgConfig(
        peers_dir=peers_dir,
        subnet=subnet,
        server_ip=server_ip,
        server_pubkey=server_pubkey,
        server_endpoint=server_endpoint,
        interface=interface,
    )


# ─── Keypair generation (X25519, libsodium-equivalent) ───────────


def generate_keypair() -> tuple[str, str]:
    """Return `(private_b64, public_b64)`.

    44 characters each (32 raw bytes → base64). Compatible with the
    keys `wg genkey` / `wg pubkey` produce.
    """
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(priv_bytes).decode("ascii"),
        base64.b64encode(pub_bytes).decode("ascii"),
    )


# ─── peers.d I/O ─────────────────────────────────────────────────


# Peer file names map 1:1 to router names. Restrict the charset so
# operator-provided names can't escape the peers.d directory or
# clash with shell glob patterns.
_PEER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _slugify_router_name(name: str) -> str:
    """Turn a free-form router name into a safe filename stem."""
    cleaned = re.sub(r"\s+", "-", (name or "").strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", cleaned)
    cleaned = cleaned.strip("-._")
    if not cleaned:
        raise ValueError("اسم الراوتر فارغ بعد التنظيف")
    if not _PEER_NAME_RE.match(cleaned):
        raise ValueError(
            f"اسم الراوتر بعد التنظيف لا يتطابق مع النمط: {cleaned!r}"
        )
    return cleaned


def _peer_path(cfg: WgConfig, slug: str) -> Path:
    return cfg.peers_dir / f"{slug}.conf"


_FIELD_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z]+)\s*=\s*(?P<val>.+?)\s*$"
)


def parse_peer_file(path: Path) -> dict:
    """Read one peer fragment back into a dict.

    Tolerates comment lines (starting with '#') and the leading
    `[Peer]` header. Unknown fields are still returned in the
    result dict so callers can introspect (e.g. an `Endpoint`
    written by hand).
    """
    out: dict = {"path": str(path), "name": path.stem}
    text = path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        m = _FIELD_RE.match(line)
        if not m:
            continue
        out[m.group("key")] = m.group("val")
    return out


def list_managed_peers(cfg: Optional[WgConfig] = None) -> list[dict]:
    """Return every peer file in peers.d as a parsed dict.

    Ignores hidden files (starting with `.`). Returns an empty
    list if the directory doesn't exist yet — the wizard may run
    before init-wg-reloader.
    """
    cfg = cfg or load_config()
    if not cfg.peers_dir.is_dir():
        return []
    rows: list[dict] = []
    for child in sorted(cfg.peers_dir.iterdir()):
        if child.is_file() and child.suffix == ".conf" and not child.name.startswith("."):
            try:
                rows.append(parse_peer_file(child))
            except OSError as exc:
                _LOG.warning("skipped unreadable peer file %s: %s", child, exc)
    return rows


# ─── IP allocation ───────────────────────────────────────────────


def _used_ips_from_peers(peers: Iterable[dict]) -> set[ipaddress.IPv4Address]:
    used: set[ipaddress.IPv4Address] = set()
    for p in peers:
        raw = (p.get("AllowedIPs") or "").strip()
        if not raw:
            continue
        # AllowedIPs may carry multiple comma-separated entries; the
        # first /32 is the peer's address.
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                net = ipaddress.ip_network(piece, strict=False)
            except ValueError:
                continue
            if isinstance(net, ipaddress.IPv4Network) and net.prefixlen == 32:
                used.add(net.network_address)
                break
    return used


def allocate_next_ip(cfg: Optional[WgConfig] = None) -> ipaddress.IPv4Address:
    """Pick the first /32 host that isn't the server itself and
    isn't claimed by any existing peer file."""
    cfg = cfg or load_config()
    reserved = {cfg.server_ip}
    reserved |= _used_ips_from_peers(list_managed_peers(cfg))

    for candidate in cfg.subnet.hosts():
        if candidate not in reserved:
            return candidate
    raise RuntimeError(
        f"subnet {cfg.subnet} is exhausted — every host /32 is taken"
    )


# ─── Provision / deprovision ─────────────────────────────────────


def _render_peer_block(
    *,
    router_name: str,
    public_key: str,
    allowed_ip: ipaddress.IPv4Address,
    keepalive_sec: int,
) -> str:
    """One peer fragment in the exact shape `wg set` consumes.

    Comments include the operator-visible name so an admin SSH'd
    into the VPS can read peers.d/*.conf and identify peers
    without round-tripping to the DB.
    """
    return (
        f"[Peer]\n"
        f"# HobeRadius — auto-provisioned for router: {router_name}\n"
        f"PublicKey = {public_key}\n"
        f"AllowedIPs = {allowed_ip}/32\n"
        f"PersistentKeepalive = {keepalive_sec}\n"
    )


@dataclass(frozen=True)
class ProvisionResult:
    """What `provision_peer` returns. Everything the wizard needs to
    write the nas_devices row AND render the RouterOS script."""

    router_name: str
    slug: str
    peer_file: Path
    router_private_key: str
    router_public_key: str
    allowed_ip: ipaddress.IPv4Address
    server_pubkey: str
    server_endpoint: str
    server_ip_in_tunnel: ipaddress.IPv4Address
    subnet: ipaddress.IPv4Network
    interface: str
    keepalive_sec: int

    def to_dict(self) -> dict:
        return {
            "router_name": self.router_name,
            "slug": self.slug,
            "peer_file": str(self.peer_file),
            "router_private_key": self.router_private_key,
            "router_public_key": self.router_public_key,
            "allowed_ip": str(self.allowed_ip),
            "server_pubkey": self.server_pubkey,
            "server_endpoint": self.server_endpoint,
            "server_ip_in_tunnel": str(self.server_ip_in_tunnel),
            "subnet": str(self.subnet),
            "interface": self.interface,
            "keepalive_sec": self.keepalive_sec,
        }


def provision_peer(
    router_name: str,
    *,
    keepalive_sec: int = DEFAULT_KEEPALIVE_SEC,
    cfg: Optional[WgConfig] = None,
) -> ProvisionResult:
    """End-to-end: generate keypair, allocate IP, write peer file.

    Raises ValueError if the name is unusable or if a peer file
    with the same slug already exists (we don't overwrite — the
    operator should either pick a different name or call
    `deprovision_peer(slug)` first).
    """
    cfg = cfg or load_config()
    slug = _slugify_router_name(router_name)
    target = _peer_path(cfg, slug)
    if target.exists():
        raise ValueError(
            f"peer file already exists: {target} — pick a different name "
            "or remove the existing peer first"
        )

    cfg.peers_dir.mkdir(parents=True, exist_ok=True)

    priv, pub = generate_keypair()
    allowed_ip = allocate_next_ip(cfg)
    block = _render_peer_block(
        router_name=router_name,
        public_key=pub,
        allowed_ip=allowed_ip,
        keepalive_sec=keepalive_sec,
    )

    # Atomic write: stage to a sibling tempfile + rename, so the
    # systemd path-unit doesn't observe a partial write halfway
    # through.
    staging = target.with_suffix(target.suffix + ".tmp")
    staging.write_text(block, encoding="utf-8")
    try:
        os.chmod(staging, 0o660)
    except OSError:
        # Best-effort — the bind-mount may not allow chmod
        pass
    staging.replace(target)

    _LOG.info(
        "wg: provisioned peer name=%s ip=%s file=%s",
        router_name, allowed_ip, target,
    )

    return ProvisionResult(
        router_name=router_name,
        slug=slug,
        peer_file=target,
        router_private_key=priv,
        router_public_key=pub,
        allowed_ip=allowed_ip,
        server_pubkey=cfg.server_pubkey,
        server_endpoint=cfg.server_endpoint,
        server_ip_in_tunnel=cfg.server_ip,
        subnet=cfg.subnet,
        interface=cfg.interface,
        keepalive_sec=keepalive_sec,
    )


def deprovision_peer(
    slug_or_name: str, *, cfg: Optional[WgConfig] = None,
) -> bool:
    """Delete the peer file. Returns True if the file existed.

    Accepts either the slug (already cleaned) or a raw name —
    `_slugify_router_name` is idempotent for already-clean slugs.
    """
    cfg = cfg or load_config()
    try:
        slug = _slugify_router_name(slug_or_name)
    except ValueError:
        return False
    target = _peer_path(cfg, slug)
    if not target.exists():
        return False
    target.unlink()
    _LOG.info("wg: deprovisioned peer slug=%s file=%s", slug, target)
    return True


__all__ = [
    "WgConfig",
    "load_config",
    "generate_keypair",
    "parse_peer_file",
    "list_managed_peers",
    "allocate_next_ip",
    "ProvisionResult",
    "provision_peer",
    "deprovision_peer",
    "DEFAULT_KEEPALIVE_SEC",
]
