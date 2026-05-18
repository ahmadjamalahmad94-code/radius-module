"""
شكل ردود JSON موحَّد لكل الـ API.

استخدم:
    return ok({"username": "u1"})
    return fail("not_found", "...", status=404)
    return not_implemented("accounts.create")
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from flask import jsonify, request

_API_VERSION = "v1"


def _meta() -> dict:
    return {
        "request_id": request.headers.get("X-Request-Id") or str(uuid.uuid4()),
        "version": _API_VERSION,
    }


def ok(data: Any = None, *, status: int = 200, extra_meta: Optional[dict] = None):
    body = {"ok": True, "data": data if data is not None else {}, "meta": _meta()}
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
        f"العملية {operation!r} مُسجَّلة كـ contract لكن منطقها لم يكتمل بعد.",
        status=501,
        details={"operation": operation},
    )
