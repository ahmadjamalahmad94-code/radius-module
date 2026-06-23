"""مستودع حالة الإشعارات الدوريّة للمراقبة (monitoring_notify_state).

يَخنق (throttle) التذكير الدوريّ للعناصر المفصولة + التقرير الدوريّ للأسطول،
بحفظ آخر إرسال لكل نطاق (scope) + بداية الانقطاع (للتذكير). كل الدوال
tenant-scoped وآمنة.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso


def _row(r) -> Optional[dict]:
    return {k: r[k] for k in r.keys()} if r is not None else None


def get(tenant_id: int, scope: str) -> Optional[dict]:
    r = db().execute(
        "SELECT * FROM monitoring_notify_state WHERE tenant_id=? AND scope=?",
        (int(tenant_id), str(scope))).fetchone()
    return _row(r)


def upsert(tenant_id: int, scope: str, *, down_since: Optional[str] = None,
           last_sent_at: Optional[str] = None) -> None:
    """يُنشئ/يُحدّث صفّ النطاق. القيم None تُبقي الموجود (COALESCE)."""
    with transaction() as conn:
        conn.execute(
            "INSERT INTO monitoring_notify_state("
            " tenant_id, scope, down_since, last_sent_at, updated_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(tenant_id, scope) DO UPDATE SET "
            " down_since=COALESCE(?, monitoring_notify_state.down_since), "
            " last_sent_at=COALESCE(?, monitoring_notify_state.last_sent_at), "
            " updated_at=excluded.updated_at",
            (int(tenant_id), str(scope), down_since or "", last_sent_at or "",
             now_iso(), down_since, last_sent_at))


def delete(tenant_id: int, scope: str) -> None:
    with transaction() as conn:
        conn.execute(
            "DELETE FROM monitoring_notify_state WHERE tenant_id=? AND scope=?",
            (int(tenant_id), str(scope)))


def list_reminder_scopes(tenant_id: int) -> list[str]:
    rows = db().execute(
        "SELECT scope FROM monitoring_notify_state "
        "WHERE tenant_id=? AND scope LIKE 'reminder:%'",
        (int(tenant_id),)).fetchall()
    return [r["scope"] for r in rows]


def clear_recovered(tenant_id: int, active_scopes: set) -> int:
    """يَحذف صفوف التذكير لعناصر لم تَعُد مفصولة (تعافت) — إعادة ضبط الحلقة كي
    يَبدأ انقطاعٌ لاحق من جديد. يُرجع عدد المحذوف."""
    removed = 0
    for scope in list_reminder_scopes(int(tenant_id)):
        if scope not in active_scopes:
            delete(int(tenant_id), scope)
            removed += 1
    return removed
