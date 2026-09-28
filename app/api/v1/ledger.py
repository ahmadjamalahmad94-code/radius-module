"""Accounting ledger API."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusConflict, RadiusNotFound, RadiusValidationError
from ...radius.services.accounting import service_from_context
from ..auth import require_api_token
from ..responses import fail, ok
from .paging import PagingError, page_args


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/ledger", "ledger_list",
                    require_api_token(ledger_list), methods=["GET"])
    bp.add_url_rule("/ledger/void", "ledger_void",
                    require_api_token(ledger_void), methods=["POST"])


def ledger_list():
    try:
        limit, offset = page_args(default=100, maximum=500)
        subscriber_id = request.args.get("subscriber_id")
        items = service_from_context().list_ledger(
            entry_type=(request.args.get("entry_type") or "").strip(),
            subscriber_id=int(subscriber_id) if subscriber_id else None,
            limit=limit,
            offset=offset,
        )
    except (PagingError, ValueError):
        return fail("validation_error", "قيم limit و offset ومعرّف المشترك يجب أن تكون أرقامًا صحيحة.", status=422)
    except RadiusValidationError as e:
        return fail("validation_error", getattr(e, "message", str(e)), status=422)
    return ok({"items": items, "count": len(items)})


def ledger_void():
    """قيدٌ عكسيّ واحد لكل قيد: الثاني 409، وعكس قيدٍ عكسيّ 422، وقيد الدفعة
    يُلغي الدفعة نفسها (تُعلَّم «voided» ويُسترجع وقتها)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    try:
        entry_id = int(body.get("entry_id") or 0)
        if entry_id <= 0:
            raise RadiusValidationError("معرّف القيد مطلوب.")
        entry = service_from_context().void_ledger(
            entry_id=entry_id,
            actor=_actor(),
            reason=str(body.get("reason") or "")[:500],
        )
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف القيد يجب أن يكون رقمًا صحيحًا.", status=422)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusConflict as e:
        return fail("conflict", e.message, status=409)
    except RadiusValidationError as e:
        return fail("validation_error", getattr(e, "message", str(e)), status=422)
    return ok({"entry": entry}, status=201)
