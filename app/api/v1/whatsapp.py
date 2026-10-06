"""WhatsApp bridge controls API.

This mirrors the web `/admin/radius/whatsapp` page as JSON for native
Flutter clients. The runtime remains a thin client: provider secrets and real
sending stay in the license panel, while radius-module only stores local
per-event gates and calls the signed admin-panel bridge.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import time
import uuid
from typing import Any

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


WHATSAPP_EVENTS: tuple[tuple[str, str], ...] = (
    ("otp", N_("رمز التحقق عند الدخول")),
    ("expiry", N_("تنبيه قرب انتهاء الاشتراك")),
    ("maintenance", N_("إشعارات الصيانة والانقطاع")),
    ("password", N_("تغيير كلمة المرور")),
)

# «نفاد الباقة» (quota) و«روابط بوابة المشترك» (portal) أُزيلا — قرار المالك
# 2026-10-06: لا مُرسِل يستدعي هاتين البوّابتين. تُقبَلان بصمت (نسخ التطبيق
# القديمة) ولا تُكتبان؛ القيمة المخزّنة تبقى بلا قارئ.
RETIRED_EVENTS = frozenset({"quota", "portal"})

EVENT_HELP = {
    "otp": N_("إرسال رمز تحقق للمشترك عند تسجيل الدخول للتأكد من هويته."),
    "expiry": N_("تذكير المشترك قبل انتهاء اشتراكه ليجدّد في الوقت المناسب."),
    "maintenance": N_("إبلاغ المشتركين بأعمال الصيانة أو الانقطاع المجدول."),
    "password": N_("إشعار المشترك فور تغيير كلمة مروره حمايةً لحسابه."),
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
                N_("لا يخزن الريدياس أي مفاتيح أو رموز من واتساب."),
                N_("الإرسال الرسمي يتم عبر لوحة التراخيص فقط."),
                N_("هذه المفاتيح تتحكم بما يُسمح للريدياس بطلبه من اللوحة."),
            ],
        }
    )


def whatsapp_settings_save():
    from ...radius.db.repos import tenants_repo

    tenant_id = _tid()
    data = _body()
    raw = data.get("toggles") if isinstance(data.get("toggles"), dict) else data
    allowed = {key for key, _label in WHATSAPP_EVENTS} | RETIRED_EVENTS
    unknown = sorted(str(key) for key in raw if str(key) not in allowed)
    if unknown:
        return fail(
            "validation_error",
            _tr("يوجد نوع رسالة غير معروف."),
            status=422,
            details={"unknown": unknown},
        )

    for key, _label in WHATSAPP_EVENTS:
        # Parity-b: a partial map updates only the keys it names (an absent
        # key used to switch that message OFF).
        if key not in raw:
            continue
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
            "message": _tr("تم حفظ إعدادات رسائل واتساب للمشتركين."),
        }
    )


def whatsapp_test_send():
    phone = str(_body().get("recipient_phone") or "").strip()
    if not phone:
        return fail("validation_error", _tr("أدخل رقم هاتف لإرسال رسالة الاختبار."), status=422)

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
    return _send_result(result, success_message=N_("تم إرسال رسالة الاختبار عبر لوحة التراخيص."))


def whatsapp_cloud_test_send():
    data = _body()
    phone = str(data.get("recipient_phone") or "").strip()
    if not phone:
        return fail("validation_error", _tr("أدخل رقم هاتف لإرسال رسالة الاختبار."), status=422)

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
        return ok({"message": _tr("تم إرسال رسالة الاختبار عبر بيانات اللوحة."), "status": "sent"})
    reason = panel.get("message_ar") or _status_label(result.get("status"))
    return fail("whatsapp_send_failed", _tr('تعذّر إرسال رسالة الاختبار: %(reason)s.', reason=reason), status=502)


def _send_result(result: dict[str, Any], *, success_message: str):
    if result.get("ok"):
        return ok({"message": success_message, "status": _status_label(result.get("status"))})
    return fail(
        "whatsapp_send_failed",
        _tr('تعذّر إرسال رسالة الاختبار: %(v)s.', v=_status_label(result.get('status'))),
        status=502,
    )


def _onboarding_label(value: str, ok_status: bool) -> str:
    if not ok_status:
        return N_("غير متوفّرة")
    return {
        "connected": N_("متصل"),
        "not_connected": N_("غير متصل"),
        "needs_setup": N_("بحاجة إلى الإعداد"),
    }.get(value, N_("بحاجة إلى الإعداد"))


def _status_label(status: object) -> str:
    return {
        "success": _tr("ناجح"),
        "sent": N_("تم الإرسال"),
        "queued": N_("في الطابور"),
        "unavailable": N_("غير متوفر"),
        "error": _tr("خطأ"),
        "timeout": N_("انتهت المهلة"),
        "not_configured": N_("غير مهيأ"),
    }.get(str(status or "").strip(), N_("غير معروف"))
