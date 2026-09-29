"""Distributor / scoped manager operational API foundation."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusConflict, RadiusError, RadiusNotFound, RadiusValidationError
from ..auth import require_api_token
from ..json_input import json_object
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
        # limit سالب كان يصير LIMIT -1 في SQLite = كلّ الصفوف.
        limit = max(1, min(int(request.args.get("limit") or default_limit), 1000))
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        raise RadiusValidationError("قيم limit و offset يجب أن تكون أرقامًا صحيحة.")
    return limit, offset


def distributors_list():
    try:
        limit, offset = _page_args()
        rows = _svc().list_distributors(
            tenant_id=_tid(),
            status=(request.args.get("status") or "").strip() or None,
            limit=limit + 1,
            offset=offset,
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    items = rows[:limit]
    return ok({"items": items, "count": len(items), "limit": limit,
               "offset": offset, "has_more": len(rows) > limit})


def _can_manage_distributors() -> bool:
    """Web parity (routes/distributors.py ``_can_manage_distributors``): the
    owner / co-owner (or an unbound credential) always; a limited manager only
    when the owner granted him «إدارة الموزعين» (``can_manage_distributors``).
    p01/D16: the API used to create a distributor for any token."""
    from ..access_control import admin_id, token_bypasses_rbac
    if token_bypasses_rbac():
        return True
    try:
        from ...radius.services.manager_distributor_ops import (
            ManagerDistributorOpsService,
        )
        return bool(ManagerDistributorOpsService(tenant_id=_tid()).has_permission(
            entity_type="manager", entity_id=int(admin_id()),
            permission="can_manage_distributors"))
    except Exception:  # noqa: BLE001 — never grant on a lookup error
        return False


def distributors_create():
    # reports.finance is enforced by the central API guard (web
    # distributors_create); the «إدارة الموزعين» grant is checked here, exactly
    # like the web route.
    if not _can_manage_distributors():
        return fail("forbidden",
                    "لا تملك صلاحية إدارة الموزعين. اطلب من المالك تفعيلها.",
                    status=403)
    body, err = json_object()
    if err:
        return err
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
    body, err = json_object()
    if err:
        return err
    # batch_id (number) OR the visible batch code: {"batch_code": "B-…"} or
    # {"batch_id": "B-…"} — the operator sees the code, not the id.
    raw_batch = body.get("batch_id")
    is_code = False
    if raw_batch in (None, "", 0) and body.get("batch_code") not in (None, ""):
        raw_batch, is_code = body.get("batch_code"), True
    try:
        batch_id = _svc().resolve_batch_ref(_tid(), raw_batch, is_code=is_code)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        assignment = _svc().assign_batch(
            tenant_id=_tid(),
            distributor_id=distributor_id,
            batch_id=batch_id,
            actor=_actor(),
            notes=body.get("notes") if isinstance(body.get("notes"), str) else "",
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusConflict as e:
        return fail("distributor_disabled", e.message, status=409)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return ok({"assignment": assignment})


def distributors_settle(distributor_id: int):
    body, err = json_object()
    if err:
        return err
    try:
        entry = _svc().settle_distributor(
            tenant_id=_tid(),
            distributor_id=distributor_id,
            actor=_actor(),
            data=body,
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusConflict as e:
        return fail("distributor_disabled", e.message, status=409)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return ok({"entry": entry}, status=201)
