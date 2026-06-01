"""Accounting ledger API."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusValidationError
from ...radius.services.accounting import service_from_context
from ..auth import require_api_token
from ..responses import fail, ok


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/ledger", "ledger_list",
                    require_api_token(ledger_list), methods=["GET"])
    bp.add_url_rule("/ledger/void", "ledger_void",
                    require_api_token(ledger_void), methods=["POST"])


def ledger_list():
    try:
        limit = min(int(request.args.get("limit") or 100), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
        subscriber_id = request.args.get("subscriber_id")
        items = service_from_context().list_ledger(
            entry_type=(request.args.get("entry_type") or "").strip(),
            subscriber_id=int(subscriber_id) if subscriber_id else None,
            limit=limit,
            offset=offset,
        )
    except ValueError:
        return fail("validation_error", "قيم limit و offset ومعرّف المشترك يجب أن تكون أرقامًا صحيحة.", status=422)
    except RadiusValidationError as e:
        return fail("validation_error", getattr(e, "message", str(e)), status=422)
    return ok({"items": items, "count": len(items)})


def ledger_void():
    body = request.get_json(silent=True) or {}
    try:
        entry_id = int(body.get("entry_id") or 0)
        if entry_id <= 0:
            raise RadiusValidationError("معرّف القيد مطلوب.")
        entry = service_from_context().void_ledger(
            entry_id=entry_id,
            actor=_actor(),
            reason=str(body.get("reason") or "")[:500],
        )
    except ValueError:
        return fail("validation_error", "معرّف القيد يجب أن يكون رقمًا صحيحًا.", status=422)
    except RadiusValidationError as e:
        return fail("validation_error", getattr(e, "message", str(e)), status=422)
    return ok({"entry": entry}, status=201)
