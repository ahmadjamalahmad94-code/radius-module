"""Failed-login throttle — per (kind, username, client IP), in-memory.

Stress campaign 2026-09-28: 20 wrong passwords on ``POST /api/admin/login``
were all answered 401 in ~2 s and the right password then logged in at once —
no lockout, no delay (the same for the web login, HTTP Basic on the API and the
current-password check of ``/api/admin/password``). This module adds a sliding
window: after ``MAX_FAILURES`` failures inside ``WINDOW_SECONDS`` for the same
username from the same address, further attempts are refused with 429 (even
with the right password) until the oldest failure leaves the window. A
successful login clears the counter.

In-memory by design: the deployment runs ONE gunicorn worker (threads share
this dict under a lock), no DB writes on the hot path, nothing to migrate. A
restart forgets the counters — acceptable for a brute-force brake.

Keys are namespaced per Flask app (a random id) so test files that build
many apps in one process do not leak lockouts into each other.
"""
from __future__ import annotations

import math
import os
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

_DEFAULT_MAX_FAILURES = 10
_DEFAULT_WINDOW_MINUTES = 15

_lock = Lock()
_failures: dict[str, deque] = defaultdict(deque)


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
    prefix = ""
    try:
        from flask import current_app
        # a random namespace per Flask app (production runs one app; test
        # files create many — an id() of a freed app object can be reused)
        ns = current_app.extensions.setdefault("login_throttle_ns", uuid.uuid4().hex)
        prefix = f"{ns}:"
    except Exception:  # noqa: BLE001 — outside an app context
        prefix = ""
    return f"{prefix}{kind}:{(username or '').strip().lower()}|{ip or ''}"


def _prune(log: deque, now: float, window: int) -> None:
    while log and (now - log[0]) >= window:
        log.popleft()


def retry_after(kind: str, username: str, ip: str | None = None) -> int:
    """Seconds until another attempt is allowed; 0 = not locked."""
    ip = client_ip() if ip is None else ip
    key = _key(kind, username, ip)
    now = time.monotonic()
    window = window_seconds()
    with _lock:
        log = _failures.get(key)
        if not log:
            return 0
        _prune(log, now, window)
        if len(log) < max_failures():
            if not log:
                _failures.pop(key, None)
            return 0
        return max(1, int(math.ceil(window - (now - log[0]))))


def register_failure(kind: str, username: str, ip: str | None = None) -> None:
    ip = client_ip() if ip is None else ip
    key = _key(kind, username, ip)
    now = time.monotonic()
    with _lock:
        log = _failures[key]
        _prune(log, now, window_seconds())
        log.append(now)


def register_success(kind: str, username: str, ip: str | None = None) -> None:
    ip = client_ip() if ip is None else ip
    with _lock:
        _failures.pop(_key(kind, username, ip), None)


def locked_message(seconds: int) -> str:
    minutes = max(1, int(math.ceil(seconds / 60)))
    return (f"تم تجاوز عدد محاولات الدخول الفاشلة المسموح. "
            f"حاول مجددًا بعد {minutes} دقيقة.")


def reset_for_tests() -> None:
    with _lock:
        _failures.clear()
