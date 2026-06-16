"""whatsapp-bot — v1 JSON API (feat/api-first-parity, group 6).

Mirrors the WhatsApp auto-reply bot settings page
(`routes/communications.py:communications_bot_settings`,
`/admin/radius/communications/bot`): the bot config (enabled, greeting,
fallback) and the auto-reply **rules** (commands: keyword → reply_template).
Reuses `comms_bot.load_bot_config` / `save_bot_config` (no duplicated logic;
storage is `tenant_settings comms.bot.*`, no migration).

Mounted under `/whatsapp/bot` (the existing `/whatsapp` endpoints are the
notification provider settings — a separate concern).
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.services import comms_bot
from ..auth import require_api_token
from ..responses import ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _admin_id() -> int:
    try:
        return int(getattr(g, "admin_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/whatsapp/bot", "whatsapp_bot_get",
                    require_api_token(bot_get), methods=["GET"])
    bp.add_url_rule("/whatsapp/bot", "whatsapp_bot_save",
                    require_api_token(bot_save), methods=["PUT", "PATCH"])


def _config_json(cfg) -> dict:
    return {
        "enabled": bool(cfg.enabled),
        "greeting": cfg.greeting,
        "fallback": cfg.fallback,
        # القواعد (الأوامر): كل قاعدة keyword → reply_template + enabled.
        "commands": list(cfg.commands),
        "active_commands_count": len(cfg.active_commands()),
    }


def _webhook_url() -> str:
    try:
        from flask import url_for
        return url_for("radius.communications_bot_webhook", _external=True)
    except Exception:  # noqa: BLE001
        return ""


def _channel_ready() -> bool:
    try:
        from ...radius.services import comms_providers
        status = comms_providers.channel_status(_tid(), comms_bot.BOT_CHANNEL) or {}
        return bool(status.get("ready") or status.get("configured") or status.get("connected"))
    except Exception:  # noqa: BLE001
        return False


def bot_get():
    """GET /whatsapp/bot — إعدادات البوت + القواعد + رابط الـwebhook."""
    cfg = comms_bot.load_bot_config(_tid())
    return ok({
        "config": _config_json(cfg),
        "webhook_url": _webhook_url(),
        "channel_ready": _channel_ready(),
    })


def bot_save():
    """PUT/PATCH /whatsapp/bot — حفظ إعدادات البوت والقواعد (يطابق
    communications_bot_settings POST). الحقول الغائبة تبقى كما هي.

    ``commands`` قائمة قواعد: ``[{keyword, reply_template, enabled}]`` —
    تُنقّى داخل save_bot_config (تُتجاهل الفارغة)."""
    body = request.get_json(silent=True) or {}
    cur = comms_bot.load_bot_config(_tid())

    def _pick(key, default):
        return body[key] if key in body else default

    cfg = comms_bot.save_bot_config(
        _tid(),
        {
            "enabled": _pick("enabled", cur.enabled),
            "greeting": _pick("greeting", cur.greeting),
            "fallback": _pick("fallback", cur.fallback),
            "commands": _pick("commands", cur.commands),
        },
        by=_admin_id(),
    )
    return ok({"config": _config_json(cfg), "webhook_url": _webhook_url()})
