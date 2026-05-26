"""Setup Wizard v3 — thin route layer.

Every endpoint maps to one state-machine transition in
`setup_wizard_v3.WizardV3Service`. The page is a single
template that polls `/state` every 2-3 seconds and rerenders
the appropriate section.

Endpoints
=========

  GET   /admin/radius/setup-wizard-v3
        Render the single-page wizard.

  POST  /admin/radius/setup-wizard-v3/runs
        Create a new run. → returns {run: {...}}

  GET   /admin/radius/setup-wizard-v3/runs/<id>/state
        Polling endpoint. → returns {run: {...}}

  POST  /admin/radius/setup-wizard-v3/runs/<id>/router-info
        Submit name + type. COLLECTING → PLANNING

  POST  /admin/radius/setup-wizard-v3/runs/<id>/generate-script
        Build + store the unified .rsc.
        PLANNING → AWAITING_HANDSHAKE

  POST  /admin/radius/setup-wizard-v3/runs/<id>/submit-key
        Operator paste the WG public key.
        AWAITING_HANDSHAKE → APPLYING_SERVER_PEER

  POST  /admin/radius/setup-wizard-v3/runs/<id>/apply-server-peer
        Write peers.d file. APPLYING_SERVER_PEER → VERIFYING

  POST  /admin/radius/setup-wizard-v3/runs/<id>/mark-handshake
        Operator-driven fallback: "ping worked".
        VERIFYING → REGISTERING

  POST  /admin/radius/setup-wizard-v3/runs/<id>/register
        Create nas_devices row. REGISTERING → COMPLETE

  GET   /wz/<short_code>.rsc
        Public path the MikroTik /tool fetch hits to download
        the unified script. Auth = secret short code.
"""
from __future__ import annotations

import os
from flask import Blueprint, Response, g, jsonify, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..services.setup_wizard_v3 import (
    V3Error, V3InvalidState, V3NotFound, WizardV3Service,
)
from ..services.setup_wizard_added_services_phase_planner import (
    AddedServicesPhasePlanner,
)
from ..services.setup_wizard_broadband_phase_planner import (
    BroadbandPhasePlanner,
)
from ..services.setup_wizard_hotspot_phase_planner import (
    HotspotPhasePlanner,
)
from ..services.setup_wizard_internet_phase_planner import (
    InternetPhasePlanner,
)
from ..services.setup_wizard_vpn_radius_phase_planner import (
    VpnRadiusPhasePlanner,
)


# Map phase name → planner class. SW7 binds the SW1-SW6
# phase planners to the v3 route layer so the operator can
# preview per-phase scripts on top of the unified v3 setup.
PHASE_PLANNERS = {
    "internet":       InternetPhasePlanner,
    "vpn_radius":     VpnRadiusPhasePlanner,
    "hotspot":        HotspotPhasePlanner,
    "broadband":      BroadbandPhasePlanner,
    "added_services": AddedServicesPhasePlanner,
}


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> str:
    return getattr(g, "admin_username", "") or "wizard"


def _svc() -> WizardV3Service:
    return WizardV3Service()


def _body() -> dict:
    if not request.is_json:
        return request.form.to_dict()
    return request.get_json(silent=True) or {}


def _err(msg: str, status: int = 400, code: str = "v3_error"):
    return jsonify({
        "ok": False,
        "error": msg,
        "code": code,
    }), status


# ─── Handlers ───────────────────────────────────────────────


def setup_wizard_v3_page():
    return render_template(
        "radius/setup_wizard_v3.html",
        page_title="معالج إضافة راوتر v3",
    )


# Router Services Dashboard — landing page that opens AFTER a router
# finishes the base setup wizard. Lists the optional configurations
# the operator can do on this specific router (Hotspot, Broadband,
# website block/open, public-IP change, remote tech access) — each
# one its own multi-step phased flow, hidden behind a single "إعداد"
# button per card. Designed to keep the operator focused on intent,
# not on RouterOS commands.
ROUTER_SERVICE_CARDS = [
    {
        "key": "hotspot",
        "title_ar": "Hotspot",
        "subtitle_ar": "بوابة دخول عامّة مع صفحة تسجيل دخول",
        "icon": "wifi",
        "color": "blue",
        "phases_count": 4,
    },
    {
        "key": "broadband",
        "title_ar": "Broadband",
        "subtitle_ar": "PPPoE — اشتراك ثابت بمستخدم وكلمة مرور",
        "icon": "ethernet",
        "color": "green",
        "phases_count": 4,
    },
    {
        "key": "block-sites",
        "title_ar": "حجب مواقع",
        "subtitle_ar": "منع الوصول لمواقع محدّدة",
        "icon": "ban",
        "color": "red",
        "phases_count": 3,
    },
    {
        "key": "open-sites",
        "title_ar": "فتح مواقع",
        "subtitle_ar": "السماح بمواقع بدون تسجيل دخول",
        "icon": "circle-check",
        "color": "teal",
        "phases_count": 3,
    },
    {
        "key": "public-ip",
        "title_ar": "تغيير IP الخروج",
        "subtitle_ar": "توجيه المشتركين عبر IP عام جديد",
        "icon": "globe",
        "color": "amber",
        "phases_count": 4,
    },
    {
        "key": "remote-access",
        "title_ar": "اتصال عن بُعد",
        "subtitle_ar": "نفق VPN للفنّي لإدارة الراوتر",
        "icon": "key",
        "color": "purple",
        "phases_count": 3,
    },
]


def setup_wizard_v3_router_services_dashboard(router_id: int):
    """Legacy URL — Router Services Dashboard was merged into the
    per-router unified hub at /admin/radius/mt/<id>/dashboard in
    May 2026. Kept as a 302 redirect so existing operator
    bookmarks + the «إعداد خدمات» button from mt/operations land
    on the same destination. The 6 service cards now appear as a
    'خدمات الراوتر' section at the top of the hub."""
    from flask import redirect, url_for
    return redirect(
        url_for("radius.mt_dashboard", nas_id=router_id),
        code=302,
    )


def setup_wizard_v3_router_discover_interfaces(router_id: int):
    """Router-scoped interface discovery for the Services Dashboard.

    Calls the MikroTik API over the established VPN tunnel using the
    credentials stored in `nas_devices` for this router. Used by every
    per-service partial that needs to let the operator pick a real
    interface (Hotspot, Broadband, etc.) instead of typing names.

    Returns: { ok, interfaces: [{name, type, running, disabled, recommended}, ...] }
    """
    from ..db.repos import nas_repo
    from ..services.setup_wizard_v3_interface_discovery import (
        InterfaceDiscoveryError, discover_via_api,
    )

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return _err(
            "لا توجد كلمة مرور API لهذا الراوتر — افتح صفحة تعديل الراوتر وأدخلها.",
            status=409, code="no_api_password",
        )
    try:
        interfaces = discover_via_api(
            router_vpn_ip=nas.address,
            api_user=nas.api_user or "admin",
            api_password=nas.api_password,
            port=int(nas.api_port or 8728),
            use_tls=bool(nas.api_use_tls),
        )
    except InterfaceDiscoveryError as exc:
        return _err(str(exc), status=502, code="discovery_failed")
    except Exception as exc:  # noqa: BLE001
        return _err(f"تعذّر اكتشاف الواجهات: {exc}", status=500,
                    code="discovery_error")
    return jsonify({"ok": True, "interfaces": interfaces})


def _plan_hotspot(router_id: int, inputs: dict) -> dict:
    """Shared planner call used by both preview and apply.
    Returns: (plan_result_dict, http_status, error_dict_or_none)."""
    from ..services.setup_wizard_hotspot_phase_planner import (
        HotspotPhasePlanner,
    )
    try:
        result = HotspotPhasePlanner().plan(run_id=router_id, inputs=inputs)
    except Exception as exc:  # noqa: BLE001
        return None, 500, {"error": f"planner failed: {exc}",
                           "code": "planner_failed"}
    return result, 200, None


def _hotspot_preview_bullets(plan_result) -> list[str]:
    """Translate a HotspotPhasePlanner result into plain-Arabic
    bullets for the «معاينة» pane. Never expose the .rsc body."""
    bullets = []
    notes = list(getattr(plan_result, "notes", ()) or ())
    if notes:
        bullets.extend(notes)
    # Fallback summary if the planner didn't emit notes.
    if not bullets:
        bullets.append("سيتم إنشاء خادم Hotspot جديد على الواجهات المختارة.")
        bullets.append("سيُضاف خادم DHCP يوزّع عناوين IP على الأجهزة المتصلة.")
        bullets.append("سيُربط بـ RADIUS الخاص بهذا الخادم لتفعيل الحسابات.")
    warnings = list(getattr(plan_result, "warnings", ()) or ())
    for w in warnings:
        bullets.append(f"⚠ تنبيه: {w}")
    return bullets


def setup_wizard_v3_hotspot_preview(router_id: int):
    """Phase 2 «معاينة»: run the Hotspot planner and turn the result
    into plain-Arabic bullets. No router contact, no script exposure."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")

    body = _body() or {}
    inputs = {
        "selected_interfaces": list(body.get("selected_interfaces") or []),
        "subnet_base": str(body.get("subnet_base") or "10.20.0.0/16"),
        "mode": "manual",
        "blocked_interfaces": list(body.get("blocked_interfaces") or []),
        "blocked_network_cidrs": [],
    }
    if not inputs["selected_interfaces"]:
        return _err(
            "اختر واجهة شبكة واحدة على الأقل قبل المعاينة.",
            status=400, code="no_interface_selected",
        )

    plan_result, status, err = _plan_hotspot(router_id, inputs)
    if err:
        return jsonify({"ok": False, **err}), status

    if plan_result.blocking_errors:
        # Surface planner blockers as friendly bullets instead of
        # raw codes.
        return jsonify({
            "ok": False,
            "code": "planner_blocked",
            "blocking_errors": list(plan_result.blocking_errors),
            "bullets": _hotspot_preview_bullets(plan_result),
        }), 409

    return jsonify({
        "ok": True,
        "bullets": _hotspot_preview_bullets(plan_result),
    })


def setup_wizard_v3_hotspot_apply(router_id: int):
    """Phase 3 «إرسال»: re-plan (idempotent) + push the resulting
    script to the router via LiveRouterExecutor. Returns per-substep
    progress in one shot — the UI animates that on the client side."""
    from ..db.repos import nas_repo
    from ..services.npc_router_executor import (
        ExecutorNotConfigured, get_router_executor,
    )

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")

    body = _body() or {}
    inputs = {
        "selected_interfaces": list(body.get("selected_interfaces") or []),
        "subnet_base": str(body.get("subnet_base") or "10.20.0.0/16"),
        "mode": "manual",
        "blocked_interfaces": list(body.get("blocked_interfaces") or []),
        "blocked_network_cidrs": [],
    }
    if not inputs["selected_interfaces"]:
        return _err("اختر واجهة شبكة واحدة على الأقل.",
                    status=400, code="no_interface_selected")

    plan_result, status, err = _plan_hotspot(router_id, inputs)
    if err:
        return jsonify({"ok": False, **err}), status
    if plan_result.blocking_errors:
        return jsonify({
            "ok": False, "code": "planner_blocked",
            "blocking_errors": list(plan_result.blocking_errors),
        }), 409
    if not plan_result.script or not plan_result.script.strip():
        return _err("لا يوجد سكربت لإرساله — تحقّق من المدخلات.",
                    status=400, code="empty_script")

    try:
        executor = get_router_executor()
        exec_result = executor.execute_forward(
            router_id=router_id, script=plan_result.script,
        )
    except ExecutorNotConfigured:
        return _err(
            "وحدة تنفيذ السكربتات غير مُهيّأة على الخادم. "
            "راجع المسؤول لتفعيل LiveRouterExecutor.",
            status=503, code="executor_not_configured",
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"خطأ غير متوقّع عند الإرسال: {exc}",
                    status=500, code="apply_error")

    if not exec_result.ok:
        return jsonify({
            "ok": False, "code": "apply_failed",
            "error": exec_result.error_message or "تعذّر تنفيذ السكربت",
            "stderr": exec_result.stderr or "",
            "duration_ms": exec_result.duration_ms,
        }), 502

    return jsonify({
        "ok": True,
        "duration_ms": exec_result.duration_ms,
        "substeps": [
            {"key": "connect", "status": "done"},
            {"key": "send",    "status": "done"},
            {"key": "commit",  "status": "done"},
        ],
    })


def setup_wizard_v3_hotspot_verify(router_id: int):
    """Phase 4 «تحقّق»: real probe on the router. Reads
    /ip/hotspot/server/print and /ip/dhcp-server/print to confirm
    the Hotspot + DHCP services are actually running. Each check
    returns ok/fail with an Arabic label."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return _err("لا توجد كلمة مرور API لهذا الراوتر.",
                    status=409, code="no_api_password")

    checks = []

    # Lazy import — only when this endpoint is hit.
    try:
        from ..services.mikrotik_admin_client import MikrotikClient
    except Exception as exc:  # noqa: BLE001
        return _err(f"تعذّر تحميل عميل MikroTik: {exc}",
                    status=500, code="mt_client_load_error")

    cfg = {
        "host": nas.address,
        "username": nas.api_user or "admin",
        "password": nas.api_password,
        "port": int(nas.api_port or 8728),
        "use_tls": bool(nas.api_use_tls),
        "timeout": 8.0,
    }

    try:
        with MikrotikClient(**cfg) as mt:
            # Check 1: Hotspot server present & enabled.
            servers = list(mt.print_("/ip/hotspot/server/print"))
            running = [s for s in servers
                       if str(s.get("disabled", "")).lower() in ("false", "no", "")]
            checks.append({
                "label": "خادم Hotspot يعمل",
                "status": "ok" if running else "fail",
            })
            # Check 2: At least one DHCP server tied to a hotspot
            # interface.
            dhcp = list(mt.print_("/ip/dhcp-server/print"))
            dhcp_active = [d for d in dhcp
                           if str(d.get("disabled", "")).lower() in ("false", "no", "")]
            checks.append({
                "label": "خادم DHCP يوزّع العناوين",
                "status": "ok" if dhcp_active else "fail",
            })
            # Check 3: Hotspot profile points at RADIUS (best effort).
            profiles = list(mt.print_("/ip/hotspot/profile/print"))
            radius_linked = any(
                str(p.get("use-radius", "")).lower() in ("true", "yes")
                for p in profiles
            )
            checks.append({
                "label": "تفعيل مصادقة RADIUS",
                "status": "ok" if radius_linked else "fail",
            })
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False, "code": "probe_failed",
            "error": f"تعذّر الاتصال بالراوتر للفحص: {exc}",
            "checks": checks,
        }), 502

    all_ok = all(c["status"] == "ok" for c in checks)
    return jsonify({"ok": all_ok, "checks": checks})


def _plan_broadband(router_id: int, inputs: dict):
    """Shared Broadband planner call. Returns (PhasePlanResult, status, err_dict)."""
    from ..services.setup_wizard_broadband_phase_planner import (
        BroadbandPhasePlanner,
    )
    try:
        result = BroadbandPhasePlanner().plan(run_id=router_id, inputs=inputs)
    except Exception as exc:  # noqa: BLE001
        return None, 500, {"error": f"planner failed: {exc}",
                           "code": "planner_failed"}
    return result, 200, None


def _broadband_preview_bullets(plan_result) -> list[str]:
    bullets = []
    notes = list(getattr(plan_result, "notes", ()) or ())
    if notes:
        bullets.extend(notes)
    if not bullets:
        bullets.append("سيتم تفعيل خادم PPPoE على الواجهات المختارة.")
        bullets.append("سيُنشَأ pool عناوين IP يوزّع على المشتركين تلقائياً.")
        bullets.append("سيُربط بـ RADIUS لمصادقة المشتركين باسم المستخدم وكلمة المرور.")
    warnings = list(getattr(plan_result, "warnings", ()) or ())
    for w in warnings:
        bullets.append(f"⚠ تنبيه: {w}")
    return bullets


def setup_wizard_v3_broadband_preview(router_id: int):
    """Phase 2 «معاينة»: Broadband planner → plain-Arabic bullets."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")

    body = _body() or {}
    inputs = {
        "selected_interfaces": list(body.get("selected_interfaces") or []),
        "local_address": str(body.get("local_address") or "") or None,
        "remote_pool_cidr": str(body.get("remote_pool_cidr") or "") or None,
        "mode": "manual",
        "blocked_interfaces": list(body.get("blocked_interfaces") or []),
        "blocked_network_cidrs": [],
    }
    if not inputs["selected_interfaces"]:
        return _err("اختر واجهة شبكة واحدة على الأقل قبل المعاينة.",
                    status=400, code="no_interface_selected")
    plan_result, status, err = _plan_broadband(router_id, inputs)
    if err:
        return jsonify({"ok": False, **err}), status
    if plan_result.blocking_errors:
        return jsonify({
            "ok": False, "code": "planner_blocked",
            "blocking_errors": list(plan_result.blocking_errors),
            "bullets": _broadband_preview_bullets(plan_result),
        }), 409
    return jsonify({"ok": True,
                    "bullets": _broadband_preview_bullets(plan_result)})


def setup_wizard_v3_broadband_apply(router_id: int):
    """Phase 3 «إرسال»: re-plan + execute via LiveRouterExecutor."""
    from ..db.repos import nas_repo
    from ..services.npc_router_executor import (
        ExecutorNotConfigured, get_router_executor,
    )

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")

    body = _body() or {}
    inputs = {
        "selected_interfaces": list(body.get("selected_interfaces") or []),
        "local_address": str(body.get("local_address") or "") or None,
        "remote_pool_cidr": str(body.get("remote_pool_cidr") or "") or None,
        "mode": "manual",
        "blocked_interfaces": list(body.get("blocked_interfaces") or []),
        "blocked_network_cidrs": [],
    }
    if not inputs["selected_interfaces"]:
        return _err("اختر واجهة شبكة واحدة على الأقل.",
                    status=400, code="no_interface_selected")
    plan_result, status, err = _plan_broadband(router_id, inputs)
    if err:
        return jsonify({"ok": False, **err}), status
    if plan_result.blocking_errors:
        return jsonify({
            "ok": False, "code": "planner_blocked",
            "blocking_errors": list(plan_result.blocking_errors),
        }), 409
    if not plan_result.script or not plan_result.script.strip():
        return _err("لا يوجد سكربت لإرساله — تحقّق من المدخلات.",
                    status=400, code="empty_script")
    try:
        exec_result = get_router_executor().execute_forward(
            router_id=router_id, script=plan_result.script,
        )
    except ExecutorNotConfigured:
        return _err(
            "وحدة تنفيذ السكربتات غير مُهيّأة على الخادم.",
            status=503, code="executor_not_configured",
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"خطأ غير متوقّع: {exc}",
                    status=500, code="apply_error")
    if not exec_result.ok:
        return jsonify({
            "ok": False, "code": "apply_failed",
            "error": exec_result.error_message or "تعذّر تنفيذ السكربت",
            "stderr": exec_result.stderr or "",
            "duration_ms": exec_result.duration_ms,
        }), 502
    return jsonify({
        "ok": True,
        "duration_ms": exec_result.duration_ms,
        "substeps": [
            {"key": "connect", "status": "done"},
            {"key": "send",    "status": "done"},
            {"key": "commit",  "status": "done"},
        ],
    })


def setup_wizard_v3_broadband_verify(router_id: int):
    """Phase 4 «تحقّق»: probe PPPoE server + RADIUS link."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return _err("لا توجد كلمة مرور API لهذا الراوتر.",
                    status=409, code="no_api_password")

    checks = []
    try:
        from ..services.mikrotik_admin_client import MikrotikClient
    except Exception as exc:  # noqa: BLE001
        return _err(f"تعذّر تحميل عميل MikroTik: {exc}",
                    status=500, code="mt_client_load_error")

    cfg = {
        "host": nas.address,
        "username": nas.api_user or "admin",
        "password": nas.api_password,
        "port": int(nas.api_port or 8728),
        "use_tls": bool(nas.api_use_tls),
        "timeout": 8.0,
    }
    try:
        with MikrotikClient(**cfg) as mt:
            # Check 1: PPPoE server present + enabled.
            servers = list(mt.print_("/interface/pppoe-server/server/print"))
            running = [s for s in servers
                       if str(s.get("disabled", "")).lower() in ("false", "no", "")]
            checks.append({
                "label": "خادم PPPoE يعمل",
                "status": "ok" if running else "fail",
            })
            # Check 2: IP pool exists.
            pools = list(mt.print_("/ip/pool/print"))
            checks.append({
                "label": "نطاق IP المشتركين موجود",
                "status": "ok" if pools else "fail",
            })
            # Check 3: PPP profile references RADIUS.
            profiles = list(mt.print_("/ppp/profile/print"))
            radius_linked = any(
                str(p.get("use-radius", "")).lower() in ("true", "yes",
                                                          "default-use-radius")
                for p in profiles
            ) or any(
                "radius" in str(p.get("name", "")).lower()
                for p in profiles
            )
            checks.append({
                "label": "ملف PPP مرتبط بـ RADIUS",
                "status": "ok" if radius_linked else "fail",
            })
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False, "code": "probe_failed",
            "error": f"تعذّر الاتصال بالراوتر للفحص: {exc}",
            "checks": checks,
        }), 502

    all_ok = all(c["status"] == "ok" for c in checks)
    return jsonify({"ok": all_ok, "checks": checks})


def _plan_added_service(router_id: int, service_key: str, inputs: dict):
    """Shared planner call for added services (walled_garden, block_sites,
    etc). Returns (PhasePlanResult, status, err_dict)."""
    from ..services.setup_wizard_added_services_phase_planner import (
        AddedServicesPhasePlanner,
    )
    merged = dict(inputs)
    merged["service_key"] = service_key
    try:
        result = AddedServicesPhasePlanner().plan(
            run_id=router_id, inputs=merged,
        )
    except Exception as exc:  # noqa: BLE001
        return None, 500, {"error": f"planner failed: {exc}",
                           "code": "planner_failed"}
    return result, 200, None


def _added_service_apply(router_id: int, service_key: str, inputs: dict):
    """Re-plan + push to the router. Returns Flask response."""
    from ..db.repos import nas_repo
    from ..services.npc_router_executor import (
        ExecutorNotConfigured, get_router_executor,
    )

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    plan_result, status, err = _plan_added_service(router_id, service_key, inputs)
    if err:
        return jsonify({"ok": False, **err}), status
    if plan_result.blocking_errors:
        return jsonify({
            "ok": False, "code": "planner_blocked",
            "blocking_errors": list(plan_result.blocking_errors),
        }), 409
    if not plan_result.script or not plan_result.script.strip():
        return _err("لا يوجد سكربت لإرساله — تحقّق من المدخلات.",
                    status=400, code="empty_script")
    try:
        exec_result = get_router_executor().execute_forward(
            router_id=router_id, script=plan_result.script,
        )
    except ExecutorNotConfigured:
        return _err(
            "وحدة تنفيذ السكربتات غير مُهيّأة على الخادم.",
            status=503, code="executor_not_configured",
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"خطأ غير متوقّع: {exc}",
                    status=500, code="apply_error")
    if not exec_result.ok:
        return jsonify({
            "ok": False, "code": "apply_failed",
            "error": exec_result.error_message or "تعذّر تنفيذ السكربت",
            "stderr": exec_result.stderr or "",
            "duration_ms": exec_result.duration_ms,
        }), 502
    return jsonify({
        "ok": True,
        "duration_ms": exec_result.duration_ms,
        "substeps": [
            {"key": "connect", "status": "done"},
            {"key": "send",    "status": "done"},
            {"key": "commit",  "status": "done"},
        ],
    })


def setup_wizard_v3_block_sites_preview(router_id: int):
    body = _body() or {}
    domains = list(body.get("domains") or [])
    if not domains:
        return _err("اكتب موقعاً واحداً على الأقل قبل المعاينة.",
                    status=400, code="no_domains")
    plan_result, status, err = _plan_added_service(
        router_id, "block_sites", {"domains": domains},
    )
    if err:
        return jsonify({"ok": False, **err}), status
    bullets = []
    notes = list(getattr(plan_result, "notes", ()) or ())
    if notes:
        bullets.extend(notes)
    else:
        bullets.append(f"سيتم إنشاء قائمة عناوين تحتوي {len(domains)} موقعاً.")
        bullets.append("سيُضاف قاعدة في جدار الحماية تمنع الحركة لهذه القائمة.")
        bullets.append("التغيير قابل للتراجع بإعادة تشغيل الخدمة بقائمة فارغة.")
    warnings = list(getattr(plan_result, "warnings", ()) or ())
    for w in warnings:
        bullets.append(f"⚠ تنبيه: {w}")
    if plan_result.blocking_errors:
        return jsonify({
            "ok": False, "code": "planner_blocked",
            "blocking_errors": list(plan_result.blocking_errors),
            "bullets": bullets,
        }), 409
    return jsonify({"ok": True, "bullets": bullets})


def setup_wizard_v3_block_sites_apply(router_id: int):
    body = _body() or {}
    return _added_service_apply(
        router_id, "block_sites",
        {"domains": list(body.get("domains") or [])},
    )


def setup_wizard_v3_block_sites_verify(router_id: int):
    """Phase 4 — confirm the address-list + filter rule are in place."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return _err("لا توجد كلمة مرور API لهذا الراوتر.",
                    status=409, code="no_api_password")
    checks = []
    try:
        from ..services.mikrotik_admin_client import MikrotikClient
    except Exception as exc:  # noqa: BLE001
        return _err(f"تعذّر تحميل عميل MikroTik: {exc}", status=500,
                    code="mt_client_load_error")
    cfg = {
        "host": nas.address, "username": nas.api_user or "admin",
        "password": nas.api_password, "port": int(nas.api_port or 8728),
        "use_tls": bool(nas.api_use_tls), "timeout": 8.0,
    }
    try:
        with MikrotikClient(**cfg) as mt:
            # Check 1: address-list entries created by this run.
            entries = list(mt.print_("/ip/firewall/address-list/print"))
            managed = [e for e in entries
                       if "HOBERADIUS_SETUP" in str(e.get("comment", ""))
                       and "block_sites" in str(e.get("comment", ""))]
            checks.append({
                "label": f"قائمة العناوين المحجوبة موجودة ({len(managed)} إدخالاً)",
                "status": "ok" if managed else "fail",
            })
            # Check 2: filter rule referencing the list.
            rules = list(mt.print_("/ip/firewall/filter/print"))
            blocked_rules = [r for r in rules
                             if str(r.get("action", "")).lower() == "drop"
                             and "HOBERADIUS_SETUP" in str(r.get("comment", ""))
                             and "block_sites" in str(r.get("comment", ""))]
            checks.append({
                "label": "قاعدة الحجب نشطة في جدار الحماية",
                "status": "ok" if blocked_rules else "fail",
            })
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False, "code": "probe_failed",
            "error": f"تعذّر الاتصال بالراوتر للفحص: {exc}",
            "checks": checks,
        }), 502
    all_ok = all(c["status"] == "ok" for c in checks)
    return jsonify({"ok": all_ok, "checks": checks})


def setup_wizard_v3_open_sites_preview(router_id: int):
    body = _body() or {}
    domains = list(body.get("domains") or [])
    if not domains:
        return _err("اكتب موقعاً واحداً على الأقل قبل المعاينة.",
                    status=400, code="no_domains")
    plan_result, status, err = _plan_added_service(
        router_id, "walled_garden", {"domains": domains},
    )
    if err:
        return jsonify({"ok": False, **err}), status
    bullets = []
    notes = list(getattr(plan_result, "notes", ()) or ())
    if notes:
        bullets.extend(notes)
    else:
        bullets.append(
            f"سيتم السماح بالوصول لـ {len(domains)} موقعاً قبل تسجيل الدخول."
        )
        bullets.append("المستخدمون يصلون لهذه المواقع مباشرة دون شاشة Hotspot.")
        bullets.append("التغيير قابل للتراجع بإفراغ القائمة وإعادة التطبيق.")
    warnings = list(getattr(plan_result, "warnings", ()) or ())
    for w in warnings:
        bullets.append(f"⚠ تنبيه: {w}")
    if plan_result.blocking_errors:
        return jsonify({
            "ok": False, "code": "planner_blocked",
            "blocking_errors": list(plan_result.blocking_errors),
            "bullets": bullets,
        }), 409
    return jsonify({"ok": True, "bullets": bullets})


def setup_wizard_v3_open_sites_apply(router_id: int):
    body = _body() or {}
    return _added_service_apply(
        router_id, "walled_garden",
        {"domains": list(body.get("domains") or [])},
    )


def setup_wizard_v3_open_sites_verify(router_id: int):
    """Phase 4 — confirm walled-garden entries are in place."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return _err("لا توجد كلمة مرور API لهذا الراوتر.",
                    status=409, code="no_api_password")
    checks = []
    try:
        from ..services.mikrotik_admin_client import MikrotikClient
    except Exception as exc:  # noqa: BLE001
        return _err(f"تعذّر تحميل عميل MikroTik: {exc}",
                    status=500, code="mt_client_load_error")
    cfg = {
        "host": nas.address, "username": nas.api_user or "admin",
        "password": nas.api_password, "port": int(nas.api_port or 8728),
        "use_tls": bool(nas.api_use_tls), "timeout": 8.0,
    }
    try:
        with MikrotikClient(**cfg) as mt:
            # Check: walled-garden host entries created by this run.
            entries = list(mt.print_("/ip/hotspot/walled-garden/print"))
            managed = [e for e in entries
                       if "HOBERADIUS_SETUP" in str(e.get("comment", ""))
                       and "walled_garden" in str(e.get("comment", ""))]
            checks.append({
                "label": f"إدخالات المواقع المسموحة موجودة ({len(managed)} موقعاً)",
                "status": "ok" if managed else "fail",
            })
            # Check: Hotspot must be active for walled-garden to take effect.
            servers = list(mt.print_("/ip/hotspot/server/print"))
            running = [s for s in servers
                       if str(s.get("disabled", "")).lower() in ("false", "no", "")]
            checks.append({
                "label": "Hotspot نشط (شرط لعمل القائمة)",
                "status": "ok" if running else "fail",
            })
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False, "code": "probe_failed",
            "error": f"تعذّر الاتصال بالراوتر للفحص: {exc}",
            "checks": checks,
        }), 502
    all_ok = all(c["status"] == "ok" for c in checks)
    return jsonify({"ok": all_ok, "checks": checks})


def setup_wizard_v3_router_exit_nodes(router_id: int):
    """List VPS exit nodes available to this tenant. Used by the
    Public-IP service partial to populate its node dropdown."""
    from ..db.repos import vps_exit_nodes_repo, nas_repo

    if not nas_repo.get_nas(_tid(), router_id):
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    try:
        rows = vps_exit_nodes_repo.list_for_tenant(_tid())
    except Exception as exc:  # noqa: BLE001
        return _err(f"تعذّر قراءة عقد الخروج: {exc}", status=500,
                    code="exit_nodes_load_error")
    # Normalise — only expose the fields the partial needs.
    nodes = [{
        "id": int(r.get("id") or 0),
        "name": str(r.get("name") or "—"),
        "public_ip": str(r.get("public_ip") or ""),
        "wireguard_interface_name": str(r.get("wireguard_interface_name") or ""),
    } for r in (rows or [])]
    return jsonify({"ok": True, "nodes": nodes})


def setup_wizard_v3_public_ip_preview(router_id: int):
    body = _body() or {}
    destinations = list(body.get("destinations") or [])
    exit_node_id = body.get("exit_node_id")
    if not exit_node_id:
        return _err("اختر عقدة خروج قبل المعاينة.",
                    status=400, code="no_exit_node")
    if not destinations:
        return _err("اكتب موقعاً واحداً على الأقل قبل المعاينة.",
                    status=400, code="no_destinations")
    plan_result, status, err = _plan_added_service(
        router_id, "site_exit_public_ip",
        {
            "destinations": destinations,
            "exit_node_id": int(exit_node_id),
            "wireguard_interface_name": str(body.get("wireguard_interface_name") or ""),
        },
    )
    if err:
        return jsonify({"ok": False, **err}), status
    bullets = []
    notes = list(getattr(plan_result, "notes", ()) or ())
    if notes:
        bullets.extend(notes)
    else:
        bullets.append(
            f"سيتم توجيه {len(destinations)} موقعاً عبر عقدة الخروج المختارة."
        )
        bullets.append("يتم إنشاء mangle rules + routing table مخصّصة لهذه المواقع.")
        bullets.append("باقي حركة الراوتر تبقى على المسار الافتراضي.")
    warnings = list(getattr(plan_result, "warnings", ()) or ())
    for w in warnings:
        bullets.append(f"⚠ تنبيه: {w}")
    if plan_result.blocking_errors:
        return jsonify({
            "ok": False, "code": "planner_blocked",
            "blocking_errors": list(plan_result.blocking_errors),
            "bullets": bullets,
        }), 409
    return jsonify({"ok": True, "bullets": bullets})


def setup_wizard_v3_public_ip_apply(router_id: int):
    body = _body() or {}
    return _added_service_apply(
        router_id, "site_exit_public_ip",
        {
            "destinations": list(body.get("destinations") or []),
            "exit_node_id": int(body.get("exit_node_id") or 0),
            "wireguard_interface_name": str(
                body.get("wireguard_interface_name") or ""
            ),
        },
    )


def setup_wizard_v3_public_ip_verify(router_id: int):
    """Phase 4 — confirm routing + mangle rules are in place on the router."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return _err("لا توجد كلمة مرور API لهذا الراوتر.",
                    status=409, code="no_api_password")
    checks = []
    try:
        from ..services.mikrotik_admin_client import MikrotikClient
    except Exception as exc:  # noqa: BLE001
        return _err(f"تعذّر تحميل عميل MikroTik: {exc}", status=500,
                    code="mt_client_load_error")
    cfg = {
        "host": nas.address, "username": nas.api_user or "admin",
        "password": nas.api_password, "port": int(nas.api_port or 8728),
        "use_tls": bool(nas.api_use_tls), "timeout": 8.0,
    }
    try:
        with MikrotikClient(**cfg) as mt:
            # Check 1: mangle rules tagged by this service.
            mangle = list(mt.print_("/ip/firewall/mangle/print"))
            managed_mangle = [m for m in mangle
                              if "HOBERADIUS_SETUP" in str(m.get("comment", ""))
                              and "site_exit" in str(m.get("comment", ""))]
            checks.append({
                "label": f"قواعد التوجيه (mangle) موجودة ({len(managed_mangle)})",
                "status": "ok" if managed_mangle else "fail",
            })
            # Check 2: routing table entry for the exit node.
            routes = list(mt.print_("/ip/route/print"))
            managed_routes = [r for r in routes
                              if "HOBERADIUS_SETUP" in str(r.get("comment", ""))
                              and "site_exit" in str(r.get("comment", ""))]
            checks.append({
                "label": "مسار التوجيه عبر العقدة موجود",
                "status": "ok" if managed_routes else "fail",
            })
            # Check 3: address-list with destinations.
            entries = list(mt.print_("/ip/firewall/address-list/print"))
            managed_entries = [e for e in entries
                               if "HOBERADIUS_SETUP" in str(e.get("comment", ""))
                               and "site_exit" in str(e.get("comment", ""))]
            checks.append({
                "label": f"قائمة المواقع المختارة محمَّلة ({len(managed_entries)})",
                "status": "ok" if managed_entries else "fail",
            })
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False, "code": "probe_failed",
            "error": f"تعذّر الاتصال بالراوتر للفحص: {exc}",
            "checks": checks,
        }), 502
    all_ok = all(c["status"] == "ok" for c in checks)
    return jsonify({"ok": all_ok, "checks": checks})


def _remote_access_build_script(*, services: list, ttl_hours: int,
                                source_ip: str, grant_token: str) -> str:
    """Build a small idempotent RouterOS v7 script that:
      1. Removes any prior rules carrying the same HOBERADIUS_TECH
         comment tag (idempotent re-apply).
      2. Adds /ip firewall filter accept rules for each requested
         service port (Winbox/SSH/WebFig/API), constrained to the
         source IP if provided.
      3. Adds /system scheduler entry that auto-removes those
         rules + itself after ttl_hours.
    Lightweight on purpose — does not write to nas_devices or any
    DB table. The grant token in the comments is how the verify
    + revoke endpoints find the rules later.
    """
    tag = f"HOBERADIUS_TECH:{grant_token}"
    sched_name = f"hr-tech-revoke-{grant_token}"
    lines = [
        f"# Remote tech access — auto-expires in {ttl_hours}h",
        "/ip firewall filter",
        f':foreach r in=[find comment~"{tag}"] do={{remove $r}}',
    ]
    src_clause = f"src-address={source_ip}" if source_ip else ""
    for svc in services or []:
        port = int(svc.get("port", 0) or 0)
        if not port:
            continue
        name = str(svc.get("name", "?"))
        proto = "tcp"
        parts = [
            "add", "chain=input", f"protocol={proto}", f"dst-port={port}",
            "action=accept", f'comment="{tag}:{name}"',
            "place-before=0",
        ]
        if src_clause:
            parts.insert(4, src_clause)
        lines.append(" ".join(parts))
    # Scheduler — fires after ttl_hours, removes rules + itself.
    on_event = (
        f'/ip firewall filter remove [find comment~"{tag}"]; '
        f'/system scheduler remove [find name="{sched_name}"]'
    )
    lines += [
        "/system scheduler",
        f':foreach s in=[find name="{sched_name}"] do={{remove $s}}',
        (
            f'add name="{sched_name}" '
            f'interval=0s start-time=startup '
            f'on-event=":delay {int(ttl_hours)}h; {on_event}" '
            f'comment="{tag}:auto-revoke"'
        ),
    ]
    return "\n".join(lines) + "\n"


def setup_wizard_v3_remote_access_preview(router_id: int):
    body = _body() or {}
    services = list(body.get("services") or [])
    ttl_hours = int(body.get("ttl_hours") or 4)
    source_ip = str(body.get("source_ip") or "").strip()
    if not services:
        return _err("اختر خدمة واحدة على الأقل.",
                    status=400, code="no_services")
    if ttl_hours < 1 or ttl_hours > 168:  # cap at one week
        return _err("المدّة يجب أن تكون بين ساعة و 7 أيام.",
                    status=400, code="bad_ttl")
    svc_names_ar = {
        "winbox": "Winbox (8291)",
        "ssh": "SSH (22)",
        "webfig": "WebFig (80)",
        "api": "API (8728)",
    }
    enabled = [svc_names_ar.get(s.get("name", "?"), s.get("name", "?"))
               for s in services]
    bullets = [
        f"سيُسمح بالاتصال على: {' • '.join(enabled)}.",
        (f"من IP: {source_ip}" if source_ip
         else "من أي IP (الأفضل تحديد IP الفنّي)."),
        f"المدّة: {ttl_hours} ساعة — تُحذف القواعد تلقائيّاً عند انتهائها.",
        "يمكن إلغاء الوصول يدويّاً في أي وقت من بطاقة «اتصال عن بُعد».",
    ]
    return jsonify({"ok": True, "bullets": bullets})


def setup_wizard_v3_remote_access_apply(router_id: int):
    from ..db.repos import nas_repo
    from ..services.npc_router_executor import (
        ExecutorNotConfigured, get_router_executor,
    )

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    body = _body() or {}
    services = list(body.get("services") or [])
    ttl_hours = int(body.get("ttl_hours") or 4)
    source_ip = str(body.get("source_ip") or "").strip()
    grant_token = str(body.get("grant_token") or "").strip()
    if not services:
        return _err("اختر خدمة واحدة على الأقل.",
                    status=400, code="no_services")
    if not grant_token:
        return _err("معرّف القاعدة غير صالح.",
                    status=400, code="bad_token")
    script = _remote_access_build_script(
        services=services, ttl_hours=ttl_hours,
        source_ip=source_ip, grant_token=grant_token,
    )
    try:
        exec_result = get_router_executor().execute_forward(
            router_id=router_id, script=script,
        )
    except ExecutorNotConfigured:
        return _err("وحدة تنفيذ السكربتات غير مُهيّأة على الخادم.",
                    status=503, code="executor_not_configured")
    except Exception as exc:  # noqa: BLE001
        return _err(f"خطأ غير متوقّع: {exc}",
                    status=500, code="apply_error")
    if not exec_result.ok:
        return jsonify({
            "ok": False, "code": "apply_failed",
            "error": exec_result.error_message or "تعذّر تنفيذ السكربت",
            "stderr": exec_result.stderr or "",
            "duration_ms": exec_result.duration_ms,
        }), 502
    return jsonify({
        "ok": True,
        "duration_ms": exec_result.duration_ms,
        "grant_token": grant_token,
        "substeps": [
            {"key": "connect", "status": "done"},
            {"key": "send",    "status": "done"},
            {"key": "commit",  "status": "done"},
        ],
    })


def setup_wizard_v3_remote_access_verify(router_id: int):
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return _err("لا توجد كلمة مرور API لهذا الراوتر.",
                    status=409, code="no_api_password")
    token = str(request.args.get("token") or "").strip()
    if not token:
        return _err("معرّف القاعدة مفقود.", status=400, code="missing_token")
    checks = []
    try:
        from ..services.mikrotik_admin_client import MikrotikClient
    except Exception as exc:  # noqa: BLE001
        return _err(f"تعذّر تحميل عميل MikroTik: {exc}", status=500,
                    code="mt_client_load_error")
    cfg = {
        "host": nas.address, "username": nas.api_user or "admin",
        "password": nas.api_password, "port": int(nas.api_port or 8728),
        "use_tls": bool(nas.api_use_tls), "timeout": 8.0,
    }
    tag = f"HOBERADIUS_TECH:{token}"
    try:
        with MikrotikClient(**cfg) as mt:
            # Check 1: at least one firewall rule with our token.
            rules = list(mt.print_("/ip/firewall/filter/print"))
            mine = [r for r in rules if tag in str(r.get("comment", ""))]
            checks.append({
                "label": f"قاعدة الإتاحة نشطة ({len(mine)} قاعدة)",
                "status": "ok" if mine else "fail",
            })
            # Check 2: scheduler entry exists for auto-revoke.
            scheds = list(mt.print_("/system/scheduler/print"))
            mine_s = [s for s in scheds if tag in str(s.get("comment", ""))]
            checks.append({
                "label": "مؤقّت الإلغاء التلقائي مضبوط",
                "status": "ok" if mine_s else "fail",
            })
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False, "code": "probe_failed",
            "error": f"تعذّر الاتصال بالراوتر للفحص: {exc}",
            "checks": checks,
        }), 502
    all_ok = all(c["status"] == "ok" for c in checks)
    return jsonify({"ok": all_ok, "checks": checks})


def setup_wizard_v3_router_service_flow(router_id: int, service_key: str):
    """Per-service phased flow.

    Renders the SAME shell template for every service — what differs
    is the `service_config_partial` Jinja path injected per service.
    The shell drives the 4 phases (تهيئة → معاينة → إرسال → تحقّق);
    each per-service partial fills only the configure form for phase 1
    and dispatches a `swsvf:configure-submit` event for the JS state
    machine to take over.

    In this commit only the shell + stepper navigation are live. The
    per-service partials are stubbed (template lookup falls back to a
    «not wired yet» message). Commits 3-8 each plug one service in.
    """
    from ..db.repos import nas_repo

    router = nas_repo.get_nas(_tid(), router_id)
    card = next(
        (c for c in ROUTER_SERVICE_CARDS if c["key"] == service_key),
        None,
    )
    if not card:
        return _err(
            f"خدمة غير معروفة: {service_key}",
            status=404, code="unknown_service",
        )
    # Per-service configure form. Each commit that wires a service
    # adds a partial under templates/radius/svc_partials/. Missing
    # partials fall back to the shell's «not wired yet» message via
    # the {% include ... ignore missing %} clause.
    partial = f"radius/svc_partials/{service_key}.html"
    return render_template(
        "radius/setup_wizard_v3_router_service_flow.html",
        router=router,
        router_id=router_id,
        service_key=service_key,
        card=card,
        service_config_partial=partial,
        page_title=f"{card['title_ar']} — {router.name if router else ''}",
    )


def setup_wizard_v3_create_run():
    try:
        run = _svc().start_new_run(
            tenant_id=_tid(), actor=_actor(),
        )
    except V3Error as exc:
        return _err(str(exc))
    return jsonify({"ok": True, "run": run.to_dict()})


def setup_wizard_v3_get_state(run_id: int):
    try:
        run = _svc().get_state(
            tenant_id=_tid(), run_id=run_id,
        )
    except V3NotFound as exc:
        return _err(str(exc), status=404, code="not_found")
    except V3Error as exc:
        return _err(str(exc))
    return jsonify({"ok": True, "run": run.to_dict()})


def setup_wizard_v3_router_info(run_id: int):
    body = _body()
    try:
        run = _svc().submit_router_info(
            tenant_id=_tid(), run_id=run_id,
            router_name=str(body.get("router_name") or ""),
            router_type=str(body.get("router_type") or "hotspot"),
        )
    except V3InvalidState as exc:
        return _err(str(exc), status=409, code="invalid_state")
    except V3Error as exc:
        return _err(str(exc))
    return jsonify({"ok": True, "run": run.to_dict()})


def setup_wizard_v3_generate_script(run_id: int):
    body = _body()
    endpoint = (
        body.get("vps_public_endpoint")
        or os.environ.get("HOBERADIUS_PUBLIC_HOST")
        or os.environ.get("HOBERADIUS_WG_SERVER_ENDPOINT", "").split(":")[0]
        or (request.host.split(":")[0] if request else "")
    )
    pubkey = (
        body.get("vps_wg_pubkey")
        or os.environ.get("HOBERADIUS_WG_SERVER_PUBKEY")
        or ""
    )
    if not endpoint:
        return _err(
            "vps_public_endpoint غير معروف. اضبط "
            "HOBERADIUS_PUBLIC_HOST في بيئة الخادم.",
            code="missing_endpoint",
        )
    if not pubkey:
        return _err(
            "مفتاح WireGuard العام للخادم غير معروف. اضبط "
            "HOBERADIUS_WG_SERVER_PUBKEY في بيئة الخادم.",
            code="missing_server_pubkey",
        )
    try:
        result = _svc().generate_unified_script(
            tenant_id=_tid(), run_id=run_id,
            vps_public_endpoint=str(endpoint),
            vps_wg_pubkey=str(pubkey),
        )
    except V3InvalidState as exc:
        return _err(str(exc), status=409, code="invalid_state")
    except V3Error as exc:
        return _err(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_v3_submit_key(run_id: int):
    body = _body()
    try:
        run = _svc().submit_router_public_key(
            tenant_id=_tid(), run_id=run_id,
            pasted_or_key=str(body.get("pasted_output")
                              or body.get("public_key")
                              or ""),
        )
    except V3InvalidState as exc:
        return _err(str(exc), status=409, code="invalid_state")
    except V3Error as exc:
        return _err(str(exc))
    return jsonify({"ok": True, "run": run.to_dict()})


def setup_wizard_v3_apply_peer(run_id: int):
    try:
        run = _svc().apply_server_peer(
            tenant_id=_tid(), run_id=run_id,
        )
    except V3InvalidState as exc:
        return _err(str(exc), status=409, code="invalid_state")
    except V3Error as exc:
        return _err(str(exc))
    return jsonify({"ok": True, "run": run.to_dict()})


def setup_wizard_v3_mark_handshake(run_id: int):
    try:
        run = _svc().mark_handshake_observed(
            tenant_id=_tid(), run_id=run_id,
        )
    except V3InvalidState as exc:
        return _err(str(exc), status=409, code="invalid_state")
    except V3Error as exc:
        return _err(str(exc))
    return jsonify({"ok": True, "run": run.to_dict()})


def setup_wizard_v3_force_register(run_id: int):
    """Recovery: manually re-run the register step on a run
    that's stuck in BLOCKED with MISSING_REGISTRATION_INPUT.

    Reads router_name + router_vpn_ip from state_json. If
    they exist, transitions the run to REGISTERING and calls
    register_router_in_inventory directly. Useful when a v3
    run advanced state but lost state_json entries due to a
    transient bug or operator skipping ahead.

    Only intended for support — not a regular flow."""
    body = _body()
    api_user = str(body.get("api_user") or "admin")
    api_password = str(body.get("api_password") or "")
    try:
        # First, unblock the state machine.
        _svc()._repo.update_state(
            tenant_id=_tid(), run_id=run_id,
            state="REGISTERING",
        )
        run = _svc().register_router_in_inventory(
            tenant_id=_tid(), run_id=run_id,
            api_user=api_user,
            api_password=api_password,
        )
    except V3InvalidState as exc:
        return _err(str(exc), status=409, code="invalid_state")
    except V3Error as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"force register failed: {exc}",
            status=500,
            code="force_register_failed",
        )
    return jsonify({"ok": True, "run": run.to_dict()})


def setup_wizard_v3_register(run_id: int):
    body = _body()
    try:
        run = _svc().register_router_in_inventory(
            tenant_id=_tid(), run_id=run_id,
            api_user=str(body.get("api_user") or "admin"),
            api_password=str(body.get("api_password") or ""),
        )
    except V3InvalidState as exc:
        return _err(str(exc), status=409, code="invalid_state")
    except V3Error as exc:
        return _err(str(exc))
    return jsonify({"ok": True, "run": run.to_dict()})


def setup_wizard_v3_serve_script(short_code: str):
    """Public endpoint the router fetches via /tool fetch.
    Auth = the secret short code in the URL path."""
    rec = _svc()._repo.get_unified_script_by_code(short_code)
    if not rec:
        return Response("not found\n", status=404,
                        mimetype="text/plain")
    _svc()._repo.mark_script_fetched(
        short_code=short_code,
        user_agent=str(request.headers.get("User-Agent", "")),
        remote_addr=str(request.remote_addr or ""),
    )
    return Response(
        rec["script_body"],
        status=200,
        mimetype="text/plain; charset=utf-8",
    )


# ─── SW7: phase planner endpoints ───────────────────────────


def setup_wizard_v3_phase_planners_index():
    """List the phases the operator can plan, with sample input
    schemas so the UI can render dynamic forms."""
    return jsonify({
        "ok": True,
        "phases": [
            {
                "phase": "internet",
                "title_ar": "وصلة الإنترنت (uplink)",
                "description_ar": (
                    "VLAN / IP ثابت / DHCP / PPPoE"
                ),
                "required_inputs": ["source_type"],
            },
            {
                "phase": "vpn_radius",
                "title_ar": "VPN + RADIUS",
                "description_ar": (
                    "إنشاء واجهة WireGuard + ربط RADIUS"
                ),
                "required_inputs": [
                    "router_vpn_ip", "vps_vpn_ip",
                    "vps_public_endpoint", "radius_secret",
                    "server_public_key",
                ],
            },
            {
                "phase": "hotspot",
                "title_ar": "Hotspot (وصول عام)",
                "description_ar": (
                    "خادم Hotspot + DHCP + RADIUS authentication"
                ),
                "required_inputs": [
                    "selected_interfaces", "subnet_base",
                    "radius_secret", "router_vpn_ip",
                ],
            },
            {
                "phase": "broadband",
                "title_ar": "Broadband / PPPoE server",
                "description_ar": (
                    "خادم PPPoE + IP pool + NAT مقيّد"
                ),
                "required_inputs": [
                    "selected_interfaces", "local_address",
                    "remote_pool_cidr",
                ],
            },
            {
                "phase": "added_services",
                "title_ar": "خدمات إضافية",
                "description_ar": (
                    "walled garden / حجب مواقع / Site exit"
                ),
                "required_inputs": ["service_key"],
            },
        ],
    })


def setup_wizard_v3_phase_plan(run_id: int, phase: str):
    """Run the SW1-SW6 phase planner for the given phase.
    Returns a PhasePlanResult — script + warnings + Arabic
    notes + diagnostic codes + tags.

    The wizard run must exist (so we have a real run_id to tag
    the script with), but the planner itself is pure and never
    touches the DB or the router."""
    cls = PHASE_PLANNERS.get(str(phase or "").strip().lower())
    if not cls:
        return _err(
            f"unknown phase '{phase}' (allowed: "
            + ", ".join(PHASE_PLANNERS.keys()) + ")",
            status=400,
            code="unknown_phase",
        )
    # Validate the run exists so the operator can't generate a
    # script for a non-existent run.
    try:
        run = _svc().get_state(tenant_id=_tid(), run_id=run_id)
    except V3NotFound as exc:
        return _err(str(exc), status=404, code="not_found")
    except V3Error as exc:
        return _err(str(exc))

    body = _body() or {}
    # Allow either flat (inputs at the top level) or nested
    # ({"inputs": {...}, "service_key": "...", ...}). When both
    # are present, merge so top-level keys like service_key get
    # passed through to the planner alongside the inputs dict.
    if isinstance(body.get("inputs"), dict):
        inputs = dict(body["inputs"])
        for k, v in body.items():
            if k != "inputs":
                inputs.setdefault(k, v)
    else:
        inputs = dict(body)
    try:
        planner = cls()
        result = planner.plan(
            run_id=int(run.id),
            inputs=inputs,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"planner failed: {exc}",
            status=500,
            code="planner_failed",
        )

    # Decorate blockers with Arabic explanations from the
    # diagnostics catalogue so the UI can render them directly.
    diagnostics = []
    if result.blocking_errors:
        from ..services import setup_wizard_diagnostics as _d
        for code in result.blocking_errors:
            try:
                diag = _d.get(code)
                diagnostics.append({
                    "code": code,
                    "ar_explanation": diag.ar_explanation,
                    "cause": diag.cause,
                    "fix": diag.fix,
                    "severity": diag.severity,
                    "inspect_command": diag.inspect_command,
                })
            except KeyError:
                diagnostics.append({
                    "code": code,
                    "ar_explanation": (
                        "خطأ تشخيصي غير معروف."
                    ),
                    "severity": "error",
                })

    return jsonify({
        "ok": True,
        "phase": phase,
        "plan": result.to_dict(),
        "diagnostics": diagnostics,
    })


def setup_wizard_v3_handshake_status(run_id: int):
    """Probe the router via the WireGuard tunnel. Returns
    `tunnel_up: true` only when the API port is actually
    reachable — meaning the handshake really completed and
    the operator can advance from Step 4 safely."""
    from ..services.setup_wizard_v3_handshake_probe import (
        probe_tunnel_alive,
    )

    try:
        run = _svc().get_state(tenant_id=_tid(), run_id=run_id)
    except V3NotFound as exc:
        return _err(str(exc), status=404, code="not_found")
    except V3Error as exc:
        return _err(str(exc))

    result = probe_tunnel_alive(run.router_vpn_ip or "")

    # When the tunnel becomes alive for the first time, advance
    # the state machine — same path as the manual 'confirm'
    # button used to take. Safe to call repeatedly: the v3
    # service's mark_handshake_observed() is idempotent across
    # AWAITING_HANDSHAKE → APPLYING_SERVER_PEER → VERIFYING.
    if result.get("tunnel_up"):
        try:
            _svc().mark_handshake_observed(
                tenant_id=_tid(), run_id=run_id,
            )
        except V3InvalidState:
            # Already advanced — fine.
            pass
        except V3Error:
            # Don't fail the probe response on this — the
            # tunnel IS up, that's the headline info.
            pass

    return jsonify({"ok": True, **result})


def setup_wizard_v3_configure_server_radius(run_id: int):
    """One-click: write the FreeRADIUS clients.conf snippet for
    this wizard run + trigger a server-side reload. Replaces
    the manual SSH + edit + restart flow shown to the operator
    in the RADIUS-secret card."""
    from ..services.setup_wizard_v3_radius_server_provisioning import (
        FreeRadiusProvisioningError,
        write_client_for_run,
    )

    try:
        run = _svc().get_state(tenant_id=_tid(), run_id=run_id)
    except V3NotFound as exc:
        return _err(str(exc), status=404, code="not_found")
    except V3Error as exc:
        return _err(str(exc))

    if not run.router_vpn_ip:
        return _err(
            "أكمل الخطوة 3 (الربط بالخادم) أوّلاً.",
            status=409,
            code="no_vpn_ip",
        )

    # Secret is stashed in state_json by generate_unified_script.
    raw_state = _svc()._repo._raw_state_json(_tid(), run_id)
    secret = str(raw_state.get("radius_secret") or "").strip()
    if not secret:
        return _err(
            "لم يتم توليد سرّ RADIUS بعد. ارجع للخطوة 3 "
            "واضغط (توليد سكربت الربط).",
            status=409,
            code="no_secret",
        )

    try:
        result = write_client_for_run(
            run_id=run_id,
            router_vpn_ip=run.router_vpn_ip,
            radius_secret=secret,
            shortname=f"wizard-{run.router_name or run_id}",
        )
    except FreeRadiusProvisioningError as exc:
        return _err(
            str(exc),
            status=500,
            code="freeradius_provisioning_failed",
        )
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"خطأ غير متوقّع: {exc}",
            status=500,
            code="freeradius_provisioning_error",
        )
    return jsonify({"ok": True, **result})


def setup_wizard_v3_discover_interfaces(run_id: int):
    """Discover the router's actual interfaces — either via the
    API over the established VPN tunnel, or by parsing pasted
    `/interface print` output. Returns a normalised list the
    Hotspot card renders as checkboxes."""
    from ..services.setup_wizard_v3_interface_discovery import (
        InterfaceDiscoveryError,
        discover_via_api,
        discover_via_paste,
    )

    body = _body() or {}
    mode = str(body.get("mode") or "").strip().lower()
    blocked = [
        str(x).strip() for x in (body.get("blocked_interfaces") or [])
        if str(x).strip()
    ]
    try:
        run = _svc().get_state(tenant_id=_tid(), run_id=run_id)
    except V3NotFound as exc:
        return _err(str(exc), status=404, code="not_found")
    except V3Error as exc:
        return _err(str(exc))

    try:
        if mode == "paste":
            ifaces = discover_via_paste(
                pasted_output=str(body.get("pasted_output") or ""),
            )
        else:
            # API mode (default) — needs credentials + the
            # already-allocated router VPN IP from the run.
            if not run.router_vpn_ip:
                return _err(
                    "أكمل الخطوة 3 أوّلاً — لم يُخصَّص "
                    "عنوان VPN للراوتر بعد.",
                    status=409,
                    code="no_vpn_ip",
                )
            ifaces = discover_via_api(
                router_vpn_ip=run.router_vpn_ip,
                api_user=str(body.get("api_user") or "admin"),
                api_password=str(body.get("api_password") or ""),
            )
    except InterfaceDiscoveryError as exc:
        return _err(str(exc), status=400, code="discovery_failed")
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"فشل اكتشاف المنافذ: {exc}",
            status=500,
            code="discovery_error",
        )

    # Filter out the operator-supplied blocked interfaces
    # (usually the WAN port chosen in Step 2) so they can never
    # be picked for hotspot/broadband — protects internet uplink
    # from being torn down by a downstream phase script.
    if blocked:
        blocked_set = {b for b in blocked}
        ifaces = [
            i for i in ifaces if i.get("name") not in blocked_set
        ]

    return jsonify({
        "ok": True,
        "mode": mode or "api",
        "interfaces": ifaces,
        "blocked_interfaces": blocked,
        "router_vpn_ip": run.router_vpn_ip,
    })


def setup_wizard_v3_diagnostics_catalogue():
    """Expose the full diagnostics catalogue for the UI to
    render lookup tables and tooltips."""
    from ..services import setup_wizard_diagnostics as _d
    out = []
    for code in _d.all_codes():
        diag = _d.get(code)
        out.append({
            "code": diag.code,
            "phase": diag.phase,
            "ar_explanation": diag.ar_explanation,
            "cause": diag.cause,
            "fix": diag.fix,
            "severity": diag.severity,
            "inspect_command": diag.inspect_command,
        })
    return jsonify({"ok": True, "catalogue": out})


# ─── Registration ───────────────────────────────────────────


def register_setup_wizard_v3_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/setup-wizard-v3",
        "setup_wizard_v3_page",
        setup_wizard_v3_page,
        methods=["GET"],
    )
    # Router Services Dashboard — opens AFTER base wizard ends.
    # Each card on the dashboard links to a per-service phased flow.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services",
        "setup_wizard_v3_router_services_dashboard",
        setup_wizard_v3_router_services_dashboard,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/<service_key>",
        "setup_wizard_v3_router_service_flow",
        setup_wizard_v3_router_service_flow,
        methods=["GET"],
    )
    # Per-router interface discovery (shared by Hotspot + Broadband
    # configure forms — operator picks from a real list).
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/discover-interfaces",
        "setup_wizard_v3_router_discover_interfaces",
        setup_wizard_v3_router_discover_interfaces,
        methods=["POST"],
    )
    # Hotspot service flow endpoints (Phase 2/3/4 of the shell).
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/hotspot/preview",
        "setup_wizard_v3_hotspot_preview",
        setup_wizard_v3_hotspot_preview,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/hotspot/apply",
        "setup_wizard_v3_hotspot_apply",
        setup_wizard_v3_hotspot_apply,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/hotspot/verify",
        "setup_wizard_v3_hotspot_verify",
        setup_wizard_v3_hotspot_verify,
        methods=["GET"],
    )
    # Broadband flow endpoints.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/broadband/preview",
        "setup_wizard_v3_broadband_preview",
        setup_wizard_v3_broadband_preview,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/broadband/apply",
        "setup_wizard_v3_broadband_apply",
        setup_wizard_v3_broadband_apply,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/broadband/verify",
        "setup_wizard_v3_broadband_verify",
        setup_wizard_v3_broadband_verify,
        methods=["GET"],
    )
    # Block-sites flow endpoints.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/block-sites/preview",
        "setup_wizard_v3_block_sites_preview",
        setup_wizard_v3_block_sites_preview,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/block-sites/apply",
        "setup_wizard_v3_block_sites_apply",
        setup_wizard_v3_block_sites_apply,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/block-sites/verify",
        "setup_wizard_v3_block_sites_verify",
        setup_wizard_v3_block_sites_verify,
        methods=["GET"],
    )
    # Open-sites (walled garden) flow endpoints.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/open-sites/preview",
        "setup_wizard_v3_open_sites_preview",
        setup_wizard_v3_open_sites_preview,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/open-sites/apply",
        "setup_wizard_v3_open_sites_apply",
        setup_wizard_v3_open_sites_apply,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/open-sites/verify",
        "setup_wizard_v3_open_sites_verify",
        setup_wizard_v3_open_sites_verify,
        methods=["GET"],
    )
    # Public-IP (site exit) — helper to list exit nodes + 3 flow endpoints.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/exit-nodes",
        "setup_wizard_v3_router_exit_nodes",
        setup_wizard_v3_router_exit_nodes,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/public-ip/preview",
        "setup_wizard_v3_public_ip_preview",
        setup_wizard_v3_public_ip_preview,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/public-ip/apply",
        "setup_wizard_v3_public_ip_apply",
        setup_wizard_v3_public_ip_apply,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/public-ip/verify",
        "setup_wizard_v3_public_ip_verify",
        setup_wizard_v3_public_ip_verify,
        methods=["GET"],
    )
    # Remote-access (temp tech firewall opening) flow endpoints.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/remote-access/preview",
        "setup_wizard_v3_remote_access_preview",
        setup_wizard_v3_remote_access_preview,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/remote-access/apply",
        "setup_wizard_v3_remote_access_apply",
        setup_wizard_v3_remote_access_apply,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/remote-access/verify",
        "setup_wizard_v3_remote_access_verify",
        setup_wizard_v3_remote_access_verify,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs",
        "setup_wizard_v3_create_run",
        setup_wizard_v3_create_run,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/state",
        "setup_wizard_v3_get_state",
        setup_wizard_v3_get_state,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/router-info",
        "setup_wizard_v3_router_info",
        setup_wizard_v3_router_info,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/generate-script",
        "setup_wizard_v3_generate_script",
        setup_wizard_v3_generate_script,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/submit-key",
        "setup_wizard_v3_submit_key",
        setup_wizard_v3_submit_key,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/apply-server-peer",
        "setup_wizard_v3_apply_peer",
        setup_wizard_v3_apply_peer,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/mark-handshake",
        "setup_wizard_v3_mark_handshake",
        setup_wizard_v3_mark_handshake,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/register",
        "setup_wizard_v3_register",
        setup_wizard_v3_register,
        methods=["POST"],
    )
    # The script-delivery endpoint. NOT auth-gated (the secret
    # short code IS the auth). Path lives at the blueprint root
    # so a MikroTik /tool fetch can hit it without admin login.
    bp.add_url_rule(
        "/wz/<short_code>.rsc",
        "setup_wizard_v3_serve_script",
        setup_wizard_v3_serve_script,
        methods=["GET"],
    )
    # SW7 — phase planner integration endpoints.
    bp.add_url_rule(
        "/setup-wizard-v3/phase-planners",
        "setup_wizard_v3_phase_planners_index",
        setup_wizard_v3_phase_planners_index,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/phase-plan/<phase>",
        "setup_wizard_v3_phase_plan",
        setup_wizard_v3_phase_plan,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/force-register",
        "setup_wizard_v3_force_register",
        setup_wizard_v3_force_register,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/handshake-status",
        "setup_wizard_v3_handshake_status",
        setup_wizard_v3_handshake_status,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/configure-server-radius",
        "setup_wizard_v3_configure_server_radius",
        setup_wizard_v3_configure_server_radius,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/runs/<int:run_id>/discover-interfaces",
        "setup_wizard_v3_discover_interfaces",
        setup_wizard_v3_discover_interfaces,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/diagnostics-catalogue",
        "setup_wizard_v3_diagnostics_catalogue",
        setup_wizard_v3_diagnostics_catalogue,
        methods=["GET"],
    )
