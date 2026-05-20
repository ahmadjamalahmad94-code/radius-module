"""Recycle bin / soft-delete API.

This slice intentionally archives operational records instead of deleting them.
Financial tables remain append-only and are not exposed here.
"""
from __future__ import annotations

from typing import Callable

from flask import Blueprint, g, request

from ...radius.db.connection import db
from ...radius.db.helpers import row_to_dict
from ...radius.db.repos import admins_repo, cards_repo, nas_repo, plans_repo, subscribers_repo
from ..auth import require_api_token
from ..responses import fail, ok


_SUPPORTED = {
    "subscriber": "subscribers",
    "subscribers": "subscribers",
    "plan": "access_plans",
    "profile": "access_plans",
    "plans": "access_plans",
    "nas": "nas_devices",
    "admin": "admins",
    "admins": "admins",
    "role": "roles",
    "roles": "roles",
    "card_batch": "card_batches",
    "card_batches": "card_batches",
}


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _limit_offset() -> tuple[int, int]:
    try:
        limit = min(max(int(request.args.get("limit") or 100), 1), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
    except (TypeError, ValueError):
        limit, offset = 100, 0
    return limit, offset


def _deleted_rows(table: str, *, limit: int, offset: int) -> list[dict]:
    tenant_tables = {"subscribers", "access_plans", "nas_devices", "card_batches"}
    if table in tenant_tables:
        rows = db().execute(
            f"SELECT * FROM {table} WHERE tenant_id = ? AND deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC LIMIT ? OFFSET ?",
            (_tid(), limit, offset),
        ).fetchall()
    elif table == "roles":
        rows = db().execute(
            "SELECT * FROM roles WHERE deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM admins WHERE deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_serialize_deleted(table, row_to_dict(r)) for r in rows]


def _serialize_deleted(table: str, row: dict) -> dict:
    label = (
        row.get("username")
        or row.get("name")
        or row.get("batch_code")
        or row.get("display_name")
        or str(row.get("id"))
    )
    return {
        "entity_type": table,
        "id": row.get("id"),
        "label": label,
        "status": row.get("status") or ("enabled" if row.get("enabled") else "disabled"),
        "deleted_at": row.get("deleted_at"),
        "deleted_by": row.get("deleted_by") or "",
        "delete_reason": row.get("delete_reason") or "",
    }


def _subscriber_username(entity_id: int, *, include_deleted: bool) -> str | None:
    sql = "SELECT username FROM subscribers WHERE tenant_id = ? AND id = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    row = db().execute(sql, (_tid(), entity_id)).fetchone()
    return row["username"] if row else None


def _archive_handler(table: str) -> Callable[[int, str], bool]:
    return {
        "subscribers": _archive_subscriber,
        "access_plans": lambda entity_id, reason: plans_repo.archive_plan(
            _tid(), entity_id, actor=_actor(), reason=reason),
        "nas_devices": lambda entity_id, reason: nas_repo.archive_nas(
            _tid(), entity_id, actor=_actor(), reason=reason),
        "admins": lambda entity_id, reason: admins_repo.archive_admin(
            entity_id, actor=_actor(), reason=reason),
        "roles": lambda entity_id, reason: admins_repo.archive_role(
            entity_id, actor=_actor(), reason=reason),
        "card_batches": lambda entity_id, reason: cards_repo.archive_batch(
            _tid(), entity_id, actor=_actor(), reason=reason),
    }[table]


def _restore_handler(table: str) -> Callable[[int], bool]:
    return {
        "subscribers": _restore_subscriber,
        "access_plans": lambda entity_id: plans_repo.restore_plan(_tid(), entity_id, actor=_actor()),
        "nas_devices": lambda entity_id: nas_repo.restore_nas(_tid(), entity_id, actor=_actor()),
        "admins": lambda entity_id: admins_repo.restore_admin(entity_id, actor=_actor()),
        "roles": lambda entity_id: admins_repo.restore_role(entity_id, actor=_actor()),
        "card_batches": lambda entity_id: cards_repo.restore_batch(_tid(), entity_id, actor=_actor()),
    }[table]


def _archive_subscriber(entity_id: int, reason: str) -> bool:
    username = _subscriber_username(entity_id, include_deleted=False)
    if not username:
        return False
    return subscribers_repo.archive_subscriber(
        _tid(), username, actor=_actor(), reason=reason)


def _restore_subscriber(entity_id: int) -> bool:
    username = _subscriber_username(entity_id, include_deleted=True)
    if not username:
        return False
    return subscribers_repo.restore_subscriber(_tid(), username, actor=_actor())


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/recycle-bin", "recycle_bin_list",
                    require_api_token(recycle_bin_list), methods=["GET"])
    bp.add_url_rule("/recycle-bin/<entity_type>/<int:entity_id>/archive",
                    "recycle_bin_archive",
                    require_api_token(recycle_bin_archive), methods=["POST"])
    bp.add_url_rule("/recycle-bin/<entity_type>/<int:entity_id>/restore",
                    "recycle_bin_restore",
                    require_api_token(recycle_bin_restore), methods=["POST"])


def recycle_bin_list():
    limit, offset = _limit_offset()
    requested = request.args.get("entity_type")
    tables = [_SUPPORTED.get(requested)] if requested else sorted(set(_SUPPORTED.values()))
    tables = [t for t in tables if t]
    if requested and not tables:
        return fail("validation_error", "unsupported entity_type", status=422)
    items: list[dict] = []
    for table in tables:
        items.extend(_deleted_rows(table, limit=limit, offset=offset))
    items.sort(key=lambda x: x.get("deleted_at") or "", reverse=True)
    return ok({
        "items": items[:limit],
        "count": min(len(items), limit),
        "supported_types": sorted(_SUPPORTED),
    })


def recycle_bin_archive(entity_type: str, entity_id: int):
    table = _SUPPORTED.get(entity_type)
    if not table:
        return fail("validation_error", "unsupported entity_type", status=422)
    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "")[:300]
    changed = _archive_handler(table)(entity_id, reason)
    if not changed:
        return fail("not_found", "record not found or already archived", status=404)
    return ok({"entity_type": table, "id": entity_id, "archived": True})


def recycle_bin_restore(entity_type: str, entity_id: int):
    table = _SUPPORTED.get(entity_type)
    if not table:
        return fail("validation_error", "unsupported entity_type", status=422)
    changed = _restore_handler(table)(entity_id)
    if not changed:
        return fail("not_found", "record not found or not archived", status=404)
    return ok({"entity_type": table, "id": entity_id, "restored": True})
