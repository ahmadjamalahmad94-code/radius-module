"""notifications — v1 JSON API (feat/api-notifications).

Exposes the panel notification center (the bell) to API clients (the Flutter
app). Mirrors the web routes in `routes/notifications.py` but as JSON, reusing
`notifications_repo` — no duplicated logic. Tenant-scoped via `g.tenant_id`
(set by `require_api_token`); read + mark-read only (no create — notifications
are produced by the local services + the licensing bridge).

Endpoints:
    GET  /notifications                 list (paged) + unread_count
    GET  /notifications/unread-count    cheap badge poll
    GET  /notifications/<id>            single
    POST /notifications/<id>/read       mark one read
    POST /notifications/read-all        mark all read
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.db.repos import notifications_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/notifications", "notifications_list",
                    require_api_token(list_notifications), methods=["GET"])
    bp.add_url_rule("/notifications/unread-count", "notifications_unread_count",
                    require_api_token(get_unread_count), methods=["GET"])
    bp.add_url_rule("/notifications/<int:notif_id>", "notifications_get",
                    require_api_token(get_notification), methods=["GET"])
    bp.add_url_rule("/notifications/<int:notif_id>/read",
                    "notifications_mark_read",
                    require_api_token(mark_read), methods=["POST"])
    bp.add_url_rule("/notifications/read-all", "notifications_read_all",
                    require_api_token(mark_all_read), methods=["POST"])


def _clamp(value, lo, hi, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def list_notifications():
    """GET /notifications?unread_only=&limit=&offset= — paged list + unread.

    Returns the same row shape the web center renders, plus the global
    unread_count so a client can refresh the badge in one round-trip.
    """
    tid = _tid()
    unread_only = str(request.args.get("unread_only", "")).strip().lower() in (
        "1", "true", "yes", "on")
    limit = _clamp(request.args.get("limit"), 1, 100, 30)
    offset = _clamp(request.args.get("offset"), 0, 1_000_000, 0)
    items = notifications_repo.list_for(
        tid, unread_only=unread_only, limit=limit, offset=offset)
    return ok({
        "items": items,
        "unread_count": notifications_repo.unread_count(tid),
        "limit": limit,
        "offset": offset,
        "has_more": len(items) == limit,
    })


def get_unread_count():
    """GET /notifications/unread-count — cheap poll for the bell badge."""
    return ok({"unread_count": notifications_repo.unread_count(_tid())})


def get_notification(notif_id: int):
    """GET /notifications/<id> — single notification."""
    notif = notifications_repo.get(_tid(), int(notif_id))
    if not notif:
        return fail("not_found", "الإشعار غير موجود.", status=404)
    return ok(notif)


def mark_read(notif_id: int):
    """POST /notifications/<id>/read — mark one read (idempotent)."""
    tid = _tid()
    if not notifications_repo.get(tid, int(notif_id)):
        return fail("not_found", "الإشعار غير موجود.", status=404)
    notifications_repo.mark_read(tid, int(notif_id))
    return ok({"id": int(notif_id), "unread_count": notifications_repo.unread_count(tid)})


def mark_all_read():
    """POST /notifications/read-all — mark every unread notification read."""
    tid = _tid()
    count = notifications_repo.mark_all_read(tid)
    return ok({"marked": count, "unread_count": notifications_repo.unread_count(tid)})


__all__ = ["register"]
