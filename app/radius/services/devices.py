"""
NasDevicesService — منطق إدارة الـ NAS.

- يستلم RadiusAdapter + RadiusAuditService.
- لا يلامس Flask request/session — الـ route يمرّر `actor`.
- كل عملية كتابة → audit.record(...).
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from typing import Optional, Sequence

from ..core.constants import (
    AUDIT_ACTION_ARCHIVE,
    AUDIT_ACTION_CREATE,
    AUDIT_ACTION_UPDATE,
    NAS_VENDORS,
)
from ..core.errors import RadiusConflict, RadiusValidationError
from ..core.types import NasDevice
from ..integration.adapter import RadiusAdapter
from .audit import RadiusAuditService


_LOG = logging.getLogger(__name__)


class NasDevicesService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit
        # Outcome of the FreeRADIUS client-file sync of the LAST create/update
        # done through this instance: None (nothing to report) or
        # {"ok": False, "message": <Arabic>, "detail": <raw>}. Routes surface
        # it so a failed registration is never reported as a clean success.
        self.radius_client_warning: Optional[dict] = None

    def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[NasDevice]:
        return self._adapter.list_nas(limit=limit, offset=offset)

    def get(self, nas_id: int) -> NasDevice:
        return self._adapter.get_nas(nas_id)

    def create(self, *, actor: str, device: NasDevice) -> NasDevice:
        device = _validate(device, existing=None)
        _pop_radius_sync()
        saved = _save_or_conflict(self._adapter, device)
        self.radius_client_warning = _radius_sync_warning(_pop_radius_sync())
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_CREATE,
            target_type="nas",
            target_id=str(saved.id),
            payload={"name": saved.name, "address": saved.address, "vendor": saved.vendor},
        )
        return saved

    def update(self, *, actor: str, device: NasDevice) -> NasDevice:
        if device.id is None:
            raise RadiusValidationError("معرّف الراوتر مطلوب للتعديل.")
        existing = self._adapter.get_nas(device.id)
        device = _validate(device, existing=existing)
        _pop_radius_sync()
        saved = _save_or_conflict(self._adapter, device)
        self.radius_client_warning = _radius_sync_warning(_pop_radius_sync())
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_UPDATE,
            target_type="nas",
            target_id=str(saved.id),
            payload={"name": saved.name, "address": saved.address, "vendor": saved.vendor},
        )
        return saved

    def delete(self, *, actor: str, nas_id: int) -> None:
        # Capture the router's mgmt-tunnel IP BEFORE archiving so we can release
        # the WireGuard peer bound to it on the VPS. Best-effort — a lookup miss
        # never blocks the delete.
        mgmt_ip = _mgmt_ip_for_nas(nas_id)
        self._adapter.delete_nas(nas_id)
        # Release the 10.10.0.x mgmt peer on the VPS so the IP is actually freed
        # everywhere (not just soft-deleted in the DB). The allocator already
        # stops counting the archived row (deleted_at filter); removing the peer
        # file prevents a stale peer/route lingering when the IP is reused.
        if mgmt_ip:
            _release_mgmt_peer(mgmt_ip)
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_ARCHIVE,
            target_type="nas",
            target_id=str(nas_id),
            payload={"mode": "soft_delete", "released_mgmt_ip": mgmt_ip or ""},
        )


def _mgmt_ip_for_nas(nas_id: int) -> str:
    """The router's mgmt-tunnel IP (``vpn_peer_address``, e.g. 10.10.0.7) for a
    NAS, read raw since the ``NasDevice`` dataclass omits the VPN columns.

    Best-effort: returns "" on any error (missing column/table in some deploys,
    row already gone). Never raises — releasing the peer is a cleanup step, not a
    precondition for the delete."""
    try:
        from ..db.connection import db
        try:
            from ..integration.sqlite_adapter import _tid
            tenant_id = _tid()
        except Exception:  # noqa: BLE001 — no request context → default tenant
            tenant_id = 1
        row = db().execute(
            "SELECT vpn_peer_address FROM nas_devices WHERE id=? AND tenant_id=?",
            (int(nas_id), int(tenant_id)),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return ""
    if not row:
        return ""
    return str((row["vpn_peer_address"] if not isinstance(row, dict) else row.get("vpn_peer_address")) or "").strip()


def _release_mgmt_peer(mgmt_ip: str) -> None:
    """Remove the mgmt-WG peer bound to ``mgmt_ip`` on the VPS. Best-effort."""
    try:
        from .wg_peer_manager import release_peer_by_ip
        release_peer_by_ip(mgmt_ip)
    except Exception:  # noqa: BLE001 — peer cleanup must never break a delete
        import logging
        logging.getLogger(__name__).warning(
            "wg mgmt peer release failed for ip=%s", mgmt_ip, exc_info=True,
        )


# ─────────────── validation (web form + /api/v1/nas share this) ───────────────
#
# Stress campaign A08 (2026-09-28): the NAS row feeds the FreeRADIUS clients
# directory verbatim (``ipaddr = <address>`` / ``secret = <secret>``, unquoted).
# Garbage (``abc``, ``999.1.1.1``, a CIDR, a 10k-char string) or a second row on
# the SAME address makes FreeRADIUS refuse to start → silent crash-loop → every
# router on the server loses RADIUS. So the service is the wall: strict values,
# one address per server (all tenants share one clients directory).

NAS_TYPES_ALLOWED = ("hotspot", "pppoe", "dhcp", "router", "ap", "switch",
                     "firewall", "other")
# Unquoted value in clients.conf: no whitespace, quotes, braces, comment char,
# backslash or ``$`` (variable expansion). 1..128 chars (RFC 2865 practice).
_SECRET_OK = re.compile(r"^[A-Za-z0-9!%&()*+,\-./:;<=>?@\[\]^_|~]{1,128}$")
_HOST_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
_TEXT_MAX = {
    "name": 100, "shortname": 64, "location": 255, "coordinates": 100,
    "description": 1000, "snmp_community": 128, "api_user": 128,
    "api_password": 256, "tags": 1000, "metadata": 10000,
}
_TEXT_LABELS = {
    "name": "اسم الراوتر", "shortname": "الاسم المختصر", "location": "الموقع",
    "coordinates": "الإحداثيات", "description": "الوصف",
    "snmp_community": "SNMP community", "api_user": "مستخدم API",
    "api_password": "كلمة سر API", "tags": "الوسوم", "metadata": "البيانات الإضافية",
}
# (field, label, min, max)
_PORT_FIELDS = (
    ("auth_port", "منفذ المصادقة", 1, 65535),
    ("acct_port", "منفذ المحاسبة", 1, 65535),
    ("coa_port", "منفذ CoA", 1, 65535),
    ("api_port", "منفذ API", 1, 65535),
    ("ssh_port", "منفذ SSH", 1, 65535),
    ("ports", "عدد المنافذ", 0, 65535),
)


def normalize_nas_address(raw) -> str:
    """IPv4/IPv6 literal (canonical form) or an RFC-1123 hostname (lower-case).
    Raises RadiusValidationError (Arabic) for anything else."""
    addr = str(raw or "").strip()
    if not addr:
        raise RadiusValidationError("عنوان الراوتر مطلوب.")
    if len(addr) > 253:
        raise RadiusValidationError("عنوان الراوتر طويل جدًا.")
    if "%" in addr:
        # f06-H2: «fe80::1%eth0» — ip_address() يقبل معرّف النطاق (zone id)
        # لكنّ FreeRADIUS يرفضه («Invalid address») فيتعطّل الرديوس لكلّ
        # الراوترات عند إعادة التشغيل التالية.
        raise RadiusValidationError(
            "عنوان IPv6 بمعرّف نطاق (مثل ‎%eth0) غير مدعوم في الرديوس — "
            "أدخل العنوان بلا «%…» (عنوان IPv4 أو IPv6 عاديّ).")
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_unspecified or ip.is_multicast:
            raise RadiusValidationError(
                f"العنوان {addr[:64]} ليس عنوان جهازٍ صالحًا للراوتر.")
        # «::ffff:192.0.2.1» IS 192.0.2.1 — store the IPv4 form so the
        # duplicate-address check (and the FreeRADIUS client key) sees it.
        mapped = getattr(ip, "ipv4_mapped", None)
        return str(mapped if mapped is not None else ip)
    if "/" in addr:
        raise RadiusValidationError(
            "عنوان الراوتر يجب أن يكون عنوان IP واحدًا، لا نطاق شبكة (CIDR).")
    labels = addr.rstrip(".").split(".")
    if all(lbl.isdigit() for lbl in labels if lbl):
        raise RadiusValidationError(f"عنوان IP غير صالح: {addr[:64]}")
    # A hostname must be a real FQDN («router.example.com»): a single word
    # («abc») or a numeric TLD is a typo, not a router address.
    if (len(labels) < 2 or not labels[-1].isalpha()
            or not all(_HOST_LABEL.match(lbl) for lbl in labels)):
        raise RadiusValidationError(
            "عنوان الراوتر غير صالح — أدخل عنوان IP (مثل 10.0.0.1) أو اسم نطاق صحيحًا.")
    return addr.rstrip(".").lower()


def _is_ip_literal(value: str) -> bool:
    """An address FreeRADIUS can load as ``ipaddr`` (f06-H2: a scoped IPv6
    «fe80::1%eth0» is NOT one, although ``ip_address()`` accepts it)."""
    from .setup_wizard_v3_radius_server_provisioning import radiusd_ip_literal
    return radiusd_ip_literal(value) is not None


def check_restorable_nas(tenant_id: int, nas_id: int) -> None:
    """f06-H1/H2 — may this archived router come back?

    Restoring used to bring back its address even when a live router had
    re-used it meanwhile ⇒ two live rows on one IP, and enabling either one
    silently swapped the FreeRADIUS secret. Now: 409 when the address (or its
    tunnel IP) belongs to another live router, 422 when the stored address is
    something radiusd cannot parse. (A taken NAME is not fatal: the restored
    row is renamed «… (مستعاد N)» and comes back disabled.)"""
    from ..db.connection import db
    row = db().execute(
        "SELECT address, COALESCE(vpn_peer_address,'') AS vpa, "
        "       COALESCE(management_remote_address,'') AS mra "
        "  FROM nas_devices WHERE tenant_id = ? AND id = ? "
        "   AND deleted_at IS NOT NULL AND deleted_at != ''",
        (int(tenant_id), int(nas_id))).fetchone()
    if not row:
        return
    try:
        address = normalize_nas_address(row["address"])
    except RadiusValidationError as exc:
        raise RadiusValidationError(
            f"لا يمكن استعادة الراوتر: {exc.message} عدّل العنوان بعد إضافته من جديد.",
            details={"field": "address", "code": "nas_address_invalid"}) from None
    for addr in (address, str(row["mra"]).strip(), str(row["vpa"]).strip()):
        if not addr:
            continue
        owner = find_address_owner(addr, exclude_id=nas_id)
        if owner is None:
            continue
        same_tenant = int(owner["tenant_id"]) == int(tenant_id)
        who = f" «{owner['name']}»" if same_tenant and owner["name"] else " آخر"
        raise RadiusConflict(
            f"لا يمكن استعادة الراوتر: العنوان {addr} مستخدم الآن لراوتر{who} — "
            "راوتران بعنوانٍ واحد يعطّلان الرديوس. احذف ذلك الراوتر أو غيّر "
            "عنوانه أولًا، ثم أعد المحاولة.",
            details={"field": "address", "code": "nas_address_conflict",
                     "existing_nas_id": owner["id"] if same_tenant else None})


def _tunnel_source_ip(nas_id) -> str:
    """The row's tunnel IP (management_remote_address / vpn_peer_address) — the
    ipaddr FreeRADIUS is keyed on for a tunnel router. "" when none/unknown."""
    if nas_id is None:
        return ""
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT COALESCE(management_remote_address,'') AS mra, "
            "       COALESCE(vpn_peer_address,'') AS vpa "
            "FROM nas_devices WHERE id = ?", (int(nas_id),),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return ""
    if not row:
        return ""
    return str(row["mra"] or row["vpa"] or "").strip()


def find_address_owner(address: str, *, exclude_id=None) -> Optional[dict]:
    """Another live NAS row — in ANY tenant, since the FreeRADIUS clients
    directory is one per server — already registered on ``address`` (as its
    address or its tunnel IP). Returns ``{"id", "tenant_id", "name"}`` or None."""
    addr = str(address or "").strip().lower()
    if not addr:
        return None
    from ..db.connection import db
    row = db().execute(
        "SELECT id, tenant_id, name FROM nas_devices "
        " WHERE (deleted_at IS NULL OR deleted_at = '') "
        "   AND id != ? "
        "   AND (lower(trim(address)) = ? "
        "        OR (COALESCE(vpn_peer_address,'') != '' AND lower(trim(vpn_peer_address)) = ?) "
        "        OR (COALESCE(management_remote_address,'') != '' "
        "            AND lower(trim(management_remote_address)) = ?)) "
        " ORDER BY id LIMIT 1",
        (int(exclude_id) if exclude_id is not None else -1, addr, addr, addr),
    ).fetchone()
    if not row:
        return None
    return {"id": int(row["id"]), "tenant_id": int(row["tenant_id"]),
            "name": row["name"] or ""}


def find_name_owner(tenant_id, name: str, *, exclude_id=None) -> Optional[dict]:
    """Another LIVE router of the same tenant already called ``name``
    (case-insensitive). Archived (recycle-bin) rows never block a name."""
    nm = str(name or "").strip()
    if not nm:
        return None
    from ..db.connection import db
    row = db().execute(
        "SELECT id, name FROM nas_devices "
        " WHERE tenant_id = ? AND (deleted_at IS NULL OR deleted_at = '') "
        "   AND id != ? AND lower(trim(name)) = lower(?) "
        " ORDER BY id LIMIT 1",
        (int(tenant_id or 0), int(exclude_id) if exclude_id is not None else -1, nm),
    ).fetchone()
    if not row:
        return None
    return {"id": int(row["id"]), "name": row["name"] or ""}


def _request_tenant_id() -> int:
    try:
        from flask import g
        return int(getattr(g, "tenant_id", 1) or 1)
    except (ImportError, RuntimeError, TypeError, ValueError):
        return 1


def _name_conflict(name: str, owner_id=None) -> RadiusConflict:
    return RadiusConflict(
        f"اسم الراوتر «{str(name)[:100]}» مستخدم لراوتر آخر — اختر اسمًا مختلفًا.",
        details={"field": "name", "code": "nas_name_conflict",
                 "existing_nas_id": owner_id},
    )


def _save_or_conflict(adapter, device: NasDevice) -> NasDevice:
    """upsert_nas, mapping a DB unique-name violation (a parallel create that
    slipped past the pre-check) to the same 409 instead of a 500."""
    import sqlite3
    try:
        return adapter.upsert_nas(device)
    except sqlite3.IntegrityError as exc:
        if "name" in str(exc).lower():
            raise _name_conflict(device.name) from exc
        raise


def _validate(device: NasDevice, *, existing: Optional[NasDevice]) -> NasDevice:
    """Validate + normalise a NAS about to be saved. On update only the fields
    that CHANGED are re-checked, so a legacy row can still be edited/disabled."""
    from dataclasses import replace

    from ..core.strict_input import parse_ranged_int

    def changed(field: str) -> bool:
        return existing is None or getattr(existing, field) != getattr(device, field)

    changes: dict = {}

    vendor = str(device.vendor or "").strip().lower()
    if changed("vendor") or vendor != device.vendor:
        if vendor not in NAS_VENDORS:
            raise RadiusValidationError(
                f"نوع الجهاز غير معروف: «{str(device.vendor)[:40]}». "
                f"المسموح: {'، '.join(NAS_VENDORS)}.")
        changes["vendor"] = vendor

    if changed("nas_type"):
        nas_type = str(device.nas_type or "").strip().lower() or "hotspot"
        if nas_type not in NAS_TYPES_ALLOWED:
            raise RadiusValidationError(
                f"نوع الخدمة غير معروف: «{str(device.nas_type)[:40]}». "
                f"المسموح: {'، '.join(NAS_TYPES_ALLOWED)}.")
        changes["nas_type"] = nas_type

    if changed("name"):
        name = str(device.name or "").strip()
        if not name:
            raise RadiusValidationError("اسم الراوتر مطلوب.")
        changes["name"] = name
        tenant = ((existing.tenant_id if existing is not None else None)
                  or device.tenant_id or _request_tenant_id())
        same = find_name_owner(tenant, name, exclude_id=device.id)
        if same is not None:
            raise _name_conflict(name, same["id"])

    for field, limit in _TEXT_MAX.items():
        val = changes.get(field, getattr(device, field))
        if changed(field) and val is not None and len(str(val)) > limit:
            raise RadiusValidationError(
                f"«{_TEXT_LABELS[field]}» طويل جدًا (الحد الأقصى {limit} حرفًا).")

    for field, label, lo, hi in _PORT_FIELDS:
        if changed(field):
            changes[field] = parse_ranged_int(
                getattr(device, field), label=label, minimum=lo, maximum=hi)

    address = device.address
    # f06-H1: the owner check re-runs on EVERY save of an enabled router — not
    # only when the address is in the PATCH. `{"enabled": true}` on a restored
    # row used to put two enabled rows on one IP and flip the RADIUS secret.
    if changed("address") or bool(device.enabled):
        if changed("address"):
            address = normalize_nas_address(device.address)
            changes["address"] = address
        owner = find_address_owner(address, exclude_id=device.id)
        if owner is not None:
            same_tenant = int(owner["tenant_id"]) == int(device.tenant_id or 0) or (
                existing is not None and int(owner["tenant_id"]) == int(existing.tenant_id))
            who = f" «{owner['name']}»" if same_tenant and owner["name"] else " آخر"
            raise RadiusConflict(
                f"العنوان {address} مستخدم لراوتر{who} على هذا الخادم — لا يمكن "
                "تسجيل راوترين بنفس العنوان في الرديوس (يتعطّل الرديوس كليًّا).",
                details={"field": "address", "existing_nas_id":
                         owner["id"] if same_tenant else None},
            )

    secret = device.secret or ""
    if changed("secret") and secret:
        if not _SECRET_OK.match(secret):
            raise RadiusValidationError(
                "كلمة سر الرديوس غير صالحة: 1–128 حرفًا إنجليزيًا/أرقامًا/رموزًا، "
                "بلا مسافات ولا الرموز \" ' ` { } # \\ $.")

    # A router that will be REGISTERED in FreeRADIUS (enabled + secret) must be
    # keyed on an IP literal — a hostname that fails to resolve at reload makes
    # FreeRADIUS exit. Tunnel routers are keyed on their tunnel IP instead.
    enabled = bool(device.enabled)
    relevant = changed("address") or changed("secret") or changed("enabled")
    if relevant and enabled and secret:
        source = _tunnel_source_ip(device.id) or str(address or "").strip()
        if not _is_ip_literal(source):
            raise RadiusValidationError(
                "لتسجيل الراوتر في الرديوس يجب أن يكون عنوانه IP صريحًا (لا اسم "
                "نطاق). أدخل عنوان IP أو عطّل الراوتر.")

    return replace(device, **changes) if changes else device


def _pop_radius_sync():
    try:
        from .freeradius_translator import pop_last_nas_sync
        return pop_last_nas_sync()
    except Exception:  # noqa: BLE001
        return None


def _radius_sync_warning(result) -> Optional[dict]:
    """Turn a failed FreeRADIUS client-file sync into a visible warning."""
    if not isinstance(result, dict) or result.get("ok", True):
        return None
    detail = str(result.get("error") or "")[:500]
    _LOG.warning("NAS saved but FreeRADIUS client registration failed: %s", detail)
    return {
        "ok": False,
        "code": "radius_client_sync_failed",
        "message": ("حُفظ الراوتر لكن تعذّر تسجيله في خادم الرديوس — لن يستجيب "
                    "الرديوس لهذا الراوتر حتى تُصلَح المشكلة."),
        "detail": detail,
    }


def probe_nas_tcp(ip: str, port: int) -> tuple[str, str]:
    """TCP reachability with Arabic, errno-free messages (the raw
    «[Errno -3] Temporary failure in name resolution» leaked to the app).
    Never raises — a 10k-char legacy address used to crash with an idna
    UnicodeError (not an OSError) → HTML 500."""
    try:
        with socket.create_connection((ip, port), timeout=2.0):
            return "reachable", f"الاتصال نجح على {ip}:{port}"
    except socket.timeout:
        return "timeout", f"انتهت المهلة (2 ث) على {ip}:{port} — الراوتر لا يرد."
    except socket.gaierror:
        return "unreachable", "تعذّر حلّ اسم الراوتر — تحقّق من العنوان."
    except ConnectionRefusedError:
        return "unreachable", f"الراوتر رفض الاتصال على المنفذ {port} — تأكّد من تفعيل خدمة API."
    except OSError:
        return "unreachable", f"تعذّر الوصول إلى {str(ip)[:64]}:{port} — الشبكة غير متاحة."
    except (UnicodeError, ValueError):
        return "unreachable", "عنوان الراوتر غير صالح — عدّله ثم أعد الاختبار."


# Helper للـ routes في M2 (يستخدم الـ defaults)
def get_nas_devices_service() -> NasDevicesService:
    from ..integration.factory import get_radius_adapter

    return NasDevicesService(get_radius_adapter(), audit=_audit_singleton())


def _audit_singleton() -> RadiusAuditService:
    from .audit import get_audit_service

    return get_audit_service()
