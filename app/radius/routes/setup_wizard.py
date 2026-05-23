from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, g, jsonify, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..services.setup_wizard import (
    STEP_BROADBAND_VERIFICATION,
    STEP_HOTSPOT_VERIFICATION,
    STEP_INTERNET_VERIFICATION,
    STEP_VPN_RADIUS_VERIFICATION,
    SetupWizardValidationError,
    get_setup_wizard_service,
)
from ..services.setup_wizard_interface_contract import InterfaceInfo
from ..services.setup_wizard_verification import SetupDiagnosticsService


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _svc():
    return get_setup_wizard_service()


def _body() -> dict[str, Any]:
    if request.is_json:
        return dict(request.get_json(silent=True) or {})
    return request.form.to_dict(flat=True)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _json_error(message: str, status: int = 400, code: str = "validation_error"):
    return jsonify({"ok": False, "error": message, "code": code}), status


def register_setup_wizard_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/setup-wizard", "setup_wizard_page", setup_wizard_page, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs", "setup_wizard_create_run", setup_wizard_create_run, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>", "setup_wizard_get_run", setup_wizard_get_run, methods=["GET"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/internet-source", "setup_wizard_set_internet_source", setup_wizard_set_internet_source, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/generate-internet-script", "setup_wizard_generate_internet_script", setup_wizard_generate_internet_script, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/verify-internet", "setup_wizard_verify_internet", setup_wizard_verify_internet, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/generate-vpn-radius-script", "setup_wizard_generate_vpn_script", setup_wizard_generate_vpn_script, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/verify-vpn-radius", "setup_wizard_verify_vpn", setup_wizard_verify_vpn, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/interfaces/candidates", "setup_wizard_interfaces_candidates", setup_wizard_interfaces_candidates, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/generate-hotspot-script", "setup_wizard_generate_hotspot_script", setup_wizard_generate_hotspot_script, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/verify-hotspot", "setup_wizard_verify_hotspot", setup_wizard_verify_hotspot, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/generate-broadband-script", "setup_wizard_generate_broadband_script", setup_wizard_generate_broadband_script, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/verify-broadband", "setup_wizard_verify_broadband", setup_wizard_verify_broadband, methods=["POST"])
    bp.add_url_rule("/setup-wizard/runs/<int:run_id>/summary", "setup_wizard_run_summary", setup_wizard_run_summary, methods=["GET"])


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


def _verify_step(*, run_id: int, step_key: str):
    body = _body()
    ok = _coerce_bool(body.get("ok"), True)
    result = body.get("verification_result")
    if not isinstance(result, dict):
        result = {}
    diagnostics = body.get("diagnostics")
    if isinstance(diagnostics, list):
        result["diagnostics"] = diagnostics
    try:
        if ok:
            step = _svc().mark_verified(
                tenant_id=_tid(),
                run_id=run_id,
                step_key=step_key,
                verification_result=result or {"ok": True},
            )
        else:
            step = _svc().mark_failed(
                tenant_id=_tid(),
                run_id=run_id,
                step_key=step_key,
                error_message=str(body.get("error_message") or "verification failed"),
                verification_result=result or {"ok": False},
            )
    except SetupWizardValidationError as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, "step": step})


def setup_wizard_verify_internet(run_id: int):
    return _verify_step(run_id=run_id, step_key=STEP_INTERNET_VERIFICATION)


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


def setup_wizard_verify_vpn(run_id: int):
    return _verify_step(run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION)


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
    return _verify_step(run_id=run_id, step_key=STEP_HOTSPOT_VERIFICATION)


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
    return _verify_step(run_id=run_id, step_key=STEP_BROADBAND_VERIFICATION)


def setup_wizard_run_summary(run_id: int):
    try:
        summary = _svc().get_run_summary(tenant_id=_tid(), run_id=run_id)
    except SetupWizardValidationError as exc:
        return _json_error(str(exc), status=404, code="not_found")
    return jsonify({"ok": True, **summary})
