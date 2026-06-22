"""«إشعارات التلجرام» — صفحة إدارة (feat/telegram-admin-alerts).

لاب واحد: المالك يلصق بيانات بوت التلجرام (توكن + معرّف محادثة)، يضغط «اختبار
الاتصال»، ثم يرى جرد كل تنبيهات الإدارة — لكل تنبيه مفتاح تفعيل، زر اختبار
يرسل نموذجًا، ومعاينة شكل القالب. التوكن يُخزَّن مشفّرًا.

يعيد استخدام:
  * tenant_telegram_settings_repo (تخزين البوت، تشفير التوكن)
  * services.admin_alerts (الجرد + التصيير + التفعيل + الإرسال + الاختبار)
  * services.telegram_notifier (النقل)
"""
from __future__ import annotations

from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import tenant_telegram_settings_repo as tg_repo
from ..services import admin_alerts


def register_admin_alerts_routes(bp: Blueprint) -> None:
    # ملاحظة: المسار تحت /alerts/telegram (لا /alerts) لأنّ /alerts محجوز
    # لصفحة «التنبيهات الذكية» (mt_alerts_index). أسماء الـendpointات ثابتة
    # فلا يتأثّر url_for في القالب.
    bp.add_url_rule("/alerts/telegram", "admin_alerts_page", alerts_page, methods=["GET"])
    bp.add_url_rule("/alerts/telegram/bot", "admin_alerts_save_bot", save_bot, methods=["POST"])
    bp.add_url_rule("/alerts/telegram/test-connection", "admin_alerts_test_connection",
                    test_connection, methods=["POST"])
    bp.add_url_rule("/alerts/telegram/toggle", "admin_alerts_toggle", toggle_alert, methods=["POST"])
    bp.add_url_rule("/alerts/telegram/test", "admin_alerts_test", test_alert, methods=["POST"])


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _admin_id() -> int:
    try:
        return int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        return 0


def _mask(token: str) -> str:
    t = token or ""
    return ("…" + t[-4:]) if len(t) > 4 else ("…" if t else "")


def _bot_view(tid: int) -> dict:
    cfg = tg_repo.get(tid) or {}
    token = cfg.get("bot_token") or ""
    return {
        "has_token": bool(token),
        "token_masked": _mask(token),
        "chat_id": cfg.get("chat_id") or "",
        "thread_id": cfg.get("thread_id") or "",
        "enabled": bool(cfg.get("enabled")),
        "ready": admin_alerts.telegram_ready(tid),
    }


def alerts_page():
    # طُويت هذه الصفحة ضمن المجموعة الموحّدة «الإشعارات والتواصل» (Phase 1):
    # جرد التنبيهات + قنوات كل حدث في «إشعارات الإدارة»، وإعداد البوت في
    # «التكاملات والقنوات». نُعيد التوجيه فلا 404. نقاط POST (bot/test/toggle)
    # تبقى مُسجَّلة وتستهلكها الصفحتان الجديدتان.
    from flask import redirect, url_for
    return redirect(url_for("radius.admin_notifications"))


def save_bot():
    """حفظ بيانات البوت. التوكن: فارغ = إبقاء الحالي (لا يُمسح سهوًا)."""
    tid = _tid()
    cur = tg_repo.get(tid) or {}
    new_token = (request.form.get("bot_token") or "").strip()
    token = new_token or (cur.get("bot_token") or "")
    chat_id = (request.form.get("chat_id") or "").strip()
    thread_id = (request.form.get("thread_id") or "").strip()
    enabled = (request.form.get("enabled") or "") in ("1", "on", "true", "yes")
    tg_repo.upsert(tenant_id=tid, bot_token=token, chat_id=chat_id,
                   enabled=enabled, thread_id=thread_id)
    if enabled and token and chat_id:
        flash("حُفظت بيانات البوت. الإشعارات مفعّلة.", "success")
    elif enabled:
        flash("حُفظت البيانات — أكمل التوكن ومعرّف المحادثة لتعمل الإشعارات.", "warning")
    else:
        flash("حُفظت البيانات. الإشعارات معطّلة حاليًا.", "info")
    return redirect(url_for("radius.admin_alerts_page"))


def test_connection():
    """زر «اختبار الاتصال» — AJAX (JSON) أو fallback نموذج."""
    result = admin_alerts.test_connection(_tid())
    if request.headers.get("X-Requested-With") or request.is_json \
            or request.headers.get("X-CSRFToken"):
        return jsonify(result)
    flash("✅ نجح إرسال الاختبار — افحص محادثة تلجرام."
          if result["ok"] else f"فشل الإرسال: {result.get('error') or '—'}",
          "success" if result["ok"] else "error")
    return redirect(url_for("radius.admin_alerts_page"))


def toggle_alert():
    """تفعيل/تعطيل تنبيه واحد — AJAX."""
    key = (request.form.get("key") or "").strip()
    if not admin_alerts.get_spec(key):
        return jsonify({"ok": False, "error": "تنبيه غير معروف."}), 404
    enabled = (request.form.get("enabled") or "") in ("1", "on", "true", "yes")
    admin_alerts.set_enabled(_tid(), key, enabled, by=_admin_id())
    return jsonify({"ok": True, "key": key, "enabled": enabled})


def test_alert():
    """زر اختبار تنبيه — يرسل نموذجًا ويُعيد النتيجة + نص القالب المُصيَّر."""
    key = (request.form.get("key") or "").strip()
    if not admin_alerts.get_spec(key):
        return jsonify({"ok": False, "error": "تنبيه غير معروف."}), 404
    return jsonify(admin_alerts.send_test(_tid(), key))
