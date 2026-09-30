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
    """Unscoped visibility: an unbound master credential, an owner-like admin
    (original owner or co-owner — ``auth/owner.is_owner_like``), or the
    «مدير عام» role (``super_admin`` = all non-owner permissions, incl. «رؤية كل
    المشتركين»). The raw ``is_super_admin`` column alone no longer counts
    (permmodel D12/D13: a flag set on a limited role used to unlock everything)."""
    aid = admin_id()
    if aid <= 0:
        return True
    try:
        from ..radius.auth.owner import is_owner_like
        if is_owner_like(aid):
            return True
    except Exception:  # noqa: BLE001
        pass
    admin = token_admin()
    if admin is None or not getattr(admin, "role_id", None):
        return False
    try:
        from ..radius.db.repos import admins_repo
        return admins_repo.role_is_super(admins_repo.get_role(int(admin.role_id)))
    except Exception:  # noqa: BLE001
        return False


def is_owner_level() -> bool:
    """Owner-level principal (NOT visibility): an unbound master credential, or
    the owner / co-owner behind the token — the web owner predicate
    (``radius.auth.owner.is_owner_like``). Neither the bare ``is_super_admin``
    flag nor the «مدير عام» role is owner-level (p01/D13). Used where an
    action reaches OTHER admins' assets (e.g. every API token)."""
    aid = admin_id()
    if aid <= 0:
        return True
    try:
        from ..radius.auth.owner import is_owner_like
        return bool(is_owner_like(aid))
    except Exception:  # noqa: BLE001 — never grant on an unexpected error
        return False


def is_full_access() -> bool:
    """Full (unscoped) visibility: owner-level principals, or the «مدير عام»
    role — unless the admin behind the token IS a distributor login, which is
    always scoped to its assigned batches. The token's scope list is NOT
    trusted for a bound principal: an app login always carries
    ``admin:full``, so the admin behind it decides."""
    if is_owner_level():
        return True
    if current_distributor():
        return False
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
    message = _FORBIDDEN_AR
    try:
        # D24: name what ACTUALLY denied (section lock, action gate, bulk.ops…)
        # rather than the table key, which the admin may well hold.
        info = getattr(g, "_rbac_denial", None) or {}
        from ..radius.routes.blueprint import _PERM_GUARDED, denial_message
        perm = info.get("permission") or (None if info.get("reason") else _PERM_GUARDED.get(endpoint))
        if perm:
            details["permission"] = perm
        if info.get("reason"):
            details["reason"] = info["reason"]
            message = denial_message() or message
    except Exception:  # noqa: BLE001
        pass
    if status == 429:
        return fail("rate_limited", "بلغت الحدّ اليوميّ المسموح لهذا الإجراء.",
                    status=429, details=details)
    return fail("forbidden", message, status=403, details=details)


def require_web_permission(endpoint: str, method: str = "POST"):
    """Returns an error response when the token's admin may not perform the
    web action ``endpoint``; ``None`` when allowed."""
    code = web_permission_denial(endpoint, method)
    if code is not None:
        return forbidden_response(endpoint, code)
    return None


def current_distributor() -> dict | None:
    # Owner / co-owner / unbound credentials are never a distributor login;
    # anyone else (even the «مدير عام» role) whose account IS a distributor
    # (``distributors.login_admin_id``) is scoped to that distributor.
    if is_owner_level():
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
    """fix3 (F01 F10): «رؤية كل حِزم البطاقات» — the same predicate as the web
    (``services/card_batch_scope``): owner-level / view-all → any batch; else
    the manager's own ∪ his distributors' (a distributor login: its own).
    Used to scope only distributor logins, so every manager read every batch."""
    if is_owner_level():
        return True
    from ..radius.services.card_batch_scope import batch_accessible
    return batch_accessible(batch_id, admin_id(), tenant_id=tenant_id())


def subscriber_in_scope(username: str = "", subscriber_id: int | None = None) -> bool:
    """D09 — the SAME predicate as the web panel (``services/subscriber_scope``):
    owner/co-owner or «عرض كل المشتركين» → any; else own subscribers ∪ those of
    the manager's distributors ∪ (a distributor login) its assigned batches."""
    if is_full_access():
        return True
    # fix3: a distributor login uses the SAME predicate (its assigned batches ∪
    # the subscribers it created itself) — it used to see only batch rows, so
    # a subscriber it had just created vanished (F07 H1).
    from ..radius.services.subscriber_scope import subscriber_accessible
    return subscriber_accessible(admin_id(), username=username,
                                 subscriber_id=subscriber_id, tenant_id=tenant_id())


def subscriber_scope_admin_id() -> int | None:
    """Owner-scope for list queries (None = sees all). Distributor logins go
    through the same predicate (their assigned batches ∪ their own)."""
    if is_full_access():
        return None
    from ..radius.services.subscriber_scope import scope_admin_id
    return scope_admin_id(admin_id(), tenant_id=tenant_id())


def deny_out_of_scope():
    return fail(
        "forbidden",
        "هذه البيانات ليست ضمن نطاقك (تخصّ مديرًا أو موزّعًا آخر).",
        status=403,
        details={"reason": "out_of_scope"},
    )
