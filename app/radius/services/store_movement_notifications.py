# -*- coding: utf-8 -*-
"""Subscriber/customer-facing notifications for e-card STORE money movements.

Three movements on the e-card store (the marketplace ``card_users`` buy from)
notify the buyer on the channels the operator enabled, reusing the existing
:mod:`notifications_engine` (per-event/per-channel toggles, TweetSMS adapter,
60-char SMS logic) — never a parallel stack:

  * شحن رصيد   → ``store_balance_recharge``  (wallet credited: deposit confirmed
    / admin recharge)
  * سحب رصيد   → ``store_balance_withdraw``  (wallet debited: withdrawal confirmed)
  * شراء بطاقات → ``store_cards_purchased``   (e-card bought) — AND the purchased
    card(s) login (username + password) is sent by SMS to the buyer's registered
    mobile through the tenant's connected TweetSMS, 60-char/segment aware.

The buyer is a ``card_users`` row (id, display_name, mobile, …) — NOT a RADIUS
subscriber. We hand :func:`notifications_engine.notify_event` a tiny recipient
shim carrying the buyer's ``mobile`` and ``id=0`` so the engine's SMS/WhatsApp
direct-to-phone path is used (no subscriber delivery-log mis-routing), and
Telegram is cleanly skipped (card_users have no connected chat).

Golden rule (mirrors the engine): every entry point here is fire-and-forget — a
missing mobile, an unconnected SMS account or a dead gateway can NEVER break the
money/store flow that triggered it. The cleartext card password travels ONLY in
the SMS body to the buyer's own number — never into the delivery log, WhatsApp,
Telegram, or the audit payload (a redacted audit row records only the outcome).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

_LOG = logging.getLogger(__name__)

# Event keys (registered in notifications_engine under the "store" group).
EVENT_RECHARGE = "store_balance_recharge"
EVENT_WITHDRAW = "store_balance_withdraw"
EVENT_CARDS_PURCHASED = "store_cards_purchased"
EVENT_ACCOUNT_CREATED = "store_account_created"

# How many card logins we list verbatim in one SMS before summarising (the real
# purchase path mints ONE card; the cap only guards a pathological bulk case so
# a runaway SMS can never cost dozens of segments silently).
_MAX_LISTED_CARDS = 8


# ── recipient shim ─────────────────────────────────────────────────────────
def _recipient(card_user: dict[str, Any]):
    """A minimal subscriber-shaped object for the engine, from a card_user row.

    ``id=0`` keeps the engine on its direct-to-phone send path (the buyer is not
    a subscribers-table recipient); ``telegram_chat_id=""`` makes Telegram skip
    cleanly. mobile/name drive SMS/WhatsApp + the {name} placeholder.
    """
    name = str((card_user or {}).get("display_name") or "").strip()
    return SimpleNamespace(
        id=0,
        username=name,
        full_name=name,
        name=name,
        mobile=str((card_user or {}).get("mobile") or "").strip(),
        telegram_chat_id="",
    )


def _money(amount_minor: int) -> str:
    try:
        from .business_os_finance import minor_to_money

        return str(minor_to_money(int(amount_minor or 0)))
    except Exception:  # noqa: BLE001
        return f"{(int(amount_minor or 0) / 100.0):.2f}"


def _currency(tenant_id: int) -> str:
    try:
        from ..core.system_config import default_currency

        return str(default_currency() or "")
    except Exception:  # noqa: BLE001
        return ""


# ── card-credentials SMS body (the only place a card password is rendered) ──
def build_cards_sms_body(cards: list[dict[str, Any]]) -> str:
    """Short Arabic body carrying the purchased card login(s): username + password.

    One card → friendly labelled body (stays within one ~60-char Unicode SMS).
    Many cards → compact ``user / pass`` lines, capped at :data:`_MAX_LISTED_CARDS`
    so the SMS cost can never run away (the overflow count is stated honestly).
    """
    clean = [
        {
            "username": str((c or {}).get("username") or "").strip(),
            "password": str((c or {}).get("password") or ""),
        }
        for c in (cards or [])
        if (c or {}).get("username") and (c or {}).get("password")
    ]
    if not clean:
        return ""
    if len(clean) == 1:
        c = clean[0]
        return (
            "بطاقتك جاهزة ✅\n"
            f"المستخدم: {c['username']}\n"
            f"كلمة المرور: {c['password']}"
        )
    listed = clean[:_MAX_LISTED_CARDS]
    lines = [f"بطاقاتك ({len(clean)}):"]
    lines += [f"{c['username']} / {c['password']}" for c in listed]
    if len(clean) > _MAX_LISTED_CARDS:
        lines.append(f"و{len(clean) - _MAX_LISTED_CARDS} بطاقة أخرى في حسابك.")
    return "\n".join(lines)


def _segments(body: str) -> dict[str, Any]:
    """Accurate SMS cost for ``body`` (encoding/length/segments + 60-char flag)."""
    try:
        from . import sms_segments

        seg = sms_segments.analyze(body)
        return {
            "encoding": seg.encoding,
            "length": seg.length,
            "segments": seg.segments,
            "over_recommended": seg.over_recommended,
            "recommended_max": seg.recommended_max,
        }
    except Exception:  # noqa: BLE001 — cost math must never break a send
        return {}


def _audit_cards_sms(tenant_id: int, actor: str, card_user_id: int, *, ok: bool,
                     reason: str, count: int, segments: dict[str, Any],
                     code: str, channel: str = "sms",
                     kind: str = "cards") -> None:
    """Record a REDACTED audit row — never the body or any card password.

    ``kind``: «cards» لبيانات البطاقات المشتراة، «account» لبيانات حساب
    المستفيد الجديد — يميّز صفوف التدقيق دون أي تسريب للمحتوى."""
    try:
        from .audit import get_audit_service

        get_audit_service().record(
            actor=actor or "system",
            action=f"store.{kind}_credentials_{channel}",
            target_type="card_user",
            target_id=str(card_user_id or ""),
            payload={
                "channel": channel,
                "sent": bool(ok),
                "reason": reason,
                "cards": int(count or 0),  # COUNT only — never the creds
                "segments": segments or {},
                "code": code,
            },
            result_status="sent" if ok else "failed",
        )
    except Exception:  # noqa: BLE001 — audit must never break the flow
        _LOG.debug("[store_movement] cards-creds audit failed", exc_info=True)


def send_cards_credentials_sms(tenant_id: int, recipient, cards: list[dict[str, Any]],
                               *, actor: str = "", card_user_id: int = 0) -> dict[str, Any]:
    """Send the purchased card login(s) by SMS via TweetSMS. NEVER raises.

    Mirrors :mod:`subscriber_credentials`: the body goes ONLY through the
    TweetSMS adapter (which never logs the body) and only a redacted audit row
    is kept. Returns ``{ok, error_ar, reason, segments}``.
    """
    tid = int(tenant_id or 1)
    mobile = str(getattr(recipient, "mobile", "") or "").strip()
    if not mobile:
        return {"ok": False, "error_ar": "لا يوجد رقم جوال للمشتري", "reason": "no_mobile", "segments": {}}

    body = build_cards_sms_body(cards)
    if not body:
        return {"ok": False, "error_ar": "لا توجد بيانات بطاقات للإرسال", "reason": "no_cards", "segments": {}}

    from . import tweetsms

    if not tweetsms.is_connected(tid):
        return {"ok": False, "error_ar": "اربط حساب SMS أولاً", "reason": "not_connected", "segments": {}}

    segments = _segments(body)
    try:
        outcome = tweetsms.send_sms(tid, mobile, body)
    except Exception as exc:  # noqa: BLE001 — adapter is defensive, but be safe
        _audit_cards_sms(tid, actor, card_user_id, ok=False, reason="send_error",
                         count=len(cards or []), segments=segments, code="")
        return {"ok": False, "error_ar": f"تعذّر الإرسال: {exc}", "reason": "send_error", "segments": segments}

    ok = bool(outcome.get("ok"))
    first = (outcome.get("results") or [{}])[0]
    code = str(first.get("code") or "")
    segments = outcome.get("segments") or segments
    error_ar = "" if ok else (outcome.get("error_ar") or first.get("message_ar")
                              or "فشل الإرسال عبر TweetSMS.")
    _audit_cards_sms(tid, actor, card_user_id, ok=ok,
                     reason=("sent" if ok else "send_failed"),
                     count=len(cards or []), segments=segments, code=code)
    return {"ok": ok, "error_ar": error_ar, "reason": ("sent" if ok else "send_failed"),
            "segments": segments}


def build_account_sms_body(username: str, password: str) -> str:
    """نصّ رسالة بيانات حساب المستفيد الجديد — اسم المستخدم (رقم الجوال)
    وكلمة المرور. المكان الوحيد الذي تُصاغ فيه كلمة المرور نصًّا."""
    u = str(username or "").strip()
    p = str(password or "")
    if not u or not p:
        return ""
    return (
        "تم إنشاء حسابك ✅\n"
        f"اسم المستخدم: {u}\n"
        f"كلمة المرور: {p}"
    )


def send_account_credentials_sms(tenant_id: int, recipient, account: dict[str, Any],
                                 *, actor: str = "", card_user_id: int = 0) -> dict[str, Any]:
    """إرسال بيانات حساب المستفيد الجديد عبر SMS (TweetSMS مباشرة). NEVER raises.

    مرآة :func:`send_cards_credentials_sms` بجسم «تم إنشاء حسابك» — الجسم لا
    يُسجَّل أبدًا؛ صفّ تدقيق منقّح فقط (store.account_credentials_sms)."""
    tid = int(tenant_id or 1)
    mobile = str(getattr(recipient, "mobile", "") or "").strip()
    if not mobile:
        return {"ok": False, "error_ar": "لا يوجد رقم جوال للمستفيد", "reason": "no_mobile", "segments": {}}

    body = build_account_sms_body((account or {}).get("username"), (account or {}).get("password"))
    if not body:
        return {"ok": False, "error_ar": "لا توجد بيانات حساب للإرسال", "reason": "no_account", "segments": {}}

    from . import tweetsms

    if not tweetsms.is_connected(tid):
        return {"ok": False, "error_ar": "اربط حساب SMS أولاً", "reason": "not_connected", "segments": {}}

    segments = _segments(body)
    try:
        outcome = tweetsms.send_sms(tid, mobile, body)
    except Exception as exc:  # noqa: BLE001 — adapter is defensive, but be safe
        _audit_cards_sms(tid, actor, card_user_id, ok=False, reason="send_error",
                         count=1, segments=segments, code="", kind="account")
        return {"ok": False, "error_ar": f"تعذّر الإرسال: {exc}", "reason": "send_error", "segments": segments}

    ok = bool(outcome.get("ok"))
    first = (outcome.get("results") or [{}])[0]
    code = str(first.get("code") or "")
    segments = outcome.get("segments") or segments
    error_ar = "" if ok else (outcome.get("error_ar") or first.get("message_ar")
                              or "فشل الإرسال عبر TweetSMS.")
    _audit_cards_sms(tid, actor, card_user_id, ok=ok,
                     reason=("sent" if ok else "send_failed"),
                     count=1, segments=segments, code=code, kind="account")
    return {"ok": ok, "error_ar": error_ar, "reason": ("sent" if ok else "send_failed"),
            "segments": segments}


def send_account_credentials_whatsapp(tenant_id: int, recipient, account: dict[str, Any],
                                      *, actor: str = "", card_user_id: int = 0) -> dict[str, Any]:
    """إرسال بيانات حساب المستفيد الجديد عبر واتساب (direct_send). NEVER raises."""
    tid = int(tenant_id or 1)
    mobile = str(getattr(recipient, "mobile", "") or "").strip()
    if not mobile:
        return {"ok": False, "error_ar": "لا يوجد رقم جوال للمستفيد", "reason": "no_mobile"}

    body = build_account_sms_body((account or {}).get("username"), (account or {}).get("password"))
    if not body:
        return {"ok": False, "error_ar": "لا توجد بيانات حساب للإرسال", "reason": "no_account"}

    from . import comms_providers

    if not comms_providers.is_channel_active(
        comms_providers.load_channel_config(tid, "whatsapp")
    ):
        return {"ok": False, "error_ar": "اضبط قناة واتساب أولاً", "reason": "not_connected"}

    try:
        ok, err = comms_providers.direct_send(tid, "whatsapp", mobile, body)
    except Exception as exc:  # noqa: BLE001 — provider is defensive, but be safe
        _audit_cards_sms(tid, actor, card_user_id, ok=False, reason="send_error",
                         count=1, segments={}, code="", channel="whatsapp", kind="account")
        return {"ok": False, "error_ar": f"تعذّر الإرسال: {exc}", "reason": "send_error"}

    error_ar = "" if ok else (err or "فشل الإرسال عبر واتساب.")
    _audit_cards_sms(tid, actor, card_user_id, ok=ok,
                     reason=("sent" if ok else "send_failed"),
                     count=1, segments={}, code="", channel="whatsapp", kind="account")
    return {"ok": ok, "error_ar": error_ar, "reason": ("sent" if ok else "send_failed")}


def send_cards_credentials_whatsapp(tenant_id: int, recipient, cards: list[dict[str, Any]],
                                    *, actor: str = "", card_user_id: int = 0) -> dict[str, Any]:
    """Send the purchased card login(s) by WhatsApp. NEVER raises.

    The WhatsApp sibling of :func:`send_cards_credentials_sms`: the same
    card-login body (username + password) rides the tenant's configured WhatsApp
    channel via :func:`comms_providers.direct_send` — a DIRECT, unlogged send so
    the cleartext card password never lands in the delivery log. Only a redacted,
    count-only audit row (channel=whatsapp) is kept. Returns
    ``{ok, error_ar, reason}``.
    """
    tid = int(tenant_id or 1)
    mobile = str(getattr(recipient, "mobile", "") or "").strip()
    if not mobile:
        return {"ok": False, "error_ar": "لا يوجد رقم جوال للمشتري", "reason": "no_mobile"}

    body = build_cards_sms_body(cards)
    if not body:
        return {"ok": False, "error_ar": "لا توجد بيانات بطاقات للإرسال", "reason": "no_cards"}

    from . import comms_providers

    if not comms_providers.is_channel_active(
        comms_providers.load_channel_config(tid, "whatsapp")
    ):
        return {"ok": False, "error_ar": "اضبط قناة واتساب أولاً", "reason": "not_connected"}

    try:
        ok, err = comms_providers.direct_send(tid, "whatsapp", mobile, body)
    except Exception as exc:  # noqa: BLE001 — provider is defensive, but be safe
        _audit_cards_sms(tid, actor, card_user_id, ok=False, reason="send_error",
                         count=len(cards or []), segments={}, code="", channel="whatsapp")
        return {"ok": False, "error_ar": f"تعذّر الإرسال: {exc}", "reason": "send_error"}

    error_ar = "" if ok else (err or "فشل الإرسال عبر واتساب.")
    _audit_cards_sms(tid, actor, card_user_id, ok=ok,
                     reason=("sent" if ok else "send_failed"),
                     count=len(cards or []), segments={}, code="", channel="whatsapp")
    return {"ok": ok, "error_ar": error_ar, "reason": ("sent" if ok else "send_failed")}


# ── fire-and-forget movement notifications (called at the action sites) ─────
def _fire(event_key: str, tenant_id: int, card_user: dict[str, Any],
          context: dict[str, Any]):
    """Fire one store movement event. NEVER raises into the money flow."""
    try:
        from . import notifications_engine as ne

        return ne.notify_event(
            event_key, tenant_id=int(tenant_id or 1),
            subscriber=_recipient(card_user), context=context,
        )
    except Exception:  # noqa: BLE001 — a notification can never break the sale
        _LOG.debug("[store_movement] notify %s failed", event_key, exc_info=True)
        return None


def notify_recharge(tenant_id: int, card_user: dict[str, Any], *,
                    amount_minor: int, balance_minor: int):
    """شحن رصيد — the buyer's store wallet was credited."""
    return _fire(EVENT_RECHARGE, tenant_id, card_user, {
        "amount": f"{_money(amount_minor)} {_currency(tenant_id)}".strip(),
        "balance": f"{_money(balance_minor)} {_currency(tenant_id)}".strip(),
    })


def notify_withdraw(tenant_id: int, card_user: dict[str, Any], *,
                    amount_minor: int, balance_minor: int):
    """سحب رصيد — the buyer's store wallet was debited."""
    return _fire(EVENT_WITHDRAW, tenant_id, card_user, {
        "amount": f"{_money(amount_minor)} {_currency(tenant_id)}".strip(),
        "balance": f"{_money(balance_minor)} {_currency(tenant_id)}".strip(),
    })


def notify_cards_purchased(tenant_id: int, card_user: dict[str, Any], *,
                           cards: list[dict[str, Any]], amount_minor: int,
                           package_name: str = ""):
    """شراء بطاقات — fire the purchase event. The SMS channel carries the
    purchased card login(s); WhatsApp/Telegram get only the password-free body.

    The cards (with passwords) ride in ``context['cards']`` for the SMS branch;
    the engine never renders/logs them (the template has no {cards} placeholder
    and non-scalar context values are skipped before rendering)."""
    valid = [c for c in (cards or []) if (c or {}).get("username") and (c or {}).get("password")]
    return _fire(EVENT_CARDS_PURCHASED, tenant_id, card_user, {
        "count": str(len(valid)),
        "amount": f"{_money(amount_minor)} {_currency(tenant_id)}".strip(),
        "package": str(package_name or ""),
        "cards": valid,          # SMS-only; skipped by the template renderer
        "card_user_id": int((card_user or {}).get("id") or 0),
    })


def notify_account_created(tenant_id: int, card_user: dict[str, Any], *,
                           password: str):
    """إنشاء حساب مستفيد (من الإدارة أو تسجيل ذاتي) — رسالة بيانات الدخول:
    اسم المستخدم (رقم الجوال) + كلمة المرور، عبر SMS/واتساب حسب القنوات.

    كلمة المرور تركب في ``context['account']`` لفرعي الإرسال المباشر فقط —
    المحرك لا يصيّرها في القالب ولا يسجّلها (القيم غير العددية تُتخطّى)."""
    mobile = str((card_user or {}).get("mobile") or "").strip()
    return _fire(EVENT_ACCOUNT_CREATED, tenant_id, card_user, {
        "name": str((card_user or {}).get("display_name") or ""),
        "username": mobile,
        "account": {"username": mobile, "password": str(password or "")},
        "card_user_id": int((card_user or {}).get("id") or 0),
    })


__all__ = [
    "EVENT_RECHARGE",
    "EVENT_WITHDRAW",
    "EVENT_ACCOUNT_CREATED",
    "EVENT_CARDS_PURCHASED",
    "build_cards_sms_body",
    "build_account_sms_body",
    "send_cards_credentials_sms",
    "send_cards_credentials_whatsapp",
    "send_account_credentials_sms",
    "send_account_credentials_whatsapp",
    "notify_recharge",
    "notify_withdraw",
    "notify_cards_purchased",
    "notify_account_created",
]
