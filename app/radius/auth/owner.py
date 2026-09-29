"""Owner-level principal — ONE shared predicate for the web panel and the API.

``is_owner_like(admin)`` answers «does this admin bypass RBAC like the owner?».
It is the single place both the web guard and the ``/api/v1`` guard ask, so the
owner / co-owner decision can never drift between the panel and the app again
(stress campaign p01 / D13: the API trusted the bare ``is_super_admin`` flag
while the web trusted the owner designation → a flag-holder could rename the
owner and reset his password over the API).

Resolution order:
  1. An explicit owner column on ``admins`` (``is_owner`` / ``is_co_owner`` /
     ``co_owner``) when the schema has one — the co-owner designation that the
     grant-model stream adds. Read from the object first, then from the DB.
  2. Otherwise the existing web predicate ``admins_repo.admin_is_owner`` (the
     designated owner set synced from the licensing panel, or the root admin).

The bare ``is_super_admin`` flag is deliberately NOT enough (it is the
assignable «super_admin» role / a licensing override, not ownership).

``can_modify_admin(actor, target)`` protects owner accounts: only an
owner-like actor may rename / reset the password of / demote / disable /
delete an owner-like target.
"""
from __future__ import annotations

from typing import Any

# Columns that, when present on ``admins``, explicitly mark an owner/co-owner.
_OWNER_COLUMNS: tuple[str, ...] = ("is_owner", "is_co_owner", "co_owner")


def _owner_columns_present() -> tuple[str, ...]:
    """The owner columns that exist on the ``admins`` table (schema probe)."""
    try:
        from ..db.connection import db
        rows = db().execute("PRAGMA table_info(admins)").fetchall()
        names = {str(r[1]) for r in rows}
    except Exception:  # noqa: BLE001 — no DB / transient error → none
        return ()
    return tuple(c for c in _OWNER_COLUMNS if c in names)


def _explicit_owner_flag(admin: Any) -> bool:
    """True when an explicit owner/co-owner column marks this admin."""
    for col in _OWNER_COLUMNS:
        if bool(getattr(admin, col, False)):
            return True
    aid = getattr(admin, "id", None)
    if not aid:
        return False
    cols = _owner_columns_present()
    if not cols:
        return False
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT " + ", ".join(cols) + " FROM admins WHERE id = ?",
            (int(aid),),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return False
    if row is None:
        return False
    return any(bool(row[i]) for i in range(len(cols)))


def is_owner_like(admin: Any) -> bool:
    """Owner or co-owner — the principal that bypasses RBAC (web + API)."""
    if admin is None:
        return False
    if _explicit_owner_flag(admin):
        return True
    try:
        from ..db.repos import admins_repo
        return bool(admins_repo.admin_is_owner(admin))
    except Exception:  # noqa: BLE001 — never grant on an unexpected error
        return False


def is_owner_like_id(admin_id: int | None) -> bool:
    """``is_owner_like`` by admin id (``False`` for a missing admin)."""
    if not admin_id:
        return False
    try:
        from ..db.repos import admins_repo
        admin = admins_repo.get_admin(int(admin_id))
    except Exception:  # noqa: BLE001
        return False
    return is_owner_like(admin)


def can_modify_admin(actor: Any, target: Any) -> bool:
    """May ``actor`` rename / reset the password of / demote / disable /
    delete ``target``? An owner-like target is protected from everyone who is
    not owner-like themselves."""
    if target is None:
        return True
    if not is_owner_like(target):
        return True
    return is_owner_like(actor)


__all__ = ["is_owner_like", "is_owner_like_id", "can_modify_admin"]
