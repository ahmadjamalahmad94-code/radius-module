"""Unified customer service request API.

Creates a support ticket for the requested service and, when requested, opens a
manual wallet payment request for admin review. The endpoint records intent only;
it never activates services directly.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import math
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
from ...radius.routes.finance_collection import collection_frozen
from ...radius.db.repos.service_entitlements_repo import (
    LocalServiceEntitlementRepository,
    ServiceRequestLinkRepository,
)
from ..access_control import deny_out_of_scope, subscriber_in_scope
from ..auth import require_api_token
from ..json_input import json_object
from ..responses import fail, ok
from ...radius.core.numbers import strict_float  # Infinity/NaN → ValueError (422)


SERVICE_LABELS = {
    "cards": N_("الكروت"),
    "cards_recharge": N_("شحن الكروت"),
    "communications": N_("التواصل والحملات"),
    "customer_portal": N_("بوابة العميل"),
    "customer_support": N_("الدعم الفني"),
    "distributors": N_("الموزعون"),
    "finance_center": N_("المركز المالي"),
    "integration_bridge": N_("جسر الربط"),
    "integration_tokens": N_("مفاتيح الربط"),
    "ip_change_vpn": N_("تغيير عنوان الإنترنت"),
    "nas": N_("أجهزة الشبكة"),
    "network_policy": N_("سياسات الشبكة"),
    "payment_collection": N_("تحصيل المدفوعات"),
    "reports": N_("التقارير"),
    "sessions": N_("الجلسات"),
    "subscribers": N_("المشتركين"),
    "other": N_("خدمة أخرى"),
}

REQUEST_TYPES = {
    "activation": N_("تفعيل"),
    "upgrade": N_("ترقية"),
    "trial": N_("فتح تجريبي"),
    "renewal": N_("تجديد"),
    "support": N_("مراجعة فنية"),
}

DECISIONS = {
    "approve": N_("موافقة مبدئية"),
    "reject": N_("رفض الطلب"),
    "request_payment": N_("طلب دفع"),
    "trial": N_("فتح تجريبي"),
}

# آلة حالات القرار: من أيّ حالة تذكرة يُسمح بكلّ قرار.
#   • approve («موافقة مبدئية») مرّة واحدة: من open/pending فقط.
#   • trial / request_payment / reject مسموحة أيضًا بعد الموافقة المبدئيّة
#     (in_progress) — «وافقنا مبدئيًّا ثمّ رُفض لعدم الدفع» تسلسلٌ مشروع.
#   • «مغلقة/محلولة» نهائيّة: الطلب المرفوض لا يُوافَق عليه لاحقًا → 409.
# والانتقال نفسه compare-and-set على الحالة+updated_at المقروءين: من قرارات
# متوازية قرأت الحالة نفسها يفوز واحد والبقيّة 409 (بلا أيّ أثر جانبيّ).
# ولمن يريد «قرارًا واحدًا فقط» صراحةً: expected_status في الجسم → 409 إن
# تغيّرت الحالة عمّا رآه المشغّل.
_DECISION_FROM = {
    "approve": {"open", "pending"},
    "trial": {"open", "pending", "in_progress"},
    "reject": {"open", "pending", "in_progress"},
    "request_payment": {"open", "pending", "in_progress"},
}
_TERMINAL_STATUSES = {"closed", "resolved"}

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
        "created_at": ticket.created_at.isoformat() + "Z" if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() + "Z" if ticket.updated_at else None,
        "closed_at": ticket.closed_at.isoformat() + "Z" if ticket.closed_at else None,
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
    if isinstance(value, bool):
        raise ValueError("amount")
    try:
        parsed = strict_float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("amount") from exc
    # NaN/Infinity تمرّ من «<= 0» — تُرفض صراحةً.
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("amount")
    return parsed


def _validated_payment(payment: dict[str, Any] | None):
    if not payment:
        return None, None
    if not isinstance(payment, dict):
        return None, fail("validation_error", _tr("بيانات الدفع غير صحيحة."), status=422)
    amount_value = payment.get("amount")
    if amount_value in (None, ""):
        return None, None
    try:
        amount = _positive_amount(amount_value)
    except ValueError:
        return None, fail("validation_error", _tr("المبلغ يجب أن يكون أكبر من صفر."), status=422)

    settings = PaymentSettingsRepository().get(_tid())
    # تجميد قسم التحصيل: لا فتح طلبات دفع من جهة العميل/المشترك حتى ربط
    # بوابة دفع حقيقية (انظر collection_frozen في finance_collection).
    if collection_frozen(settings):
        return None, fail(
            "collection_frozen",
            _tr("قسم التحصيل مجمّد — اربط بوابة دفع أولًا."),
            status=423,
        )
    if not settings or not settings.enabled:
        return None, fail("payments_disabled", _tr("تحصيل المدفوعات غير مفعل."), status=422)
    if settings.provider == "jawwal_pay":
        return None, fail("provider_disabled", _tr("مزود جوال باي غير مفعل لهذه العملية."), status=422)

    purpose = str(payment.get("purpose") or "monthly_subscription").strip()
    if purpose not in PAYMENT_PURPOSES:
        return None, fail("validation_error", _tr("غرض الدفع غير صحيح."), status=422)
    if not _purpose_enabled(settings, purpose):
        return None, fail("purpose_disabled", _tr("هذا النوع من المدفوعات غير مفعل."), status=422)

    if settings.min_amount is not None and amount < settings.min_amount:
        return None, fail("validation_error", _tr("المبلغ أقل من الحد الأدنى."), status=422)
    if settings.max_amount is not None and amount > settings.max_amount:
        return None, fail("validation_error", _tr("المبلغ أعلى من الحد الأقصى."), status=422)

    currency = str(payment.get("currency") or settings.currency).strip()
    if currency not in CURRENCIES:
        return None, fail("validation_error", _tr("عملة الدفع غير مدعومة."), status=422)

    return {
        "amount": amount,
        "currency": currency,
        "purpose": purpose,
        "settings": settings,
    }, None


def _service_body(*, service_label: str, request_label: str, subscriber, notes: str,
                  payment_context: dict[str, Any] | None) -> str:
    lines = [
        _tr('الخدمة المطلوبة: %(service_label)s', service_label=service_label),
        _tr('نوع الطلب: %(request_label)s', request_label=request_label),
        _tr('المشترك: %(username)s', username=subscriber['username']),
    ]
    if subscriber["full_name"]:
        lines.append(_tr('الاسم: %(full_name)s', full_name=subscriber['full_name']))
    if subscriber["mobile"]:
        lines.append(_tr('الجوال: %(mobile)s', mobile=subscriber['mobile']))
    if notes:
        lines.extend(["", N_("ملاحظات العميل:"), notes])
    if payment_context:
        lines.extend([
            "",
            _tr('طلب دفع مطلوب: %(amount)s %(currency)s', amount=payment_context['amount'], currency=payment_context['currency']),
            N_("يبقى تنفيذ الخدمة بانتظار مراجعة الإدارة وإقرار الدفع."),
        ])
    else:
        lines.extend(["", N_("لا يوجد طلب دفع مرتبط عند إنشاء التذكرة.")])
    return "\n".join(lines)


def _create_payment_request(subscriber_id: int, payment_context: dict[str, Any]) -> dict[str, Any]:
    settings = payment_context["settings"]
    return PaymentRequestRepository().create(
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


def _trial_days(value: Any) -> int:
    if value in (None, ""):
        return 7
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("trial_days") from exc
    if days < 1 or days > 60:
        raise ValueError("trial_days")
    return days


def _entitlement_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row.get("id"),
        "service_key": row.get("service_key"),
        "service_label": row.get("service_label"),
        "enabled": bool(row.get("enabled")),
        "status": row.get("status"),
        "source_type": row.get("source_type"),
        "ticket_id": row.get("ticket_id"),
        "subscriber_id": row.get("subscriber_id"),
        "expires_at": row.get("expires_at"),
        "updated_at": row.get("updated_at"),
    }


def _decision_reply(
    decision: str,
    note: str,
    payment_request: dict[str, Any] | None = None,
    trial_days: int | None = None,
    entitlement: dict[str, Any] | None = None,
) -> str:
    lines = [_tr('قرار الإدارة: %(v)s', v=DECISIONS[decision])]
    if decision == "approve":
        lines.append(N_("تمت الموافقة المبدئية على طلب الخدمة. التنفيذ النهائي يبقى حسب الدفع والصلاحيات."))
    elif decision == "reject":
        lines.append(N_("تم رفض طلب الخدمة وإغلاق التذكرة."))
    elif decision == "trial":
        days = trial_days or 7
        expires_at = entitlement.get("expires_at") if entitlement else None
        lines.append(_tr('تم فتح صلاحية تجريبية مؤقتة لمدة %(days)s يوم.', days=days))
        if expires_at:
            lines.append(_tr('تاريخ انتهاء التجربة: %(expires_at)s', expires_at=expires_at))
        lines.append(N_("هذا يحدث عقد التشغيل فقط، بدون أي أوامر مباشرة على الراوتر."))
    elif decision == "request_payment" and payment_request:
        lines.append(
            _tr('تم فتح طلب دفع: %(reference_code)s بقيمة %(amount)s %(currency)s.', reference_code=payment_request['reference_code'], amount=payment_request['amount'], currency=payment_request['currency'])
        )
    if note:
        lines.extend(["", N_("ملاحظة الإدارة:"), note])
    return "\n".join(lines)


def _service_ticket_or_error(ticket_id: int):
    ticket = tickets_repo.get_ticket(_tid(), ticket_id)
    if not ticket or ticket.category != "service_request":
        return None, fail("not_found", _tr("طلب الخدمة غير موجود."), status=404)
    if not subscriber_in_scope(subscriber_id=ticket.subscriber_id):
        return None, deny_out_of_scope()
    return ticket, None


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/service-requests",
        "service_requests_list",
        require_api_token(list_service_requests),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/service-requests",
        "service_requests_create",
        require_api_token(create_service_request),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/service-requests/<int:ticket_id>/decision",
        "service_requests_decision",
        require_api_token(service_request_decision),
        methods=["POST"],
    )


def list_service_requests():
    status = str(request.args.get("status") or "").strip() or None
    try:
        limit = min(max(1, int(request.args.get("limit") or 100)), 500)
        offset = max(0, int(request.args.get("offset") or 0))
    except (TypeError, ValueError):
        return fail("validation_error", _tr("قيمة الترقيم غير صحيحة."), status=422)
    items = [
        _ticket_payload(ticket)
        for ticket in tickets_repo.list_tickets(
            _tid(),
            status=status,
            category="service_request",
            limit=limit,
            offset=offset,
        )
        if subscriber_in_scope(subscriber_id=ticket.subscriber_id)
    ]
    return ok({"items": items, "count": len(items)})


def create_service_request():
    body, err = json_object()
    if err:
        return err
    try:
        raw_sid = body.get("subscriber_id")
        subscriber_id = 0 if isinstance(raw_sid, (bool, dict, list)) else int(raw_sid or 0)
    except (TypeError, ValueError):
        subscriber_id = 0
    if subscriber_id <= 0:
        return fail("validation_error", _tr("اختر المشترك أولًا."), status=422)
    if not subscriber_in_scope(subscriber_id=subscriber_id):
        return deny_out_of_scope()

    subscriber = _subscriber_row(subscriber_id)
    if not subscriber:
        return fail("not_found", _tr("المشترك غير موجود."), status=404)

    for _text_key in ("service_key", "service_name", "request_type", "priority", "notes"):
        if isinstance(body.get(_text_key), (dict, list)):
            return fail("validation_error", _tr("قيم الطلب النصّيّة غير صحيحة."), status=422)
    service_key = str(body.get("service_key") or "other").strip()
    if not _SLUG_RE.match(service_key):
        return fail("validation_error", _tr("تعريف الخدمة غير صحيح."), status=422)
    service_label = str(body.get("service_name") or SERVICE_LABELS.get(service_key) or "").strip()
    if not service_label:
        return fail("validation_error", _tr("اسم الخدمة مطلوب."), status=422)
    service_label = service_label[:160]

    request_type = str(body.get("request_type") or "activation").strip()
    if request_type not in REQUEST_TYPES:
        return fail("validation_error", _tr("نوع الطلب غير صحيح."), status=422)
    priority = str(body.get("priority") or "normal").strip()
    if priority not in TICKET_PRIORITIES:
        return fail("validation_error", _tr("أولوية التذكرة غير صحيحة."), status=422)
    notes = str(body.get("notes") or "").strip()[:1000]

    payment_context, error = _validated_payment(body.get("payment"))
    if error:
        return error

    ticket = tickets_repo.create_ticket(Ticket(
        id=None,
        tenant_id=_tid(),
        subscriber_id=subscriber_id,
        subject=_tr('طلب خدمة: %(service_label)s', service_label=service_label),
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
    ServiceRequestLinkRepository().create_or_update(
        tenant_id=_tid(),
        ticket_id=int(ticket.id or 0),
        subscriber_id=subscriber_id,
        service_key=service_key,
        service_label=service_label,
        request_type=request_type,
        status=ticket.status,
    )
    if payment_context:
        payment_request = _create_payment_request(subscriber_id, payment_context)
        ServiceRequestLinkRepository().update_decision(
            tenant_id=_tid(),
            ticket_id=int(ticket.id or 0),
            decision="payment_requested",
            status=ticket.status,
            latest_payment_request_id=int(payment_request["id"]),
        )
        tickets_repo.add_reply(TicketReply(
            id=None,
            tenant_id=_tid(),
            ticket_id=int(ticket.id or 0),
            author_type="admin",
            author_id=_admin_id(),
            body=(
                _tr('تم فتح طلب دفع مرتبط بالتذكرة: %(reference_code)s بقيمة %(amount)s %(currency)s.', reference_code=payment_request['reference_code'], amount=payment_request['amount'], currency=payment_request['currency'])
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


def service_request_decision(ticket_id: int):
    ticket, error = _service_ticket_or_error(ticket_id)
    if error:
        return error
    body, err = json_object()
    if err:
        return err
    decision = body.get("decision")
    decision = decision.strip() if isinstance(decision, str) else ""
    if decision not in DECISIONS:
        return fail("validation_error", _tr("قرار الطلب غير صحيح."), status=422)
    note = body.get("note")
    note = (note if isinstance(note, str) else "").strip()[:1000]

    version = tickets_repo.ticket_version(_tid(), int(ticket.id or ticket_id))
    if version is None:
        return fail("not_found", _tr("طلب الخدمة غير موجود."), status=404)
    seen_status, seen_version = version
    if seen_status in _TERMINAL_STATUSES:
        return fail("conflict", _tr("تم البتّ في هذا الطلب وإغلاقه مسبقًا — لا يمكن تغيير القرار."),
                    status=409, details={"status": seen_status})
    expected = body.get("expected_status")
    if isinstance(expected, str) and expected.strip() and expected.strip() != seen_status:
        return fail("conflict", _tr("تغيّرت حالة الطلب منذ فتحته — حدّث الصفحة وراجع القرار."),
                    status=409, details={"status": seen_status})
    if seen_status not in _DECISION_FROM[decision]:
        return fail("conflict", N_("هذا القرار غير متاح في حالة الطلب الحالية."),
                    status=409, details={"status": seen_status, "decision": decision})

    payment_request = None
    payment_context = None
    trial_days = None
    service_entitlement = None
    next_status = seen_status
    if decision == "approve":
        next_status = "in_progress"
    elif decision == "trial":
        try:
            trial_days = _trial_days(body.get("trial_days"))
        except ValueError:
            return fail("validation_error", _tr("مدة التجربة يجب أن تكون بين 1 و60 يوم."), status=422)
        next_status = "in_progress"
    elif decision == "reject":
        next_status = "closed"
    elif decision == "request_payment":
        payment_context, validation_error = _validated_payment(body.get("payment"))
        if validation_error:
            return validation_error
        if not payment_context:
            return fail("validation_error", _tr("أدخل مبلغ طلب الدفع."), status=422)
        next_status = "pending"

    # الانتقال الذرّيّ أوّلًا — لا أثر جانبيّ (طلب دفع/تجربة/ردّ) قبل الفوز به.
    if not tickets_repo.compare_and_set_status(
            _tid(), int(ticket.id or ticket_id), expected_status=seen_status,
            expected_version=seen_version, new_status=next_status):
        return fail("conflict", _tr("اتُّخذ قرار آخر على هذا الطلب للتوّ — حدّث الصفحة وراجع حالته."),
                    status=409)
    if payment_context:
        payment_request = _create_payment_request(ticket.subscriber_id, payment_context)
    updated = tickets_repo.get_ticket(_tid(), ticket.id or ticket_id)
    service_link = ServiceRequestLinkRepository().update_decision(
        tenant_id=_tid(),
        ticket_id=int(ticket.id or ticket_id),
        decision=decision,
        status=next_status,
        latest_payment_request_id=int(payment_request["id"]) if payment_request else None,
    )
    if decision == "trial" and service_link:
        service_entitlement = LocalServiceEntitlementRepository().upsert_trial_from_service_request(
            tenant_id=_tid(),
            link=service_link,
            trial_days=trial_days or 7,
            actor=f"admin:{_admin_id()}",
        )
    tickets_repo.add_reply(TicketReply(
        id=None,
        tenant_id=_tid(),
        ticket_id=ticket.id or ticket_id,
        author_type="admin",
        author_id=_admin_id(),
        body=_decision_reply(
            decision,
            note,
            payment_request,
            trial_days=trial_days,
            entitlement=service_entitlement,
        ),
    ))
    updated = tickets_repo.get_ticket(_tid(), ticket.id or ticket_id) or updated
    return ok({
        "service_request": {
            "reference": f"SR-{ticket.id}",
            "ticket_id": ticket.id,
            "payment_request_id": payment_request["id"] if payment_request else None,
            "status": updated.status if updated else next_status,
            "decision": decision,
            "decision_label": DECISIONS[decision],
            "local_service_apply": bool(service_entitlement),
            "trial_days": trial_days,
            "expires_at": service_entitlement.get("expires_at") if service_entitlement else None,
        },
        "ticket": _ticket_payload(updated or ticket),
        "payment_request": _payment_request_payload(payment_request) if payment_request else None,
        "service_entitlement": _entitlement_payload(service_entitlement),
    })
