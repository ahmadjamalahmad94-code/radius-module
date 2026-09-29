from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.types_saas import TICKET_PRIORITIES, TICKET_STATUSES, Ticket, TicketReply
from ...radius.db.connection import db
from ...radius.db.repos import tickets_repo
from ..auth import require_api_token
from ..json_input import InputError, json_object, opt_int, opt_text
from ..responses import fail, ok

# حدود الحقول النصّيّة (كانت بلا حدّ: عنوان 10,005 حرفًا وتصنيف 5,000 مقبولان).
_SUBJECT_MAX = 200
_CATEGORY_MAX = 60
_BODY_MAX = 20000
_ATTACHMENTS_MAX = 20


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _int_arg(name: str, default: int, maximum: int = 500) -> int:
    try:
        return min(max(0, int(request.args.get(name, default))), maximum)
    except (TypeError, ValueError):
        return default


def _iso(value) -> str | None:
    # الطوابع مخزّنة UTC — نُلحق Z كبقيّة الـAPI (كانت بلا لاحقة منطقة).
    return value.isoformat() + "Z" if value else None


# Arabic label for a ticket category key (same map as the web ticket pages;
# the raw keys network / installation / account / cards reached the list —
# re-test R13 L4). Unknown keys fall back to the key itself.
TICKET_CATEGORY_LABELS = {
    "general": "عام", "billing": "الفواتير والدفع",
    "connection": "الاتصال والخدمة", "hardware": "الأجهزة والمعدّات",
    "complaint": "شكوى", "service_request": "طلب خدمة",
    "network": "الشبكة", "installation": "التركيب", "account": "الحساب",
    "cards": "الكروت", "technical": "دعم فنّي", "support": "دعم فنّي",
    "payment": "الدفع", "internet": "الإنترنت", "speed": "السرعة",
    "other": "أخرى",
}


def _ticket(ticket: Ticket) -> dict:
    return {
        "id": ticket.id,
        "subscriber_id": ticket.subscriber_id,
        "subject": ticket.subject,
        "category": ticket.category,
        "category_label": TICKET_CATEGORY_LABELS.get(
            str(ticket.category or "").lower(), ticket.category or "—"),
        "priority": ticket.priority,
        "status": ticket.status,
        "assignee_admin_id": ticket.assignee_admin_id,
        "body": ticket.body,
        "attachments": list(ticket.attachments),
        "created_at": _iso(ticket.created_at),
        "updated_at": _iso(ticket.updated_at),
        "closed_at": _iso(ticket.closed_at),
    }


def _reply(reply: TicketReply) -> dict:
    return {
        "id": reply.id,
        "ticket_id": reply.ticket_id,
        "body": reply.body,
        "author_type": reply.author_type,
        "author_id": reply.author_id,
        "created_at": _iso(reply.created_at),
    }


def _subscriber_exists(subscriber_id: int) -> bool:
    return db().execute(
        "SELECT 1 FROM subscribers WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (_tid(), int(subscriber_id))).fetchone() is not None


def _admin_exists(admin_id: int) -> bool:
    return db().execute("SELECT 1 FROM admins WHERE id = ?", (int(admin_id),)).fetchone() is not None


def _attachments(value) -> tuple:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(a, str) for a in value):
        raise InputError("المرفقات يجب أن تكون قائمة روابط نصّيّة.")
    if len(value) > _ATTACHMENTS_MAX:
        raise InputError(f"عدد المرفقات أكبر من المسموح ({_ATTACHMENTS_MAX}).")
    return tuple(a.strip() for a in value if a.strip())


def _assignee(value) -> int | None:
    admin_id = opt_int(value, label="الموظف المسؤول", minimum=1)
    if admin_id is not None and not _admin_exists(admin_id):
        raise InputError("الموظف المسؤول غير موجود.")
    return admin_id


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/tickets", "tickets_list", require_api_token(list_tickets), methods=["GET"])
    bp.add_url_rule("/tickets", "tickets_create", require_api_token(create_ticket), methods=["POST"])
    bp.add_url_rule("/tickets/<int:ticket_id>", "tickets_get", require_api_token(get_ticket), methods=["GET"])
    bp.add_url_rule("/tickets/<int:ticket_id>", "tickets_patch", require_api_token(patch_ticket), methods=["PATCH"])
    bp.add_url_rule("/tickets/<int:ticket_id>/replies", "tickets_reply", require_api_token(add_reply), methods=["POST"])


def list_tickets():
    status = (request.args.get("status") or "").strip() or None
    if status and status not in TICKET_STATUSES:
        return fail("validation_error", "حالة التذكرة غير صحيحة.", status=422)
    subscriber_id = request.args.get("subscriber_id")
    try:
        parsed_subscriber_id = int(subscriber_id) if subscriber_id else None
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف المشترك يجب أن يكون رقمًا صحيحًا.", status=422)
    limit = max(1, _int_arg("limit", 200))
    offset = _int_arg("offset", 0, maximum=100000)
    rows = tickets_repo.list_tickets(
        _tid(),
        status=status,
        subscriber_id=parsed_subscriber_id,
        limit=limit + 1,
        offset=offset,
    )
    items = [_ticket(t) for t in rows[:limit]]
    # has_more دقيق (صفّ زائد) — التطبيق كان يقف عند 200 تذكرة بلا ترقيم.
    return ok({"items": items, "count": len(items), "limit": limit,
               "offset": offset, "has_more": len(rows) > limit})


def get_ticket(ticket_id: int):
    ticket = tickets_repo.get_ticket(_tid(), ticket_id)
    if not ticket:
        return fail("not_found", "التذكرة غير موجودة.", status=404)
    replies = [_reply(r) for r in tickets_repo.list_replies(_tid(), ticket_id)]
    return ok({"ticket": _ticket(ticket), "replies": replies})


def create_ticket():
    body, err = json_object()
    if err:
        return err
    try:
        subject = opt_text(body.get("subject"), label="عنوان التذكرة", max_len=_SUBJECT_MAX)
        try:
            subscriber_id = opt_int(body.get("subscriber_id"), label="معرّف المشترك") or 0
        except InputError:
            raise InputError("معرّف المشترك يجب أن يكون رقمًا صحيحًا.")
        if not subject or subscriber_id <= 0:
            return fail("validation_error", "اختر المشترك وأدخل عنوان التذكرة.", status=422)
        priority = opt_text(body.get("priority"), label="الأولوية") or "normal"
        status = opt_text(body.get("status"), label="الحالة") or "open"
        if priority not in TICKET_PRIORITIES or status not in TICKET_STATUSES:
            return fail("validation_error", "أولوية التذكرة أو حالتها غير صحيحة.", status=422)
        category = opt_text(body.get("category"), label="التصنيف", max_len=_CATEGORY_MAX) or "general"
        text = opt_text(body.get("body"), label="نص التذكرة", max_len=_BODY_MAX)
        attachments = _attachments(body.get("attachments"))
        assignee_admin_id = _assignee(body.get("assignee_admin_id"))
    except InputError as e:
        return fail("validation_error", e.message, status=422)
    if not _subscriber_exists(subscriber_id):
        return fail("not_found", "المشترك غير موجود.", status=404)
    ticket = Ticket(
        id=None,
        tenant_id=_tid(),
        subscriber_id=subscriber_id,
        subject=subject,
        category=category,
        priority=priority,
        status=status,
        assignee_admin_id=assignee_admin_id,
        body=text,
        attachments=attachments,
    )
    return ok(_ticket(tickets_repo.create_ticket(ticket)), status=201)


def patch_ticket(ticket_id: int):
    if not tickets_repo.get_ticket(_tid(), ticket_id):
        return fail("not_found", "التذكرة غير موجودة.", status=404)
    body, err = json_object()
    if err:
        return err
    changes: dict = {}
    try:
        if "subject" in body:
            subject = opt_text(body["subject"], label="عنوان التذكرة", max_len=_SUBJECT_MAX)
            if not subject:
                raise InputError("عنوان التذكرة لا يمكن أن يكون فارغًا.")
            changes["subject"] = subject
        if "category" in body:
            changes["category"] = opt_text(
                body["category"], label="التصنيف", max_len=_CATEGORY_MAX) or "general"
        if "body" in body:
            changes["body"] = opt_text(body["body"], label="نص التذكرة", max_len=_BODY_MAX)
        if "priority" in body:
            if body["priority"] not in TICKET_PRIORITIES:
                raise InputError("أولوية التذكرة غير صحيحة.")
            changes["priority"] = body["priority"]
        if "status" in body:
            if body["status"] not in TICKET_STATUSES:
                raise InputError("حالة التذكرة غير صحيحة.")
            changes["status"] = body["status"]
        if "assignee_admin_id" in body:
            changes["assignee_admin_id"] = _assignee(body["assignee_admin_id"])
    except InputError as e:
        return fail("validation_error", e.message, status=422)
    ticket = tickets_repo.update_ticket(_tid(), ticket_id, **changes)
    return ok(_ticket(ticket))


def add_reply(ticket_id: int):
    if not tickets_repo.get_ticket(_tid(), ticket_id):
        return fail("not_found", "التذكرة غير موجودة.", status=404)
    body, err = json_object()
    if err:
        return err
    try:
        text = opt_text(body.get("body"), label="نص الرد", max_len=_BODY_MAX)
    except InputError as e:
        return fail("validation_error", e.message, status=422)
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
