"""Distributor / scoped manager operational API foundation."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _svc():
    from ...radius.services.operations import get_operations_service
    return get_operations_service()


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/distributors", "distributors_list",
                    require_api_token(distributors_list), methods=["GET"])
    bp.add_url_rule("/distributors", "distributors_create",
                    require_api_token(distributors_create), methods=["POST"])
    bp.add_url_rule("/distributors/<int:distributor_id>/summary",
                    "distributors_summary",
                    require_api_token(distributors_summary), methods=["GET"])
    bp.add_url_rule("/distributors/<int:distributor_id>/batches",
                    "distributors_batches",
                    require_api_token(distributors_batches), methods=["GET"])
    bp.add_url_rule("/distributors/<int:distributor_id>/assign-batch",
                    "distributors_assign_batch",
                    require_api_token(distributors_assign_batch), methods=["POST"])
    bp.add_url_rule("/distributors/<int:distributor_id>/settle",
                    "distributors_settle",
                    require_api_token(distributors_settle), methods=["POST"])


def _page_args(default_limit: int = 200) -> tuple[int, int]:
    try:
        limit = min(int(request.args.get("limit") or default_limit), 1000)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        raise RadiusValidationError("قيم limit و offset يجب أن تكون أرقامًا صحيحة.")
    return limit, offset


def distributors_list():
    try:
        limit, offset = _page_args()
        items = _svc().list_distributors(
            tenant_id=_tid(),
            status=(request.args.get("status") or "").strip() or None,
            limit=limit,
            offset=offset,
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return ok({"items": items, "count": len(items)})


def distributors_create():
    body = request.get_json(silent=True) or {}
    try:
        saved = _svc().create_distributor(
            tenant_id=_tid(), actor=_actor(), data=body
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"distributor": saved}, status=201)


def distributors_summary(distributor_id: int):
    try:
        summary = _svc().distributor_summary(
            tenant_id=_tid(), distributor_id=distributor_id
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    return ok({"summary": summary})


def distributors_batches(distributor_id: int):
    try:
        limit, offset = _page_args()
        items = _svc().list_distributor_batches(
            tenant_id=_tid(), distributor_id=distributor_id,
            limit=limit, offset=offset,
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return ok({"items": items, "count": len(items)})


def distributors_assign_batch(distributor_id: int):
    body = request.get_json(silent=True) or {}
    try:
        batch_id = int(body.get("batch_id") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف حزمة الكروت يجب أن يكون رقمًا صحيحًا.", status=422)
    if batch_id <= 0:
        return fail("validation_error", "اختر حزمة الكروت أولًا.", status=422)
    try:
        assignment = _svc().assign_batch(
            tenant_id=_tid(),
            distributor_id=distributor_id,
            batch_id=batch_id,
            actor=_actor(),
            notes=str(body.get("notes") or ""),
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return ok({"assignment": assignment})


def distributors_settle(distributor_id: int):
    body = request.get_json(silent=True) or {}
    try:
        entry = _svc().settle_distributor(
            tenant_id=_tid(),
            distributor_id=distributor_id,
            actor=_actor(),
            data=body,
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return ok({"entry": entry}, status=201)
