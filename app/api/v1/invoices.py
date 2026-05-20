from __future__ import annotations

from datetime import datetime

from flask import Blueprint, g, request

from ...radius.core.types_saas import INVOICE_STATUSES, Invoice
from ...radius.db.repos import invoices_repo
from ..auth import require_api_token
from ..responses import fail, ok


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
        raise ValueError("date values must be ISO strings")
    return datetime.fromisoformat(raw.replace("Z", ""))


def _item(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "subscriber_id": invoice.subscriber_id,
        "username": invoice.username,
        "amount": invoice.amount,
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
        return fail("not_found", "invoice not found", status=404)
    return ok(_item(invoice))


def create_invoice():
    body = request.get_json(silent=True) or {}
    try:
        subscriber_id = int(body.get("subscriber_id") or 0)
        amount = float(body.get("amount") or 0)
        recharged_on = _dt(body.get("recharged_on"))
        expiration_at = _dt(body.get("expiration_at"))
    except (TypeError, ValueError) as exc:
        return fail("validation_error", str(exc), status=422)
    username = str(body.get("username") or "").strip()
    if subscriber_id <= 0 or amount < 0 or not username:
        return fail("validation_error", "subscriber_id, username and amount are required", status=422)
    invoice = Invoice(
        id=None,
        tenant_id=_tid(),
        invoice_number=str(body.get("invoice_number") or ""),
        subscriber_id=subscriber_id,
        username=username,
        amount=amount,
        admin_id=int(getattr(g, "admin_id", 0) or 0),
        plan_id=int(body["plan_id"]) if body.get("plan_id") not in (None, "") else None,
        plan_name=str(body.get("plan_name") or ""),
        service_type=str(body.get("service_type") or "Hotspot"),
        router_id=int(body["router_id"]) if body.get("router_id") not in (None, "") else None,
        direction=str(body.get("direction") or "charge"),
        balance_before=float(body.get("balance_before") or 0),
        balance_after=float(body.get("balance_after") or 0),
        recharged_on=recharged_on,
        expiration_at=expiration_at,
        payment_method=str(body.get("payment_method") or "cash"),
        payment_gateway_id=int(body["payment_gateway_id"]) if body.get("payment_gateway_id") not in (None, "") else None,
        status=str(body.get("status") or "paid"),
        note=str(body.get("note") or ""),
    )
    if invoice.status not in INVOICE_STATUSES:
        return fail("validation_error", "invalid invoice status", status=422)
    return ok(_item(invoices_repo.create(invoice)), status=201)


def update_status(invoice_id: int):
    body = request.get_json(silent=True) or {}
    status = str(body.get("status") or "").strip()
    if status not in INVOICE_STATUSES:
        return fail("validation_error", "invalid invoice status", status=422)
    if not invoices_repo.get(_tid(), invoice_id):
        return fail("not_found", "invoice not found", status=404)
    invoices_repo.update_status(_tid(), invoice_id, status, note=str(body.get("note") or ""))
    return ok(_item(invoices_repo.get(_tid(), invoice_id)))
