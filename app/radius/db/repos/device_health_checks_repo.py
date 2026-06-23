"""device_health_checks_repo — سجل فحوصات «تتبع حالة الأجهزة».

صفّ لكل دورة فحص (يدوية من زر «فحص الكل» أو دورية من
device_health_poll_worker) بملخّصها (متصل/مفصول/بنج عالٍ/…) وتفاصيل كل
جهاز JSON. يقصّ السجل تلقائيًا عند الإدراج (آخر _KEEP_PER_TENANT فحص).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from ..connection import db, transaction

_KEEP_PER_TENANT = 500


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def insert_check(*, tenant_id: int, source: str = "manual", ok: bool = True,
                 error: str = "", summary: Mapping[str, Any] | None = None,
                 duration_ms: int = 0,
                 details: Sequence[Mapping[str, Any]] | None = None) -> int:
    """يسجّل دورة فحص واحدة. summary = ناتج poller.tick
    (scanned/up/down/high_latency/unknown/changed/alerts)."""
    s = dict(summary or {})
    rows = [dict(d) for d in (details or [])]
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO network_device_health_checks(
                tenant_id, source, ok, error, scanned, up_count, down_count,
                high_latency, unknown_count, changed, alerts, duration_ms,
                details_json, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (int(tenant_id), str(source or "manual")[:20], 1 if ok else 0,
             str(error or "")[:500],
             int(s.get("scanned") or 0), int(s.get("up") or 0),
             # «unavailable» (خلف راوتر مفصول) مشكلة اتصال ⇒ يُطوى في عمود
             # «المفصول» للسجل (لا عمود مستقلّ)، فلا تَظهر الدورة «سليمة».
             int(s.get("down") or 0) + int(s.get("unavailable") or 0),
             int(s.get("high_latency") or 0),
             int(s.get("unknown") or 0), int(s.get("changed") or 0),
             int(s.get("alerts") or 0), int(duration_ms or 0),
             json.dumps(rows, ensure_ascii=False)[:40_000], _now()),
        )
        check_id = int(cur.lastrowid or 0)
        conn.execute(
            """
            DELETE FROM network_device_health_checks
            WHERE tenant_id=? AND id NOT IN (
                SELECT id FROM network_device_health_checks
                WHERE tenant_id=? ORDER BY id DESC LIMIT ?)
            """,
            (int(tenant_id), int(tenant_id), _KEEP_PER_TENANT),
        )
    return check_id


def list_checks(tenant_id: int, *, limit: int = 30) -> list[dict]:
    """آخر الفحوصات (الأحدث أولًا) مع details مفكوكة من JSON."""
    cur = db().execute(
        "SELECT * FROM network_device_health_checks "
        "WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
        (int(tenant_id), max(1, min(int(limit), 200))),
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


def stats(tenant_id: int, *, hours: int = 24) -> dict:
    """إحصائيات سريعة لشريط الصفحة: فحوصات آخر N ساعة، أعطال اكتُشفت،
    تغييرات حالة، آخر فحص (وقته ومصدره)."""
    since = (datetime.utcnow() - timedelta(hours=max(1, hours))).isoformat() + "Z"
    row = db().execute(
        """
        SELECT COUNT(*)                    AS checks,
               COALESCE(SUM(down_count), 0)  AS downs,
               COALESCE(SUM(changed), 0)     AS changes,
               COALESCE(SUM(alerts), 0)      AS alerts
        FROM network_device_health_checks
        WHERE tenant_id=? AND created_at >= ?
        """,
        (int(tenant_id), since),
    ).fetchone()
    last = db().execute(
        "SELECT created_at, source, ok FROM network_device_health_checks "
        "WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
        (int(tenant_id),),
    ).fetchone()
    out = dict(row) if row else {"checks": 0, "downs": 0, "changes": 0, "alerts": 0}
    out["last_at"] = str(dict(last).get("created_at") or "") if last else ""
    out["last_source"] = str(dict(last).get("source") or "") if last else ""
    out["last_ok"] = bool(dict(last).get("ok")) if last else True
    out["window_hours"] = hours
    return out


def last_check_at(tenant_id: int, *, source: str = "") -> str:
    """وقت آخر فحص (اختياريًا لمصدر بعينه) — '' إن لم يوجد. يستخدمه
    worker الفحص الدوري لاحترام فترة كل مستأجر."""
    sql = ("SELECT created_at FROM network_device_health_checks "
           "WHERE tenant_id=?")
    args: list[Any] = [int(tenant_id)]
    if source:
        sql += " AND source=?"
        args.append(str(source))
    sql += " ORDER BY id DESC LIMIT 1"
    row = db().execute(sql, args).fetchone()
    return str(dict(row).get("created_at") or "") if row else ""
