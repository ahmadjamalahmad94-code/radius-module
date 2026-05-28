"""Router-event HMAC token (Sprint 6).

Generates + validates a short token the customer's MikroTik
embeds in webhook URLs when a netwatch script fires. We use
HMAC-SHA256 keyed by a server secret so:

  • The token is deterministic for a given (tenant_id, device_id)
    pair — same value across restarts, no DB lookup needed on
    the receive side, no token-table to manage.
  • Anyone who doesn't know the secret can't forge a webhook,
    even if they know which device IDs exist.
  • Rotating the secret invalidates every router-side script —
    the planner will re-emit fresh URLs on next apply.

Secret source: `HOBERADIUS_NETWATCH_SECRET` env var. If unset,
we fall back to a process-local random secret (still secure
within one process, but invalidates on restart — fine for dev,
WARNS for prod).
"""
from __future__ import annotations

import hmac
import hashlib
import logging
import os
import secrets
from typing import Tuple

_LOG = logging.getLogger(__name__)


def _secret() -> bytes:
    """The HMAC key. Sourced from env in prod, generated once
    per process otherwise. We cache the fallback as a module
    attribute so consecutive calls within the same process
    are stable."""
    raw = (os.environ.get("HOBERADIUS_NETWATCH_SECRET") or "").strip()
    if raw:
        return raw.encode("utf-8")
    # Fallback: stable for the life of THIS process. Logs a
    # warning so an ops person sees it in startup output.
    global _PROC_FALLBACK
    try:
        return _PROC_FALLBACK
    except NameError:
        _PROC_FALLBACK = secrets.token_bytes(32)
        _LOG.warning(
            "[router-events] HOBERADIUS_NETWATCH_SECRET not set — "
            "using process-local fallback; existing router scripts "
            "will need re-apply after each restart.",
        )
        return _PROC_FALLBACK


def make_token(tenant_id: int, device_id: int) -> str:
    """The HMAC token embedded in the router's webhook URL.
    16 hex chars (64 bits) — plenty for un-guessability while
    staying short enough not to bloat the RouterOS script."""
    payload = f"{int(tenant_id)}:{int(device_id)}".encode("utf-8")
    digest = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return digest[:8].hex()


def verify_token(tenant_id: int, device_id: int, token: str) -> bool:
    """Constant-time check. Returns True iff `token` is the one
    we'd emit for (tenant_id, device_id) under the current
    secret. The caller still has to validate that the device
    belongs to the tenant before trusting the call."""
    expected = make_token(tenant_id, device_id)
    return hmac.compare_digest(expected, str(token or "").strip())


def split_event(tenant_id: int, query: dict) -> Tuple[int, str, str] | None:
    """Pulls the three expected query params (device_id, state,
    token) out of a Flask `request.args`-style dict + verifies
    the token. Returns (device_id, state, raw_token) on success,
    None on any validation failure."""
    try:
        device_id = int(query.get("device_id") or 0)
    except (TypeError, ValueError):
        return None
    state = str(query.get("state") or "").lower().strip()
    if state not in ("up", "down"):
        return None
    token = str(query.get("token") or "").strip()
    if not device_id or not token:
        return None
    if not verify_token(int(tenant_id), device_id, token):
        return None
    return device_id, state, token
