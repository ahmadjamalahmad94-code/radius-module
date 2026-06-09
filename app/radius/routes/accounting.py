"""Web admin accounting screens for payments, loans, and ledger."""
from __future__ import annotations

import json

from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for

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
    # عرض صفوف لقطة محفوظة — تستهلكه نافذة «عرض اللقطة» العائمة (fetch).
    bp.add_url_rule(
        "/finance/reports/snapshot/<int:snapshot_id>.json",
        "finance_reports_snapshot_json",
        finance_reports_snapshot_json,
        methods=["GET"],
    )
    bp.add_url_rule("/users/<username>/finance", "users_finance", users_finance, methods=["GET"])
    bp.add_url_rule("/users/<username>/payments", "users_payment_create", users_payment_create, methods=["POST"])
    bp.add_url_rule("/users/payments-bulk", "users_payment_create_bulk", users_payment_create_bulk, methods=["POST"])
    bp.add_url_rule("/users/<username>/loans", "users_loan_create", users_loan_create, methods=["POST"])
    bp.add_url_rule("/users/loans-bulk", "users_loan_create_bulk", users_loan_create_bulk, methods=["POST"])
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


def _bulk_usernames() -> list[str]:
    """قراءة أسماء المشتركين المحدَّدين من حقل `usernames` المتكرر —
    نفس نمط مسارات الدفعات الجماعية في users.py (تسامح + إزالة تكرار)."""
    raw = request.form.getlist("usernames")
    if len(raw) == 1 and "," in raw[0]:
        raw = raw[0].split(",")
    seen: set[str] = set()
    usernames: list[str] = []
    for name in raw:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            usernames.append(name)
    return usernames


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
    sub = _subscriber(username)
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
    # الرصيد السالب يعني دينًا على المشترك. إذا اختار الموظف تسويته من الدفعة،
    # نخصم جزءًا من المبلغ بعد السلف وبحد الدين نفسه؛ والباقي فقط يشتري مدة.
    # التنفيذ الفعلي يحدث بعد نجاح تسجيل الدفعة، مثل مسار السلف.
    cur_balance = float(getattr(sub, "balance", 0) or 0)
    balance_settle = 0.0
    if _truthy("settle_balance") and cur_balance < 0:
        remaining = max(amount_f - settled_total, 0.0)
        balance_settle = round(min(remaining, -cur_balance), 2)
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
        "balance_settled_total": balance_settle,
    }
    try:
        payment = _svc().create_payment(body, actor=_actor())
    except RadiusError as e:
        if _wants_json():
            return jsonify({"ok": False, "error": e.message}), getattr(e, "http_status", 400)
        flash(e.message, "error")
        return redirect(url_for("radius.users_finance", username=username))
    except Exception as e:  # noqa: BLE001 — surface the real reason, don't 500 silently
        current_app.logger.exception("payment create failed for %s", username)
        reason = f"خطأ غير متوقع أثناء تسجيل الدفعة: {e}"
        if _wants_json():
            return jsonify({"ok": False, "error": reason}), 500
        flash(reason, "error")
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
    # ثم نسوي دين الرصيد السالب بإرجاع الرصيد باتجاه الصفر وتسجيل قيد موازن.
    debt_done = 0.0
    if balance_settle > 0:
        try:
            debt_done = float(get_users_service().apply_payment_to_balance(
                actor=_actor(), username=username, amount=balance_settle,
            ))
        except RadiusError:
            debt_done = 0.0
    result = payment.get("activation_result") or {}
    settle_note = f" وتسوية سلف بقيمة {settled_done:.2f}" if settled_done > 0 else ""
    debt_note = f" وسداد دين بقيمة {debt_done:.2f}" if debt_done > 0 else ""
    extra = f"{settle_note}{debt_note}"
    if result.get("dry_run"):
        msg, cat = f"تم تسجيل الدفعة كمعاينة بدون تطبيق على RADIUS{extra}.", "warning"
    elif result.get("applied_to_radius"):
        msg, cat = f"تم تسجيل الدفعة وتطبيق مدة الاستحقاق على الحساب{extra}.", "success"
    else:
        msg, cat = f"تم تسجيل الدفعة في السجل المالي{extra}.", "success"
    if _wants_json():
        return jsonify({"ok": True, "message": msg})
    flash(msg, cat)
    return redirect(url_for("radius.users_finance", username=username))


def users_payment_create_bulk():
    """تسجيل دفعة نقدية لعدة مشتركين محدَّدين في POST واحد — المبلغ لكل مشترك.

    يعيد استخدام نفس مسار التسجيل الفردي — create_payment() — لكل اسم
    (نفس التدقيق والتطبيق على RADIUS لكل مشترك على حدة). تسوية السلف
    ودين الرصيد (loan_actions / settle_balance) ميزات فردية فتُتجاهل هنا.
    الأسماء الفاشلة تُتخطّى وتُعرض في الملخص دون إيقاف الدفعة.
    """
    usernames = _bulk_usernames()
    if not usernames:
        flash("لم يتم تحديد أي مشترك لتسجيل الدفعة.", "warning")
        return redirect(url_for("radius.users_list"))
    try:
        amount_f = float(_field("amount") or 0)
    except (TypeError, ValueError):
        amount_f = 0.0
    if amount_f <= 0:
        flash("قيمة الدفعة غير صحيحة.", "error")
        return redirect(url_for("radius.users_list"))

    svc = _svc()
    actor = _actor()
    done = 0
    failed: list[str] = []
    for name in usernames:
        body = {
            "username": name,
            "amount": _field("amount"),
            "currency": _field("currency") or default_currency(),
            "method": _field("method") or "cash",
            "notes": _field("notes"),
            "rounding_mode": _field("rounding_mode") or "floor",
            "apply_to_radius": _truthy("apply_to_radius"),
            "dry_run": _truthy("dry_run"),
        }
        try:
            svc.create_payment(body, actor=actor)
            done += 1
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            current_app.logger.exception("bulk payment create failed for %s", name)
            failed.append(name)

    if done:
        flash(f"تم تسجيل دفعة {amount_f:.2f} لكل مشترك من {done} مشترك وتطبيقها على حساباتهم.", "success")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        flash(f"تعذّر تسجيل الدفعة لـ {len(failed)} مشترك: {preview}", "warning")
    return redirect(url_for("radius.users_list"))


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
            return jsonify({"ok": False, "error": e.message}), getattr(e, "http_status", 400)
        flash(e.message, "error")
    except Exception as e:  # noqa: BLE001 — never swallow the reason; the operator must see it
        current_app.logger.exception("loan create failed for %s", username)
        reason = f"خطأ غير متوقع أثناء منح السلفة: {e}"
        if _wants_json():
            return jsonify({"ok": False, "error": reason}), 500
        flash(reason, "error")
    return redirect(url_for("radius.users_finance", username=username))


def users_loan_create_bulk():
    """إضافة سلفة لعدة مشتركين محدَّدين في POST واحد — المدة لكل مشترك.

    يعيد استخدام نفس مسار المنح الفردي — create_loan() — لكل اسم؛ في وضع
    الدين (price_from_days=1) تُحتسب قيمة السلفة لكل مشترك من سعره الفعلي
    (العرض/المخصّص) داخل الخدمة نفسها. النافذة تُرسل عبر fetch فنرد JSON
    بملخص الدفعة. الأسماء الفاشلة تُتخطّى دون إيقاف الدفعة.
    """
    usernames = _bulk_usernames()
    if not usernames:
        if _wants_json():
            return jsonify({"ok": False, "error": "لم يتم تحديد أي مشترك لمنح السلفة."}), 400
        flash("لم يتم تحديد أي مشترك لمنح السلفة.", "warning")
        return redirect(url_for("radius.users_list"))

    svc = _svc()
    actor = _actor()
    done = 0
    failed: list[str] = []
    for name in usernames:
        body = {
            "username": name,
            "hours": _field("hours"),
            "days": _field("days"),
            "duration_minutes": _field("duration_minutes"),
            # في وضع الدين تُحتسب القيمة داخل الخدمة لكل مشترك من سعره؛
            # لذا لا نمرّر القيمة المحسوبة في الواجهة (خاصة بصفٍّ واحد).
            "amount": 0,
            "price_from_days": _truthy("price_from_days"),
            "currency": _field("currency") or default_currency(),
            "reason": _field("reason"),
            "apply_to_radius": _truthy("apply_to_radius"),
            "dry_run": _truthy("dry_run"),
        }
        try:
            svc.create_loan(body, actor=actor)
            done += 1
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            current_app.logger.exception("bulk loan create failed for %s", name)
            failed.append(name)

    parts = []
    if done:
        parts.append(f"تم تسجيل السلفة لـ {done} مشترك (المدة لكلٍّ منهم).")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        parts.append(f"تعذّر المنح لـ {len(failed)} مشترك: {preview}")
    msg = " ".join(parts) or "لم يتم منح أي سلفة."
    if _wants_json():
        if done:
            return jsonify({"ok": True, "message": msg})
        return jsonify({"ok": False, "error": msg}), 400
    flash(msg, "success" if done else "error")
    return redirect(url_for("radius.users_list"))


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


def _snapshot_date(name: str) -> str:
    """قراءة حقل تاريخ اختياري بصيغة ISO (YYYY-MM-DD) من نافذة اللقطة.

    الحقل اختياري للحفاظ على التوافق: النماذج القديمة بلا تواريخ تظل تعمل
    (لقطة كاملة بلا فلترة). أي قيمة غير صالحة تُرفض برسالة عربية واضحة.
    """
    raw = _field(name)
    if not raw:
        return ""
    try:
        from datetime import date
        date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise RadiusValidationError("صيغة التاريخ غير صحيحة — استخدم سنة-شهر-يوم.")
    return raw


def finance_reports_snapshot():
    report_type = _field("report_type") or "daily"
    if report_type not in _REPORTS:
        report_type = "daily"
    try:
        # نطاق من/إلى وملاحظة اختياريان (نافذة «حفظ لقطة ثابتة» العائمة) —
        # غيابهما يعني لقطة كاملة كما كان السلوك القديم تمامًا.
        date_from = _snapshot_date("date_from")
        date_to = _snapshot_date("date_to")
        if date_from and date_to and date_from > date_to:
            raise RadiusValidationError("تاريخ «من» يجب أن يسبق تاريخ «إلى».")
        snapshot = _svc().create_report_snapshot(
            report_type=report_type,
            actor=_actor(),
            date_from=date_from,
            date_to=date_to,
            note=_field("note"),
            parameters={"web_route": "finance_reports"},
        )
        rng = f" للفترة {date_from or '…'} ← {date_to or '…'}" if (date_from or date_to) else ""
        flash(f"تم حفظ لقطة ثابتة للتقرير #{snapshot['id']}{rng}.", "success")
    except RadiusValidationError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.accounting_hub", tab="reports", type=report_type))


def finance_reports_snapshot_json(snapshot_id: int):
    """صفوف لقطة محفوظة كـ JSON — تعرضها نافذة «عرض اللقطة» العائمة.

    البيانات مأخوذة من result_json المجمّد وقت الإنشاء (لا إعادة احتساب)،
    فاللقطة تبقى مرجعًا ثابتًا حتى لو تغيّر دفتر القيود لاحقًا.
    """
    try:
        snapshot = _svc().get_report_snapshot(snapshot_id)
    except RadiusValidationError as e:
        return jsonify({"ok": False, "error": e.message}), 404
    result = snapshot.get("result") or {}
    return jsonify({
        "ok": True,
        "id": snapshot.get("id"),
        "report_type": snapshot.get("report_type"),
        "report_label": _REPORTS.get(snapshot.get("report_type"), snapshot.get("report_type")),
        "created_at": snapshot.get("created_at"),
        "created_by": snapshot.get("created_by"),
        "date_from": snapshot.get("date_from") or result.get("date_from") or "",
        "date_to": snapshot.get("date_to") or result.get("date_to") or "",
        "note": result.get("note") or (snapshot.get("parameters") or {}).get("note") or "",
        "count": result.get("count", len(result.get("items") or [])),
        "total": result.get("total"),
        "items": result.get("items") or [],
    })
