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
    """Lists configurable services for one router. Each card opens
    its own multi-step phased flow (the per-service screen).

    Renders a friendly 'router not found' fallback instead of 404
    so the back-from-fleet UX doesn't dead-end if a router was
    just retired.
    """
    from ..db.repos import nas_repo

    router = nas_repo.get_nas(_tid(), router_id)
    return render_template(
        "radius/setup_wizard_v3_router_services.html",
        router=router,
        router_id=router_id,
        service_cards=ROUTER_SERVICE_CARDS,
        page_title=(
            f"خدمات الراوتر «{router.name}»" if router
            else "خدمات الراوتر"
        ),
    )


def setup_wizard_v3_router_service_flow(router_id: int, service_key: str):
    """Per-service phased flow. For each service key, renders the
    same shell template — the concrete steps are driven by the
    service definition. In this commit the page shows a 'coming
    soon' placeholder; subsequent commits will wire each flow to
    its planner + verification probe."""
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
    return render_template(
        "radius/setup_wizard_v3_router_service_flow.html",
        router=router,
        router_id=router_id,
        service_key=service_key,
        card=card,
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
