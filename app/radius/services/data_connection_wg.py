"""«اتصال بيانات» — قرين WireGuard لاتصال بيانات v7 (جانب الـVPS).

feat/data-connection-oneclick. يُولّد زوج مفاتيح للعميل، يخصّص عنوانًا من
مجمّع WG مستقلّ لكل VPS، ويُنشئ صفّ القرين في DB. المفتاح الخاص للعميل
يُعاد مرّة واحدة لتضمينه في السكربت ولا يُخزَّن.

نقطة WG لاتصال البيانات مستقلّة تمامًا عن نفق إدارة WireGuard (10.10.0.0/24،
المنفذ 51820) كي لا تتسرّب شبكة الإدارة إلى سكربت العميل.

═══════════════════════ LAB-PENDING (متابعة مخبرية) ═══════════════════════
بُني الآن: النموذج (جدول data_connection_wg_peers)، توليد المفاتيح، تخصيص
العنوان من المجمّع (على مستوى DB)، وبناء السكربت.

مؤجَّل صراحةً (مُعلَّم أدناه بـ ``# LAB-PENDING``):
  1. **دفع القرين إلى واجهة WG الحيّة على الـVPS** — كتابة [Peer] فعليًّا في
     ``wg set`` / peers.d على الخادم. الآن: نُسجّل القرين في DB فقط
     (applied_to_vps=0).
  2. **سقف 5 ميجابت لكل قرين** — queue/tc مربوط بعنوان القرين من المجمّع على
     الـVPS. الآن: دالة ``apply_peer_queue`` لا تفعل شيئًا (queue_applied=0).
  3. **منفذ WG لاتصال البيانات** — ``HOBERADIUS_DATA_WG_PORT`` (افتراضي 51821،
     يجب أن يطابق مستمع WG الفعلي للبيانات على الـVPS).
  4. **مفتاح الخادم العام لاتصال البيانات** — ``HOBERADIUS_DATA_WG_PUBKEY``.
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from ..core import env_settings
from . import data_connection as dc
from .wg_peer_manager import generate_keypair

# ════════════════════════════════════════════════════════════════════════
# التهيئة (LAB-PENDING) — كلها قابلة للضبط من إعدادات النظام
# ════════════════════════════════════════════════════════════════════════

#: مجمّع عناوين WG لاتصال البيانات لكل VPS. مستقلّ عن نفق الإدارة
#: (10.10.0.0/24) وخارج بادئات التسرّب الممنوعة (10.99/10.98/10.51).
DATA_WG_POOL_ENV = "HOBERADIUS_DATA_WG_POOL"
DATA_WG_POOL_DEFAULT = "10.60.0.0/24"

#: منفذ استماع WG لاتصال البيانات على الـVPS (مستقلّ عن 51820 للإدارة).
DATA_WG_PORT_ENV = "HOBERADIUS_DATA_WG_PORT"
DATA_WG_PORT_DEFAULT = 51821

#: المفتاح العام لخادم WG لاتصال البيانات (ليس سرًّا — يدخل في كل سكربت).
DATA_WG_PUBKEY_ENV = "HOBERADIUS_DATA_WG_PUBKEY"

#: أول عنوان قابل للتخصيص يُحجز للخادم نفسه داخل النفق (مثل .1).
_SERVER_HOST_INDEX = 1


def data_wg_pool() -> ipaddress.IPv4Network:
    raw = str(env_settings.env(DATA_WG_POOL_ENV, DATA_WG_POOL_DEFAULT) or DATA_WG_POOL_DEFAULT)
    try:
        return ipaddress.ip_network(raw.strip(), strict=False)
    except ValueError as exc:
        raise dc.DataConnectionError(f"مجمّع WG غير صالح: {raw!r}") from exc


def data_wg_port() -> int:
    raw = env_settings.env(DATA_WG_PORT_ENV, str(DATA_WG_PORT_DEFAULT))
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return DATA_WG_PORT_DEFAULT


def data_wg_server_pubkey() -> str:
    """LAB-PENDING — المفتاح العام لخادم WG للبيانات. فارغ = لم يُضبط بعد."""
    return str(env_settings.env(DATA_WG_PUBKEY_ENV, "") or "").strip()


# ════════════════════════════════════════════════════════════════════════
# تخصيص العنوان من المجمّع (جانب DB — حقيقي؛ التطبيق على الـVPS مؤجَّل)
# ════════════════════════════════════════════════════════════════════════


def allocate_pool_ip(tenant_id: int) -> str:
    """يُعيد أول عنوان حرّ في مجمّع WG للبيانات (يتخطّى عنوان الخادم).

    حقيقي على مستوى DB: يقرأ العناوين المُسنَدة من
    data_connection_wg_peers ويختار التالي. **التطبيق على واجهة WG الحيّة
    على الـVPS مؤجَّل (LAB-PENDING)** — هذا يحجز العنوان منطقيًّا فقط."""
    from ..db.repos import data_connection_wg_peers_repo as repo

    pool = data_wg_pool()
    used = repo.used_ips(int(tenant_id))
    hosts = pool.hosts()
    server_ip = pool.network_address + _SERVER_HOST_INDEX
    for ip in hosts:
        if ip == server_ip:
            continue
        if str(ip) not in used:
            return str(ip)
    raise dc.DataConnectionError("نفد مجمّع عناوين WG لاتصال البيانات.")


def apply_peer_queue(peer_id: int, assigned_ip: str) -> bool:
    """LAB-PENDING — سقف 5 ميجابت لكل قرين على الـVPS (queue/tc مربوط
    بعنوان القرين). الآن: لا يفعل شيئًا ويُعيد False (لم يُطبَّق).

    عند التفعيل المخبري: أنشئ simple queue / tc بسرعة
    ``dc.DATA_SPEED_KBIT`` مربوطة بـ ``assigned_ip/32`` على واجهة WG
    للبيانات، ثم نادِ repo.mark_applied(peer_id, queue_applied=True)."""
    return False  # LAB-PENDING — راجع رأس الملف (البند 2)


def push_peer_to_vps(peer_id: int, public_key: str, assigned_ip: str) -> bool:
    """LAB-PENDING — كتابة [Peer] فعليًّا في واجهة WG على الـVPS. الآن:
    لا يفعل شيئًا ويُعيد False (القرين في DB فقط، applied_to_vps=0)."""
    return False  # LAB-PENDING — راجع رأس الملف (البند 1)


# ════════════════════════════════════════════════════════════════════════
# التزويد — يُولّد المفاتيح، يخصّص العنوان، يُنشئ صفّ القرين
# ════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class WgPeerProvision:
    """ناتج تزويد قرين WG. ``client_private_key`` يُعاد مرّة واحدة للسكربت
    فقط ولا يُخزَّن في DB."""
    peer_id: int
    assigned_ip: str
    client_private_key: str
    client_public_key: str
    server_public_key: str
    endpoint_host: str
    endpoint_port: int
    speed_kbit: int


def provision_data_wg_peer(*, tenant_id: int, subscriber_id: int,
                           username: str, endpoint_host: str) -> WgPeerProvision:
    """ينشئ قرين WG لاتصال بيانات v7 ويعيد كل ما يلزم السكربت.

    خطوات: توليد زوج مفاتيح العميل → تخصيص عنوان من المجمّع → كتابة صفّ
    القرين (DB). دفع القرين/السقف إلى الـVPS الحيّ مؤجَّل (LAB-PENDING)."""
    from ..db.repos import data_connection_wg_peers_repo as repo

    server_pub = data_wg_server_pubkey()
    if not server_pub:
        raise dc.DataConnectionError(
            "لم يُضبط المفتاح العام لخادم WireGuard للبيانات بعد "
            "(HOBERADIUS_DATA_WG_PUBKEY)."
        )
    priv, pub = generate_keypair()
    assigned_ip = allocate_pool_ip(tenant_id)
    port = data_wg_port()
    peer_id = repo.create_peer(
        tenant_id=tenant_id, subscriber_id=subscriber_id, username=username,
        public_key=pub, assigned_ip=assigned_ip,
        endpoint_host=endpoint_host, endpoint_port=port,
        speed_kbit=dc.DATA_SPEED_KBIT,
    )
    # LAB-PENDING: push_peer_to_vps + apply_peer_queue (متابعة مخبرية).
    return WgPeerProvision(
        peer_id=peer_id, assigned_ip=assigned_ip,
        client_private_key=priv, client_public_key=pub,
        server_public_key=server_pub,
        endpoint_host=endpoint_host, endpoint_port=port,
        speed_kbit=dc.DATA_SPEED_KBIT,
    )


__all__ = [
    "DATA_WG_POOL_DEFAULT", "DATA_WG_PORT_DEFAULT",
    "data_wg_pool", "data_wg_port", "data_wg_server_pubkey",
    "allocate_pool_ip", "apply_peer_queue", "push_peer_to_vps",
    "WgPeerProvision", "provision_data_wg_peer",
]
