"""Send a subscriber their own login (username + password) by SMS.

Two callers reuse this one place:

  1. The «subscriber_created» notification — when the operator enabled the SMS
     channel for it, the SMS to the new subscriber carries their username AND
     password (the system stores the cleartext password for RADIUS PAP, so it's
     available). WhatsApp/Telegram keep the password-free template.
  2. The «إرسال بيانات المشترك» button on the subscribers page — an on-demand
     resend of the same short credentials SMS.

Why a dedicated tiny service (and why it does NOT go through the campaign
delivery log):
  * The cleartext password is SENSITIVE. It must travel ONLY inside the SMS
    body to the subscriber's own number — never into the ``message_deliveries``
    body, Telegram, push, or the audit payload. So we send through the TweetSMS
    adapter directly (:func:`tweetsms.send_sms`, which never logs the body) and
    record only a REDACTED audit row (sent? + segment cost + result code).
  * The body is deliberately SHORT (mostly-ASCII creds) so a typical user/pass
    stays within ~one 60-char SMS segment; :mod:`sms_segments` reports the real
    cost so the caller can surface a segment warning when it overflows.

Like the rest of the notification pipeline, every entry point is defensive — it
NEVER raises into the caller; a missing mobile / unconnected account / dead
gateway comes back as a result dict with an Arabic message.
"""
from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)

# The short credentials body. Concise Arabic labels keep a typical user/pass
# within one ~60-char Unicode SMS segment. ONLY this body ever carries the
# password — and ONLY over SMS/WhatsApp, to the subscriber's own number.
CREDENTIALS_SMS_TEMPLATE = "المستخدم: {username} كلمة المرور: {password}"

# WhatsApp isn't billed per 60-char segment, so it can carry a friendlier,
# multi-line body. Still the ONLY WhatsApp body that ever carries the password,
# and it goes DIRECT to the subscriber's own number (never into a delivery log).
CREDENTIALS_WA_TEMPLATE = (
    "مرحبًا 👋\n"
    "تم إنشاء حسابك بنجاح.\n"
    "اسم المستخدم: {username}\n"
    "كلمة المرور: {password}"
)

# Clear, reusable Arabic errors (exact strings the subscribers page expects).
ERR_NO_MOBILE = "لا يوجد رقم جوال للمشترك"
ERR_NOT_CONNECTED = "اربط حساب SMS أولاً"
ERR_WA_NOT_CONNECTED = "اضبط قناة واتساب أولاً"


def build_body(username: str, password: str) -> str:
    """Render the short credentials SMS body (username + password)."""
    return CREDENTIALS_SMS_TEMPLATE.format(
        username=str(username or "").strip(),
        password=str(password or ""),
    )


def build_whatsapp_body(username: str, password: str) -> str:
    """Render the (friendlier) credentials WhatsApp body (username + password)."""
    return CREDENTIALS_WA_TEMPLATE.format(
        username=str(username or "").strip(),
        password=str(password or ""),
    )


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
            "summary_ar": sms_segments.summary_ar(body),
        }
    except Exception:  # noqa: BLE001 — cost math must never break a send
        return {}


def _result(ok: bool, *, error_ar: str = "", reason: str = "",
            sent_count: int = 0, segments: dict[str, Any] | None = None,
            code: str = "") -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "error_ar": "" if ok else str(error_ar or ""),
        "reason": str(reason or ("sent" if ok else "failed")),
        "sent_count": int(sent_count or 0),
        "segments": segments or {},
        "code": str(code or ""),
    }


def _audit(tenant_id: int, actor: str, username: str, *, ok: bool,
           reason: str, segments: dict[str, Any], code: str,
           channel: str = "sms") -> None:
    """Record a REDACTED audit row — never the body or the password."""
    try:
        from .audit import get_audit_service

        get_audit_service().record(
            actor=actor or "system",
            action=f"subscriber.credentials_{channel}",
            target_type="user",
            target_id=str(username or ""),
            # No body / no password — only the outcome + the SMS cost.
            payload={
                "channel": channel,
                "sent": bool(ok),
                "reason": reason,
                "segments": segments or {},
                "code": code,
            },
            result_status="sent" if ok else "failed",
        )
    except Exception:  # noqa: BLE001 — audit must never break the flow
        _LOG.debug("[subscriber_credentials] audit record failed", exc_info=True)


def send(tenant_id: int, subscriber, *, actor: str = "") -> dict[str, Any]:
    """Send the subscriber's username+password by SMS. NEVER raises.

    Returns ``{ok, error_ar, reason, sent_count, segments, code}``:
      * ``reason="no_mobile"``       → subscriber has no mobile on file.
      * ``reason="not_connected"``   → tenant hasn't connected TweetSMS yet.
      * ``reason="sent"``/``"send_failed"`` otherwise (TweetSMS outcome).
    """
    tid = int(tenant_id or 1)
    username = str(getattr(subscriber, "username", "") or "").strip()
    password = str(getattr(subscriber, "password", "") or "")
    mobile = str(getattr(subscriber, "mobile", "") or "").strip()

    if not mobile:
        return _result(False, error_ar=ERR_NO_MOBILE, reason="no_mobile")

    from . import tweetsms

    if not tweetsms.is_connected(tid):
        return _result(False, error_ar=ERR_NOT_CONNECTED, reason="not_connected")

    body = build_body(username, password)
    segments = _segments(body)

    try:
        outcome = tweetsms.send_sms(tid, mobile, body)
    except Exception as exc:  # noqa: BLE001 — adapter is defensive, but be safe
        _audit(tid, actor, username, ok=False, reason="send_error",
               segments=segments, code="")
        return _result(False, error_ar=f"تعذّر الإرسال: {exc}", reason="send_error",
                       segments=segments)

    ok = bool(outcome.get("ok"))
    first = (outcome.get("results") or [{}])[0]
    code = str(first.get("code") or "")
    # Prefer the adapter's accurate per-send segment info when present.
    segments = outcome.get("segments") or segments
    error_ar = "" if ok else (outcome.get("error_ar") or first.get("message_ar")
                              or "فشل الإرسال عبر TweetSMS.")
    _audit(tid, actor, username, ok=ok, reason=("sent" if ok else "send_failed"),
           segments=segments, code=code)
    return _result(ok, error_ar=error_ar, reason=("sent" if ok else "send_failed"),
                   sent_count=int(outcome.get("sent_count") or 0),
                   segments=segments, code=code)


def send_whatsapp(tenant_id: int, subscriber, *, actor: str = "") -> dict[str, Any]:
    """Send the subscriber's username+password by WhatsApp. NEVER raises.

    Mirrors :func:`send` but rides the tenant's configured WhatsApp channel via
    :func:`comms_providers.direct_send` — a DIRECT, unlogged send so the
    cleartext password never lands in the delivery log. Only a redacted audit
    row (sent? + code, channel=whatsapp) is kept.

    Returns ``{ok, error_ar, reason, sent_count, segments, code}``:
      * ``reason="no_mobile"``       → subscriber has no mobile on file.
      * ``reason="not_connected"``   → tenant hasn't configured WhatsApp yet.
      * ``reason="sent"``/``"send_failed"`` otherwise (gateway outcome).
    """
    tid = int(tenant_id or 1)
    username = str(getattr(subscriber, "username", "") or "").strip()
    password = str(getattr(subscriber, "password", "") or "")
    mobile = str(getattr(subscriber, "mobile", "") or "").strip()

    if not mobile:
        return _result(False, error_ar=ERR_NO_MOBILE, reason="no_mobile")

    from . import comms_providers

    if not comms_providers.is_channel_active(
        comms_providers.load_channel_config(tid, "whatsapp")
    ):
        return _result(False, error_ar=ERR_WA_NOT_CONNECTED, reason="not_connected")

    body = build_whatsapp_body(username, password)
    try:
        ok, err = comms_providers.direct_send(tid, "whatsapp", mobile, body)
    except Exception as exc:  # noqa: BLE001 — provider is defensive, but be safe
        _audit(tid, actor, username, ok=False, reason="send_error",
               segments={}, code="", channel="whatsapp")
        return _result(False, error_ar=f"تعذّر الإرسال: {exc}", reason="send_error")

    error_ar = "" if ok else (err or "فشل الإرسال عبر واتساب.")
    _audit(tid, actor, username, ok=ok, reason=("sent" if ok else "send_failed"),
           segments={}, code="", channel="whatsapp")
    return _result(ok, error_ar=error_ar, reason=("sent" if ok else "send_failed"),
                   sent_count=(1 if ok else 0))
