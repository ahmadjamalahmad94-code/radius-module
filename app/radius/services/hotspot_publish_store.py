"""Transient token→blob store for router-pull publishing (/tool fetch).

WHY
---
Onboarding hardening disables FTP on the router (`/ip service disable ftp`),
so the hotspot login-page publish can no longer push large files over FTP.
Instead the ROUTER pulls each file from the panel over the management
tunnel with `/tool fetch http://<panel>/.../hotspot/pull/<token>`. This
module holds the rendered bytes for that pull, keyed by an unguessable
one-time token, for a short TTL.

SCOPE
-----
In-process, per-worker, thread-safe. The deployment runs a single gunicorn
worker (see deploy/gunicorn.conf.py — background workers are in-process
singletons), and the router fetches each blob within seconds of the panel
issuing the `/tool fetch` command, so a process-local store is the right
scope: no migration, no secret persisted to disk, and the blob evaporates
on TTL / first fetch. The token is a cryptographically-random secret — it
IS the auth for the public serve route.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

# How long a stashed blob stays fetchable. A publish issues the /tool fetch
# command immediately, so the router pulls within seconds; 10 min is a wide
# safety margin for a slow tunnel without keeping bytes around for long.
DEFAULT_TTL_SEC = 600.0


@dataclass
class _Blob:
    body: bytes
    content_type: str
    expires_at: float


_lock = threading.Lock()
_blobs: dict[str, _Blob] = {}


def _now() -> float:
    return time.monotonic()


def _purge_expired(now: float) -> None:
    """Drop expired entries. Caller holds the lock."""
    dead = [t for t, b in _blobs.items() if b.expires_at <= now]
    for t in dead:
        _blobs.pop(t, None)


def stash(body: bytes | str, *, content_type: str = "text/plain; charset=utf-8",
          ttl_sec: float = DEFAULT_TTL_SEC) -> str:
    """Store `body` and return a one-time secret token to fetch it with."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    token = secrets.token_urlsafe(24)
    now = _now()
    with _lock:
        _purge_expired(now)
        _blobs[token] = _Blob(
            body=bytes(body), content_type=content_type,
            expires_at=now + max(1.0, float(ttl_sec)),
        )
    return token


def take(token: str) -> tuple[bytes, str] | None:
    """Return (body, content_type) for a valid token AND remove it
    (one-time use). Returns None if missing/expired."""
    if not token:
        return None
    now = _now()
    with _lock:
        _purge_expired(now)
        blob = _blobs.pop(token, None)
    if blob is None:
        return None
    return blob.body, blob.content_type


def peek(token: str) -> tuple[bytes, str] | None:
    """Like take() but does NOT consume — for tests / retried fetches."""
    if not token:
        return None
    now = _now()
    with _lock:
        _purge_expired(now)
        blob = _blobs.get(token)
    if blob is None:
        return None
    return blob.body, blob.content_type


def clear() -> None:
    """Drop everything (tests / shutdown)."""
    with _lock:
        _blobs.clear()


__all__ = ["stash", "take", "peek", "clear", "DEFAULT_TTL_SEC"]
