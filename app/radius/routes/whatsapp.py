"""WhatsApp subscriber-messaging — thin-client admin page + signed bridge.

This module is deliberately a THIN CLIENT. It NEVER:
  * stores any provider credential (token / business-account id / app secret /
    verify-token) anywhere,
  * talks to the upstream messaging provider's HTTP API directly,
  * sends a real WhatsApp message itself.

Every WhatsApp action goes through the signed AdminPanelClient bridge to the
license panel, which owns the provider credentials and performs the real send.
The only thing stored locally is a set of per-event ON/OFF gates in
tenant_settings (``whatsapp.send.*``) — radius-side toggles that decide whether
the module is even allowed to ASK the panel to message subscribers for that
event. No provider secrets are stored or transmitted by this module.

To honour the "never log phone numbers / message bodies at INFO" rule, this
module logs nothing about recipients or message content.
"""
from __future__ import annotations

import time
import uuid

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

# The business events the operator can gate WhatsApp messaging on. Each maps to
# a tenant_settings key ``whatsapp.send.<event>`` (default OFF). These are pure
# radius-side gates — they carry NO Meta secrets.
WHATSAPP_EVENTS: tuple[tuple[str, str], ...] = (
    ("otp", "رمز التحقق (OTP) عند الدخول"),
    ("expiry", "تنبيه قرب انتهاء الاشتراك"),
    ("quota", "تنبيه قرب نفاد الباقة"),
    ("maintenance", "إشعارات الصيانة والانقطاع"),
    ("password", "تغيير كلمة المرور"),
    ("portal", "روابط ودعوات بوابة المشترك"),
)

# Where the operator manages the actual Meta connection — on the panel portal.
PANEL_PORTAL_WHATSAPP_PATH = "/portal/whatsapp"


def register_whatsapp_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/whatsapp", "whatsapp", whatsapp_page, methods=["GET"])
    bp.add_url_rule("/whatsapp/settings", "whatsapp_settings", whatsapp_settings, methods=["POST"])
    bp.add_url_rule("/whatsapp/test", "whatsapp_test", whatsapp_test, methods=["POST"])
    bp.add_url_rule("/whatsapp/cloud-test", "whatsapp_cloud_test", whatsapp_cloud_test, methods=["POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _admin_id() -> int:
    try:
        return int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        return 0


def _setting_key(event: str) -> str:
    return f"whatsapp.send.{event}"


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _event_toggles(tenant_id: int) -> dict[str, bool]:
    """Read every per-event gate from tenant_settings (default OFF)."""
    from ..db.repos import tenants_repo

    toggles: dict[str, bool] = {}
    for key, _label in WHATSAPP_EVENTS:
        try:
            raw = tenants_repo.get_setting(tenant_id, _setting_key(key), "0")
        except Exception:  # noqa: BLE001 — the page must always render
            raw = "0"
        toggles[key] = _truthy(raw)
    return toggles


def _panel_portal_url() -> str:
    """Absolute URL to «إدارة الربط من لوحة التراخيص», if a panel URL is set.

    Reads the existing ``license_admin_bridge.base_url`` setting (NOT a provider
    secret). Returns "" when no panel URL is configured so the template can
    hide the link rather than render a broken one.
    """
    from ..services.admin_panel_client import bridge_setting

    base = (bridge_setting("license_admin_bridge.base_url", "") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}{PANEL_PORTAL_WHATSAPP_PATH}"


def _safe_status() -> dict:
    """Fetch the WhatsApp status from the panel, never raising.

    On ANY failure (bridge disabled, HTTPS missing, timeout, bad payload) we
    return a dict with ``ok=False`` so the template renders the pending card
    «تعذّر جلب الحالة من لوحة التراخيص» instead of 500-ing.
    """
    from ..services.admin_panel_client import AdminPanelClient

    try:
        status = AdminPanelClient().get_whatsapp_status()
    except Exception:  # noqa: BLE001 — bridge errors must never break the page
        return {"ok": False, "status": "unavailable"}
    if not isinstance(status, dict):
        return {"ok": False, "status": "unavailable"}
    return status


def whatsapp_page():
    """GET — the WhatsApp subscriber-messaging admin page.

    Shows the panel-reported connection/usage status, the local per-event
    gates, an opt-in note, a test-message form, and a link to manage the Meta
    connection on the panel portal. No Meta token field anywhere.
    """
    tid = _tid()
    status = _safe_status()
    response = status.get("response") if isinstance(status.get("response"), dict) else {}
    # The panel may return the connection facts either at the top level or under
    # ``response`` (the bridge wraps successful payloads in ``response``).
    facts = response or status
    view = {
        "ok": bool(status.get("ok")),
        "status": status.get("status") or "unavailable",
        "enabled": bool(facts.get("enabled")),
        "connected": bool(facts.get("connected")),
        "phone": facts.get("phone") or facts.get("phone_number") or "",
        "usage": facts.get("usage") if isinstance(facts.get("usage"), dict) else {},
    }
    return render_template(
        "radius/whatsapp.html",
        status=view,
        events=WHATSAPP_EVENTS,
        toggles=_event_toggles(tid),
        panel_portal_url=_panel_portal_url(),
    )


def whatsapp_settings():
    """POST — persist the local per-event gates (radius-side only, no secrets)."""
    tid = _tid()
    from ..db.repos import tenants_repo

    try:
        for key, _label in WHATSAPP_EVENTS:
            value = "1" if request.form.get(f"send_{key}") else "0"
            tenants_repo.set_setting(tid, _setting_key(key), value, by=_admin_id())
        flash("تم حفظ إعدادات رسائل واتساب للمشتركين.", "success")
    except Exception:  # noqa: BLE001 — settings must never 500 the page
        flash("تعذّر حفظ الإعدادات. حاول مرة أخرى.", "error")
    return redirect(url_for("radius.whatsapp"))


def whatsapp_test():
    """POST — ask the panel to send a single WhatsApp test message.

    Routes through the signed bridge only. We build a stable idempotency key so
    a double-submit doesn't double-send. The recipient phone is NOT logged.
    """
    phone = (request.form.get("recipient_phone") or "").strip()
    if not phone:
        flash("أدخل رقم هاتف لإرسال رسالة الاختبار.", "error")
        return redirect(url_for("radius.whatsapp"))

    # Stable-ish key: tenant + phone + coarse minute bucket → resending within
    # the same minute is deduped by the panel, but a later retry is a new send.
    bucket = int(time.time() // 60)
    idempotency_key = f"wa-test-{_tid()}-{uuid.uuid5(uuid.NAMESPACE_DNS, f'{phone}:{bucket}').hex}"

    from ..services.admin_panel_client import AdminPanelClient

    try:
        result = AdminPanelClient().send_whatsapp_test(
            recipient_phone=phone,
            idempotency_key=idempotency_key,
        )
    except Exception:  # noqa: BLE001 — bridge errors must never 500 the page
        result = {"ok": False, "status": "unavailable"}

    status = _status_label(result.get("status"))
    if result.get("ok"):
        flash(f"تم إرسال رسالة الاختبار عبر لوحة التراخيص. الحالة: {status}.", "success")
    else:
        flash(
            f"تعذّر إرسال رسالة الاختبار: {status}. "
            "تأكد من ربط واتساب وتفعيل الجسر في صفحة «ملف التراخيص» ثم أعد المحاولة.",
            "error",
        )
    return redirect(url_for("radius.whatsapp"))


def whatsapp_cloud_test():
    """POST — ask the panel to send a test message via its HOUSE Cloud API
    credentials (the panel settings), through the signed bridge.

    This is the companion to ``whatsapp_test`` (per-customer): it verifies the
    panel's house WhatsApp pipe end-to-end from radius-module. Test-only; the
    recipient phone is NOT logged.
    """
    phone = (request.form.get("recipient_phone") or "").strip()
    if not phone:
        flash("أدخل رقم هاتف لإرسال رسالة الاختبار.", "error")
        return redirect(url_for("radius.whatsapp"))
    template_name = (request.form.get("template_name") or "").strip()
    language = (request.form.get("language") or "").strip()

    from ..services.admin_panel_client import AdminPanelClient

    try:
        result = AdminPanelClient().send_whatsapp_cloud_test(
            recipient_phone=phone, template_name=template_name, language=language,
        )
    except Exception:  # noqa: BLE001 — bridge errors must never 500 the page
        result = {"ok": False, "status": "unavailable"}

    # The bridge wraps the panel's JSON under ``response`` and reports its OWN
    # ``ok`` for transport success. A real send requires BOTH: the bridge call
    # succeeded AND the panel reported ok.
    panel = result.get("response") if isinstance(result.get("response"), dict) else {}
    if result.get("ok") and panel.get("ok"):
        flash("تم إرسال رسالة الاختبار عبر بيانات اللوحة (Cloud API). تحقّق من واتساب المستلم.", "success")
    else:
        reason = panel.get("message_ar") or _status_label(result.get("status"))
        flash("تعذّر إرسال رسالة الاختبار: " + str(reason), "error")
    return redirect(url_for("radius.whatsapp"))


def _status_label(status: object) -> str:
    """Translate a bridge status code into a friendly Arabic label."""
    try:
        from .admin_bridge import _sync_status_label

        return _sync_status_label(status)
    except Exception:  # noqa: BLE001
        return str(status or "unknown")
