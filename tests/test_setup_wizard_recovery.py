from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta

import pytest

from app.radius.db.connection import db, reset_for_tests, transaction
from app.radius.services.setup_wizard import (
    STEP_INTERNET_VERIFICATION,
    STEP_VPN_RADIUS_VERIFICATION,
    SetupWizardValidationError,
    get_setup_wizard_service,
)
from app.radius.services.setup_wizard_recovery import SetupWizardRecoveryService


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-recovery-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_VPN_POOL", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "187.77.70.18:51820")
    reset_for_tests(os.path.join(tmp_path, "test.db"))
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "qa_admin"
        sess["admin_name"] = "QA Admin"
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "test-csrf"


def _post(client, url: str, payload: dict):
    return client.post(url, json=payload, headers={"X-CSRFToken": "test-csrf"})


def _verified_run() -> dict:
    svc = get_setup_wizard_service()
    run = svc.create_run(tenant_id=1, actor="qa")
    svc.mark_verified(tenant_id=1, run_id=run["id"], step_key=STEP_INTERNET_VERIFICATION)
    return run


def _vpn_plan(run_id: int) -> dict:
    return get_setup_wizard_service().generate_vpn_radius_script(
        tenant_id=1,
        run_id=run_id,
        payload={"router_label": f"Recovery {run_id}", "router_identity": f"recovery-{run_id}"},
    )


def _insert_snapshot(run_id: int, *, created_at: str, risk_report: dict | None = None):
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO setup_wizard_router_snapshots (
              wizard_run_id, tenant_id, source, identity_json, interfaces_json,
              addresses_json, routes_json, pools_json, nat_json, radius_json,
              hotspot_json, ppp_json, wireguard_json, risk_report_json, created_at
            ) VALUES (?, 1, 'test', '{}', '[]', '[]', '[]', '[]', '[]', '[]', '[]', '[]', '[]', ?, ?)
            """,
            (int(run_id), json.dumps(risk_report or {}), created_at),
        )


def test_clean_run_returns_clean_resume(app):
    with app.app_context():
        run = get_setup_wizard_service().create_run(tenant_id=1, actor="qa")
        recovery = get_setup_wizard_service().recovery(tenant_id=1, run_id=run["id"])

    assert recovery["recovery_state"] == "clean_resume"
    assert recovery["can_resume"] is True
    assert recovery["next_safe_step"] == "welcome"


def test_missing_public_key_returns_peer_key_missing(app):
    with app.app_context():
        run = _verified_run()
        _vpn_plan(run["id"])
        recovery = get_setup_wizard_service().recovery(tenant_id=1, run_id=run["id"])

    assert recovery["recovery_state"] == "peer_key_missing"
    assert recovery["can_reissue_peer"] is True
    assert recovery["next_safe_step"] == "router_public_key"


def test_failed_vpn_verification_returns_retry_action(app):
    with app.app_context():
        run = _verified_run()
        _vpn_plan(run["id"])
        get_setup_wizard_service().mark_failed(
            tenant_id=1,
            run_id=run["id"],
            step_key=STEP_VPN_RADIUS_VERIFICATION,
            error_message="vpn failed",
            verification_result={"diagnostics": [{"code": "vpn_not_handshaking"}]},
        )
        recovery = get_setup_wizard_service().recovery(tenant_id=1, run_id=run["id"])

    assert recovery["recovery_state"] == "failed_verification"
    assert recovery["can_retry_verification"] is True
    assert any(action["action"] == "retry_verification" for action in recovery["recommended_actions"])


def test_stale_inventory_returns_stale_inventory(app):
    with app.app_context():
        run = get_setup_wizard_service().create_run(tenant_id=1, actor="qa")
        old = (datetime.utcnow() - timedelta(hours=3)).replace(microsecond=0).isoformat() + "Z"
        _insert_snapshot(run["id"], created_at=old)
        recovery = get_setup_wizard_service().recovery(tenant_id=1, run_id=run["id"])

    assert recovery["recovery_state"] == "stale_inventory"
    assert any(problem["code"] == "stale_inventory" for problem in recovery["problems"])


def test_partial_apply_returns_rollback_suggestion(app):
    with app.app_context():
        run = get_setup_wizard_service().create_run(tenant_id=1, actor="qa")
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO setup_wizard_operations (
                  wizard_run_id, tenant_id, step_key, operation_type, operation_order,
                  status, command_preview, rollback_command, created_at
                ) VALUES
                (?, 1, 'hotspot', 'add', 1, 'applied',
                 '/ip hotspot add comment="HOBERADIUS_SETUP:1:hotspot"',
                 '/ip hotspot remove [find where comment="HOBERADIUS_SETUP:1:hotspot"]', 'now'),
                (?, 1, 'hotspot', 'add', 2, 'failed',
                 '/ip pool add comment="HOBERADIUS_SETUP:1:hotspot"', '', 'now')
                """,
                (run["id"], run["id"]),
            )
        recovery = get_setup_wizard_service().recovery(tenant_id=1, run_id=run["id"])

    assert recovery["recovery_state"] == "partial_apply"
    assert any(action["action"] == "review_rollback" for action in recovery["recommended_actions"])


def test_regenerate_script_preserves_allocation(app):
    with app.app_context():
        run = _verified_run()
        first = _vpn_plan(run["id"])
        first_registry = first["router_provisioning"]
        result = get_setup_wizard_service().recovery_regenerate_script(
            tenant_id=1,
            run_id=run["id"],
            step_key="vpn_radius",
        )
        after = result["plan"]["router_provisioning"]

    assert result["status"] == "generated"
    assert result["allocation_preserved"] is True
    assert after["id"] == first_registry["id"]
    assert after["router_vpn_ip"] == first_registry["router_vpn_ip"]


def test_reissue_blocked_if_peer_applied(app):
    with app.app_context():
        run = _verified_run()
        plan = _vpn_plan(run["id"])
        with transaction() as conn:
            conn.execute(
                "UPDATE prepared_wireguard_peers SET status='applied' WHERE id=?",
                (int(plan["prepared_wireguard_peer"]["id"]),),
            )
        result = SetupWizardRecoveryService(wizard_service=get_setup_wizard_service()).reissue_router_credentials(
            tenant_id=1,
            run_id=run["id"],
            reason="operator asked",
        )

    assert result["status"] == "blocked"
    assert result["reason"] == "peer_already_applied"


def test_abandon_requires_reason(app):
    with app.app_context():
        run = get_setup_wizard_service().create_run(tenant_id=1, actor="qa")
        with pytest.raises(SetupWizardValidationError):
            get_setup_wizard_service().recovery_abandon_step(
                tenant_id=1,
                run_id=run["id"],
                step_key="vpn_radius_verification",
                reason="",
            )


def test_retire_marks_terminal_and_blocks_normal_resume(app):
    with app.app_context():
        run = _verified_run()
        _vpn_plan(run["id"])
        retired = get_setup_wizard_service().recovery_retire_router(
            tenant_id=1,
            run_id=run["id"],
            reason="lab abandoned",
        )
        recovery = get_setup_wizard_service().recovery(tenant_id=1, run_id=run["id"])

    assert retired["status"] == "retired"
    assert recovery["recovery_state"] == "terminal_retired"
    assert recovery["can_resume"] is False


def test_v2_renders_recovery_panel_placeholder(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "data-swv2-recovery-panel" in html
    assert "data-swv2-recovery-check" in html
    assert "data-swv2-recovery-regenerate" in html


def test_recovery_and_support_bundle_do_not_leak_secrets(app):
    with app.app_context():
        run = get_setup_wizard_service().create_run(tenant_id=1, actor="qa")
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO setup_wizard_operations (
                  wizard_run_id, tenant_id, step_key, operation_type, operation_order,
                  status, command_preview, rollback_command, result_json, created_at
                ) VALUES (?, 1, 'internet', 'add', 1, 'failed',
                  '/interface pppoe-client add password="secret-pass" comment="HOBERADIUS_SETUP:1:internet"',
                  '',
                  '{"radius_secret": "top-secret", "api_password": "secret-pass"}',
                  'now')
                """,
                (run["id"],),
            )
        recovery = get_setup_wizard_service().recovery(tenant_id=1, run_id=run["id"])
        bundle = get_setup_wizard_service().support_bundle(tenant_id=1, run_id=run["id"])

    serialized = json.dumps({"recovery": recovery, "bundle": bundle}, ensure_ascii=False)
    assert "top-secret" not in serialized
    assert "secret-pass" not in serialized
