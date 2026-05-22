"""S3 — MikroTik / NAS permission helpers.

The audit (preflight) found that the admins table already
carries `is_super_admin` + `role_id`, and roles can have
permissions attached. What was missing: a stable allowlist of
permission *names* for the MikroTik domain, and a single
choke-point that the routes (in S3.2) call to authorize.

This file owns both. Routes do NOT import admins_repo directly
for permission checks — they go through `current_admin_has()`.
That gives us one place to extend the policy later (tenant
scope, IP allowlists, time-of-day, ...).

Default policy:
  * Super admins (`is_super_admin = 1`) always allowed.
  * Other admins must have the named permission in their role.
  * Anonymous / no admin → denied.

The permission strings are stable identifiers — never rename;
add new ones instead. Role rows in the DB store these as text.
"""
from __future__ import annotations

from typing import Iterable

from flask import g

from ..core.tenant import DEFAULT_TENANT_ID


# ─── Permission catalogue ─────────────────────────────────────


# View access: navigation + read-only data + diagnostics page.
PERM_VIEW           = "mikrotik.view"
PERM_DIAGNOSTICS    = "mikrotik.diagnostics"

# Day-to-day management: enable/disable, edit metadata, kick
# sessions (read-only by default in P6 but the contract is here
# for when a disconnect button ships).
PERM_MANAGE         = "mikrotik.manage"

# Programming surfaces — Q1/Q2/Q3 plan + apply.
PERM_PROGRAM        = "mikrotik.program"

# R3 — hotspot login deploy.
PERM_DEPLOY_LOGIN   = "mikrotik.deploy_login"

# Q4 — destructive rollback.
PERM_ROLLBACK       = "mikrotik.rollback"

# S8 — backup save + restore (restore stays gated; see S8.4).
PERM_BACKUP         = "mikrotik.backup"
PERM_RESTORE        = "mikrotik.restore"

# S2.2 — audit log center.
PERM_AUDIT_VIEW     = "mikrotik.audit.view"

# Top-level admin escape hatch — anyone with this can do
# everything in the MikroTik domain. Roughly the operator who
# would otherwise need is_super_admin but doesn't (yet).
PERM_ADMIN          = "mikrotik.admin"


ALL_PERMISSIONS: tuple[str, ...] = (
    PERM_VIEW, PERM_DIAGNOSTICS, PERM_MANAGE,
    PERM_PROGRAM, PERM_DEPLOY_LOGIN, PERM_ROLLBACK,
    PERM_BACKUP, PERM_RESTORE,
    PERM_AUDIT_VIEW, PERM_ADMIN,
)


# Permissions PERM_ADMIN implies (so an admin role doesn't have
# to list every sub-permission). Routes ask for the *specific*
# permission they need; this map lets a single PERM_ADMIN cover
# them all without scattering "if admin then allow" checks.
_IMPLIED_BY_ADMIN: frozenset[str] = frozenset({
    PERM_VIEW, PERM_DIAGNOSTICS, PERM_MANAGE,
    PERM_PROGRAM, PERM_DEPLOY_LOGIN, PERM_ROLLBACK,
    PERM_BACKUP, PERM_RESTORE, PERM_AUDIT_VIEW,
})


# ─── Resolution ──────────────────────────────────────────────


def _current_admin():
    """Resolve the admin attached to the current Flask session.

    Returns the Admin DTO or None. Pulled here (not at module
    import) so tests can swap g.admin_id without dragging in the
    rest of the admins service.
    """
    admin_id = getattr(g, "admin_id", None)
    if admin_id is None:
        return None
    try:
        from .admins import get_admins_service
        svc = get_admins_service()
        return svc._store.by_id(int(admin_id))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None


def admin_permissions(admin) -> frozenset[str]:
    """Resolve the full set of MikroTik permissions for `admin`.

    Super admins get everything. Otherwise we ask the admins
    service for the role's permission list and filter to the
    MikroTik allowlist (anything outside ALL_PERMISSIONS is
    ignored — keeps the surface narrow).
    """
    if admin is None:
        return frozenset()
    if getattr(admin, "is_super_admin", False):
        return frozenset(ALL_PERMISSIONS)
    try:
        from .admins import get_admins_service
        raw = tuple(get_admins_service().permissions_of(admin) or ())
    except Exception:  # noqa: BLE001
        raw = ()
    perms: set[str] = set()
    for p in raw:
        if p in ALL_PERMISSIONS:
            perms.add(p)
    if PERM_ADMIN in perms:
        perms.update(_IMPLIED_BY_ADMIN)
    return frozenset(perms)


def has(admin, perm: str) -> bool:
    """`True` if `admin` (DTO or None) holds `perm`."""
    if perm not in ALL_PERMISSIONS:
        return False
    return perm in admin_permissions(admin)


def current_admin_has(perm: str) -> bool:
    """Convenience for routes — pulls the admin from the Flask
    session and checks the permission. Returns False if no admin
    is signed in."""
    return has(_current_admin(), perm)


def require_perms(*perms: str) -> tuple[bool, str]:
    """Return (ok, reason) for a route-layer guard. `ok=True`
    when the current admin has ALL requested perms. The reason
    string is operator-facing Arabic so the 403 page can render
    it directly."""
    if not perms:
        return True, ""
    admin = _current_admin()
    if admin is None:
        return False, "تحتاج إلى تسجيل الدخول للوصول لهذه الصفحة."
    held = admin_permissions(admin)
    missing = [p for p in perms if p not in held]
    if missing:
        return False, "ليست لديك صلاحية كافية: " + ", ".join(missing)
    return True, ""


__all__ = [
    "PERM_VIEW", "PERM_DIAGNOSTICS", "PERM_MANAGE",
    "PERM_PROGRAM", "PERM_DEPLOY_LOGIN", "PERM_ROLLBACK",
    "PERM_BACKUP", "PERM_RESTORE",
    "PERM_AUDIT_VIEW", "PERM_ADMIN",
    "ALL_PERMISSIONS",
    "admin_permissions",
    "has",
    "current_admin_has",
    "require_perms",
]
