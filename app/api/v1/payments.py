"""Payments and partial payment API foundation."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusValidationError
from ...radius.db.helpers import json_load
from ...radius.db.repos.payments_repo import (
    CURRENCIES,
    PAYMENT_PURPOSES,
    PaymentCollectionLedgerRepository,
    PaymentProofRepository,
    PaymentReconciliationRepository,
    PaymentRequestRepository,
    PaymentServiceApplyRepository,
    PaymentSettings,
    PaymentSettingsRepository,
    PaymentTransactionRepository,
    PaymentWebhookEventRepository,
)
from ...radius.services.accounting import service_from_context
from ..access_control import current_distributor, deny_out_of_scope, subscriber_in_scope
from ..auth import require_api_token
from ..responses import fail, ok


_PAYMENT_ERROR_MESSAGES = {
    "amount": "المبلغ غير صالح.",
    "currency": "العملة غير مسموحة.",
    "purpose": "نوع طلب الدفع غير صالح.",
    "payer_type": "نوع الدافع غير صالح.",
    "limit": "قيمة limit يجب أن تكون رقمًا صحيحًا.",
    "offset": "قيمة offset يجب أن تكون رقمًا صحيحًا.",
    "payment_request": "طلب الدفع غير موجود.",
    "status": "حالة طلب الدفع غير صالحة.",
    "proof_type": "نوع إثبات الدفع غير صالح.",
    "reference_number": "رقم مرجع الدفع مطلوب.",
}


def _payment_error_message(message: object) -> str:
    text = str(message)
    return _PAYMENT_ERROR_MESSAGES.get(text, text)


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/payments/settings", "payment_collection_settings_get",
                    require_api_token(payment_collection_settings_get), methods=["GET"])
    bp.add_url_rule("/payments/settings", "payment_collection_settings_patch",
                    require_api_token(payment_collection_settings_patch), methods=["PATCH"])
    bp.add_url_rule("/payments/requests", "payment_collection_requests_list",
                    require_api_token(payment_collection_requests_list), methods=["GET"])
    bp.add_url_rule("/payments/requests", "payment_collection_requests_create",
                    require_api_token(payment_collection_requests_create), methods=["POST"])
    bp.add_url_rule("/payments/requests/<int:request_id>",
                    "payment_collection_requests_get", methods=["GET"],
                    view_func=require_api_token(payment_collection_requests_get))
    bp.add_url_rule("/payments/requests/<int:request_id>/instructions",
                    "payment_collection_request_instructions", methods=["GET"],
                    view_func=require_api_token(payment_collection_request_instructions))
    bp.add_url_rule("/payments/requests/<int:request_id>/proofs",
                    "payment_collection_submit_proof", methods=["POST"],
                    view_func=require_api_token(payment_collection_submit_proof))
    bp.add_url_rule("/admin/payments/review-queue",
                    "payment_collection_review_queue", methods=["GET"],
                    view_func=require_api_token(payment_collection_review_queue))
    bp.add_url_rule("/admin/payments/reconciliation",
                    "payment_collection_reconciliation", methods=["GET"],
                    view_func=require_api_token(payment_collection_reconciliation))
    bp.add_url_rule("/admin/payments/requests/<int:request_id>/approve",
                    "payment_collection_approve", methods=["POST"],
                    view_func=require_api_token(payment_collection_approve))
    bp.add_url_rule("/admin/payments/requests/<int:request_id>/reject",
                    "payment_collection_reject", methods=["POST"],
                    view_func=require_api_token(payment_collection_reject))
    bp.add_url_rule("/admin/payments/requests/<int:request_id>/apply-service",
                    "payment_collection_apply_service", methods=["POST"],
                    view_func=require_api_token(payment_collection_apply_service))
    bp.add_url_rule("/payments/webhooks/jawwal-pay",
                    "payment_collection_jawwal_webhook", methods=["POST"],
                    view_func=require_api_token(payment_collection_jawwal_webhook))
    bp.add_url_rule("/payments", "payments_list",
                    require_api_token(payments_list), methods=["GET"])
    bp.add_url_rule("/payments", "payments_create",
                    require_api_token(payments_create), methods=["POST"])
    bp.add_url_rule("/payments/<int:payment_id>/void",
                    "payments_void", methods=["POST"],
                    view_func=require_api_token(payments_void))


def payments_list():
    try:
        limit = min(int(request.args.get("limit") or 100), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
        subscriber_id = request.args.get("subscriber_id")
        dist = current_distributor()
        if subscriber_id and not subscriber_in_scope(subscriber_id=int(subscriber_id)):
            return deny_out_of_scope()
        items = service_from_context().list_payments(
            subscriber_id=int(subscriber_id) if subscriber_id else None,
            distributor_id=int(dist["id"]) if dist else None,
            limit=limit,
            offset=offset,
        )
    except (ValueError, RadiusValidationError) as e:
        return fail("validation_error", getattr(e, "message", str(e)), status=422)
    return ok({"items": items, "count": len(items)})


def payments_create():
    body = request.get_json(silent=True) or {}
    dist = current_distributor()
    if dist and not subscriber_in_scope(
        username=str(body.get("username") or "").strip(),
        subscriber_id=body.get("subscriber_id"),
    ):
        return deny_out_of_scope()
    try:
        payment = service_from_context().create_payment(
            body,
            actor=_actor(),
            distributor_id=int(dist["id"]) if dist else None,
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422, details=e.details)
    return ok({"payment": payment}, status=201)


def payments_void(payment_id: int):
    body = request.get_json(silent=True) or {}
    try:
        payment = service_from_context().get_payment(payment_id)
        if current_distributor() and not subscriber_in_scope(
            username=payment.get("username") or "",
            subscriber_id=payment.get("subscriber_id"),
        ):
            return deny_out_of_scope()
        result = service_from_context().void_payment(
            payment_id=payment_id,
            actor=_actor(),
            reason=str(body.get("reason") or "")[:500],
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422, details=e.details)
    return ok(result, status=201)


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _settings_payload(settings: PaymentSettings | None) -> dict:
    if settings is None:
        settings = PaymentSettings(id=None, tenant_id=_tid())
    return {
        "id": settings.id,
        "tenant_id": settings.tenant_id,
        "provider": settings.provider,
        "enabled": settings.enabled,
        "wallet_number": settings.wallet_number,
        "wallet_owner_name": settings.wallet_owner_name,
        "currency": settings.currency,
        "confirmation_mode": settings.confirmation_mode,
        "auto_apply": settings.auto_apply,
        "allow_cards": settings.allow_cards,
        "allow_monthly_subscriptions": settings.allow_monthly_subscriptions,
        "allow_distributor_payments": settings.allow_distributor_payments,
        "min_amount": settings.min_amount,
        "max_amount": settings.max_amount,
        "payment_request_ttl_minutes": settings.payment_request_ttl_minutes,
        "created_at": settings.created_at,
        "updated_at": settings.updated_at,
    }


def _request_payload(row: dict) -> dict:
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


def _settings_as_kwargs(existing: PaymentSettings | None, updates: dict) -> dict:
    base = _settings_payload(existing)
    allowed = {
        "provider",
        "enabled",
        "wallet_number",
        "wallet_owner_name",
        "currency",
        "confirmation_mode",
        "auto_apply",
        "allow_cards",
        "allow_monthly_subscriptions",
        "allow_distributor_payments",
        "min_amount",
        "max_amount",
        "payment_request_ttl_minutes",
    }
    unknown = sorted(str(key) for key in updates if str(key) not in allowed)
    if unknown:
        raise ValueError("unknown:" + ",".join(unknown))
    for key in allowed:
        if key in updates:
            base[key] = updates[key]
    return {key: base[key] for key in allowed}


def _purpose_enabled(settings: PaymentSettings, purpose: str) -> bool:
    if purpose == "card_purchase":
        return settings.allow_cards
    if purpose in {"monthly_subscription", "subscriber_renewal", "quota_topup", "time_extension"}:
        return settings.allow_monthly_subscriptions
    if purpose == "distributor_payment":
        return settings.allow_distributor_payments
    return purpose == "loan_settlement"


def payment_collection_settings_get():
    settings = PaymentSettingsRepository().get(_tid())
    return ok({"settings": _settings_payload(settings)})


def payment_collection_settings_patch():
    body = request.get_json(silent=True) or {}
    repo = PaymentSettingsRepository()
    try:
        merged = _settings_as_kwargs(repo.get(_tid()), body.get("settings", body))
        settings = repo.upsert(tenant_id=_tid(), **merged)
    except ValueError as exc:
        message = str(exc)
        details = {}
        if message.startswith("unknown:"):
            details["unknown"] = message.replace("unknown:", "").split(",")
            message = "مفتاح إعداد غير معروف."
        return fail("validation_error", message, status=422, details=details)
    return ok({"settings": _settings_payload(settings)})


def payment_collection_requests_create():
    body = request.get_json(silent=True) or {}
    settings = PaymentSettingsRepository().get(_tid())
    if not settings or not settings.enabled:
        return fail("payments_disabled", "تحصيل المدفوعات غير مفعل.", status=422)
    if settings.provider == "jawwal_pay":
        return fail("provider_disabled", "مزود Jawwal Pay غير مفعل حاليًا.", status=422)
    purpose = str(body.get("purpose") or "").strip()
    if purpose not in PAYMENT_PURPOSES:
        return fail("validation_error", "نوع طلب الدفع غير صالح.", status=422)
    if not _purpose_enabled(settings, purpose):
        return fail("purpose_disabled", "هذا النوع من الدفع غير مفعل.", status=422)
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return fail("validation_error", "المبلغ غير صالح.", status=422)
    if settings.min_amount is not None and amount < settings.min_amount:
        return fail("validation_error", "المبلغ أقل من الحد الأدنى.", status=422)
    if settings.max_amount is not None and amount > settings.max_amount:
        return fail("validation_error", "المبلغ أعلى من الحد الأقصى.", status=422)
    currency = str(body.get("currency") or settings.currency).strip()
    if currency not in CURRENCIES:
        return fail("validation_error", "العملة غير مسموحة.", status=422)
    try:
        created = PaymentRequestRepository().create(
            tenant_id=_tid(),
            payer_type=str(body.get("payer_type") or "subscriber").strip(),
            payer_id=body.get("payer_id"),
            purpose=purpose,
            amount=amount,
            currency=currency,
            provider=settings.provider,
            receiver_wallet=settings.wallet_number,
            created_by=int(getattr(g, "admin_id", 0) or 0) or None,
            ttl_minutes=settings.payment_request_ttl_minutes,
        )
    except ValueError as exc:
        return fail("validation_error", _payment_error_message(exc), status=422)
    return ok({"request": _request_payload(created)}, status=201)


def payment_collection_requests_list():
    try:
        rows = PaymentRequestRepository().list(
            _tid(),
            status=str(request.args.get("status") or "").strip(),
            purpose=str(request.args.get("purpose") or "").strip(),
            payer_type=str(request.args.get("payer_type") or "").strip(),
            limit=int(request.args.get("limit") or 100),
            offset=int(request.args.get("offset") or 0),
        )
    except ValueError as exc:
        return fail("validation_error", _payment_error_message(exc), status=422)
    return ok({"items": [_request_payload(row) for row in rows], "count": len(rows)})


def payment_collection_requests_get(request_id: int):
    row = PaymentRequestRepository().get(_tid(), request_id)
    if not row:
        return fail("not_found", "طلب الدفع غير موجود.", status=404)
    return ok({"request": _request_payload(row)})


def payment_collection_request_instructions(request_id: int):
    row = PaymentRequestRepository().get(_tid(), request_id)
    if not row:
        return fail("not_found", "طلب الدفع غير موجود.", status=404)
    settings = PaymentSettingsRepository().get(_tid())
    return ok({
        "instructions": {
            "amount": row["amount"],
            "currency": row["currency"],
            "receiver_wallet": row["receiver_wallet"],
            "wallet_owner_name": settings.wallet_owner_name if settings else "",
            "reference_code": row["reference_code"],
            "expires_at": row["expires_at"],
            "instructions": "أرسل المبلغ نفسه إلى المحفظة، واكتب رمز المرجع مع العملية. لا يعتمد الدفع إلا بعد مراجعة الإدارة.",
            "status": row["status"],
        }
    })


def _proof_payload(row: dict) -> dict:
    return {
        "id": row["id"],
        "payment_request_id": row["payment_request_id"],
        "proof_type": row["proof_type"],
        "reference_number": row["reference_number"],
        "image_path": row["image_path"],
        "note": row["note"],
        "submitted_at": row["submitted_at"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "review_status": row["review_status"],
        "review_note": row["review_note"],
    }


def payment_collection_submit_proof(request_id: int):
    request_row = PaymentRequestRepository().get(_tid(), request_id)
    if not request_row:
        return fail("not_found", "طلب الدفع غير موجود.", status=404)
    if request_row["status"] in {"paid", "rejected", "expired", "cancelled", "failed"}:
        return fail("invalid_state", "لا يمكن إرسال إثبات دفع لهذا الطلب.", status=422)
    body = request.get_json(silent=True) or {}
    try:
        proof = PaymentProofRepository().create(
            payment_request_id=request_id,
            proof_type=str(body.get("proof_type") or "manual_reference"),
            reference_number=str(body.get("reference_number") or "").strip(),
            note=str(body.get("note") or "").strip(),
        )
        PaymentRequestRepository().update_status(_tid(), request_id, "proof_submitted")
    except ValueError as exc:
        return fail("validation_error", _payment_error_message(exc), status=422)
    return ok({"proof": _proof_payload(proof)}, status=201)


def payment_collection_review_queue():
    rows = PaymentRequestRepository().list_for_review(_tid())
    return ok({"items": [_request_payload(row) for row in rows], "count": len(rows)})


def payment_collection_reconciliation():
    return ok({"reconciliation": PaymentReconciliationRepository().summary(tenant_id=_tid())})


def _reviewable_request(request_id: int):
    row = PaymentRequestRepository().get(_tid(), request_id)
    if not row:
        return None, fail("not_found", "طلب الدفع غير موجود.", status=404)
    if row["status"] not in {"proof_submitted", "under_review"}:
        return None, fail("invalid_state", "هذا الطلب غير قابل للمراجعة.", status=422)
    proof = PaymentProofRepository().latest_for_request(request_id)
    if not proof:
        return None, fail("invalid_state", "لم يتم إرفاق إثبات دفع.", status=422)
    return (row, proof), None


def payment_collection_approve(request_id: int):
    pair, error = _reviewable_request(request_id)
    if error:
        return error
    request_row, proof = pair
    body = request.get_json(silent=True) or {}
    PaymentProofRepository().mark_reviewed(
        proof_id=proof["id"],
        reviewed_by=int(getattr(g, "admin_id", 0) or 0) or None,
        review_status="approved",
        review_note=str(body.get("review_note") or "").strip(),
    )
    PaymentRequestRepository().update_status(_tid(), request_id, "paid")
    transaction = PaymentTransactionRepository().create(
        payment_request_id=request_id,
        amount=request_row["amount"],
        currency=request_row["currency"],
        status="paid_manual",
        provider_transaction_id=None,
        raw_payload={"proof_id": proof["id"], "review": "approved_manual"},
    )
    ledger_entry = PaymentCollectionLedgerRepository().apply_paid_request(
        tenant_id=_tid(),
        request_id=request_id,
        actor=_actor(),
    )
    updated = PaymentRequestRepository().get(_tid(), request_id)
    return ok({
        "request": _request_payload(updated),
        "transaction": transaction,
        "ledger_entry": ledger_entry,
    })


def payment_collection_reject(request_id: int):
    pair, error = _reviewable_request(request_id)
    if error:
        return error
    _request_row, proof = pair
    body = request.get_json(silent=True) or {}
    PaymentProofRepository().mark_reviewed(
        proof_id=proof["id"],
        reviewed_by=int(getattr(g, "admin_id", 0) or 0) or None,
        review_status="rejected",
        review_note=str(body.get("review_note") or "").strip(),
    )
    PaymentRequestRepository().update_status(_tid(), request_id, "rejected")
    updated = PaymentRequestRepository().get(_tid(), request_id)
    return ok({"request": _request_payload(updated)})


def _apply_attempt_payload(row: dict) -> dict:
    result = json_load(row.get("result_json"), default={})
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "payment_request_id": row["payment_request_id"],
        "status": row["status"],
        "actor": row["actor"],
        "result_json": row["result_json"],
        "result": result if isinstance(result, dict) else {},
        "error_message": row["error_message"],
        "created_at": row["created_at"],
    }


def payment_collection_apply_service(request_id: int):
    body = request.get_json(silent=True) or {}
    try:
        attempt = PaymentServiceApplyRepository().apply_paid_request(
            tenant_id=_tid(),
            request_id=request_id,
            actor=_actor(),
            simulate_failure=bool(body.get("simulate_failure")),
        )
    except ValueError as exc:
        message = str(exc)
        if message == "payment_request":
            return fail("not_found", "طلب الدفع غير موجود.", status=404)
        if message == "status":
            return fail("invalid_state", "لا يمكن تطبيق الخدمة إلا بعد اعتماد الدفع.", status=422)
        return fail("validation_error", message, status=422)
    updated = PaymentRequestRepository().get(_tid(), request_id)
    return ok({"request": _request_payload(updated), "apply_attempt": _apply_attempt_payload(attempt)})


def payment_collection_jawwal_webhook():
    payload = request.get_json(silent=True) or {}
    event = PaymentWebhookEventRepository().create(
        provider="jawwal_pay",
        payload=payload,
        event_id=None,
        payment_request_id=None,
        signature_valid=False,
        processed=False,
    )
    return ok({
        "event": {
            "id": event["id"],
            "provider": event["provider"],
            "signature_valid": bool(event["signature_valid"]),
            "processed": bool(event["processed"]),
            "payment_request_id": event["payment_request_id"],
            "created_at": event["created_at"],
        },
        "status": "stored_unprocessed",
        "paid": False,
    }, status=202)
