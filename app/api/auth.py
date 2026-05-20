"""
Bearer Token Auth للـ API + rate limit per-token.

مصادر التوكنات:
1. `HOBERADIUS_API_TOKENS` env (CSV) — tenant_id=1.
2. DB `api_tokens` — لها tenant_id ومجال (scopes) و expires_at.
3. dev fallback `dev-token-please-change` — متاح **فقط** خارج بيئة الإنتاج.

تحديد بيئة الإنتاج:
- `HOBERADIUS_ENV=prod|production` أو `FLASK_ENV=prod|production`.
- في الإنتاج: لا fallback dev token، وأي token مفقود/غير صالح يفشل.

التحقّق من الانتهاء:
- DB tokens بحقل `expires_at` (UTC ISO). لو تجاوز utcnow → 401 token_expired.
- env tokens ليس لها expiry (مُدارة يدويًا).

عند نجاح التحقّق نضع: g.api_token_id, g.tenant_id (override).
"""
from __future__ import annotations

import functools
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime
from threading import Lock
from typing import Optional

from flask import current_app, g, request

from .responses import fail

_LOG = logging.getLogger(__name__)
_DEV_DEFAULT_TOKEN = "dev-token-please-change"
_DEV_FALLBACK_WARNED = False

# rate limit state (in-memory)
_rate_lock = Lock()
_rate_log: dict[str, deque] = defaultdict(deque)


def _is_production() -> bool:
    """Production when HOBERADIUS_ENV or FLASK_ENV resolves to prod/production.
    Empty/unset → development. Keep the check cheap — called per request."""
    env = (os.environ.get("HOBERADIUS_ENV")
           or os.environ.get("FLASK_ENV")
           or "").strip().lower()
    return env in {"prod", "production"}


def _allowed_env_tokens() -> tuple[str, ...]:
    """Explicit env tokens first, then the dev fallback when **not** in prod.

    In production with no `HOBERADIUS_API_TOKENS` set, returns an empty tuple
    — every request must authenticate against the DB `api_tokens` table or
    fail. This closes the dev-token attack surface on a misconfigured deploy.
    """
    global _DEV_FALLBACK_WARNED
    raw = (os.environ.get("HOBERADIUS_API_TOKENS") or "").strip()
    if raw:
        return tuple(t.strip() for t in raw.split(",") if t.strip())
    if _is_production():
        return ()
    if not _DEV_FALLBACK_WARNED:
        _LOG.warning(
            "DEV-MODE auth fallback engaged (token=%r). "
            "Set HOBERADIUS_ENV=prod and HOBERADIUS_API_TOKENS (or use DB "
            "tokens) before deploying.",
            _DEV_DEFAULT_TOKEN,
        )
        _DEV_FALLBACK_WARNED = True
    return (_DEV_DEFAULT_TOKEN,)


def _extract_bearer() -> Optional[str]:
    h = request.headers.get("Authorization") or ""
    if not h.lower().startswith("bearer "):
        return None
    return h.split(None, 1)[1].strip() or None


def _rate_limit_check(token_key: str, *, per_minute: int = 60) -> bool:
    """يُرجع True لو لا يزال مسموحًا. يستخدم سجل dequeue ثابت لكل token."""
    if current_app.testing or os.environ.get("PYTEST_CURRENT_TEST"):
        return True
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


def _is_expired(expires_at_raw) -> Optional[bool]:
    """Return True if expired, False if still valid, None if no expiry set.
    Malformed `expires_at` is treated as expired — fail closed."""
    if not expires_at_raw:
        return None
    try:
        s = str(expires_at_raw).replace("Z", "")
        exp = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return True
    return datetime.utcnow() > exp


def require_api_token(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        token = _extract_bearer()
        if not token:
            return fail("unauthorized", "Authorization Bearer مطلوب", status=401)

        tenant_id = 1
        token_id = None
        token_scopes: list[str] = []
        admin_id = 0
        rpm = 60  # default

        # 1. env token (dev fallback included only when not in production)
        if token in _allowed_env_tokens():
            tenant_id = 1
            token_scopes = ["admin:full"]
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
            # 2a. expiry — fail closed
            expired = _is_expired(rec.get("expires_at"))
            if expired is True:
                _LOG.info("expired api token attempted (id=%s)", rec.get("id"))
                return fail(
                    "token_expired",
                    "انتهت صلاحية الـ token — سجّل دخول مجددًا",
                    status=401,
                )
            tenant_id = rec["tenant_id"]
            token_id = rec["id"]
            token_scopes = list(rec.get("scopes") or [])
            admin_id = int(rec.get("created_by") or 0)
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
        key_prefix = f"test:{id(current_app)}:" if current_app.testing else ""
        key = f"{key_prefix}tok:{token_id or token[:12]}"
        if not _rate_limit_check(key, per_minute=rpm):
            return fail("rate_limited",
                        f"تجاوزت الحد ({rpm} req/min)", status=429,
                        details={"retry_after_seconds": 60})

        # set context
        g.api_token = token
        g.api_token_id = token_id
        g.api_token_scopes = token_scopes
        g.admin_id = admin_id
        g.tenant_id = tenant_id

        return view(*a, **kw)
    return wrapped
