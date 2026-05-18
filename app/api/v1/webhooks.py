"""
Webhooks config + اختبار الإرسال.

HobeHub يستخدم هذه الـ endpoints ليُسجِّل URL الذي يستقبل الأحداث منا.
"""
from __future__ import annotations

from flask import Blueprint, request

from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/webhooks/config", "webhooks_get",
                    require_api_token(webhooks_get), methods=["GET"])
    bp.add_url_rule("/webhooks/config", "webhooks_set",
                    require_api_token(webhooks_set), methods=["PUT"])
    bp.add_url_rule("/webhooks/test", "webhooks_test",
                    require_api_token(webhooks_test), methods=["POST"])


def _config_store():
    from app.webhooks.config import WebhookConfigStore
    return WebhookConfigStore.instance()


def webhooks_get():
    cfg = _config_store().get()
    return ok({
        "target_url": cfg.target_url,
        "enabled_events": list(cfg.enabled_events),
        "secret_set": bool(cfg.secret),
    })


def webhooks_set():
    body = request.get_json(silent=True) or {}
    url = (body.get("target_url") or "").strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        return fail("validation_error", "target_url يجب أن يبدأ بـ http(s)://", status=422)
    secret = (body.get("secret") or "").strip()
    events = body.get("enabled_events")
    if events is not None and not isinstance(events, list):
        return fail("validation_error", "enabled_events يجب أن تكون list", status=422)
    cfg = _config_store().update(target_url=url, secret=secret, enabled_events=events)
    return ok({
        "target_url": cfg.target_url,
        "enabled_events": list(cfg.enabled_events),
        "secret_set": bool(cfg.secret),
    })


def webhooks_test():
    from app.webhooks.dispatcher import dispatch_event
    event_id = dispatch_event(
        "webhook.test",
        {"message": "this is a test event from HobeRadius"},
    )
    return ok({"dispatched": True, "event_id": event_id})
