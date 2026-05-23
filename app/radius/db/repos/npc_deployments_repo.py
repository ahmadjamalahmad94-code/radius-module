"""npc_deployments_repo — shared deployment state across the
three NPC sub-services.

One row per (service, policy). Lifecycle:

    draft → previewed → applied
                     ↘ failed
                     ↘ disabled

Reapplies and re-previews mutate the same row — they don't
insert new ones. Script bodies live on `npc_script_versions`
where each preview append-only writes a row.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from .npc_common import (
    ALLOWED_SERVICES, assert_service, now_iso,
)


STATUS_DRAFT     = "draft"
STATUS_PREVIEWED = "previewed"
STATUS_APPLIED   = "applied"
STATUS_FAILED    = "failed"
STATUS_DISABLED  = "disabled"

ALLOWED_STATUSES = frozenset({
    STATUS_DRAFT, STATUS_PREVIEWED, STATUS_APPLIED,
    STATUS_FAILED, STATUS_DISABLED,
})


def get_for_policy(
    *, tenant_id: int, service: str, policy_id: int,
) -> Optional[dict]:
    """Returns the deployment row for (service, policy). Each
    policy has at most one — we ORDER BY id DESC LIMIT 1 to be
    forgiving of any historic duplication, but the
    `ensure_for_policy` path keeps a single row going forward.
    """
    assert_service(service)
    row = db().execute(
        "SELECT * FROM npc_deployments "
        "WHERE tenant_id=? AND service=? AND policy_id=? "
        "ORDER BY id DESC LIMIT 1",
        (int(tenant_id), service, int(policy_id)),
    ).fetchone()
    return dict(row) if row else None


def ensure_for_policy(
    *, tenant_id: int, service: str,
    policy_id: int, router_id: int,
) -> dict:
    assert_service(service)
    existing = get_for_policy(
        tenant_id=tenant_id, service=service,
        policy_id=policy_id,
    )
    if existing:
        return existing
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO npc_deployments
                (tenant_id, service, policy_id, router_id,
                 status, generated_script_hash,
                 last_preview_at, last_applied_at,
                 last_error, last_audit_id,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, 'draft', '', '', '', '', NULL, ?, ?)
            """,
            (
                int(tenant_id), service,
                int(policy_id), int(router_id),
                now, now,
            ),
        )
        new_id = int(cur.lastrowid)
    row = db().execute(
        "SELECT * FROM npc_deployments WHERE id=?",
        (new_id,),
    ).fetchone()
    return dict(row)


def record_preview(
    *, tenant_id: int, service: str,
    policy_id: int, router_id: int,
    script_hash: str,
) -> dict:
    assert_service(service)
    dep = ensure_for_policy(
        tenant_id=tenant_id, service=service,
        policy_id=policy_id, router_id=router_id,
    )
    now = now_iso()
    with transaction() as c:
        c.execute(
            "UPDATE npc_deployments "
            "SET status='previewed', "
            "    generated_script_hash=?, "
            "    last_preview_at=?, updated_at=? "
            "WHERE id=?",
            (str(script_hash)[:128], now, now, int(dep["id"])),
        )
    return get_for_policy(
        tenant_id=tenant_id, service=service,
        policy_id=policy_id,
    ) or dep


def record_apply_success(
    *, tenant_id: int, service: str,
    policy_id: int, router_id: int,
    script_hash: str, audit_id: Optional[int] = None,
) -> dict:
    assert_service(service)
    dep = ensure_for_policy(
        tenant_id=tenant_id, service=service,
        policy_id=policy_id, router_id=router_id,
    )
    now = now_iso()
    with transaction() as c:
        c.execute(
            "UPDATE npc_deployments "
            "SET status='applied', "
            "    generated_script_hash=?, "
            "    last_applied_at=?, last_error='', "
            "    last_audit_id=?, updated_at=? "
            "WHERE id=?",
            (str(script_hash)[:128], now,
             int(audit_id) if audit_id is not None else None,
             now, int(dep["id"])),
        )
    return get_for_policy(
        tenant_id=tenant_id, service=service,
        policy_id=policy_id,
    ) or dep


def record_apply_failure(
    *, tenant_id: int, service: str,
    policy_id: int, router_id: int,
    error: str, audit_id: Optional[int] = None,
) -> dict:
    assert_service(service)
    dep = ensure_for_policy(
        tenant_id=tenant_id, service=service,
        policy_id=policy_id, router_id=router_id,
    )
    now = now_iso()
    with transaction() as c:
        c.execute(
            "UPDATE npc_deployments "
            "SET status='failed', last_error=?, "
            "    last_audit_id=?, updated_at=? "
            "WHERE id=?",
            (str(error)[:2000],
             int(audit_id) if audit_id is not None else None,
             now, int(dep["id"])),
        )
    return get_for_policy(
        tenant_id=tenant_id, service=service,
        policy_id=policy_id,
    ) or dep


def set_status(
    *, tenant_id: int, service: str,
    policy_id: int, status: str,
) -> Optional[dict]:
    assert_service(service)
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    now = now_iso()
    with transaction() as c:
        c.execute(
            "UPDATE npc_deployments "
            "SET status=?, updated_at=? "
            "WHERE tenant_id=? AND service=? AND policy_id=?",
            (status, now, int(tenant_id), service,
             int(policy_id)),
        )
    return get_for_policy(
        tenant_id=tenant_id, service=service,
        policy_id=policy_id,
    )


def list_for_tenant(
    tenant_id: int, *,
    service: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Listing — optionally filter by service. Newest first."""
    if service is not None:
        assert_service(service)
        rows = db().execute(
            "SELECT * FROM npc_deployments "
            "WHERE tenant_id=? AND service=? "
            "ORDER BY id DESC LIMIT ?",
            (int(tenant_id), service, int(limit)),
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM npc_deployments "
            "WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
            (int(tenant_id), int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def list_for_router(
    *, tenant_id: int, router_id: int,
    service: Optional[str] = None,
) -> list[dict]:
    if service is not None:
        assert_service(service)
        rows = db().execute(
            "SELECT * FROM npc_deployments "
            "WHERE tenant_id=? AND router_id=? AND service=? "
            "ORDER BY id DESC",
            (int(tenant_id), int(router_id), service),
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM npc_deployments "
            "WHERE tenant_id=? AND router_id=? ORDER BY id DESC",
            (int(tenant_id), int(router_id)),
        ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "STATUS_DRAFT", "STATUS_PREVIEWED", "STATUS_APPLIED",
    "STATUS_FAILED", "STATUS_DISABLED", "ALLOWED_STATUSES",
    "ALLOWED_SERVICES",
    "get_for_policy", "ensure_for_policy",
    "record_preview",
    "record_apply_success", "record_apply_failure",
    "set_status",
    "list_for_tenant", "list_for_router",
]
