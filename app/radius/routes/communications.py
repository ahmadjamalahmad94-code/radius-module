"""Notification and campaign web routes."""
from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from ..services import comms_bot, comms_providers
from ..services.notification_campaigns import NotificationCampaignError, NotificationCampaignService


def register_communications_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/communications", "communications", communications_dashboard, methods=["GET"])
    bp.add_url_rule("/communications/templates", "communications_templates", communications_templates, methods=["GET", "POST"])
    bp.add_url_rule("/communications/send", "communications_send", communications_send, methods=["GET", "POST"])
    bp.add_url_rule("/communications/campaigns", "communications_campaigns", communications_campaigns, methods=["GET", "POST"])
    bp.add_url_rule("/communications/deliveries", "communications_deliveries", communications_deliveries, methods=["GET"])
    bp.add_url_rule("/communications/audience", "communications_audience", communications_audience, methods=["GET", "POST"])
    bp.add_url_rule("/communications/channels", "communications_channels", communications_channels, methods=["GET", "POST"])
    bp.add_url_rule("/communications/channels/test", "communications_channels_test", communications_channels_test, methods=["POST"])
    # WhatsApp bot (Phase 2): a simple settings page + a public inbound webhook.
    bp.add_url_rule("/communications/bot", "communications_bot", communications_bot_settings, methods=["GET", "POST"])
    bp.add_url_rule("/communications/bot/webhook", "communications_bot_webhook", communications_bot_webhook, methods=["POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "غير معروف"


def _admin_id() -> int:
    try:
        return int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        return 0


def _svc() -> NotificationCampaignService:
    return NotificationCampaignService(tenant_id=_tid())


def communications_dashboard():
    svc = _svc()
    return render_template(
        "radius/communications.html",
        summary=svc.dashboard(),
        templates=svc.list_templates(),
        segments=svc.list_segments(),
        deliveries=svc.delivery_log(limit=10),
        active="dashboard",
    )


def communications_templates():
    svc = _svc()
    if request.method == "POST":
        try:
            svc.create_template(
                template_key=request.form.get("template_key") or "",
                title=request.form.get("title") or "",
                channel=request.form.get("channel") or "internal",
                subject=request.form.get("subject") or "",
                body=request.form.get("body") or "",
                variables=[
                    part.strip()
                    for part in (request.form.get("variables") or "").split(",")
                    if part.strip()
                ],
                actor=_actor(),
            )
            flash("تم حفظ قالب الرسالة.", "success")
        except NotificationCampaignError as exc:
            flash(str(exc), "error")
        return redirect(url_for("radius.communications_templates"))
    return render_template("radius/communications_templates.html", templates=svc.list_templates(), active="templates")


def communications_send():
    svc = _svc()
    preview = []
    if request.method == "POST":
        try:
            audience = _audience_from_form()
            preview = svc.preview_audience(audience)
            if request.form.get("send_now") == "1":
                result = svc.send_manual(
                    audience=audience,
                    channel=request.form.get("channel") or "internal",
                    subject=request.form.get("subject") or "",
                    message=request.form.get("message") or "",
                    actor=_actor(),
                )
                flash(f"تمت إضافة {result['queued_count']} رسالة إلى قائمة الإرسال.", "success")
                return redirect(url_for("radius.communications_deliveries"))
        except NotificationCampaignError as exc:
            flash(str(exc), "error")
    return render_template(
        "radius/communications_send.html",
        preview=preview,
        templates=svc.list_templates(),
        active="send",
    )


def communications_campaigns():
    svc = _svc()
    campaign = None
    if request.method == "POST":
        try:
            actions = []
            for action_type in request.form.getlist("actions"):
                actions.append({"type": action_type})
            campaign = svc.campaign_dry_run(
                campaign_key=request.form.get("campaign_key") or "",
                title=request.form.get("title") or "",
                template_id=int(request.form.get("template_id") or 0),
                audience=_audience_from_form(),
                actions=actions,
                actor=_actor(),
            )
            flash("تم تجهيز تجربة جافة للحملة. لم يتم إرسال أي رسالة خارجية.", "success")
        except (NotificationCampaignError, ValueError) as exc:
            flash(str(exc), "error")
    return render_template(
        "radius/communications_campaigns.html",
        templates=svc.list_templates(),
        campaign=campaign,
        active="campaigns",
    )


def communications_deliveries():
    return render_template(
        "radius/communications_deliveries.html",
        deliveries=_svc().delivery_log(limit=200),
        active="deliveries",
    )


def communications_audience():
    svc = _svc()
    preview = []
    if request.method == "POST":
        try:
            filters = _audience_from_form()
            svc.create_segment(
                segment_key=request.form.get("segment_key") or "",
                title=request.form.get("title") or "",
                filters=filters,
                actor=_actor(),
            )
            preview = svc.preview_audience(filters)
            flash("تم حفظ شريحة الجمهور.", "success")
        except NotificationCampaignError as exc:
            flash(str(exc), "error")
    return render_template(
        "radius/communications_audience.html",
        segments=svc.list_segments(),
        preview=preview,
        active="audience",
    )


def communications_channels():
    """Per-tenant send-channel settings for SMS and WhatsApp (generic HTTP)."""
    tid = _tid()
    if request.method == "POST":
        channel = (request.form.get("channel") or "").strip().lower()
        if channel not in comms_providers.HTTP_CHANNELS:
            flash("قناة غير مدعومة.", "error")
            return redirect(url_for("radius.communications_channels"))
        try:
            comms_providers.save_channel_config(
                tid,
                channel,
                {
                    "enabled": request.form.get("enabled") or "0",
                    "mode": request.form.get("mode") or comms_providers.DEFAULT_MODE,
                    "send_url_template": request.form.get("send_url_template") or "",
                    "http_method": request.form.get("http_method") or comms_providers.DEFAULT_METHOD,
                    "balance_url": request.form.get("balance_url") or "",
                },
                by=_admin_id(),
            )
            flash("تم حفظ إعدادات القناة.", "success")
        except Exception:  # noqa: BLE001 — settings must never 500 the page
            flash("تعذّر حفظ الإعدادات. راجع البيانات وحاول مرة أخرى.", "error")
        return redirect(url_for("radius.communications_channels"))

    channels = {ch: comms_providers.channel_status(tid, ch) for ch in comms_providers.HTTP_CHANNELS}
    return render_template(
        "radius/communications_channels.html",
        channels=channels,
        modes=comms_providers.CHANNEL_MODES,
        active="channels",
    )


def communications_channels_test():
    """Send a single test message to an admin-entered phone via the provider.

    Returns JSON so the page can show the gateway result inline. Honours the
    saved per-channel config; if the channel is disabled/unconfigured the
    provider reports it without hitting any URL.
    """
    tid = _tid()
    channel = (request.form.get("channel") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip()
    message = (request.form.get("message") or "").strip() or "رسالة اختبار من مركز التواصل."

    if channel not in comms_providers.HTTP_CHANNELS:
        return jsonify({"ok": False, "status": "error", "message": "قناة غير مدعومة."}), 400
    if not phone:
        return jsonify({"ok": False, "status": "error", "message": "أدخل رقم هاتف للاختبار."}), 400

    config = comms_providers.load_channel_config(tid, channel)
    if not config.get("enabled"):
        return jsonify({"ok": False, "status": "skipped", "message": "القناة متوقفة. فعّلها أولًا ثم احفظ الإعدادات."})
    if "{phone}" not in (config.get("send_url_template") or ""):
        return jsonify({"ok": False, "status": "skipped", "message": "اضبط رابط إرسال يحتوي على {phone} أولًا."})

    outcome = comms_providers.http_send(
        template=config["send_url_template"],
        method=config["http_method"],
        phone=phone,
        message=message,
    )
    return jsonify({
        "ok": bool(outcome.ok),
        "status": "sent" if outcome.ok else "failed",
        "http_status": outcome.status_code,
        "message": (
            "تم إرسال رسالة الاختبار بنجاح."
            if outcome.ok
            else (outcome.error or "فشل إرسال رسالة الاختبار.")
        ),
        "response_excerpt": outcome.body_excerpt,
    })


def communications_bot_settings():
    """Simple WhatsApp-bot settings page (enable + greeting/fallback + commands).

    GET renders the page; POST saves the config and redirects back. Everything
    is stored in ``tenant_settings`` (``comms.bot.*``) — no migration.
    """
    tid = _tid()
    if request.method == "POST":
        try:
            comms_bot.save_bot_config(
                tid,
                {
                    "enabled": request.form.get("enabled") or "0",
                    "greeting": request.form.get("greeting") or "",
                    "fallback": request.form.get("fallback") or "",
                    "commands": _bot_commands_from_form(),
                },
                by=_admin_id(),
            )
            flash("تم حفظ إعدادات بوت واتساب.", "success")
        except Exception:  # noqa: BLE001 — settings must never 500 the page
            flash("تعذّر حفظ الإعدادات. راجع البيانات وحاول مرة أخرى.", "error")
        return redirect(url_for("radius.communications_bot"))

    config = comms_bot.load_bot_config(tid)
    whatsapp = comms_providers.channel_status(tid, comms_bot.BOT_CHANNEL)
    return render_template(
        "radius/communications_bot.html",
        config=config,
        webhook_url=_bot_webhook_url(),
        whatsapp=whatsapp,
        active="bot",
    )


def communications_bot_webhook():
    """Public inbound webhook for the WhatsApp bot (CSRF-exempt).

    Reads the sender phone + message from common JSON/form shapes, hands them
    to the bot dispatcher (match → render → send), and ALWAYS answers 200 fast.
    Never raises: a disabled bot or a malformed payload is a quiet no-op.
    """
    try:
        json_body = request.get_json(silent=True)
    except Exception:  # noqa: BLE001
        json_body = None
    try:
        phone, text = comms_bot.parse_inbound(
            json_body=json_body,
            form=request.form,
            args=request.args,
        )
        result = comms_bot.handle_inbound(_tid(), phone=phone, text=text)
        return jsonify({"ok": True, "handled": result.handled, "reason": result.reason}), 200
    except Exception:  # noqa: BLE001 — the webhook must never 500
        return jsonify({"ok": True, "handled": False, "reason": "error"}), 200


def _bot_webhook_url() -> str:
    """Absolute URL of the inbound webhook, built from the current request host."""
    try:
        return url_for("radius.communications_bot_webhook", _external=True)
    except Exception:  # noqa: BLE001
        # Fallback: relative path is still useful even if _external fails.
        return url_for("radius.communications_bot_webhook")


def _bot_commands_from_form() -> list[dict]:
    """Read the editable command rows posted by the settings page.

    The form posts parallel arrays ``cmd_keyword[]`` / ``cmd_reply[]`` /
    ``cmd_enabled[]`` (enabled is a hidden 0/1 per row). Empty rows are dropped.
    """
    keywords = request.form.getlist("cmd_keyword")
    replies = request.form.getlist("cmd_reply")
    enabled = request.form.getlist("cmd_enabled")
    rows: list[dict] = []
    for idx, keyword in enumerate(keywords):
        kw = (keyword or "").strip()
        reply = (replies[idx] if idx < len(replies) else "").strip()
        if not kw and not reply:
            continue
        is_on = (enabled[idx] if idx < len(enabled) else "1")
        rows.append({"keyword": kw, "reply_template": reply, "enabled": is_on})
    return rows


def _audience_from_form() -> dict:
    ids_raw = request.form.get("ids") or ""
    return {
        "target": request.form.get("target") or "subscriber",
        "manager_id": request.form.get("manager_id") or "",
        "ids": [int(part.strip()) for part in ids_raw.split(",") if part.strip().isdigit()],
        "limit": int(request.form.get("limit") or 100),
    }
