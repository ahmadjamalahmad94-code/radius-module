"""router_loop_checks_repo — سجل فحوصات اللوب لكل راوتر.

صفّ لكل فحص (يدوي من صفحة الخدمة أو دوري من loop_probe_poller) مع
ملخّص النتيجة وتفاصيل كل منفذ JSON. يقصّ السجل تلقائيًا عند الإدراج
حتى لا ينمو بلا حدّ (آخر _KEEP_PER_ROUTER فحص لكل راوتر).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping, Sequence

from ..connection import db, transaction

_KEEP_PER_ROUTER = 300


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def insert_check(*, tenant_id: int, router_id: int, source: str = "manual",
                 ok: bool = True, error: str = "",
                 details: Sequence[Mapping[str, Any]] | None = None) -> int:
    """يسجّل فحصًا واحدًا. details = قائمة قواميس لكل منفذ:
    {"iface","status","is_loop","address","server"} — الملخّصات
    (ports_total/loops_found/rules_missing) تُشتق هنا فلا تنحرف عنها."""
    rows = [dict(d) for d in (details or [])]
    loops = sum(1 for d in rows if d.get("is_loop"))
    missing = sum(1 for d in rows if (d.get("status") or "") == "no-rule")
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO router_loop_checks(
                tenant_id, router_id, source, ok, error,
                ports_total, loops_found, rules_missing,
                details_json, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (int(tenant_id), int(router_id),
             str(source or "manual")[:20], 1 if ok else 0,
             str(error or "")[:500],
             len(rows), loops, missing,
             json.dumps(rows, ensure_ascii=False)[:20_000], _now()),
        )
        check_id = int(cur.lastrowid or 0)
        # قصّ السجل: نحتفظ بآخر N فحص لكل راوتر.
        conn.execute(
            """
            DELETE FROM router_loop_checks
            WHERE tenant_id=? AND router_id=? AND id NOT IN (
                SELECT id FROM router_loop_checks
                WHERE tenant_id=? AND router_id=?
                ORDER BY id DESC LIMIT ?)
            """,
            (int(tenant_id), int(router_id),
             int(tenant_id), int(router_id), _KEEP_PER_ROUTER),
        )
    return check_id


def list_for_router(tenant_id: int, router_id: int,
                    *, limit: int = 30) -> list[dict]:
    """آخر الفحوصات (الأحدث أولًا) مع details مفكوكة من JSON."""
    cur = db().execute(
        "SELECT * FROM router_loop_checks "
        "WHERE tenant_id=? AND router_id=? ORDER BY id DESC LIMIT ?",
        (int(tenant_id), int(router_id), max(1, min(int(limit), 200))),
    )
    out: list[dict] = []
    for r in cur.fetchall():
        row = dict(r)
        try:
            row["details"] = json.loads(row.get("details_json") or "[]")
        except ValueError:
            row["details"] = []
        out.append(row)
    return out


def last_check_at(tenant_id: int, router_id: int,
                  *, source: str = "") -> str:
    """وقت آخر فحص (اختياريًا لمصدر بعينه) — '' إن لم يوجد. يستخدمه
    الـpoller لاحترام فترة الفحص الدوري لكل راوتر."""
    sql = ("SELECT created_at FROM router_loop_checks "
           "WHERE tenant_id=? AND router_id=?")
    args: list[Any] = [int(tenant_id), int(router_id)]
    if source:
        sql += " AND source=?"
        args.append(str(source))
    sql += " ORDER BY id DESC LIMIT 1"
    row = db().execute(sql, args).fetchone()
    return str(dict(row).get("created_at") or "") if row else ""
