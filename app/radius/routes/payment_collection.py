"""Admin web UI for Payment Collection Center."""
from __future__ import annotations
from ..core.system_config import default_currency

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos.payments_repo import (
    PaymentCollectionLedgerRepository,
    PaymentProofRepository,
    PaymentReconciliationRepository,
    PaymentRequestRepository,
    PaymentServiceApplyRepository,
    PaymentSettingsRepository,
    PaymentTransactionRepository,
)


def _tid() -> int:
    return DEFAULT_TENANT_ID


def register_payment_collection_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/payments/settings",
        "payment_collection_settings",
        payment_collection_settings,
        methods=["GET", "POST"],
    )
    bp.add_url_rule(
        "/payments/requests",
        "payment_collection_requests",
        payment_collection_requests,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/payments/requests/<int:request_id>",
        "payment_collection_request_detail",
        payment_collection_request_detail,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/payments/review-queue",
        "payment_collection_review_queue_web",
        payment_collection_review_queue_web,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/payments/reconciliation",
        "payment_collection_reconciliation_web",
        payment_collection_reconciliation_web,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/payments/requests/<int:request_id>/approve",
        "payment_collection_approve_web",
        payment_collection_approve_web,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/payments/requests/<int:request_id>/reject",
        "payment_collection_reject_web",
        payment_collection_reject_web,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/payments/requests/<int:request_id>/apply-service",
        "payment_collection_apply_service_web",
        payment_collection_apply_service_web,
        methods=["POST"],
    )


def payment_collection_settings():
    repo = PaymentSettingsRepository()
    settings = repo.get(_tid())
    if request.method == "POST":
        form = request.form
        existing = settings
        try:
            repo.upsert(
                tenant_id=_tid(),
                provider=form.get("provider") or (existing.provider if existing else "manual_wallet"),
                enabled=bool(form.get("enabled")),
                wallet_number=form.get("wallet_number") or "",
                wallet_owner_name=form.get("wallet_owner_name") or "",
                currency=form.get("currency") or (existing.currency if existing else default_currency()),
                confirmation_mode=form.get("confirmation_mode") or "manual",
                auto_apply=bool(form.get("auto_apply")),
                allow_cards=bool(form.get("allow_cards")),
                allow_monthly_subscriptions=bool(form.get("allow_monthly_subscriptions")),
                allow_distributor_payments=bool(form.get("allow_distributor_payments")),
                min_amount=float(form["min_amount"]) if form.get("min_amount") else None,
                max_amount=float(form["max_amount"]) if form.get("max_amount") else None,
                payment_request_ttl_minutes=int(form["payment_request_ttl_minutes"])
                if form.get("payment_request_ttl_minutes") else None,
            )
        except ValueError as exc:
            flash(f"إعداد دفع غير صالح: {exc}", "danger")
        else:
            flash("تم حفظ إعدادات تحصيل الدفعات.", "success")
        return redirect(url_for("radius.collection_hub", tab="settings"))

    # GET: the settings form now lives in a modal on the collection hub.
    return redirect(url_for("radius.collection_hub", tab="settings"))


def payment_collection_requests():
    # Consolidated into the collection hub (Hub 2). Redirect keeps the
    # old URL + filters working.
    args = {"tab": "requests"}
    for key in ("status", "purpose", "payer_type"):
        val = (request.args.get(key) or "").strip()
        if val:
            args[key] = val
    return redirect(url_for("radius.collection_hub", **args))


def payment_collection_request_detail(request_id: int):
    item = PaymentRequestRepository().get(_tid(), request_id)
    if not item:
        flash("طلب الدفع غير موجود.", "warning")
        return redirect(url_for("radius.payment_collection_requests"))
    proofs = PaymentProofRepository().list_for_request(request_id)
    apply_attempts = PaymentServiceApplyRepository().list_for_request(
        tenant_id=_tid(),
        request_id=request_id,
    )
    return render_template(
        "radius/payment_collection_request_detail.html",
        item=item,
        proofs=proofs,
        apply_attempts=apply_attempts,
    )


def payment_collection_review_queue_web():
    # Consolidated into the collection hub (Hub 2).
    return redirect(url_for("radius.collection_hub", tab="review"))


def payment_collection_reconciliation_web():
    # Consolidated into the collection hub (Hub 2).
    return redirect(url_for("radius.collection_hub", tab="reconciliation"))


def _reviewable(request_id: int):
    repo = PaymentRequestRepository()
    item = repo.get(_tid(), request_id)
    if not item:
        flash("طلب الدفع غير موجود.", "warning")
        return None, None
    if item["status"] not in {"proof_submitted", "under_review"}:
        flash("طلب الدفع غير قابل للمراجعة.", "warning")
        return None, item
    proof = PaymentProofRepository().latest_for_request(request_id)
    if not proof:
        flash("لم يتم إرسال إثبات دفع.", "warning")
        return None, item
    return proof, item


def payment_collection_approve_web(request_id: int):
    proof, item = _reviewable(request_id)
    if proof and item:
        PaymentProofRepository().mark_reviewed(
            proof_id=proof["id"],
            reviewed_by=None,
            review_status="approved",
            review_note=(request.form.get("review_note") or "").strip(),
        )
        PaymentRequestRepository().update_status(_tid(), request_id, "paid")
        PaymentTransactionRepository().create(
            payment_request_id=request_id,
            amount=item["amount"],
            currency=item["currency"],
            status="paid_manual",
            raw_payload={"proof_id": proof["id"], "review": "approved_manual_web"},
        )
        PaymentCollectionLedgerRepository().apply_paid_request(
            tenant_id=_tid(),
            request_id=request_id,
            actor="admin-web",
        )
        flash("تم قبول الدفع يدويًا وترحيله إلى دفتر القيود. تفعيل الخدمة ينتظر اعتماد الإدارة من صفحة الطلب.", "success")
    return redirect(url_for("radius.payment_collection_request_detail", request_id=request_id))


def payment_collection_reject_web(request_id: int):
    proof, _item = _reviewable(request_id)
    if proof:
        PaymentProofRepository().mark_reviewed(
            proof_id=proof["id"],
            reviewed_by=None,
            review_status="rejected",
            review_note=(request.form.get("review_note") or "").strip(),
        )
        PaymentRequestRepository().update_status(_tid(), request_id, "rejected")
        flash("تم رفض إثبات الدفع.", "info")
    return redirect(url_for("radius.payment_collection_request_detail", request_id=request_id))


def payment_collection_apply_service_web(request_id: int):
    try:
        # ملاحظة: simulate_failure أداة اختبار عبر API فقط ولا تُمرَّر من الويب.
        PaymentServiceApplyRepository().apply_paid_request(
            tenant_id=_tid(),
            request_id=request_id,
            actor="admin-web",
        )
    except ValueError as exc:
        if str(exc) == "status":
            flash("لا يمكن تطبيق سوى الطلبات المدفوعة.", "warning")
        else:
            flash(f"فشل تطبيق الخدمة: {exc}", "danger")
    else:
        flash("تم تسجيل تطبيق الخدمة بدون أي إجراء مباشر على RADIUS أو CoA أو MikroTik.", "success")
    return redirect(url_for("radius.payment_collection_request_detail", request_id=request_id))
