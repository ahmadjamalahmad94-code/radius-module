"""Web panel → executor bridge: run ``/api/v1/ops/*`` as the LOGGED-IN admin.

The web chat is a session (cookie) request; the executor API authenticates a
bearer credential. So the server holds a short-lived token bound to the
session's admin AND tenant (``created_by`` = admin → the API applies that
admin's own RBAC, scoping, caps — exactly like the mobile app) and calls the
API in-process through ``dispatch.call(..., credential=...)``.

* The plaintext token lives only in this process's memory — never in the
  cookie session, the page, the browser or a log.
* Name prefix ``login:`` → a password change / disable revokes it
  (``api_tokens_repo.revoke_admin_tokens``); a deleted/disabled admin's token
  is refused at authentication anyway.
* TTL ``TOKEN_TTL``; reused while more than ``TOKEN_MIN_LEFT`` remains and the
  row still resolves to the same admin + tenant, so chat turns do not pile up
  ``api_tokens`` rows (≈ 2 rows per admin per hour per worker).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from .dispatch import ApiResult, call

TOKEN_TTL = timedelta(minutes=30)
TOKEN_MIN_LEFT = 5 * 60          # seconds
_CACHE: dict[tuple[str, int, int], tuple[str, float]] = {}
_LOCK = threading.Lock()


def _db_key() -> str:
    try:
        from ...db.connection import db_path
        return str(db_path())
    except Exception:  # noqa: BLE001
        return ""


def _mint(admin_id: int, tenant_id: int, username: str) -> tuple[str, float]:
    from ...db.repos import api_tokens_repo
    now = datetime.utcnow()
    _rec, plain = api_tokens_repo.create_token(
        tenant_id=int(tenant_id),
        name=f"{api_tokens_repo.LOGIN_TOKEN_PREFIX}ui-ops:{str(username)[:40]}:"
             f"{now.strftime('%Y%m%dT%H%M%S')}",
        scopes=["admin:full"],          # narrows nothing; the admin's own perms apply
        created_by=int(admin_id),
        expires_at=now + TOKEN_TTL)
    return plain, time.time() + TOKEN_TTL.total_seconds()


def credential(admin_id: int, tenant_id: int, username: str = "") -> str:
    """A valid bearer token for (admin, tenant) — cached, re-minted when needed."""
    if int(admin_id or 0) <= 0:
        return ""
    key = (_db_key(), int(admin_id), int(tenant_id))
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and hit[1] - time.time() > TOKEN_MIN_LEFT:
        from ...db.repos import api_tokens_repo
        try:
            rec = api_tokens_repo.resolve_by_plain(hit[0])
        except Exception:  # noqa: BLE001
            rec = None
        if rec and int(rec.get("created_by") or 0) == int(admin_id) \
                and int(rec.get("tenant_id") or 0) == int(tenant_id):
            return hit[0]
    plain, exp = _mint(admin_id, tenant_id, username or str(admin_id))
    with _LOCK:
        _CACHE[key] = (plain, exp)
    return plain


class Api:
    """``/api/v1`` caller bound to one admin + tenant (the web session's)."""

    def __init__(self, admin_id: int, tenant_id: int, username: str = ""):
        self.admin_id = int(admin_id)
        self.tenant_id = int(tenant_id)
        self.username = username

    def __call__(self, method: str, path: str, body: Optional[dict] = None,
                 query: Optional[dict] = None) -> ApiResult:
        tok = credential(self.admin_id, self.tenant_id, self.username)
        if not tok:
            return ApiResult(status=401, body={"ok": False, "error": {
                "code": "unauthorized", "message": ""}})
        return call(method, path, body=body, query=query, credential=tok)


def reset_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = ["Api", "credential", "reset_cache", "TOKEN_TTL"]
