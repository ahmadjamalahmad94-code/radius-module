"""WhatsApp bridge controls API.

This mirrors the web `/admin/radius/whatsapp` page as JSON for native
Flutter clients. The runtime remains a thin client: provider secrets and real
sending stay in the license panel, while radius-module only stores local
per-event gates and calls the signed admin-panel bridge.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


WHATSAPP_EVENTS: tuple[tuple[str, str], ...] = (
    ("otp", "رمز التحقق عند الدخول"),
    ("expiry", "تنبيه قرب انتهاء الاشتراك"),
    ("quota", "تنبيه قرب نفاد الباقة"),
    ("maintenance", "إشعارات الصيانة والانقطاع"),
    ("password", "تغيير كلمة المرور"),
    ("portal", "روابط ودعوات بوابة المشترك"),
)

EVENT_HELP = {
    "otp": "إرسال رمز تحقق للمشترك عند تسجيل الدخول للتأكد من هويته.",
    "expiry": "تذكير المشترك قبل انتهاء اشتراكه ليجدّد في الوقت المناسب.",
    "quota": "تنبيه المشترك عندما تقترب باقته من النفاد.",
    "maintenance": "إبلاغ المشتركين بأعمال الصيانة أو الانقطاع المجدول.",
    "password": "إشعار المشترك فور تغيير كلمة مروره حمايةً لحسابه.",
    "portal": "إرسال روابط الدخول والدعوات إلى بوابة المشترك.",
}

PANEL_PORTAL_WHATSAPP_PATH = "/portal/whatsapp"


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/whatsapp",
        "whatsapp_state",
        require_api_token(whatsapp_state),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/whatsapp/settings",
        "whatsapp_settings_save",
        require_api_token(whatsapp_settings_save),
        methods=["PATCH"],
    )
    bp.add_url_rule(
        "/whatsapp/test",
        "whatsapp_test_send",
        require_api_token(whatsapp_test_send),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/whatsapp/cloud-test",
        "whatsapp_cloud_test_send",
        require_api_token(whatsapp_cloud_test_send),
        methods=["POST"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1) or 1)


def _admin_id() -> int:
    try:
        return int(getattr(g, "admin_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _setting_key(event: str) -> str:
    return f"whatsapp.send.{event}"


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _event_toggles(tenant_id: int) -> dict[str, bool]:
    from ...radius.db.repos import tenants_repo

    toggles: dict[str, bool] = {}
    for key, _label in WHATSAPP_EVENTS:
        try:
            raw = tenants_repo.get_setting(tenant_id, _setting_key(key), "0")
        except Exception:  # noqa: BLE001
            raw = "0"
        toggles[key] = _truthy(raw)
    return toggles


def _event_payloads(toggles: dict[str, bool]) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": label,
            "help": EVENT_HELP.get(key, ""),
            "setting_key": _setting_key(key),
            "enabled": bool(toggles.get(key)),
        }
        for key, label in WHATSAPP_EVENTS
    ]


def _panel_portal_url() -> str:
    from ...radius.services.admin_panel_client import bridge_setting

    base = (bridge_setting("license_admin_bridge.base_url", "") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}{PANEL_PORTAL_WHATSAPP_PATH}"


def _safe_status() -> dict[str, Any]:
    from ...radius.services.admin_panel_client import AdminPanelClient

    try:
        status = AdminPanelClient().get_whatsapp_status()
    except Exception:  # noqa: BLE001
        return {"ok": False, "status": "unavailable"}
    return status if isinstance(status, dict) else {"ok": False, "status": "unavailable"}


def _status_view(raw: dict[str, Any]) -> dict[str, Any]:
    response = raw.get("response") if isinstance(raw.get("response"), dict) else {}
    facts = response or raw
    account_status = str(facts.get("account_status") or "").strip()
    if not account_status and facts.get("connected") is not None:
        account_status = "connected" if facts.get("connected") else "disconnected"
    onboarding = str(facts.get("onboarding_state") or "").strip()
    if onboarding not in ("connected", "not_connected", "needs_setup"):
        if account_status == "connected":
            onboarding = "connected"
        elif account_status in ("disconnected", "error", "suspended"):
            onboarding = "not_connected"
        else:
            onboarding = "needs_setup"

    usage = facts.get("usage") if isinstance(facts.get("usage"), dict) else {}
    return {
        "ok": bool(raw.get("ok")),
        "status": str(raw.get("status") or "unavailable"),
        "enabled": bool(facts.get("enabled")),
        "connected": account_status == "connected",
        "onboarding": onboarding,
        "onboarding_label": _onboarding_label(onboarding, bool(raw.get("ok"))),
        "phone": str(
            facts.get("display_phone_number")
            or facts.get("phone")
            or facts.get("phone_number")
            or ""
        ),
        "business": str(facts.get("business_display_name") or ""),
        "usage": usage,
    }


def whatsapp_state():
    toggles = _event_toggles(_tid())
    status = _status_view(_safe_status())
    return ok(
        {
            "status": status,
            "events": _event_payloads(toggles),
            "panel_portal_url": _panel_portal_url(),
            "principles": [
                "لا يخزن الريدياس أي مفاتيح أو رموز من واتساب.",
                "الإرسال الرسمي يتم عبر لوحة التراخيص فقط.",
                "هذه المفاتيح تتحكم بما يُسمح للريدياس بطلبه من اللوحة.",
            ],
        }
    )


def whatsapp_settings_save():
    from ...radius.db.repos import tenants_repo

    tenant_id = _tid()
    data = _body()
    raw = data.get("toggles") if isinstance(data.get("toggles"), dict) else data
    allowed = {key for key, _label in WHATSAPP_EVENTS}
    unknown = sorted(str(key) for key in raw if str(key) not in allowed)
    if unknown:
        return fail(
            "validation_error",
            "يوجد نوع رسالة غير معروف.",
            status=422,
            details={"unknown": unknown},
        )

    for key, _label in WHATSAPP_EVENTS:
        tenants_repo.set_setting(
            tenant_id,
            _setting_key(key),
            "1" if _truthy(raw.get(key)) else "0",
            by=_admin_id(),
        )
    toggles = _event_toggles(tenant_id)
    return ok(
        {
            "events": _event_payloads(toggles),
            "message": "تم حفظ إعدادات رسائل واتساب للمشتركين.",
        }
    )


def whatsapp_test_send():
    phone = str(_body().get("recipient_phone") or "").strip()
    if not phone:
        return fail("validation_error", "أدخل رقم هاتف لإرسال رسالة الاختبار.", status=422)

    from ...radius.services.admin_panel_client import AdminPanelClient

    bucket = int(time.time() // 60)
    idempotency_key = (
        f"wa-test-{_tid()}-"
        f"{uuid.uuid5(uuid.NAMESPACE_DNS, f'{phone}:{bucket}').hex}"
    )
    try:
        result = AdminPanelClient().send_whatsapp_test(
            recipient_phone=phone,
            idempotency_key=idempotency_key,
        )
    except Exception:  # noqa: BLE001
        result = {"ok": False, "status": "unavailable"}
    return _send_result(result, success_message="تم إرسال رسالة الاختبار عبر لوحة التراخيص.")


def whatsapp_cloud_test_send():
    data = _body()
    phone = str(data.get("recipient_phone") or "").strip()
    if not phone:
        return fail("validation_error", "أدخل رقم هاتف لإرسال رسالة الاختبار.", status=422)

    from ...radius.services.admin_panel_client import AdminPanelClient

    try:
        result = AdminPanelClient().send_whatsapp_cloud_test(
            recipient_phone=phone,
            template_name=str(data.get("template_name") or "").strip(),
            language=str(data.get("language") or "").strip(),
        )
    except Exception:  # noqa: BLE001
        result = {"ok": False, "status": "unavailable"}
    panel = result.get("response") if isinstance(result.get("response"), dict) else {}
    if result.get("ok") and panel.get("ok"):
        return ok({"message": "تم إرسال رسالة الاختبار عبر بيانات اللوحة.", "status": "sent"})
    reason = panel.get("message_ar") or _status_label(result.get("status"))
    return fail("whatsapp_send_failed", f"تعذّر إرسال رسالة الاختبار: {reason}.", status=502)


def _send_result(result: dict[str, Any], *, success_message: str):
    if result.get("ok"):
        return ok({"message": success_message, "status": _status_label(result.get("status"))})
    return fail(
        "whatsapp_send_failed",
        f"تعذّر إرسال رسالة الاختبار: {_status_label(result.get('status'))}.",
        status=502,
    )


def _onboarding_label(value: str, ok_status: bool) -> str:
    if not ok_status:
        return "غير متوفّرة"
    return {
        "connected": "متصل",
        "not_connected": "غير متصل",
        "needs_setup": "بحاجة إلى الإعداد",
    }.get(value, "بحاجة إلى الإعداد")


def _status_label(status: object) -> str:
    return {
        "success": "ناجح",
        "sent": "تم الإرسال",
        "queued": "في الطابور",
        "unavailable": "غير متوفر",
        "error": "خطأ",
        "timeout": "انتهت المهلة",
        "not_configured": "غير مهيأ",
    }.get(str(status or "").strip(), "غير معروف")
