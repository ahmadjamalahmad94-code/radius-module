"""مستودع رسائل المشغّل ← لوحة التراخيص (تذاكر/شكاوى صادرة).

سجلّ محلّي لما يُرسله المشغّل للمزوّد مع حالة التسليم عبر الجسر. منفصل عن
جدول tickets (المرتبط بمشترك) لأن جمهوره المزوّد لا المشترك.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso


def _row(r) -> dict:
    return {
        "id": int(r["id"]),
        "tenant_id": int(r["tenant_id"]),
        "kind": r["kind"] or "ticket",
        "subject": r["subject"] or "",
        "body": r["body"] or "",
        "category": r["category"] or "general",
        "priority": r["priority"] or "normal",
        "bridge_status": r["bridge_status"] or "pending",
        "bridge_ref": r["bridge_ref"] or "",
        "created_by": r["created_by"] or "",
        "created_at": r["created_at"] or "",
    }


def create(tenant_id: int, *, kind: str = "ticket", subject: str = "",
           body: str = "", category: str = "general", priority: str = "normal",
           created_by: str = "") -> int:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO provider_messages("
            " tenant_id, kind, subject, body, category, priority,"
            " bridge_status, bridge_ref, created_by, created_at)"
            " VALUES(?,?,?,?,?,?, 'pending', '', ?, ?)",
            (tenant_id, kind, subject, body, category, priority, created_by, now),
        )
        return int(cur.lastrowid)


def set_bridge_status(tenant_id: int, msg_id: int, *, status: str,
                      ref: str = "") -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE provider_messages SET bridge_status=?, bridge_ref=? "
            "WHERE tenant_id=? AND id=?",
            (status, ref or "", tenant_id, int(msg_id)))


def list_for(tenant_id: int, *, limit: int = 100) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM provider_messages WHERE tenant_id=? "
        "ORDER BY id DESC LIMIT ?", (tenant_id, int(limit))).fetchall()
    return [_row(r) for r in rows]


def get(tenant_id: int, msg_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM provider_messages WHERE tenant_id=? AND id=?",
        (tenant_id, int(msg_id))).fetchone()
    return _row(row) if row else None
