"""Idempotency for the /api/v1 money endpoints (payment, extend, loan, balance,
quota top-up, raw payments/loans).

A client may send ``Idempotency-Key: <uuid>`` (or ``client_request_id`` in the
JSON body). The same key on the same endpoint within 24 h returns the FIRST
result again (header ``Idempotent-Replay: true``) without writing anything —
a double tap or a retry after a timeout no longer records two payments.

The key is reserved (row ``pending``, UNIQUE per tenant+key+endpoint) before
the view runs, so two parallel requests with one key cannot both execute: the
second gets 409 while the first is in flight. Only 2xx results are kept; a
failed attempt releases the key so the client can retry it. Requests without a
key behave exactly as before.

Fix wave 2: the key is bound to the request fingerprint (method + path + body);
the same key with a different body or for another subscriber → 422
«مفتاح التكرار استُخدم لطلب مختلف». Keys longer than 200 chars → 422. The logic
lives in ``radius/services/idempotency`` so the web forms use it too.
"""
from __future__ import annotations
from app.i18n_text import _tr

import functools

from flask import Response, g, make_response, request

from ...radius.services import idempotency as _idem
from ..responses import fail

TTL_HOURS = _idem.TTL_HOURS
_MAX_KEY = _idem.MAX_KEY


def _raw_key() -> str:
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            key = str(body.get("client_request_id") or "").strip()
    return key


def _request_key() -> str:
    return _raw_key()[:_MAX_KEY]


def _is_dry_run() -> bool:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return False
    val = body.get("dry_run")
    return val is True or str(val or "").strip().lower() in {"1", "true", "yes", "on"}


def _release(tid: int, key: str, scope: str) -> None:
    _idem.release(tid, key, scope)


def idempotent(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        key = _raw_key()
        if len(key) > _MAX_KEY:
            # A 5,000-char key used to be accepted (silently truncated).
            return fail("validation_error", _tr(_idem.TOO_LONG_MSG, n=_idem.MAX_KEY), status=422)
        if not key or _is_dry_run():
            # معاينةٌ لا تكتب شيئًا فلا تحجز المفتاح — وإلّا أعاد التنفيذُ
            # الحقيقيّ بالمفتاح نفسه نتيجةَ المعاينة بدل أن يُنفَّذ.
            return view(*args, **kwargs)
        tid = int(getattr(g, "tenant_id", 1) or 1)
        scope = f"{request.method} {request.path}"
        body = request.get_json(silent=True)
        if body is None:
            body = request.get_data(as_text=True) or ""
        state, row = _idem.claim(tid, key, scope,
                                 _idem.fingerprint(request.method, request.path, body))
        if state == _idem.MISMATCH:
            # Same key, different body / another subscriber: never replay the
            # first result silently (the second payment was simply lost).
            return fail("idempotency_key_reused", _idem.MISMATCH_AR, status=422)
        if state == _idem.REPLAY:
            replay = Response(row["response_json"] or "{}",
                              status=int(row["status_code"] or 200),
                              mimetype="application/json")
            replay.headers["Idempotent-Replay"] = "true"
            return replay
        if state == _idem.IN_PROGRESS:
            return fail("idempotency_in_progress",
                        _tr("طلبٌ بنفس مفتاح التكرار قيد التنفيذ — انتظر نتيجته ولا تُعِد الإرسال."),
                        status=409)
        try:
            response = make_response(view(*args, **kwargs))
        except Exception:
            _release(tid, key, scope)
            raise
        if 200 <= response.status_code < 300:
            _idem.finish(tid, key, scope, response.status_code,
                         response.get_data(as_text=True))
        else:
            _release(tid, key, scope)
        return response
    return wrapped
