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
                  limit: int = 200, offset: int = 0) -> list[Ticket]:
    sql = "SELECT * FROM tickets WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if status:
        sql += " AND status = ?"; vals.append(status)
    if subscriber_id is not None:
        sql += " AND subscriber_id = ?"; vals.append(subscriber_id)
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
    if changes.get("status") == "closed":
        sets.append("closed_at = ?"); vals.append(now_iso())
    vals += [tenant_id, tid]
    with transaction() as conn:
        conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE tenant_id = ? AND id = ?", vals)
    return get_ticket(tenant_id, tid)


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
