"""WireGuard management-tunnel surface — the v7 parallel to
``router_mgmt_tunnel`` (which owns v6 SSTP/PPTP over RADIUS).

v7 RouterOS routers reach the panel over a **WireGuard** management
tunnel: the panel writes one peer file per router under
``wg-peers.d`` (host-side ``wg0``) and stores the router's tunnel IP +
public key on its ``nas_devices`` row. There is **no RADIUS account**
for a WireGuard management tunnel (auth is the WG key exchange itself),
so this module deliberately offers a *different* action set than the
SSTP page — no password reveal, no MSCHAP diagnostics.

What is REAL here (surfaced honestly):
  * router name, tunnel IP (``vpn_peer_address`` / ``address``),
    public key, interface — from ``nas_devices`` (always available).
  * peer-provisioned-on-server — derived by matching the row's tunnel
    IP against the ``/32`` AllowedIPs in the host's peer files. Returns
    ``None`` (→ "غير متاح") when the peers dir can't be read (e.g. a dev
    box), never a false "no peer".
  * reachability over the tunnel — the same honest ``last_check_status``
    TCP probe the Operations Center uses.
  * server settings (endpoint/subnet/pubkey/interface) — from env.

What is PENDING (shown as an honest "not collected yet", never faked):
  * live WireGuard handshake / last-seen. The ``vpn_last_handshake_ts``
    column exists but nothing polls ``wg show`` yet, so it stays 0.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core import env_settings
from ..db.connection import db, transaction
from . import wg_peer_manager as wpm

_LOG = logging.getLogger(__name__)

#: management_tunnel_type values that mark a v6 (SSTP/PPTP) row — excluded.
_V6_MGMT_TYPES = ("sstp_mgmt", "pptp_mgmt")


# ─── peer-file presence (host-side source of truth) ──────────────────


def provisioned_peer_ips() -> "set[str] | None":
    """Tunnel ``/32`` addresses that have a WireGuard peer file.

    Returns ``None`` when the peers dir can't be read (e.g. a dev box
    without ``wg-peers.d``) so callers never falsely claim "no peer".
    """
    try:
        peers_dir = Path(env_settings.env(wpm.PEERS_DIR_ENV) or wpm.PEERS_DIR_DEFAULT)
        if not peers_dir.is_dir():
            return None
        ips: "set[str]" = set()
        for child in sorted(peers_dir.iterdir()):
            if not (child.is_file() and child.suffix == ".conf"
                    and not child.name.startswith(".")):
                continue
            try:
                parsed = wpm.parse_peer_file(child)
            except OSError:
                continue
            for piece in str(parsed.get("AllowedIPs") or "").split(","):
                piece = piece.strip()
                if piece:
                    ips.add(piece.split("/")[0])
        return ips
    except Exception:  # noqa: BLE001 — presence is best-effort, never fatal
        return None


# ─── server settings (env) ───────────────────────────────────────────


@dataclass(frozen=True)
class ServerInfo:
    """What the panel knows about the WireGuard server side."""

    configured: bool
    endpoint: str = ""
    subnet: str = ""
    interface: str = ""
    server_pubkey: str = ""
    error: str = ""


def server_info() -> ServerInfo:
    """Best-effort read of the WG server settings from the environment.

    Never raises — an unconfigured host yields ``configured=False`` with
    a human message so the page can show an honest "server not configured
    yet" banner instead of a 500.
    """
    try:
        cfg = wpm.load_config()
    except ValueError as exc:
        return ServerInfo(configured=False, error=str(exc))
    return ServerInfo(
        configured=True,
        endpoint=cfg.server_endpoint,
        subnet=str(cfg.subnet),
        interface=cfg.interface,
        server_pubkey=cfg.server_pubkey,
    )


# ─── honest per-peer status ──────────────────────────────────────────


def _peer_status(row: dict, has_peer: "bool | None") -> dict:
    """Derive an honest status pill for one WG-managed router.

    Mirrors the Operations Center logic but WG-specific: peer-file
    presence + the real TCP reachability probe. Never invents handshake
    data (not collected yet).
    """
    check = str(row.get("last_check_status") or "").strip().lower()
    if not bool(row.get("enabled", 1)):
        return {"state": "disabled", "color": "amber", "label": "معطّل",
                "reason": "الراوتر معطّل في اللوحة."}
    if check == "reachable":
        return {"state": "active", "color": "green", "label": "متّصل",
                "reason": "الراوتر يُجاب عبر نفق WireGuard (آخر فحص ناجح)."}
    if check in ("timeout", "unreachable"):
        return {"state": "down", "color": "red", "label": "لا يستجيب",
                "reason": "تعذّر الوصول إلى الراوتر عبر النفق في آخر فحص."}
    if has_peer is True:
        return {"state": "ready", "color": "green", "label": "Peer جاهز",
                "reason": "أُنشئ peer على الخادم لهذا الراوتر."}
    if has_peer is False:
        return {"state": "no_peer", "color": "red", "label": "لا Peer",
                "reason": "لم يُنشأ peer على الخادم بعد — أعِد توليد المفاتيح أو شغّل المعالج."}
    # peers dir unreadable → honestly unknown
    return {"state": "unknown", "color": "grey", "label": "لم يُفحص",
            "reason": "لم يُختبر النفق بعد، وتعذّر التحقّق من peer الخادم."}


# ─── list / fetch ────────────────────────────────────────────────────


_SELECT_COLS = (
    "id, name, address, enabled, ros_version, connection_mode, "
    "vpn_peer_address, vpn_public_key, vpn_interface, vpn_last_handshake_ts, "
    "management_tunnel_type, last_check_status, last_check_at"
)


def _is_wg_row(row: dict) -> bool:
    """A router managed over a WireGuard tunnel.

    IMPORTANT: do NOT require ros_version=='7'. The real creation path
    (setup_wizard_v3) inserts the nas_devices row WITHOUT ros_version, so it
    stays '' — the old `ros.startswith("7")` check silently hid every
    wizard-provisioned (i.e. every live) WG router. The reliable
    discriminator is: it is NOT a v6 SSTP/PPTP tunnel, AND it carries a
    WireGuard signal (connection_mode='vpn', or a WG public key, or a wg*
    interface). v6 tunnels also use connection_mode='vpn' but always set
    management_tunnel_type to sstp_mgmt/pptp_mgmt, so they're excluded first."""
    mtype = str(row.get("management_tunnel_type") or "").strip().lower()
    if mtype in _V6_MGMT_TYPES:
        return False
    mode = str(row.get("connection_mode") or "").strip().lower()
    has_pub = bool(str(row.get("vpn_public_key") or "").strip())
    iface = str(row.get("vpn_interface") or "").strip().lower()
    return mode == "vpn" or has_pub or iface.startswith("wg")


def _enrich_peer(row: dict, peer_ips: "set[str] | None", endpoint: str) -> dict:
    """Turn one ``nas_devices`` row into the honest per-peer dict the WG pages
    render. Shared by the list and the per-router details page so both surfaces
    show identical facts."""
    tunnel_ip = (str(row.get("vpn_peer_address") or "").strip()
                 or str(row.get("address") or "").strip())
    has_peer = (tunnel_ip in peer_ips) if (peer_ips is not None and tunnel_ip) else None
    pub = str(row.get("vpn_public_key") or "").strip()
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "tunnel_ip": tunnel_ip,
        # AllowedIPs the server pins for this peer (its /32 inside wg0).
        "allowed_ips": (tunnel_ip + "/32") if tunnel_ip else "",
        "public_key": pub,
        "public_key_short": (pub[:10] + "…" + pub[-6:]) if len(pub) > 20 else pub,
        # The private key is NEVER stored (it belongs on the router); it is
        # only revealed ONCE at regenerate time via the setup script. So
        # there is no stored private key to reveal here — the UI flags this
        # honestly instead of faking a value.
        "has_private_key": False,
        "interface": str(row.get("vpn_interface") or "wg0"),
        # The server endpoint the router dials (same for every peer).
        "endpoint": endpoint,
        "enabled": bool(row.get("enabled", 1)),
        "has_peer": has_peer,
        "last_handshake_collected": False,  # PENDING: wg show not polled yet
        "last_check_status": str(row.get("last_check_status") or ""),
        "last_check_at": str(row.get("last_check_at") or ""),
        "status": _peer_status(row, has_peer),
    }


def list_mgmt_peers(tenant_id: int) -> list[dict]:
    """Every router whose management tunnel is WireGuard, enriched with
    peer-file presence + honest status. No mock data.

    The SQL is broad (any non-v6 row that carries a WireGuard signal); the
    precise call is made by :func:`_is_wg_row`. This is what makes a live
    wizard-provisioned WG router actually appear (its ros_version is blank)."""
    rows = db().execute(
        f"SELECT {_SELECT_COLS} FROM nas_devices "
        "WHERE tenant_id=? "
        "  AND (management_tunnel_type IS NULL "
        "       OR management_tunnel_type NOT IN ('sstp_mgmt','pptp_mgmt')) "
        "  AND (connection_mode='vpn' "
        "       OR (vpn_public_key IS NOT NULL AND vpn_public_key<>'') "
        "       OR vpn_interface LIKE 'wg%') "
        "  AND (deleted_at IS NULL OR deleted_at='') "
        "ORDER BY name COLLATE NOCASE",
        (int(tenant_id),),
    ).fetchall()
    peer_ips = provisioned_peer_ips()
    srv = server_info()
    endpoint = srv.endpoint if srv.configured else ""
    out: list[dict] = []
    for r in rows:
        row = dict(r)
        if not _is_wg_row(row):
            continue
        out.append(_enrich_peer(row, peer_ips, endpoint))
    return out


def get_wg_nas(tenant_id: int, nas_id: int) -> "dict | None":
    """Fetch one WG-managed router row (or ``None`` if it isn't one)."""
    r = db().execute(
        f"SELECT {_SELECT_COLS} FROM nas_devices "
        "WHERE id=? AND tenant_id=? AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), int(tenant_id)),
    ).fetchone()
    if not r:
        return None
    row = dict(r)
    return row if _is_wg_row(row) else None


def get_mgmt_peer(tenant_id: int, nas_id: int) -> "dict | None":
    """The single-peer parallel to :func:`list_mgmt_peers` — the enriched,
    honest dict for ONE WG-managed router (or ``None`` if the id isn't a
    WireGuard-managed router for this tenant). Backs the per-router details
    page (the WG sibling of the SSTP credentials page)."""
    row = get_wg_nas(tenant_id, nas_id)
    if not row:
        return None
    srv = server_info()
    endpoint = srv.endpoint if srv.configured else ""
    return _enrich_peer(row, provisioned_peer_ips(), endpoint)


# ─── actions ─────────────────────────────────────────────────────────


class WireguardMgmtError(RuntimeError):
    """Raised when a WG management action can't complete (host/env)."""


def regenerate_peer(tenant_id: int, nas_id: int) -> str:
    """Rotate this router's WireGuard keypair on the server and return
    the fresh **private** key (one-time reveal — it belongs on the
    router, never in our DB).

    Re-writes the host peer file (new public key) and updates the row's
    public key + tunnel IP. Raises :class:`WireguardMgmtError` when the
    WG server env isn't configured or the peers dir can't be written.
    """
    row = get_wg_nas(tenant_id, nas_id)
    if not row:
        raise WireguardMgmtError("ليس راوتر WireGuard مُدار.")
    name = str(row.get("name") or "").strip()
    try:
        cfg = wpm.load_config()
    except ValueError as exc:
        raise WireguardMgmtError(
            "إعدادات خادم WireGuard غير مُهيّأة في البيئة: " + str(exc)) from exc
    try:
        # free the old peer file so the IP can be reclaimed, then write a
        # fresh keypair + allocation. The host reloader picks both up.
        wpm.deprovision_peer(name, cfg=cfg)
        res = wpm.provision_peer(name, cfg=cfg)
    except (OSError, ValueError, RuntimeError) as exc:
        raise WireguardMgmtError("تعذّر كتابة peer على الخادم: " + str(exc)) from exc

    new_ip = str(res.allowed_ip)
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET vpn_public_key=?, vpn_peer_address=?, "
            "       vpn_interface=?, address=? WHERE id=? AND tenant_id=?",
            (res.router_public_key, new_ip, res.interface, new_ip,
             int(nas_id), int(tenant_id)),
        )
        # keep the FreeRADIUS `nas` mirror's WG columns consistent
        c.execute(
            "UPDATE nas SET vpn_public_key=?, vpn_peer_address=? "
            "WHERE tenant_id=? AND nasname=?",
            (res.router_public_key, new_ip, int(tenant_id), str(row.get("address") or "")),
        )
    _LOG.info("wg-mgmt: regenerated keys for router id=%s name=%s ip=%s",
              nas_id, name, new_ip)
    return res.router_private_key


def remove_peer(tenant_id: int, nas_id: int) -> bool:
    """Remove this router's WireGuard peer from the server (``wg0``).

    The router row stays in inventory; only the host peer file is
    deleted (the tunnel drops until the peer is re-provisioned). Returns
    True if a peer file existed. Best-effort on the env read.
    """
    row = get_wg_nas(tenant_id, nas_id)
    if not row:
        raise WireguardMgmtError("ليس راوتر WireGuard مُدار.")
    name = str(row.get("name") or "").strip()
    try:
        removed = wpm.deprovision_peer(name)
    except ValueError as exc:
        raise WireguardMgmtError("تعذّر إزالة peer: " + str(exc)) from exc
    # forget the cached public key so the row reflects "no peer"
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET vpn_public_key='' WHERE id=? AND tenant_id=?",
            (int(nas_id), int(tenant_id)),
        )
    _LOG.info("wg-mgmt: removed peer for router id=%s name=%s removed=%s",
              nas_id, name, removed)
    return bool(removed)


__all__ = [
    "ServerInfo", "WireguardMgmtError",
    "provisioned_peer_ips", "server_info", "list_mgmt_peers",
    "get_wg_nas", "get_mgmt_peer", "regenerate_peer", "remove_peer",
]
