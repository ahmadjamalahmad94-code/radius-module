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
"""
from __future__ import annotations

import functools
import sqlite3
from datetime import datetime, timedelta

from flask import Response, g, make_response, request

from ...radius.db.connection import db, transaction
from ..responses import fail

TTL_HOURS = 24
_MAX_KEY = 200


def _request_key() -> str:
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            key = str(body.get("client_request_id") or "").strip()
    return key[:_MAX_KEY]


def _release(tid: int, key: str, scope: str) -> None:
    try:
        with transaction() as conn:
            conn.execute(
                "DELETE FROM api_idempotency_keys WHERE tenant_id = ? AND idem_key = ? "
                "AND scope = ? AND state = 'pending'", (tid, key, scope))
    except Exception:  # noqa: BLE001 — never mask the real outcome
        pass


def idempotent(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        key = _request_key()
        if not key:
            return view(*args, **kwargs)
        tid = int(getattr(g, "tenant_id", 1) or 1)
        scope = f"{request.method} {request.path}"
        now = datetime.utcnow()
        cutoff = (now - timedelta(hours=TTL_HOURS)).isoformat()
        with transaction() as conn:
            conn.execute("DELETE FROM api_idempotency_keys WHERE created_at < ?", (cutoff,))
            try:
                conn.execute(
                    "INSERT INTO api_idempotency_keys(tenant_id, idem_key, scope, state, "
                    "created_at) VALUES(?,?,?,'pending',?)",
                    (tid, key, scope, now.isoformat()))
                claimed = True
            except sqlite3.IntegrityError:
                claimed = False
        if not claimed:
            row = db().execute(
                "SELECT state, status_code, response_json FROM api_idempotency_keys "
                "WHERE tenant_id = ? AND idem_key = ? AND scope = ?", (tid, key, scope),
            ).fetchone()
            if row and row["state"] == "done":
                replay = Response(row["response_json"] or "{}",
                                  status=int(row["status_code"] or 200),
                                  mimetype="application/json")
                replay.headers["Idempotent-Replay"] = "true"
                return replay
            return fail("idempotency_in_progress",
                        "طلبٌ بنفس مفتاح التكرار قيد التنفيذ — انتظر نتيجته ولا تُعِد الإرسال.",
                        status=409)
        try:
            response = make_response(view(*args, **kwargs))
        except Exception:
            _release(tid, key, scope)
            raise
        if 200 <= response.status_code < 300:
            try:
                with transaction() as conn:
                    conn.execute(
                        "UPDATE api_idempotency_keys SET state = 'done', status_code = ?, "
                        "response_json = ? WHERE tenant_id = ? AND idem_key = ? AND scope = ?",
                        (response.status_code, response.get_data(as_text=True), tid, key, scope))
            except Exception:  # noqa: BLE001 — the action itself succeeded
                _release(tid, key, scope)
        else:
            _release(tid, key, scope)
        return response
    return wrapped
