"""Accounting endpoints — REAL: from radacct table."""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok
from ...radius.core.strict_input import iso_utc_z

# radacct keeps FreeRADIUS «YYYY-MM-DD HH:MM:SS» rows next to app-written ISO
# «…T…Z» rows; the app parsed the Z-less ones as LOCAL time (3 h off). Every
# accounting endpoint now emits ISO-8601 UTC with «Z» (stress campaign A08).
_RADACCT_TS_COLS = ("acctstarttime", "acctupdatetime", "acctstoptime")


def _ts_row(row):
    if not isinstance(row, dict):
        return row
    out = dict(row)
    for k in _RADACCT_TS_COLS:
        if k in out:
            out[k] = iso_utc_z(out[k])
    return out


_EVENT_ERRORS_AR = {
    "unsupported or missing accounting status_type":
        "نوع حدث المحاسبة (status_type) مفقود أو غير مدعوم.",
    "acct_session_id is required": "معرّف الجلسة (acct_session_id) مطلوب.",
    "nas_ip_address is required": "عنوان الراوتر (nas_ip_address) مطلوب.",
}


def _event_error_ar(message: str) -> str:
    msg = str(message or "").strip()
    if msg in _EVENT_ERRORS_AR:
        return _EVENT_ERRORS_AR[msg]
    if msg.startswith("unsupported accounting status type"):
        return _EVENT_ERRORS_AR["unsupported or missing accounting status_type"]
    if not msg or msg.isascii():
        return "بيانات حدث المحاسبة غير صالحة."
    return msg


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
                d[k] = iso_utc_z(v)
        out.append(d)
    return ok({"items": out, "count": len(out)})


def accounting_event_ingest():
    from ...radius.services.accounting_events import AccountingEventsService

    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return fail("validation_error", "جسم الطلب يجب أن يكون كائن JSON.", status=422)
    try:
        result = AccountingEventsService().ingest(tenant_id=_tid(), payload=body)
    except ValueError as exc:
        return fail("validation_error", _event_error_ar(str(exc)), status=422)
    if isinstance(result, dict) and isinstance(result.get("session"), dict):
        result = dict(result, session=_ts_row(result["session"]))
    return ok(result)


def accounting_online():
    from ...radius.services.accounting_events import AccountingEventsService

    try:
        limit = min(max(int(request.args.get("limit") or 100), 1), 500)
    except ValueError:
        return fail("validation_error", "قيمة limit يجب أن تكون رقمًا صحيحًا.", status=422)
    items = AccountingEventsService().list_online(tenant_id=_tid(), limit=limit)
    items = [_ts_row(r) for r in items]
    return ok({"items": items, "count": len(items)})


def accounting_sessions_history():
    from ...radius.services.accounting_events import AccountingEventsService

    try:
        limit = min(max(int(request.args.get("limit") or 100), 1), 500)
    except ValueError:
        return fail("validation_error", "قيمة limit يجب أن تكون رقمًا صحيحًا.", status=422)
    items = AccountingEventsService().list_history(tenant_id=_tid(), limit=limit)
    items = [_ts_row(r) for r in items]
    return ok({"items": items, "count": len(items)})


def accounting_session_detail(session_id: str):
    from ...radius.services.accounting_events import AccountingEventsService

    item = AccountingEventsService().session_detail(tenant_id=_tid(), session_id=session_id)
    if not item:
        return fail("not_found", "جلسة المحاسبة غير موجودة.", status=404)
    return ok({"item": _ts_row(item)})


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
