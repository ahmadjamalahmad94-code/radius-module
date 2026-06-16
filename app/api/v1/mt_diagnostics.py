"""mikrotik diagnostics — v1 JSON API (feat/api-first-parity, group 7e).

Mirrors two read-only MikroTik ops pages:
  * problems center (`routes/mt_problems.py`, `/admin/radius/problems`) →
    `mt_problems.build_problems`.
  * recovery plan (`routes/mt_recovery_plan.py`, `/admin/radius/recovery/<id>`)
    → `mt_recovery_plan.build_plan`.
Reuses the same services (no router contact, no duplicated logic).
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.services.mt_problems import ALL_PROBLEM_TYPES, build_problems
from ...radius.services.mt_recovery_plan import build_plan
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/mikrotik/problems", "mt_problems",
                    require_api_token(problems), methods=["GET"])
    bp.add_url_rule("/mikrotik/recovery/<int:audit_id>", "mt_recovery_plan",
                    require_api_token(recovery_plan), methods=["GET"])


def problems():
    """GET /mikrotik/problems — مركز المشاكل (يطابق mt_problems_index).
    فلاتر: router_id, severity (critical/warning/info), type."""
    a = request.args
    raw_router = (a.get("router_id") or "").strip()
    try:
        router_id = int(raw_router) if raw_router else None
    except (TypeError, ValueError):
        router_id = None
    severity = (a.get("severity") or "").strip() or None
    if severity not in {None, "critical", "warning", "info"}:
        severity = None
    type_ = (a.get("type") or "").strip() or None
    if type_ is not None and type_ not in ALL_PROBLEM_TYPES:
        type_ = None
    payload = build_problems(_tid(), router_id=router_id, severity=severity, type=type_)
    return ok({
        "now": payload["now"],
        "soon": payload["soon"],
        "info": payload["info"],
        "total": payload["total"],
        "filters": payload["filters"],
        "all_problem_types": list(ALL_PROBLEM_TYPES),
    })


def recovery_plan(audit_id: int):
    """GET /mikrotik/recovery/<audit_id> — خطة التعافي لعملية (يطابق
    mt_recovery_plan). 404 إن لم توجد خطة لهذا السجلّ."""
    plan = build_plan(tenant_id=_tid(), audit_id=int(audit_id))
    if plan is None:
        return fail("not_found", "لا توجد خطة تعافٍ لهذا السجلّ.", status=404)
    return ok({"plan": plan.to_dict()})
