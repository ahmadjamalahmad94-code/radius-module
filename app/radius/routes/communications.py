"""Notification and campaign web routes."""
from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from ..services import comms_providers
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


def _audience_from_form() -> dict:
    ids_raw = request.form.get("ids") or ""
    return {
        "target": request.form.get("target") or "subscriber",
        "manager_id": request.form.get("manager_id") or "",
        "ids": [int(part.strip()) for part in ids_raw.split(",") if part.strip().isdigit()],
        "limit": int(request.form.get("limit") or 100),
    }
