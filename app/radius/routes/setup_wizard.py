from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..services.setup_wizard import (
    SetupWizardValidationError,
    get_setup_wizard_service,
)
from ..services.setup_wizard_fleet import RouterFleetProvisioningService
from ..services.setup_wizard_fleet_emergency_reset import (
    FleetResetConfirmationError,
    SetupWizardFleetEmergencyReset,
)
from ..services.setup_wizard_interface_contract import InterfaceInfo
from ..services.setup_wizard_recovery import SetupWizardRecoveryService
from ..services.setup_wizard_server_wg_readiness import ServerWireGuardReadinessService
from ..services.setup_wizard_verification import SetupDiagnosticsService


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _svc():
    return get_setup_wizard_service()


def _fleet_svc():
    return RouterFleetProvisioningService(
        recovery_service=SetupWizardRecoveryService(wizard_service=_svc())
    )


def _body() -> dict[str, Any]:
    if request.is_json:
        return dict(request.get_json(silent=True) or {})
    return request.form.to_dict(flat=True)


def _json_error(message: str, status: int = 400, code: str = "validation_error"):
    return jsonify({"ok": False, "error": message, "code": code}), status


def _verification_payload(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    mode = str(body.get("mode") or "pasted_output").strip().lower()
    payload = {
        "output": str(body.get("output") or ""),
    }
    checks = body.get("checks")
    if isinstance(checks, dict):
        payload["checks"] = checks
    return mode, payload


def register_setup_wizard_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/setup-wizard", "setup_wizard_page", setup_wizard_page, methods=["GET"])
    bp.add_url_rule("/setup-wizard-v2", "setup_wizard_v2_page", setup_wizard_v2_page, methods=["GET"])
    bp.add_url_rule("/setup-wizard/fleet", "setup_wizard_fleet_page", setup_wizard_fleet_page, methods=["GET"])
    bp.add_url_rule("/setup-wizard/fleet/data", "setup_wizard_fleet_data", setup_wizard_fleet_data, methods=["GET"])
    bp.add_url_rule("/setup-wizard/fleet/router/<int:registry_id>", "setup_wizard_fleet_router", setup_wizard_fleet_router, methods=["GET"])
    bp.add_url_rule("/setup-wizard/fleet/router/<int:registry_id>/resume", "setup_wizard_fleet_router_resume", setup_wizard_fleet_router_resume, methods=["POST"])
    bp.add_url_rule("/setup-wizard/fleet/router/<int:registry_id>/retire", "setup_wizard_fleet_router_retire", setup_wizard_fleet_router_retire, methods=["POST"])
    bp.add_url_rule(
        "/setup-wizard/_system_health",
        "setup_wizard_system_health",
        setup_wizard_system_health,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard/fleet/emergency-reset/preview",
        "setup_wizard_fleet_emergency_reset_preview",
        setup_wizard_fleet_emergency_reset_preview,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard/fleet/emergency-reset",
        "setup_wizard_fleet_emergency_reset",
        setup_wizard_fleet_emergency_reset,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/fleet/router/<int:registry_id>/cancel-tentative",
        "setup_wizard_fleet_cancel_tentative",
        setup_wizard_fleet_cancel_tentative,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/fleet/reclaim-expired",
        "setup_wizard_fleet_reclaim_expired",
        setup_wizard_fleet_reclaim_expired,
        methods=["POST"],
    )
    bp.add_url_rule("/setup-wizard/server-wg/readiness", "setup_wizard_server_wg_readiness", setup_wizard_server_wg_readiness, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs", "setup_wizard_create_run", setup_wizard_create_run, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>", "setup_wizard_get_run", setup_wizard_get_run, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/internet-source", "setup_wizard_set_internet_source", setup_wizard_set_internet_source, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/generate-internet-script", "setup_wizard_generate_internet_script", setup_wizard_generate_internet_script, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/verify-internet", "setup_wizard_verify_internet", setup_wizard_verify_internet, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/generate-vpn-radius-script", "setup_wizard_generate_vpn_script", setup_wizard_generate_vpn_script, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/router-public-key", "setup_wizard_router_public_key", setup_wizard_router_public_key, methods=["POST"])
    bp.add_url_rule(
        "/setup-wizard/runs/<int:run_id>/router-public-key/auto-detect",
        "setup_wizard_router_public_key_auto_detect",
        setup_wizard_router_public_key_auto_detect,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/runs/<int:run_id>/server-peer/complete",
        "setup_wizard_server_peer_complete",
        setup_wizard_server_peer_complete,
        methods=["POST"],
    )
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/server-peer/dry-run", "setup_wizard_server_peer_dry_run", setup_wizard_server_peer_dry_run, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/server-peer/apply", "setup_wizard_server_peer_apply", setup_wizard_server_peer_apply, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/server-peer/rollback", "setup_wizard_server_peer_rollback", setup_wizard_server_peer_rollback, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/server-peer/verify", "setup_wizard_server_peer_verify", setup_wizard_server_peer_verify, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/server-peer/health", "setup_wizard_server_peer_health", setup_wizard_server_peer_health, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/server-peer/operations", "setup_wizard_server_peer_operations", setup_wizard_server_peer_operations, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/verify-vpn-radius", "setup_wizard_verify_vpn", setup_wizard_verify_vpn, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/interfaces/candidates", "setup_wizard_interfaces_candidates", setup_wizard_interfaces_candidates, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/generate-hotspot-script", "setup_wizard_generate_hotspot_script", setup_wizard_generate_hotspot_script, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/verify-hotspot", "setup_wizard_verify_hotspot", setup_wizard_verify_hotspot, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/generate-broadband-script", "setup_wizard_generate_broadband_script", setup_wizard_generate_broadband_script, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/verify-broadband", "setup_wizard_verify_broadband", setup_wizard_verify_broadband, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/summary", "setup_wizard_run_summary", setup_wizard_run_summary, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/dry-run/<step_key>", "setup_wizard_dry_run", setup_wizard_dry_run, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/apply/<step_key>", "setup_wizard_apply", setup_wizard_apply, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/rollback/<step_key>", "setup_wizard_rollback", setup_wizard_rollback, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/operations", "setup_wizard_operations", setup_wizard_operations, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/inventory", "setup_wizard_inventory", setup_wizard_inventory, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/inventory/latest", "setup_wizard_inventory_latest", setup_wizard_inventory_latest, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/orchestrate/hotspot", "setup_wizard_orchestrate_hotspot", setup_wizard_orchestrate_hotspot, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/orchestrate/broadband", "setup_wizard_orchestrate_broadband", setup_wizard_orchestrate_broadband, methods=["POST"])
    bp.add_url_rule("/setup-wizard/added-services/catalog", "setup_wizard_added_services_catalog", setup_wizard_added_services_catalog, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/added-services/plan", "setup_wizard_added_services_plan", setup_wizard_added_services_plan, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/added-services/dry-run", "setup_wizard_added_services_dry_run", setup_wizard_added_services_dry_run, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/added-services/apply", "setup_wizard_added_services_apply", setup_wizard_added_services_apply, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/added-services/verify", "setup_wizard_added_services_verify", setup_wizard_added_services_verify, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/support-bundle", "setup_wizard_support_bundle", setup_wizard_support_bundle, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/health", "setup_wizard_health", setup_wizard_health, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/pilot-drill", "setup_wizard_pilot_drill", setup_wizard_pilot_drill, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/recovery", "setup_wizard_recovery", setup_wizard_recovery, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/recovery/resume", "setup_wizard_recovery_resume", setup_wizard_recovery_resume, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/recovery/retry-verification", "setup_wizard_recovery_retry_verification", setup_wizard_recovery_retry_verification, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/recovery/regenerate-script", "setup_wizard_recovery_regenerate_script", setup_wizard_recovery_regenerate_script, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/recovery/abandon-step", "setup_wizard_recovery_abandon_step", setup_wizard_recovery_abandon_step, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/recovery/retire-router", "setup_wizard_recovery_retire_router", setup_wizard_recovery_retire_router, methods=["POST"])


def setup_wizard_page():
    run_id = request.args.get("run_id", type=int)
    summary = None
    if run_id:
        try:
            summary = _svc().get_run_summary(tenant_id=_tid(), run_id=run_id)
        except SetupWizardValidationError:
            summary = None
    return render_template(
        "radius/setup_wizard.html",
        summary=summary,
        diagnostics_catalog=SetupDiagnosticsService().list_all(),
    )


def setup_wizard_v2_page():
    return render_template("radius/setup_wizard_v2.html")


def setup_wizard_fleet_page():
    """Legacy URL — the provisioning-only fleet page was consolidated
    into the unified router management page (/mt/operations) in
    May 2026. Kept as a 302 redirect so existing operator bookmarks
    and the «العودة إلى أسطول الراوترات» link from the Router Services
    Dashboard land on the same destination. The data + maintenance
    API endpoints below remain at their original URLs because the
    new page calls them via JS."""
    return redirect(url_for("radius.mt_operations"), code=302)


def setup_wizard_fleet_data():
    include_retired = str(request.args.get("include_retired", "1")).lower() not in {"0", "false", "no"}
    try:
        data = _fleet_svc().summary(
            tenant_id=_tid(),
            status=str(request.args.get("status") or ""),
            lifecycle_state=str(request.args.get("lifecycle_state") or ""),
            failed_only=str(request.args.get("failed_only", "")).lower() in {"1", "true", "yes"},
            include_retired=include_retired,
            search=str(request.args.get("q") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=400, code="fleet_query_failed")
    return jsonify({"ok": True, "fleet": data})


def setup_wizard_fleet_router(registry_id: int):
    try:
        detail = _fleet_svc().router_detail(tenant_id=_tid(), registry_id=registry_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=404, code="router_not_found")
    return jsonify({"ok": True, "detail": detail})


def setup_wizard_fleet_router_resume(registry_id: int):
    try:
        result = _fleet_svc().resume_router(tenant_id=_tid(), registry_id=registry_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="resume_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") != "blocked", **result}), status


def setup_wizard_fleet_router_retire(registry_id: int):
    body = _body()
    try:
        result = _fleet_svc().retire_router(
            tenant_id=_tid(),
            registry_id=registry_id,
            reason=str(body.get("reason") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="retire_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") != "blocked", **result}), status


def setup_wizard_system_health():
    """Single-pane production health check. Runs every
    invariant established in postmortems #1-#21 and reports
    a verdict. Returns HTTP 503 when overall != 'healthy' so
    external uptime monitors alarm on degraded/critical.
    """
    from ..services.setup_wizard_system_health import check_all
    try:
        report = check_all()
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False,
            "overall": "critical",
            "error": f"health-check failed: {exc}",
        }), 503
    # HTTP semantics:
    # - 200: healthy OR degraded (system is functional;
    #   degraded means 'drift detected but self-healed —
    #   investigate when convenient'). External monitors
    #   shouldn't page on degraded — it's expected after
    #   any deploy.
    # - 503: critical (something fundamental broken).
    #   External monitors should page IMMEDIATELY.
    http_status = (
        503 if report["overall"] == "critical" else 200
    )
    return jsonify({"ok": True, **report}), http_status


def setup_wizard_fleet_emergency_reset_preview():
    """Preview row counts that would be deleted by the
    emergency reset. Read-only."""
    try:
        preview = SetupWizardFleetEmergencyReset().preview(tenant_id=_tid())
    except Exception as exc:  # noqa: BLE001
        return _json_error(
            f"emergency reset preview failed: {exc}",
            status=500,
            code="emergency_reset_preview_failed",
        )
    return jsonify({"ok": True, "preview": preview})


def setup_wizard_fleet_emergency_reset():
    """DANGER: wipes wizard fleet provisioning state. Requires
    the literal confirm phrase in the request body. Audit-logs
    every invocation at severity=critical."""
    body = _body()
    confirm = str(body.get("confirm") or "")
    clear_peer_files = (
        str(body.get("clear_peer_files", "1")).lower()
        not in {"0", "false", "no"}
    )
    actor = str(
        body.get("actor")
        or getattr(g, "admin_username", "")
        or "admin",
    )
    try:
        summary = SetupWizardFleetEmergencyReset().reset(
            tenant_id=_tid(),
            confirm=confirm,
            actor=actor,
            clear_peer_files=clear_peer_files,
        )
    except FleetResetConfirmationError as exc:
        return _json_error(
            str(exc),
            status=412,
            code="emergency_reset_confirmation_required",
        )
    except Exception as exc:  # noqa: BLE001
        return _json_error(
            f"emergency reset failed: {exc}",
            status=500,
            code="emergency_reset_failed",
        )
    return jsonify({"ok": True, "reset": summary})


def setup_wizard_fleet_cancel_tentative(registry_id: int):
    """Manually cancel a single tentative reservation. The
    operator clicks this when they know a wizard run has failed
    and they don't want to wait for the TTL to expire."""
    from ..services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    body = _body()
    actor = str(
        body.get("actor")
        or getattr(g, "admin_username", "")
        or "admin",
    )
    try:
        result = SetupWizardTentativeReclaimer().reclaim_one(
            tenant_id=_tid(),
            registry_id=int(registry_id),
            reason="manual_cancel",
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001
        return _json_error(
            f"manual cancel failed: {exc}",
            status=500,
            code="manual_cancel_failed",
        )
    if result.get("status") == "not_found":
        return _json_error(
            "router not found",
            status=404,
            code="router_not_found",
        )
    if result.get("status") == "skipped_permanent":
        return _json_error(
            "router has already been verified — refusing to cancel",
            status=409,
            code="cannot_cancel_permanent",
        )
    return jsonify({"ok": True, "cancel": result})


def setup_wizard_fleet_reclaim_expired():
    """Sweep all expired tentative reservations on demand. Used
    by the UI's 'Reclaim expired now' button — also runs every
    5 min automatically via the background worker."""
    from ..services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    actor = str(
        getattr(g, "admin_username", "") or "admin",
    )
    try:
        result = SetupWizardTentativeReclaimer().reclaim_all_expired(
            tenant_id=_tid(),
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001
        return _json_error(
            f"sweep failed: {exc}",
            status=500,
            code="reclaim_sweep_failed",
        )
    return jsonify({"ok": True, "sweep": result})


def setup_wizard_server_wg_readiness():
    return jsonify({"ok": True, "readiness": ServerWireGuardReadinessService().evaluate()})


def setup_wizard_create_run():
    body = _body()
    actor = str(body.get("actor") or getattr(g, "admin_username", "") or "wizard")
    run = _svc().create_run(tenant_id=_tid(), actor=actor)
    return jsonify({"ok": True, "run": run})


def setup_wizard_get_run(run_id: int):
    try:
        summary = _svc().get_run_summary(tenant_id=_tid(), run_id=run_id)
    except SetupWizardValidationError:
        abort(404)
    return jsonify({"ok": True, **summary})


def setup_wizard_set_internet_source(run_id: int):
    body = _body()
    source_type = str(body.get("source_type") or body.get("internet_source_type") or "").strip().lower()
    selected_wan_interface = str(body.get("selected_wan_interface") or "").strip()
    input_json = dict(body.get("input_json") or {})
    if not input_json:
        input_json = {k: v for k, v in body.items() if k not in {"source_type", "internet_source_type", "selected_wan_interface"}}
    try:
        run = _svc().set_internet_source(
            tenant_id=_tid(),
            run_id=run_id,
            source_type=source_type,
            selected_wan_interface=selected_wan_interface,
            input_json=input_json,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "run": run})


def setup_wizard_generate_internet_script(run_id: int):
    body = _body()
    payload = body.get("payload")
    if not isinstance(payload, dict):
        payload = {
            k: v
            for k, v in body.items()
            if k not in {"source_type", "selected_wan_interface"}
        }
    try:
        plan = _svc().generate_internet_script(
            tenant_id=_tid(),
            run_id=run_id,
            source_type=str(body.get("source_type") or "").strip().lower(),
            selected_wan_interface=str(body.get("selected_wan_interface") or "").strip(),
            payload=payload,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "plan": plan})


def setup_wizard_verify_internet(run_id: int):
    body = _body()
    mode, payload = _verification_payload(body)
    try:
        result = _svc().verify_internet(
            tenant_id=_tid(),
            run_id=run_id,
            mode=mode,
            payload=payload,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_generate_vpn_script(run_id: int):
    body = _body()
    payload = body.get("payload")
    if not isinstance(payload, dict):
        payload = dict(body)
    try:
        plan = _svc().generate_vpn_radius_script(
            tenant_id=_tid(),
            run_id=run_id,
            payload=payload,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "plan": plan})


def setup_wizard_router_public_key(run_id: int):
    body = _body()
    try:
        result = _svc().submit_router_public_key(
            tenant_id=_tid(),
            run_id=run_id,
            public_key=str(body.get("public_key") or ""),
            actor=str(body.get("actor") or getattr(g, "admin_username", "") or "wizard"),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "provisioning": result})


def setup_wizard_router_public_key_auto_detect(run_id: int):
    """Auto-fetch the router's WireGuard public key via MikroTik
    API. Removes the copy-paste step that confused operators."""
    body = _body()
    try:
        result = _svc().auto_detect_router_public_key(
            tenant_id=_tid(),
            run_id=run_id,
            router_address=str(body.get("router_address") or ""),
            api_user=str(body.get("api_user") or ""),
            api_password=str(body.get("api_password") or ""),
            api_port=int(body.get("api_port") or 8728),
            api_use_tls=bool(body.get("api_use_tls") or False),
            wg_interface_name=str(
                body.get("wg_interface_name") or "hr-wg"
            ),
            actor=str(
                body.get("actor")
                or getattr(g, "admin_username", "")
                or "wizard"
            ),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "provisioning": result})


def setup_wizard_server_peer_complete(run_id: int):
    """One-button server-peer setup. Two modes:

    * **paste mode** (preferred when the VPS can't reach the
      router — router behind NAT with dynamic IP): the body
      carries `pasted_output` with the operator's
      `/interface wireguard print detail` output. We extract
      the public key locally — no API call to the router.

    * **API mode** (legacy, only works when the router has a
      reachable public IP + API port): the body carries
      `router_address` + API creds. We connect to the router
      via API.

    Both modes converge on the same dry-run + apply + verify
    pipeline."""
    body = _body()
    actor = str(
        body.get("actor")
        or getattr(g, "admin_username", "")
        or "wizard"
    )
    pasted = str(body.get("pasted_output") or "").strip()
    try:
        if pasted:
            # Paste mode — no router connectivity required.
            result = _svc().server_peer_complete_setup_from_paste(
                tenant_id=_tid(),
                run_id=run_id,
                pasted_output=pasted,
                actor=actor,
            )
        else:
            # Legacy API mode — only works for routers with a
            # reachable public IP.
            result = _svc().server_peer_complete_setup(
                tenant_id=_tid(),
                run_id=run_id,
                router_address=str(body.get("router_address") or ""),
                api_user=str(body.get("api_user") or ""),
                api_password=str(body.get("api_password") or ""),
                api_port=int(body.get("api_port") or 8728),
                api_use_tls=bool(body.get("api_use_tls") or False),
                wg_interface_name=str(
                    body.get("wg_interface_name") or "hr-wg"
                ),
                actor=actor,
            )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify(result)


def setup_wizard_server_peer_dry_run(run_id: int):
    try:
        result = _svc().server_peer_dry_run(tenant_id=_tid(), run_id=run_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_server_peer_apply(run_id: int):
    body = _body()
    try:
        result = _svc().server_peer_apply(
            tenant_id=_tid(),
            run_id=run_id,
            confirmation=str(body.get("confirmation") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="server_peer_apply_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") != "blocked", **result}), status


def setup_wizard_server_peer_rollback(run_id: int):
    body = _body()
    try:
        result = _svc().server_peer_rollback(
            tenant_id=_tid(),
            run_id=run_id,
            confirmation=str(body.get("confirmation") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="server_peer_rollback_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") != "blocked", **result}), status


def setup_wizard_server_peer_verify(run_id: int):
    body = _body()
    try:
        result = _svc().server_peer_verify(
            tenant_id=_tid(),
            run_id=run_id,
            output=str(body.get("output") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_server_peer_health(run_id: int):
    body = _body()
    previous = body.get("previous_observation")
    if not isinstance(previous, dict):
        previous = None
    try:
        result = _svc().server_peer_health(
            tenant_id=_tid(),
            run_id=run_id,
            output=str(body.get("output") or ""),
            previous_observation=previous,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "health": result})


def setup_wizard_server_peer_operations(run_id: int):
    try:
        operations = _svc().server_peer_operations(tenant_id=_tid(), run_id=run_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=404, code="not_found")
    return jsonify({"ok": True, "operations": operations})


def setup_wizard_verify_vpn(run_id: int):
    body = _body()
    mode, payload = _verification_payload(body)
    try:
        result = _svc().verify_vpn_radius(
            tenant_id=_tid(),
            run_id=run_id,
            mode=mode,
            payload=payload,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_interfaces_candidates(run_id: int):
    body = _body()
    interfaces_raw = body.get("interfaces")
    interfaces: list[InterfaceInfo] | None = None
    if isinstance(interfaces_raw, list):
        parsed: list[InterfaceInfo] = []
        for item in interfaces_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            parsed.append(
                InterfaceInfo(
                    name=name,
                    kind=str(item.get("kind") or "ether"),
                    running=bool(item.get("running", True)),
                )
            )
        interfaces = parsed
    try:
        candidates = _svc().get_interface_candidates(
            tenant_id=_tid(), run_id=run_id, interfaces=interfaces
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "candidates": candidates})


def setup_wizard_generate_hotspot_script(run_id: int):
    body = _body()
    payload = body.get("payload")
    if not isinstance(payload, dict):
        payload = dict(body)
    blocked = body.get("blocked_network_cidrs")
    if not isinstance(blocked, list):
        blocked = []
    try:
        plan = _svc().generate_hotspot_script(
            tenant_id=_tid(),
            run_id=run_id,
            mode=str(body.get("mode") or "smart"),
            payload=payload,
            blocked_network_cidrs=[str(x) for x in blocked],
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "plan": plan})


def setup_wizard_verify_hotspot(run_id: int):
    body = _body()
    mode, payload = _verification_payload(body)
    try:
        result = _svc().verify_hotspot(
            tenant_id=_tid(),
            run_id=run_id,
            mode=mode,
            payload=payload,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_generate_broadband_script(run_id: int):
    body = _body()
    payload = body.get("payload")
    if not isinstance(payload, dict):
        payload = dict(body)
    blocked = body.get("blocked_network_cidrs")
    if not isinstance(blocked, list):
        blocked = []
    try:
        plan = _svc().generate_broadband_script(
            tenant_id=_tid(),
            run_id=run_id,
            mode=str(body.get("mode") or "smart"),
            payload=payload,
            blocked_network_cidrs=[str(x) for x in blocked],
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "plan": plan})


def setup_wizard_verify_broadband(run_id: int):
    body = _body()
    mode, payload = _verification_payload(body)
    try:
        result = _svc().verify_broadband(
            tenant_id=_tid(),
            run_id=run_id,
            mode=mode,
            payload=payload,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_run_summary(run_id: int):
    try:
        summary = _svc().get_run_summary(tenant_id=_tid(), run_id=run_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=404, code="not_found")
    return jsonify({"ok": True, **summary})


def setup_wizard_dry_run(run_id: int, step_key: str):
    try:
        result = _svc().dry_run_step(tenant_id=_tid(), run_id=run_id, step_key=step_key)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_apply(run_id: int, step_key: str):
    body = _body()
    try:
        result = _svc().apply_step(
            tenant_id=_tid(),
            run_id=run_id,
            step_key=step_key,
            confirmation=str(body.get("confirmation") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="apply_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") not in {"blocked", "failed"}, **result}), status


def setup_wizard_rollback(run_id: int, step_key: str):
    body = _body()
    try:
        result = _svc().rollback_step(
            tenant_id=_tid(),
            run_id=run_id,
            step_key=step_key,
            confirmation=str(body.get("confirmation") or ""),
            preview=bool(body.get("preview", False)),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="rollback_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") not in {"blocked", "failed"}, **result}), status


def setup_wizard_operations(run_id: int):
    step_key = request.args.get("step_key") or None
    try:
        operations = _svc().list_operations(
            tenant_id=_tid(),
            run_id=run_id,
            step_key=step_key,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "operations": operations})


def setup_wizard_inventory(run_id: int):
    body = _body()
    try:
        snapshot = _svc().collect_router_inventory(
            tenant_id=_tid(),
            run_id=run_id,
            output=str(body.get("output") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "snapshot": snapshot})


def setup_wizard_inventory_latest(run_id: int):
    snapshot = _svc().latest_router_snapshot(tenant_id=_tid(), run_id=run_id)
    return jsonify({"ok": True, "snapshot": snapshot})


def setup_wizard_orchestrate_hotspot(run_id: int):
    body = _body()
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else dict(body)
    try:
        result = _svc().plan_hotspot_orchestration(
            tenant_id=_tid(),
            run_id=run_id,
            mode=str(body.get("mode") or "smart"),
            payload=payload,
            manual_override=bool(body.get("manual_override", False)),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_orchestrate_broadband(run_id: int):
    body = _body()
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else dict(body)
    try:
        result = _svc().plan_broadband_orchestration(
            tenant_id=_tid(),
            run_id=run_id,
            mode=str(body.get("mode") or "smart"),
            payload=payload,
            manual_override=bool(body.get("manual_override", False)),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **result})


def setup_wizard_added_services_catalog():
    return jsonify({"ok": True, **_svc().added_services_catalog()})


def setup_wizard_added_services_plan(run_id: int):
    body = _body()
    service_key = str(body.get("service_key") or "").strip()
    inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
    try:
        plan = _svc().plan_added_service(
            tenant_id=_tid(),
            run_id=run_id,
            service_key=service_key,
            inputs=inputs,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    if plan.get("plan_status") == "rejected":
        return jsonify({"ok": False, "code": "unknown_added_service", "plan": plan}), 400
    return jsonify({"ok": True, "plan": plan})


def setup_wizard_added_services_dry_run(run_id: int):
    body = _body()
    service_key = str(body.get("service_key") or "").strip()
    inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
    try:
        result = _svc().dry_run_added_service(
            tenant_id=_tid(),
            run_id=run_id,
            service_key=service_key,
            inputs=inputs,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="dry_run_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") != "blocked", **result}), status


def setup_wizard_added_services_apply(run_id: int):
    body = _body()
    try:
        result = _svc().apply_step(
            tenant_id=_tid(),
            run_id=run_id,
            step_key="added-services",
            confirmation=str(body.get("confirmation") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="apply_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") not in {"blocked", "failed"}, **result}), status


def setup_wizard_added_services_verify(run_id: int):
    body = _body()
    service_key = str(body.get("service_key") or "").strip()
    try:
        result = _svc().verify_added_service(
            tenant_id=_tid(),
            run_id=run_id,
            service_key=service_key,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="verification_blocked")
    return jsonify({"ok": True, **result})


def setup_wizard_support_bundle(run_id: int):
    try:
        bundle = _svc().support_bundle(tenant_id=_tid(), run_id=run_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=404, code="not_found")
    return jsonify({"ok": True, "bundle": bundle})


def setup_wizard_health(run_id: int):
    try:
        health = _svc().health(tenant_id=_tid(), run_id=run_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=404, code="not_found")
    return jsonify({"ok": True, "health": health})


def setup_wizard_pilot_drill(run_id: int):
    step_key = str(request.args.get("step") or "internet")
    try:
        drill = _svc().pilot_drill(tenant_id=_tid(), run_id=run_id, step_key=step_key)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=404, code="not_found")
    return jsonify({"ok": True, "pilot_drill": drill})


def setup_wizard_recovery(run_id: int):
    try:
        recovery = _svc().recovery(tenant_id=_tid(), run_id=run_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=404, code="not_found")
    return jsonify({"ok": True, "recovery": recovery})


def setup_wizard_recovery_resume(run_id: int):
    try:
        result = _svc().recovery_resume(tenant_id=_tid(), run_id=run_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=404, code="not_found")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") != "blocked", **result}), status


def setup_wizard_recovery_retry_verification(run_id: int):
    body = _body()
    mode, payload = _verification_payload(body)
    try:
        result = _svc().recovery_retry_verification(
            tenant_id=_tid(),
            run_id=run_id,
            step_key=str(body.get("step_key") or ""),
            mode=mode,
            payload=payload,
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="retry_verification_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") != "blocked", **result}), status


def setup_wizard_recovery_regenerate_script(run_id: int):
    body = _body()
    try:
        result = _svc().recovery_regenerate_script(
            tenant_id=_tid(),
            run_id=run_id,
            step_key=str(body.get("step_key") or "vpn_radius"),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="regenerate_script_blocked")
    status = 409 if result.get("status") == "blocked" else 200
    return jsonify({"ok": result.get("status") != "blocked", **result}), status


def setup_wizard_recovery_abandon_step(run_id: int):
    body = _body()
    try:
        result = _svc().recovery_abandon_step(
            tenant_id=_tid(),
            run_id=run_id,
            step_key=str(body.get("step_key") or "current_step"),
            reason=str(body.get("reason") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=400, code="abandon_step_blocked")
    return jsonify({"ok": True, **result})


def setup_wizard_recovery_retire_router(run_id: int):
    body = _body()
    try:
        result = _svc().recovery_retire_router(
            tenant_id=_tid(),
            run_id=run_id,
            reason=str(body.get("reason") or ""),
        )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=409, code="retire_router_blocked")
    return jsonify({"ok": True, **result})
