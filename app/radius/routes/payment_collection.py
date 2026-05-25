"""Admin web UI for Payment Collection Center."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos.payments_repo import (
    PaymentProofRepository,
    PaymentRequestRepository,
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
                currency=form.get("currency") or (existing.currency if existing else "ILS"),
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
            flash(f"Invalid payment setting: {exc}", "danger")
        else:
            flash("Payment collection settings saved.", "success")
        return redirect(url_for("radius.payment_collection_settings"))

    return render_template(
        "radius/payment_collection_settings.html",
        settings=settings,
    )


def payment_collection_requests():
    repo = PaymentRequestRepository()
    status = (request.args.get("status") or "").strip()
    purpose = (request.args.get("purpose") or "").strip()
    payer_type = (request.args.get("payer_type") or "").strip()
    try:
        items = repo.list(_tid(), status=status, purpose=purpose, payer_type=payer_type)
    except ValueError as exc:
        flash(f"Invalid filter: {exc}", "warning")
        items = []
    return render_template(
        "radius/payment_collection_requests.html",
        items=items,
        status=status,
        purpose=purpose,
        payer_type=payer_type,
    )


def payment_collection_request_detail(request_id: int):
    item = PaymentRequestRepository().get(_tid(), request_id)
    if not item:
        flash("Payment request not found.", "warning")
        return redirect(url_for("radius.payment_collection_requests"))
    proofs = PaymentProofRepository().list_for_request(request_id)
    return render_template(
        "radius/payment_collection_request_detail.html",
        item=item,
        proofs=proofs,
    )


def payment_collection_review_queue_web():
    items = PaymentRequestRepository().list_for_review(_tid())
    return render_template("radius/payment_collection_review_queue.html", items=items)


def _reviewable(request_id: int):
    repo = PaymentRequestRepository()
    item = repo.get(_tid(), request_id)
    if not item:
        flash("Payment request not found.", "warning")
        return None, None
    if item["status"] not in {"proof_submitted", "under_review"}:
        flash("Payment request is not reviewable.", "warning")
        return None, item
    proof = PaymentProofRepository().latest_for_request(request_id)
    if not proof:
        flash("No proof submitted.", "warning")
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
        flash("Payment approved manually. Service activation is still pending later slices.", "success")
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
        flash("Payment proof rejected.", "info")
    return redirect(url_for("radius.payment_collection_request_detail", request_id=request_id))
