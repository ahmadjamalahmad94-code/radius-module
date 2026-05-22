"""site_exit_deployments_repo — VX2 deployment state per policy.

A deployment is a "live state" row for one policy on one router.
It's created lazily on the first preview and mutates across the
lifecycle:

    draft → previewed → applied
                     ↘ failed
                     ↘ disabled

There is ONE deployment row per (policy, router). Reapplies and
re-previews update the same row — they don't insert new ones.
Script history (the actual command bodies) lives in the
separate `site_exit_script_versions` table.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..connection import db, transaction


STATUS_DRAFT     = "draft"
STATUS_PREVIEWED = "previewed"
STATUS_APPLIED   = "applied"
STATUS_FAILED    = "failed"
STATUS_DISABLED  = "disabled"

ALLOWED_STATUSES = frozenset({
    STATUS_DRAFT, STATUS_PREVIEWED, STATUS_APPLIED,
    STATUS_FAILED, STATUS_DISABLED,
})


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def get_for_policy(
    tenant_id: int, policy_id: int,
) -> Optional[dict]:
    """The (policy → deployment) relation is 1:1. This returns
    the row if it exists, else None."""
    row = db().execute(
        "SELECT * FROM site_exit_deployments "
        "WHERE tenant_id=? AND policy_id=? "
        "ORDER BY id DESC LIMIT 1",
        (int(tenant_id), int(policy_id)),
    ).fetchone()
    return dict(row) if row else None


def ensure_for_policy(
    *, tenant_id: int, policy_id: int, router_id: int,
) -> dict:
    """Returns the deployment row, creating a draft one if it
    doesn't exist yet. Idempotent — safe to call on every
    preview/apply request."""
    existing = get_for_policy(tenant_id, policy_id)
    if existing:
        return existing
    now = _now()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO site_exit_deployments
                (tenant_id, policy_id, router_id, status,
                 generated_script_hash, last_preview_at,
                 last_applied_at, last_error, last_audit_id,
                 created_at, updated_at)
            VALUES (?, ?, ?, 'draft', '', '', '', '', NULL, ?, ?)
            """,
            (int(tenant_id), int(policy_id), int(router_id),
             now, now),
        )
        new_id = int(cur.lastrowid)
    row = db().execute(
        "SELECT * FROM site_exit_deployments WHERE id=?",
        (new_id,),
    ).fetchone()
    return dict(row)


def record_preview(
    *, tenant_id: int, policy_id: int, router_id: int,
    script_hash: str,
) -> dict:
    """Move the deployment to `previewed` and stamp the hash +
    timestamp. Used by the preview flow before any wire I/O."""
    dep = ensure_for_policy(
        tenant_id=tenant_id, policy_id=policy_id,
        router_id=router_id)
    now = _now()
    with transaction() as c:
        c.execute(
            "UPDATE site_exit_deployments "
            "SET status='previewed', "
            "    generated_script_hash=?, "
            "    last_preview_at=?, updated_at=? "
            "WHERE id=?",
            (str(script_hash)[:128], now, now, int(dep["id"])),
        )
    return get_for_policy(tenant_id, policy_id) or dep


def record_apply_success(
    *, tenant_id: int, policy_id: int, router_id: int,
    script_hash: str, audit_id: Optional[int] = None,
) -> dict:
    dep = ensure_for_policy(
        tenant_id=tenant_id, policy_id=policy_id,
        router_id=router_id)
    now = _now()
    with transaction() as c:
        c.execute(
            "UPDATE site_exit_deployments "
            "SET status='applied', "
            "    generated_script_hash=?, "
            "    last_applied_at=?, last_error='', "
            "    last_audit_id=?, updated_at=? "
            "WHERE id=?",
            (str(script_hash)[:128], now,
             int(audit_id) if audit_id is not None else None,
             now, int(dep["id"])),
        )
    return get_for_policy(tenant_id, policy_id) or dep


def record_apply_failure(
    *, tenant_id: int, policy_id: int, router_id: int,
    error: str, audit_id: Optional[int] = None,
) -> dict:
    dep = ensure_for_policy(
        tenant_id=tenant_id, policy_id=policy_id,
        router_id=router_id)
    now = _now()
    with transaction() as c:
        c.execute(
            "UPDATE site_exit_deployments "
            "SET status='failed', last_error=?, "
            "    last_audit_id=?, updated_at=? "
            "WHERE id=?",
            (str(error)[:2000],
             int(audit_id) if audit_id is not None else None,
             now, int(dep["id"])),
        )
    return get_for_policy(tenant_id, policy_id) or dep


def set_status(
    *, tenant_id: int, policy_id: int, status: str,
) -> Optional[dict]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    now = _now()
    with transaction() as c:
        c.execute(
            "UPDATE site_exit_deployments "
            "SET status=?, updated_at=? "
            "WHERE tenant_id=? AND policy_id=?",
            (status, now, int(tenant_id), int(policy_id)),
        )
    return get_for_policy(tenant_id, policy_id)


def list_for_tenant(
    tenant_id: int, *, limit: int = 100,
) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM site_exit_deployments "
        "WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
        (int(tenant_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def list_for_router(
    tenant_id: int, router_id: int,
) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM site_exit_deployments "
        "WHERE tenant_id=? AND router_id=? ORDER BY id DESC",
        (int(tenant_id), int(router_id)),
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "STATUS_DRAFT", "STATUS_PREVIEWED", "STATUS_APPLIED",
    "STATUS_FAILED", "STATUS_DISABLED", "ALLOWED_STATUSES",
    "get_for_policy", "ensure_for_policy",
    "record_preview",
    "record_apply_success", "record_apply_failure",
    "set_status",
    "list_for_tenant", "list_for_router",
]
