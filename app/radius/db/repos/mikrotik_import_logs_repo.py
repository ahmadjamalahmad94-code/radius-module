"""mikrotik_import_logs — repo لسجلّ عمليات استيراد مستخدمي المايكروتيك
(feat/mikrotik-user-import، migration 124).

صفّ لكل عملية استيراد: NAS، النوع/المصدر، العدّادات، أخطاء كل اسم مستخدم،
المدير، والطابع الزمني. لا كلمات مرور خام. tenant-scoped.
"""
from __future__ import annotations

import json
from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso, row_to_dict


def create(
    *, tenant_id: int, nas_id: int, nas_name: str, import_type: str,
    source: str = "", transport: str = "", duplicate_mode: str = "skip_existing",
    total: int = 0, imported: int = 0, updated: int = 0, skipped: int = 0,
    failed: int = 0, errors: Optional[list] = None, status: str = "completed",
    message: str = "", started_by: int = 0, started_by_name: str = "",
    started_at: str = "", finished_at: str = "",
) -> int:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO mikrotik_import_logs
                (tenant_id, nas_id, nas_name, import_type, source, transport,
                 duplicate_mode, total_count, imported_count, updated_count,
                 skipped_count, failed_count, errors_json, status, message,
                 started_by, started_by_name, started_at, finished_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (int(tenant_id), int(nas_id), str(nas_name or ""), str(import_type),
             str(source or ""), str(transport or ""), str(duplicate_mode or "skip_existing"),
             int(total or 0), int(imported or 0), int(updated or 0), int(skipped or 0),
             int(failed or 0), json.dumps(errors or [], ensure_ascii=False),
             str(status or "completed"), str(message or "")[:500],
             int(started_by or 0), str(started_by_name or ""),
             str(started_at or now), str(finished_at or now)),
        )
        return int(cur.lastrowid)


def list_for_tenant(tenant_id: int, *, nas_id: Optional[int] = None,
                    limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM mikrotik_import_logs WHERE tenant_id = ?"
    vals: list = [int(tenant_id)]
    if nas_id is not None:
        sql += " AND nas_id = ?"
        vals.append(int(nas_id))
    sql += " ORDER BY id DESC LIMIT ?"
    vals.append(int(limit))
    rows = []
    for r in db().execute(sql, vals).fetchall():
        d = row_to_dict(r)
        try:
            d["errors"] = json.loads(d.pop("errors_json", "[]") or "[]")
        except (TypeError, ValueError):
            d["errors"] = []
        rows.append(d)
    return rows


def get(tenant_id: int, log_id: int) -> Optional[dict]:
    r = db().execute(
        "SELECT * FROM mikrotik_import_logs WHERE tenant_id = ? AND id = ?",
        (int(tenant_id), int(log_id)),
    ).fetchone()
    if not r:
        return None
    d = row_to_dict(r)
    try:
        d["errors"] = json.loads(d.pop("errors_json", "[]") or "[]")
    except (TypeError, ValueError):
        d["errors"] = []
    return d


__all__ = ["create", "list_for_tenant", "get"]
