"""O12 — Guided operations assistant route.

GET /admin/radius/mt/<nas_id>/assistant?op=<operation>

Read-only checklist composer. Each step is the output of a
Phase O service (health/safety/backup/recent-failure). The
route only renders — no router contact, no mutation.

Guarded by PERM_VIEW because the assistant itself is purely
advisory; the underlying operation pages (program/restore)
remain guarded by their own stricter permissions.
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, g, render_template, request,
)

from ..auth.session_helpers import current_admin_id
from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import admins_repo
from ..services.mt_guided_op import (
    ALL_OPERATIONS, OP_PROGRAMMING_HOTSPOT, build_checklist,
)
from ..services.mt_permissions import PERM_VIEW, requires_perm


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _current_admin():
    aid = current_admin_id()
    if not aid:
        return None
    try:
        return admins_repo.get_admin(int(aid))
    except Exception:  # noqa: BLE001
        return None


def register_mt_guided_op_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/assistant",
        "mt_guided_op",
        requires_perm(PERM_VIEW)(mt_guided_op),
        methods=["GET"],
    )


def mt_guided_op(nas_id: int):
    operation = (request.args.get("op")
                  or OP_PROGRAMMING_HOTSPOT).strip()
    if operation not in ALL_OPERATIONS:
        operation = OP_PROGRAMMING_HOTSPOT
    checklist = build_checklist(
        tenant_id=_tid(), nas_id=int(nas_id),
        admin=_current_admin(), operation=operation,
    )
    if checklist is None:
        abort(404)
    return render_template(
        "radius/mt_guided_op.html",
        checklist=checklist,
        nas_id=int(nas_id),
        operation=operation,
        all_operations=ALL_OPERATIONS,
    )
