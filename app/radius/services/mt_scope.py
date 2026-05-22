"""S3.3 — Router scope enforcement.

Today HobeRadius scopes every `nas_devices` row by `tenant_id`,
and `_tid()` in each route already filters that way. What
hasn't existed: a single helper a future code path can call to
answer "does this admin own / can access router N?" — for the
moment that's just a tenant check, but the hook lives here so:

  1. Routes have ONE choke point that grows when the model
     grows (distributor scope, customer ownership, ...).
  2. Bypassing it later requires obvious intent (the function
     is named).
  3. Tests assert the contract end-to-end.

If a future migration adds `nas_devices.distributor_id` /
`nas_devices.assigned_to_admin_id` columns this is where the
extra check goes — every caller already routes through here.

NOTE: This does NOT replace the per-route `_tid()` filter that
each handler already uses in SQL queries. It's a defense in
depth — an extra `assert_router_accessible(nas_id)` after
loading the row catches "loaded under wrong tenant" bugs.
"""
from __future__ import annotations

from flask import session

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db


class RouterAccessDenied(Exception):
    """Raised when a NAS exists but the current admin can't
    reach it. Caller is expected to surface this as 403 / 404
    depending on policy."""


def _current_admin_id() -> int | None:
    aid = session.get("admin_id") if session else None
    if aid is None:
        return None
    try:
        return int(aid)
    except (TypeError, ValueError):
        return None


def _current_tenant_id() -> int:
    raw = session.get("tenant_id") if session else None
    try:
        return int(raw) if raw is not None else DEFAULT_TENANT_ID
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def admin_can_access_router(admin_id: int | None,
                            tenant_id: int,
                            nas_row: dict | None) -> bool:
    """Pure-function predicate. Returns False on any of:
      - nas_row is None (router doesn't exist in this tenant)
      - nas_row.tenant_id mismatches the resolver's tenant
      - admin_id is None (no admin in session)

    Extends here when distributor/owner columns ship.
    """
    if admin_id is None or nas_row is None:
        return False
    if int(nas_row.get("tenant_id") or 0) != int(tenant_id):
        return False
    return True


def assert_router_accessible(nas_id: int) -> dict:
    """Load the row, raise if the current admin can't see it.
    Returns the dict so the caller doesn't need a second query.
    """
    row = db().execute(
        "SELECT * FROM nas_devices "
        "WHERE id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id),),
    ).fetchone()
    row_dict = dict(row) if row else None
    tid = _current_tenant_id()
    aid = _current_admin_id()
    if not admin_can_access_router(aid, tid, row_dict):
        raise RouterAccessDenied(
            f"router {nas_id} not accessible to admin {aid} "
            f"(tenant {tid})"
        )
    return row_dict  # type: ignore[return-value]


__all__ = [
    "RouterAccessDenied",
    "admin_can_access_router",
    "assert_router_accessible",
]
