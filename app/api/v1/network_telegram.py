"""network-telegram — v1 JSON API (feat/api-first-parity, group 5).

Mirrors the network Telegram alerts settings page
(`routes/network_telegram_settings.py`, `/admin/radius/network/telegram`):
the tenant's Telegram config (bot token / chat / thread / enabled) and a
live "send test" action. Reuses `tenant_telegram_settings_repo` +
`telegram_notifier` (no duplicated logic).

Secret safety: the bot token is a secret. GET never returns it raw — it
returns ``has_bot_token`` + a masked tail. Update is PATCH-style: only fields
present in the body change (so a client that didn't fetch the token can't
accidentally wipe it). This differs from the web form's full-replace, but is
the safe JSON equivalent.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.db.repos import tenant_telegram_settings_repo as repo
from ...radius.services import telegram_notifier
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/network/telegram", "network_telegram_get",
                    require_api_token(get_settings), methods=["GET"])
    bp.add_url_rule("/network/telegram", "network_telegram_save",
                    require_api_token(save_settings), methods=["PATCH", "PUT"])
    bp.add_url_rule("/network/telegram/test", "network_telegram_test",
                    require_api_token(send_test), methods=["POST"])


def _mask(token: str) -> str:
    t = str(token or "")
    if not t:
        return ""
    return ("…" + t[-4:]) if len(t) > 4 else "…"


def _public(row: dict) -> dict:
    """تمثيل آمن: بلا قيمة التوكن الخام."""
    token = str(row.get("bot_token") or "")
    return {
        "tenant_id": row.get("tenant_id"),
        "has_bot_token": bool(token),
        "bot_token_masked": _mask(token),
        "chat_id": row.get("chat_id") or "",
        "thread_id": row.get("thread_id") or "",
        "enabled": bool(row.get("enabled")),
        "updated_at": row.get("updated_at") or "",
        # جاهزية الإرسال — مطابِقة لشرط صفحة الويب (enabled + token + chat).
        "ready": bool(row.get("enabled") and token and (row.get("chat_id") or "")),
    }


def _current(tid: int) -> dict:
    return repo.get(tid) or {
        "tenant_id": tid, "bot_token": "", "chat_id": "",
        "enabled": False, "thread_id": "", "updated_at": "",
    }


def get_settings():
    """GET /network/telegram — الإعدادات (التوكن مُقنّع)."""
    return ok({"settings": _public(_current(_tid()))})


def save_settings():
    """PATCH/PUT /network/telegram — حفظ الإعدادات. الحقول الغائبة من الجسم
    تبقى كما هي (يمنع مسح التوكن سهوًا). يطابق upsert صفحة الويب."""
    body = request.get_json(silent=True) or {}
    cur = _current(_tid())

    def _pick(key: str, default):
        return body[key] if key in body else default

    bot_token = str(_pick("bot_token", cur.get("bot_token") or "")).strip()
    chat_id = str(_pick("chat_id", cur.get("chat_id") or "")).strip()
    thread_id = str(_pick("thread_id", cur.get("thread_id") or "")).strip()
    enabled = bool(_pick("enabled", bool(cur.get("enabled"))))
    repo.upsert(tenant_id=_tid(), bot_token=bot_token, chat_id=chat_id,
                enabled=enabled, thread_id=thread_id)
    return ok({"settings": _public(_current(_tid()))})


def send_test():
    """POST /network/telegram/test — إرسال رسالة اختبار الآن (يطابق
    network_telegram_test). يُعيد ok/خطأ المُرسِل."""
    test_text = (
        "✅ <b>اختبار التنبيهات</b>\n"
        "تم إرسال هذه الرسالة من إعدادات تلجرام في HobeRadius.\n"
        "إذا تستلمها — إعداداتك صحيحة وستصلك التنبيهات الفعلية عند انقطاع جهاز."
    )
    sent, err = telegram_notifier.send_to_tenant(_tid(), test_text)
    if sent:
        return ok({"sent": True})
    return fail("telegram_send_failed",
                err or "لم يتم الإرسال — تأكّد من التوكن وchat id وتفعيل التنبيهات.",
                status=502, details={"sent": False})
