"""Communications and campaigns JSON API."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, g, request

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
