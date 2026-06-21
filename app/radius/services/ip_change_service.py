"""ip_change_service — منطق خدمة «تغيير الـIP» المدفوعة (جانب العميل).

المرحلة 1 (هذا الملف): جانب العميل فقط — قراءة حالة منح المزوّد، سعر
الميغا، طلبات التفعيل المُرسَلة، وبوابة قراءة بيانات التزويد (SSTP) التي
ستصل لاحقًا عبر الجسر من لوحة التراخيص.

العقد (مؤكَّد من المالك):
  • الخدمة مدفوعة، السعر **لكلّ ميغا من السرعة (Mbps)** — قابل للضبط.
  • اشتراك **شهريّ متجدّد**، البيانات **غير محدودة** (الشراء للسرعة لا الكمّيّة).
  • الطلب يُرسَل عبر مسار الطلبات الموحّد (POST /admin/radius/service-requests
    → service_specs.validate_spec → tenant_settings) حاملًا:
    requested_speed_mbps + billing_cycle=monthly + data_limit=unlimited.
  • الدفع/الموافقة/التزويد على جهة لوحة التراخيص (مهمّة منفصلة).

لا منطق مايكروتيك هنا (حقن سكربت «تغيير الـIP» بضغطة = مرحلة لاحقة).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import tenants_repo
from . import provider_grant

# مفتاح خدمة المزوّد (بوابة المنح) + نوع المواصفات (service_specs).
SERVICE_KEY = "ip_change"
SERVICE_TYPE = "ip_change"

# مفتاح إعداد سعر الميغا المحلّي (احتياط لو لم يُرسله العقد).
PRICE_SETTING_KEY = "ip_change.price_per_mbps"

# بادئات مفاتيح الطلبات المخزّنة في tenant_settings (نفس صيغة
# service_requests.service_request_create: service_requests.<type>.<scope>.<ts>).
_REQUEST_KEY_PREFIXES = (
    "service_requests.ip_change.",
    "service_requests.ipchange.",
    "service_requests.ip-change.",
)

# حالات منح المزوّد التي تُعتبر «مُفعَّلة فعلًا».
_ACTIVE_STATUSES = {"active", "enabled", "granted", "on", "ok"}

# حالات «انتهاء الصلاحية» (كانت مُفعَّلة ثم انقضت/عُلِّقت) — تُظهر مسار التراجع.
_EXPIRED_STATUSES = {"expired", "suspended", "cancelled", "lapsed"}

# نوع لقطة التزويد التي يُسلّمها المزوّد عبر الجسر (السحب). تَحمل بيانات
# SSTP + IP الخادم لخدمة ip_change. (جانب الكتابة على لوحة التراخيص — انظر
# «فجوة العقد» في التقرير؛ هنا جانب القراءة جاهز.)
SNAPSHOT_PROVISIONING = "ip_change_provisioning"


def _tid(tenant_id: int | None = None) -> int:
    if tenant_id is not None:
        return int(tenant_id)
    try:
        from flask import g
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except Exception:  # noqa: BLE001
        return DEFAULT_TENANT_ID


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


# ─────────────────────────────────────────────────────────────────────
# سعر الميغا (قابل للضبط) — يُقرأ من عقد المزوّد أوّلًا، ثم إعداد محلّي.
# ─────────────────────────────────────────────────────────────────────
def price_per_mbps(tenant_id: int | None = None) -> float:
    """سعر تغيير الـIP لكلّ ميغا من السرعة (Mbps).

    ترتيب القراءة:
      1) عقد المزوّد: services.ip_change.price_per_mbps أو
         limits["ip_change.price_per_mbps"] (المزوّد يُسعّر خدمته المدفوعة).
      2) إعداد محلّي: tenant_settings["ip_change.price_per_mbps"] (يَسمح
         للمالك بضبطه محليًّا — admin-configurable).
      3) 0.0 = «غير محدّد بعد» (تَعرضه الواجهة كـ«يُحدَّد عند المراجعة»).
    """
    tid = _tid(tenant_id)
    # (1) عقد المزوّد — حقل صريح ضمن الخدمة.
    try:
        payload = provider_grant.get_payload(tid) or {}
        services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
        svc = services.get(SERVICE_KEY) if isinstance(services, dict) else None
        if isinstance(svc, dict):
            for k in ("price_per_mbps", "price_mbps", "mbps_price"):
                v = _to_float(svc.get(k))
                if v is not None:
                    return v
    except Exception:  # noqa: BLE001 — fail-open
        pass
    # (1b) عقد المزوّد — ضمن limits بمسار منقّط.
    try:
        for path in ("ip_change.price_per_mbps", "ip_change.mbps_price"):
            v = provider_grant.get_limit(tid, path)
            if v is not None:
                return float(v)
    except Exception:  # noqa: BLE001
        pass
    # (2) إعداد محلّي.
    v = _to_float(tenants_repo.get_setting(tid, PRICE_SETTING_KEY, ""))
    if v is not None:
        return v
    # (3) افتراضي.
    return 0.0


def monthly_total(price: float, mbps: float) -> float:
    """الإجمالي الشهريّ = السعر × عدد الميغا."""
    try:
        return round(float(price) * float(mbps), 2)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────
# حالة منح المزوّد للخدمة (بوابة الدفع/التفعيل).
# ─────────────────────────────────────────────────────────────────────
def grant_state(tenant_id: int | None = None) -> dict[str, Any]:
    """يُلخّص حالة الخدمة من عقد المزوّد لاتّخاذ قرار العرض:

      granted=True          → مُفعَّلة (نَعرض قسم الحالة/التزويد).
      granted=False         → غير مُفعَّلة (نَعرض «طلب تفعيل»):
          requires_upgrade  → «مدفوعة-غير-مفعّلة» صراحةً من العقد.
          disabled          → أوقفها المزوّد (نَعرض «غير متاحة»).
          (غير مذكورة)      → افتراضيًّا تحتاج تفعيلًا (paid-not-active).
    """
    tid = _tid(tenant_id)
    g = provider_grant.lookup(tid, SERVICE_KEY)
    status_norm = (g.status or "").strip().lower()
    granted = bool(
        g.present and g.enabled and not g.disabled and not g.requires_upgrade
        and status_norm in _ACTIVE_STATUSES
    )
    # «منتهية»: ذُكرت في العقد وحالتها انتهاء/تعليق — كانت مُفعَّلة فانقضت.
    expired = bool(g.present and status_norm in _EXPIRED_STATUSES)
    return {
        "present": bool(g.present),
        "granted": granted,
        "requires_upgrade": bool(g.requires_upgrade),
        "disabled": bool(g.disabled),
        "expired": expired,
        "status": g.status,
    }


def expiry_state(tenant_id: int | None = None) -> dict[str, Any]:
    """حالة انتهاء الصلاحية للعرض + قرار «تراجع» (المرحلة 5).

    نَعكس الحالة الحقيقيّة من عقد المزوّد (read-on-render) — لا إنفاذ وهميّ:
      expired=True  → الاشتراك منتهٍ/مُعلَّق؛ نَعرض لافتة + مسار تراجع.
    expires_at من لقطة التزويد إن توفّر (عرض تنبيهيّ فقط)."""
    tid = _tid(tenant_id)
    g = grant_state(tid)
    prov = provision(tid)
    return {
        "expired": bool(g["expired"]),
        "status": g["status"],
        "expires_at": (prov or {}).get("expires_at"),
        # كان مُزوَّدًا سابقًا؟ (يوجد طلب أو تزويد) — يجعل «التراجع» ذا معنى.
        "had_service": bool(prov) or bool(list_requests(tid)),
    }


# ─────────────────────────────────────────────────────────────────────
# طلبات التفعيل المُرسَلة (من مسار /service-requests → tenant_settings).
# ─────────────────────────────────────────────────────────────────────
def list_requests(tenant_id: int | None = None) -> list[dict[str, Any]]:
    """كلّ طلبات «تغيير الـIP» المخزّنة لهذا المستأجر، الأحدث أوّلًا.

    نقرأ من نفس مخزن مسار الطلبات الموحّد (tenant_settings)، فلا قناة
    مكرّرة — مصدر واحد للحقيقة."""
    from ..db.connection import db
    tid = _tid(tenant_id)
    items: list[dict[str, Any]] = []
    for prefix in _REQUEST_KEY_PREFIXES:
        rows = db().execute(
            "SELECT key, value, updated_at FROM tenant_settings "
            "WHERE tenant_id=? AND key LIKE ? ",
            (tid, prefix + "%"),
        ).fetchall()
        for r in rows:
            rec = dict(r)
            try:
                data = json.loads(rec.get("value") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            data["_key"] = rec.get("key")
            data["_updated_at"] = rec.get("updated_at")
            items.append(data)
    items.sort(key=lambda d: int(d.get("requested_at") or 0), reverse=True)
    return items


def latest_request(tenant_id: int | None = None) -> Optional[dict[str, Any]]:
    items = list_requests(tenant_id)
    return items[0] if items else None


# ─────────────────────────────────────────────────────────────────────
# بوّابة قراءة بيانات التزويد (SSTP user/pass + server IP).
# ─────────────────────────────────────────────────────────────────────
def _decrypt_maybe(enc: str) -> str:
    """يفكّ تشفير كلمة المرور المُسلَّمة مشفّرة (Fernet) إن أمكن — best-effort."""
    if not enc:
        return ""
    try:
        from .vpn_account_service import _decrypt  # type: ignore
        out = _decrypt(str(enc))
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    return ""


def provision(tenant_id: int | None = None) -> Optional[dict[str, Any]]:
    """بيانات التزويد (SSTP + IP الخادم) المُسلَّمة من لوحة التراخيص عبر
    جسر السحب، أو None لو لم تصل بعد.

    يقرأ آخر لقطة ناجحة من نوع SNAPSHOT_PROVISIONING (نفس مخزن لقطات الجسر
    الذي يقرأه provider_grant)، ويستخرج services.ip_change. يُعيد القيم فقط
    عند status=provisioned ووجود الحدّ الأدنى (خادم + مستخدم + كلمة مرور).
    كلمة المرور تُقبل صريحة (sstp_password) أو مشفّرة (sstp_password_enc).

    **فجوة العقد عبر اللوحات:** جانب الكتابة (لوحة التراخيص تُسلّم هذه اللقطة
    بعد الموافقة+الدفع) مهمّة منفصلة — هذا جانب القراءة جاهز للعرض فور وصولها.
    """
    tid = _tid(tenant_id)
    try:
        from .admin_panel_client import LicenseAdminSnapshotStore
        snap = LicenseAdminSnapshotStore().latest_success(
            tenant_id=tid, snapshot_type=SNAPSHOT_PROVISIONING)
    except Exception:  # noqa: BLE001 — fail-open: لا تزويد = None
        return None
    if not snap:
        return None
    payload = snap.get("payload_json") or {}
    if not isinstance(payload, dict):
        return None
    services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
    svc = services.get(SERVICE_KEY) if isinstance(services, dict) else None
    if not isinstance(svc, dict):
        return None
    if str(svc.get("status") or "").strip().lower() != "provisioned":
        return None
    username = str(svc.get("sstp_username") or "").strip()
    password = str(svc.get("sstp_password") or "").strip() \
        or _decrypt_maybe(str(svc.get("sstp_password_enc") or ""))
    server_ip = str(svc.get("server_ip") or "").strip()
    server_host = str(svc.get("server_host") or "").strip() or server_ip
    if not (username and password and (server_ip or server_host)):
        return None
    speed = svc.get("speed_mbps")
    return {
        "status": "provisioned",
        "server_host": server_host,
        "server_ip": server_ip,
        "sstp_username": username,
        "sstp_password": password,
        "speed_mbps": int(speed) if str(speed or "").strip().isdigit() else None,
        "provisioned_at": svc.get("provisioned_at"),
        "expires_at": svc.get("expires_at"),
    }


def is_provisioned(tenant_id: int | None = None) -> bool:
    return provision(tenant_id) is not None


__all__ = [
    "SERVICE_KEY", "SERVICE_TYPE", "PRICE_SETTING_KEY", "SNAPSHOT_PROVISIONING",
    "price_per_mbps", "monthly_total", "grant_state", "expiry_state",
    "list_requests", "latest_request", "provision", "is_provisioned",
]
