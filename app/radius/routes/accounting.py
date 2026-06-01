"""Web admin accounting screens for payments, loans, and ledger."""
from __future__ import annotations

import json

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError, RadiusValidationError
from ..core.system_config import default_currency
from ..services.accounting import service_from_context
from ..services.users import get_users_service


def users_open_loans(username: str):
    """JSON response for open subscriber loans used by the payment modal."""
    sub = _subscriber(username)
    loans = _svc().open_loans_for(subscriber_id=sub.id)
    return jsonify({
        "ok": True,
        "loans": [
            {
                "id": ln["id"],
                "amount": float(ln.get("amount") or 0),
                "days": ln.get("days", 0),
                "minutes": int(ln.get("duration_minutes") or 0),
                "currency": ln.get("currency") or default_currency(),
                "reason": ln.get("reason") or "",
            }
            for ln in loans
        ],
    })


def register_accounting_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/finance/ledger", "finance_ledger", finance_ledger, methods=["GET"])
    bp.add_url_rule("/finance/ledger/void", "finance_ledger_void", finance_ledger_void, methods=["POST"])
    bp.add_url_rule("/finance/reports", "finance_reports", finance_reports, methods=["GET"])
    bp.add_url_rule(
        "/finance/reports/export.csv",
        "finance_reports_export_csv",
        finance_reports_export_csv,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/finance/reports/export.xlsx",
        "finance_reports_export_xlsx",
        finance_reports_export_xlsx,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/finance/reports/export.pdf",
        "finance_reports_export_pdf",
        finance_reports_export_pdf,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/finance/reports/snapshot",
        "finance_reports_snapshot",
        finance_reports_snapshot,
        methods=["POST"],
    )
    bp.add_url_rule("/users/<username>/finance", "users_finance", users_finance, methods=["GET"])
    bp.add_url_rule("/users/<username>/payments", "users_payment_create", users_payment_create, methods=["POST"])
    bp.add_url_rule("/users/<username>/loans", "users_loan_create", users_loan_create, methods=["POST"])
    bp.add_url_rule(
        "/users/<username>/loans/<int:loan_id>/settle",
        "users_loan_settle",
        users_loan_settle,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/users/<username>/open-loans",
        "users_open_loans",
        users_open_loans,
        methods=["GET"],
    )


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _svc():
    return service_from_context()


def _field(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _truthy(name: str) -> bool:
    return request.form.get(name, "") in {"1", "on", "true", "yes"}


def _parse_loan_actions() -> list[dict]:
    """Parse the modal's loan_actions field — a JSON list of {loan_id, action}
    where action ∈ settle|writeoff (defer/omitted = leave open)."""
    raw = (request.form.get("loan_actions") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _wants_json() -> bool:
    """True when the request came from the floating modal (fetch) and expects a
    JSON reply instead of a redirect."""
    return (
        request.headers.get("X-Requested-With") == "fetch"
        or "application/json" in (request.headers.get("Accept") or "")
    )


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
    basis = svc.price_basis(sub)
    return render_template(
        "radius/users_finance.html",
        sub=sub,
        payments=payments,
        loans=loans,
        ledger=ledger,
        eff_price=basis["price"],
        plan_minutes=basis["minutes"],
        price_custom=basis["custom"],
    )


def users_payment_create(username: str):
    _subscriber(username)
    svc = _svc()
    actions = _parse_loan_actions()
    # Validate the amount BEFORE touching loans so we never settle a debt and
    # then fail to record the payment.
    try:
        amount_f = float(_field("amount") or 0)
    except (TypeError, ValueError):
        amount_f = 0.0
    if amount_f <= 0:
        if _wants_json():
            return jsonify({"ok": False, "error": "قيمة الدفعة غير صحيحة."}), 400
        flash("قيمة الدفعة غير صحيحة.", "error")
        return redirect(url_for("radius.users_finance", username=username))
    # PREVIEW the settle total (read-only) so the payment is recorded FIRST.
    # Loans are only actually settled AFTER the payment succeeds — a failed
    # payment must never leave orphaned (already-settled) loans.
    settled_total = svc.settle_preview_total(actions) if actions else 0.0
    body = {
        "username": username,
        "amount": _field("amount"),
        "currency": _field("currency") or default_currency(),
        "method": _field("method") or "cash",
        "custom_price": _field("custom_price"),
        "discount_amount": _field("discount_amount") or 0,
        "discount_reason": _field("discount_reason"),
        "rounding_mode": _field("rounding_mode") or "floor",
        "notes": _field("notes"),
        "apply_to_radius": _truthy("apply_to_radius"),
        "dry_run": _truthy("dry_run"),
        "loan_settled_total": settled_total,
    }
    try:
        payment = _svc().create_payment(body, actor=_actor())
    except RadiusError as e:
        if _wants_json():
            return jsonify({"ok": False, "error": e.message}), 400
        flash(e.message, "error")
        return redirect(url_for("radius.users_finance", username=username))
    # Payment recorded — NOW apply the loan resolutions (settle/writeoff). If this
    # best-effort step fails, the payment still stands and the loans simply stay
    # open (operator can re-settle); no money lost, nothing orphaned.
    settled_done = 0.0
    if actions:
        try:
            settled_done = float(svc.resolve_loan_actions(actions, actor=_actor()).get("settled_total") or 0)
        except RadiusError:
            settled_done = 0.0
    result = payment.get("activation_result") or {}
    settle_note = f" وتسوية سلف بقيمة {settled_done:.2f}" if settled_done > 0 else ""
    if result.get("dry_run"):
        msg, cat = f"تم تسجيل الدفعة كمعاينة بدون تطبيق على RADIUS{settle_note}.", "warning"
    elif result.get("applied_to_radius"):
        msg, cat = f"تم تسجيل الدفعة وتطبيق مدة الاستحقاق على الحساب{settle_note}.", "success"
    else:
        msg, cat = f"تم تسجيل الدفعة في السجل المالي{settle_note}.", "success"
    if _wants_json():
        return jsonify({"ok": True, "message": msg})
    flash(msg, cat)
    return redirect(url_for("radius.users_finance", username=username))


def users_loan_create(username: str):
    _subscriber(username)
    body = {
        "username": username,
        "hours": _field("hours"),
        "days": _field("days"),
        "duration_minutes": _field("duration_minutes"),
        "amount": _field("amount") or 0,
        "price_from_days": _truthy("price_from_days"),
        "currency": _field("currency") or default_currency(),
        "reason": _field("reason"),
        "apply_to_radius": _truthy("apply_to_radius"),
        "dry_run": _truthy("dry_run"),
    }
    try:
        loan = _svc().create_loan(body, actor=_actor())
        result = loan.get("activation_result") or {}
        if result.get("dry_run"):
            msg = "تم تسجيل السلفة كمعاينة بدون تطبيق على RADIUS."
        elif result.get("applied_to_radius"):
            msg = "تم تسجيل السلفة وتطبيق نافذة التفعيل المؤقتة."
        else:
            msg = "تم تسجيل السلفة بدون تطبيق فوري على RADIUS."
        if _wants_json():
            return jsonify({"ok": True, "message": msg})
        flash(msg, "success")
    except RadiusError as e:
        if _wants_json():
            return jsonify({"ok": False, "error": e.message}), 400
        flash(e.message, "error")
    return redirect(url_for("radius.users_finance", username=username))


def users_loan_settle(username: str, loan_id: int):
    _subscriber(username)
    body = {
        "amount": _field("amount"),
        "currency": _field("currency") or default_currency(),
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
    args = request.args.to_dict(flat=True)
    args["tab"] = "ledger"
    return redirect(url_for("radius.accounting_hub", **args))


def finance_ledger_legacy_context():
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
    return redirect(url_for("radius.accounting_hub", tab="ledger"))


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
    args = request.args.to_dict(flat=True)
    args["tab"] = "reports"
    return redirect(url_for("radius.accounting_hub", **args))


def finance_reports_legacy_context():
    report_type = (request.args.get("type") or "daily").strip()
    if report_type not in _REPORTS:
        report_type = "daily"
    try:
        items = _svc().reports(report_type=report_type)
        snapshots = _svc().list_report_snapshots(report_type=report_type, limit=10)
    except RadiusValidationError as e:
        flash(e.message, "error")
        items = []
        snapshots = []
    return render_template(
        "radius/accounting_reports.html",
        report_type=report_type,
        report_label=_REPORTS[report_type],
        reports=_REPORTS,
        items=items,
        snapshots=snapshots,
    )


def finance_reports_export_csv():
    report_type = (request.args.get("type") or "daily").strip()
    if report_type not in _REPORTS:
        report_type = "daily"
    try:
        csv_text = _svc().report_csv(report_type=report_type)
    except RadiusValidationError as e:
        flash(e.message, "error")
        return redirect(url_for("radius.accounting_hub", tab="reports", type=report_type))
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="hoberadius-{report_type}.csv"',
        },
    )


def finance_reports_export_xlsx():
    report_type = (request.args.get("type") or "daily").strip()
    if report_type not in _REPORTS:
        report_type = "daily"
    try:
        xlsx_bytes = _svc().report_xlsx(report_type=report_type)
    except RadiusValidationError as e:
        flash(e.message, "error")
        return redirect(url_for("radius.accounting_hub", tab="reports", type=report_type))
    return Response(
        xlsx_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="hoberadius-{report_type}.xlsx"',
        },
    )


def finance_reports_export_pdf():
    report_type = (request.args.get("type") or "daily").strip()
    if report_type not in _REPORTS:
        report_type = "daily"
    try:
        pdf_bytes = _svc().report_pdf(report_type=report_type)
    except RadiusValidationError as e:
        flash(e.message, "error")
        return redirect(url_for("radius.accounting_hub", tab="reports", type=report_type))
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="hoberadius-{report_type}.pdf"',
        },
    )


def finance_reports_snapshot():
    report_type = _field("report_type") or "daily"
    if report_type not in _REPORTS:
        report_type = "daily"
    try:
        snapshot = _svc().create_report_snapshot(
            report_type=report_type,
            actor=_actor(),
            parameters={"web_route": "finance_reports"},
        )
        flash(f"تم حفظ لقطة ثابتة للتقرير #{snapshot['id']}.", "success")
    except RadiusValidationError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.accounting_hub", tab="reports", type=report_type))
