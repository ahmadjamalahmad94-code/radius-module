"""Sync queue repo — يدير الـ jobs لكل tenant."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso, parse_dt


def _row(r) -> dict:
    return {
        "id": r["id"], "tenant_id": r["tenant_id"], "router_id": r["router_id"],
        "kind": r["kind"], "entity_id": r["entity_id"], "entity_key": r["entity_key"] or "",
        "payload": json_load(r["payload_json"], default={}),
        "status": r["status"], "attempts": r["attempts"],
        "last_error": r["last_error"] or "", "last_router_id": r["last_router_id"],
        "next_attempt_at": r["next_attempt_at"],
        "completed_at": r["completed_at"],
        "created_at": r["created_at"],
    }


def enqueue(*, tenant_id: int, kind: str, entity_id: Optional[int] = None,
            entity_key: str = "", payload: Optional[dict] = None,
            router_id: Optional[int] = None) -> int:
    """يضع job جديد. يُرجع id الـ row."""
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO sync_queue(tenant_id, router_id, kind, entity_id, entity_key,
                payload_json, status, attempts, next_attempt_at, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (tenant_id, router_id, kind, entity_id, entity_key,
              json_dump(payload or {}), "queued", 0, now, now))
        return cur.lastrowid


def pick_due(limit: int = 10) -> list[dict]:
    """يأخذ jobs مستحقّة (queued أو retrying وحان وقتها)."""
    cur = db().execute("""
        SELECT * FROM sync_queue
        WHERE status IN ('queued','retrying') AND next_attempt_at <= ?
        ORDER BY id LIMIT ?
    """, (now_iso(), limit))
    return [_row(r) for r in cur.fetchall()]


def mark_syncing(job_id: int) -> None:
    with transaction() as conn:
        conn.execute("UPDATE sync_queue SET status='syncing' WHERE id = ?", (job_id,))


def mark_done(job_id: int) -> None:
    now = now_iso()
    with transaction() as conn:
        conn.execute("""
            UPDATE sync_queue
            SET status='done', completed_at=?, attempts = attempts + 1, last_error = ''
            WHERE id = ?
        """, (now, job_id))


def mark_failed(job_id: int, *, error: str, max_attempts: int = 6,
                last_router_id: Optional[int] = None) -> None:
    """يجدول إعادة محاولة بـ backoff تصاعدي. عند تجاوز max → failed terminal."""
    row = db().execute("SELECT attempts FROM sync_queue WHERE id = ?", (job_id,)).fetchone()
    attempts = (row["attempts"] if row else 0) + 1
    if attempts >= max_attempts:
        status = "failed"
        next_at = now_iso()
    else:
        status = "retrying"
        # backoff: 15s, 1m, 5m, 15m, 1h
        delays = (15, 60, 300, 900, 3600)
        delay_s = delays[min(attempts - 1, len(delays) - 1)]
        next_at = (datetime.utcnow() + timedelta(seconds=delay_s)).isoformat() + "Z"
    with transaction() as conn:
        conn.execute("""
            UPDATE sync_queue
            SET status=?, attempts=?, last_error=?, next_attempt_at=?, last_router_id=?
            WHERE id = ?
        """, (status, attempts, (error or "")[:500], next_at, last_router_id, job_id))


def list_jobs(tenant_id: int, *, status: Optional[str] = None,
              limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM sync_queue WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if status:
        sql += " AND status = ?"; vals.append(status)
    sql += " ORDER BY id DESC LIMIT ?"; vals.append(limit)
    return [_row(r) for r in db().execute(sql, vals).fetchall()]


def stats(tenant_id: int) -> dict:
    cur = db().execute("""
        SELECT status, COUNT(*) AS c FROM sync_queue WHERE tenant_id = ? GROUP BY status
    """, (tenant_id,))
    out = {"queued": 0, "syncing": 0, "retrying": 0, "done": 0, "failed": 0}
    for r in cur.fetchall():
        out[r["status"]] = r["c"]
    out["total"] = sum(out.values())
    return out


def mark_stale_resolved(tenant_id: Optional[int] = None) -> int:
    """تنظيف آمن: يحوّل صفوف الطابور العالقة (failed/retrying) إلى 'done'.

    خلفية: ``sync_queue`` طابور router-push قديم. بعد إسقاط جدول
    ``mikrotik_configs`` (migration 035) صار النظام مدعومًا بـRADIUS
    بالكامل، و``router_sync.execute_job`` بلا أثر فعليّ (``list_configs``
    تُعيد ``[]`` فالـjob يُعدّ منجزًا noop). صفوف ``failed``/``retrying``
    هي بقايا محاولات دفع قديمة — «أعطال» وهميّة لا قيمة تشغيليّة لها،
    تَملأ صفحة الطابور إزعاجًا للعميل.

    الإجراء حياديّ وidempotent: لا يُسقط الجدول (يبقى مستخدمًا:
    enqueue→worker→done noop)، ولا يلمس ``queued``/``syncing`` الجارية،
    وإعادة تشغيله بعد أوّل مرّة تُعيد 0 (لا صفوف عالقة متبقّية).

    ``tenant_id=None`` → كل الـtenants. يُرجع عدد الصفوف المُحدَّثة."""
    now = now_iso()
    sql = ("UPDATE sync_queue SET status='done', "
           "completed_at = COALESCE(NULLIF(completed_at, ''), ?), "
           "last_error = '' WHERE status IN ('failed', 'retrying')")
    vals: list = [now]
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        vals.append(int(tenant_id))
    with transaction() as conn:
        cur = conn.execute(sql, vals)
        return int(cur.rowcount or 0)
