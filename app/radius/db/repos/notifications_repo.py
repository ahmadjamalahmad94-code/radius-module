"""مستودع الإشعارات الموحّد — قراءة/كتابة جدول notifications.

العمود الفقري لمركز الإشعارات: إشعارات محلّية (قرب انتهاء الاشتراك/الخدمة)
وإشعارات مدفوعة من لوحة التراخيص عبر الجسر. إزالة التكرار بـ dedup_key
(فهرس فريد جزئي)، وحالة القراءة عبر read_at. كل الدوال tenant-scoped.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso

# الأنواع/الشدّات المعتمدة (للتحقّق الناعم في الطبقة الأعلى).
TYPES = ("license", "subscription", "service", "support", "billing", "system")
SEVERITIES = ("info", "success", "warning", "critical")


def _row(r) -> dict:
    return {
        "id": int(r["id"]),
        "tenant_id": int(r["tenant_id"]),
        "type": r["type"] or "system",
        "severity": r["severity"] or "info",
        "title": r["title"] or "",
        "body": r["body"] or "",
        "link": r["link"] or "",
        "dedup_key": r["dedup_key"] or "",
        "source": r["source"] or "local",
        "source_ref": r["source_ref"] or "",
        "read_at": r["read_at"] or "",
        "created_at": r["created_at"] or "",
        "is_read": bool(r["read_at"]),
    }


def create(tenant_id: int, *, type: str = "system", severity: str = "info",
           title: str = "", body: str = "", link: str = "",
           dedup_key: str = "", source: str = "local",
           source_ref: str = "") -> Optional[int]:
    """يُدرج إشعارًا. عند وجود dedup_key مكرّر يُتجاهل الإدراج بهدوء
    (INSERT OR IGNORE على الفهرس الفريد الجزئي) ويُرجع id الموجود مسبقًا.
    يُرجع id الإشعار (الجديد أو القائم)، أو None لو فشل لأي سبب."""
    sev = severity if severity in SEVERITIES else "info"
    src = source if source in ("local", "bridge") else "local"
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO panel_notifications("
            " tenant_id, type, severity, title, body, link, dedup_key,"
            " source, source_ref, read_at, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,'',?)",
            (tenant_id, type or "system", sev, title, body, link,
             dedup_key or "", src, source_ref or "", now),
        )
        if cur.lastrowid and cur.rowcount:
            return int(cur.lastrowid)
    # كان مكرّرًا (تُجوهِل) — أعِد id الصفّ القائم بنفس المفتاح.
    if dedup_key:
        row = db().execute(
            "SELECT id FROM panel_notifications WHERE tenant_id=? AND dedup_key=?",
            (tenant_id, dedup_key)).fetchone()
        if row:
            return int(row["id"])
    return None


def list_for(tenant_id: int, *, unread_only: bool = False,
             limit: int = 100, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM panel_notifications WHERE tenant_id=?"
    vals: list = [tenant_id]
    if unread_only:
        sql += " AND read_at=''"
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [int(limit), int(offset)]
    return [_row(r) for r in db().execute(sql, vals).fetchall()]


def recent(tenant_id: int, limit: int = 6) -> list[dict]:
    """أحدث الإشعارات لقائمة الجرس المنسدلة."""
    return list_for(tenant_id, limit=max(1, int(limit)))


def unread_count(tenant_id: int) -> int:
    row = db().execute(
        "SELECT COUNT(*) AS c FROM panel_notifications WHERE tenant_id=? AND read_at=''",
        (tenant_id,)).fetchone()
    return int(row["c"] if row else 0)


def get(tenant_id: int, notif_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM panel_notifications WHERE tenant_id=? AND id=?",
        (tenant_id, int(notif_id))).fetchone()
    return _row(row) if row else None


def mark_read(tenant_id: int, notif_id: int) -> bool:
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE panel_notifications SET read_at=? "
            "WHERE tenant_id=? AND id=? AND read_at=''",
            (now_iso(), tenant_id, int(notif_id)))
        return bool(cur.rowcount)


def mark_all_read(tenant_id: int) -> int:
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE panel_notifications SET read_at=? WHERE tenant_id=? AND read_at=''",
            (now_iso(), tenant_id))
        return int(cur.rowcount or 0)


def has_unread_of_type(tenant_id: int, type: str, *,
                       source: Optional[str] = None) -> bool:
    """هل يوجد إشعار غير مقروء بنوعٍ معيّن (واختياريًّا مصدرٍ معيّن)؟
    تستعمله خدمة العدّ التنازلي لتفادي تكرار ما أرسلته لوحة التراخيص."""
    sql = ("SELECT 1 FROM panel_notifications WHERE tenant_id=? AND type=? "
           "AND read_at=''")
    vals: list = [tenant_id, type]
    if source:
        sql += " AND source=?"
        vals.append(source)
    sql += " LIMIT 1"
    return db().execute(sql, vals).fetchone() is not None
