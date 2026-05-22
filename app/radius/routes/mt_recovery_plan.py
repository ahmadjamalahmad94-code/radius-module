"""O8 — Recovery plan route."""
from __future__ import annotations

from flask import Blueprint, abort, g, render_template

from ..core.tenant import DEFAULT_TENANT_ID
from ..services.mt_permissions import PERM_VIEW, requires_perm
from ..services.mt_recovery_plan import build_plan


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_mt_recovery_plan_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/recovery/<int:audit_id>",
        "mt_recovery_plan",
        requires_perm(PERM_VIEW)(mt_recovery_plan),
        methods=["GET"],
    )


def mt_recovery_plan(audit_id: int):
    plan = build_plan(tenant_id=_tid(), audit_id=int(audit_id))
    if plan is None:
        abort(404)
    return render_template("radius/mt_recovery_plan.html",
                           plan=plan)
