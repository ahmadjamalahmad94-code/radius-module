"""O4 — Per-router activity timeline route."""
from __future__ import annotations

from flask import Blueprint, abort, g, render_template

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import audit_repo
from ..services.mt_audit_presenter import present_many
from ..services.mt_permissions import PERM_VIEW, requires_perm


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_mt_audit_timeline_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/timeline",
        "mt_audit_timeline",
        requires_perm(PERM_VIEW)(mt_audit_timeline),
        methods=["GET"],
    )


def mt_audit_timeline(nas_id: int):
    row = db().execute(
        "SELECT id, name, address FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), _tid()),
    ).fetchone()
    if not row:
        abort(404)
    nas = dict(row)
    raw_rows = audit_repo.recent(
        _tid(), router_id=int(nas_id), limit=200,
    )
    entries = present_many(raw_rows)
    return render_template(
        "radius/mt_audit_timeline.html",
        nas=nas, entries=entries,
    )
