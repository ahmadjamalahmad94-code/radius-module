"""telegram_connect — «اربط تيليجرام» بضغطة واحدة (آليّة قابلة لإعادة الاستخدام).

يستبدل الخطوة اليدويّة المؤلمة (الذهاب لـBotFather، نسخ «معرّف المحادثة Chat
ID») بمسار ربط آليّ:

  1. start_link(tenant, scope) — يشتقّ اسم البوت من التوكن عبر ``getMe`` (لا
     يكتبه المستخدم)، يولّد «رمز ربط» لمرّة واحدة، ويُعيد رابطًا عميقًا
     ``https://t.me/<bot>?start=<code>`` + رمز QR (SVG) يعمل على الهاتف.
  2. المستخدم يضغط START داخل تيليجرام فيصل البوت ``/start <code>``.
  3. poll_link(tenant, scope) — أثناء نافذة الربط (~دقيقتان) نستطلع ``getUpdates``
     (استطلاع طويل بلا webhook — لا يحتاج HTTPS عامًّا ويطابق طريقة اللوحة في
     بلوغ api.telegram.org)، نطابق ``<code>`` ونخزّن chat_id تلقائيًّا.

كل طلبات HTTP تمرّ عبر ``_api_call`` (طبقة واحدة) كي تُحاكى في الاختبارات. لا
mock في الإنتاج: نستدعي getMe/getUpdates فعليًّا، ونحمي حالتَي «لا توكن» و«فشل
الشبكة» برسالة واضحة.

النطاق scope:
  admin       — بوت تنبيهات المستأجر؛ النجاح يكتب chat_id في
                tenant_telegram_settings (يستبدل خطوة Chat ID اليدويّة).
  subscriber  — حساب مشترك؛ النجاح يخزّن chat_id في رمز الربط (Phase 2 يربطه
                سطح إشعارات المشترك بجدول مخصّص).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from ..db.repos import telegram_link_codes_repo as codes_repo
from ..db.repos import tenant_telegram_settings_repo as tg_repo

_LOG = logging.getLogger(__name__)
_API_BASE = "https://api.telegram.org/bot"
_API_TIMEOUT_SEC = 10.0
_LINK_TTL_SEC = 120


class TelegramNetworkError(Exception):
    """فشل شبكي عند بلوغ api.telegram.org (DNS/مهلة/رفض)."""


# ════════════════════════════════════════════════════════════════════════
# طبقة HTTP وحيدة (تُحاكى في الاختبارات)
# ════════════════════════════════════════════════════════════════════════
def _api_call(token: str, method: str, params: Optional[dict] = None) -> dict:
    """يستدعي Bot API ويُعيد JSON المُحلَّل (``{ok, result|error_code,
    description}``). يرفع TelegramNetworkError على فشل الشبكة فقط؛ ردود
    تيليجرام بـok=False تُعاد كما هي ليفحصها المُستدعي."""
    url = _API_BASE + token + "/" + method
    data = urllib.parse.urlencode(params or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "HobeRadius-Connect/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        # تيليجرام يُرجع JSON صالحًا حتى مع 4xx (bad token/409 webhook).
        try:
            return json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "error_code": e.code,
                    "description": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError) as e:
        raise TelegramNetworkError(str(getattr(e, "reason", e)))
    except Exception as exc:  # noqa: BLE001
        raise TelegramNetworkError(str(exc)[:200])


# ════════════════════════════════════════════════════════════════════════
# getMe — اشتقاق اسم/هوية البوت
# ════════════════════════════════════════════════════════════════════════
def get_me(token: str) -> dict:
    """يُعيد ``{ok, username, name, id, error}``. لا يرفع استثناء."""
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "لا يوجد توكن بوت محفوظ."}
    try:
        res = _api_call(token, "getMe")
    except TelegramNetworkError as e:
        return {"ok": False, "error": f"تعذّر الاتصال بتيليجرام: {e}"}
    if not res.get("ok"):
        desc = res.get("description") or "توكن غير صالح"
        return {"ok": False, "error": f"تيليجرام رفض التوكن: {desc}"}
    r = res.get("result") or {}
    username = r.get("username") or ""
    if not username:
        return {"ok": False, "error": "البوت بلا اسم مستخدم (username)."}
    return {
        "ok": True,
        "username": username,
        "name": r.get("first_name") or username,
        "id": r.get("id"),
        "error": "",
    }


# ════════════════════════════════════════════════════════════════════════
# مساعدات داخليّة
# ════════════════════════════════════════════════════════════════════════
def _resolve_token(tenant_id: int, explicit: Optional[str]) -> str:
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    cfg = tg_repo.get(int(tenant_id)) or {}
    return (cfg.get("bot_token") or "").strip()


def _save_admin_token(tenant_id: int, token: str) -> None:
    """يحفظ توكنًا جديدًا للمستأجر دون مساس بـchat_id/الموضوع/التفعيل."""
    cur = tg_repo.get(int(tenant_id)) or {}
    tg_repo.upsert(
        tenant_id=int(tenant_id), bot_token=token,
        chat_id=cur.get("chat_id") or "",
        enabled=bool(cur.get("enabled")),
        thread_id=cur.get("thread_id") or "",
    )


def _chat_display_name(chat: dict) -> str:
    """اسم مقروء للحساب/المجموعة المربوطة."""
    if not isinstance(chat, dict):
        return ""
    if chat.get("title"):
        return str(chat["title"])
    parts = [chat.get("first_name") or "", chat.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    if name:
        return name
    return ("@" + chat["username"]) if chat.get("username") else ""


def _drain_updates(token: str, tenant_id: int) -> None:
    """يتقدّم بمؤشّر getUpdates خلف أي تحديثات قديمة كي تلتقط النافذة رسائل
    /start الجديدة فقط (المستخدم يضغط الرابط بعد الضغط على «اربط»)."""
    try:
        cursor = codes_repo.get_cursor(tenant_id)
        res = _api_call(token, "getUpdates",
                        {"offset": cursor, "timeout": 0, "limit": 100})
    except TelegramNetworkError:
        return
    if not res.get("ok"):
        return
    updates = res.get("result") or []
    if updates:
        last = max(int(u.get("update_id", 0)) for u in updates)
        codes_repo.set_cursor(tenant_id, last + 1)


# ════════════════════════════════════════════════════════════════════════
# (1) بدء الربط
# ════════════════════════════════════════════════════════════════════════
def start_link(
    tenant_id: int, *,
    scope: str = "admin",
    subscriber_id: int = 0,
    token: Optional[str] = None,
    ttl_sec: int = _LINK_TTL_SEC,
    qr: bool = True,
) -> dict:
    """يبدأ نافذة ربط. يُعيد عند النجاح:
    ``{ok, username, bot_name, deep_link, code, qr_svg, expires_in}``
    أو ``{ok:False, error}``."""
    tid = int(tenant_id)
    tok = _resolve_token(tid, token)
    if not tok:
        return {"ok": False, "error": "أضف توكن البوت أولًا (من BotFather)."}

    me = get_me(tok)
    if not me.get("ok"):
        return {"ok": False, "error": me.get("error") or "تعذّر التحقّق من البوت."}

    # احفظ التوكن الجديد (admin) كي يعمل الإرسال فورًا بعد الربط.
    if scope == "admin" and (token or "").strip():
        _save_admin_token(tid, tok)

    # تخلّص من webhook قديم إن وُجد (getUpdates و webhook متعارضان). أفضل جهد.
    try:
        _api_call(tok, "deleteWebhook", {"drop_pending_updates": "false"})
    except TelegramNetworkError:
        pass
    # تجاهل أي backlog كي تلتقط النافذة /start الجديد فقط.
    _drain_updates(tok, tid)

    rec = codes_repo.create_code(tenant_id=tid, scope=scope,
                                 subscriber_id=int(subscriber_id),
                                 ttl_sec=int(ttl_sec))
    deep_link = "https://t.me/%s?start=%s" % (me["username"], rec["code"])
    out = {
        "ok": True,
        "username": me["username"],
        "bot_name": me["name"],
        "deep_link": deep_link,
        "code": rec["code"],
        "expires_in": int(ttl_sec),
    }
    if qr:
        from .qr_svg import qr_svg
        out["qr_svg"] = qr_svg(deep_link)
    return out


# ════════════════════════════════════════════════════════════════════════
# (2) استطلاع الالتقاط
# ════════════════════════════════════════════════════════════════════════
def _bind_capture(rec: dict, chat: dict) -> Tuple[str, str]:
    """يخزّن chat_id الملتقَط في الرمز (+ tenant_telegram_settings لنطاق admin).
    يُعيد ``(chat_id, account_name)``."""
    chat_id = str(chat.get("id") or "")
    name = _chat_display_name(chat)
    codes_repo.mark_linked(rec["code"], chat_id=chat_id, account_name=name)
    if not chat_id:
        return chat_id, name
    if rec.get("scope") == "admin":
        cur = tg_repo.get(rec["tenant_id"]) or {}
        tg_repo.upsert(
            tenant_id=rec["tenant_id"],
            bot_token=cur.get("bot_token") or "",
            chat_id=chat_id,
            enabled=True,  # الربط يعني «جاهز» — فعّل القناة فورًا.
            thread_id=cur.get("thread_id") or "",
        )
    elif rec.get("scope") == "subscriber" and int(rec.get("subscriber_id") or 0):
        # سطح إشعارات المشترك (Phase 2): خزّن chat_id على ملف المشترك
        # (أعمدة migration 133). يبقى محفوظًا أيضًا في رمز الربط للتدقيق.
        try:
            from ..db.connection import transaction
            with transaction() as conn:
                conn.execute(
                    "UPDATE subscribers SET telegram_chat_id=?, "
                    "telegram_account_name=? WHERE id=?",
                    (chat_id, name, int(rec["subscriber_id"])),
                )
        except Exception:  # noqa: BLE001 — الجدول/العمود قد لا يكون متاحًا
            _LOG.warning("telegram_connect: subscriber bind failed", exc_info=True)
    return chat_id, name


def _scan_update_for_code(tenant_id: int, update: dict) -> Optional[dict]:
    """إن حملت الرسالة ``/start <code>`` لرمز معلّق لهذا المستأجر، يربطه ويُعيد
    سجلّ الالتقاط؛ وإلّا None."""
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    if not text.startswith("/start"):
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    rec = codes_repo.get_by_code(payload)
    if not rec or rec["status"] != "pending":
        return None
    if int(rec["tenant_id"]) != int(tenant_id):
        return None
    chat = msg.get("chat") or {}
    chat_id, name = _bind_capture(rec, chat)
    return {"code": rec["code"], "scope": rec["scope"],
            "subscriber_id": rec["subscriber_id"],
            "chat_id": chat_id, "account_name": name}


def _mask_chat_id(chat_id: str) -> str:
    s = str(chat_id or "")
    return ("…" + s[-4:]) if len(s) > 4 else s


def poll_link(
    tenant_id: int, *,
    scope: str = "admin",
    subscriber_id: int = 0,
) -> dict:
    """يستطلع getUpdates مرّة ويلتقط أي /start مطابق. يُعيد:
    ``{ok, linked, status, account_name, chat_id_masked, error}``.

    ``linked`` يصدق إذا التُقط الرمز النشط لهذا النطاق في هذا الاستطلاع أو
    سابقًا. ``status``: linked|pending|expired."""
    tid = int(tenant_id)
    codes_repo.expire_stale()

    active = codes_repo.get_active(tenant_id=tid, scope=scope,
                                   subscriber_id=int(subscriber_id))
    # إن لم يبقَ رمز معلّق، فحص آخر سجل: ربّما التُقط (linked) أو انتهى.
    if not active:
        recent = _recent_record(tid, scope, subscriber_id)
        if recent and recent["status"] == "linked":
            return {"ok": True, "linked": True, "status": "linked",
                    "account_name": recent["account_name"],
                    "chat_id_masked": _mask_chat_id(recent["chat_id"]),
                    "error": ""}
        return {"ok": True, "linked": False, "status": "expired",
                "account_name": "", "chat_id_masked": "",
                "error": "انتهت نافذة الربط — أعد المحاولة."}

    tok = _resolve_token(tid, None)
    if not tok:
        return {"ok": False, "linked": False, "status": "pending",
                "account_name": "", "chat_id_masked": "",
                "error": "لا يوجد توكن بوت."}

    captured = _poll_once(tid, tok)
    if captured and captured["code"] == active["code"]:
        return {"ok": True, "linked": True, "status": "linked",
                "account_name": captured["account_name"],
                "chat_id_masked": _mask_chat_id(captured["chat_id"]),
                "error": ""}

    # ربّما التُقط الرمز النشط في استطلاع سابق (نفس النافذة)؛ أعد فحصه.
    refreshed = codes_repo.get_by_code(active["code"])
    if refreshed and refreshed["status"] == "linked":
        return {"ok": True, "linked": True, "status": "linked",
                "account_name": refreshed["account_name"],
                "chat_id_masked": _mask_chat_id(refreshed["chat_id"]),
                "error": ""}

    return {"ok": True, "linked": False, "status": "pending",
            "account_name": "", "chat_id_masked": "", "error": ""}


def _poll_once(tenant_id: int, token: str) -> Optional[dict]:
    """جلب getUpdates مرّة + معالجة كل التحديثات + تقديم المؤشّر. يُعيد سجلّ
    أحدث التقاط مطابق (إن وُجد)."""
    cursor = codes_repo.get_cursor(tenant_id)
    try:
        res = _api_call(token, "getUpdates",
                        {"offset": cursor, "timeout": 0, "limit": 100})
    except TelegramNetworkError as e:
        _LOG.info("telegram_connect: getUpdates network fail: %s", e)
        return None
    if not res.get("ok"):
        # 409 = webhook نشط يتعارض مع getUpdates → احذفه وأعد المحاولة مرّة.
        if int(res.get("error_code") or 0) == 409:
            try:
                _api_call(token, "deleteWebhook", {"drop_pending_updates": "false"})
                res = _api_call(token, "getUpdates",
                                {"offset": cursor, "timeout": 0, "limit": 100})
            except TelegramNetworkError:
                return None
        if not res.get("ok"):
            return None
    updates = res.get("result") or []
    captured = None
    max_id = cursor - 1
    for u in updates:
        max_id = max(max_id, int(u.get("update_id", 0)))
        hit = _scan_update_for_code(tenant_id, u)
        if hit:
            captured = hit
    if updates:
        codes_repo.set_cursor(tenant_id, max_id + 1)
    return captured


def _recent_record(tenant_id: int, scope: str, subscriber_id: int) -> Optional[dict]:
    """آخر سجل (بأي حالة) لنطاق/مشترك — لإظهار «متصل ✓» بعد انتهاء النافذة."""
    return codes_repo.recent(tenant_id=int(tenant_id), scope=scope,
                             subscriber_id=int(subscriber_id))


# ════════════════════════════════════════════════════════════════════════
# حالة الربط الحاليّة (للعرض)
# ════════════════════════════════════════════════════════════════════════
def connection_status(tenant_id: int, *, scope: str = "admin",
                      subscriber_id: int = 0) -> dict:
    """ملخّص للعرض: هل مربوط؟ + اسم الحساب + هل التوكن موجود."""
    tok = _resolve_token(int(tenant_id), None)
    recent = _recent_record(int(tenant_id), scope, int(subscriber_id))
    linked = bool(recent and recent["status"] == "linked" and recent["chat_id"])
    return {
        "has_token": bool(tok),
        "linked": linked,
        "account_name": recent["account_name"] if linked else "",
        "chat_id_masked": _mask_chat_id(recent["chat_id"]) if linked else "",
    }
