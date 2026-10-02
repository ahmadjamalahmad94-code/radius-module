from __future__ import annotations

from datetime import datetime

from flask import Blueprint, g, request

from ...radius.core.types_saas import INVOICE_STATUSES, Invoice
from ...radius.db.repos import invoices_repo
from ..auth import require_api_token
from ..responses import fail, ok
from ...radius.core.timeparse import parse_iso_utc
from ...radius.core.numbers import strict_float  # Infinity/NaN → ValueError (422)


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _int_arg(name: str, default: int, maximum: int = 500) -> int:
    try:
        return min(max(0, int(request.args.get(name, default))), maximum)
    except (TypeError, ValueError):
        return default


def _dt(raw):
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ValueError("قيم التاريخ يجب أن تكون نصًا بصيغة ISO.")
    try:
        # «Z»/إزاحة → UTC ساكن (كانت الإزاحة تُخزَّن واعية فتكسر المقارنات).
        return parse_iso_utc(raw, strict=True)
    except ValueError as exc:
        raise ValueError("قيمة التاريخ غير صالحة. استخدم صيغة ISO.") from exc


def _system_currency() -> str:
    from ...radius.core.system_config import default_currency
    return default_currency()


def _item(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "subscriber_id": invoice.subscriber_id,
        "username": invoice.username,
        "amount": invoice.amount,
        # zero-w3: the row's own currency (per-row rule) — the app shows it.
        "currency": invoice.currency or _system_currency(),
        "admin_id": invoice.admin_id,
        "plan_id": invoice.plan_id,
        "plan_name": invoice.plan_name,
        "service_type": invoice.service_type,
        "router_id": invoice.router_id,
        "direction": invoice.direction,
        "balance_before": invoice.balance_before,
        "balance_after": invoice.balance_after,
        "recharged_on": invoice.recharged_on.isoformat() if invoice.recharged_on else None,
        "expiration_at": invoice.expiration_at.isoformat() if invoice.expiration_at else None,
        "payment_method": invoice.payment_method,
        "payment_gateway_id": invoice.payment_gateway_id,
        "status": invoice.status,
        "note": invoice.note,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "updated_at": invoice.updated_at.isoformat() if invoice.updated_at else None,
    }


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/invoices", "invoices_list", require_api_token(list_invoices), methods=["GET"])
    bp.add_url_rule("/invoices", "invoices_create", require_api_token(create_invoice), methods=["POST"])
    bp.add_url_rule("/invoices/<int:invoice_id>", "invoices_get", require_api_token(get_invoice), methods=["GET"])
    bp.add_url_rule("/invoices/<int:invoice_id>/status", "invoices_status", require_api_token(update_status), methods=["POST"])


def list_invoices():
    status = (request.args.get("status") or "").strip() or None
    subscriber_id = request.args.get("subscriber_id")
    items = [
        _item(i)
        for i in invoices_repo.list_all(
            _tid(),
            status=status,
            subscriber_id=int(subscriber_id) if subscriber_id else None,
            limit=_int_arg("limit", 200),
            offset=_int_arg("offset", 0, maximum=100000),
        )
    ]
    return ok({"items": items, "count": len(items), "stats": invoices_repo.stats(_tid())})


def get_invoice(invoice_id: int):
    invoice = invoices_repo.get(_tid(), invoice_id)
    if not invoice:
        return fail("not_found", "الفاتورة غير موجودة.", status=404)
    return ok(_item(invoice))


def create_invoice():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):  # [1] / "x" → .get() was a 500 (R08 NEW-4)
        return fail("validation_error", "جسم الطلب يجب أن يكون كائن JSON.", status=422)
    try:
        subscriber_id = int(body.get("subscriber_id") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف المشترك يجب أن يكون رقمًا صحيحًا.", status=422)
    try:
        amount = strict_float(body.get("amount") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "قيمة الفاتورة يجب أن تكون رقمًا صحيحًا.", status=422)
    try:
        recharged_on = _dt(body.get("recharged_on"))
        expiration_at = _dt(body.get("expiration_at"))
    except ValueError as exc:
        return fail("validation_error", str(exc), status=422)
    username = str(body.get("username") or "").strip()
    if subscriber_id <= 0 or amount < 0:
        return fail("validation_error", "اختر المشترك، وأدخل اسم المستخدم، وقيمة الفاتورة.", status=422)
    # Parity-b F7: like the web (inv_create) — the subscriber is resolved in
    # this tenant and the username/balances come from it. A typed id that does
    # not exist was a 500 (FK); a wrong-but-valid id put the invoice in another
    # subscriber's customer portal under a mismatched username.
    from ...radius.db.connection import db
    sub = db().execute(
        "SELECT id, username, balance FROM subscribers WHERE tenant_id = ? AND id = ? "
        "AND deleted_at IS NULL", (_tid(), subscriber_id)).fetchone()
    if sub is None:
        return fail("validation_error", "المشترك غير موجود.", status=422,
                    details={"field": "subscriber_id"})
    if username and username != str(sub["username"]):
        return fail("validation_error",
                    f"اسم المستخدم لا يطابق المشترك #{subscriber_id} ({sub['username']}).",
                    status=422, details={"field": "username"})
    username = str(sub["username"])
    from ...radius.core import limits
    _msg = limits.amount_error(amount, "generic", label="قيمة الفاتورة")
    if _msg:   # «الحدود» — باقي المدخلات الماليّة
        return fail("validation_error", _msg, status=422)
    try:
        plan_id = int(body["plan_id"]) if body.get("plan_id") not in (None, "") else None
        router_id = int(body["router_id"]) if body.get("router_id") not in (None, "") else None
        payment_gateway_id = (
            int(body["payment_gateway_id"])
            if body.get("payment_gateway_id") not in (None, "")
            else None
        )
        _bal = float(sub["balance"] or 0)
        balance_before = (strict_float(body["balance_before"])
                          if body.get("balance_before") not in (None, "") else _bal)
        balance_after = (strict_float(body["balance_after"])
                         if body.get("balance_after") not in (None, "") else _bal + amount)
    except (TypeError, ValueError):
        return fail("validation_error", "القيم الرقمية في الفاتورة يجب أن تكون صحيحة.", status=422)
    # zero-w3: 0 = «غير محدّد» (the app sent 0 for blank numeric fields) and a
    # plan / router / gateway that is not in this network is a 422 — each was
    # a FOREIGN KEY 500.
    plan_id = plan_id if plan_id and plan_id > 0 else None
    router_id = router_id if router_id and router_id > 0 else None
    payment_gateway_id = (payment_gateway_id
                          if payment_gateway_id and payment_gateway_id > 0 else None)
    plan_name = str(body.get("plan_name") or "")
    if plan_id is not None:
        from ...radius.db.repos import plans_repo
        _plan = plans_repo.get_plan(_tid(), plan_id)
        if _plan is None:
            return fail("validation_error", "الباقة المحدّدة غير موجودة.", status=422,
                        details={"field": "plan_id"})
        plan_name = plan_name or _plan.name
    if router_id is not None and db().execute(
            "SELECT 1 FROM nas_devices WHERE tenant_id = ? AND id = ?",
            (_tid(), router_id)).fetchone() is None:
        return fail("validation_error", "جهاز الشبكة المحدّد غير موجود.", status=422,
                    details={"field": "router_id"})
    if payment_gateway_id is not None and db().execute(
            "SELECT 1 FROM payment_gateways WHERE tenant_id = ? AND id = ?",
            (_tid(), payment_gateway_id)).fetchone() is None:
        return fail("validation_error", "بوّابة الدفع المحدّدة غير موجودة.", status=422,
                    details={"field": "payment_gateway_id"})
    invoice = Invoice(
        id=None,
        tenant_id=_tid(),
        invoice_number=str(body.get("invoice_number") or ""),
        subscriber_id=subscriber_id,
        username=username,
        amount=amount,
        admin_id=int(getattr(g, "admin_id", 0) or 0),
        plan_id=plan_id,
        plan_name=plan_name,
        service_type=str(body.get("service_type") or "Hotspot"),
        router_id=router_id,
        direction=str(body.get("direction") or "charge"),
        balance_before=balance_before,
        balance_after=balance_after,
        recharged_on=recharged_on or datetime.utcnow(),
        expiration_at=expiration_at,
        payment_method=str(body.get("payment_method") or "cash"),
        payment_gateway_id=payment_gateway_id,
        status=str(body.get("status") or "paid"),
        note=str(body.get("note") or ""),
    )
    if invoice.status not in INVOICE_STATUSES:
        return fail("validation_error", "حالة الفاتورة غير صحيحة.", status=422)
    return ok(_item(invoices_repo.create(invoice)), status=201)


def update_status(invoice_id: int):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):  # [1] / "x" → .get() was a 500 (R08 NEW-4)
        return fail("validation_error", "جسم الطلب يجب أن يكون كائن JSON.", status=422)
    status = str(body.get("status") or "").strip()
    if status not in INVOICE_STATUSES:
        return fail("validation_error", "حالة الفاتورة غير صحيحة.", status=422)
    if not invoices_repo.get(_tid(), invoice_id):
        return fail("not_found", "الفاتورة غير موجودة.", status=404)
    invoices_repo.update_status(_tid(), invoice_id, status, note=str(body.get("note") or ""))
    return ok(_item(invoices_repo.get(_tid(), invoice_id)))
