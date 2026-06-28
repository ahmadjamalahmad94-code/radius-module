"""mt_permission_matrix — O11 read-only permission review.

Composes one row per admin × MikroTik-permission so operators
can audit, at a glance, who can do what. Pure DB read.

This is intentionally *not* an editing UI: it would otherwise
add a fast, dangerous knob (one click to grant PERM_PROGRAM).
The Phase O directive explicitly puts role editing UI out of
scope. Operators still change roles through the existing
admins page; this view only surfaces the current state.

Shape (returned by `build_matrix`):

    PermissionMatrix(
        permissions=("mikrotik.view", "mikrotik.diagnostics", ...),
        rows=[
            AdminRow(
                admin_id=1, username="alice",
                full_name="Alice", role_name="operator",
                is_super_admin=False,
                granted={"mikrotik.view": True, ...},
                granted_count=3,
            ),
            ...
        ],
    )

Granting model:
  - is_super_admin=True → every cell True (and `via_super=True`)
  - PERM_ADMIN → every implied cell True (and `via_admin=True`)
  - Otherwise → True iff the admin's role-level permissions
    list contains the perm string.

`granted` is the boolean projection a template renders directly.
`granted_count` is the row total (super admins always == total).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..db.repos import admins_repo
from . import mt_permissions as mp


@dataclass(frozen=True)
class AdminRow:
    admin_id: int
    username: str
    full_name: str
    role_id: int | None
    role_name: str
    is_super_admin: bool
    enabled: bool
    granted: dict[str, bool] = field(default_factory=dict)
    via_super: bool = False
    via_admin: bool = False
    granted_count: int = 0


@dataclass(frozen=True)
class PermissionMatrix:
    permissions: tuple[str, ...]
    rows: tuple[AdminRow, ...]

    def total_admins(self) -> int:
        return len(self.rows)

    def grants_for(self, perm: str) -> int:
        return sum(1 for r in self.rows if r.granted.get(perm))


def _role_name(role_id: int | None) -> str:
    if role_id is None:
        return ""
    try:
        role = admins_repo.get_role(int(role_id))
    except Exception:  # noqa: BLE001
        return ""
    if role is None:
        return ""
    return role.display_name or role.name or ""


def _granted_set(admin: Any) -> tuple[frozenset[str], bool, bool]:
    """Resolve which MikroTik perms `admin` holds.

    Returns (perm_set, via_super, via_admin).
    """
    if admin is None:
        return frozenset(), False, False
    # «via_super» = تجاوزٌ كامل، وهو الآن للمالك الرئيسي وحده. حامل دور
    # super_admin المُسنَد يَظهر بصلاحياته الفعليّة من الدور لا كـ«الكل».
    if mp._is_primary_owner(admin):
        return frozenset(mp.ALL_PERMISSIONS), True, False
    raw = ()
    try:
        raw = tuple(admins_repo.admin_permissions(admin) or ())
    except Exception:  # noqa: BLE001
        raw = ()
    perms = {p for p in raw if p in mp.ALL_PERMISSIONS}
    via_admin = mp.PERM_ADMIN in perms
    if via_admin:
        # PERM_ADMIN implies the rest (mirrors mt_permissions).
        perms.update({
            mp.PERM_VIEW, mp.PERM_DIAGNOSTICS, mp.PERM_MANAGE,
            mp.PERM_PROGRAM, mp.PERM_DEPLOY_LOGIN,
            mp.PERM_ROLLBACK, mp.PERM_BACKUP, mp.PERM_RESTORE,
            mp.PERM_AUDIT_VIEW,
        })
    return frozenset(perms), False, via_admin


def build_matrix() -> PermissionMatrix:
    """Walk every active admin and build the matrix.

    Soft-deleted admins are excluded — they can't act on the
    system. Order is stable by admin id so the page doesn't
    shuffle between refreshes.
    """
    admins = sorted(
        admins_repo.list_admins(include_deleted=False),
        key=lambda a: (a.id or 0),
    )
    rows: list[AdminRow] = []
    for a in admins:
        perms, via_super, via_admin = _granted_set(a)
        granted = {p: (p in perms) for p in mp.ALL_PERMISSIONS}
        rows.append(AdminRow(
            admin_id=int(a.id or 0),
            username=str(a.username or ""),
            full_name=str(a.full_name or ""),
            role_id=a.role_id,
            role_name=_role_name(a.role_id),
            is_super_admin=bool(a.is_super_admin),
            enabled=bool(a.enabled),
            granted=granted,
            via_super=via_super,
            via_admin=via_admin,
            granted_count=sum(1 for v in granted.values() if v),
        ))
    return PermissionMatrix(
        permissions=tuple(mp.ALL_PERMISSIONS),
        rows=tuple(rows),
    )


__all__ = ["AdminRow", "PermissionMatrix", "build_matrix"]
