"""Failed-login throttle — per (kind, username, client IP), in-memory.

Stress campaign 2026-09-28: 20 wrong passwords on ``POST /api/admin/login``
were all answered 401 in ~2 s and the right password then logged in at once —
no lockout, no delay (the same for the web login, HTTP Basic on the API and the
current-password check of ``/api/admin/password``). This module adds a sliding
window: after ``MAX_FAILURES`` failures inside ``WINDOW_SECONDS`` for the same
username from the same address, further attempts are refused with 429 (even
with the right password) until the oldest failure leaves the window. A
successful login clears the counter.

Shared by every process (leftover wave): the panel now runs several gunicorn
worker PROCESSES, so the failures live in the ``rate_events`` table
(migration 177) — an in-memory dict would make the lockout N× looser and let it
«clear» depending on which process answered. Only failures are written (a
successful login deletes its key). Without the table (bare unit tests) it falls
back to process memory.

The key is the same in every process (username + client IP); each install —
and each test file — has its own DB, so nothing leaks between them.
"""
from __future__ import annotations

import math
import os
import time

_DEFAULT_MAX_FAILURES = 10
_DEFAULT_WINDOW_MINUTES = 15



def _int_env(name: str, default: int) -> int:
    try:
        value = int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def max_failures() -> int:
    return _int_env("HOBERADIUS_LOGIN_MAX_FAILURES", _DEFAULT_MAX_FAILURES)


def window_seconds() -> int:
    return _int_env("HOBERADIUS_LOGIN_WINDOW_MINUTES", _DEFAULT_WINDOW_MINUTES) * 60


def client_ip() -> str:
    """The client address as seen by the edge proxy: the LAST hop of
    ``X-Forwarded-For`` (the one our nginx appended — earlier entries are
    client-supplied and spoofable), else ``remote_addr``."""
    try:
        from flask import request
        xff = request.headers.get("X-Forwarded-For") or ""
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        if hops:
            return hops[-1]
        return request.remote_addr or ""
    except Exception:  # noqa: BLE001
        return ""


def _key(kind: str, username: str, ip: str) -> str:
    return f"{kind}:{(username or '').strip().lower()}|{ip or ''}"


_SCOPE = "login_fail"


def retry_after(kind: str, username: str, ip: str | None = None) -> int:
    """Seconds until another attempt is allowed; 0 = not locked."""
    from ..db import shared_state
    ip = client_ip() if ip is None else ip
    window = window_seconds()
    now = time.time()
    hits = shared_state.rate_events(_SCOPE, _key(kind, username, ip), window=window, now=now)
    if len(hits) < max_failures():
        return 0
    return max(1, int(math.ceil(window - (now - hits[0]))))


def register_failure(kind: str, username: str, ip: str | None = None) -> None:
    from ..db import shared_state
    ip = client_ip() if ip is None else ip
    shared_state.rate_hit(_SCOPE, _key(kind, username, ip), window=window_seconds())


def register_success(kind: str, username: str, ip: str | None = None) -> None:
    from ..db import shared_state
    ip = client_ip() if ip is None else ip
    shared_state.rate_clear(_SCOPE, _key(kind, username, ip))


def locked_message(seconds: int) -> str:
    minutes = max(1, int(math.ceil(seconds / 60)))
    return (f"تم تجاوز عدد محاولات الدخول الفاشلة المسموح. "
            f"حاول مجددًا بعد {minutes} دقيقة.")


def reset_for_tests() -> None:
    from ..db import shared_state
    shared_state.reset_memory()
    try:
        shared_state.rate_clear(_SCOPE)
    except Exception:  # noqa: BLE001 — no DB in this test
        pass
