"""Tr069SyncService — يربط الطابور بـ GenieACS ويُزامن حالة الأجهزة.

مسؤوليّتان يستدعيهما العامل الخلفيّ:
  • process_queued_actions: يحوّل أوامر الطابور إلى Tasks في GenieACS ويتابع.
  • reconcile_from_genieacs: يقرأ أجهزة GenieACS ويحدّث/يُسجّل صفوف tr069_devices
    (online, last_inform, WAN, model...)، ويحوّل pending→active عند أوّل Inform.

محصّن بالكامل: لا يرفع — أي فشل شبكة/تحليل يُسجَّل ويتابع.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ...db.repos import tr069_repo
from . import alerts, config, parameter_resolver
from .genieacs_client import GenieAcsClient, get_client

_LOG = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(iso_str: str) -> float | None:
    """ثوانٍ منذ طابع ISO (يقبل لاحقة Z). None عند تعذّر التحليل (فلا نحكم بالفصل)."""
    s = str(iso_str or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except (ValueError, TypeError):
        return None


def _derive_internet(online: bool, conn_status: str, wan_ip: str) -> str:
    """حالة الإنترنت خلف الراوتر: up | down | unknown.

    الراوتر المفصول عن ACS = unknown (لا نعرف). المتصل: نقرأ ConnectionStatus
    لـ WAN/PPP؛ وإلّا نستدلّ بوجود WAN IP قابل للتوجيه."""
    if not online:
        return "unknown"
    s = str(conn_status or "").strip().lower()
    if s in ("connected", "up"):
        return "up"
    if s in ("disconnected", "down", "pendingdisconnect", "disconnecting", "error"):
        return "down"
    ip = str(wan_ip or "").strip()
    if ip and ip not in ("0.0.0.0", "", "0.0.0.0.0"):
        return "up"
    return "unknown"


def _gval(node: Any) -> Any:
    """GenieACS يخزّن القيم كـ {"_value":x,"_timestamp":...}. يستخرج _value."""
    if isinstance(node, dict) and "_value" in node:
        return node["_value"]
    return node


def _dig(tree: dict, path: str) -> Any:
    cur: Any = tree
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return _gval(cur)


# ─────────────── الأوامر → Tasks ───────────────
def _build_task(action_type: str, device: dict, secret: dict) -> dict | None:
    if action_type == "reboot":
        return {"name": "reboot"}
    if action_type == "factory_reset":
        return {"name": "factoryReset"}
    if action_type in ("refresh", "connection_request"):
        return {"name": "refreshObject", "objectName": device.get("root_object") or ""}
    if action_type == "change_wifi":
        vals = []
        if secret.get("ssid"):
            p = parameter_resolver.resolve_path(device, "wifi_ssid")
            if p:
                vals.append([p, str(secret["ssid"]), "xsd:string"])
        if secret.get("password"):
            p = parameter_resolver.resolve_path(device, "wifi_password")
            if p:
                vals.append([p, str(secret["password"]), "xsd:string"])
        return {"name": "setParameterValues", "parameterValues": vals} if vals else None
    if action_type == "change_pppoe":
        vals = []
        if secret.get("username"):
            p = parameter_resolver.resolve_path(device, "pppoe_username")
            if p:
                vals.append([p, str(secret["username"]), "xsd:string"])
        if secret.get("password"):
            p = parameter_resolver.resolve_path(device, "pppoe_password")
            if p:
                vals.append([p, str(secret["password"]), "xsd:string"])
        return {"name": "setParameterValues", "parameterValues": vals} if vals else None
    return None


def process_queued_actions(tenant_id: int, client: GenieAcsClient | None = None,
                           limit: int = 20) -> int:
    client = client or get_client()
    processed = 0
    for act in tr069_repo.claim_pending_actions(limit=limit):
        if int(act.get("tenant_id") or 1) != int(tenant_id):
            continue
        aid = act["id"]
        device = tr069_repo.get_device(tenant_id, act["device_id"])
        if not device or not device.get("acs_device_id"):
            tr069_repo.update_action(aid, status="failed", failed_at=_now(),
                                     error_message="الجهاز غير مُسجَّل في GenieACS")
            continue
        try:
            payload = json.loads(act.get("request_payload") or "{}")
        except ValueError:
            payload = {}
        # الأسرار مشفّرة Fernet — تُفكّ للحظة الإرسال فقط.
        secret = {}
        enc = payload.get("_secret_enc") or ""
        if enc:
            from ...core import env_settings
            try:
                secret = json.loads(env_settings._decrypt(enc) or "{}")
            except ValueError:
                secret = {}
        task = _build_task(act["action_type"], device, secret)
        if task is None:
            tr069_repo.update_action(aid, status="failed", failed_at=_now(),
                                     error_code="unsupported",
                                     error_message="الأمر غير مدعوم لهذا الموديل")
            continue
        res = client.create_task(device["acs_device_id"], task, connection_request=True)
        if res.ok:
            task_id = ""
            if isinstance(res.data, dict):
                task_id = str(res.data.get("_id") or "")
            tr069_repo.update_action(aid, status="sent", sent_at=_now(), acs_task_id=task_id,
                                     result_payload=json.dumps({"queued": True}))
        else:
            # لو الجهاز offline: يبقى Task في GenieACS ويُنفَّذ عند أوّل Inform.
            tr069_repo.update_action(aid, status="waiting_for_inform", sent_at=_now(),
                                     error_message=res.error or "بانتظار اتصال الراوتر")
        # لا نُخزّن الأسرار: نمسح _secret من request_payload بعد الإرسال.
        tr069_repo.update_action(aid, request_payload=json.dumps(
            {"params": payload.get("params", {})}))
        processed += 1
    return processed


# ─────────────── مزامنة الأجهزة ───────────────
def reconcile_from_genieacs(tenant_id: int, client: GenieAcsClient | None = None) -> int:
    client = client or get_client()
    devices = client.list_devices(limit=500)
    offline_sec = config.offline_after_minutes(tenant_id) * 60
    updated = 0
    for gd in devices:
        try:
            if _reconcile_one(tenant_id, gd, offline_sec=offline_sec):
                updated += 1
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("[tr069] reconcile one failed: %s", exc)
    return updated


def _reconcile_one(tenant_id: int, gd: dict, *, offline_sec: int = 600) -> bool:
    acs_id = str(gd.get("_id") or "")
    if not acs_id:
        return False
    row = tr069_repo.get_by_acs_id(tenant_id, acs_id)

    di = gd.get("InternetGatewayDevice", {}) or gd.get("Device", {}) or {}
    root = "InternetGatewayDevice" if "InternetGatewayDevice" in gd else (
        "Device" if "Device" in gd else "")
    info = di.get("DeviceInfo", {}) if isinstance(di, dict) else {}
    inform_iso = str(gd.get("_lastInform") or "")
    # online = بلّغ حديثًا (أحدث من العتبة). GenieACS يُبقي الجهاز في قائمته حتى
    # وهو مفصول، فالحكم بعمر آخر Inform لا بمجرّد وجوده. تعذّر التحليل ⇒ online
    # (لا نُطلق فصلًا كاذبًا).
    age = _age_seconds(inform_iso)
    online = 1 if (age is None or age <= offline_sec) else 0
    fields: dict[str, Any] = {
        "manufacturer": _gval(info.get("Manufacturer")) or "",
        "model_name": _gval(info.get("ModelName")) or "",
        "hardware_version": _gval(info.get("HardwareVersion")) or "",
        "software_version": _gval(info.get("SoftwareVersion")) or "",
        "serial_number": _gval(info.get("SerialNumber")) or (
            _gval(gd.get("_deviceId", {}).get("SerialNumber")) if isinstance(gd.get("_deviceId"), dict) else ""),
        "product_class": (_gval(gd.get("_deviceId", {}).get("ProductClass"))
                          if isinstance(gd.get("_deviceId"), dict) else "") or "",
        "oui": (_gval(gd.get("_deviceId", {}).get("OUI"))
                if isinstance(gd.get("_deviceId"), dict) else "") or "",
        "root_object": root,
        "data_model": "tr098" if root == "InternetGatewayDevice" else ("tr181" if root else ""),
        "last_inform_at": inform_iso or _now(),
        "is_online": online,
        "last_sync_at": _now(),
    }
    # WAN IP/MAC/PPPoE + حالة اتصال WAN عبر resolver (best-effort).
    probe = {"root_object": root, "data_model": fields["data_model"]}
    conn_status = ""
    for key, col in (("wan_ip", "wan_ip"), ("wan_mac", "wan_mac"),
                     ("pppoe_username", "pppoe_username_detected"),
                     ("wan_connection_status", "_conn_status")):
        p = parameter_resolver.resolve_path(probe, key)
        if p:
            v = _dig(gd, p)
            if v:
                if col == "_conn_status":
                    conn_status = str(v)
                else:
                    fields[col] = str(v)
    internet = _derive_internet(bool(online), conn_status,
                                fields.get("wan_ip") or (row.get("wan_ip") if row else ""))
    fields["wan_status"] = conn_status
    fields["internet_status"] = internet

    if row:
        # اكتشاف الانتقالات قبل الكتابة (edge-triggered) لطوابع التغيّر والتنبيه.
        if bool(row.get("is_online")) != bool(online):
            fields["last_online_change_at"] = _now()
        if str(row.get("internet_status") or "unknown") != internet:
            fields["last_internet_change_at"] = _now()
        tr069_repo.update_device(tenant_id, row["id"], **fields)
        tr069_repo.record_event(tenant_id, device_id=row["id"], acs_device_id=acs_id,
                                event_type="inform")
        # تنبيهات فصل/عودة الراوتر + الإنترنت (لا يرفع، حدّيّ فقط).
        off_min = int(age // 60) if (age is not None and not online) else 0
        alerts.evaluate(tenant_id, row, now_online=bool(online),
                        new_internet=internet, fields=fields, offline_minutes=off_min)
        # جهاز مكتشَف/مُسجَّل لم يُربَط بعد: جرّب الربط المسبق بالسيريال (قد يكون
        # المالك سجّل السيريال بعد ظهور الجهاز).
        if not (row.get("radius_username") or row.get("subscriber_id")):
            _try_serial_autolink(tenant_id, row["id"], fields.get("serial_number"))
        return True

    # جهاز جديد في GenieACS: (1) طابقه بتسجيل معلّق عبر رمز التسجيل، أو
    # (2) بلا لمس — أنشئه كجهاز «مكتشَف» ثم اربطه مسبقًا بالسيريال إن أمكن.
    if _match_pending(tenant_id, gd, acs_id, fields):
        return True
    return _create_discovered(tenant_id, acs_id, fields)


def _create_discovered(tenant_id: int, acs_id: str, fields: dict) -> bool:
    """تسجيل تلقائيّ (Zero-touch): جهاز اتّصل بـ ACS بلا رمز تسجيل مسبق.

    يُنشأ بحالة active/discovered وغير مربوط بمشترك؛ ثم يُطبَّق الربط المسبق
    بالسيريال إن وُجد. عزل الملكيّة يبقى مضمونًا (owner_admin_id يأتي من الربط
    المسبق فقط — قبله الجهاز «غير مربوط» يراه المالك/السوبر)."""
    device_id = tr069_repo.create_device(
        tenant_id, acs_device_id=acs_id, status="active",
        provisioning_status="provisioned", origin="discovered", **fields)
    tr069_repo.record_event(tenant_id, device_id=device_id, acs_device_id=acs_id,
                            event_type="discovered")
    _try_serial_autolink(tenant_id, device_id, fields.get("serial_number"))
    return True


def _try_serial_autolink(tenant_id: int, device_id: int, serial: Any) -> bool:
    """يربط جهازًا بمشترك آليًّا إن سبق تسجيل سيريال الراوتر في الربط المسبق.

    لا يمسّ جهازًا مربوطًا مسبقًا؛ يُستدعى فقط للأجهزة غير المربوطة."""
    serial = str(serial or "").strip()
    if not serial:
        return False
    binding = tr069_repo.get_serial_binding(tenant_id, serial)
    if not binding:
        return False
    tr069_repo.update_device(
        tenant_id, device_id,
        subscriber_id=binding.get("subscriber_id"),
        radius_username=binding.get("radius_username") or "",
        owner_admin_id=binding.get("owner_admin_id"))
    tr069_repo.record_assignment(
        tenant_id, device_id=device_id, subscriber_id=binding.get("subscriber_id"),
        radius_username=binding.get("radius_username") or "",
        assigned_by="auto", method="serial")
    tr069_repo.record_event(tenant_id, device_id=device_id, event_type="auto_linked",
                            detail={"by": "serial", "serial": serial})
    return True


def _match_pending(tenant_id: int, gd: dict, acs_id: str, fields: dict) -> bool:
    """يطابق أوّل Inform بجهاز pending عبر tag رمز التسجيل، فيُفعّله."""
    tags = gd.get("_tags") or []
    token_hashes = set()
    import hashlib
    for t in tags:
        token_hashes.add(hashlib.sha256(str(t).encode("utf-8")).hexdigest())
    # ابحث في الأجهزة المعلّقة عن تطابق رمز.
    for dev in tr069_repo.list_devices(tenant_id, status="pending", limit=500):
        if dev.get("enrollment_token_hash") and dev["enrollment_token_hash"] in token_hashes:
            tr069_repo.update_device(
                tenant_id, dev["id"], acs_device_id=acs_id, status="active",
                provisioning_status="provisioned", **fields)
            tr069_repo.record_event(tenant_id, device_id=dev["id"], acs_device_id=acs_id,
                                    event_type="online")
            return True
    return False
