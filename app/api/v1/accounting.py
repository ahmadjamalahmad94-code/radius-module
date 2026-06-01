"""Accounting endpoints — REAL: from radacct table."""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/accounting", "accounting_list",
                    require_api_token(accounting_list), methods=["GET"])
    bp.add_url_rule("/accounting/events", "accounting_event_ingest",
                    require_api_token(accounting_event_ingest), methods=["POST"])
    bp.add_url_rule("/accounting/online", "accounting_online",
                    require_api_token(accounting_online), methods=["GET"])
    bp.add_url_rule("/accounting/sessions", "accounting_sessions_history",
                    require_api_token(accounting_sessions_history), methods=["GET"])
    bp.add_url_rule("/accounting/sessions/<session_id>", "accounting_session_detail",
                    require_api_token(accounting_session_detail), methods=["GET"])
    bp.add_url_rule("/accounting/usage/tenant", "accounting_usage_tenant",
                    require_api_token(accounting_usage_tenant), methods=["GET"])
    bp.add_url_rule("/accounting/usage/subscribers/<username>", "accounting_usage_subscriber",
                    require_api_token(accounting_usage_subscriber), methods=["GET"])
    bp.add_url_rule("/accounting/usage/plans/<int:plan_id>", "accounting_usage_plan",
                    require_api_token(accounting_usage_plan), methods=["GET"])
    bp.add_url_rule("/accounting/quota/check", "accounting_quota_check",
                    require_api_token(accounting_quota_check), methods=["POST"])


def accounting_list():
    try:
        limit = min(int(request.args.get("limit") or 50), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "قيم limit و offset يجب أن تكون أرقامًا صحيحة.", status=422)
    username = request.args.get("username")
    from ...radius.integration.factory import get_radius_adapter
    items = get_radius_adapter().list_accounting(
        username=username, limit=limit, offset=offset)
    out = []
    for a in items:
        d = asdict(a)
        for k in ("started_at", "stopped_at", "update_at"):
            v = d.get(k)
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat() + "Z"
        out.append(d)
    return ok({"items": out, "count": len(out)})


def accounting_event_ingest():
    from ...radius.services.accounting_events import AccountingEventsService

    body = request.get_json(silent=True) or {}
    try:
        result = AccountingEventsService().ingest(tenant_id=_tid(), payload=body)
    except ValueError as exc:
        return fail("validation_error", str(exc), status=422)
    return ok(result)


def accounting_online():
    from ...radius.services.accounting_events import AccountingEventsService

    try:
        limit = min(max(int(request.args.get("limit") or 100), 1), 500)
    except ValueError:
        return fail("validation_error", "قيمة limit يجب أن تكون رقمًا صحيحًا.", status=422)
    items = AccountingEventsService().list_online(tenant_id=_tid(), limit=limit)
    return ok({"items": items, "count": len(items)})


def accounting_sessions_history():
    from ...radius.services.accounting_events import AccountingEventsService

    try:
        limit = min(max(int(request.args.get("limit") or 100), 1), 500)
    except ValueError:
        return fail("validation_error", "قيمة limit يجب أن تكون رقمًا صحيحًا.", status=422)
    items = AccountingEventsService().list_history(tenant_id=_tid(), limit=limit)
    return ok({"items": items, "count": len(items)})


def accounting_session_detail(session_id: str):
    from ...radius.services.accounting_events import AccountingEventsService

    item = AccountingEventsService().session_detail(tenant_id=_tid(), session_id=session_id)
    if not item:
        return fail("not_found", "جلسة المحاسبة غير موجودة.", status=404)
    return ok({"item": item})


def _usage_window() -> str:
    return "monthly" if request.args.get("window") == "monthly" else "daily"


def accounting_usage_tenant():
    from ...radius.services.usage_counters import UsageCountersService

    return ok(UsageCountersService().tenant_summary(tenant_id=_tid(), window=_usage_window()))


def accounting_usage_subscriber(username: str):
    from ...radius.services.usage_counters import UsageCountersService

    return ok(
        UsageCountersService().subscriber_summary(
            tenant_id=_tid(),
            username=username,
            window=_usage_window(),
        )
    )


def accounting_usage_plan(plan_id: int):
    from ...radius.services.usage_counters import UsageCountersService

    return ok(
        UsageCountersService().plan_summary(
            tenant_id=_tid(),
            plan_id=plan_id,
            window=_usage_window(),
        )
    )


def accounting_quota_check():
    from ...radius.services.usage_counters import UsageCountersService

    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip()
    if not username:
        return fail("validation_error", "اسم المستخدم مطلوب.", status=422)
    try:
        limit_bytes = int(body.get("limit_bytes") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "قيمة limit_bytes يجب أن تكون رقمًا صحيحًا.", status=422)
    window = "monthly" if body.get("window") == "monthly" else "daily"
    result = UsageCountersService().quota_decision(
        tenant_id=_tid(),
        username=username,
        limit_bytes=limit_bytes,
        window=window,
    )
    return ok(result)
