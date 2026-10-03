"""
شكل ردود JSON موحَّد لكل الـ API.

استخدم:
    return ok({"username": "u1"})
    return fail("not_found", "...", status=404)
    return not_implemented("accounts.create")
"""
from __future__ import annotations
from app.i18n_text import _tr

import uuid
from typing import Any, Optional

from flask import jsonify, request

_API_VERSION = "v1"


def _meta() -> dict:
    return {
        "request_id": request.headers.get("X-Request-Id") or str(uuid.uuid4()),
        "version": _API_VERSION,
    }


def _enrich(data: Any) -> Any:
    """fix3: ``actor_name``/``created_by_name``… بجانب كلّ فاعلٍ خام، و«api-token:N»
    في النصوص الحرّة ⇒ «تطبيق — <المدير>» (``services.actor_names``)."""
    if not isinstance(data, (dict, list)):
        return data
    try:
        from ..radius.services.actor_names import enrich_api_payload
        return enrich_api_payload(data)
    except Exception:  # noqa: BLE001
        return data


def ok(data: Any = None, *, status: int = 200, extra_meta: Optional[dict] = None):
    body = {"ok": True, "data": _enrich(data) if data is not None else {}, "meta": _meta()}
    if extra_meta:
        body["meta"].update(extra_meta)
    return jsonify(body), status


def fail(code: str, message: str = "", *, status: int = 400, details: Optional[dict] = None):
    body = {
        "ok": False,
        "error": {"code": code, "message": message or code, "details": details or {}},
        "meta": _meta(),
    }
    return jsonify(body), status


def not_implemented(operation: str):
    return fail(
        "not_implemented",
        _tr('العملية %(operation)s مُسجَّلة كـ contract لكن منطقها لم يكتمل بعد.', operation=repr(operation)),
        status=501,
        details={"operation": operation},
    )
