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
    bp.add_url_rule("/webhooks/deliveries", "webhooks_deliveries",
                    require_api_token(webhooks_deliveries), methods=["GET"])


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
        return fail("validation_error", "رابط الاستقبال يجب أن يبدأ بـ http:// أو https://.", status=422)
    secret = (body.get("secret") or "").strip()
    events = body.get("enabled_events")
    if events is not None and not isinstance(events, list):
        return fail("validation_error", "الأحداث المفعلة يجب أن تكون قائمة.", status=422)
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
        {"message": "هذا حدث اختبار من HobeRadius"},
    )
    return ok({"dispatched": True, "event_id": event_id})


def _delivery_item(item) -> dict:
    return {
        "id": item.id,
        "tenant_id": item.tenant_id,
        "subscription_id": item.subscription_id,
        "event": item.event,
        "event_id": item.event_id,
        "status": item.status,
        "attempts": item.attempts,
        "last_status_code": item.last_status_code,
        "last_response_excerpt": item.last_response_excerpt,
        "next_attempt_at": item.next_attempt_at.isoformat() + "Z"
        if item.next_attempt_at
        else None,
        "created_at": item.created_at.isoformat() + "Z" if item.created_at else None,
    }


def webhooks_deliveries():
    from flask import g
    from app.radius.db.repos import webhooks_repo

    status = (request.args.get("status") or "").strip()
    if status and status not in {"queued", "retrying", "delivered", "failed"}:
        return fail("validation_error", "حالة التسليم غير معروفة.", status=422)
    try:
        limit = min(500, max(1, int(request.args.get("limit") or 200)))
    except ValueError:
        return fail("validation_error", "قيمة limit يجب أن تكون رقمًا صحيحًا.", status=422)
    items = webhooks_repo.list_deliveries(
        int(getattr(g, "tenant_id", 1)),
        status=status or None,
        limit=limit,
    )
    return ok(
        {
            "items": [_delivery_item(item) for item in items],
            "count": len(items),
            "status": status or "all",
        }
    )
