"""Events, risk, security, and investigation center routes."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..services.events_risk_center import EventsRiskCenterService, EventsRiskError


def register_events_risk_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/events", "events_center", events_center, methods=["GET"])
    bp.add_url_rule("/events/<int:event_id>", "events_detail", events_detail, methods=["GET"])
    bp.add_url_rule("/events/risk", "events_risk", events_risk, methods=["GET", "POST"])
    bp.add_url_rule("/events/security", "events_security", events_security, methods=["GET"])
    bp.add_url_rule("/events/investigations", "events_investigations", events_investigations, methods=["GET", "POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _svc() -> EventsRiskCenterService:
    return EventsRiskCenterService(tenant_id=_tid())


_EVENTS_PAGE = 200


def events_center():
    svc = _svc()
    filters = dict(
        category=request.args.get("category") or "",
        severity=request.args.get("severity") or "",
        actor_type=request.args.get("actor_type") or "",
        actor_id=_int_or_none(request.args.get("actor_id")),
        target_type=request.args.get("target_type") or "",
        target_id=_int_or_none(request.args.get("target_id")),
        correlation_id=request.args.get("correlation_id") or "",
        date_from=request.args.get("from") or "",
        date_to=request.args.get("to") or "",
    )
    offset = max(0, _int_or_none(request.args.get("offset")) or 0)
    try:
        events = svc.list_events(limit=_EVENTS_PAGE, offset=offset, **filters)
        total = svc.count_events(**filters)
    except EventsRiskError as exc:
        # تاريخ غير صالح: نُنبّه ونعرض بلا فلتر التاريخ بدل صفحة 500.
        flash(str(exc), "error")
        filters.update(date_from="", date_to="")
        events = svc.list_events(limit=_EVENTS_PAGE, offset=offset, **filters)
        total = svc.count_events(**filters)
    # روابط «السابق/التالي» تحفظ الفلاتر الحاليّة (كان العرض يقف عند 200 بلا ترقيم).
    args = {k: v for k, v in request.args.items() if k != "offset"}
    prev_url = (url_for("radius.events_center", **args, offset=max(0, offset - _EVENTS_PAGE))
                if offset > 0 else None)
    next_url = (url_for("radius.events_center", **args, offset=offset + _EVENTS_PAGE)
                if offset + len(events) < total else None)
    return render_template(
        "radius/events_center.html", events=events, summary=svc.dashboard(),
        total=total, offset=offset, prev_url=prev_url, next_url=next_url,
    )


def events_detail(event_id: int):
    try:
        event = _svc().get_event(event_id)
    except EventsRiskError:
        return redirect(url_for("radius.events_center"))
    timeline = _svc().entity_timeline(
        entity_type=event.get("target_type") or "",
        entity_id=int(event.get("target_id") or 0),
    ) if event.get("target_type") and event.get("target_id") else []
    return render_template("radius/events_detail.html", event=event, timeline=timeline)


def events_risk():
    svc = _svc()
    result = None
    if request.method == "POST":
        result = svc.run_risk_rules()
        flash(f"أنشأ فحص المخاطر {result['flags_created']} إشارة.", "success")
    return render_template("radius/events_risk.html", flags=svc.list_fraud_flags(), result=result, summary=svc.dashboard())


def events_security():
    return render_template(
        "radius/events_security.html",
        events=_svc().list_events(category="security", limit=200),
        flags=_svc().list_fraud_flags(status="open", limit=100),
    )


def events_investigations():
    svc = _svc()
    if request.method == "POST":
        try:
            svc.create_investigation(
                title=request.form.get("title") or "",
                severity=request.form.get("severity") or "warning",
                entity_type=request.form.get("entity_type") or "",
                entity_id=_int_or_none(request.form.get("entity_id")),
                summary=request.form.get("summary") or "",
                actor=_actor(),
            )
            flash("تم فتح تحقيق.", "success")
        except EventsRiskError as exc:
            flash(str(exc), "error")
        return redirect(url_for("radius.events_investigations"))
    return render_template("radius/events_investigations.html", investigations=svc.list_investigations())


def _int_or_none(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
