"""admin-alerts (telegram) — v1 JSON API (feat/api-first-endpoints).

Mirrors the Telegram admin-alerts page
(`routes/admin_alerts.py`, `/admin/radius/alerts/telegram`): the alert
catalogue (per-tenant enable state + rendered preview), the bot config,
and the test/toggle actions. Reuses the `admin_alerts` service +
`tenant_telegram_settings_repo` — no duplicated logic.

Secret safety: the bot token is a secret. GET never returns it raw — only
`has_token` + a masked tail. Bot save is PATCH-style: an empty/absent token
keeps the stored one (so a client that didn't fetch it can't wipe it),
matching the web form's "blank = keep current".
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.db.repos import tenant_telegram_settings_repo as tg_repo
from ...radius.services import admin_alerts
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/alerts/telegram", "alerts_telegram_get",
                    require_api_token(get_catalogue), methods=["GET"])
    bp.add_url_rule("/alerts/telegram/bot", "alerts_telegram_bot_save",
                    require_api_token(save_bot), methods=["PATCH", "PUT"])
    bp.add_url_rule("/alerts/telegram/test-connection",
                    "alerts_telegram_test_connection",
                    require_api_token(test_connection), methods=["POST"])
    bp.add_url_rule("/alerts/telegram/alerts/<key>/toggle",
                    "alerts_telegram_toggle",
                    require_api_token(toggle_alert), methods=["POST"])
    bp.add_url_rule("/alerts/telegram/alerts/<key>/test",
                    "alerts_telegram_alert_test",
                    require_api_token(test_alert), methods=["POST"])


def _mask(token: str) -> str:
    t = token or ""
    return ("…" + t[-4:]) if len(t) > 4 else ("…" if t else "")


def _bot_view(tid: int) -> dict:
    """نفس شكل _bot_view في صفحة الويب — بلا التوكن الخام."""
    cfg = tg_repo.get(tid) or {}
    token = cfg.get("bot_token") or ""
    return {
        "has_token": bool(token),
        "token_masked": _mask(token),
        "chat_id": cfg.get("chat_id") or "",
        "thread_id": cfg.get("thread_id") or "",
        "enabled": bool(cfg.get("enabled")),
        "ready": admin_alerts.telegram_ready(tid),
    }


def _groups() -> list[dict]:
    """مجموعات العرض كـ JSON (المصدر tuples: key, label, icon)."""
    return [{"key": k, "label": label, "icon": icon}
            for (k, label, icon) in admin_alerts.GROUPS]


def get_catalogue():
    """GET /alerts/telegram — البوت + المجموعات + جرد التنبيهات (مع حالة
    التفعيل والمعاينة لكل تنبيه). يطابق سياق صفحة الويب."""
    tid = _tid()
    return ok({
        "bot": _bot_view(tid),
        "groups": _groups(),
        "catalogue": admin_alerts.catalogue(tid),
    })


def save_bot():
    """PATCH/PUT /alerts/telegram/bot — حفظ بيانات البوت. التوكن الفارغ/الغائب
    يُبقي المخزَّن (لا يُمسح سهوًا) — يطابق «فارغ = إبقاء الحالي» في الويب."""
    body = request.get_json(silent=True) or {}
    tid = _tid()
    cur = tg_repo.get(tid) or {}
    new_token = str(body.get("bot_token") or "").strip()
    token = new_token or (cur.get("bot_token") or "")
    chat_id = str(body["chat_id"] if "chat_id" in body else (cur.get("chat_id") or "")).strip()
    thread_id = str(body["thread_id"] if "thread_id" in body else (cur.get("thread_id") or "")).strip()
    enabled = bool(body["enabled"] if "enabled" in body else bool(cur.get("enabled")))
    tg_repo.upsert(tenant_id=tid, bot_token=token, chat_id=chat_id,
                   enabled=enabled, thread_id=thread_id)
    return ok({"bot": _bot_view(tid)})


def test_connection():
    """POST /alerts/telegram/test-connection — اختبار اتصال البوت (يطابق
    admin_alerts.test_connection)."""
    result = admin_alerts.test_connection(_tid())
    if result.get("ok"):
        return ok({"sent": True})
    return fail("telegram_send_failed",
                result.get("error") or "تعذّر الإرسال — تأكّد من بيانات البوت.",
                status=502, details={"sent": False})


def toggle_alert(key: str):
    """POST /alerts/telegram/alerts/<key>/toggle — تفعيل/تعطيل تنبيه واحد.
    body: {enabled: bool}."""
    key = (key or "").strip()
    if not admin_alerts.get_spec(key):
        return fail("not_found", "تنبيه غير معروف.", status=404)
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    admin_alerts.set_enabled(_tid(), key, enabled, by=int(getattr(g, "admin_id", 0) or 0))
    return ok({"key": key, "enabled": enabled})


def test_alert(key: str):
    """POST /alerts/telegram/alerts/<key>/test — إرسال نموذج التنبيه + إعادة
    النصّ المُصيَّر (يطابق admin_alerts.send_test)."""
    key = (key or "").strip()
    if not admin_alerts.get_spec(key):
        return fail("not_found", "تنبيه غير معروف.", status=404)
    result = admin_alerts.send_test(_tid(), key)
    payload = {"key": key, "sent": bool(result.get("ok")),
               "rendered": result.get("text") or ""}
    if result.get("ok"):
        return ok(payload)
    return fail("telegram_send_failed", result.get("error") or "تعذّر الإرسال.",
                status=502, details=payload)
