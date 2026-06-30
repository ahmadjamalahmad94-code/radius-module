"""Per-customer SMS connection page (TweetSMS — bring-your-own-provider).

SMS is a FREE BYO service: every customer connects their OWN TweetSMS account
(api_key OR username+password + an approved sender name) and buys credit from
TweetSMS directly. There is no admin-sold message bundle / quota.

This page mirrors the WhatsApp connect page's style/UX, but — unlike WhatsApp
which is brokered through the licensing panel — the SMS credentials live here,
stored ENCRYPTED at rest (Fernet, ``enc:`` prefix) in ``tenant_sms_settings``
and MASKED in the UI. The real sends/balance checks go through the clean
``services/tweetsms.py`` adapter. Secrets are never logged.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for


def register_sms_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/sms", "sms", sms_page, methods=["GET"])
    bp.add_url_rule("/sms/save", "sms_save", sms_save, methods=["POST"])
    bp.add_url_rule("/sms/balance", "sms_balance", sms_balance, methods=["POST"])
    bp.add_url_rule("/sms/test", "sms_test", sms_test, methods=["POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def sms_page():
    """GET — the SMS connection page: connection status, credential form (masked),
    a balance check and a test-message form."""
    from ..services import tweetsms

    tid = _tid()
    return render_template(
        "radius/sms.html",
        status=tweetsms.connection_status(tid),
    )


def sms_save():
    """POST — persist the tenant's TweetSMS credentials.

    The auth mode (``api_key`` or ``user_pass``) decides which credential is
    kept; the non-selected one is cleared. A blank secret field means «keep the
    existing value» (the field is masked, so the customer needn't re-type it).
    """
    from ..db.repos import tenant_sms_settings_repo

    tid = _tid()
    existing = tenant_sms_settings_repo.get(tid) or {}

    auth_mode = (request.form.get("auth_mode") or "api_key").strip().lower()
    sender = (request.form.get("sender") or "").strip()
    enabled = _truthy(request.form.get("enabled"))

    form_api_key = (request.form.get("api_key") or "").strip()
    form_username = (request.form.get("username") or "").strip()
    form_password = (request.form.get("password") or "").strip()

    if auth_mode == "user_pass":
        api_key = ""
        username = form_username
        # blank password → keep existing (masked field wasn't re-typed)
        password = form_password or (existing.get("password") or "")
    else:
        auth_mode = "api_key"
        # blank api_key → keep existing
        api_key = form_api_key or (existing.get("api_key") or "")
        username = ""
        password = ""

    try:
        tenant_sms_settings_repo.upsert(
            tenant_id=tid,
            provider="tweetsms",
            api_key=api_key,
            username=username,
            password=password,
            sender=sender,
            enabled=enabled,
        )
        flash("تم حفظ إعدادات ربط SMS (TweetSMS).", "success")
    except Exception:  # noqa: BLE001 — settings must never 500 the page
        flash("تعذّر حفظ الإعدادات. حاول مرة أخرى.", "error")
    return redirect(url_for("radius.sms"))


def sms_balance():
    """POST — check the connected TweetSMS account balance and flash it."""
    from ..services import tweetsms

    result = tweetsms.check_balance(_tid())
    if result.get("ok"):
        balance = result.get("balance")
        flash(f"رصيد TweetSMS الحالي: {balance}.", "success")
    else:
        flash(f"تعذّر جلب الرصيد: {result.get('error_ar') or 'خطأ غير معروف.'}", "error")
    return redirect(url_for("radius.sms"))


def sms_test():
    """POST — send one test SMS to an admin-entered number, flash the parsed
    result (or the Arabic error). The recipient number is NOT logged."""
    from ..services import tweetsms

    phone = (request.form.get("recipient_phone") or "").strip()
    if not phone:
        flash("أدخل رقم هاتف لإرسال رسالة الاختبار.", "error")
        return redirect(url_for("radius.sms"))
    message = (request.form.get("message") or "").strip() or "رسالة اختبار من نظام HobeRadius عبر TweetSMS."

    from ..services import sms_segments
    cost = sms_segments.summary_ar(message)

    result = tweetsms.send_sms(_tid(), phone, message)
    if result.get("ok"):
        first = (result.get("results") or [{}])[0]
        sms_id = first.get("sms_id") or ""
        suffix = f" مُعرّف الرسالة: {sms_id}." if sms_id else ""
        seg = result.get("segments") or {}
        if seg.get("segments", 1) > 1:
            flash(f"تم إرسال رسالة الاختبار ({cost}) — حُسبت {seg.get('segments')} رسائل SMS. اختصر النص لتوفير التكلفة.{suffix}", "warning")
        else:
            flash(f"تم إرسال رسالة الاختبار بنجاح ({cost}).{suffix}", "success")
    else:
        reason = result.get("error_ar") or ""
        if not reason:
            first = (result.get("results") or [{}])[0]
            reason = first.get("message_ar") or "فشل الإرسال."
        flash(f"تعذّر إرسال رسالة الاختبار: {reason}", "error")
    return redirect(url_for("radius.sms"))
