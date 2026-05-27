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


def _infer_fail_stage(exec_result) -> str:
    """Classify a failed ExecutionResult into one of the three
    progress stages the UI shows: connect / send / commit.

    The router executor returns a single ``ok=False`` regardless of
    whether the failure was at the TCP/auth layer, refusing-to-send
    layer, or a RouterOS trap from the script body itself. The UI
    pretends these are separate substeps for the operator's mental
    model, so we infer the stage from the error text rather than
    marking all three as failed at once (which is misleading)."""
    text = " ".join([
        (exec_result.error_message or ""),
        (exec_result.stderr or ""),
    ]).lower()

    # ── stage 1: connect ── (couldn't even reach the router)
    connect_markers = (
        "not found in nas_devices",
        "no api credentials",
        "connecterror",
        "autherror",
        "timed out",
        "timeout",
        "connection refused",
        "unreachable",
        "no route to host",
        "name or service not known",
        "executor not configured",
        "executornotconfigured",
        "network is unreachable",
    )
    if any(m in text for m in connect_markers):
        return "connect"

    # ── stage 2: send ── (connected but refused to ship the script)
    send_markers = (
        "refusing to execute empty",
        "exceeds max size",
        "script create rejected",
        "refusing to send",
    )
    if any(m in text for m in send_markers):
        return "send"

    # ── stage 3: commit ── (script reached RouterOS and was rejected)
    # Default — this is the «trap» case (expected end of command,
    # no such item, invalid value, etc.). Most failures land here.
    return "commit"


def _build_fail_substeps(fail_stage: str) -> list[dict]:
    """Return the substeps list for the JSON apply-failure response.
    Stages before ``fail_stage`` are marked done, ``fail_stage`` is
    marked failed, and any stage after it stays pending so the UI
    can render «−» instead of «✗» for steps we never attempted."""
    order = ("connect", "send", "commit")
    out = []
    seen_fail = False
    for key in order:
        if key == fail_stage:
            status = "failed"
            seen_fail = True
        elif seen_fail:
            status = "pending"
        else:
            status = "done"
        out.append({"key": key, "status": status})
    return out


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
    # Detect WAN-side interface(s): look up the default route and
    # the WireGuard tunnel interface, and mark them in the response
    # so the operator can't accidentally turn them into a Hotspot
    # uplink or PPPoE server (which would brick the router's own
    # internet access). Soft-fail — if the probe doesn't work the
    # interfaces still load, just without is_wan flags.
    wan_names: set[str] = set()
    try:
        from ..integration.mikrotik import MikrotikClient
        cfg = {
            "host": nas.address,
            "username": nas.api_user or "admin",
            "password": nas.api_password,
            "port": int(nas.api_port or 8728),
            "use_tls": bool(nas.api_use_tls),
            "timeout": 6.0,
        }
        with MikrotikClient(**cfg) as mt:
            # 1. Default-route gateway interface (typical WAN).
            #    The `gateway` field is either an IP, an iface name,
            #    or "IP%iface" format depending on RouterOS version.
            routes = list(mt.print_("/ip/route/print"))
            for r in routes:
                if str(r.get("dst-address", "")) != "0.0.0.0/0":
                    continue
                if str(r.get("active", "")).lower() not in ("true", "yes"):
                    continue
                gw = str(r.get("gateway", "") or "")
                # "1.2.3.4%ether1" → "ether1"
                if "%" in gw:
                    wan_names.add(gw.split("%")[-1])
                # Pure interface name (no IP, no %).
                elif gw and "." not in gw and ":" not in gw:
                    wan_names.add(gw)
            # 2. Every WireGuard interface — it carries the VPN
            #    tunnel back to the HobeRadius VPS and must never
            #    be repurposed as LAN.
            try:
                wg_ifaces = list(mt.print_("/interface/wireguard/print"))
                for w in wg_ifaces:
                    name = str(w.get("name", "") or "").strip()
                    if name:
                        wan_names.add(name)
            except Exception:  # noqa: BLE001
                pass  # WireGuard package may not be installed
            # 3. Trace logical WAN interfaces (PPPoE client, VLAN,
            #    L2TP/PPP, EoIP, IPIP, GRE …) back to the PHYSICAL
            #    ether port they ride on. Repeat until no new
            #    parent names are discovered — handles nested
            #    constructs like a VLAN on top of a bridge.
            def _walk_parents() -> bool:
                """Returns True if we added something new."""
                added = False
                for path in (
                    "/interface/pppoe-client/print",
                    "/interface/vlan/print",
                    "/interface/eoip/print",
                    "/interface/gre/print",
                    "/interface/ipip/print",
                    "/interface/l2tp-client/print",
                ):
                    try:
                        rows = list(mt.print_(path))
                    except Exception:  # noqa: BLE001
                        continue  # protocol not enabled — fine
                    for row in rows:
                        if str(row.get("name", "") or "") not in wan_names:
                            continue
                        parent = (str(row.get("interface", "") or "").strip()
                                  or str(row.get("master-interface", "") or "").strip())
                        if parent and parent not in wan_names:
                            wan_names.add(parent)
                            added = True
                return added
            # A couple of passes covers PPPoE-on-VLAN-on-ether etc.
            for _ in range(3):
                if not _walk_parents():
                    break
    except Exception:  # noqa: BLE001
        pass  # WAN detection is best-effort
    for it in interfaces:
        it["is_wan"] = it.get("name") in wan_names
        if it["is_wan"]:
            # Unset the "recommended" hint and force not-checked default.
            it["recommended"] = False
    return jsonify({"ok": True, "interfaces": interfaces,
                    "wan_interfaces": sorted(wan_names)})


# ─── Friendly Arabic translations for planner blocking-error codes ───
# Surfaced to the operator instead of raw codes like
# `hotspot_no_interface_selected`. Falls back to the raw code when an
# entry isn't in the map — so new errors degrade gracefully.
_BLOCKING_ERRORS_AR = {
    # Hotspot
    "hotspot_no_interface_selected":
        "اختر واجهة شبكة واحدة على الأقل قبل المتابعة.",
    "hotspot_subnet_conflict":
        "النطاق الذي اخترته يتعارض مع شبكة أخرى على الراوتر. "
        "غيّره يدوياً أو اضغط «توليد».",
    # Broadband
    "broadband_no_interface_selected":
        "اختر واجهة شبكة واحدة على الأقل قبل المتابعة.",
    "broadband_pool_conflict":
        "نطاق المشتركين يتعارض مع شبكة أخرى على الراوتر. "
        "غيّر «نطاق المشتركين» يدوياً.",
    # Added services (block_sites / walled_garden / site_exit)
    "added_services_no_domains":
        "اكتب موقعاً واحداً على الأقل قبل المتابعة.",
    "added_services_too_many_targets":
        "عدد المواقع تجاوز الحدّ الأقصى (200). اختصر القائمة وحاول مرة أخرى.",
    "added_services_module_not_available":
        "هذه الخدمة غير مدعومة بعد على هذه النسخة.",
    "site_exit_no_exit_node":
        "اختر عقدة خروج (VPS exit node) قبل المتابعة.",
    "site_exit_invalid_destinations":
        "صيغة المواقع غير صحيحة — تأكّد من كتابة domain صحيح في كل سطر.",
    "radius_secret_mismatch":
        "سرّ RADIUS مفقود أو غير صحيح لهذا الراوتر. تحقّق من صفحة تعديل الراوتر.",
}


def _translate_blockers(blockers):
    """Map planner blocking-error codes to plain-Arabic messages.
    Unknown codes pass through (better than dropping them)."""
    return [_BLOCKING_ERRORS_AR.get(str(b), str(b)) for b in (blockers or ())]


def _plan_hotspot(router_id: int, inputs: dict) -> dict:
    """Shared planner call used by both preview and apply.

    The HotspotBootstrapPlanner needs THREE fields from the router's
    registry record that the operator never sees in the form:

      • radius_secret  — pre-shared secret between FreeRADIUS and the
                         router (lives in nas_devices.secret)
      • router_vpn_ip  — the router's address on the WireGuard tunnel
                         (lives in nas_devices.address since the
                         wizard registers it that way)
      • radius_server_ip — the FreeRADIUS box's WG-side address;
                           HOBERADIUS_WG_SERVER_IP env var or 10.10.0.1

    Inject them here so the operator's form payload stays minimal.
    Returns: (plan_result_dict, http_status, error_dict_or_none).
    """
    import os
    from ..db.repos import nas_repo
    from ..services.setup_wizard_hotspot_phase_planner import (
        HotspotPhasePlanner,
    )
    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return None, 404, {"error": "الراوتر غير موجود",
                           "code": "router_not_found"}
    inputs = dict(inputs)
    inputs.setdefault("radius_secret", str(nas.secret or ""))
    inputs.setdefault("router_vpn_ip", str(nas.address or ""))
    inputs.setdefault(
        "radius_server_ip",
        os.environ.get("HOBERADIUS_WG_SERVER_IP", "10.10.0.1"),
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
            "blocking_errors": _translate_blockers(plan_result.blocking_errors),
            "bullets": _hotspot_preview_bullets(plan_result),
        }), 409

    return jsonify({
        "ok": True,
        "bullets": _hotspot_preview_bullets(plan_result),
        "script": plan_result.script or "",
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
            "blocking_errors": _translate_blockers(plan_result.blocking_errors),
        }), 409
    if not plan_result.script or not plan_result.script.strip():
        return _err("لا يوجد سكربت لإرساله — تحقّق من المدخلات.",
                    status=400, code="empty_script")

    # Inject HOBERADIUS_SETUP tag on /ip hotspot add + /ip hotspot
    # profile add lines so the «خدماتي» tab can correctly identify
    # these entries as wizard-managed (the legacy planner only tags
    # address-list / walled-garden rows).
    script_to_send = _hotspot_post_process_script(
        plan_result.script, router_id=router_id,
    )
    try:
        executor = get_router_executor()
        exec_result = executor.execute_forward(
            router_id=router_id, script=script_to_send,
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
        fail_stage = _infer_fail_stage(exec_result)
        return jsonify({
            "ok": False, "code": "apply_failed",
            "error": exec_result.error_message or "تعذّر تنفيذ السكربت",
            "stderr": exec_result.stderr or "",
            "duration_ms": exec_result.duration_ms,
            "fail_stage": fail_stage,
            "substeps": _build_fail_substeps(fail_stage),
            # Surface the exact post-processed script so the operator
            # can copy it into MikroTik Terminal to find the trap.
            "debug_script": script_to_send,
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
        from ..integration.mikrotik import MikrotikClient
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
            servers = list(mt.print_("/ip/hotspot/print"))
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
    # Defaults — BroadbandBootstrapPlanner in mode="manual" REQUIRES
    # local_address + remote_pool_cidr (raises if either is empty).
    # IMPORTANT: local_address is a single IPv4 HOST (validated by
    # ipaddress.IPv4Address which rejects /xx prefixes), while
    # remote_pool_cidr is an IPv4 NETWORK (CIDR).
    #
    # dns_servers default = a SINGLE IP. The planner's own default
    # ("1.1.1.1,8.8.8.8") wraps the value in quotes inside the .rsc:
    #     dns-server="1.1.1.1,8.8.8.8"
    # RouterOS's `dns-server` property takes a list (IP[,IP]) but
    # quoting it as a comma-bearing string trips the parser
    # («expected end of command»). Using one IP avoids the issue
    # while keeping the script idempotent.
    inputs = {
        "selected_interfaces": list(body.get("selected_interfaces") or []),
        "local_address": (str(body.get("local_address") or "").strip()
                          or "10.30.0.1"),
        "remote_pool_cidr": (str(body.get("remote_pool_cidr") or "").strip()
                             or "10.30.1.0/24"),
        "dns_servers": (str(body.get("dns_servers") or "").strip()
                        or "1.1.1.1"),
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
            "blocking_errors": _translate_blockers(plan_result.blocking_errors),
            "bullets": _broadband_preview_bullets(plan_result),
        }), 409
    return jsonify({
        "ok": True,
        "bullets": _broadband_preview_bullets(plan_result),
        # Show the post-processed script so review reflects what's
        # actually sent (after dns-server strip + IP unquote).
        "script": _broadband_post_process_script(plan_result.script or ""),
    })


def _broadband_post_process_script(script: str) -> str:
    """Surgical fix-ups on the legacy planner's .rsc output so it
    works on RouterOS 7 (verified on 7.20.6 with operator).

    Six fix-ups, in order:

      1. dns-server="x.x.x.x" on /ppp profile  →  drop entirely
         IP-typed property doesn't accept quoted string. Router
         falls back to /ip dns servers — normal operator intent.

      2. local-address="x.x.x.x" on /ppp profile  →  unquote
         IP-typed; strict IPv4 pattern so string-typed properties
         elsewhere (name=, comment=, service-name=) aren't touched.

      3. use-radius=yes anywhere on /ppp profile  →  drop entirely
         Property doesn't exist on /ppp profile in RouterOS 7.

      4. /ppp profile set "name" …  →  /ppp profile set [find name="name"] …
         Direct-name set raises "no such item" even when the entry
         exists; the query form works.

      5. If step 3 dropped a use-radius=yes, append
         /ppp aaa set use-radius=yes  on its own line at the end.
         RADIUS for PPP lives on /ppp aaa in RouterOS 7, not on
         the profile.

      6. ⚠ Per-interface cleanup scoping (THE REPLACEMENT BUG FIX).
         The legacy planner emits a SHARED cleanup block keyed by the
         wizard run-id, which in turn equals the router-id. Every
         apply on the same router uses the same tag, so the cleanup
         wipes EVERY HobeRadius pppoe-server entry — including ones
         on other interfaces from previous runs. Result: programming
         ether3 silently nukes the ether2 setup.

         Fix: rewrite the cleanup so it only targets the interfaces
         the operator is actually re-programming in THIS run. We scan
         the script for ``/interface pppoe-server server add
         interface="X"`` lines, then replace the shared
         ``[find where comment~"<tag>"]`` cleanup with a precise
         ``[find where interface=X comment~"<tag>"]`` per-interface
         cleanup. The shared profile / pool / NAT entries are left
         untouched — they survive because the idempotent ``:if`` guards
         skip recreation, so concurrent sessions on other interfaces
         keep working.
    """
    import re
    # 1.
    script = re.sub(r'\s+dns-server="[^"]*"', "", script)
    # 2.
    script = re.sub(
        r'local-address="(\d{1,3}(?:\.\d{1,3}){3})"',
        r'local-address=\1',
        script,
    )
    # 3.
    had_use_radius = bool(re.search(r'\buse-radius=yes\b', script))
    script = re.sub(r'\s+use-radius=yes', "", script)
    # 4.
    script = re.sub(
        r'(/ppp\s+profile\s+set)\s+"([^"]+)"',
        r'\1 [find name="\2"]',
        script,
    )
    # 5.
    if had_use_radius and "/ppp aaa set use-radius=yes" not in script:
        script += (
            "\n"
            "# Enable RADIUS authentication for PPP globally\n"
            "/ppp aaa set use-radius=yes\n"
        )
    # 6. ── Per-interface cleanup scoping ─────────────────────────
    # Collect the interfaces this run is going to (re-)program by
    # parsing the planner's own `add interface="X"` lines.
    target_ifaces = list(dict.fromkeys(  # de-dupe, preserve order
        _re_add.group(1)
        for _re_add in re.finditer(
            r'/interface\s+pppoe-server\s+server\s+add\s+'
            r'interface="([^"]+)"',
            script,
        )
    ))
    if target_ifaces:
        # Extract the tag from any existing cleanup line so we don't
        # have to know it independently.
        tag_m = re.search(
            r'/interface\s+pppoe-server\s+server\s+remove\s+'
            r'\[find where comment~"([^"]+)"\]',
            script,
        )
        if tag_m:
            tag = tag_m.group(1)
            # Replace the shared pppoe-server cleanup with one targeted
            # remove per interface we're about to (re)program.
            iface_removes = "\n".join(
                f'/interface pppoe-server server remove '
                f'[find where interface="{i}" and comment~"{tag}"]'
                for i in target_ifaces
            )
            script = re.sub(
                r'/interface\s+pppoe-server\s+server\s+remove\s+'
                r'\[find where comment~"[^"]+"\]',
                iface_removes,
                script,
                count=1,
            )
            # The shared profile / pool / NAT removes would also wipe
            # state used by OTHER interfaces' active sessions. Drop
            # them — the idempotent `:if` guards re-create what's
            # missing without disturbing what exists.
            script = re.sub(
                r'^/ppp\s+profile\s+remove\s+'
                r'\[find where comment~"[^"]+"\]\s*\n',
                "",
                script,
                flags=re.MULTILINE,
            )
            script = re.sub(
                r'^/ip\s+pool\s+remove\s+'
                r'\[find where comment~"[^"]+"\]\s*\n',
                "",
                script,
                flags=re.MULTILINE,
            )
            script = re.sub(
                r'^/ip\s+firewall\s+nat\s+remove\s+'
                r'\[find where comment~"[^"]+"\]\s*\n',
                "",
                script,
                flags=re.MULTILINE,
            )
    return script


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
    # Defaults — BroadbandBootstrapPlanner in mode="manual" REQUIRES
    # local_address + remote_pool_cidr (raises if either is empty).
    # IMPORTANT: local_address is a single IPv4 HOST (validated by
    # ipaddress.IPv4Address which rejects /xx prefixes), while
    # remote_pool_cidr is an IPv4 NETWORK (CIDR).
    #
    # dns_servers default = a SINGLE IP. The planner's own default
    # ("1.1.1.1,8.8.8.8") wraps the value in quotes inside the .rsc:
    #     dns-server="1.1.1.1,8.8.8.8"
    # RouterOS's `dns-server` property takes a list (IP[,IP]) but
    # quoting it as a comma-bearing string trips the parser
    # («expected end of command»). Using one IP avoids the issue
    # while keeping the script idempotent.
    inputs = {
        "selected_interfaces": list(body.get("selected_interfaces") or []),
        "local_address": (str(body.get("local_address") or "").strip()
                          or "10.30.0.1"),
        "remote_pool_cidr": (str(body.get("remote_pool_cidr") or "").strip()
                             or "10.30.1.0/24"),
        "dns_servers": (str(body.get("dns_servers") or "").strip()
                        or "1.1.1.1"),
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
            "blocking_errors": _translate_blockers(plan_result.blocking_errors),
        }), 409
    if not plan_result.script or not plan_result.script.strip():
        return _err("لا يوجد سكربت لإرساله — تحقّق من المدخلات.",
                    status=400, code="empty_script")
    # Post-process the legacy planner output before sending — strips
    # the dns-server="x.x.x.x" attribute that RouterOS 7 refuses
    # quoted. The router uses system-wide DNS as a fallback, which
    # is the typical operator intent.
    script_to_send = _broadband_post_process_script(plan_result.script)
    try:
        exec_result = get_router_executor().execute_forward(
            router_id=router_id, script=script_to_send,
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
        fail_stage = _infer_fail_stage(exec_result)
        return jsonify({
            "ok": False, "code": "apply_failed",
            "error": exec_result.error_message or "تعذّر تنفيذ السكربت",
            "stderr": exec_result.stderr or "",
            "duration_ms": exec_result.duration_ms,
            "fail_stage": fail_stage,
            "substeps": _build_fail_substeps(fail_stage),
            # Surface the exact post-processed script so the operator
            # can copy it into MikroTik Terminal and find the offending
            # line by hand. Only exposed on failure — never on success.
            "debug_script": script_to_send,
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


def setup_wizard_v3_broadband_script(router_id: int):
    """Read-only debug endpoint — re-plans Broadband with the same
    inputs the apply endpoint would use and returns the
    post-processed .rsc text the executor would send. Operator
    pastes it into MikroTik Terminal to find the offending line
    when the apply traps somewhere we haven't seen yet."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    body = _body() or {}
    inputs = {
        "selected_interfaces": list(body.get("selected_interfaces") or []),
        "local_address": (str(body.get("local_address") or "").strip()
                          or "10.30.0.1"),
        "remote_pool_cidr": (str(body.get("remote_pool_cidr") or "").strip()
                             or "10.30.1.0/24"),
        "dns_servers": (str(body.get("dns_servers") or "").strip()
                        or "1.1.1.1"),
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
    raw = plan_result.script or ""
    cleaned = _broadband_post_process_script(raw)
    return jsonify({
        "ok": True,
        "raw_script": raw,
        "cleaned_script": cleaned,
        "blocking_errors": _translate_blockers(plan_result.blocking_errors),
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
        from ..integration.mikrotik import MikrotikClient
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
            # Check 3: PPP global RADIUS enable. In RouterOS 7 this
            # property lives on /ppp aaa, NOT on /ppp profile (where
            # it used to be in earlier versions). The post-processor
            # ensures `/ppp aaa set use-radius=yes` runs on apply, so
            # we mirror that here. Fallback: legacy profile-level
            # use-radius (RouterOS 6) for forward/backward safety.
            radius_linked = False
            try:
                aaa_rows = list(mt.print_("/ppp/aaa/print"))
                radius_linked = any(
                    str(a.get("use-radius", "")).lower() in ("true", "yes")
                    for a in aaa_rows
                )
            except Exception:  # noqa: BLE001
                pass
            if not radius_linked:
                profiles = list(mt.print_("/ppp/profile/print"))
                radius_linked = any(
                    str(p.get("use-radius", "")).lower() in (
                        "true", "yes", "default-use-radius",
                    )
                    for p in profiles
                )
            checks.append({
                "label": "RADIUS مفعَّل لـ PPP",
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
            "blocking_errors": _translate_blockers(plan_result.blocking_errors),
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
        fail_stage = _infer_fail_stage(exec_result)
        return jsonify({
            "ok": False, "code": "apply_failed",
            "error": exec_result.error_message or "تعذّر تنفيذ السكربت",
            "stderr": exec_result.stderr or "",
            "duration_ms": exec_result.duration_ms,
            "fail_stage": fail_stage,
            "substeps": _build_fail_substeps(fail_stage),
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


def _mt_client_for(nas):
    """Build a MikrotikClient config dict from a nas_devices row."""
    return {
        "host": nas.address,
        "username": nas.api_user or "admin",
        "password": nas.api_password,
        "port": int(nas.api_port or 8728),
        "use_tls": bool(nas.api_use_tls),
        "timeout": 6.0,
    }


def _is_block_sites_entry(comment: str) -> bool:
    """Match comments from BOTH the NPC engine that actually applies
    block-sites (uses `HOBE_NPC_WEB-BLOCK:<pid>:` prefix) AND any
    direct HOBERADIUS_SETUP tags. Earlier filter only matched the
    second form, missed everything the planner actually writes."""
    c = str(comment or "")
    return (
        "HOBE_NPC_WEB-BLOCK" in c
        or "HOBE_NPC_BLOCK" in c
        or ("HOBERADIUS_SETUP" in c and "block_sites" in c)
        or ("HOBERADIUS_SETUP" in c and "block-sites" in c)
        or ("HOBERADIUS_SETUP" in c and "web-block" in c)
    )


def _classify_source(comment: str) -> str:
    """Tell whether a RouterOS row was created by HobeRadius
    (carries our comment tag) or by the operator manually.
    Surfaced in the «خدماتي» tab so the operator knows what they
    can safely re-create from the wizard vs what they manage by hand.
    """
    c = str(comment or "")
    if (
        "HOBERADIUS_SETUP" in c
        or "HOBE_NPC_" in c
        or "HOBERADIUS_TECH" in c
    ):
        return "hoberadius"
    return "operator"


def _classify_hotspot_source(row: dict) -> str:
    """Hotspot-aware classifier. The legacy planner doesn't write
    a HOBERADIUS_SETUP comment on /ip hotspot rows, so the generic
    classifier above always returns "operator" for them. We catch
    that with a name-pattern fallback — the planner names the
    server `hotspot-<iface>` and the profile `hsprof-<iface>`."""
    src = _classify_source(row.get("comment", ""))
    if src == "hoberadius":
        return src
    name = str(row.get("name", "") or "")
    profile = str(row.get("profile", "") or "")
    if name.startswith("hotspot-") and profile.startswith("hsprof-"):
        return "hoberadius"
    return "operator"


def _classify_broadband_source(row: dict) -> str:
    """Broadband-aware classifier. The planner DOES write a comment
    on /interface pppoe-server server add, but we keep a name-
    pattern fallback in case an operator's previous wizard run
    pre-dates the tagging change."""
    src = _classify_source(row.get("comment", ""))
    if src == "hoberadius":
        return src
    service = str(row.get("service-name", "") or "")
    profile = str(row.get("default-profile", "") or "")
    if (service.startswith("hr-pppoe-") or
            profile.startswith("hr-ppp-profile-")):
        return "hoberadius"
    return "operator"


def _hotspot_post_process_script(script: str, *, router_id: int) -> str:
    """Quote comma-list values on /ip hotspot profile add lines.

    Context: the operator confirmed that pasting the exact same
    script body into the MikroTik *Terminal* works fine, but sending
    it through ``/system/script/add + /system/script/run`` (which is
    what LiveRouterExecutor does) raises «expected end of command».
    The script-mode parser is stricter than the CLI parser about
    bare comma-separated values: ``login-by=http-pap,cookie,mac-cookie``
    parses fine interactively but is rejected inside a stored script.

    Fix: wrap any ``login-by=…,…`` value in double quotes so the
    parser treats it as a single string literal. RouterOS accepts
    the quoted form on both the CLI and inside scripts.

    Note about tagging: we previously also tried injecting a
    ``comment="HOBERADIUS_SETUP:..."`` onto hotspot rows so the
    «خدماتي» inventory could classify them, but RouterOS 7.20.6
    exposes NO ``comment`` property on /ip hotspot or
    /ip hotspot profile entries — both raised their own traps. The
    inventory classifier already recognises wizard-managed entries
    by their ``hotspot-<iface>`` / ``hsprof-<iface>`` name pattern,
    so tagging is unnecessary.
    """
    import re as _re

    # Quote login-by=<list> if it contains a comma AND is not
    # already quoted. The pattern allows bare tokens such as
    # ``http-pap,cookie,mac-cookie`` but stops at the next
    # whitespace, which is the property boundary in RouterOS.
    re_login_by = _re.compile(
        r"(login-by=)(?!\")([^\s\"]*,[^\s\"]*)"
    )

    out: list[str] = []
    for line in script.split("\n"):
        if "/ip hotspot profile add " in line:
            line = re_login_by.sub(r'\1"\2"', line)
        out.append(line)
    return "\n".join(out)


def setup_wizard_v3_router_inventory(router_id: int):
    """Returns every active service entry the router currently has,
    grouped by service type. Each item carries:
      - a unique `target` identifier (the RouterOS internal .id),
      - a few human-readable detail fields,
      - a `source` flag (hoberadius / operator).

    Used by the «خدماتي» tab on the router page so the operator
    sees one consolidated list across all six services with per-
    item delete buttons.
    """
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return jsonify({"ok": True, "groups": []})
    groups: list = []
    # ── VPS-side remote-access info (computed independently of the
    # router) so the banner shows the correct host:port even when
    # the customer's public IP is dynamic and the router itself is
    # only reachable via the WireGuard tunnel. ──────────────────
    public_host_value = ""
    vps_urls: list = []
    try:
        from ..services import npc_remote_tunnel
        from ..services.npc_remote_access_urls import (
            compute_remote_access_urls,
        )
        from ..db.repos import npc_remote_port_mappings_repo as ports_repo

        public_host_value = (
            npc_remote_tunnel.public_host() or (nas.address or "")
        )
        existing_mappings = ports_repo.list_for_router(router_id)
        # Synthesize the policy from whatever mappings actually exist:
        # each enabled mapping tells us the service was previously
        # programmed via NPC / the wizard, so the URL should appear.
        synth_policy: dict = {"router_id": router_id}
        for m in existing_mappings:
            if not m.get("enabled"):
                continue
            svc = str(m.get("service") or "")
            synth_policy[f"allow_{svc}"] = True
        vps_urls = compute_remote_access_urls(
            synth_policy,
            public_host=public_host_value,
            mappings=existing_mappings,
        )
    except Exception:  # noqa: BLE001
        pass

    router_info: dict = {
        "vpn_address":     str(nas.address or ""),
        # VPS public host (the IP customers should actually use)
        # plus the per-service URL list. Replaces the old
        # public_address from /ip cloud which was unreliable on
        # dynamic-IP connections.
        "public_host":     public_host_value,
        "remote_urls":     vps_urls,
        # Backwards-compat: kept for any older client still reading
        # `public_address`. Set to the same VPS host so legacy code
        # paths point at the right thing instead of the dynamic
        # customer IP.
        "public_address":  public_host_value,
    }
    try:
        from ..integration.mikrotik import MikrotikClient
        with MikrotikClient(**_mt_client_for(nas)) as mt:
            # ─── Hotspot servers ────────────────────────────
            hotspot_items = []
            for s in mt.print_("/ip/hotspot/print"):
                hotspot_items.append({
                    "target": str(s.get(".id", "") or ""),
                    "service_type": "hotspot",
                    "label": str(s.get("interface", "") or "—"),
                    "details": {
                        "الواجهة": str(s.get("interface", "") or "—"),
                        "اسم الخادم": str(s.get("name", "") or "—"),
                        "الملف": str(s.get("profile", "") or "—"),
                        "الحالة": ("معطّل"
                                   if str(s.get("disabled", "")).lower() in ("true", "yes")
                                   else "يعمل"),
                    },
                    "source": _classify_hotspot_source(s),
                })
            if hotspot_items:
                groups.append({
                    "service_type": "hotspot",
                    "title": "Hotspot",
                    "color": "blue",
                    "icon": "wifi",
                    "items": hotspot_items,
                })
            # ─── PPPoE / Broadband ──────────────────────────
            pppoe_items = []
            for s in mt.print_("/interface/pppoe-server/server/print"):
                pppoe_items.append({
                    "target": str(s.get(".id", "") or ""),
                    "service_type": "broadband",
                    "label": str(s.get("interface", "") or "—"),
                    "details": {
                        "الواجهة": str(s.get("interface", "") or "—"),
                        "الخدمة": str(s.get("service-name", "") or "—"),
                        "الملف الافتراضي": str(s.get("default-profile", "") or "—"),
                        "الحالة": ("معطّل"
                                   if str(s.get("disabled", "")).lower() in ("true", "yes")
                                   else "يعمل"),
                    },
                    "source": _classify_broadband_source(s),
                })
            if pppoe_items:
                groups.append({
                    "service_type": "broadband",
                    "title": "Broadband (PPPoE)",
                    "color": "green",
                    "icon": "ethernet",
                    "items": pppoe_items,
                })
            # ─── Block-sites (address-list entries) ─────────
            block_items = []
            for e in mt.print_("/ip/firewall/address-list/print"):
                if not _is_block_sites_entry(e.get("comment", "")):
                    continue
                addr = str(e.get("address", "") or "").strip()
                if not addr:
                    continue
                block_items.append({
                    "target": str(e.get(".id", "") or ""),
                    "service_type": "block-sites",
                    "label": addr,
                    "details": {
                        "الموقع": addr,
                        "القائمة": str(e.get("list", "") or "—"),
                    },
                    "source": _classify_source(e.get("comment", "")),
                })
            if block_items:
                groups.append({
                    "service_type": "block-sites",
                    "title": "حجب مواقع",
                    "color": "red",
                    "icon": "ban",
                    "items": block_items,
                })
            # ─── Walled garden / open-sites ────────────────
            open_items = []
            for e in mt.print_("/ip/hotspot/walled-garden/print"):
                if not _is_walled_garden_entry(e.get("comment", "")):
                    continue
                host = (str(e.get("dst-host", "") or "").strip()
                        or str(e.get("dst-address", "") or "").strip())
                if not host:
                    continue
                open_items.append({
                    "target": str(e.get(".id", "") or ""),
                    "service_type": "open-sites",
                    "label": host,
                    "details": {
                        "الموقع": host,
                        "البروتوكول": str(e.get("server", "") or "—"),
                    },
                    "source": _classify_source(e.get("comment", "")),
                })
            if open_items:
                groups.append({
                    "service_type": "open-sites",
                    "title": "فتح مواقع (Walled Garden)",
                    "color": "teal",
                    "icon": "circle-check",
                    "items": open_items,
                })
            # ─── Public-IP (site-exit) — mangle rules ──────
            siteexit_items = []
            for r in mt.print_("/ip/firewall/mangle/print"):
                comment = str(r.get("comment", "") or "")
                if "HOBERADIUS_SETUP" not in comment or "site_exit" not in comment:
                    continue
                siteexit_items.append({
                    "target": str(r.get(".id", "") or ""),
                    "service_type": "public-ip",
                    "label": str(r.get("action", "") or "mark"),
                    "details": {
                        "السلسلة": str(r.get("chain", "") or "—"),
                        "العمل": str(r.get("action", "") or "—"),
                        "الهدف": str(r.get("dst-address-list", "")
                                     or r.get("new-routing-mark", "") or "—"),
                    },
                    "source": "hoberadius",
                })
            if siteexit_items:
                groups.append({
                    "service_type": "public-ip",
                    "title": "تغيير IP الخروج",
                    "color": "amber",
                    "icon": "globe",
                    "items": siteexit_items,
                })
            # ─── Remote-access grants — firewall filter rules
            remote_items = []
            for r in mt.print_("/ip/firewall/filter/print"):
                comment = str(r.get("comment", "") or "")
                if "HOBERADIUS_TECH" not in comment:
                    continue
                # Parse the token out of "HOBERADIUS_TECH:abc123ef:winbox"
                token = ""
                parts = [p for p in comment.split(":") if p]
                if len(parts) >= 2:
                    token = parts[1]
                remote_items.append({
                    "target": str(r.get(".id", "") or ""),
                    "service_type": "remote-access",
                    "label": (parts[2] if len(parts) >= 3 else "?"),
                    "details": {
                        "المنفذ": str(r.get("dst-port", "") or "—"),
                        "IP المصدر": str(r.get("src-address", "") or "أي"),
                        "معرّف الإذن": token or "—",
                    },
                    "source": "hoberadius",
                })
            if remote_items:
                groups.append({
                    "service_type": "remote-access",
                    "title": "اتصال عن بُعد",
                    "color": "purple",
                    "icon": "key",
                    "items": remote_items,
                })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"تعذّر قراءة الخدمات: {exc}",
                        "groups": groups,
                        "router_info": router_info}), 502
    return jsonify({"ok": True, "groups": groups,
                    "router_info": router_info})


def setup_wizard_v3_router_inventory_remove(router_id: int):
    """Delete one specific service entry by its RouterOS .id. The
    cleanup is precise — we remove exactly the row the operator
    confirmed, no cascading wipes. Pools / DHCP / IP addresses
    that the planner co-created stay behind so the operator can
    inspect them if they want; the «إيقاف الخدمة» button on each
    service flow still does the broader sweep.

    Body: { service_type: "hotspot"|"broadband"|..., target: ".id" }
    """
    from ..db.repos import nas_repo
    from ..services.npc_router_executor import (
        ExecutorNotConfigured, get_router_executor,
    )

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    body = _body() or {}
    service_type = str(body.get("service_type") or "").strip()
    target = str(body.get("target") or "").strip()
    if not target:
        return _err("معرّف العنصر مفقود.", status=400, code="missing_target")

    # Map service_type → RouterOS path the row lives in.
    SVC_PATHS = {
        "hotspot":       "/ip hotspot",
        "broadband":     "/interface pppoe-server server",
        "block-sites":   "/ip firewall address-list",
        "open-sites":    "/ip hotspot walled-garden",
        "public-ip":     "/ip firewall mangle",
        "remote-access": "/ip firewall filter",
    }
    path = SVC_PATHS.get(service_type)
    if not path:
        return _err(f"نوع خدمة غير مدعوم: {service_type}",
                    status=400, code="unknown_service")

    # The script removes a single row by its .id value (RouterOS
    # `find where .id=X` is the safest way to reference one entry).
    # Single quotes around target are safe — .id values are like
    # `*A1B2` (alphanumeric with leading `*`).
    script = (
        f'{path} remove [find where .id=\"{target}\"]\n'
    )
    try:
        exec_result = get_router_executor().execute_forward(
            router_id=router_id, script=script,
        )
    except ExecutorNotConfigured:
        return _err("وحدة تنفيذ السكربتات غير مُهيّأة.",
                    status=503, code="executor_not_configured")
    except Exception as exc:  # noqa: BLE001
        return _err(f"خطأ: {exc}", status=500, code="remove_error")
    if not exec_result.ok:
        return jsonify({
            "ok": False, "code": "remove_failed",
            "error": exec_result.error_message or "تعذّر الحذف",
            "stderr": exec_result.stderr or "",
        }), 502
    return jsonify({"ok": True, "duration_ms": exec_result.duration_ms})


def setup_wizard_v3_hotspot_current(router_id: int):
    """Read which interfaces have a Hotspot server running, plus the
    subnet that each one serves. Returns both the flat interface
    list (for pre-tick) AND an `installations` array with per-
    interface details for the "currently active" panel."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return jsonify({"ok": True, "interfaces": [], "installations": []})
    try:
        from ..integration.mikrotik import MikrotikClient
        with MikrotikClient(**_mt_client_for(nas)) as mt:
            servers = list(mt.print_("/ip/hotspot/print"))
            addresses = list(mt.print_("/ip/address/print"))
            # Index addresses by interface for quick lookup
            addr_by_iface: dict[str, str] = {}
            for a in addresses:
                ifn = str(a.get("interface", "") or "").strip()
                val = str(a.get("address", "") or "").strip()
                if ifn and val and ifn not in addr_by_iface:
                    addr_by_iface[ifn] = val
            installations = []
            interfaces = set()
            for s in servers:
                if str(s.get("disabled", "")).lower() not in ("false", "no", ""):
                    continue
                ifn = str(s.get("interface", "") or "").strip()
                if not ifn:
                    continue
                interfaces.add(ifn)
                installations.append({
                    "interface": ifn,
                    "name": str(s.get("name", "") or ""),
                    "profile": str(s.get("profile", "") or ""),
                    "address": addr_by_iface.get(ifn, ""),
                })
        return jsonify({
            "ok": True,
            "interfaces": sorted(interfaces),
            "installations": installations,
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": True, "interfaces": [],
                        "installations": [], "warning": str(exc)})


def setup_wizard_v3_broadband_current(router_id: int):
    """Same idea as hotspot/current — list active PPPoE servers and
    surface enough detail for the "currently active" panel."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return jsonify({"ok": True, "interfaces": [], "installations": []})
    try:
        from ..integration.mikrotik import MikrotikClient
        with MikrotikClient(**_mt_client_for(nas)) as mt:
            servers = list(mt.print_("/interface/pppoe-server/server/print"))
            profiles = list(mt.print_("/ppp/profile/print"))
            pools = list(mt.print_("/ip/pool/print"))
            pool_ranges = {
                str(p.get("name", "") or ""): str(p.get("ranges", "") or "")
                for p in pools
            }
            profile_remote = {
                str(p.get("name", "") or ""): str(p.get("remote-address", "") or "")
                for p in profiles
            }
            installations = []
            interfaces = set()
            for s in servers:
                if str(s.get("disabled", "")).lower() not in ("false", "no", ""):
                    continue
                ifn = str(s.get("interface", "") or "").strip()
                if not ifn:
                    continue
                interfaces.add(ifn)
                profile = str(s.get("default-profile", "") or "")
                pool = profile_remote.get(profile, "")
                installations.append({
                    "interface": ifn,
                    "name": str(s.get("service-name", "") or ""),
                    "profile": profile,
                    "pool_range": pool_ranges.get(pool, ""),
                })
        return jsonify({
            "ok": True,
            "interfaces": sorted(interfaces),
            "installations": installations,
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": True, "interfaces": [],
                        "installations": [], "warning": str(exc)})


def _is_walled_garden_entry(comment: str) -> bool:
    c = str(comment or "")
    return (
        "HOBE_NPC_WALLED-GARDEN" in c
        or "HOBE_NPC_WALLED_GARDEN" in c
        or ("HOBERADIUS_SETUP" in c and "walled_garden" in c)
        or ("HOBERADIUS_SETUP" in c and "walled-garden" in c)
        or ("HOBERADIUS_SETUP" in c and "open_sites" in c)
    )


def setup_wizard_v3_block_sites_current(router_id: int):
    """Read the currently-blocked domains for this router so the
    partial can pre-fill the textarea. Matches BOTH HOBE_NPC_WEB-
    BLOCK (the actual prefix the legacy planner writes) and the
    HOBERADIUS_SETUP fallback for forward compatibility."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return jsonify({"ok": True, "domains": []})
    try:
        from ..integration.mikrotik import MikrotikClient
        with MikrotikClient(**_mt_client_for(nas)) as mt:
            entries = list(mt.print_("/ip/firewall/address-list/print"))
            domains = sorted({
                str(e.get("address", "") or "").strip()
                for e in entries
                if _is_block_sites_entry(e.get("comment", ""))
                and str(e.get("address", "") or "").strip()
            })
        return jsonify({"ok": True, "domains": domains,
                        "entries_scanned": len(entries) if entries else 0})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": True, "domains": [], "warning": str(exc)})


def setup_wizard_v3_open_sites_current(router_id: int):
    """Same idea for walled-garden — pre-fill from /ip hotspot
    walled-garden entries matching either prefix."""
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        return jsonify({"ok": True, "domains": []})
    try:
        from ..integration.mikrotik import MikrotikClient
        with MikrotikClient(**_mt_client_for(nas)) as mt:
            entries = list(mt.print_("/ip/hotspot/walled-garden/print"))
            # Walled-garden uses `dst-host` (DNS) — fall back to
            # `dst-address` in case the operator added an IP form.
            domains = sorted({
                (str(e.get("dst-host", "") or "").strip()
                 or str(e.get("dst-address", "") or "").strip())
                for e in entries
                if _is_walled_garden_entry(e.get("comment", ""))
                and (str(e.get("dst-host", "") or "").strip()
                     or str(e.get("dst-address", "") or "").strip())
            })
        return jsonify({"ok": True, "domains": domains,
                        "entries_scanned": len(entries) if entries else 0})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": True, "domains": [], "warning": str(exc)})


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
            "blocking_errors": _translate_blockers(plan_result.blocking_errors),
            "bullets": bullets,
        }), 409
    return jsonify({"ok": True, "bullets": bullets,
                    "script": plan_result.script or ""})


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
        from ..integration.mikrotik import MikrotikClient
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
            # Check 1: address-list entries — match both NPC engine
            # prefix (the actual format) and the HOBERADIUS_SETUP tag.
            entries = list(mt.print_("/ip/firewall/address-list/print"))
            managed = [e for e in entries
                       if _is_block_sites_entry(e.get("comment", ""))]
            checks.append({
                "label": f"قائمة العناوين المحجوبة موجودة ({len(managed)} إدخالاً)",
                "status": "ok" if managed else "fail",
            })
            # Check 2: filter rule referencing the list.
            rules = list(mt.print_("/ip/firewall/filter/print"))
            blocked_rules = [r for r in rules
                             if str(r.get("action", "")).lower() == "drop"
                             and _is_block_sites_entry(r.get("comment", ""))]
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
            "blocking_errors": _translate_blockers(plan_result.blocking_errors),
            "bullets": bullets,
        }), 409
    return jsonify({"ok": True, "bullets": bullets,
                    "script": plan_result.script or ""})


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
        from ..integration.mikrotik import MikrotikClient
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
            # Check: walled-garden host entries — match both NPC
            # engine prefix and the HOBERADIUS_SETUP fallback.
            entries = list(mt.print_("/ip/hotspot/walled-garden/print"))
            managed = [e for e in entries
                       if _is_walled_garden_entry(e.get("comment", ""))]
            checks.append({
                "label": f"إدخالات المواقع المسموحة موجودة ({len(managed)} موقعاً)",
                "status": "ok" if managed else "fail",
            })
            # Check: Hotspot must be active for walled-garden to take effect.
            servers = list(mt.print_("/ip/hotspot/print"))
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
            "blocking_errors": _translate_blockers(plan_result.blocking_errors),
            "bullets": bullets,
        }), 409
    return jsonify({"ok": True, "bullets": bullets,
                    "script": plan_result.script or ""})


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
        from ..integration.mikrotik import MikrotikClient
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
    # Scheduler — fires after `interval` (set to ttl_hours), then the
    # on-event removes the rules + the scheduler itself in one go.
    #
    # CRITICAL: the on-event value is itself a quoted string in the
    # outer command. Any literal `"` inside it would terminate the
    # outer string prematurely (MikrotikTrap: «expected end of
    # command»). RouterOS treats `\"` as a literal quote inside a
    # quoted string — we use that here for every nested quote.
    on_event = (
        f'/ip firewall filter remove [find comment~\\"{tag}\\"]; '
        f'/system scheduler remove [find name=\\"{sched_name}\\"]'
    )
    lines += [
        "/system scheduler",
        f':foreach s in=[find name="{sched_name}"] do={{remove $s}}',
        (
            f'add name="{sched_name}" '
            f'interval={int(ttl_hours)}h '
            f'on-event="{on_event}" '
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
    # Build a *preview* of the same script the apply path would send.
    # The grant_token will be regenerated client-side at apply time,
    # so this is a representative sample (not the exact final one).
    preview_token = str(body.get("grant_token") or "PREVIEW")
    script = _remote_access_build_script(
        services=services, ttl_hours=ttl_hours,
        source_ip=source_ip, grant_token=preview_token,
    )
    return jsonify({"ok": True, "bullets": bullets, "script": script})


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
        fail_stage = _infer_fail_stage(exec_result)
        return jsonify({
            "ok": False, "code": "apply_failed",
            "error": exec_result.error_message or "تعذّر تنفيذ السكربت",
            "stderr": exec_result.stderr or "",
            "duration_ms": exec_result.duration_ms,
            "fail_stage": fail_stage,
            "substeps": _build_fail_substeps(fail_stage),
        }), 502

    # ── Allocate VPS public ports + build «from-outside» URLs ──
    #
    # The router-side firewall script alone only opens ports on the
    # router; customers whose ISPs hand out dynamic public IPs can't
    # rely on the router's own public-address being reachable. The
    # production design (see npc_remote_port_mappings + nginx-stream
    # in deploy/) routes remote access through the VPS: every enabled
    # service gets a stable port in the 51000-51199 range that nginx
    # forwards to the router over the WireGuard tunnel.
    #
    # We do this here (rather than in the script builder) so allocation
    # only happens AFTER the router accepted the rules — no orphan
    # mappings if the apply failed.
    connection_urls: list[dict] = []
    public_host_value = ""
    try:
        from ..services import npc_remote_tunnel
        from ..services.npc_remote_access_urls import (
            compute_remote_access_urls,
        )

        # Map the wizard's free-form services list (name + port pairs)
        # onto the canonical NPC policy keys understood by
        # ensure_tunnels_for_policy + compute_remote_access_urls.
        svc_names = {str(s.get("name", "")).lower() for s in services}
        policy = {
            "router_id":          router_id,
            "allow_winbox":       "winbox" in svc_names,
            "allow_ssh":          "ssh" in svc_names,
            "allow_webfig_http":  "webfig" in svc_names,
            "allow_api":          "api" in svc_names,
        }
        mappings = npc_remote_tunnel.ensure_tunnels_for_policy(
            tenant_id=_tid(), policy=policy,
        )
        # Push the new mappings into the nginx stream config + signal
        # the sidecar to reload. Best-effort — a stale config just
        # means the operator's external URLs won't work yet; the
        # router-side rules still work for VPN-internal access.
        try:
            npc_remote_tunnel.regenerate_and_reload()
        except Exception:  # noqa: BLE001
            pass

        # VPS IP from env, falls back to nas.address (which is the
        # WG tunnel IP — usable inside VPN only).
        public_host_value = (
            npc_remote_tunnel.public_host() or (nas.address or "")
        )
        connection_urls = compute_remote_access_urls(
            policy, public_host=public_host_value, mappings=mappings,
        )
    except Exception:  # noqa: BLE001
        # Allocator must never block the apply from reporting success
        # — the firewall script ran fine, just the VPS relay setup
        # is degraded. Operator can re-trigger to retry.
        pass

    return jsonify({
        "ok": True,
        "duration_ms": exec_result.duration_ms,
        "grant_token": grant_token,
        "connection": {
            # The address most operators want — the VPS public host
            # they enter in Winbox / their browser, regardless of
            # what the customer's ISP is doing with their public IP.
            "public_host":   public_host_value,
            "urls":          connection_urls,
            # VPN address kept for internal-network use (techs on
            # the same WireGuard mesh).
            "vpn_address":   nas.address or "",
            "services":      services,
            "ttl_hours":     ttl_hours,
            "source_ip":     source_ip,
        },
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
        from ..integration.mikrotik import MikrotikClient
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


_REVOKE_SCRIPTS = {
    # Each entry returns a RouterOS v7 script that removes ONLY items
    # tagged with HOBERADIUS_SETUP:<router_id>:... (or HOBERADIUS_TECH:*
    # for remote-access). The router_id is templated in at runtime so
    # cross-router removal is impossible.
    "hotspot": lambda rid: f"""\
/ip hotspot
:foreach h in=[find comment~"HOBERADIUS_SETUP:{rid}"] do={{remove $h}}
/ip hotspot profile
:foreach p in=[find comment~"HOBERADIUS_SETUP:{rid}"] do={{remove $p}}
/ip pool
:foreach p in=[find comment~"HOBERADIUS_SETUP:{rid}"] do={{remove $p}}
/ip dhcp-server
:foreach d in=[find comment~"HOBERADIUS_SETUP:{rid}"] do={{remove $d}}
""",
    "broadband": lambda rid: f"""\
/interface pppoe-server server
:foreach s in=[find comment~"HOBERADIUS_SETUP:{rid}"] do={{remove $s}}
/ppp profile
:foreach p in=[find comment~"HOBERADIUS_SETUP:{rid}"] do={{remove $p}}
/ip pool
:foreach p in=[find comment~"HOBERADIUS_SETUP:{rid}"] do={{remove $p}}
""",
    "block-sites": lambda rid: f"""\
/ip firewall address-list
:foreach a in=[find comment~"HOBERADIUS_SETUP:{rid}.*block_sites"] do={{remove $a}}
/ip firewall filter
:foreach f in=[find comment~"HOBERADIUS_SETUP:{rid}.*block_sites"] do={{remove $f}}
""",
    "open-sites": lambda rid: f"""\
/ip hotspot walled-garden
:foreach w in=[find comment~"HOBERADIUS_SETUP:{rid}.*walled_garden"] do={{remove $w}}
""",
    "public-ip": lambda rid: f"""\
/ip firewall mangle
:foreach m in=[find comment~"HOBERADIUS_SETUP:{rid}.*site_exit"] do={{remove $m}}
/ip route
:foreach r in=[find comment~"HOBERADIUS_SETUP:{rid}.*site_exit"] do={{remove $r}}
/ip firewall address-list
:foreach a in=[find comment~"HOBERADIUS_SETUP:{rid}.*site_exit"] do={{remove $a}}
""",
    "remote-access": lambda _rid: """\
/ip firewall filter
:foreach f in=[find comment~"HOBERADIUS_TECH"] do={remove $f}
/system scheduler
:foreach s in=[find comment~"HOBERADIUS_TECH"] do={remove $s}
""",
}


def setup_wizard_v3_service_revoke(router_id: int, service_key: str):
    """Generic revoke endpoint — builds a service-specific rollback
    script that ONLY removes RouterOS objects tagged with this
    router's HOBERADIUS_SETUP:<router_id>:... comment prefix.

    Cross-router safety: the router_id is interpolated into the
    pattern, so even if two routers share managed comments by
    accident, this endpoint never touches another router's items.
    """
    from ..db.repos import nas_repo
    from ..services.npc_router_executor import (
        ExecutorNotConfigured, get_router_executor,
    )

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    builder = _REVOKE_SCRIPTS.get(service_key)
    if not builder:
        return _err(f"الإيقاف غير مدعوم لهذه الخدمة: {service_key}",
                    status=400, code="revoke_not_supported")
    script = builder(router_id)
    try:
        exec_result = get_router_executor().execute_forward(
            router_id=router_id, script=script,
        )
    except ExecutorNotConfigured:
        return _err("وحدة تنفيذ السكربتات غير مُهيّأة على الخادم.",
                    status=503, code="executor_not_configured")
    except Exception as exc:  # noqa: BLE001
        return _err(f"خطأ غير متوقّع: {exc}",
                    status=500, code="revoke_error")
    if not exec_result.ok:
        return jsonify({
            "ok": False, "code": "revoke_failed",
            "error": exec_result.error_message or "تعذّر تنفيذ سكربت الإيقاف",
            "stderr": exec_result.stderr or "",
        }), 502
    return jsonify({"ok": True, "duration_ms": exec_result.duration_ms})


def setup_wizard_v3_router_services_status(router_id: int):
    """Returns the live state of every service for one router in one
    MikroTik API call. Used by the Router Hub chips to show 🟢/⚪
    badges without making 6 separate verify requests.

    Output:
      { ok: true,
        services: {
          hotspot: true|false,
          broadband: true|false,
          "block-sites": true|false,
          "open-sites": true|false,
          "public-ip": true|false,
          "remote-access": true|false,
        }
      }
    """
    from ..db.repos import nas_repo

    nas = nas_repo.get_nas(_tid(), router_id)
    if not nas:
        return _err("الراوتر غير موجود", status=404, code="router_not_found")
    if not nas.api_password:
        # Treat as "unknown" — no creds, all services neutral.
        return jsonify({
            "ok": True,
            "services": {k: None for k in (
                "hotspot", "broadband", "block-sites",
                "open-sites", "public-ip", "remote-access",
            )},
        })
    try:
        from ..integration.mikrotik import MikrotikClient
    except Exception as exc:  # noqa: BLE001
        return _err(f"تعذّر تحميل عميل MikroTik: {exc}", status=500,
                    code="mt_client_load_error")
    cfg = {
        "host": nas.address, "username": nas.api_user or "admin",
        "password": nas.api_password, "port": int(nas.api_port or 8728),
        "use_tls": bool(nas.api_use_tls), "timeout": 6.0,
    }
    status = {
        "hotspot": False, "broadband": False, "block-sites": False,
        "open-sites": False, "public-ip": False, "remote-access": False,
    }
    rid_tag = f"HOBERADIUS_SETUP:{router_id}"
    try:
        with MikrotikClient(**cfg) as mt:
            # Hotspot: any enabled hotspot server.
            servers = list(mt.print_("/ip/hotspot/print"))
            status["hotspot"] = any(
                str(s.get("disabled", "")).lower() in ("false", "no", "")
                for s in servers
            )
            # Broadband: any enabled PPPoE server.
            pppoe = list(mt.print_("/interface/pppoe-server/server/print"))
            status["broadband"] = any(
                str(s.get("disabled", "")).lower() in ("false", "no", "")
                for s in pppoe
            )
            # Block-sites: address-list entries tagged for this run.
            al = list(mt.print_("/ip/firewall/address-list/print"))
            status["block-sites"] = any(
                rid_tag in str(e.get("comment", ""))
                and "block_sites" in str(e.get("comment", ""))
                for e in al
            )
            # Open-sites: walled-garden hosts tagged for this run.
            wg = list(mt.print_("/ip/hotspot/walled-garden/print"))
            status["open-sites"] = any(
                rid_tag in str(e.get("comment", ""))
                and "walled_garden" in str(e.get("comment", ""))
                for e in wg
            )
            # Public-IP: mangle rules tagged for this run.
            mangle = list(mt.print_("/ip/firewall/mangle/print"))
            status["public-ip"] = any(
                rid_tag in str(m.get("comment", ""))
                and "site_exit" in str(m.get("comment", ""))
                for m in mangle
            )
            # Remote-access: ANY rule with HOBERADIUS_TECH (no run_id
            # since techs may have multiple tokens).
            rules = list(mt.print_("/ip/firewall/filter/print"))
            status["remote-access"] = any(
                "HOBERADIUS_TECH" in str(r.get("comment", ""))
                for r in rules
            )
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False, "code": "probe_failed",
            "error": f"تعذّر الاتصال بالراوتر: {exc}",
            # Return current best-effort partial status.
            "services": status,
        }), 502
    return jsonify({"ok": True, "services": status})


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
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/broadband/script",
        "setup_wizard_v3_broadband_script",
        setup_wizard_v3_broadband_script,
        methods=["POST"],
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
    # Read-current endpoints so each partial can pre-fill its textarea
    # with what's already configured — solves the «I added 4 then tried
    # to add 3 and the first 4 disappeared» surprise.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/block-sites/current",
        "setup_wizard_v3_block_sites_current",
        setup_wizard_v3_block_sites_current,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/open-sites/current",
        "setup_wizard_v3_open_sites_current",
        setup_wizard_v3_open_sites_current,
        methods=["GET"],
    )
    # Hotspot/Broadband current-state: which interfaces are already
    # serving each service, so the partial pre-ticks them and a
    # single-pick replacement doesn't accidentally wipe peers.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/hotspot/current",
        "setup_wizard_v3_hotspot_current",
        setup_wizard_v3_hotspot_current,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/broadband/current",
        "setup_wizard_v3_broadband_current",
        setup_wizard_v3_broadband_current,
        methods=["GET"],
    )
    # «خدماتي» tab — unified inventory + per-item remove.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/inventory",
        "setup_wizard_v3_router_inventory",
        setup_wizard_v3_router_inventory,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/inventory/remove",
        "setup_wizard_v3_router_inventory_remove",
        setup_wizard_v3_router_inventory_remove,
        methods=["POST"],
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
    # Generic revoke — dispatches by service_key. Removes ONLY the
    # RouterOS objects tagged with this router's HOBERADIUS_SETUP
    # comment prefix.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services/<service_key>/revoke",
        "setup_wizard_v3_service_revoke",
        setup_wizard_v3_service_revoke,
        methods=["POST"],
    )
    # Bulk live-status probe for all 6 services in one MT call.
    bp.add_url_rule(
        "/setup-wizard-v3/routers/<int:router_id>/services-status",
        "setup_wizard_v3_router_services_status",
        setup_wizard_v3_router_services_status,
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
