"""events — v1 JSON API (feat/api-first-parity, group 4).

Mirrors the events / risk / security / investigations web pages
(`routes/events_risk.py`, `/admin/radius/events*`) as JSON. Reuses
``EventsRiskCenterService`` (no duplicated logic). Read endpoints for the
events center, event detail, risk flags, security view and investigations;
write endpoints for running the risk rules and opening an investigation.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.services.events_risk_center import (
    EventsRiskCenterService, EventsRiskError,
)
from ..auth import require_api_token
from ..json_input import InputError, json_object, opt_int, opt_text
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return getattr(g, "admin_username", None) or "api"


def _svc() -> EventsRiskCenterService:
    return EventsRiskCenterService(tenant_id=_tid())


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def register(bp: Blueprint) -> None:
    # ملاحظة: الأساس /events-center (لا /events) لأنّ /api/v1/events محجوز
    # لعقد business_os القديم (events list/record). أسماء الـendpointات ثابتة.
    bp.add_url_rule("/events-center", "events_list", require_api_token(events_list), methods=["GET"])
    bp.add_url_rule("/events-center/risk", "events_risk", require_api_token(events_risk), methods=["GET"])
    bp.add_url_rule("/events-center/risk/run", "events_risk_run", require_api_token(events_risk_run), methods=["POST"])
    bp.add_url_rule("/events-center/security", "events_security", require_api_token(events_security), methods=["GET"])
    bp.add_url_rule("/events-center/investigations", "events_investigations_list",
                    require_api_token(investigations_list), methods=["GET"])
    bp.add_url_rule("/events-center/investigations", "events_investigations_create",
                    require_api_token(investigations_create), methods=["POST"])
    # detail last so it doesn't shadow the literal sub-paths above.
    bp.add_url_rule("/events-center/<int:event_id>", "events_detail",
                    require_api_token(events_detail), methods=["GET"])


def events_list():
    """GET /events-center — مركز الأحداث مع الفلاتر (يطابق events_center).

    from/to = أيّام محلّيّة شاملة (from=to=اليوم → أحداث اليوم كلّها).
    ترقيم: limit (1..500، افتراضيًّا 200) + offset، مع total و has_more.
    """
    a = request.args
    try:
        limit = opt_int(a.get("limit"), label="limit") or 200
        offset = opt_int(a.get("offset"), label="offset", minimum=0) or 0
    except InputError as e:
        return fail("validation_error", e.message, status=422)
    limit = max(1, min(limit, 500))
    filters = dict(
        category=a.get("category") or "",
        severity=a.get("severity") or "",
        actor_type=a.get("actor_type") or "",
        actor_id=_int_or_none(a.get("actor_id")),
        target_type=a.get("target_type") or "",
        target_id=_int_or_none(a.get("target_id")),
        correlation_id=a.get("correlation_id") or "",
        date_from=a.get("from") or "",
        date_to=a.get("to") or "",
    )
    svc = _svc()
    try:
        events = svc.list_events(limit=limit, offset=offset, **filters)
        total = svc.count_events(**filters)
    except EventsRiskError as exc:
        return fail("validation_error", str(exc), status=422)
    return ok({
        "events": events, "count": len(events), "total": total,
        "limit": limit, "offset": offset,
        "has_more": offset + len(events) < total,
        "summary": svc.dashboard(),
    })


def events_detail(event_id: int):
    """GET /events/<id> — تفاصيل الحدث + الخط الزمني للكيان (يطابق events_detail)."""
    svc = _svc()
    try:
        event = svc.get_event(event_id)
    except EventsRiskError:
        return fail("not_found", "الحدث غير موجود.", status=404)
    timeline = []
    if event.get("target_type") and event.get("target_id"):
        timeline = svc.entity_timeline(
            entity_type=event.get("target_type") or "",
            entity_id=int(event.get("target_id") or 0),
        )
    return ok({"event": event, "timeline": timeline})


def events_risk():
    """GET /events/risk — إشارات الاحتيال + الملخّص (يطابق events_risk GET)."""
    svc = _svc()
    return ok({"flags": svc.list_fraud_flags(), "summary": svc.dashboard()})


def events_risk_run():
    """POST /events/risk/run — تشغيل قواعد المخاطر (يطابق events_risk POST)."""
    result = _svc().run_risk_rules()
    return ok({"result": result, "flags": _svc().list_fraud_flags()})


def events_security():
    """GET /events/security — أحداث الأمان + الإشارات المفتوحة (يطابق events_security)."""
    svc = _svc()
    return ok({
        "events": svc.list_events(category="security", limit=200),
        "flags": svc.list_fraud_flags(status="open", limit=100),
    })


def investigations_list():
    """GET /events/investigations — قائمة التحقيقات (يطابق events_investigations GET)."""
    status = request.args.get("status") or ""
    return ok({"investigations": _svc().list_investigations(status=status)})


def investigations_create():
    """POST /events/investigations — فتح تحقيق (يطابق events_investigations POST)."""
    body, err = json_object()
    if err:
        return err
    try:
        title = opt_text(body.get("title"), label="عنوان التحقيق", max_len=200)
        severity = opt_text(body.get("severity"), label="الخطورة", max_len=20) or "warning"
        entity_type = opt_text(body.get("entity_type"), label="نوع الكيان", max_len=60)
        entity_id = opt_int(body.get("entity_id"), label="معرّف الكيان")
        summary = opt_text(body.get("summary"), label="الملخّص", max_len=5000)
    except InputError as e:
        return fail("validation_error", e.message, status=422)
    if not title:
        return fail("validation_error", "عنوان التحقيق مطلوب.", status=422)
    try:
        inv = _svc().create_investigation(
            title=title,
            severity=severity,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            actor=_actor(),
        )
    except EventsRiskError as exc:
        return fail("validation_error", str(exc), status=422)
    return ok({"investigation": inv}, status=201)
