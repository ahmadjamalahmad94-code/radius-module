"""MT32 — شات المزوّد ↔ الشبكة.

خيطٌ واحد لكل شبكة: طرفاه ``provider`` (لوحة المزوّد) و``network`` (لوحة
الشبكة). رسائل نصّية خالصة، بلا مال ولا مرفقات — التحديث بـpolling خفيف
عبر ``after_id``.

**العزل**: كل دالة تأخذ ``tenant_id`` صراحةً وتُقيّد به كل استعلام. لا
توجد دالة تقرأ «كل الرسائل» إلا ``unread_by_tenant`` (للمزوّد وحده،
وتُرجع أعدادًا لا محتوى).
"""
from __future__ import annotations

from typing import Any, Optional

from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict

__all__ = ["ProviderChatError", "post_message", "list_messages", "mark_read",
           "unread_count", "unread_by_tenant", "last_activity_by_tenant"]

SENDERS = ("provider", "network")
_MAX_BODY = 4000
_MAX_LIMIT = 300


class ProviderChatError(ValueError):
    """خطأ تحقّق آمن (رسالة عربية تُعرَض للمستخدم)."""


def post_message(*, tenant_id: int, sender: str, body: str,
                 sender_name: str = "") -> dict[str, Any]:
    """يُضيف رسالة للخيط. الطرف المُرسِل يقرأ رسالته تلقائيًّا."""
    if sender not in SENDERS:
        raise ProviderChatError("طرف غير معروف.")
    text = (body or "").strip()
    if not text:
        raise ProviderChatError("لا يمكن إرسال رسالة فارغة.")
    text = text[:_MAX_BODY]
    tid = int(tenant_id)
    if tid <= 0:
        raise ProviderChatError("الشبكة غير محدّدة.")
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO provider_chat_messages"
            " (tenant_id, sender, sender_name, body, created_at,"
            "  read_by_provider, read_by_network) VALUES (?,?,?,?,?,?,?)",
            (tid, sender, (sender_name or "")[:120], text, now_iso(),
             1 if sender == "provider" else 0,
             1 if sender == "network" else 0))
        mid = int(cur.lastrowid)
    row = db().execute(
        "SELECT * FROM provider_chat_messages WHERE id=? AND tenant_id=?",
        (mid, tid)).fetchone()
    return row_to_dict(row)


def list_messages(*, tenant_id: int, after_id: int = 0,
                  limit: int = 100) -> list[dict[str, Any]]:
    """رسائل خيط شبكةٍ واحدة، تصاعديًّا. ``after_id`` للـpolling."""
    tid = int(tenant_id)
    lim = max(1, min(int(limit or 100), _MAX_LIMIT))
    rows = db().execute(
        "SELECT * FROM provider_chat_messages WHERE tenant_id=? AND id>? "
        "ORDER BY id ASC LIMIT ?", (tid, int(after_id or 0), lim)).fetchall()
    return [row_to_dict(r) for r in rows]


def mark_read(*, tenant_id: int, side: str) -> int:
    """يُعلّم رسائل الطرف الآخر مقروءةً لهذا الطرف. يُرجع عدد ما تغيّر."""
    if side not in SENDERS:
        raise ProviderChatError("طرف غير معروف.")
    col = "read_by_provider" if side == "provider" else "read_by_network"
    with transaction() as conn:
        cur = conn.execute(
            f"UPDATE provider_chat_messages SET {col}=1 "
            f"WHERE tenant_id=? AND {col}=0", (int(tenant_id),))
        return int(cur.rowcount or 0)


def unread_count(*, tenant_id: int, side: str) -> int:
    col = "read_by_provider" if side == "provider" else "read_by_network"
    row = db().execute(
        f"SELECT COUNT(*) AS n FROM provider_chat_messages "
        f"WHERE tenant_id=? AND {col}=0", (int(tenant_id),)).fetchone()
    return int(row["n"] if row else 0)


def unread_by_tenant() -> dict[int, int]:
    """للمزوّد وحده: عدد غير المقروء لكل شبكة (أعداد لا محتوى)."""
    out: dict[int, int] = {}
    for r in db().execute(
            "SELECT tenant_id, COUNT(*) AS n FROM provider_chat_messages "
            "WHERE read_by_provider=0 GROUP BY tenant_id"):
        out[int(r["tenant_id"])] = int(r["n"])
    return out


def last_activity_by_tenant() -> dict[int, str]:
    """للمزوّد: آخر وقت رسالة لكل شبكة (لترتيب القائمة)."""
    out: dict[int, str] = {}
    for r in db().execute(
            "SELECT tenant_id, MAX(created_at) AS t FROM provider_chat_messages "
            "GROUP BY tenant_id"):
        out[int(r["tenant_id"])] = str(r["t"] or "")
    return out


def thread_summary(tenant_id: int) -> dict[str, Any]:
    """ملخّص خيط شبكة: العدد الكليّ وآخر رسالة."""
    tid = int(tenant_id)
    total = db().execute(
        "SELECT COUNT(*) AS n FROM provider_chat_messages WHERE tenant_id=?",
        (tid,)).fetchone()
    last: Optional[Any] = db().execute(
        "SELECT * FROM provider_chat_messages WHERE tenant_id=? "
        "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    return {"total": int(total["n"] if total else 0),
            "last": row_to_dict(last) if last else None}
