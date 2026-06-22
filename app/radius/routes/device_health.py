"""device_health routes — «تتبع حالة الأجهزة» (Network Device Health Monitor).

Page + JSON API. NO live MikroTik mutation in this delivery (Phase 1/2):
  GET   /admin/radius/device-health                         — page
  GET   /admin/radius/device-health/api/devices            — list (+summary)
  POST  /admin/radius/device-health/api/devices            — create
  PATCH /admin/radius/device-health/api/devices/<id>       — update
  POST  /admin/radius/device-health/api/devices/<id>/enable
  POST  /admin/radius/device-health/api/devices/<id>/disable
  POST  /admin/radius/device-health/api/devices/<id>/delete
  POST  /admin/radius/device-health/api/devices/<id>/sync       — read-only diff
  POST  /admin/radius/device-health/api/devices/<id>/test-ping  — read-only ping
  GET   /admin/radius/device-health/api/plan?router_id=&interface=&ip=…

CSRF: these live under /admin/… (not /api/…) so the global guard enforces
CSRF on every POST/PATCH — the page JS sends the X-CSRFToken header.
"""
from __future__ import annotations

import json
from typing import Any

from flask import (Blueprint, Response, g, jsonify, render_template, request,
                   session, stream_with_context)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import device_health_repo as repo
from ..services import device_health as svc
from ..services.device_health import DeviceHealthError


def register_device_health_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/device-health", "device_health_page",
                    device_health_page, methods=["GET"])
    bp.add_url_rule("/device-health/api/devices", "device_health_api_list",
                    device_health_api_list, methods=["GET"])
    bp.add_url_rule("/device-health/api/devices", "device_health_api_create",
                    device_health_api_create, methods=["POST"])
    bp.add_url_rule("/device-health/api/devices/<int:device_id>",
                    "device_health_api_update",
                    device_health_api_update, methods=["PATCH", "POST"])
    bp.add_url_rule("/device-health/api/devices/<int:device_id>/enable",
                    "device_health_api_enable",
                    device_health_api_enable, methods=["POST"])
    bp.add_url_rule("/device-health/api/devices/<int:device_id>/disable",
                    "device_health_api_disable",
                    device_health_api_disable, methods=["POST"])
    bp.add_url_rule("/device-health/api/devices/<int:device_id>/delete",
                    "device_health_api_delete",
                    device_health_api_delete, methods=["POST"])
    bp.add_url_rule("/device-health/api/devices/<int:device_id>/sync",
                    "device_health_api_sync",
                    device_health_api_sync, methods=["POST"])
    bp.add_url_rule("/device-health/api/devices/<int:device_id>/apply",
                    "device_health_api_apply",
                    device_health_api_apply, methods=["POST"])
    bp.add_url_rule("/device-health/api/devices/<int:device_id>/test-ping",
                    "device_health_api_test_ping",
                    device_health_api_test_ping, methods=["POST"])
    bp.add_url_rule("/device-health/api/poll", "device_health_api_poll",
                    device_health_api_poll, methods=["POST"])
    bp.add_url_rule("/device-health/api/poll/stream",
                    "device_health_api_poll_stream",
                    device_health_api_poll_stream, methods=["POST"])
    bp.add_url_rule("/device-health/api/devices/<int:device_id>/events",
                    "device_health_api_events",
                    device_health_api_events, methods=["GET"])
    bp.add_url_rule("/device-health/api/devices/<int:device_id>/alerts",
                    "device_health_api_alerts",
                    device_health_api_alerts, methods=["GET"])
    bp.add_url_rule("/device-health/api/router-interfaces",
                    "device_health_api_router_interfaces",
                    device_health_api_router_interfaces, methods=["GET"])
    bp.add_url_rule("/device-health/api/live-apply",
                    "device_health_api_live_apply",
                    device_health_api_live_apply, methods=["GET", "POST"])
    bp.add_url_rule("/device-health/api/plan", "device_health_api_plan",
                    device_health_api_plan, methods=["GET"])
    # إعدادات الفحص الدوري (تفعيل + الفترة بالدقائق) — يقرؤها/يحفظها
    # نموذج الصفحة ويحترمها device_health_poll_worker كل دورة.
    bp.add_url_rule("/device-health/api/poll-settings",
                    "device_health_api_poll_settings",
                    device_health_api_poll_settings, methods=["GET", "POST"])
    # سجل الفحوصات (للتحديث الحي بعد «فحص الكل») + الإحصائيات.
    bp.add_url_rule("/device-health/api/checks",
                    "device_health_api_checks",
                    device_health_api_checks, methods=["GET"])


# ── helpers ────────────────────────────────────────────────────

def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _payload() -> dict:
    """Accept either a JSON body or form-encoded fields."""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return {k: v for k, v in request.form.items()}


def _actor() -> str:
    return str(session.get("admin_user") or session.get("admin_name") or "system")


# ── views ──────────────────────────────────────────────────────

def device_health_page():
    tenant_id = _tid()
    data = svc.list_with_routers(tenant_id)
    counts = svc.summary(tenant_id)
    # سجل الفحوصات + إحصائياته + إعدادات الفحص الدوري — أقسام «الإدارة
    # الاحترافية». دفاعية على جدول ناقص في بيئة لم تشغّل المهاجرة بعد.
    try:
        from ..db.repos import device_health_checks_repo as checks_repo
        checks = checks_repo.list_checks(tenant_id, limit=20)
        check_stats = checks_repo.stats(tenant_id, hours=24)
    except Exception:  # noqa: BLE001
        checks, check_stats = [], {}
    from ...workers.device_health_poll_worker import poll_settings
    # هل تلجرام مُفعّل؟ تنبيهات انقطاع/عودة الأجهزة تُسلَّم افتراضيًا عبر تلجرام
    # (router_down/router_up في محرّك الإشعارات + قناة الجهاز الافتراضية). إن لم
    # يكن مُفعّلًا، نُظهر شريطًا واضحًا «فعّله ليصلك الإشعار» بدل الصمت.
    try:
        from ..db.repos import tenant_telegram_settings_repo as _tg
        telegram_configured = _tg.is_configured(tenant_id)
    except Exception:  # noqa: BLE001
        telegram_configured = False
    return render_template(
        "radius/device_health.html",
        devices=data["devices"],
        routers=data["routers"],
        counts=counts,
        device_types=sorted(repo.ALLOWED_DEVICE_TYPES),
        alert_channels=["", "telegram", "sms", "whatsapp"],
        live_apply=svc.live_apply_state(tenant_id),
        checks=checks,
        check_stats=check_stats,
        poll_settings=poll_settings(tenant_id),
        telegram_configured=telegram_configured,
    )


def device_health_api_list():
    tenant_id = _tid()
    filters: dict[str, Any] = {}
    if request.args.get("router_id"):
        filters["router_id"] = _int(request.args.get("router_id"))
    if request.args.get("status"):
        filters["status"] = request.args.get("status")
    if request.args.get("device_type"):
        filters["device_type"] = request.args.get("device_type")
    data = svc.list_with_routers(tenant_id, **filters)
    return jsonify({
        "ok": True,
        "devices": data["devices"],
        "summary": svc.summary(tenant_id),
    })


def device_health_api_create():
    tenant_id = _tid()
    try:
        result = svc.create_device(tenant_id, _payload())
    except DeviceHealthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    device = repo.get_device(tenant_id, result["device_id"])
    return jsonify({
        "ok": True,
        "device": device,
        "warnings": result["warnings"],
        "network": result["network"],
    }), 201


def device_health_api_update(device_id: int):
    tenant_id = _tid()
    try:
        result = svc.update_device(tenant_id, device_id, _payload())
    except DeviceHealthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({
        "ok": True,
        "device": repo.get_device(tenant_id, device_id),
        "warnings": result["warnings"],
    })


def device_health_api_live_apply():
    """Read (GET) or set (POST {enabled}) the panel live-apply toggle.
    Owner rule: live-apply is controlled from the panel, not the terminal."""
    tenant_id = _tid()
    if request.method == "GET":
        return jsonify({"ok": True, **svc.live_apply_state(tenant_id)})
    body = _payload()
    enabled = _to_bool(body.get("enabled"))
    try:
        by = int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        by = 0
    state = svc.set_live_apply(tenant_id, enabled, by=by)
    return jsonify({"ok": True, **state})


def device_health_api_enable(device_id: int):
    tenant_id = _tid()
    ok = svc.set_monitoring(tenant_id, device_id, True)
    if not ok:
        return jsonify({"ok": False, "error": "الجهاز غير موجود."}), 404
    return jsonify({"ok": True, "device": repo.get_device(tenant_id, device_id)})


def device_health_api_disable(device_id: int):
    tenant_id = _tid()
    ok = svc.set_monitoring(tenant_id, device_id, False)
    if not ok:
        return jsonify({"ok": False, "error": "الجهاز غير موجود."}), 404
    return jsonify({"ok": True, "device": repo.get_device(tenant_id, device_id)})


def device_health_api_delete(device_id: int):
    tenant_id = _tid()
    ok = svc.delete_device(tenant_id, device_id, actor=_actor())
    if not ok:
        return jsonify({"ok": False, "error": "الجهاز غير موجود."}), 404
    return jsonify({"ok": True})


def device_health_api_sync(device_id: int):
    """Phase 2 — read the router and return an idempotent plan. NO mutation."""
    tenant_id = _tid()
    try:
        result = svc.live_plan(tenant_id, device_id)
    except DeviceHealthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(result)


def device_health_api_apply(device_id: int):
    """Phase 3 — controlled live apply. NO-OP (gated) unless the env master
    switch is set; applies only missing planned items, idempotent, audited."""
    tenant_id = _tid()
    body = _payload()
    actions = body.get("actions") if isinstance(body.get("actions"), list) else None
    try:
        result = svc.apply_device(tenant_id, device_id, actions=actions)
    except DeviceHealthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    status = 200 if (result.get("ok") or result.get("gated")) else 502
    return jsonify(result), status


def device_health_api_test_ping(device_id: int):
    tenant_id = _tid()
    try:
        result = svc.test_ping(tenant_id, device_id)
    except DeviceHealthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(result)




def device_health_api_poll():
    """Phase 4 — run one polling sweep on demand for this tenant (router
    reads only; no mutation). Returns the sweep summary. يُدوَّن في سجل
    الفحوصات (source=manual)."""
    tenant_id = _tid()
    from ..services import device_health_poller as poller
    summary = poller.tick(tenant_id=tenant_id, log_source="manual")
    return jsonify({"ok": True, "summary": summary})


def device_health_api_poll_stream():
    """Streaming «فحص الكل» — emits one NDJSON line per device as it is checked
    (start → progress×N → done) so the UI shows a live progress bar. Router
    reads only; no mutation. stream_with_context keeps g.tenant_id + the DB
    connection alive for the duration of the sweep. يُدوَّن في سجل الفحوصات
    (source=manual)."""
    tenant_id = _tid()
    from ..services import device_health_poller as poller

    @stream_with_context
    def generate():
        try:
            for ev in poller.iter_tick(tenant_id=tenant_id,
                                       log_source="manual"):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as exc:  # noqa: BLE001 — surface as a stream event
            yield json.dumps({"type": "error", "error": str(exc)},
                             ensure_ascii=False) + "\n"

    return Response(generate(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


def device_health_api_poll_settings():
    """قراءة (GET) أو حفظ (POST {enabled, minutes}) إعدادات الفحص الدوري
    لهذا المستأجر — device_health_poll_worker يحترمها من الدورة التالية."""
    tenant_id = _tid()
    from ...workers.device_health_poll_worker import poll_settings
    if request.method == "GET":
        return jsonify({"ok": True, **poll_settings(tenant_id)})
    body = _payload()
    enabled = _to_bool(body.get("enabled"))
    minutes = max(1, min(24 * 60, _int(body.get("minutes"), 5) or 5))
    from ..db.repos import tenants_repo
    try:
        by = int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        by = 0
    tenants_repo.set_setting(tenant_id, "device_health.poll_enabled",
                             "1" if enabled else "0", by=by)
    tenants_repo.set_setting(tenant_id, "device_health.poll_minutes",
                             str(minutes), by=by)
    return jsonify({"ok": True, **poll_settings(tenant_id)})


def device_health_api_checks():
    """آخر فحوصات السجل + الإحصائيات — لتحديث القسم بعد «فحص الكل» بلا
    إعادة تحميل الصفحة."""
    tenant_id = _tid()
    try:
        from ..db.repos import device_health_checks_repo as checks_repo
        return jsonify({
            "ok": True,
            "checks": checks_repo.list_checks(tenant_id, limit=20),
            "stats": checks_repo.stats(tenant_id, hours=24),
        })
    except Exception as exc:  # noqa: BLE001 — جدول ناقص في بيئة قديمة
        return jsonify({"ok": False, "error": str(exc)}), 500


def device_health_api_events(device_id: int):
    """Phase 6 — recent status-change history for one device."""
    tenant_id = _tid()
    events = repo.list_events(tenant_id, device_id=device_id, limit=100)
    return jsonify({"ok": True, "events": events})


def device_health_api_alerts(device_id: int):
    """Phase 6 — recent alert decisions (sent/skipped/failed) for one device."""
    tenant_id = _tid()
    return jsonify({"ok": True,
                    "alerts": repo.list_alerts(tenant_id, device_id=device_id)})


def device_health_api_router_interfaces():
    """Live LAN interfaces for the selected router/CHR (WAN + tunnels excluded).
    Offline router → {online: False} so the form falls back to free-text."""
    tenant_id = _tid()
    router_id = _int(request.args.get("router_id"))
    if not router_id:
        return jsonify({"ok": True, "online": False, "interfaces": []})
    try:
        result = svc.list_router_interfaces(tenant_id, router_id)
    except DeviceHealthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    return jsonify({"ok": True, **result})


def device_health_api_plan():
    """Phase 1 dry-run plan from query params (router not contacted)."""
    params = {
        "interface_name": request.args.get("interface")
        or request.args.get("interface_name") or "",
        "ip_address": request.args.get("ip")
        or request.args.get("ip_address") or "",
        "subnet_prefix": request.args.get("subnet_prefix") or 24,
        "gateway_last_octet": request.args.get("gateway_last_octet") or 254,
        "netwatch_interval_sec": request.args.get("netwatch_interval_sec") or 60,
        "netwatch_timeout_sec": request.args.get("netwatch_timeout_sec") or 3,
    }
    plan = svc.intended_plan(params)
    status = 200 if plan.get("ok") else 400
    return jsonify({"ok": plan.get("ok", False), "plan": plan}), status


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")
