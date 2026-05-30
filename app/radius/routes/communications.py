"""Notification and campaign web routes."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..services.notification_campaigns import NotificationCampaignError, NotificationCampaignService


def register_communications_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/communications", "communications", communications_dashboard, methods=["GET"])
    bp.add_url_rule("/communications/templates", "communications_templates", communications_templates, methods=["GET", "POST"])
    bp.add_url_rule("/communications/send", "communications_send", communications_send, methods=["GET", "POST"])
    bp.add_url_rule("/communications/campaigns", "communications_campaigns", communications_campaigns, methods=["GET", "POST"])
    bp.add_url_rule("/communications/deliveries", "communications_deliveries", communications_deliveries, methods=["GET"])
    bp.add_url_rule("/communications/audience", "communications_audience", communications_audience, methods=["GET", "POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


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
            flash("Template saved.", "success")
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
                flash(f"Queued {result['queued_count']} delivery records.", "success")
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


def _audience_from_form() -> dict:
    ids_raw = request.form.get("ids") or ""
    return {
        "target": request.form.get("target") or "subscriber",
        "manager_id": request.form.get("manager_id") or "",
        "ids": [int(part.strip()) for part in ids_raw.split(",") if part.strip().isdigit()],
        "limit": int(request.form.get("limit") or 100),
    }
