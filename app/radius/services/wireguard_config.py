"""K1.4 — WireGuard config generator for new MT peers.

The operator hits a "Generate router config" button on the NAS
form; this module produces the two text blocks they paste:

1. A **server-side** peer stanza they drop into the VPS's
   `/etc/wireguard/wg0.conf` (or pass to `wg set wg0 peer …`).
2. A **router-side** RouterOS script the operator pastes into
   the MikroTik terminal — adds the interface, the peer, and an
   IP on the WG subnet so the radius admin can dial back.

Both use:
- The next available IP in the VPS's WG subnet (queried from the
  existing `nas_devices` table to avoid collisions).
- A freshly-generated key pair (server-side); the router-side
  block also generates its own key (`mikrotik.wireguard add`).

This module ONLY produces the text. Applying it to the VPS or
the router is the operator's job — keeps the radius admin away
from any side-effect on host-level config.
"""
from __future__ import annotations

import secrets
import textwrap
from dataclasses import dataclass
from typing import Iterable

# Defaults the operator can override via env vars or admin UI.
DEFAULT_WG_INTERFACE = "wg0"
DEFAULT_WG_SUBNET = "10.10.0.0/24"
DEFAULT_WG_SERVER_IP = "10.10.0.1"
DEFAULT_WG_LISTEN_PORT = 51820
DEFAULT_KEEPALIVE_SEC = 25


@dataclass(frozen=True)
class WireGuardConfig:
    """Bundle the operator pastes — server block + router block + the
    chosen peer IP (so the admin can pre-fill `vpn_peer_address`)."""

    server_block: str
    router_block: str
    peer_ip: str
    interface: str
    note: str = ""


def next_peer_ip(
    used_ips: Iterable[str],
    *,
    subnet: str = DEFAULT_WG_SUBNET,
    server_ip: str = DEFAULT_WG_SERVER_IP,
) -> str:
    """Find the first unused /32 inside the WG subnet.

    Skips `.0` (network), `.255` (broadcast) and `server_ip`. The
    caller passes already-assigned peer IPs (from
    `nas_devices.vpn_assigned_ip` rows).
    """
    base = subnet.split("/")[0]
    parts = base.split(".")
    if len(parts) != 4:
        raise ValueError(f"unsupported subnet shape: {subnet!r}")
    prefix = ".".join(parts[:3])
    server_octet = int(server_ip.split(".")[-1])
    taken = {
        ip.split("/")[0].strip() for ip in used_ips if ip
    }
    for octet in range(2, 255):
        if octet == server_octet:
            continue
        candidate = f"{prefix}.{octet}"
        if candidate == base:
            continue
        if candidate in taken:
            continue
        return candidate
    raise RuntimeError(f"no free IPs left in {subnet}")


def generate_router_config(
    *,
    nas_name: str,
    peer_ip: str,
    server_public_key: str,
    server_endpoint: str,
    server_port: int = DEFAULT_WG_LISTEN_PORT,
    allowed_subnet: str = DEFAULT_WG_SUBNET,
    interface_name: str = "wg-radius",
    listen_port: int = 13231,
    keepalive_sec: int = DEFAULT_KEEPALIVE_SEC,
) -> str:
    """RouterOS-7 script the operator pastes into the MikroTik
    terminal. Adds a WG interface, the radius VPS as a peer, and an
    IP on the WG subnet so outbound API calls have a return path.

    The router generates its OWN key pair when `/interface/wireguard
    add` runs — the operator copies the printed public key back to
    the admin's `vpn_public_key` field.
    """
    return textwrap.dedent(f"""\
        # ── HobeRadius VPN setup for: {nas_name}
        # Paste this whole block into RouterOS 7 terminal. The router
        # will generate its own key pair — copy the resulting public
        # key back to the admin (NAS form → vpn_public_key).

        /interface/wireguard add \\
            name={interface_name} \\
            listen-port={listen_port} \\
            comment="HobeRadius admin tunnel"

        /interface/wireguard/peers add \\
            interface={interface_name} \\
            public-key="{server_public_key}" \\
            endpoint-address={server_endpoint} \\
            endpoint-port={server_port} \\
            allowed-address={allowed_subnet} \\
            persistent-keepalive={keepalive_sec}s \\
            comment="HobeRadius admin"

        /ip/address add \\
            interface={interface_name} \\
            address={peer_ip}/24 \\
            comment="HobeRadius admin tunnel"

        # Verify connectivity:
        :put [/interface/wireguard get [find name={interface_name}] public-key]
        /ping {DEFAULT_WG_SERVER_IP} count=3
    """).strip() + "\n"


def generate_server_block(
    *,
    nas_name: str,
    peer_ip: str,
    router_public_key: str,
) -> str:
    """Server-side peer stanza for `/etc/wireguard/wg0.conf`.

    The operator either appends this to wg0.conf and reloads, or
    passes the same values to `wg set wg0 peer <pub> allowed-ips
    <ip>/32` for a live add without restart.
    """
    return textwrap.dedent(f"""\
        # ── HobeRadius peer: {nas_name}
        [Peer]
        # AllowedIPs MUST be a /32 — one peer per router, no overlap.
        PublicKey = {router_public_key}
        AllowedIPs = {peer_ip}/32

        # Live add (no restart needed):
        #   wg set {DEFAULT_WG_INTERFACE} peer {router_public_key} allowed-ips {peer_ip}/32
    """).strip() + "\n"


def build_for_new_peer(
    *,
    nas_name: str,
    used_peer_ips: Iterable[str],
    server_public_key: str,
    server_endpoint: str,
    router_public_key: str = "",
    subnet: str = DEFAULT_WG_SUBNET,
    server_ip: str = DEFAULT_WG_SERVER_IP,
    server_port: int = DEFAULT_WG_LISTEN_PORT,
    interface: str = DEFAULT_WG_INTERFACE,
) -> WireGuardConfig:
    """Top-level helper.

    Caller passes the existing peer IPs (from the DB) and the
    server's WireGuard public key (which the VPS admin set up
    once, stored e.g. in env var `HOBERADIUS_WG_SERVER_PUBKEY`).

    Returns the combined config bundle. The router's own public
    key is filled in AFTER the operator runs the router-side
    block and pastes the result back.
    """
    peer_ip = next_peer_ip(
        used_peer_ips, subnet=subnet, server_ip=server_ip,
    )
    router_block = generate_router_config(
        nas_name=nas_name,
        peer_ip=peer_ip,
        server_public_key=server_public_key,
        server_endpoint=server_endpoint,
        server_port=server_port,
        allowed_subnet=subnet,
    )
    note = (
        ""
        if router_public_key
        else "بعد تشغيل block الراوتر، انسخ المفتاح العام الناتج "
             "وألصقه في حقل vpn_public_key ثم احفظ — هذا يكمل "
             "إعداد الـ peer على الخادم."
    )
    server_block = generate_server_block(
        nas_name=nas_name,
        peer_ip=peer_ip,
        router_public_key=router_public_key or "<router public key here>",
    )
    return WireGuardConfig(
        server_block=server_block,
        router_block=router_block,
        peer_ip=peer_ip,
        interface=interface,
        note=note,
    )


def generate_server_keypair() -> tuple[str, str]:
    """Return (private_key, public_key) — both 32 bytes base64.

    Used the very first time the operator sets up the WG server.
    After that the server keys are stable; only per-router peer
    keys change.

    Implementation note: we ship a pure-Python placeholder using
    `secrets.token_bytes` for the seed + curve25519 if available.
    On a real VPS the operator should still run `wg genkey | tee …
    | wg pubkey` once and stash the result in env — this helper
    is for the bootstrap UI only.
    """
    import base64

    # Try the dependency-free path first (PyNaCl ships curve25519).
    try:
        from nacl.public import PrivateKey  # type: ignore
        priv = PrivateKey.generate()
        return (
            base64.b64encode(bytes(priv)).decode("ascii"),
            base64.b64encode(bytes(priv.public_key)).decode("ascii"),
        )
    except Exception:
        # Random-bytes fallback. NOT a valid WG keypair, but it
        # gives the operator something to paste in dev/test
        # without requiring PyNaCl.
        seed = secrets.token_bytes(32)
        return (
            base64.b64encode(seed).decode("ascii"),
            base64.b64encode(b"\x00" * 32).decode("ascii"),
        )
