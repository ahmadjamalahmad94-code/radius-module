from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.db.repos import share_groups_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _int(body: dict, key: str, default: int = 0) -> int:
    return int(body.get(key) or default)


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/share-groups", "share_groups_list", require_api_token(list_groups), methods=["GET"])
    bp.add_url_rule("/share-groups", "share_groups_create", require_api_token(create_group), methods=["POST"])
    bp.add_url_rule("/share-groups/<int:group_id>", "share_groups_get", require_api_token(get_group), methods=["GET"])
    bp.add_url_rule("/share-groups/<int:group_id>", "share_groups_patch", require_api_token(patch_group), methods=["PATCH"])
    bp.add_url_rule("/share-groups/<int:group_id>", "share_groups_delete", require_api_token(delete_group), methods=["DELETE"])
    bp.add_url_rule("/share-groups/<int:group_id>/members", "share_groups_add_member", require_api_token(add_member), methods=["POST"])
    bp.add_url_rule("/share-groups/<int:group_id>/members/<int:subscriber_id>", "share_groups_remove_member", require_api_token(remove_member), methods=["DELETE"])


def list_groups():
    items = share_groups_repo.list_groups(_tid())
    return ok({"items": items, "count": len(items)})


def get_group(group_id: int):
    group = share_groups_repo.get(_tid(), group_id)
    if not group:
        return fail("not_found", "share group not found", status=404)
    return ok({"group": group, "members": share_groups_repo.list_members(group_id)})


def create_group():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return fail("validation_error", "name is required", status=422)
    group_id = share_groups_repo.create(
        tenant_id=_tid(),
        name=name,
        description=str(body.get("description") or ""),
        shared_quota_mb=_int(body, "shared_quota_mb"),
        shared_speed_down_kbps=_int(body, "shared_speed_down_kbps"),
        shared_speed_up_kbps=_int(body, "shared_speed_up_kbps"),
        max_members=_int(body, "max_members"),
        enabled=bool(body.get("enabled", True)),
    )
    return ok(share_groups_repo.get(_tid(), group_id), status=201)


def patch_group(group_id: int):
    if not share_groups_repo.get(_tid(), group_id):
        return fail("not_found", "share group not found", status=404)
    body = request.get_json(silent=True) or {}
    updated = share_groups_repo.update(_tid(), group_id, **body)
    return ok(updated)


def delete_group(group_id: int):
    if not share_groups_repo.get(_tid(), group_id):
        return fail("not_found", "share group not found", status=404)
    share_groups_repo.delete(_tid(), group_id)
    return ok({"id": group_id, "deleted": True})


def add_member(group_id: int):
    if not share_groups_repo.get(_tid(), group_id):
        return fail("not_found", "share group not found", status=404)
    body = request.get_json(silent=True) or {}
    subscriber_id = int(body.get("subscriber_id") or 0)
    if subscriber_id <= 0:
        return fail("validation_error", "subscriber_id is required", status=422)
    share_groups_repo.add_member(_tid(), group_id, subscriber_id)
    return ok({"group_id": group_id, "subscriber_id": subscriber_id, "added": True}, status=201)


def remove_member(group_id: int, subscriber_id: int):
    if not share_groups_repo.get(_tid(), group_id):
        return fail("not_found", "share group not found", status=404)
    share_groups_repo.remove_member(group_id, subscriber_id)
    return ok({"group_id": group_id, "subscriber_id": subscriber_id, "removed": True})
