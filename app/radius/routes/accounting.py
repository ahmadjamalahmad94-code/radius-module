"""Web admin accounting screens for payments, loans, and ledger."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError, RadiusValidationError
from ..services.accounting import service_from_context
from ..services.users import get_users_service


def register_accounting_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/finance/ledger", "finance_ledger", finance_ledger, methods=["GET"])
    bp.add_url_rule("/finance/ledger/void", "finance_ledger_void", finance_ledger_void, methods=["POST"])
    bp.add_url_rule("/finance/reports", "finance_reports", finance_reports, methods=["GET"])
    bp.add_url_rule("/users/<username>/finance", "users_finance", users_finance, methods=["GET"])
    bp.add_url_rule("/users/<username>/payments", "users_payment_create", users_payment_create, methods=["POST"])
    bp.add_url_rule("/users/<username>/loans", "users_loan_create", users_loan_create, methods=["POST"])
    bp.add_url_rule(
        "/users/<username>/loans/<int:loan_id>/settle",
        "users_loan_settle",
        users_loan_settle,
        methods=["POST"],
    )


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _svc():
    return service_from_context()


def _field(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _truthy(name: str) -> bool:
    return request.form.get(name, "") in {"1", "on", "true", "yes"}


def _subscriber(username: str):
    try:
        return get_users_service().get(username)
    except RadiusError:
        abort(404)


def users_finance(username: str):
    sub = _subscriber(username)
    svc = _svc()
    subscriber_id = sub.id
    payments = svc.list_payments(subscriber_id=subscriber_id, limit=100)
    loans = svc.list_loans(subscriber_id=subscriber_id, limit=100)
    ledger = svc.list_ledger(subscriber_id=subscriber_id, limit=150)
    return render_template(
        "radius/users_finance.html",
        sub=sub,
        payments=payments,
        loans=loans,
        ledger=ledger,
    )


def users_payment_create(username: str):
    _subscriber(username)
    body = {
        "username": username,
        "amount": _field("amount"),
        "currency": _field("currency") or "JOD",
        "method": _field("method") or "cash",
        "custom_price": _field("custom_price"),
        "discount_amount": _field("discount_amount") or 0,
        "discount_reason": _field("discount_reason"),
        "rounding_mode": _field("rounding_mode") or "floor",
        "notes": _field("notes"),
        "apply_to_radius": _truthy("apply_to_radius"),
        "dry_run": _truthy("dry_run"),
    }
    try:
        payment = _svc().create_payment(body, actor=_actor())
        result = payment.get("activation_result") or {}
        if result.get("dry_run"):
            flash("تم تسجيل الدفعة كتجربة فقط بدون تطبيق على RADIUS.", "warning")
        elif result.get("applied_to_radius"):
            flash("تم تسجيل الدفعة وتطبيق مدة الاستحقاق على الحساب.", "success")
        else:
            flash("تم تسجيل الدفعة في السجل المالي.", "success")
    except RadiusValidationError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_finance", username=username))


def users_loan_create(username: str):
    _subscriber(username)
    body = {
        "username": username,
        "hours": _field("hours"),
        "days": _field("days"),
        "duration_minutes": _field("duration_minutes"),
        "amount": _field("amount") or 0,
        "currency": _field("currency") or "JOD",
        "reason": _field("reason"),
        "apply_to_radius": _truthy("apply_to_radius"),
        "dry_run": _truthy("dry_run"),
    }
    try:
        loan = _svc().create_loan(body, actor=_actor())
        result = loan.get("activation_result") or {}
        if result.get("dry_run"):
            flash("تم تسجيل السلفة كتجربة فقط بدون تطبيق على RADIUS.", "warning")
        elif result.get("applied_to_radius"):
            flash("تم تسجيل السلفة وتطبيق نافذة التفعيل المؤقتة.", "success")
        else:
            flash("تم تسجيل السلفة بدون تطبيق فوري على RADIUS.", "success")
    except RadiusValidationError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_finance", username=username))


def users_loan_settle(username: str, loan_id: int):
    _subscriber(username)
    body = {
        "amount": _field("amount"),
        "currency": _field("currency") or "JOD",
        "method": _field("method") or "manual",
        "settlement_type": _field("settlement_type") or "manual",
        "notes": _field("notes"),
    }
    try:
        _svc().settle_loan(loan_id, body, actor=_actor())
        flash("تمت تسوية السلفة مع بقاء السجل المالي محفوظًا.", "success")
    except RadiusValidationError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_finance", username=username))


def finance_ledger():
    entry_type = (request.args.get("entry_type") or "").strip()
    subscriber_id = request.args.get("subscriber_id")
    try:
        items = _svc().list_ledger(
            entry_type=entry_type,
            subscriber_id=int(subscriber_id) if subscriber_id else None,
            limit=300,
        )
    except (ValueError, RadiusValidationError) as e:
        flash(getattr(e, "message", str(e)), "error")
        items = []
    return render_template(
        "radius/accounting_ledger.html",
        items=items,
        entry_type=entry_type,
        subscriber_id=subscriber_id or "",
    )


def finance_ledger_void():
    try:
        entry = _svc().void_ledger(
            entry_id=int(_field("entry_id") or 0),
            actor=_actor(),
            reason=_field("reason"),
        )
        flash(f"تم إنشاء قيد عكسي للقيد #{entry['reversal_of_entry_id']}.", "success")
    except (ValueError, RadiusValidationError) as e:
        flash(getattr(e, "message", str(e)), "error")
    return redirect(url_for("radius.finance_ledger"))


_REPORTS = {
    "daily": "مبيعات يومية",
    "monthly": "مبيعات شهرية",
    "yearly": "مبيعات سنوية",
    "subscriber_payments": "دفعات المستفيدين",
    "loans": "السلف",
    "activations": "التفعيلات",
    "card_sales": "مبيعات الكروت",
    "profit_loss": "ربح / خسارة",
    "distributor_debts": "ديون الموزعين",
}


def finance_reports():
    report_type = (request.args.get("type") or "daily").strip()
    if report_type not in _REPORTS:
        report_type = "daily"
    try:
        items = _svc().reports(report_type=report_type)
    except RadiusValidationError as e:
        flash(e.message, "error")
        items = []
    return render_template(
        "radius/accounting_reports.html",
        report_type=report_type,
        report_label=_REPORTS[report_type],
        reports=_REPORTS,
        items=items,
    )
