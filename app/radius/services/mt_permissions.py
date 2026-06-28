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

# VX2 — Selected Sites VPS Exit. The feature carries serious
# routing semantics, so it carves its own permission surface
# instead of reusing PERM_PROGRAM (whose holders should not
# automatically gain VPS-exit authority).
PERM_SITE_EXIT_VIEW                    = "site_exit.view"
PERM_SITE_EXIT_MANAGE                  = "site_exit.manage"
PERM_SITE_EXIT_PREVIEW                 = "site_exit.preview"
PERM_SITE_EXIT_APPLY                   = "site_exit.apply"
PERM_SITE_EXIT_OVERRIDE_BACKUP_WARNING = "site_exit.override_backup_warning"
PERM_SITE_EXIT_ENABLE_RISKY_GROUPS     = "site_exit.enable_risky_groups"

# NPC — Network Policy Center. Three sub-services × four
# verbs (view / manage / preview / apply). Same split rationale
# as VX2: a holder of `PERM_PROGRAM` should NOT automatically
# gain network-policy authority. Apply is opt-in even for
# admins (see _IMPLIED_BY_ADMIN below) — destructive surface.
PERM_NPC_REMOTE_ACCESS_VIEW    = "npc.remote_access.view"
PERM_NPC_REMOTE_ACCESS_MANAGE  = "npc.remote_access.manage"
PERM_NPC_REMOTE_ACCESS_PREVIEW = "npc.remote_access.preview"
PERM_NPC_REMOTE_ACCESS_APPLY   = "npc.remote_access.apply"

PERM_NPC_WEB_BLOCK_VIEW    = "npc.web_block.view"
PERM_NPC_WEB_BLOCK_MANAGE  = "npc.web_block.manage"
PERM_NPC_WEB_BLOCK_PREVIEW = "npc.web_block.preview"
PERM_NPC_WEB_BLOCK_APPLY   = "npc.web_block.apply"

PERM_NPC_WALLED_GARDEN_VIEW    = "npc.walled_garden.view"
PERM_NPC_WALLED_GARDEN_MANAGE  = "npc.walled_garden.manage"
PERM_NPC_WALLED_GARDEN_PREVIEW = "npc.walled_garden.preview"
PERM_NPC_WALLED_GARDEN_APPLY   = "npc.walled_garden.apply"

# أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
# كانت هنا صلاحيات «لوحة التراخيص — نفق تغيير IP المدفوع»
# (PERM_LICENSING_VIEW/MANAGE). حوكمة مركزية للمالك، لا تخص لوحة العميل.


ALL_PERMISSIONS: tuple[str, ...] = (
    PERM_VIEW, PERM_DIAGNOSTICS, PERM_MANAGE,
    PERM_PROGRAM, PERM_DEPLOY_LOGIN, PERM_ROLLBACK,
    PERM_BACKUP, PERM_RESTORE,
    PERM_AUDIT_VIEW, PERM_ADMIN,
    # VX2 — keep these explicit in the list so the permission
    # matrix page (O11) surfaces them.
    PERM_SITE_EXIT_VIEW, PERM_SITE_EXIT_MANAGE,
    PERM_SITE_EXIT_PREVIEW, PERM_SITE_EXIT_APPLY,
    PERM_SITE_EXIT_OVERRIDE_BACKUP_WARNING,
    PERM_SITE_EXIT_ENABLE_RISKY_GROUPS,
    # NPC — Network Policy Center surface.
    PERM_NPC_REMOTE_ACCESS_VIEW, PERM_NPC_REMOTE_ACCESS_MANAGE,
    PERM_NPC_REMOTE_ACCESS_PREVIEW, PERM_NPC_REMOTE_ACCESS_APPLY,
    PERM_NPC_WEB_BLOCK_VIEW, PERM_NPC_WEB_BLOCK_MANAGE,
    PERM_NPC_WEB_BLOCK_PREVIEW, PERM_NPC_WEB_BLOCK_APPLY,
    PERM_NPC_WALLED_GARDEN_VIEW, PERM_NPC_WALLED_GARDEN_MANAGE,
    PERM_NPC_WALLED_GARDEN_PREVIEW, PERM_NPC_WALLED_GARDEN_APPLY,
    # أُزيل: صلاحيات لوحة التراخيص (نفق تغيير IP) — حوكمة مركزية للمالك.
)


# Permissions PERM_ADMIN implies (so an admin role doesn't have
# to list every sub-permission). Routes ask for the *specific*
# permission they need; this map lets a single PERM_ADMIN cover
# them all without scattering "if admin then allow" checks.
_IMPLIED_BY_ADMIN: frozenset[str] = frozenset({
    PERM_VIEW, PERM_DIAGNOSTICS, PERM_MANAGE,
    PERM_PROGRAM, PERM_DEPLOY_LOGIN, PERM_ROLLBACK,
    PERM_BACKUP, PERM_RESTORE, PERM_AUDIT_VIEW,
    # VX2 — PERM_ADMIN implies the read-side site-exit perms
    # plus manage/preview (the day-to-day workflow). Apply +
    # override + risky-groups are intentionally NOT auto-
    # granted so even an admin must opt in explicitly to the
    # destructive surface.
    PERM_SITE_EXIT_VIEW, PERM_SITE_EXIT_MANAGE,
    PERM_SITE_EXIT_PREVIEW,
    # NPC — same posture: view / manage / preview implied;
    # `.apply` is opt-in even for admins.
    PERM_NPC_REMOTE_ACCESS_VIEW,
    PERM_NPC_REMOTE_ACCESS_MANAGE,
    PERM_NPC_REMOTE_ACCESS_PREVIEW,
    PERM_NPC_WEB_BLOCK_VIEW,
    PERM_NPC_WEB_BLOCK_MANAGE,
    PERM_NPC_WEB_BLOCK_PREVIEW,
    PERM_NPC_WALLED_GARDEN_VIEW,
    PERM_NPC_WALLED_GARDEN_MANAGE,
    PERM_NPC_WALLED_GARDEN_PREVIEW,
    # أُزيل: PERM_LICENSING_VIEW — لوحة التراخيص حوكمة مركزية للمالك.
})


# ─── Resolution ──────────────────────────────────────────────


def _current_admin():
    """Resolve the admin attached to the current Flask session.

    Looks in two places in order: `g.admin_id` (API auth path
    sets it explicitly) then `session["admin_id"]` (web login
    flow). Returns the Admin DTO or None.
    """
    admin_id = getattr(g, "admin_id", None)
    if admin_id is None:
        try:
            from flask import session
            admin_id = session.get("admin_id")
        except Exception:  # noqa: BLE001
            admin_id = None
    if admin_id is None:
        return None
    try:
        from .admins import get_admins_service
        svc = get_admins_service()
        # admins_store exposes `get_admin(id)`. The S3.1 test
        # stubs use a custom `_store` shape; tolerate both.
        store = getattr(svc, "_store", None)
        if store is None:
            return None
        fn = (getattr(store, "get_admin", None)
              or getattr(store, "by_id", None))
        if fn is None:
            return None
        return fn(int(admin_id))
    except Exception:  # noqa: BLE001
        return None


def _is_primary_owner(admin) -> bool:
    """An OWNER account gets every permission. Owner = membership in the
    designated owner set synced from the licensing panel (by username/email;
    MULTIPLE owners qualify), or — until a set syncs — the legacy min-id owner.
    Falls back to the ``is_super_admin`` flag only when the lookup can't run (no
    app context / DB error — e.g. pure-service unit tests), so the owner is never
    locked out. The assignable ``super_admin`` role no longer auto-grants here:
    its holder flows through ``permissions_of`` like any role."""
    try:
        from ..db.repos import admins_repo
        return admins_repo.admin_is_owner(admin)
    except Exception:  # noqa: BLE001
        return bool(getattr(admin, "is_super_admin", False))


def admin_permissions(admin) -> frozenset[str]:
    """Resolve the full set of MikroTik permissions for `admin`.

    The primary owner gets everything. Otherwise we ask the admins
    service for the role's permission list and filter to the
    MikroTik allowlist (anything outside ALL_PERMISSIONS is
    ignored — keeps the surface narrow).
    """
    if admin is None:
        return frozenset()
    if _is_primary_owner(admin):
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


# ─── Route decorator ──────────────────────────────────────────


def requires_perm(*perms: str):
    """Decorator for route handlers.

        bp.add_url_rule("/program/apply", "x",
                        requires_perm(PERM_PROGRAM)(my_handler),
                        methods=["POST"])

    Returns the wrapped handler unchanged when the current admin
    holds every listed permission. Otherwise:
      - JSON requests → 403 JSON {ok, error}
      - HTML requests → render the standard admin/forbidden.html
        with the Arabic reason. If that template is missing,
        fall back to a tiny plain-text 403 so the surface still
        works.

    Anonymous requests (no admin_id in session) get the same
    forbidden response, NOT a login redirect — the login redirect
    is handled by the blueprint's global before_request hook.
    Once you're past that, an authenticated-but-unauthorized
    request should see a 403, not a loop back to login.
    """
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Lazy import to avoid circulars at module load.
            from flask import jsonify, render_template, request
            ok, reason = require_perms(*perms)
            if ok:
                return fn(*args, **kwargs)
            accept = (request.headers.get("Accept") or "").lower()
            if "application/json" in accept:
                return jsonify(
                    {"ok": False, "error": reason or "forbidden"}
                ), 403
            try:
                return render_template(
                    "admin/forbidden.html",
                    reason=reason or "ليست لديك صلاحية كافية.",
                ), 403
            except Exception:  # noqa: BLE001
                # Template missing → don't 500 the operator;
                # surface a tiny readable page.
                return (
                    "<h1 dir=\"rtl\" lang=\"ar\" "
                    "style=\"font-family: sans-serif\">"
                    "403 — ممنوع</h1><p dir=\"rtl\">"
                    f"{reason or 'ليست لديك صلاحية كافية.'}"
                    "</p>",
                    403,
                    {"Content-Type": "text/html; charset=utf-8"},
                )
        return wrapper
    return deco


__all__ = [
    "PERM_VIEW", "PERM_DIAGNOSTICS", "PERM_MANAGE",
    "PERM_PROGRAM", "PERM_DEPLOY_LOGIN", "PERM_ROLLBACK",
    "PERM_BACKUP", "PERM_RESTORE",
    "PERM_SITE_EXIT_VIEW", "PERM_SITE_EXIT_MANAGE",
    "PERM_SITE_EXIT_PREVIEW", "PERM_SITE_EXIT_APPLY",
    "PERM_SITE_EXIT_OVERRIDE_BACKUP_WARNING",
    "PERM_SITE_EXIT_ENABLE_RISKY_GROUPS",
    "PERM_NPC_REMOTE_ACCESS_VIEW",
    "PERM_NPC_REMOTE_ACCESS_MANAGE",
    "PERM_NPC_REMOTE_ACCESS_PREVIEW",
    "PERM_NPC_REMOTE_ACCESS_APPLY",
    "PERM_NPC_WEB_BLOCK_VIEW",
    "PERM_NPC_WEB_BLOCK_MANAGE",
    "PERM_NPC_WEB_BLOCK_PREVIEW",
    "PERM_NPC_WEB_BLOCK_APPLY",
    "PERM_NPC_WALLED_GARDEN_VIEW",
    "PERM_NPC_WALLED_GARDEN_MANAGE",
    "PERM_NPC_WALLED_GARDEN_PREVIEW",
    "PERM_NPC_WALLED_GARDEN_APPLY",
    # أُزيل: PERM_LICENSING_* — لوحة التراخيص حوكمة مركزية للمالك.
    "PERM_AUDIT_VIEW", "PERM_ADMIN",
    "ALL_PERMISSIONS",
    "admin_permissions",
    "has",
    "current_admin_has",
    "require_perms",
    "requires_perm",
]
