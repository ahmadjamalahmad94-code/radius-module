"""Tickets + replies repo."""
from __future__ import annotations

from typing import Optional

from ...core.types_saas import Ticket, TicketReply
from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso, parse_dt


def _t_row(r) -> Ticket:
    return Ticket(
        id=r["id"], tenant_id=r["tenant_id"], subscriber_id=r["subscriber_id"],
        subject=r["subject"], category=r["category"], priority=r["priority"],
        status=r["status"], assignee_admin_id=r["assignee_admin_id"],
        body=r["body"] or "",
        attachments=tuple(json_load(r["attachments_json"], default=[])),
        created_at=parse_dt(r["created_at"]),
        updated_at=parse_dt(r["updated_at"]),
        closed_at=parse_dt(r["closed_at"]),
    )


def list_tickets(tenant_id: int, *, status: Optional[str] = None,
                  subscriber_id: Optional[int] = None,
                  category: Optional[str] = None,
                  limit: int = 200, offset: int = 0) -> list[Ticket]:
    sql = "SELECT * FROM tickets WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if status:
        sql += " AND status = ?"; vals.append(status)
    if subscriber_id is not None:
        sql += " AND subscriber_id = ?"; vals.append(subscriber_id)
    if category:
        sql += " AND category = ?"; vals.append(category)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    return [_t_row(r) for r in db().execute(sql, vals).fetchall()]


def get_ticket(tenant_id: int, tid: int) -> Optional[Ticket]:
    row = db().execute(
        "SELECT * FROM tickets WHERE tenant_id = ? AND id = ?",
        (tenant_id, tid)).fetchone()
    return _t_row(row) if row else None


def create_ticket(t: Ticket) -> Ticket:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO tickets(tenant_id, subscriber_id, subject, category, priority, status,
                assignee_admin_id, body, attachments_json, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (t.tenant_id, t.subscriber_id, t.subject, t.category, t.priority, t.status,
              t.assignee_admin_id, t.body, json_dump(list(t.attachments)), now, now))
        new_id = cur.lastrowid
    return get_ticket(t.tenant_id, new_id)


def update_ticket(tenant_id: int, tid: int, **changes) -> Optional[Ticket]:
    allowed = ("subject", "category", "priority", "status", "assignee_admin_id", "body")
    sets, vals = [], []
    for k, v in changes.items():
        if k in allowed:
            sets.append(f"{k} = ?"); vals.append(v)
    if not sets:
        return get_ticket(tenant_id, tid)
    sets.append("updated_at = ?"); vals.append(now_iso())
    if "status" in changes:
        # closed_at يتبع الحالة: يُختم عند الدخول إلى «مغلقة» (ويبقى ختمُه
        # الأوّل إن كانت مغلقة أصلًا)، ويُمسح عند إعادة الفتح — كانت إعادة
        # الفتح تُبقي closed_at فتبدو التذكرة المفتوحة «مغلقة في …».
        if changes.get("status") == "closed":
            sets.append("closed_at = CASE WHEN status = 'closed' AND closed_at IS NOT NULL "
                        "THEN closed_at ELSE ? END"); vals.append(now_iso())
        else:
            sets.append("closed_at = NULL")
    vals += [tenant_id, tid]
    with transaction() as conn:
        conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE tenant_id = ? AND id = ?", vals)
    return get_ticket(tenant_id, tid)


# «طلب الخدمة» (category='service_request') حالتُه تتبع آلة قرارات الإدارة
# (/service-requests/<id>/decision). المسار العامّ لتغيير الحالة (PATCH /tickets،
# «حفظ الحالة» في الويب) كان يعيد فتح طلبٍ مرفوض ثمّ يُوافَق عليه. القاعدة:
#   • مغلقة/محلولة نهائيّة — لا تغيير حالة بعدها.
#   • الإغلاق اليدويّ (→ مغلقة/محلولة) مسموح وهو نهائيّ.
#   • أيّ انتقال آخر (فتح/قيد التنفيذ/معلّقة) يمرّ عبر «قرار الإدارة» فقط.
SERVICE_REQUEST_CATEGORY = "service_request"
SERVICE_REQUEST_TERMINAL = ("closed", "resolved")

# zero-w3: the categories the GENERIC ticket form offers (web modal, web form,
# app) — one list, one set of labels. «طلب خدمة» is NOT here: a service request
# carries its own data (kind/plan/amount…) and is created only through the
# service-request flow (/service-requests). Created from the generic form it
# was a request without its data — the decision worked, but status edits 409'd.
TICKET_CREATE_CATEGORIES: dict[str, str] = {
    "general": "عام",
    "billing": "الفواتير والدفع",
    "connection": "الاتصال والخدمة",
    "hardware": "الأجهزة والمعدّات",
    "complaint": "شكوى",
}


def generic_create_category_error(category: str) -> Optional[str]:
    """رسالة رفضٍ عربيّة إن كان التصنيف ممنوعًا في الإنشاء العامّ، وإلّا None."""
    if str(category or "").strip().lower() == SERVICE_REQUEST_CATEGORY:
        return ("«طلب خدمة» لا يُنشأ من نموذج التذكرة العامّ — استخدم «طلب خدمة» "
                "من صفحة طلبات الخدمات حتى تُحفظ بياناته.")
    return None


def service_request_status_error(ticket: Ticket, new_status: str) -> Optional[str]:
    """رسالة رفضٍ عربيّة (→ 409) إن كان تغيير الحالة العامّ ممنوعًا، وإلّا None."""
    if (ticket.category or "") != SERVICE_REQUEST_CATEGORY or new_status == ticket.status:
        return None
    if ticket.status in SERVICE_REQUEST_TERMINAL:
        return "تم البتّ في هذا الطلب وإغلاقه مسبقًا — لا يمكن تغيير حالته."
    if new_status in SERVICE_REQUEST_TERMINAL:
        return None
    return ("حالة طلب الخدمة تتغيّر عبر «قرار الإدارة» فقط "
            "(موافقة / رفض / فتح تجريبي / طلب دفع).")


def change_status(tenant_id: int, ticket: Ticket, new_status: str) -> Optional[str]:
    """تغيير حالة تذكرة من المسار العامّ (API + ويب). يُعيد رسالة رفضٍ عربيّة
    (409) أو None عند النجاح. طلب الخدمة: الحارس أعلاه + انتقال ذرّيّ مشروط
    (compare-and-set) كي لا يتسابق مع قرارٍ متزامن."""
    error = service_request_status_error(ticket, new_status)
    if error:
        return error
    if (ticket.category or "") != SERVICE_REQUEST_CATEGORY:
        update_ticket(tenant_id, int(ticket.id), status=new_status)
        return None
    if new_status == ticket.status:
        return None
    version = ticket_version(tenant_id, int(ticket.id))
    if (version is None or version[0] != ticket.status
            or not compare_and_set_status(tenant_id, int(ticket.id),
                                          expected_status=version[0],
                                          expected_version=version[1],
                                          new_status=new_status)):
        return "تغيّرت حالة الطلب للتوّ — حدّث الصفحة وراجع حالته."
    return None


def ticket_version(tenant_id: int, tid: int) -> Optional[tuple[str, str]]:
    """(status, updated_at الخام) — بصمة الحالة لقرارات compare-and-set."""
    row = db().execute(
        "SELECT status, COALESCE(updated_at, '') AS v FROM tickets "
        "WHERE tenant_id = ? AND id = ?", (tenant_id, tid)).fetchone()
    return (row["status"], row["v"]) if row else None


def compare_and_set_status(tenant_id: int, tid: int, *, expected_status: str,
                           expected_version: str, new_status: str) -> bool:
    """انتقال حالة ذرّيّ مشروط: ينجح فقط إن لم تتغيّر التذكرة منذ قُرئت
    (نفس الحالة ونفس updated_at). من ثمانية قرارات متوازية على الطلب نفسه
    يفوز واحد فقط؛ البقيّة تُرجع False (→ 409)."""
    now = now_iso()
    closed_sql = "?" if new_status == "closed" else "NULL"
    vals: list = [new_status, now]
    if new_status == "closed":
        vals.append(now)
    vals += [tenant_id, tid, expected_status, expected_version]
    with transaction() as conn:
        cur = conn.execute(
            f"UPDATE tickets SET status = ?, updated_at = ?, closed_at = {closed_sql} "
            "WHERE tenant_id = ? AND id = ? AND status = ? "
            "AND COALESCE(updated_at, '') = ?", vals)
        return bool(cur.rowcount)


# replies

def _r_row(r) -> TicketReply:
    return TicketReply(
        id=r["id"], ticket_id=r["ticket_id"], body=r["body"],
        author_type=r["author_type"], author_id=r["author_id"],
        tenant_id=r["tenant_id"], created_at=parse_dt(r["created_at"]),
    )


def list_replies(tenant_id: int, ticket_id: int) -> list[TicketReply]:
    cur = db().execute(
        "SELECT * FROM ticket_replies WHERE tenant_id = ? AND ticket_id = ? ORDER BY id",
        (tenant_id, ticket_id))
    return [_r_row(r) for r in cur.fetchall()]


def add_reply(reply: TicketReply) -> TicketReply:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO ticket_replies(tenant_id, ticket_id, body, author_type, author_id, created_at)
            VALUES(?,?,?,?,?,?)
        """, (reply.tenant_id, reply.ticket_id, reply.body, reply.author_type,
              reply.author_id, now))
        conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?",
                     (now, reply.ticket_id))
        new_id = cur.lastrowid
    cur = db().execute("SELECT * FROM ticket_replies WHERE id = ?", (new_id,))
    return _r_row(cur.fetchone())
