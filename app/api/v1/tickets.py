from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.types_saas import TICKET_PRIORITIES, TICKET_STATUSES, Ticket, TicketReply
from ...radius.db.repos import tickets_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _int_arg(name: str, default: int, maximum: int = 500) -> int:
    try:
        return min(max(0, int(request.args.get(name, default))), maximum)
    except (TypeError, ValueError):
        return default


def _ticket(ticket: Ticket) -> dict:
    return {
        "id": ticket.id,
        "subscriber_id": ticket.subscriber_id,
        "subject": ticket.subject,
        "category": ticket.category,
        "priority": ticket.priority,
        "status": ticket.status,
        "assignee_admin_id": ticket.assignee_admin_id,
        "body": ticket.body,
        "attachments": list(ticket.attachments),
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
    }


def _reply(reply: TicketReply) -> dict:
    return {
        "id": reply.id,
        "ticket_id": reply.ticket_id,
        "body": reply.body,
        "author_type": reply.author_type,
        "author_id": reply.author_id,
        "created_at": reply.created_at.isoformat() if reply.created_at else None,
    }


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/tickets", "tickets_list", require_api_token(list_tickets), methods=["GET"])
    bp.add_url_rule("/tickets", "tickets_create", require_api_token(create_ticket), methods=["POST"])
    bp.add_url_rule("/tickets/<int:ticket_id>", "tickets_get", require_api_token(get_ticket), methods=["GET"])
    bp.add_url_rule("/tickets/<int:ticket_id>", "tickets_patch", require_api_token(patch_ticket), methods=["PATCH"])
    bp.add_url_rule("/tickets/<int:ticket_id>/replies", "tickets_reply", require_api_token(add_reply), methods=["POST"])


def list_tickets():
    status = (request.args.get("status") or "").strip() or None
    subscriber_id = request.args.get("subscriber_id")
    try:
        parsed_subscriber_id = int(subscriber_id) if subscriber_id else None
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف المشترك يجب أن يكون رقمًا صحيحًا.", status=422)
    items = [
        _ticket(t)
        for t in tickets_repo.list_tickets(
            _tid(),
            status=status,
            subscriber_id=parsed_subscriber_id,
            limit=_int_arg("limit", 200),
            offset=_int_arg("offset", 0, maximum=100000),
        )
    ]
    return ok({"items": items, "count": len(items)})


def get_ticket(ticket_id: int):
    ticket = tickets_repo.get_ticket(_tid(), ticket_id)
    if not ticket:
        return fail("not_found", "التذكرة غير موجودة.", status=404)
    replies = [_reply(r) for r in tickets_repo.list_replies(_tid(), ticket_id)]
    return ok({"ticket": _ticket(ticket), "replies": replies})


def create_ticket():
    body = request.get_json(silent=True) or {}
    subject = str(body.get("subject") or "").strip()
    try:
        subscriber_id = int(body.get("subscriber_id") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف المشترك يجب أن يكون رقمًا صحيحًا.", status=422)
    if not subject or subscriber_id <= 0:
        return fail("validation_error", "اختر المشترك وأدخل عنوان التذكرة.", status=422)
    priority = str(body.get("priority") or "normal")
    status = str(body.get("status") or "open")
    if priority not in TICKET_PRIORITIES or status not in TICKET_STATUSES:
        return fail("validation_error", "أولوية التذكرة أو حالتها غير صحيحة.", status=422)
    try:
        assignee_admin_id = int(body["assignee_admin_id"]) if body.get("assignee_admin_id") not in (None, "") else None
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف الموظف المسؤول يجب أن يكون رقمًا صحيحًا.", status=422)
    ticket = Ticket(
        id=None,
        tenant_id=_tid(),
        subscriber_id=subscriber_id,
        subject=subject,
        category=str(body.get("category") or "general"),
        priority=priority,
        status=status,
        assignee_admin_id=assignee_admin_id,
        body=str(body.get("body") or ""),
        attachments=tuple(body.get("attachments") or ()),
    )
    return ok(_ticket(tickets_repo.create_ticket(ticket)), status=201)


def patch_ticket(ticket_id: int):
    if not tickets_repo.get_ticket(_tid(), ticket_id):
        return fail("not_found", "التذكرة غير موجودة.", status=404)
    body = request.get_json(silent=True) or {}
    if "priority" in body and body["priority"] not in TICKET_PRIORITIES:
        return fail("validation_error", "أولوية التذكرة غير صحيحة.", status=422)
    if "status" in body and body["status"] not in TICKET_STATUSES:
        return fail("validation_error", "حالة التذكرة غير صحيحة.", status=422)
    ticket = tickets_repo.update_ticket(_tid(), ticket_id, **body)
    return ok(_ticket(ticket))


def add_reply(ticket_id: int):
    if not tickets_repo.get_ticket(_tid(), ticket_id):
        return fail("not_found", "التذكرة غير موجودة.", status=404)
    body = request.get_json(silent=True) or {}
    text = str(body.get("body") or "").strip()
    if not text:
        return fail("validation_error", "نص الرد مطلوب.", status=422)
    reply = TicketReply(
        id=None,
        tenant_id=_tid(),
        ticket_id=ticket_id,
        body=text,
        author_type="admin",
        author_id=int(getattr(g, "admin_id", 0) or 0),
    )
    return ok(_reply(tickets_repo.add_reply(reply)), status=201)
