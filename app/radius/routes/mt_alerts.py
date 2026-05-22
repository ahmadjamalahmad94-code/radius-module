"""S6.2 — Smart alerts center.

Routes:
  GET /admin/radius/alerts          list (filterable)
  GET /admin/radius/alerts/<id>     detail
"""
from __future__ import annotations

from flask import Blueprint, abort, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import alerts_repo
from ..services.mt_permissions import (
    PERM_DIAGNOSTICS, requires_perm,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_mt_alerts_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/alerts", "mt_alerts_index",
        requires_perm(PERM_DIAGNOSTICS)(mt_alerts_index),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/alerts/<int:alert_id>", "mt_alerts_detail",
        requires_perm(PERM_DIAGNOSTICS)(mt_alerts_detail),
        methods=["GET"],
    )


def mt_alerts_index():
    status = (request.args.get("status") or "open").strip().lower()
    if status not in {"open", "resolved"}:
        status = "open"
    severity = (request.args.get("severity") or "").strip() or None
    raw_router = (request.args.get("router_id") or "").strip()
    try:
        router_id = int(raw_router) if raw_router else None
    except (TypeError, ValueError):
        router_id = None

    if status == "open":
        rows = alerts_repo.list_open(
            _tid(), router_id=router_id, severity=severity,
        )
    else:
        rows = alerts_repo.list_resolved(
            _tid(), router_id=router_id,
        )

    return render_template(
        "radius/mt_alerts_index.html",
        rows=rows,
        filters={"status": status, "severity": severity,
                 "router_id": router_id},
        severities=["info", "warning", "critical"],
    )


def mt_alerts_detail(alert_id: int):
    row = alerts_repo.get_by_id(_tid(), int(alert_id))
    if not row:
        abort(404)
    return render_template("radius/mt_alerts_detail.html",
                           alert=row)
