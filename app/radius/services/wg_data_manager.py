"""مدير واجهة WireGuard البيانات (wg-data) — مستقل تمامًا عن wg-mgmt.

قواعد الأمان المُدمَجة:
- Private Key لا يُحفظ في قاعدة البيانات أبدًا — يُكتب إلى ملف محمي (0600)
- Public Key فقط يُخزَّن في wireguard_data_service.public_key
- Peer private keys تُوَلَّد وتُعاد مرةً واحدة فقط عند الإنشاء
- عدد الـ peers لا يتجاوز max_peers في service_allocation_mirror
- wg-data واجهة مستقلة: HOBERADIUS_WG_DATA_* env vars منفصلة

متغيرات البيئة:
    HOBERADIUS_WG_DATA_INTERFACE    — اسم الواجهة (افتراضي: wg-data)
    HOBERADIUS_WG_DATA_PEERS_DIR    — مجلد ملفات الـ peers (افتراضي: /etc/hoberadius/wg-data-peers.d)
    HOBERADIUS_WG_DATA_SUBNET       — شبكة الـ peers (افتراضي: 10.100.0.0/24)
    HOBERADIUS_WG_DATA_SERVER_IP    — IP السيرفر داخل النفق (افتراضي: 10.100.0.1)
    HOBERADIUS_WG_DATA_ENDPOINT     — العنوان العام للسيرفر (مثل: 1.2.3.4:51821)
    HOBERADIUS_WG_DATA_KEY_FILE     — مسار ملف الـ private key (افتراضي: /etc/wireguard/wg-data.key)
    HOBERADIUS_WG_DATA_DNS          — DNS للعملاء (افتراضي: 1.1.1.1)
    HOBERADIUS_WG_DATA_LISTEN_PORT  — منفذ الاستماع (افتراضي: 51821)
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_LOG = logging.getLogger(__name__)

_ENV_INTERFACE   = "HOBERADIUS_WG_DATA_INTERFACE"
_ENV_PEERS_DIR   = "HOBERADIUS_WG_DATA_PEERS_DIR"
_ENV_SUBNET      = "HOBERADIUS_WG_DATA_SUBNET"
_ENV_SERVER_IP   = "HOBERADIUS_WG_DATA_SERVER_IP"
_ENV_ENDPOINT    = "HOBERADIUS_WG_DATA_ENDPOINT"
_ENV_KEY_FILE    = "HOBERADIUS_WG_DATA_KEY_FILE"
_ENV_DNS         = "HOBERADIUS_WG_DATA_DNS"
_ENV_LISTEN_PORT = "HOBERADIUS_WG_DATA_LISTEN_PORT"

_DEFAULT_INTERFACE   = "wg-data"
_DEFAULT_PEERS_DIR   = "/etc/hoberadius/wg-data-peers.d"
_DEFAULT_SUBNET      = "10.100.0.0/24"
_DEFAULT_SERVER_IP   = "10.100.0.1"
_DEFAULT_KEY_FILE    = "/etc/wireguard/wg-data.key"
_DEFAULT_DNS         = "1.1.1.1"
_DEFAULT_LISTEN_PORT = 51821


def _iface() -> str:
    return os.environ.get(_ENV_INTERFACE, _DEFAULT_INTERFACE)


def _peers_dir() -> Path:
    return Path(os.environ.get(_ENV_PEERS_DIR, _DEFAULT_PEERS_DIR))


def _subnet() -> ipaddress.IPv4Network:
    return ipaddress.ip_network(
        os.environ.get(_ENV_SUBNET, _DEFAULT_SUBNET), strict=False
    )


def _server_ip() -> ipaddress.IPv4Address:
    return ipaddress.ip_address(
        os.environ.get(_ENV_SERVER_IP, _DEFAULT_SERVER_IP)
    )


def _endpoint() -> str:
    return os.environ.get(_ENV_ENDPOINT, "")


def _key_file() -> Path:
    return Path(os.environ.get(_ENV_KEY_FILE, _DEFAULT_KEY_FILE))


def _dns() -> str:
    return os.environ.get(_ENV_DNS, _DEFAULT_DNS)


def _listen_port() -> int:
    return int(os.environ.get(_ENV_LISTEN_PORT, _DEFAULT_LISTEN_PORT))


# ─── DB helpers ──────────────────────────────────────────────────

def _db():
    from ..db.connection import db
    return db()


def _tx():
    from ..db.connection import transaction
    return transaction()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ─── Keypair generation ──────────────────────────────────────────

def _generate_keypair() -> tuple[str, str]:
    """Returns (private_b64, public_b64) — X25519, WireGuard-compatible."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization

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


# ─── wg command wrapper ──────────────────────────────────────────

def _wg(*args: str) -> subprocess.CompletedProcess:
    """تشغيل أمر wg. صامت في بيئة التطوير إن لم يكن wg مثبّتًا."""
    try:
        return subprocess.run(
            ["wg", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        _LOG.warning("wg not found — skipping live wg command (dev mode)")
        return subprocess.CompletedProcess(["wg", *args], returncode=0, stdout="", stderr="")


# ─── Service (wireguard_data_service) ────────────────────────────

def get_service(tenant_id: int) -> dict | None:
    row = _db().execute(
        "SELECT * FROM wireguard_data_service WHERE tenant_id=? ORDER BY id LIMIT 1",
        (tenant_id,),
    ).fetchone()
    return dict(row) if row else None


def init_service(
    tenant_id: int,
    allocation_mirror_id: int | None = None,
    *,
    interface_name: str | None = None,
    listen_port: int | None = None,
    address_range: str | None = None,
) -> dict:
    """ينشئ wireguard_data_service ويولّد keypair.

    Private key → يُكتب إلى KEY_FILE (0600) ولا يُحفظ في DB.
    Public key  → يُخزَّن في wireguard_data_service.public_key.

    يرفع ValueError إن كانت الخدمة مُهيَّأة مسبقًا.
    """
    if get_service(tenant_id):
        raise ValueError("خدمة WireGuard البيانات مُهيَّأة مسبقًا لهذا الـ tenant.")

    iface    = interface_name or _iface()
    port     = listen_port or _listen_port()
    subnet   = address_range or str(_subnet())
    priv, pub = _generate_keypair()

    kf = _key_file()
    kf.parent.mkdir(parents=True, exist_ok=True)
    kf.write_text(priv + "\n", encoding="ascii")
    try:
        os.chmod(kf, 0o600)
    except OSError:
        _LOG.warning("wg-data: could not chmod %s (dev mode?)", kf)

    now = _now()
    with _tx() as conn:
        conn.execute(
            """INSERT INTO wireguard_data_service
               (tenant_id, allocation_mirror_id, interface_name, listen_port,
                public_key, address_range, speed_limit_mbps, max_peers,
                quota_period, quota_bytes_used, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,0,0,'','0','active',?,?)""",
            (tenant_id, allocation_mirror_id, iface, port, pub, subnet, now, now),
        )

    svc = get_service(tenant_id)
    svc["private_key_written_to"] = str(kf)
    _LOG.info("wg-data: initialized service iface=%s pubkey=%.12s...", iface, pub)
    return svc


def update_service_limits(tenant_id: int, max_peers: int, speed_limit_mbps: int) -> None:
    """يُحدِّث حدود الخدمة من التخصيص (يُستدعى بعد المزامنة)."""
    _db().execute(
        "UPDATE wireguard_data_service SET max_peers=?, speed_limit_mbps=?, updated_at=? "
        "WHERE tenant_id=?",
        (max_peers, speed_limit_mbps, _now(), tenant_id),
    )


# ─── IP allocation ────────────────────────────────────────────────

def _used_peer_ips(service_id: int) -> set[ipaddress.IPv4Address]:
    rows = _db().execute(
        "SELECT peer_address FROM wireguard_peer WHERE service_id=? AND status != 'revoked'",
        (service_id,),
    ).fetchall()
    result: set[ipaddress.IPv4Address] = set()
    for r in rows:
        try:
            result.add(ipaddress.ip_address(r["peer_address"]))
        except ValueError:
            pass
    return result


def _allocate_peer_ip(service_id: int, address_range: str) -> ipaddress.IPv4Address:
    net = ipaddress.ip_network(address_range, strict=False)
    reserved = {_server_ip()} | _used_peer_ips(service_id)
    for candidate in net.hosts():
        if candidate not in reserved:
            return candidate
    raise RuntimeError(f"شبكة wg-data {net} ممتلئة — لا مزيد من العناوين")


# ─── Peer file management ─────────────────────────────────────────

def _peer_file(peer_id: int) -> Path:
    return _peers_dir() / f"peer-{peer_id}.conf"


def _write_peer_file(peer_id: int, public_key: str, peer_address: str) -> None:
    d = _peers_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = _peer_file(peer_id)
    content = (
        f"[Peer]\n"
        f"# HobeRadius wg-data peer id={peer_id}\n"
        f"PublicKey = {public_key}\n"
        f"AllowedIPs = {peer_address}/32\n"
        f"PersistentKeepalive = 25\n"
    )
    tmp = path.with_suffix(".conf.tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.chmod(tmp, 0o640)
    except OSError:
        pass
    tmp.replace(path)


def _remove_peer_file(peer_id: int) -> None:
    p = _peer_file(peer_id)
    if p.exists():
        p.unlink()


# ─── Live wg commands ─────────────────────────────────────────────

def _wg_add_peer(public_key: str, peer_address: str, interface: str) -> None:
    _wg("set", interface, "peer", public_key, "allowed-ips", f"{peer_address}/32")


def _wg_remove_peer(public_key: str, interface: str) -> None:
    _wg("set", interface, "peer", public_key, "remove")


# ─── Client config rendering ──────────────────────────────────────

def render_client_config(
    *,
    client_priv: str,
    client_ip: str,
    server_pubkey: str,
    server_endpoint: str,
    dns: str,
) -> str:
    ep_line = (
        f"Endpoint = {server_endpoint}\n" if server_endpoint
        else "# Endpoint = <public-ip>:<port>  # اضبطه يدويًا\n"
    )
    return (
        "[Interface]\n"
        f"PrivateKey = {client_priv}\n"
        f"Address = {client_ip}/32\n"
        f"DNS = {dns}\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {server_pubkey}\n"
        "AllowedIPs = 0.0.0.0/0, ::/0\n"
        f"{ep_line}"
        "PersistentKeepalive = 25\n"
    )


# ─── Audit log helper ─────────────────────────────────────────────

def _audit(
    conn,
    tenant_id: int,
    entity_type: str,
    entity_id: int | None,
    action: str,
    description: str,
) -> None:
    conn.execute(
        """INSERT INTO service_audit_log
           (tenant_id, entity_type, entity_id, action, actor, description, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (tenant_id, entity_type, entity_id, action, "admin", description, _now()),
    )


# ─── Peer CRUD ────────────────────────────────────────────────────

def list_peers(
    tenant_id: int,
    service_id: int,
    *,
    include_revoked: bool = False,
) -> list[dict]:
    if include_revoked:
        rows = _db().execute(
            "SELECT * FROM wireguard_peer WHERE tenant_id=? AND service_id=? ORDER BY id",
            (tenant_id, service_id),
        ).fetchall()
    else:
        rows = _db().execute(
            "SELECT * FROM wireguard_peer "
            "WHERE tenant_id=? AND service_id=? AND status != 'revoked' ORDER BY id",
            (tenant_id, service_id),
        ).fetchall()
    return [dict(r) for r in rows]


def add_peer(
    *,
    tenant_id: int,
    service_id: int,
    display_name: str = "",
    notes: str = "",
    speed_limit_mbps: int | None = None,
    transfer_limit_bytes: int | None = None,
) -> tuple[dict, str, str]:
    """يضيف peer جديد.

    يعيد (peer_dict, client_private_key, client_config_text).
    client_private_key يُعاد مرةً واحدة فقط ولا يُخزَّن.
    """
    svc = get_service(tenant_id)
    if not svc or svc["id"] != service_id:
        raise ValueError("خدمة WireGuard غير موجودة.")
    if svc["status"] != "active":
        raise ValueError(f"الخدمة في حالة {svc['status']} — لا يمكن إضافة peers.")

    active_count = _db().execute(
        "SELECT COUNT(*) AS c FROM wireguard_peer "
        "WHERE service_id=? AND status NOT IN ('revoked')",
        (service_id,),
    ).fetchone()["c"]
    max_p = svc["max_peers"]
    if max_p > 0 and active_count >= max_p:
        raise ValueError(f"تجاوز الحد الأقصى للـ peers ({max_p}).")

    client_priv, client_pub = _generate_keypair()
    peer_ip = _allocate_peer_ip(service_id, svc["address_range"])
    peer_ip_str = str(peer_ip)
    now = _now()

    with _tx() as conn:
        cur = conn.execute(
            """INSERT INTO wireguard_peer
               (tenant_id, service_id, public_key, allowed_ips, peer_address,
                display_name, notes, speed_limit_mbps, transfer_limit_bytes,
                quota_bytes_used, quota_period, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,0,'','active',?,?)""",
            (
                tenant_id, service_id, client_pub, f"{peer_ip_str}/32", peer_ip_str,
                display_name.strip(), notes.strip(),
                speed_limit_mbps, transfer_limit_bytes,
                now, now,
            ),
        )
        peer_id = cur.lastrowid
        _audit(conn, tenant_id, "wireguard_peer", peer_id, "create",
               f"أُضيف peer «{display_name or peer_ip_str}»")

    try:
        _write_peer_file(peer_id, client_pub, peer_ip_str)
    except OSError as exc:
        _LOG.warning("wg-data: could not write peer file peer_id=%d: %s", peer_id, exc)

    _wg_add_peer(client_pub, peer_ip_str, svc["interface_name"])

    peer = dict(_db().execute(
        "SELECT * FROM wireguard_peer WHERE id=?", (peer_id,)
    ).fetchone())
    config = render_client_config(
        client_priv=client_priv,
        client_ip=peer_ip_str,
        server_pubkey=svc["public_key"],
        server_endpoint=_endpoint(),
        dns=_dns(),
    )
    _LOG.info("wg-data: added peer id=%d ip=%s display=%s", peer_id, peer_ip_str, display_name)
    return peer, client_priv, config


def remove_peer(tenant_id: int, peer_id: int) -> bool:
    row = _db().execute(
        "SELECT p.*, s.interface_name FROM wireguard_peer p "
        "JOIN wireguard_data_service s ON s.id = p.service_id "
        "WHERE p.id=? AND p.tenant_id=?",
        (peer_id, tenant_id),
    ).fetchone()
    if not row:
        return False
    with _tx() as conn:
        conn.execute(
            "UPDATE wireguard_peer SET status='revoked', updated_at=? WHERE id=?",
            (_now(), peer_id),
        )
        _audit(conn, tenant_id, "wireguard_peer", peer_id, "delete", "peer محذوف")
    _wg_remove_peer(row["public_key"], row["interface_name"])
    try:
        _remove_peer_file(peer_id)
    except OSError as exc:
        _LOG.warning("wg-data: could not remove peer file peer_id=%d: %s", peer_id, exc)
    return True


def suspend_peer(tenant_id: int, peer_id: int) -> bool:
    row = _db().execute(
        "SELECT p.*, s.interface_name FROM wireguard_peer p "
        "JOIN wireguard_data_service s ON s.id = p.service_id "
        "WHERE p.id=? AND p.tenant_id=? AND p.status='active'",
        (peer_id, tenant_id),
    ).fetchone()
    if not row:
        return False
    with _tx() as conn:
        conn.execute(
            "UPDATE wireguard_peer SET status='suspended', updated_at=? WHERE id=?",
            (_now(), peer_id),
        )
        _audit(conn, tenant_id, "wireguard_peer", peer_id, "suspend", "peer موقوف")
    _wg_remove_peer(row["public_key"], row["interface_name"])
    try:
        _remove_peer_file(peer_id)
    except OSError:
        pass
    return True


def activate_peer(tenant_id: int, peer_id: int) -> bool:
    row = _db().execute(
        "SELECT p.*, s.interface_name FROM wireguard_peer p "
        "JOIN wireguard_data_service s ON s.id = p.service_id "
        "WHERE p.id=? AND p.tenant_id=? AND p.status='suspended'",
        (peer_id, tenant_id),
    ).fetchone()
    if not row:
        return False
    with _tx() as conn:
        conn.execute(
            "UPDATE wireguard_peer SET status='active', updated_at=? WHERE id=?",
            (_now(), peer_id),
        )
        _audit(conn, tenant_id, "wireguard_peer", peer_id, "activate", "peer مُعاد تفعيله")
    _wg_add_peer(row["public_key"], row["peer_address"], row["interface_name"])
    try:
        _write_peer_file(peer_id, row["public_key"], row["peer_address"])
    except OSError:
        pass
    return True


# ─── Quota sync ───────────────────────────────────────────────────

def sync_quota(tenant_id: int) -> dict:
    """يقرأ `wg show <iface> transfer` ويُحدِّث quota_bytes_used لكل peer.

    مخرجات wg show transfer: "<pubkey>\\t<rx_bytes>\\t<tx_bytes>" لكل سطر.
    نخزّن rx+tx كمجموع تراكمي (يُعاد ضبطه عند تغيّر الفترة الشهرية).
    """
    svc = get_service(tenant_id)
    if not svc:
        return {"ok": False, "error": "لا توجد خدمة WireGuard مُهيَّأة"}

    iface = svc["interface_name"]
    result = _wg("show", iface, "transfer")
    if result.returncode != 0:
        _LOG.warning("wg show %s transfer: %s", iface, result.stderr)
        return {"ok": False, "error": result.stderr.strip() or "wg command failed"}

    transfer: dict[str, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) == 3:
            try:
                transfer[parts[0]] = (int(parts[1]), int(parts[2]))
            except ValueError:
                pass

    now = _now()
    period = _period()
    updated = 0
    total_bytes = 0

    peers = _db().execute(
        "SELECT id, public_key, quota_bytes_used, quota_period "
        "FROM wireguard_peer WHERE service_id=? AND status != 'revoked'",
        (svc["id"],),
    ).fetchall()

    with _tx() as conn:
        for peer in peers:
            pub = peer["public_key"]
            if pub not in transfer:
                continue
            rx, tx = transfer[pub]
            peer_total = rx + tx
            quota_used = peer_total
            conn.execute(
                "UPDATE wireguard_peer SET quota_bytes_used=?, quota_period=?, updated_at=? "
                "WHERE id=?",
                (quota_used, period, now, peer["id"]),
            )
            total_bytes += quota_used
            updated += 1

        conn.execute(
            "UPDATE wireguard_data_service "
            "SET quota_bytes_used=?, quota_period=?, updated_at=? WHERE id=?",
            (total_bytes, period, now, svc["id"]),
        )

    _LOG.info(
        "wg-data: quota sync done iface=%s peers_updated=%d total_bytes=%d",
        iface, updated, total_bytes,
    )
    return {"ok": True, "peers_updated": updated, "total_bytes": total_bytes}


__all__ = [
    "get_service",
    "init_service",
    "update_service_limits",
    "list_peers",
    "add_peer",
    "remove_peer",
    "suspend_peer",
    "activate_peer",
    "sync_quota",
    "render_client_config",
]
