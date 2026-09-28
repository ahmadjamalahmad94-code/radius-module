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
Shared by every panel process (leftover wave, 2026-09-28): the panel now runs
several gunicorn worker processes, and the router's `/tool fetch` is a
separate HTTP request that may land on a DIFFERENT process than the publish
that stashed the blob. The blobs therefore live in the shared ``shared_kv``
table (migration 177), consumed on first fetch and dropped at TTL. The token is
a cryptographically-random secret — it IS the auth for the public serve route.
Without the table (bare unit tests) it is process memory, as before.
"""
from __future__ import annotations

import json
import secrets

from ..db import shared_state

# How long a stashed blob stays fetchable. A publish issues the /tool fetch
# command immediately, so the router pulls within seconds; 10 min is a wide
# safety margin for a slow tunnel without keeping bytes around for long.
DEFAULT_TTL_SEC = 600.0
_NS = "hotspot_blob"


def _pack(body: bytes, content_type: str) -> bytes:
    head = json.dumps({"ct": content_type}).encode("utf-8")
    return len(head).to_bytes(4, "big") + head + body


def _unpack(raw) -> tuple[bytes, str] | None:
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 4:
        return None
    n = int.from_bytes(raw[:4], "big")
    head = json.loads(bytes(raw[4:4 + n]).decode("utf-8"))
    return bytes(raw[4 + n:]), str(head.get("ct") or "application/octet-stream")


def stash(body: bytes | str, *, content_type: str = "text/plain; charset=utf-8",
          ttl_sec: float = DEFAULT_TTL_SEC) -> str:
    """Store `body` and return a one-time secret token to fetch it with."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    token = secrets.token_urlsafe(24)
    shared_state.kv_put(_NS, token, _pack(bytes(body), content_type),
                        ttl=max(1.0, float(ttl_sec)))
    return token


def take(token: str) -> tuple[bytes, str] | None:
    """Return (body, content_type) for a valid token AND remove it
    (one-time use). Returns None if missing/expired."""
    if not token:
        return None
    return _unpack(shared_state.kv_pop(_NS, token))


def peek(token: str) -> tuple[bytes, str] | None:
    """Like take() but does NOT consume — for tests / retried fetches."""
    if not token:
        return None
    return _unpack(shared_state.kv_get(_NS, token))


def clear() -> None:
    """Drop everything (tests / shutdown)."""
    try:
        from ..db.connection import transaction
        with transaction() as conn:
            conn.execute("DELETE FROM shared_kv WHERE ns = ?", (_NS,))
    except Exception:  # noqa: BLE001 — no table: the memory fallback below
        pass
    with shared_state._mem_lock:
        for k in [k for k in shared_state._mem_kv if k[0] == _NS]:
            shared_state._mem_kv.pop(k, None)


__all__ = ["stash", "take", "peek", "clear", "DEFAULT_TTL_SEC"]
