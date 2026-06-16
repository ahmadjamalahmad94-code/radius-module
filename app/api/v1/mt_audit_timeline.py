"""mikrotik audit-timeline — v1 JSON API (feat/api-first-parity, group 7d).

Mirrors the per-router activity timeline web page
(`routes/mt_audit_timeline.py`, `/admin/radius/mt/<nas_id>/timeline`) as JSON.
Read-only — reuses `audit_repo.recent` + `mt_audit_presenter.present_many`
(same Arabic-presented entries the web shows).
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.db.connection import db
from ...radius.db.repos import audit_repo
from ...radius.services.mt_audit_presenter import present_many
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/mikrotik/<int:nas_id>/timeline", "mt_audit_timeline",
                    require_api_token(timeline), methods=["GET"])


def timeline(nas_id: int):
    """GET — الخط الزمني لنشاط راوتر واحد (يطابق mt_audit_timeline)."""
    row = db().execute(
        "SELECT id, name, address FROM nas_devices "
        "WHERE id=? AND tenant_id=? AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), _tid()),
    ).fetchone()
    if not row:
        return fail("not_found", "الراوتر غير موجود.", status=404)
    try:
        limit = min(max(int(request.args.get("limit") or 200), 1), 500)
    except (TypeError, ValueError):
        limit = 200
    raw = audit_repo.recent(_tid(), router_id=int(nas_id), limit=limit)
    entries = [e.to_dict() for e in present_many(raw)]
    return ok({"nas": dict(row), "entries": entries, "count": len(entries)})
