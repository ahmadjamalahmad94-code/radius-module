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


# ─── Registration ───────────────────────────────────────────


def register_setup_wizard_v3_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/setup-wizard-v3",
        "setup_wizard_v3_page",
        setup_wizard_v3_page,
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
