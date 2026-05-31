"""Unified customer service request API.

Creates a support ticket for the requested service and, when requested, opens a
manual wallet payment request for admin review. The endpoint records intent only;
it never activates services directly.
"""
from __future__ import annotations

import re
from typing import Any

from flask import Blueprint, g, request

from ...radius.core.types_saas import TICKET_PRIORITIES, Ticket, TicketReply
from ...radius.db.connection import db
from ...radius.db.repos import tickets_repo
from ...radius.db.repos.payments_repo import (
    CURRENCIES,
    PAYMENT_PURPOSES,
    PaymentRequestRepository,
    PaymentSettings,
    PaymentSettingsRepository,
)
from ..access_control import deny_out_of_scope, subscriber_in_scope
from ..auth import require_api_token
from ..responses import fail, ok


SERVICE_LABELS = {
    "cards": "الكروت",
    "cards_recharge": "شحن الكروت",
    "communications": "التواصل والحملات",
    "customer_portal": "بوابة العميل",
    "customer_support": "الدعم الفني",
    "distributors": "الموزعون",
    "finance_center": "المركز المالي",
    "integration_bridge": "جسر الربط",
    "integration_tokens": "مفاتيح الربط",
    "ip_change_vpn": "خدمة تغيير IP / VPN",
    "nas": "أجهزة الشبكة",
    "network_policy": "سياسات الشبكة",
    "payment_collection": "تحصيل المدفوعات",
    "reports": "التقارير",
    "sessions": "الجلسات",
    "subscribers": "المشتركين",
    "other": "خدمة أخرى",
}

REQUEST_TYPES = {
    "activation": "تفعيل",
    "upgrade": "ترقية",
    "trial": "فتح تجريبي",
    "renewal": "تجديد",
    "support": "مراجعة فنية",
}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _admin_id() -> int:
    return int(getattr(g, "admin_id", 0) or 0)


def _ticket_payload(ticket: Ticket) -> dict[str, Any]:
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


def _payment_request_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "payer_type": row["payer_type"],
        "payer_id": row["payer_id"],
        "purpose": row["purpose"],
        "amount": row["amount"],
        "currency": row["currency"],
        "provider": row["provider"],
        "receiver_wallet": row["receiver_wallet"],
        "reference_code": row["reference_code"],
        "status": row["status"],
        "expires_at": row["expires_at"],
        "created_by": row["created_by"],
        "ledger_entry_id": row.get("ledger_entry_id"),
        "ledger_applied_at": row.get("ledger_applied_at"),
        "service_apply_status": row.get("service_apply_status", "not_applied"),
        "service_apply_attempt_id": row.get("service_apply_attempt_id"),
        "service_applied_at": row.get("service_applied_at"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _purpose_enabled(settings: PaymentSettings, purpose: str) -> bool:
    if purpose == "card_purchase":
        return settings.allow_cards
    if purpose in {"monthly_subscription", "subscriber_renewal", "quota_topup", "time_extension"}:
        return settings.allow_monthly_subscriptions
    if purpose == "distributor_payment":
        return settings.allow_distributor_payments
    return purpose == "loan_settlement"


def _subscriber_row(subscriber_id: int):
    return db().execute(
        """
        SELECT id, username, full_name, mobile, email
        FROM subscribers
        WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
        """,
        (_tid(), subscriber_id),
    ).fetchone()


def _positive_amount(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("amount") from exc
    if parsed <= 0:
        raise ValueError("amount")
    return parsed


def _validated_payment(payment: dict[str, Any] | None):
    if not payment:
        return None, None
    if not isinstance(payment, dict):
        return None, fail("validation_error", "بيانات الدفع غير صحيحة.", status=422)
    amount_value = payment.get("amount")
    if amount_value in (None, ""):
        return None, None
    try:
        amount = _positive_amount(amount_value)
    except ValueError:
        return None, fail("validation_error", "المبلغ يجب أن يكون أكبر من صفر.", status=422)

    settings = PaymentSettingsRepository().get(_tid())
    if not settings or not settings.enabled:
        return None, fail("payments_disabled", "تحصيل المدفوعات غير مفعل.", status=422)
    if settings.provider == "jawwal_pay":
        return None, fail("provider_disabled", "مزود جوال باي غير مفعل لهذه العملية.", status=422)

    purpose = str(payment.get("purpose") or "monthly_subscription").strip()
    if purpose not in PAYMENT_PURPOSES:
        return None, fail("validation_error", "غرض الدفع غير صحيح.", status=422)
    if not _purpose_enabled(settings, purpose):
        return None, fail("purpose_disabled", "هذا النوع من المدفوعات غير مفعل.", status=422)

    if settings.min_amount is not None and amount < settings.min_amount:
        return None, fail("validation_error", "المبلغ أقل من الحد الأدنى.", status=422)
    if settings.max_amount is not None and amount > settings.max_amount:
        return None, fail("validation_error", "المبلغ أعلى من الحد الأقصى.", status=422)

    currency = str(payment.get("currency") or settings.currency).strip()
    if currency not in CURRENCIES:
        return None, fail("validation_error", "عملة الدفع غير مدعومة.", status=422)

    return {
        "amount": amount,
        "currency": currency,
        "purpose": purpose,
        "settings": settings,
    }, None


def _service_body(*, service_label: str, request_label: str, subscriber, notes: str,
                  payment_context: dict[str, Any] | None) -> str:
    lines = [
        f"الخدمة المطلوبة: {service_label}",
        f"نوع الطلب: {request_label}",
        f"المشترك: {subscriber['username']}",
    ]
    if subscriber["full_name"]:
        lines.append(f"الاسم: {subscriber['full_name']}")
    if subscriber["mobile"]:
        lines.append(f"الجوال: {subscriber['mobile']}")
    if notes:
        lines.extend(["", "ملاحظات العميل:", notes])
    if payment_context:
        lines.extend([
            "",
            f"طلب دفع مطلوب: {payment_context['amount']} {payment_context['currency']}",
            "يبقى تنفيذ الخدمة بانتظار مراجعة الإدارة وإقرار الدفع.",
        ])
    else:
        lines.extend(["", "لا يوجد طلب دفع مرتبط عند إنشاء التذكرة."])
    return "\n".join(lines)


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/service-requests",
        "service_requests_create",
        require_api_token(create_service_request),
        methods=["POST"],
    )


def create_service_request():
    body = request.get_json(silent=True) or {}
    try:
        subscriber_id = int(body.get("subscriber_id") or 0)
    except (TypeError, ValueError):
        subscriber_id = 0
    if subscriber_id <= 0:
        return fail("validation_error", "اختر المشترك أولًا.", status=422)
    if not subscriber_in_scope(subscriber_id=subscriber_id):
        return deny_out_of_scope()

    subscriber = _subscriber_row(subscriber_id)
    if not subscriber:
        return fail("not_found", "المشترك غير موجود.", status=404)

    service_key = str(body.get("service_key") or "other").strip()
    if not _SLUG_RE.match(service_key):
        return fail("validation_error", "تعريف الخدمة غير صحيح.", status=422)
    service_label = str(body.get("service_name") or SERVICE_LABELS.get(service_key) or "").strip()
    if not service_label:
        return fail("validation_error", "اسم الخدمة مطلوب.", status=422)
    service_label = service_label[:160]

    request_type = str(body.get("request_type") or "activation").strip()
    if request_type not in REQUEST_TYPES:
        return fail("validation_error", "نوع الطلب غير صحيح.", status=422)
    priority = str(body.get("priority") or "normal").strip()
    if priority not in TICKET_PRIORITIES:
        return fail("validation_error", "أولوية التذكرة غير صحيحة.", status=422)
    notes = str(body.get("notes") or "").strip()[:1000]

    payment_context, error = _validated_payment(body.get("payment"))
    if error:
        return error

    ticket = tickets_repo.create_ticket(Ticket(
        id=None,
        tenant_id=_tid(),
        subscriber_id=subscriber_id,
        subject=f"طلب خدمة: {service_label}",
        category="service_request",
        priority=priority,
        status="open",
        body=_service_body(
            service_label=service_label,
            request_label=REQUEST_TYPES[request_type],
            subscriber=subscriber,
            notes=notes,
            payment_context=payment_context,
        ),
    ))

    payment_request = None
    if payment_context:
        settings = payment_context["settings"]
        payment_request = PaymentRequestRepository().create(
            tenant_id=_tid(),
            payer_type="subscriber",
            payer_id=subscriber_id,
            purpose=payment_context["purpose"],
            amount=payment_context["amount"],
            currency=payment_context["currency"],
            provider=settings.provider,
            receiver_wallet=settings.wallet_number,
            created_by=_admin_id() or None,
            ttl_minutes=settings.payment_request_ttl_minutes,
        )
        tickets_repo.add_reply(TicketReply(
            id=None,
            tenant_id=_tid(),
            ticket_id=int(ticket.id or 0),
            author_type="admin",
            author_id=_admin_id(),
            body=(
                f"تم فتح طلب دفع مرتبط بالتذكرة: {payment_request['reference_code']} "
                f"بقيمة {payment_request['amount']} {payment_request['currency']}."
            ),
        ))

    return ok({
        "service_request": {
            "reference": f"SR-{ticket.id}",
            "ticket_id": ticket.id,
            "payment_request_id": payment_request["id"] if payment_request else None,
            "status": ticket.status,
            "service_key": service_key,
            "service_label": service_label,
            "request_type": request_type,
            "request_label": REQUEST_TYPES[request_type],
        },
        "ticket": _ticket_payload(ticket),
        "payment_request": _payment_request_payload(payment_request) if payment_request else None,
    }, status=201)
