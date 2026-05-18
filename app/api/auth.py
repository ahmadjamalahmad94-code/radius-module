"""
Bearer Token Auth للـ API + rate limit per-token.

مصادر التوكنات:
1. `HOBERADIUS_API_TOKENS` env (CSV) — تستخدم tenant_id=1.
2. DB `api_tokens` — لها tenant_id ومجال (scopes).
3. dev fallback `dev-token-please-change` — tenant_id=1.

عند نجاح التحقّق نضع: g.api_token_id, g.tenant_id (override).
"""
from __future__ import annotations

import functools
import logging
import os
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Optional

from flask import g, request

from .responses import fail

_LOG = logging.getLogger(__name__)
_DEV_DEFAULT_TOKEN = "dev-token-please-change"

# rate limit state (in-memory)
_rate_lock = Lock()
_rate_log: dict[str, deque] = defaultdict(deque)


def _allowed_env_tokens() -> tuple[str, ...]:
    raw = (os.environ.get("HOBERADIUS_API_TOKENS") or "").strip()
    if not raw:
        return (_DEV_DEFAULT_TOKEN,)
    return tuple(t.strip() for t in raw.split(",") if t.strip())


def _extract_bearer() -> Optional[str]:
    h = request.headers.get("Authorization") or ""
    if not h.lower().startswith("bearer "):
        return None
    return h.split(None, 1)[1].strip() or None


def _rate_limit_check(token_key: str, *, per_minute: int = 60) -> bool:
    """يُرجع True لو لا يزال مسموحًا. يستخدم سجل dequeue ثابت لكل token."""
    if per_minute <= 0: return True
    now = time.monotonic()
    window = 60.0
    with _rate_lock:
        log = _rate_log[token_key]
        while log and (now - log[0]) > window:
            log.popleft()
        if len(log) >= per_minute:
            return False
        log.append(now)
        return True


def require_api_token(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        token = _extract_bearer()
        if not token:
            return fail("unauthorized", "Authorization Bearer مطلوب", status=401)

        tenant_id = 1
        token_id = None
        rpm = 60  # default

        # 1. env token
        if token in _allowed_env_tokens():
            tenant_id = 1
        else:
            # 2. DB token
            try:
                from app.radius.db.repos import api_tokens_repo
                rec = api_tokens_repo.resolve_by_plain(token)
            except Exception:  # noqa: BLE001
                rec = None
            if not rec:
                _LOG.warning("invalid api token attempt (len=%d)", len(token))
                return fail("unauthorized", "توكن غير صالح", status=401)
            tenant_id = rec["tenant_id"]
            token_id = rec["id"]
            # touch last_used (best-effort)
            try: api_tokens_repo.touch_used(token_id)
            except Exception: pass
            # rate per tenant tier
            try:
                from app.radius.db.repos import tenants_repo
                t = tenants_repo.get_tenant(tenant_id)
                rpm = (t.api_rpm if t else 60) or 60
            except Exception: pass

        # rate limit
        key = f"tok:{token_id or token[:12]}"
        if not _rate_limit_check(key, per_minute=rpm):
            return fail("rate_limited",
                        f"تجاوزت الحد ({rpm} req/min)", status=429,
                        details={"retry_after_seconds": 60})

        # set context
        g.api_token = token
        g.api_token_id = token_id
        g.tenant_id = tenant_id

        return view(*a, **kw)
    return wrapped
