"""fix3 (F01 F5 / F01 F18) — who may SEE a subscriber's password or balance.

One decision for the web pages, the exports and the API:

* password — «رؤية كلمة مرور المشترك»: the RBAC key ``scope.view_passwords``
  (role) or the manager grant ``can_see_password``; owner / co-owner always.
* balance  — «رؤية الرصيد»: the manager grant ``can_see_balance`` (the web 360
  already hid it); owner / co-owner always.

``admin_id=None`` = the admin behind the current request (web session or API
token); no admin at all (unbound master credential / background job) = allowed.
"""
from __future__ import annotations

from typing import Iterable, Optional

MASK = "••••••"


def _resolve(admin_id: Optional[int]) -> Optional[int]:
    if admin_id is not None:
        return int(admin_id) or None
    from .subscriber_scope import request_admin_id
    return request_admin_id()


def _perms_of(admin_id: int) -> set:
    from ..db.repos import admins_repo
    admin = admins_repo.get_admin(int(admin_id))
    return set(admins_repo.admin_permissions(admin)) if admin is not None else set()


def can_view_subscriber_passwords(admin_id: Optional[int] = None, *,
                                  perms: Optional[Iterable[str]] = None,
                                  tenant_id: int = 1) -> bool:
    aid = _resolve(admin_id)
    if not aid:
        return True
    from ..auth.owner import is_owner_like
    if is_owner_like(aid):
        return True
    keys = set(perms) if perms is not None else _perms_of(aid)
    if "scope.view_passwords" in keys:
        return True
    try:
        from . import manager_grants as _mg
        return bool(_mg.can_see(aid, "can_see_password", tenant_id=tenant_id))
    except Exception:  # noqa: BLE001 — fail-closed
        return False


def can_view_card_passwords(admin_id: Optional[int] = None, *,
                            perms: Optional[Iterable[str]] = None) -> bool:
    """Card passwords (p01/D08): owner / co-owner, ``scope.view_passwords`` or
    ``cards.print`` (printing hands the passwords out anyway)."""
    aid = _resolve(admin_id)
    if not aid:
        return True
    from ..auth.owner import is_owner_like
    if is_owner_like(aid):
        return True
    keys = set(perms) if perms is not None else _perms_of(aid)
    return bool(keys & {"scope.view_passwords", "cards.print"})


def mask_passwords(rows, *, visible: bool, key: str = "password"):
    """Rows (dicts) with ``key`` replaced by ``MASK`` unless ``visible``."""
    if visible:
        return rows
    out = []
    for r in rows or []:
        if isinstance(r, dict) and r.get(key):
            r = dict(r)
            r[key] = MASK
        out.append(r)
    return out


def can_view_balance(admin_id: Optional[int] = None, *, tenant_id: int = 1) -> bool:
    aid = _resolve(admin_id)
    if not aid:
        return True
    from ..auth.owner import is_owner_like
    if is_owner_like(aid):
        return True
    try:
        from . import manager_grants as _mg
        return bool(_mg.can_see(aid, "can_see_balance", tenant_id=tenant_id))
    except Exception:  # noqa: BLE001 — fail-closed
        return False


__all__ = ["MASK", "can_view_subscriber_passwords", "can_view_card_passwords",
           "mask_passwords", "can_view_balance"]
