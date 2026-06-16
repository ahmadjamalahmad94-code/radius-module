"""tenant_telegram_settings repo (Sprint 2).

Per-tenant Telegram-bot routing for network alerts. Operators
configure this from the Sprint-2 settings page; the notifier
(`services/telegram_notifier.py`) reads it before each send.

`bot_token` + `chat_id` are the only required knobs. If either
is empty OR `enabled = 0`, the notifier silently no-ops — the
fire is still logged in `network_device_alerts` with
`delivery = 'skipped'` for audit.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso

# توكن البوت سرّ — يُخزَّن مشفّرًا (Fernet مشتق من FLASK_SECRET) ببادئة
# ``enc:``. الصفوف القديمة (نص خام) تبقى مقروءة (توافق خلفي).
_ENC_PREFIX = "enc:"


def _encrypt_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    from ...core.env_settings import _encrypt
    ct = _encrypt(token)
    return (_ENC_PREFIX + ct) if ct else token


def _decrypt_token(stored: str) -> str:
    stored = stored or ""
    if not stored.startswith(_ENC_PREFIX):
        return stored  # نص خام قديم (توافق خلفي)
    from ...core.env_settings import _decrypt
    return _decrypt(stored[len(_ENC_PREFIX):])


def get(tenant_id: int) -> Optional[dict]:
    """Return the tenant's Telegram config, or None if the row
    doesn't exist yet (operator never opened the settings)."""
    cur = db().execute(
        "SELECT tenant_id, bot_token, chat_id, enabled, thread_id,"
        "       updated_at "
        "FROM tenant_telegram_settings WHERE tenant_id = ?",
        (int(tenant_id),),
    )
    r = cur.fetchone()
    if not r:
        return None
    return {
        "tenant_id":  int(r["tenant_id"]),
        "bot_token":  _decrypt_token(r["bot_token"] or ""),
        "chat_id":    r["chat_id"] or "",
        "enabled":    bool(r["enabled"]),
        "thread_id":  r["thread_id"] or "",
        "updated_at": r["updated_at"] or "",
    }


def upsert(
    *,
    tenant_id: int,
    bot_token: str = "",
    chat_id: str = "",
    enabled: bool = False,
    thread_id: str = "",
) -> None:
    """Insert or update the tenant's Telegram config in one shot."""
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO tenant_telegram_settings ("
            "  tenant_id, bot_token, chat_id, enabled, thread_id,"
            "  updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tenant_id) DO UPDATE SET "
            "  bot_token  = excluded.bot_token,"
            "  chat_id    = excluded.chat_id,"
            "  enabled    = excluded.enabled,"
            "  thread_id  = excluded.thread_id,"
            "  updated_at = excluded.updated_at",
            (
                int(tenant_id),
                _encrypt_token(bot_token),
                str(chat_id or "").strip(),
                1 if enabled else 0,
                str(thread_id or "").strip(),
                now,
            ),
        )


def is_configured(tenant_id: int) -> bool:
    """Quick check used by the cron worker before fanning out.
    Saves the notifier from a round-trip when nothing's set up.
    """
    cfg = get(tenant_id)
    return bool(cfg and cfg["enabled"]
                and cfg["bot_token"] and cfg["chat_id"])
