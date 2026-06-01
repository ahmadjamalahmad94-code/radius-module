"""Telegram settings page — Sprint 2.

Per-tenant Telegram bot config. The page renders the current
state, accepts a save POST, and offers a «إرسال رسالة اختبار»
button so the operator can confirm the bot reaches their chat
without waiting for a real outage.

Routes:
  GET  /admin/radius/network/telegram          — form
  POST /admin/radius/network/telegram          — save
  POST /admin/radius/network/telegram/test     — fire a test message
"""
from __future__ import annotations

from flask import (
    Blueprint, flash, g, redirect, render_template,
    request, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import tenant_telegram_settings_repo
from ..services import telegram_notifier


def register_network_telegram_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/network/telegram",
        "network_telegram_settings",
        network_telegram_settings, methods=["GET"],
    )
    bp.add_url_rule(
        "/network/telegram",
        "network_telegram_save",
        network_telegram_save, methods=["POST"],
    )
    bp.add_url_rule(
        "/network/telegram/test",
        "network_telegram_test",
        network_telegram_test, methods=["POST"],
    )


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def network_telegram_settings():
    settings = tenant_telegram_settings_repo.get(_tid()) or {
        "tenant_id":  _tid(),
        "bot_token":  "",
        "chat_id":    "",
        "enabled":    False,
        "thread_id":  "",
        "updated_at": "",
    }
    return render_template(
        "radius/network_telegram_settings.html",
        settings=settings,
    )


def network_telegram_save():
    bot_token = (request.form.get("bot_token") or "").strip()
    chat_id   = (request.form.get("chat_id")   or "").strip()
    thread_id = (request.form.get("thread_id") or "").strip()
    enabled   = request.form.get("enabled", "") in ("1", "on", "true")
    tenant_telegram_settings_repo.upsert(
        tenant_id=_tid(),
        bot_token=bot_token,
        chat_id=chat_id,
        enabled=enabled,
        thread_id=thread_id,
    )
    if enabled and bot_token and chat_id:
        flash("حُفظت إعدادات تلجرام. التنبيهات مفعّلة.", "success")
    elif enabled:
        flash(
            "حُفظت الإعدادات لكن التنبيهات لن تعمل — أكمل bot token و chat id.",
            "warning",
        )
    else:
        flash("حُفظت الإعدادات. التنبيهات مُعطّلة حاليًا.", "info")
    return redirect(url_for("radius.network_telegram_settings"))


def network_telegram_test():
    """Send a sanity-check message right now. The notifier writes
    to log; we just relay ok/error to the operator via flash."""
    tenant_id = _tid()
    test_text = (
        "✅ <b>اختبار التنبيهات</b>\n"
        "تم إرسال هذه الرسالة من إعدادات تلجرام في HobeRadius.\n"
        "إذا تستلمها — إعداداتك صحيحة وستصلك التنبيهات الفعلية عند انقطاع جهاز."
    )
    ok, err = telegram_notifier.send_to_tenant(tenant_id, test_text)
    if ok:
        flash("✅ نجح الإرسال — افحص محادثة تلجرام.", "success")
    elif err:
        flash(f"فشل الإرسال: {err}", "danger")
    else:
        flash(
            "لم يتم الإرسال — تأكّد من تعبئة bot token و chat id وأن التنبيهات مُفعّلة.",
            "warning",
        )
    return redirect(url_for("radius.network_telegram_settings"))
