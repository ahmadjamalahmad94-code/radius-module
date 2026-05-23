"""npc_web_block_repo — Website / App blocking policies + targets.

Owns two tables — the policy header (one per router) and the
individual target rows (domains / IPs / CIDRs to block). The
(policy_id, normalized_value) dedup index lets re-imports be
idempotent.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from ..connection import db, transaction
from .npc_common import now_iso, slugify


# ─── Policy header ───────────────────────────────────────────


SCOPE_ALL_USERS = "all_users"
ALLOWED_SCOPES = frozenset({SCOPE_ALL_USERS})

_ALLOWED_POLICY_UPDATE_FIELDS = frozenset({
    "name", "slug",
    "scope", "schedule_id",
    "fail_open", "enabled",
})

_POLICY_BOOL_FIELDS = frozenset({"fail_open", "enabled"})


def _pack_bool(v: Any) -> int:
    if v is True or v == 1 or v == "1":
        return 1
    return 0


def create_policy(
    *, tenant_id: int, router_id: int,
    name: str,
    slug: Optional[str] = None,
    scope: str = SCOPE_ALL_USERS,
    schedule_id: str = "",
    fail_open: bool = True,
    enabled: bool = True,
) -> int:
    if scope not in ALLOWED_SCOPES:
        raise ValueError(f"invalid scope: {scope!r}")
    final_slug = slugify(slug or name)
    if not final_slug:
        raise ValueError("web-block policy slug cannot be empty")
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO npc_web_block_policies
                (tenant_id, router_id, name, slug,
                 scope, schedule_id, fail_open, enabled,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id), int(router_id),
                name[:120], final_slug,
                scope, (schedule_id or "")[:80],
                _pack_bool(fail_open),
                _pack_bool(enabled),
                now, now,
            ),
        )
        return int(cur.lastrowid)


def get_policy(
    tenant_id: int, policy_id: int,
) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_web_block_policies "
        "WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(policy_id)),
    ).fetchone()
    return dict(row) if row else None


def get_policy_by_slug(
    tenant_id: int, slug: str,
) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_web_block_policies "
        "WHERE tenant_id=? AND slug=?",
        (int(tenant_id), str(slug)),
    ).fetchone()
    return dict(row) if row else None


def list_policies_for_router(
    tenant_id: int, router_id: int, *,
    only_enabled: bool = False,
) -> list[dict]:
    sql = ["SELECT * FROM npc_web_block_policies "
           "WHERE tenant_id=? AND router_id=?"]
    params: list[Any] = [int(tenant_id), int(router_id)]
    if only_enabled:
        sql.append("AND enabled=1")
    sql.append("ORDER BY id")
    rows = db().execute(" ".join(sql), tuple(params)).fetchall()
    return [dict(r) for r in rows]


def list_policies_for_tenant(tenant_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM npc_web_block_policies "
        "WHERE tenant_id=? ORDER BY id",
        (int(tenant_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def update_policy(
    tenant_id: int, policy_id: int, **changes,
) -> Optional[dict]:
    payload: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in _ALLOWED_POLICY_UPDATE_FIELDS:
            continue
        if k == "scope" and v not in ALLOWED_SCOPES:
            raise ValueError(f"invalid scope: {v!r}")
        if k in _POLICY_BOOL_FIELDS:
            payload[k] = _pack_bool(v)
        elif k == "slug":
            s = slugify(str(v))
            if not s:
                raise ValueError("slug cannot be empty")
            payload[k] = s
        else:
            payload[k] = (str(v) or "")[:120]
    if not payload:
        return get_policy(tenant_id, policy_id)
    fields = ", ".join(f"{k}=?" for k in payload.keys())
    params: list[Any] = list(payload.values())
    params.extend([now_iso(), int(tenant_id), int(policy_id)])
    with transaction() as c:
        c.execute(
            f"UPDATE npc_web_block_policies SET {fields}, "
            "updated_at=? WHERE tenant_id=? AND id=?",
            tuple(params),
        )
    return get_policy(tenant_id, policy_id)


def delete_policy(tenant_id: int, policy_id: int) -> bool:
    """Hard delete + cascade through targets, deployments and
    script versions. SQLite isn't FK-enforced across our tables
    so each cascade is an explicit DELETE."""
    with transaction() as c:
        c.execute(
            "DELETE FROM npc_web_block_targets WHERE policy_id=?",
            (int(policy_id),),
        )
        c.execute(
            "DELETE FROM npc_script_versions "
            "WHERE service='web_block' AND policy_id=?",
            (int(policy_id),),
        )
        c.execute(
            "DELETE FROM npc_deployments "
            "WHERE service='web_block' AND policy_id=?",
            (int(policy_id),),
        )
        cur = c.execute(
            "DELETE FROM npc_web_block_policies "
            "WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(policy_id)),
        )
        return cur.rowcount > 0


# ─── Targets ─────────────────────────────────────────────────


TARGET_TYPE_DOMAIN = "domain"
TARGET_TYPE_IP     = "ip"
TARGET_TYPE_CIDR   = "cidr"

ALLOWED_TARGET_TYPES = frozenset({
    TARGET_TYPE_DOMAIN, TARGET_TYPE_IP, TARGET_TYPE_CIDR,
})

STATUS_ACTIVE        = "active"
STATUS_DISABLED      = "disabled"
STATUS_INVALID       = "invalid"
STATUS_MANUAL_REVIEW = "manual_review"

ALLOWED_STATUSES = frozenset({
    STATUS_ACTIVE, STATUS_DISABLED,
    STATUS_INVALID, STATUS_MANUAL_REVIEW,
})


def add_target(
    *, policy_id: int, value: str,
    target_type: str,
    normalized_value: Optional[str] = None,
    category: str = "custom",
    status: str = STATUS_ACTIVE,
    notes: str = "",
) -> int:
    """Insert one target. ON CONFLICT (policy_id,
    normalized_value) updates in place so re-imports are
    idempotent — returns the existing row id in that case."""
    if target_type not in ALLOWED_TARGET_TYPES:
        raise ValueError(f"invalid target_type: {target_type!r}")
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    nv = (normalized_value or value or "").strip().lower()
    if not nv:
        raise ValueError("normalized_value cannot be empty")
    now = now_iso()
    with transaction() as c:
        c.execute(
            """
            INSERT INTO npc_web_block_targets
                (policy_id, category, target_type, value,
                 normalized_value, status, notes,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (policy_id, normalized_value)
            DO UPDATE SET
                category = excluded.category,
                target_type = excluded.target_type,
                value = excluded.value,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                int(policy_id),
                (category or "custom")[:60],
                target_type,
                value[:255], nv[:255],
                status, (notes or "")[:500],
                now, now,
            ),
        )
    # `cur.lastrowid` after INSERT…ON CONFLICT DO UPDATE is
    # unreliable in stdlib sqlite3 across CPython versions —
    # it can return a not-yet-assigned rowid on the conflict
    # path. Source-of-truth lookup by the dedup key is cheap
    # and always correct.
    row = db().execute(
        "SELECT id FROM npc_web_block_targets "
        "WHERE policy_id=? AND normalized_value=?",
        (int(policy_id), nv),
    ).fetchone()
    return int(row["id"]) if row else 0


def add_targets_many(
    policy_id: int, items: Iterable[dict],
) -> dict[str, int]:
    """Bulk add. Returns {inserted, updated, skipped}.

    `inserted` vs `updated` is decided by a probe query before
    each add — same pattern VX2 uses on `site_exit_targets`.
    """
    inserted = updated = skipped = 0
    for item in items:
        try:
            existed = bool(db().execute(
                "SELECT 1 FROM npc_web_block_targets "
                "WHERE policy_id=? AND normalized_value=?",
                (int(policy_id),
                 (item.get("normalized_value")
                  or item.get("value") or "").strip().lower()),
            ).fetchone())
        except Exception:  # noqa: BLE001
            existed = False
        try:
            add_target(policy_id=int(policy_id), **item)
            if existed:
                updated += 1
            else:
                inserted += 1
        except (ValueError, TypeError):
            skipped += 1
    return {"inserted": inserted, "updated": updated,
            "skipped": skipped}


def get_target(target_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_web_block_targets WHERE id=?",
        (int(target_id),),
    ).fetchone()
    return dict(row) if row else None


def list_targets(
    policy_id: int, *,
    category: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    sql = ["SELECT * FROM npc_web_block_targets WHERE policy_id=?"]
    params: list[Any] = [int(policy_id)]
    if category:
        sql.append("AND category=?")
        params.append(category)
    if status:
        sql.append("AND status=?")
        params.append(status)
    sql.append("ORDER BY id")
    rows = db().execute(" ".join(sql), tuple(params)).fetchall()
    return [dict(r) for r in rows]


def target_counts(policy_id: int) -> dict[str, int]:
    """Returns counts keyed by category, plus 'total'."""
    rows = db().execute(
        "SELECT category, COUNT(*) AS n "
        "FROM npc_web_block_targets WHERE policy_id=? "
        "GROUP BY category",
        (int(policy_id),),
    ).fetchall()
    out: dict[str, int] = {}
    total = 0
    for r in rows:
        out[r["category"]] = int(r["n"])
        total += int(r["n"])
    out["total"] = total
    return out


def set_target_status(target_id: int, status: str) -> bool:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    with transaction() as c:
        cur = c.execute(
            "UPDATE npc_web_block_targets "
            "SET status=?, updated_at=? WHERE id=?",
            (status, now_iso(), int(target_id)),
        )
        return cur.rowcount > 0


def delete_target(target_id: int) -> bool:
    with transaction() as c:
        cur = c.execute(
            "DELETE FROM npc_web_block_targets WHERE id=?",
            (int(target_id),),
        )
        return cur.rowcount > 0


def delete_targets_for_policy(policy_id: int) -> int:
    with transaction() as c:
        cur = c.execute(
            "DELETE FROM npc_web_block_targets WHERE policy_id=?",
            (int(policy_id),),
        )
        return int(cur.rowcount or 0)


__all__ = [
    "SCOPE_ALL_USERS", "ALLOWED_SCOPES",
    "TARGET_TYPE_DOMAIN", "TARGET_TYPE_IP", "TARGET_TYPE_CIDR",
    "ALLOWED_TARGET_TYPES",
    "STATUS_ACTIVE", "STATUS_DISABLED",
    "STATUS_INVALID", "STATUS_MANUAL_REVIEW",
    "ALLOWED_STATUSES",
    "create_policy", "get_policy", "get_policy_by_slug",
    "list_policies_for_router", "list_policies_for_tenant",
    "update_policy", "delete_policy",
    "add_target", "add_targets_many",
    "get_target", "list_targets", "target_counts",
    "set_target_status",
    "delete_target", "delete_targets_for_policy",
]
