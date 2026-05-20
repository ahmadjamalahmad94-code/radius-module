"""API scope helpers for distributors/sub-admins.

Main/admin tokens keep full visibility. Tokens created by an admin linked to a
distributor are scoped to assigned card batches and related subscribers.
"""
from __future__ import annotations

from flask import g

from .responses import fail


def tenant_id() -> int:
    return int(getattr(g, "tenant_id", 1))


def admin_id() -> int:
    return int(getattr(g, "admin_id", 0) or 0)


def is_full_access() -> bool:
    scopes = set(getattr(g, "api_token_scopes", []) or [])
    return admin_id() <= 0 or "admin:full" in scopes or "*" in scopes


def current_distributor() -> dict | None:
    if is_full_access():
        return None
    try:
        from ..radius.db.repos import operations_repo
        return operations_repo.get_distributor_by_admin(tenant_id(), admin_id())
    except Exception:
        return None


def distributor_batch_ids() -> set[int]:
    dist = current_distributor()
    if not dist:
        return set()
    from ..radius.db.repos import operations_repo
    return set(operations_repo.assigned_batch_ids(tenant_id(), int(dist["id"])))


def batch_in_scope(batch_id: int) -> bool:
    dist = current_distributor()
    if not dist:
        return True
    from ..radius.db.repos import operations_repo
    return operations_repo.batch_assigned_to_distributor(
        tenant_id(), batch_id, int(dist["id"]))


def subscriber_in_scope(username: str = "", subscriber_id: int | None = None) -> bool:
    dist = current_distributor()
    if not dist:
        return True
    from ..radius.db.repos import operations_repo
    return operations_repo.subscriber_in_distributor_scope(
        tenant_id(),
        int(dist["id"]),
        username=username,
        subscriber_id=subscriber_id,
    )


def deny_out_of_scope():
    return fail(
        "forbidden",
        "هذا التوكن لا يملك صلاحية الوصول إلى هذه البيانات.",
        status=403,
    )
