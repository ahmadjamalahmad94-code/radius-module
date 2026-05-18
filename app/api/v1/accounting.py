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
