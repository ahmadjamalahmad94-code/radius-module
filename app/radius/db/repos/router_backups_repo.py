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
    # Migration 078 additions — file content + manifest snapshot
    # so we can show the operator what was configured at backup
    # time and restore from HobeRadius in an emergency.
    file_blob: Optional[bytes] = None,
    router_filename: str = "",
    manifest_json: str = "{}",
    manifest_summary: str = "",
    router_status: str = "on_router",
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
                 created_at, reason,
                 file_blob, router_filename,
                 manifest_json, manifest_summary, router_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?)
            """,
            (int(tenant_id), int(router_id), backup_type[:32],
             filename[:256], storage_path[:512],
             int(size_bytes), checksum[:128],
             1 if sensitive else 0,
             notes[:500], status[:32],
             (error_message or "")[:2000],
             created_by, _now(), reason,
             file_blob,
             (router_filename or filename)[:256],
             (manifest_json or "{}")[:200_000],
             (manifest_summary or "")[:500],
             (router_status or "on_router")[:32]),
        )
        return int(cur.lastrowid)


def get_blob(tenant_id: int, backup_id: int) -> Optional[bytes]:
    """Return the binary .backup contents stored at `record()` time.
    None if the row holds metadata only (file lives on the router)."""
    row = db().execute(
        "SELECT file_blob FROM router_backups "
        " WHERE id=? AND tenant_id=?",
        (int(backup_id), int(tenant_id)),
    ).fetchone()
    if not row or row["file_blob"] is None:
        return None
    return bytes(row["file_blob"])


def mark_restored(tenant_id: int, backup_id: int,
                  *, by: str = "", notes: str = "") -> bool:
    """Stamp a successful restore. Used by the
    POST /backups/<id>/restore endpoint after the .backup file
    has been pushed back to the router and loaded."""
    with transaction() as c:
        cur = c.execute(
            "UPDATE router_backups "
            "   SET restored_at=?, restored_by=?, "
            "       notes = CASE WHEN ?='' THEN notes ELSE ? END "
            " WHERE id=? AND tenant_id=?",
            (_now(), str(by)[:64],
             str(notes), str(notes)[:500],
             int(backup_id), int(tenant_id)),
        )
        return cur.rowcount > 0


def delete(tenant_id: int, backup_id: int) -> bool:
    """Hard delete a backup row (DB + blob). The on-router file is
    untouched — operator removes that via Winbox / file action."""
    with transaction() as c:
        cur = c.execute(
            "DELETE FROM router_backups WHERE id=? AND tenant_id=?",
            (int(backup_id), int(tenant_id)),
        )
        return cur.rowcount > 0


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
           "list_for_tenant",
           "get_blob", "mark_restored", "delete"]
