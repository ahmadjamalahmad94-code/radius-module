"""router_backups_repo — S8 backup metadata + O7 reason label."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..connection import db, transaction


# O7 — allowed reason labels. Enforced at the repo layer; the
# DB column has no CHECK constraint to stay forward-compat.
BACKUP_REASON_MANUAL             = "manual"
BACKUP_REASON_SCHEDULED          = "scheduled"
BACKUP_REASON_BEFORE_DANGEROUS   = "before_dangerous"
BACKUP_REASON_BEFORE_PROGRAMMING = "before_programming"
BACKUP_REASON_BEFORE_RECOVERY    = "before_recovery"

ALLOWED_REASONS = frozenset({
    BACKUP_REASON_MANUAL,
    BACKUP_REASON_SCHEDULED,
    BACKUP_REASON_BEFORE_DANGEROUS,
    BACKUP_REASON_BEFORE_PROGRAMMING,
    BACKUP_REASON_BEFORE_RECOVERY,
})


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def record(
    *, tenant_id: int, router_id: int,
    backup_type: str, filename: str,
    storage_path: str = "",
    size_bytes: int = 0, checksum: str = "",
    sensitive: bool = True,
    notes: str = "",
    status: str = "success",
    error_message: str = "",
    created_by: Optional[int] = None,
    reason: str = BACKUP_REASON_MANUAL,
) -> int:
    if reason not in ALLOWED_REASONS:
        reason = BACKUP_REASON_MANUAL
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO router_backups
                (tenant_id, router_id, backup_type, filename,
                 storage_path, size_bytes, checksum, sensitive,
                 notes, status, error_message, created_by,
                 created_at, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(tenant_id), int(router_id), backup_type[:32],
             filename[:256], storage_path[:512],
             int(size_bytes), checksum[:128],
             1 if sensitive else 0,
             notes[:500], status[:32],
             (error_message or "")[:2000],
             created_by, _now(), reason),
        )
        return int(cur.lastrowid)


def get_by_id(tenant_id: int, backup_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM router_backups WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(backup_id)),
    ).fetchone()
    return dict(row) if row else None


def list_for_router(
    tenant_id: int, router_id: int, *, limit: int = 50,
) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM router_backups "
        "WHERE tenant_id=? AND router_id=? "
        "ORDER BY id DESC LIMIT ?",
        (int(tenant_id), int(router_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def list_for_tenant(
    tenant_id: int, *, limit: int = 200,
) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM router_backups WHERE tenant_id=? "
        "ORDER BY id DESC LIMIT ?",
        (int(tenant_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = ["record", "get_by_id", "list_for_router",
           "list_for_tenant"]
