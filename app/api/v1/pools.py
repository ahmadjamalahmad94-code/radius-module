from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.types_saas import IpPool
from ...radius.db.repos import pools_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _item(pool: IpPool) -> dict:
    return {
        "id": pool.id,
        "pool_name": pool.pool_name,
        "range_ip": pool.range_ip,
        "local_ip": pool.local_ip,
        "router_id": pool.router_id,
        "created_at": pool.created_at.isoformat() if pool.created_at else None,
    }


def _payload(pool_id: int | None = None) -> IpPool | tuple:
    body = request.get_json(silent=True) or {}
    name = str(body.get("pool_name") or body.get("name") or "").strip()
    ip_range = str(body.get("range_ip") or "").strip()
    if not name or not ip_range:
        return fail("validation_error", "اسم الـ pool ونطاق العناوين مطلوبان.", status=422)
    router_id = body.get("router_id")
    try:
        parsed_router_id = int(router_id) if router_id not in (None, "") else None
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف الراوتر يجب أن يكون رقمًا صحيحًا.", status=422)
    return IpPool(
        id=pool_id,
        tenant_id=_tid(),
        pool_name=name,
        range_ip=ip_range,
        local_ip=str(body.get("local_ip") or ""),
        router_id=parsed_router_id,
    )


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/pools", "pools_list", require_api_token(list_pools), methods=["GET"])
    bp.add_url_rule("/pools", "pools_create", require_api_token(create_pool), methods=["POST"])
    bp.add_url_rule("/pools/<int:pool_id>", "pools_get", require_api_token(get_pool), methods=["GET"])
    bp.add_url_rule("/pools/<int:pool_id>", "pools_patch", require_api_token(patch_pool), methods=["PATCH"])
    bp.add_url_rule("/pools/<int:pool_id>", "pools_delete", require_api_token(delete_pool), methods=["DELETE"])


def list_pools():
    items = [_item(p) for p in pools_repo.list_all(_tid())]
    return ok({"items": items, "count": len(items)})


def get_pool(pool_id: int):
    pool = pools_repo.get(_tid(), pool_id)
    if not pool:
        return fail("not_found", "الـ pool غير موجود.", status=404)
    return ok(_item(pool))


def create_pool():
    pool = _payload()
    if isinstance(pool, tuple):
        return pool
    return ok(_item(pools_repo.upsert(pool)), status=201)


def patch_pool(pool_id: int):
    if not pools_repo.get(_tid(), pool_id):
        return fail("not_found", "الـ pool غير موجود.", status=404)
    pool = _payload(pool_id)
    if isinstance(pool, tuple):
        return pool
    return ok(_item(pools_repo.upsert(pool)))


def delete_pool(pool_id: int):
    if not pools_repo.get(_tid(), pool_id):
        return fail("not_found", "الـ pool غير موجود.", status=404)
    pools_repo.delete(_tid(), pool_id)
    return ok({"id": pool_id, "deleted": True})
