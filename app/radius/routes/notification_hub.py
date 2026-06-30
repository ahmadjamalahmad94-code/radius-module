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


# (أُزيل SUBSCRIBER_EVENTS الساكن — صفحة «إشعارات المشتركين» تُحرّك الآن
#  notifications_engine مباشرةً، المصدر الوحيد لأحداث/قوالب/قنوات المشترك.)


def register_notification_hub_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/integrations", "integrations_hub",
                    integrations_hub, methods=["GET"])
    bp.add_url_rule("/admin-notifications", "admin_notifications",
                    admin_notifications, methods=["GET"])
    bp.add_url_rule("/admin-notifications/channels",
                    "admin_notifications_set_channels",
                    admin_notifications_set_channels, methods=["POST"])
    bp.add_url_rule("/subscriber-notifications", "subscriber_notifications",
                    subscriber_notifications, methods=["GET", "POST"])
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
    # SMS — per-tenant TweetSMS connection (BYO provider). The dedicated «ربط
    # SMS» page owns the credentials; here we only surface the connection flag.
    try:
        from ..services import tweetsms
        sms = tweetsms.connection_status(tid)
    except Exception:  # noqa: BLE001
        sms = {"connected": False, "enabled": False}
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
    # حالة دفع الجوال: الدفع مركزيّ عبر لوحة التراخيص — نَعرض إن كان الجسر
    # مُهيّأ (مُفعَّل + رابط HTTPS + مفتاح ترخيص) كي نُلمّح حين لا يَصل التحويل.
    try:
        from ..services import notifications as _notif_svc
        push_ready = bool(_notif_svc.push_status(tid).get("bridge_configured"))
    except Exception:  # noqa: BLE001 — الحالة لا تكسر الصفحة أبدًا
        push_ready = False
    return render_template(
        "radius/admin_notifications.html",
        groups=[g for g in groups.values() if g["items"]],
        channels=admin_alerts.CHANNELS,
        deliverable=sorted(admin_alerts.DELIVERABLE_CHANNELS),
        deferred=sorted(admin_alerts.DEFERRED_CHANNELS),
        telegram_ready=admin_alerts.telegram_ready(tid),
        push_ready=push_ready,
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
# (ج) إشعارات المشتركين — السطح القانوني الوحيد فوق notifications_engine
# (طُوي إليه communications/notifications؛ التسليم يشمل تيليجرام المشترك).
# ════════════════════════════════════════════════════════════════════════
# قنوات المشترك في الواجهة (تيليجرام أولًا = الأساس بلا مفاتيح). نُخفي قنوات
# الأحداث التشغيليّة (الشبكة) عن هذه الصفحة — مخصّصة لأحداث المشترك.
_SUB_CHANNELS = ("telegram", "whatsapp", "sms")
_SUB_CHANNEL_LABELS = {"telegram": "تيليجرام", "whatsapp": "واتساب", "sms": "SMS"}
# مجموعات أحداث المشترك (نستبعد network التشغيليّة).
_SUB_GROUPS = ("subscribers", "billing")


def _notif_values_from_form() -> dict:
    """يبني خريطة قيم save_rules من النموذج: <event>__enabled / __channels /
    __template / __days_before — لأحداث المشترك المعروضة فقط."""
    from ..services import notifications_engine as ne
    values: dict = {}
    for key, ev in ne.EVENTS.items():
        if ev.group not in _SUB_GROUPS:
            continue
        values[f"{key}__enabled"] = "1" if request.form.get(f"{key}__enabled") else "0"
        values[f"{key}__channels"] = request.form.getlist(f"{key}__channels")
        tmpl = request.form.get(f"{key}__template")
        if tmpl is not None:
            values[f"{key}__template"] = tmpl
        if ev.has_days_before:
            values[f"{key}__days_before"] = request.form.get(f"{key}__days_before") or ""
    return values


def subscriber_notifications():
    from ..services import notifications_engine as ne
    tid = _tid()
    if request.method == "POST":
        # حفظ مقصور على أحداث المشترك المعروضة (only_keys) — لا يلمس أحداث الشبكة.
        sub_keys = [k for k, ev in ne.EVENTS.items() if ev.group in _SUB_GROUPS]
        try:
            ne.save_rules(tid, _notif_values_from_form(), by=_by(), only_keys=sub_keys)
            flash("تم حفظ إعدادات إشعارات المشتركين.", "success")
        except Exception:  # noqa: BLE001
            flash("تعذّر حفظ الإعدادات. حاول مرة أخرى.", "error")
        return redirect(url_for("radius.subscriber_notifications"))

    telegram = tenant_telegram_settings_repo.get(tid) or {}
    chan_ready = {"telegram": bool(telegram.get("bot_token") and telegram.get("enabled"))}
    # SMS readiness reflects the per-tenant TweetSMS connection (BYO provider).
    try:
        from ..services import tweetsms
        chan_ready["sms"] = tweetsms.is_connected(tid)
    except Exception:  # noqa: BLE001
        chan_ready["sms"] = False
    # WhatsApp keeps the generic-channel readiness check.
    try:
        from ..services import comms_providers
        chan_ready["whatsapp"] = comms_providers.is_channel_active(
            comms_providers.load_channel_config(tid, "whatsapp"))
    except Exception:  # noqa: BLE001
        chan_ready.setdefault("whatsapp", False)
    # رتّب القواعد في مجموعات المشترك فقط (subscribers ثم billing).
    rules = [r for r in ne.load_rules(tid) if r.event.group in _SUB_GROUPS]
    groups = []
    for gk in _SUB_GROUPS:
        items = [r for r in rules if r.event.group == gk]
        if items:
            groups.append({"key": gk, "label": ne.GROUP_LABELS.get(gk, gk), "rules": items})
    base_vars = ("{name}", "{username}", "{prof}", "{exp}", "{balance}", "{status}")
    return render_template(
        "radius/subscriber_notifications.html",
        groups=groups, channels=_SUB_CHANNELS, channel_labels=_SUB_CHANNEL_LABELS,
        chan_ready=chan_ready, base_vars=base_vars,
    )
