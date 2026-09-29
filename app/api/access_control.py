"""API scope helpers for distributors/sub-admins.

Unbound master credentials (env tokens / tokens with no ``created_by``) keep
full visibility. A token minted by an admin (the app login, HTTP Basic, or a
token created from the panel/API by that admin) carries THAT admin's identity:
it is subject to the admin's role permissions, granular grants and distributor
scoping — exactly like the admin's web session. Only the owner / super admin
keeps full access. The ``admin:full`` scope alone no longer short-circuits the
scoping (stress-test 2026-09-28: every app login got ``admin:full`` → a
distributor's app could read/act on every subscriber — IDOR).
"""
from __future__ import annotations

from typing import Any, Optional

from flask import g

from .responses import fail

_FORBIDDEN_AR = "ليس لديك صلاحية لتنفيذ هذا الإجراء."


def tenant_id() -> int:
    return int(getattr(g, "tenant_id", 1))


def admin_id() -> int:
    return int(getattr(g, "admin_id", 0) or 0)


def token_admin():
    """The (non-deleted) admin row behind the current credential, cached on
    ``g`` for the request. ``None`` for an unbound credential or a missing
    admin."""
    aid = admin_id()
    if aid <= 0:
        return None
    cache = getattr(g, "_ac_token_admin", None)
    if cache is not None and cache[0] == aid:
        return cache[1]
    try:
        from ..radius.db.repos import admins_repo
        admin = admins_repo.get_admin(aid)
    except Exception:  # noqa: BLE001
        admin = None
    g._ac_token_admin = (aid, admin)
    return admin


def is_owner_or_super() -> bool:
    """Owner-level principal: an unbound master credential, or the owner /
    co-owner behind the token — the web owner predicate
    (``radius.auth.owner.is_owner_like``). The bare ``is_super_admin`` flag is
    NOT owner-level any more (p01/D13: a flag-holder took over the owner's
    account through the API)."""
    aid = admin_id()
    if aid <= 0:
        return True
    from ..radius.auth.owner import is_owner_like
    return is_owner_like(token_admin())


def is_full_access() -> bool:
    """Full (unscoped) visibility — owner-level principals only. The token's
    scope list is NOT trusted for a bound principal: an app login always
    carries ``admin:full``, so the admin behind it decides."""
    return is_owner_or_super()


def _token_identity() -> tuple[bool, tuple[str, ...], Any]:
    """(is_super, permissions, admin) of the admin behind the token, resolved
    exactly like the web login (owner flag via ``_resolve_is_super``, role
    permissions via the admins service)."""
    cached = getattr(g, "_ac_identity", None)
    if cached is not None and cached[0] == admin_id():
        return cached[1]
    admin = token_admin()
    if admin is None:
        ident = (False, (), None)
    else:
        from ..radius.auth.session_helpers import _resolve_is_super
        from ..radius.services.admins import get_admins_service
        try:
            perms = tuple(get_admins_service().permissions_of(admin))
        except Exception:  # noqa: BLE001 — same fallback as the web login
            perms = ()
        ident = (bool(_resolve_is_super(admin)), perms, admin)
    g._ac_identity = (admin_id(), ident)
    return ident


def token_bypasses_rbac() -> bool:
    """True when the credential bypasses RBAC like the web owner session: an
    unbound master credential, or the primary owner behind the token."""
    if admin_id() <= 0:
        return True
    return bool(_token_identity()[0])


def web_permission_denial(endpoint: str, method: str = "POST", *,
                          record_activity: bool = True) -> Optional[int]:
    """Same RBAC decision the web panel guard takes for ``radius.<endpoint>``,
    evaluated with the permissions of the admin behind the token. ``None`` =
    allowed, else the HTTP status (403/429). Unbound credentials are allowed."""
    if admin_id() <= 0:
        return None
    is_super, perms, admin = _token_identity()
    if admin is None or not getattr(admin, "enabled", False):
        return 403
    from ..radius.routes.blueprint import rbac_denial_status
    return rbac_denial_status(endpoint, method, is_super=is_super, perms=perms,
                              admin_id=admin_id(), tenant_id=tenant_id(),
                              record_activity=record_activity)


def can_view_card_passwords() -> bool:
    """May the credential read card passwords? Owner / co-owner / unbound
    credentials, or an admin holding ``scope.view_passwords`` or
    ``cards.print`` — the same rule as the web batch-cards page."""
    if token_bypasses_rbac():
        return True
    perms = set(_token_identity()[1])
    return bool(perms & {"scope.view_passwords", "cards.print"})


def forbidden_response(endpoint: str, status: int = 403):
    """Arabic JSON error for a denied web-parity permission check."""
    details: dict[str, Any] = {"web_endpoint": endpoint}
    try:
        from ..radius.routes.blueprint import _PERM_GUARDED
        perm = _PERM_GUARDED.get(endpoint)
        if perm:
            details["permission"] = perm
    except Exception:  # noqa: BLE001
        pass
    if status == 429:
        return fail("rate_limited", "بلغت الحدّ اليوميّ المسموح لهذا الإجراء.",
                    status=429, details=details)
    return fail("forbidden", _FORBIDDEN_AR, status=403, details=details)


def require_web_permission(endpoint: str, method: str = "POST"):
    """Returns an error response when the token's admin may not perform the
    web action ``endpoint``; ``None`` when allowed."""
    code = web_permission_denial(endpoint, method)
    if code is not None:
        return forbidden_response(endpoint, code)
    return None


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
