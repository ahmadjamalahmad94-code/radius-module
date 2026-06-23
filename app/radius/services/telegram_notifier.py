"""Telegram bot sender — minimal, tenant-scoped (Sprint 2).

One function: `send_to_tenant(tenant_id, text)` → posts to
the tenant's configured bot/chat via Telegram's Bot API HTTPS
endpoint. Returns a `(ok, error)` tuple so the caller can
record the result in `network_device_alerts.delivery`.

Failure modes (all return ok=False without raising):
  • Tenant not configured / disabled / missing token-or-chat
  • Network error (DNS, timeout, refused)
  • Telegram returned non-200 (rate limit, bad token, ...)

Why HTTPS-direct instead of an SDK: zero new deps, the
Bot API is one POST with three form fields. The whole
sender is < 50 lines.
"""
from __future__ import annotations

import logging
import urllib.parse
import urllib.request
import urllib.error
from typing import Tuple

from ..db.repos import tenant_telegram_settings_repo

_LOG = logging.getLogger(__name__)

# Telegram's published rate limits are ~30 messages/second
# globally + 1 message/second per chat. Our cron sweep is 60s
# and a healthy fleet has <100 devices, so we never come close.
_API_TIMEOUT_SEC = 8.0
_API_BASE = "https://api.telegram.org/bot"


def send_to_tenant(tenant_id: int, text: str) -> Tuple[bool, str]:
    """Send `text` to the tenant's configured Telegram chat.

    Returns:
      (True, '')        — Telegram acknowledged the message.
      (False, reason)   — Skipped (not configured / disabled)
                          OR delivery failed.

    The caller distinguishes the two via `reason`: empty string
    means «not configured, skipped», non-empty means «we tried
    and Telegram refused».
    """
    cfg = tenant_telegram_settings_repo.get(tenant_id)
    if not cfg:
        return False, ""  # never configured
    if not cfg.get("enabled"):
        return False, ""  # explicitly disabled
    token = cfg.get("bot_token") or ""
    chat_id = cfg.get("chat_id") or ""
    thread_id = cfg.get("thread_id") or ""
    if not token or not chat_id:
        return False, ""  # half-configured

    # Build POST payload. `parse_mode=HTML` lets us send simple
    # <b>bold</b> in alert messages without escaping every glyph.
    fields = {
        "chat_id":     str(chat_id),
        "text":        str(text or "").strip(),
        "parse_mode":  "HTML",
        # «Pop» rather than «silent» — operator wants to hear
        # the phone vibrate when an AP goes down.
        "disable_notification": "false",
    }
    if thread_id:
        # Forum-supergroup thread routing — newer Telegram
        # feature, OK to omit for normal groups.
        fields["message_thread_id"] = str(thread_id)

    return _post_message(token, fields, tenant_id=tenant_id)


def send_to_chat(tenant_id: int, chat_id: str, text: str) -> Tuple[bool, str]:
    """Send `text` to an ARBITRARY chat_id using the TENANT'S bot token.

    Used for SUBSCRIBER notifications: the subscriber connected their own
    Telegram (``subscribers.telegram_chat_id``) and we message them directly
    via the tenant's provider bot. Reuses the canonical sender plumbing —
    NOT a parallel sender.

    Same contract as :func:`send_to_tenant`:
      (True, '')      — delivered.
      (False, '')     — skipped (no bot token / disabled / no chat_id).
      (False, reason) — we tried and Telegram refused.
    """
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return False, ""  # subscriber hasn't connected Telegram
    cfg = tenant_telegram_settings_repo.get(tenant_id)
    if not cfg:
        return False, ""  # tenant bot never configured
    if not cfg.get("enabled"):
        return False, ""  # tenant bot disabled
    token = cfg.get("bot_token") or ""
    if not token:
        return False, ""  # no token to send with
    fields = {
        "chat_id":     chat_id,
        "text":        str(text or "").strip(),
        "parse_mode":  "HTML",
        "disable_notification": "false",
    }
    return _post_message(token, fields, tenant_id=tenant_id)


def _post_message(token: str, fields: dict, *, tenant_id: int) -> Tuple[bool, str]:
    """Shared Telegram Bot API POST. Never raises."""
    url = _API_BASE + token + "/sendMessage"
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type":  "application/x-www-form-urlencoded",
            "User-Agent":    "HobeRadius-Notifier/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT_SEC) as resp:
            if 200 <= resp.status < 300:
                return True, ""
            try:
                body = resp.read(400).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                body = ""
            return False, f"HTTP {resp.status} — {body[:200]}"
    except urllib.error.HTTPError as e:
        try:
            body = e.read(400).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        msg = f"HTTP {e.code} — {body[:200]}"
        _LOG.warning("[telegram] send failed tenant=%d: %s", tenant_id, msg)
        return False, msg
    except urllib.error.URLError as e:
        msg = f"network: {e.reason}"
        _LOG.warning("[telegram] network failure tenant=%d: %s",
                     tenant_id, msg)
        return False, msg
    except Exception as exc:  # noqa: BLE001
        # Last-resort catch — never let a notifier crash the
        # monitor worker.
        _LOG.exception("[telegram] unexpected failure tenant=%d", tenant_id)
        return False, str(exc)[:200]
