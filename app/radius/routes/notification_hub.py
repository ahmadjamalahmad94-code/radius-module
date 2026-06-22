"""notification_hub — لوحة «الإشعارات والتواصل» الموحّدة (Phase 1).

يجمع كل تهيئة القنوات + اختيار قنوات أحداث الإدارة في مكان واحد، فوق المحرّك
الموحّد (admin_alerts.dispatch → الجرس دائمًا + تلجرام القانوني). يستبدل الصفحات
المبعثرة (تلجرام الإدارة، تلجرام الشبكة المكرّر، واتساب/SMS، الويبهوك).

ثلاث صفحات تحت المجموعة:
  • /admin/radius/integrations            — التكاملات/القنوات (تلجرام/واتساب/SMS/ويبهوك)
  • /admin/radius/admin-notifications     — إشعارات الإدارة (الجرد + قنوات لكل حدث)
  • /admin/radius/subscriber-notifications — إشعارات المشتركين (هيكل Phase 2)

ومركز الإشعارات + الجرس يعيشان في routes/notifications.py (المركز الموحّد).

الحفظ يُعاد استخدام النقاط القائمة (لا تكرار منطق): تلجرام→/alerts/telegram/bot،
واتساب→/whatsapp/settings، SMS→/communications/channels، الويبهوك→/webhooks.
"""
from __future__ import annotations

from flask import (
    Blueprint, flash, g, jsonify, redirect, render_template, request,
    session, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import tenant_telegram_settings_repo
from ..services import admin_alerts


def _tid() -> int:
    return int(getattr(g, "tenant_id", session.get("tenant_id") or DEFAULT_TENANT_ID))


def _by() -> int:
    try:
        return int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        return 0


# ── أحداث المشترك (هيكل Phase 2 — العرض فقط) ─────────────────────────────
# ~28 حدثًا تجاريًّا تُربط لاحقًا بهاتف ملف المشترك عبر واتساب/SMS (Phase 2).
SUBSCRIBER_EVENTS: list[tuple[str, str, str]] = [
    ("welcome", "ترحيب بمشترك جديد", "عند إنشاء الاشتراك"),
    ("credentials", "بيانات الدخول", "اسم المستخدم/كلمة المرور"),
    ("otp", "رمز تحقّق (OTP)", "عند الدخول/التأكيد"),
    ("expiry_soon", "قرب انتهاء الاشتراك", "قبل الانتهاء بأيام"),
    ("expired", "انتهاء الاشتراك", "عند الانتهاء"),
    ("renewed", "تجديد الاشتراك", "بعد التجديد"),
    ("quota_soon", "قرب نفاد الباقة", "عند بلوغ عتبة"),
    ("quota_exhausted", "نفاد الباقة", "عند النفاد"),
    ("speed_boost", "رفع سرعة مؤقت", "عند التفعيل/الانتهاء"),
    ("loan", "سلفة وقت", "عند المنح/السداد"),
    ("payment_received", "استلام دفعة", "بعد التحصيل"),
    ("invoice", "فاتورة مستحقّة", "عند الإصدار"),
    ("password_changed", "تغيير كلمة المرور", "بعد التغيير"),
    ("maintenance", "صيانة/انقطاع", "إشعارات الصيانة"),
    ("portal_invite", "دعوة بوابة المشترك", "رابط البوابة"),
    ("plan_changed", "تغيير الباقة", "عند الترقية/التخفيض"),
    ("disabled", "إيقاف الخدمة", "عند الإيقاف"),
    ("enabled", "إعادة التفعيل", "بعد الإيقاف"),
]


def register_notification_hub_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/integrations", "integrations_hub",
                    integrations_hub, methods=["GET"])
    bp.add_url_rule("/admin-notifications", "admin_notifications",
                    admin_notifications, methods=["GET"])
    bp.add_url_rule("/admin-notifications/channels",
                    "admin_notifications_set_channels",
                    admin_notifications_set_channels, methods=["POST"])
    bp.add_url_rule("/subscriber-notifications", "subscriber_notifications",
                    subscriber_notifications, methods=["GET"])
    # ── إعادة توجيه الصفحات المطويّة/المكرّرة (لا 404) ──
    # المكرّر network_telegram_settings: نُبقي الاسم لإعادة التوجيه فقط.
    bp.add_url_rule("/network/telegram", "network_telegram_settings",
                    _redirect_integrations, methods=["GET"])


def _redirect_integrations():
    return redirect(url_for("radius.integrations_hub"))


# ════════════════════════════════════════════════════════════════════════
# (أ) التكاملات/القنوات
# ════════════════════════════════════════════════════════════════════════
def integrations_hub():
    tid = _tid()
    telegram = tenant_telegram_settings_repo.get(tid) or {
        "tenant_id": tid, "bot_token": "", "chat_id": "",
        "enabled": False, "thread_id": "", "updated_at": "",
    }
    # SMS (HTTP provider) — config defensive.
    try:
        from ..services import comms_providers
        sms = comms_providers.load_channel_config(tid, "sms")
    except Exception:  # noqa: BLE001
        sms = {"enabled": False, "mode": "self_api", "send_url_template": "",
               "http_method": "GET", "balance_url": ""}
    # WhatsApp per-event gates (radius-side toggles; secrets live on panel).
    from ..db.repos import tenants_repo
    from .whatsapp import WHATSAPP_EVENTS, PANEL_PORTAL_WHATSAPP_PATH
    wa_gates = []
    for ev_key, ev_label in WHATSAPP_EVENTS:
        on = str(tenants_repo.get_setting(tid, f"whatsapp.send.{ev_key}", "0")).strip() in ("1", "on", "true")
        wa_gates.append({"key": ev_key, "label": ev_label, "enabled": on})
    # Webhook subscription (one per tenant).
    try:
        from ..db.repos import webhooks_repo
        subs = webhooks_repo.list_subs(tid)
        webhook = subs[0] if subs else None
    except Exception:  # noqa: BLE001
        webhook = None
    # حالة ربط تيليجرام بضغطة واحدة (مربوط؟ + اسم الحساب الملتقَط).
    try:
        from ..services import telegram_connect
        tg_connect = telegram_connect.connection_status(tid, scope="admin")
    except Exception:  # noqa: BLE001
        tg_connect = {"has_token": bool(telegram.get("bot_token")),
                      "linked": False, "account_name": "", "chat_id_masked": ""}
    return render_template(
        "radius/integrations_hub.html",
        telegram=telegram, sms=sms, wa_gates=wa_gates, webhook=webhook,
        panel_whatsapp_path=PANEL_PORTAL_WHATSAPP_PATH,
        tg_connect=tg_connect,
    )


# ════════════════════════════════════════════════════════════════════════
# (ب) إشعارات الإدارة — الجرد + قنوات لكل حدث
# ════════════════════════════════════════════════════════════════════════
def admin_notifications():
    tid = _tid()
    catalogue = admin_alerts.catalogue(tid)
    # تجميع حسب المجموعة (بترتيب GROUPS).
    groups: dict[str, dict] = {}
    for g_key, g_label, g_icon in admin_alerts.GROUPS:
        groups[g_key] = {"key": g_key, "label": g_label, "icon": g_icon, "items": []}
    for item in catalogue:
        groups.setdefault(item["group"], {"key": item["group"],
                                          "label": item["group"], "icon": "bell",
                                          "items": []})["items"].append(item)
    return render_template(
        "radius/admin_notifications.html",
        groups=[g for g in groups.values() if g["items"]],
        channels=admin_alerts.CHANNELS,
        deliverable=sorted(admin_alerts.DELIVERABLE_CHANNELS),
        deferred=sorted(admin_alerts.DEFERRED_CHANNELS),
        telegram_ready=admin_alerts.telegram_ready(tid),
    )


def admin_notifications_set_channels():
    """يضبط قنوات حدث إدارة واحد (JSON). الجرس دائمًا مُفعَّل."""
    key = (request.form.get("key") or "").strip()
    if not admin_alerts.get_spec(key):
        return jsonify({"ok": False, "error": "حدث غير معروف."}), 404
    # القنوات المُرسَلة (قائمة)؛ نتجاهل غير المعروفة، والجرس يُضاف دائمًا.
    raw = request.form.getlist("channels") or []
    if not raw:
        single = (request.form.get("channels") or "").strip()
        raw = [c for c in single.split(",") if c]
    chans = admin_alerts.set_channels(_tid(), key, raw, by=_by())
    return jsonify({"ok": True, "key": key, "channels": sorted(chans)})


# ════════════════════════════════════════════════════════════════════════
# (ج) إشعارات المشتركين — هيكل Phase 2
# ════════════════════════════════════════════════════════════════════════
def subscriber_notifications():
    return render_template(
        "radius/subscriber_notifications.html",
        events=SUBSCRIBER_EVENTS,
    )
