"""tenant_sms_settings repo — per-tenant BYO SMS provider credentials.

SMS is a FREE bring-your-own-provider service: each customer (tenant) connects
their OWN external SMS gateway and buys credit from that provider directly.
There is no admin-sold message bundle / quota.

The provider today is TweetSMS (tweetsms.ps). The customer supplies EITHER an
``api_key`` OR a ``username`` + ``password`` pair, plus an approved ``sender``
name. ``api_key`` + ``password`` are SECRETS — stored encrypted (Fernet derived
from FLASK_SECRET) behind the ``enc:`` prefix, exactly like
``tenant_telegram_settings.bot_token``. Old plaintext rows stay readable
(backward compatible). The adapter (``services/tweetsms.py``) reads this before
each send / balance check; the connection page writes it.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso

# الأسرار (مفتاح API / كلمة المرور) تُخزَّن مشفّرة (Fernet مشتق من FLASK_SECRET)
# ببادئة ``enc:``. الصفوف القديمة (نص خام) تبقى مقروءة (توافق خلفي).
_ENC_PREFIX = "enc:"


def _encrypt_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    from ...core.env_settings import _encrypt

    ct = _encrypt(value)
    return (_ENC_PREFIX + ct) if ct else value


def _decrypt_secret(stored: str) -> str:
    stored = stored or ""
    if not stored.startswith(_ENC_PREFIX):
        return stored  # نص خام قديم (توافق خلفي)
    from ...core.env_settings import _decrypt

    return _decrypt(stored[len(_ENC_PREFIX):])


def get(tenant_id: int) -> Optional[dict]:
    """Return the tenant's SMS provider config, or ``None`` if no row exists
    yet (the customer never opened the connection page). Secrets are returned
    DECRYPTED — callers must never log them."""
    cur = db().execute(
        "SELECT tenant_id, provider, api_key, username, password, sender,"
        "       enabled, updated_at "
        "FROM tenant_sms_settings WHERE tenant_id = ?",
        (int(tenant_id),),
    )
    r = cur.fetchone()
    if not r:
        return None
    return {
        "tenant_id":  int(r["tenant_id"]),
        "provider":   r["provider"] or "tweetsms",
        "api_key":    _decrypt_secret(r["api_key"] or ""),
        "username":   r["username"] or "",
        "password":   _decrypt_secret(r["password"] or ""),
        "sender":     r["sender"] or "",
        "enabled":    bool(r["enabled"]),
        "updated_at": r["updated_at"] or "",
    }


def upsert(
    *,
    tenant_id: int,
    provider: str = "tweetsms",
    api_key: str = "",
    username: str = "",
    password: str = "",
    sender: str = "",
    enabled: bool = False,
) -> None:
    """Insert or update the tenant's SMS provider config in one shot. Secrets
    are encrypted before they touch the database."""
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO tenant_sms_settings ("
            "  tenant_id, provider, api_key, username, password, sender,"
            "  enabled, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tenant_id) DO UPDATE SET "
            "  provider   = excluded.provider,"
            "  api_key    = excluded.api_key,"
            "  username   = excluded.username,"
            "  password   = excluded.password,"
            "  sender     = excluded.sender,"
            "  enabled    = excluded.enabled,"
            "  updated_at = excluded.updated_at",
            (
                int(tenant_id),
                str(provider or "tweetsms").strip() or "tweetsms",
                _encrypt_secret(api_key),
                str(username or "").strip(),
                _encrypt_secret(password),
                str(sender or "").strip(),
                1 if enabled else 0,
                now,
            ),
        )


def has_credentials(cfg: Optional[dict]) -> bool:
    """A config can authenticate when it carries either an api_key OR a full
    username+password pair."""
    if not cfg:
        return False
    if (cfg.get("api_key") or "").strip():
        return True
    return bool((cfg.get("username") or "").strip() and (cfg.get("password") or "").strip())


def is_configured(tenant_id: int) -> bool:
    """Quick check used before fanning out a real send: enabled + has creds +
    has a sender name."""
    cfg = get(tenant_id)
    return bool(cfg and cfg["enabled"] and has_credentials(cfg) and (cfg.get("sender") or "").strip())
