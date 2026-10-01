"""Who sees / manages which distributors — one predicate for web and API.

«مدير عام» (the ``super_admin`` role) is "every non-owner permission", so it
sees and manages ALL distributors — like the owner / co-owner. Any other
manager keeps the ownership scope: only the distributors he owns
(``distributors.admin_id``). A distributor LOGIN (``distributors.login_admin_id``)
is never widened, even when its role is «مدير عام».
"""
from __future__ import annotations

from typing import Optional


def is_distributor_login(admin_id: Optional[int], *, tenant_id: int = 1) -> bool:
    if not admin_id:
        return False
    try:
        from ..db.repos import operations_repo
        return operations_repo.get_distributor_by_admin(int(tenant_id or 1), int(admin_id)) is not None
    except Exception:  # noqa: BLE001 — never widen on a lookup error
        return False


def sees_all_distributors(admin_id: Optional[int], *, tenant_id: int = 1) -> bool:
    """Owner / co-owner, or the «مدير عام» role — never a distributor login."""
    if not admin_id:
        return False
    try:
        from ..auth.owner import is_owner_like
        if is_owner_like(int(admin_id)):
            return True
    except Exception:  # noqa: BLE001
        return False
    if is_distributor_login(admin_id, tenant_id=tenant_id):
        return False
    try:
        from ..db.repos import admins_repo
        admin = admins_repo.get_admin(int(admin_id))
        rid = getattr(admin, "role_id", None) if admin else None
        return bool(rid and admins_repo.role_is_super(admins_repo.get_role(int(rid))))
    except Exception:  # noqa: BLE001
        return False


def distributor_accessible(admin_id: Optional[int], distributor: Optional[dict], *,
                           tenant_id: int = 1) -> bool:
    """May this admin see / manage this distributor? (owned, or sees-all)."""
    if not distributor:
        return False
    if sees_all_distributors(admin_id, tenant_id=tenant_id):
        return True
    return bool(admin_id) and int(distributor.get("admin_id") or 0) == int(admin_id)


def list_owner_filter(admin_id: Optional[int], *, tenant_id: int = 1) -> Optional[int]:
    """``admin_id`` filter for ``list_distributors``: None = all, else the owner."""
    if sees_all_distributors(admin_id, tenant_id=tenant_id):
        return None
    return int(admin_id or 0)


__all__ = ["is_distributor_login", "sees_all_distributors", "distributor_accessible",
           "list_owner_filter"]
