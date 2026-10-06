"""مساعد العمليّات (تجريبيّ) — صفحة المحادثة في لوحة الويب.

The page is a thin shell over the deterministic executor (``/api/v1/ops/*``,
docs/OPS_EXECUTOR.md): every call runs IN-PROCESS through the real API as the
logged-in admin (``services/ops_assistant/web_bridge``) — same RBAC, scoping,
caps, audit — and the model loop lives in ``services/ops_assistant/
conversation``. Nothing executes without the admin's «تأكيد» click.

  GET  /admin/radius/ops-assistant               page (or why it is unavailable)
  POST /admin/radius/ops-assistant/message       {conversation_id?, text} → replies
  POST /admin/radius/ops-assistant/confirm       {conversation_id, proposal_id, proposal_hash}
  POST /admin/radius/ops-assistant/cancel        {conversation_id}
  GET  /admin/radius/ops-assistant/events        level-4 suggestions for this admin
  POST /admin/radius/ops-assistant/start-event   {event_type, index} → replies

Guards: login (``login_required`` + the blueprint's global guard), CSRF
(global ``_csrf_check`` → ``X-CSRFToken``), tenant flag + password gate
(``gate.availability``) on every endpoint; each action's permission is
decided by the executor/API for THIS admin.
"""
from __future__ import annotations
from app.i18n_text import _tr

import threading
import time
from typing import Any

from flask import Blueprint, jsonify, render_template, request, session

from ..auth.decorators import login_required


def register_ops_assistant_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/ops-assistant", "ops_assistant", login_required(page), methods=["GET"])
    bp.add_url_rule("/ops-assistant/message", "ops_assistant_message",
                    login_required(message), methods=["POST"])
    bp.add_url_rule("/ops-assistant/confirm", "ops_assistant_confirm",
                    login_required(confirm), methods=["POST"])
    bp.add_url_rule("/ops-assistant/cancel", "ops_assistant_cancel",
                    login_required(cancel), methods=["POST"])
    bp.add_url_rule("/ops-assistant/events", "ops_assistant_events",
                    login_required(events), methods=["GET"])
    bp.add_url_rule("/ops-assistant/start-event", "ops_assistant_start_event",
                    login_required(start_event), methods=["POST"])

    @bp.app_context_processor
    def _ops_nav():
        return {"ops_assistant_nav_visible": nav_visible}


# ─────────────────────────── identity / availability ────────────────────────────

def _tid() -> int:
    try:
        return int(session.get("tenant_id") or 1)
    except (TypeError, ValueError):
        return 1


def _aid() -> int:
    from ..auth.session_helpers import current_admin_id
    try:
        return int(current_admin_id() or 0)
    except (TypeError, ValueError):
        return 0


def _username() -> str:
    return str(session.get("admin_user") or "")


def _availability() -> dict:
    from ..services.ops_assistant.gate import availability
    return availability(_tid())


_NAV_CACHE: dict[int, tuple[float, bool]] = {}
_NAV_LOCK = threading.Lock()
_NAV_TTL = 30.0


def nav_visible() -> bool:
    """Sidebar entry: only when the flag is ON and the password gate is open.
    Cheap: flag first (one setting read), gate verdicts are cached per hash;
    the result is cached per tenant for ``_NAV_TTL`` seconds."""
    try:
        if not _aid():
            return False
        tid = _tid()
        now = time.monotonic()
        with _NAV_LOCK:
            hit = _NAV_CACHE.get(tid)
        if hit and now - hit[0] < _NAV_TTL:
            return hit[1]
        from ..services.ops_assistant.gate import flag_enabled, password_gate
        ok = flag_enabled(tid) and bool(password_gate(tid).get("ready"))
        with _NAV_LOCK:
            _NAV_CACHE[tid] = (now, ok)
        return ok
    except Exception:  # noqa: BLE001 — never break the sidebar
        return False


def reset_nav_cache() -> None:
    with _NAV_LOCK:
        _NAV_CACHE.clear()


def _unavailable_json(av: dict):
    if av.get("reason") == "disabled":
        msg = _tr("مساعد العمليّات غير مفعّل لهذه الشبكة.")
    else:
        msg = _tr("المساعد متوقّف: يوجد مدير بكلمة مرور افتراضيّة أو مؤقّتة — غيّرها أوّلًا.")
    return jsonify({"ok": False, "code": "unavailable", "reason": av.get("reason"),
                    "error": msg}), 403


def _api():
    from ..services.ops_assistant.web_bridge import Api
    return Api(_aid(), _tid(), _username())


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _fail(code: str, msg: str, status: int = 422):
    return jsonify({"ok": False, "code": code, "error": msg}), status


def _conversation(cid: Any):
    from ..services.ops_assistant import store
    if not isinstance(cid, str) or not cid:
        return None
    return store.get_conversation(cid, _tid(), _aid())


# ─────────────────────────── enrichment for the cards ────────────────────────────

def _decorate(replies: list[dict]) -> list[dict]:
    from ..services.ops_assistant.conversation import decorate
    return decorate(replies, _tid())


# ─────────────────────────── views ────────────────────────────

def page():
    av = _availability()
    from ..auth.owner import is_owner_like
    from ..auth.session_helpers import current_admin
    return render_template("radius/ops_assistant.html", av=av,
                           is_owner=bool(is_owner_like(current_admin())))


def _turn(fn):
    from ..services.ops_assistant.conversation import TurnError
    try:
        return fn()
    except TurnError as e:
        return _fail(e.code, e.message or _tr("تعذّر تنفيذ الطلب."), e.status if e.status >= 400 else 422)


def message():
    av = _availability()
    if not av["available"]:
        return _unavailable_json(av)
    body = _body()
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return _fail("validation_error", _tr("اكتب رسالة أوّلًا."))
    from ..services.ops_assistant import conversation

    def run():
        api = _api()
        cid = body.get("conversation_id")
        if cid:
            if _conversation(cid) is None:
                return _fail("not_found", _tr("المحادثة غير موجودة — ابدأ محادثة جديدة."), 404)
        else:
            cid = conversation.start(api)["conversation_id"]
        replies = conversation.say(api, cid, text, actor=_username())
        return jsonify({"ok": True, "conversation_id": cid, "replies": _decorate(replies)})
    return _turn(run)


def confirm():
    av = _availability()
    if not av["available"]:
        return _unavailable_json(av)
    body = _body()
    cid = body.get("conversation_id")
    if _conversation(cid) is None:
        return _fail("not_found", _tr("المحادثة غير موجودة — ابدأ محادثة جديدة."), 404)
    pid, phash = body.get("proposal_id"), body.get("proposal_hash")
    if not isinstance(pid, str) or not isinstance(phash, str) or not pid or not phash:
        return _fail("validation_error", _tr("تأكيد ناقص."))
    from ..services.ops_assistant import conversation

    def run():
        out = conversation.confirm(_api(), cid, pid, phash)
        resp = jsonify({"ok": True, "conversation_id": cid, "report": out["report"],
                        "show_once": out.get("show_once")})
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return _turn(run)


def cancel():
    av = _availability()
    if not av["available"]:
        return _unavailable_json(av)
    cid = _body().get("conversation_id")
    if _conversation(cid) is None:
        return _fail("not_found", _tr("المحادثة غير موجودة — ابدأ محادثة جديدة."), 404)
    from ..services.ops_assistant import conversation
    conversation.cancel(_api(), cid)
    return jsonify({"ok": True, "conversation_id": cid})


def _event_text(etype: str, rec: dict) -> tuple[str, str]:
    if etype == "expiring_tomorrow":
        return (_tr("اشتراكات تنتهي غدًا"),
                _tr("%(n)s مشترك ينتهي اشتراكه يوم %(d)s.", n=rec.get("count"),
                    d=rec.get("date_local") or ""))
    if etype == "repeated_rejects":
        return (_tr("رفض دخول متكرّر"),
                _tr("%(c)s محاولة مرفوضة على «%(nas)s» خلال %(w)s دقيقة.", c=rec.get("rejects"),
                    nas=rec.get("nas") or "-", w=rec.get("window_minutes")))
    if etype == "low_card_stock":
        return (_tr("مخزون كروت منخفض"),
                _tr("الباقة «%(p)s»: %(r)s كرت غير مستخدم (الحدّ %(t)s).", p=rec.get("plan_name") or "",
                    r=rec.get("remaining"), t=rec.get("threshold")))
    if etype == "plan_without_offers":
        return (_tr("باقة بلا عرض بيع"),
                _tr("الباقة «%(p)s» لا يبيعها أيّ عرض فعّال.", p=rec.get("plan_name") or ""))
    return etype, ""


def events():
    av = _availability()
    if not av["available"]:
        return _unavailable_json(av)
    res = _api()("GET", "/ops/events")
    if not res.ok:
        err = res.error or {}
        return _fail(err.get("code") or "api_error",
                     err.get("message") or _tr("تعذّر جلب الاقتراحات."), res.status)
    from ..services.ops_assistant.conversation import event_records
    items = []
    for ev in (res.data or {}).get("items", []):
        for rec in event_records(ev):
            title, text = _event_text(ev.get("type"), rec)
            items.append({"event_type": ev.get("type"), "index": rec["index"],
                          "title": title, "text": text})
    return jsonify({"ok": True, "items": items})


def start_event():
    av = _availability()
    if not av["available"]:
        return _unavailable_json(av)
    body = _body()
    etype = body.get("event_type")
    try:
        index = max(0, int(body.get("index") or 0))
    except (TypeError, ValueError):
        index = 0
    if not isinstance(etype, str) or not etype:
        return _fail("validation_error", _tr("نوع الحدث مطلوب."))
    from ..services.ops_assistant import conversation

    def run():
        api = _api()
        cid = conversation.start(api, event_type=etype, index=index)["conversation_id"]
        replies = conversation.run_model(api, cid, actor=_username())
        return jsonify({"ok": True, "conversation_id": cid, "replies": _decorate(replies)})
    return _turn(run)


__all__ = ["register_ops_assistant_routes", "nav_visible", "reset_nav_cache"]
