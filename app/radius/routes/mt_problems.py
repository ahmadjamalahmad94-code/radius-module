"""O3 — Operations Problems Center route."""
from __future__ import annotations

from flask import Blueprint, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..services.mt_permissions import PERM_DIAGNOSTICS, requires_perm
from ..services.mt_problems import ALL_PROBLEM_TYPES, build_problems


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_mt_problems_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/problems",
        "mt_problems_index",
        requires_perm(PERM_DIAGNOSTICS)(mt_problems_index),
        methods=["GET"],
    )


def mt_problems_index():
    raw_router = (request.args.get("router_id") or "").strip()
    try:
        router_id = int(raw_router) if raw_router else None
    except (TypeError, ValueError):
        router_id = None
    severity = (request.args.get("severity") or "").strip() or None
    if severity not in {None, "critical", "warning", "info"}:
        severity = None
    type_ = (request.args.get("type") or "").strip() or None
    if type_ is not None and type_ not in ALL_PROBLEM_TYPES:
        type_ = None
    payload = build_problems(
        _tid(), router_id=router_id,
        severity=severity, type=type_,
    )
    return render_template(
        "radius/mt_problems.html",
        now_problems=payload["now"],
        soon_problems=payload["soon"],
        info_problems=payload["info"],
        total=payload["total"],
        filters=payload["filters"],
        all_problem_types=ALL_PROBLEM_TYPES,
    )
