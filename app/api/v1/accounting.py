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


def accounting_list():
    try:
        limit = min(int(request.args.get("limit") or 50), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "limit/offset must be int", status=422)
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

    limit = min(max(int(request.args.get("limit") or 100), 1), 500)
    items = AccountingEventsService().list_online(tenant_id=_tid(), limit=limit)
    return ok({"items": items, "count": len(items)})


def accounting_sessions_history():
    from ...radius.services.accounting_events import AccountingEventsService

    limit = min(max(int(request.args.get("limit") or 100), 1), 500)
    items = AccountingEventsService().list_history(tenant_id=_tid(), limit=limit)
    return ok({"items": items, "count": len(items)})


def accounting_session_detail(session_id: str):
    from ...radius.services.accounting_events import AccountingEventsService

    item = AccountingEventsService().session_detail(tenant_id=_tid(), session_id=session_id)
    if not item:
        return fail("not_found", "Accounting session not found", status=404)
    return ok({"item": item})
