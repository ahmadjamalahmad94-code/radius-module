"""O1 — Router status overview route.

  GET /admin/radius/mt/<nas_id>/overview
"""
from __future__ import annotations

from flask import Blueprint, abort, g, render_template

from ..core.tenant import DEFAULT_TENANT_ID
from ..services.mt_health_score import score_health
from ..services.mt_permissions import PERM_VIEW, requires_perm
from ..services.mt_router_overview import build_overview


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_mt_router_overview_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/overview",
        "mt_router_overview",
        requires_perm(PERM_VIEW)(mt_router_overview),
        methods=["GET"],
    )


def mt_router_overview(nas_id: int):
    ov = build_overview(tenant_id=_tid(), nas_id=int(nas_id))
    if ov is None:
        abort(404)
    health = score_health(ov)
    return render_template("radius/mt_router_overview.html",
                           overview=ov, health=health)
