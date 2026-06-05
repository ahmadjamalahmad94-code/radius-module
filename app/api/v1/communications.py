"""Communications and campaigns JSON API."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, g, request

from ...radius.services import comms_providers, comms_quota
from ...radius.services.notification_campaigns import (
    CHANNELS,
    NotificationCampaignError,
    NotificationCampaignService,
)
from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    routes = [
        ("/communications/summary", "communications_summary", summary, ["GET"]),
        ("/communications/templates", "communications_templates_list", templates_list, ["GET"]),
        ("/communications/templates", "communications_templates_create", templates_create, ["POST"]),
        ("/communications/audience", "communications_audience_list", audience_list, ["GET"]),
        ("/communications/audience", "communications_audience_create", audience_create, ["POST"]),
        ("/communications/audience/preview", "communications_audience_preview", audience_preview, ["POST"]),
        ("/communications/send", "communications_send", send_manual, ["POST"]),
        ("/communications/campaigns", "communications_campaigns_list", campaigns_list, ["GET"]),
        ("/communications/campaigns", "communications_campaigns_dry_run", campaigns_dry_run, ["POST"]),
        ("/communications/deliveries", "communications_deliveries", deliveries, ["GET"]),
        ("/communications/channels", "communications_channels", channels, ["GET"]),
        ("/communications/channels/<channel>", "communications_channel_save", channel_save, ["POST"]),
        ("/communications/quota", "communications_quota", quota, ["GET"]),
        ("/communications/quota/<channel>/credit", "communications_quota_credit", quota_credit, ["POST"]),
    ]
    for rule, endpoint, view, methods in routes:
        bp.add_url_rule(rule, endpoint, require_api_token(view), methods=methods)


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1) or 1)


def _actor() -> str:
    admin_id = getattr(g, "admin_id", None)
    token_id = getattr(g, "api_token_id", None)
    if admin_id:
        return f"admin:{admin_id}"
    if token_id:
        return f"api-token:{token_id}"
    return "api-token:env"


def _admin_id() -> int:
    try:
        return int(getattr(g, "admin_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _svc() -> NotificationCampaignService:
    return NotificationCampaignService(tenant_id=_tid())


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _limit(default: int = 100, maximum: int = 500) -> int:
    try:
        return min(max(int(request.args.get("limit") or default), 1), maximum)
    except (TypeError, ValueError):
        return default


def _csv_ids(value: Any) -> list[int]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    return [int(str(item).strip()) for item in raw if str(item).strip().isdigit()]


def _audience(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": str(data.get("target") or "subscriber").strip(),
        "manager_id": data.get("manager_id") or "",
        "ids": _csv_ids(data.get("ids")),
        "limit": min(max(int(data.get("limit") or 100), 1), 500),
    }


def _actions(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("actions") or []
    if isinstance(raw, str):
        raw = [raw]
    return [{"type": str(item).strip()} for item in raw if str(item).strip()]


def _variables(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    return [str(part).strip() for part in raw if str(part).strip()]


def _http_channel(value: Any) -> str:
    channel = str(value or "").strip().lower()
    if channel not in comms_providers.HTTP_CHANNELS:
        raise ValueError("unsupported channel")
    return channel


def _channel_label(value: str) -> str:
    return {
        "sms": "الرسائل القصيرة",
        "whatsapp": "واتساب",
    }.get(str(value or ""), "قناة غير معروفة")


def _mode_label(value: str) -> str:
    return {
        "self_api": "ربط مباشر من العميل",
        "admin_quota": "رصيد مخصص من الإدارة",
    }.get(str(value or ""), "غير محدد")


def _positive_int(value: Any) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _channel_payload(channel: str) -> dict[str, Any]:
    status = comms_providers.channel_status(_tid(), channel)
    quota_status = comms_quota.quota_status(_tid(), channel)
    config = status.get("config") if isinstance(status.get("config"), dict) else {}
    mode = str(status.get("mode") or comms_providers.DEFAULT_MODE)
    return {
        "channel": channel,
        "label": _channel_label(channel),
        "enabled": bool(status.get("enabled")),
        "active": bool(status.get("active")),
        "mode": mode,
        "mode_label": _mode_label(mode),
        "config": {
            "send_url_template": str(config.get("send_url_template") or ""),
            "http_method": str(config.get("http_method") or comms_providers.DEFAULT_METHOD),
            "balance_url": str(config.get("balance_url") or ""),
        },
        "quota": {
            "balance": int(quota_status.balance),
            "used": int(quota_status.used),
            "is_quota_mode": bool(quota_status.is_quota_mode),
        },
    }


def _quota_payload(channel: str) -> dict[str, Any]:
    quota_status = comms_quota.quota_status(_tid(), channel)
    ledger = list(reversed(comms_quota.quota_ledger(_tid(), channel, limit=50)))
    return {
        "channel": channel,
        "label": _channel_label(channel),
        "mode": quota_status.mode,
        "mode_label": _mode_label(quota_status.mode),
        "balance": int(quota_status.balance),
        "used": int(quota_status.used),
        "is_quota_mode": bool(quota_status.is_quota_mode),
        "ledger": ledger,
    }


def _validation_error(exc: Exception):
    message = _safe_message(str(exc))
    return fail("validation_error", message, status=422)


def _safe_message(text: str) -> str:
    return {
        "unsupported channel": "القناة غير مدعومة.",
        "unsupported recipient type": "نوع المستلم غير مدعوم.",
        "unsupported audience target": "الجمهور المحدد غير مدعوم.",
        "template not found": "القالب غير موجود.",
        "notification not found": "الرسالة غير موجودة.",
        "delivery not found": "عملية الإرسال غير موجودة.",
        "key required": "أدخل مفتاحًا واضحًا.",
    }.get(text, "تعذر تنفيذ طلب التواصل. راجع البيانات وحاول مرة أخرى.")


def summary():
    svc = _svc()
    return ok({
        "summary": svc.dashboard(),
        "templates": svc.list_templates()[:8],
        "segments": svc.list_segments()[:8],
        "deliveries": svc.delivery_log(limit=10),
    })


def templates_list():
    items = _svc().list_templates()
    return ok({"items": items, "count": len(items)})


def templates_create():
    data = _body()
    try:
        template = _svc().create_template(
            template_key=str(data.get("template_key") or ""),
            title=str(data.get("title") or ""),
            channel=str(data.get("channel") or "internal"),
            subject=str(data.get("subject") or ""),
            body=str(data.get("body") or ""),
            variables=_variables(data.get("variables")),
            actor=_actor(),
        )
    except NotificationCampaignError as exc:
        return _validation_error(exc)
    return ok({"template": template}, status=201)


def audience_list():
    items = _svc().list_segments()
    return ok({"items": items, "count": len(items)})


def audience_create():
    data = _body()
    try:
        segment = _svc().create_segment(
            segment_key=str(data.get("segment_key") or ""),
            title=str(data.get("title") or ""),
            filters=_audience(data),
            actor=_actor(),
        )
        preview = _svc().preview_audience(segment["filters"])
    except (NotificationCampaignError, ValueError) as exc:
        return _validation_error(exc)
    return ok({"segment": segment, "preview": preview}, status=201)


def audience_preview():
    try:
        preview = _svc().preview_audience(_audience(_body()))
    except (NotificationCampaignError, ValueError) as exc:
        return _validation_error(exc)
    return ok({"items": preview, "count": len(preview)})


def send_manual():
    data = _body()
    channel = str(data.get("channel") or "internal").strip()
    if channel not in CHANNELS:
        return fail("validation_error", "القناة غير مدعومة.", status=422)
    message = str(data.get("message") or "").strip()
    if not message:
        return fail("validation_error", "أدخل نص الرسالة.", status=422)
    try:
        result = _svc().send_manual(
            audience=_audience(data.get("audience") if isinstance(data.get("audience"), dict) else data),
            channel=channel,
            subject=str(data.get("subject") or ""),
            message=message,
            actor=_actor(),
        )
    except (NotificationCampaignError, ValueError) as exc:
        return _validation_error(exc)
    return ok(result, status=201)


def campaigns_list():
    items = _svc().list_campaigns(limit=_limit())
    return ok({"items": items, "count": len(items)})


def campaigns_dry_run():
    data = _body()
    try:
        campaign = _svc().campaign_dry_run(
            campaign_key=str(data.get("campaign_key") or ""),
            title=str(data.get("title") or ""),
            template_id=int(data.get("template_id") or 0),
            audience=_audience(data.get("audience") if isinstance(data.get("audience"), dict) else data),
            actions=_actions(data),
            actor=_actor(),
        )
    except (NotificationCampaignError, ValueError) as exc:
        return _validation_error(exc)
    return ok({"campaign": campaign}, status=201)


def deliveries():
    items = _svc().delivery_log(limit=_limit(default=200))
    return ok({"items": items, "count": len(items)})


def channels():
    items = [_channel_payload(channel) for channel in comms_providers.HTTP_CHANNELS]
    return ok(
        {
            "items": items,
            "count": len(items),
            "modes": [
                {"key": mode, "label": _mode_label(mode)}
                for mode in comms_providers.CHANNEL_MODES
            ],
            "methods": ["GET", "POST"],
        }
    )


def channel_save(channel: str):
    try:
        channel_key = _http_channel(channel)
    except ValueError as exc:
        return _validation_error(exc)

    data = _body()
    mode = str(data.get("mode") or comms_providers.DEFAULT_MODE).strip().lower()
    if mode not in comms_providers.CHANNEL_MODES:
        return fail("validation_error", "نمط تشغيل القناة غير مدعوم.", status=422)

    send_url = str(data.get("send_url_template") or "").strip()
    balance_url = str(data.get("balance_url") or "").strip()
    if len(send_url) > 2000 or len(balance_url) > 2000:
        return fail(
            "validation_error",
            "رابط القناة طويل جدًا. اختصر الرابط أو استخدم رابطًا صالحًا من المزود.",
            status=422,
        )

    saved = comms_providers.save_channel_config(
        _tid(),
        channel_key,
        {
            "enabled": data.get("enabled"),
            "mode": mode,
            "send_url_template": send_url,
            "http_method": data.get("http_method") or comms_providers.DEFAULT_METHOD,
            "balance_url": balance_url,
        },
        by=_admin_id(),
    )
    return ok({"channel": _channel_payload(channel_key), "saved_config": saved})


def quota():
    items = [_quota_payload(channel) for channel in comms_providers.HTTP_CHANNELS]
    return ok({"items": items, "count": len(items)})


def quota_credit(channel: str):
    try:
        channel_key = _http_channel(channel)
    except ValueError as exc:
        return _validation_error(exc)
    data = _body()
    amount = _positive_int(data.get("amount"))
    if amount <= 0:
        return fail("validation_error", "أدخل عدد رسائل صحيحًا أكبر من صفر.", status=422)

    note = str(data.get("note") or "").strip()[:240] or "إضافة رصيد يدويًا"
    try:
        new_balance = comms_quota.credit_quota(
            _tid(),
            channel_key,
            amount,
            by=_actor(),
            note=note,
        )
        from ...radius.db.repos import audit_repo

        audit_repo.record(
            tenant_id=_tid(),
            actor=_actor(),
            action="comms_quota_manual_credit",
            target_type="comms_quota",
            target_id=channel_key,
            payload={
                "channel": channel_key,
                "amount": amount,
                "note": note,
                "balance_after": new_balance,
            },
        )
    except Exception:  # noqa: BLE001
        return fail(
            "quota_credit_failed",
            "تعذرت إضافة الرصيد. راجع البيانات وحاول مرة أخرى.",
            status=500,
        )

    return ok(
        {
            "quota": _quota_payload(channel_key),
            "balance_after": int(new_balance),
            "message": f"تمت إضافة {amount} رسالة إلى رصيد {_channel_label(channel_key)}.",
        },
        status=201,
    )
