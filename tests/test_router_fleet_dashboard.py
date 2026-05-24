from __future__ import annotations

import json
import os
import secrets

import pytest

from app.radius.db.connection import db, reset_for_tests, transaction
from app.radius.services.setup_wizard import STEP_INTERNET_VERIFICATION, get_setup_wizard_service
from app.radius.services.setup_wizard_fleet import RouterFleetProvisioningService
from app.radius.services.setup_wizard_provisioning_orchestrator import RouterProvisioningOrchestrator
from app.radius.services.setup_wizard_router_provisioning import RouterProvisioningService


VALID_KEY = "C" * 43 + "="


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-fleet-" + secrets.token_hex(8)
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


def _new_run() -> dict:
    return get_setup_wizard_service().create_run(tenant_id=1, actor="fleet-test")


def _verified_run() -> dict:
    run = _new_run()
    get_setup_wizard_service().mark_verified(
        tenant_id=1,
        run_id=run["id"],
        step_key=STEP_INTERNET_VERIFICATION,
    )
    return run


def _reserve(label: str = "") -> dict:
    run = _new_run()
    return RouterProvisioningService().reserve_for_run(
        tenant_id=1,
        wizard_run_id=run["id"],
        router_label=label or f"Branch {run['id']}",
        router_identity=f"branch-{run['id']}",
    )


def _vpn_plan(label: str = "Fleet Router") -> dict:
    run = _verified_run()
    return get_setup_wizard_service().generate_vpn_radius_script(
        tenant_id=1,
        run_id=run["id"],
        payload={"router_label": label, "router_identity": f"fleet-{run['id']}"},
    )


def test_fleet_service_aggregates_zero_routers(app):
    with app.app_context():
        summary = RouterFleetProvisioningService().summary(tenant_id=1)

    assert summary["metrics"]["total_routers"] == 0
    assert summary["allocation_usage"]["used"] == 0
    assert summary["allocation_usage"]["next_available"] == "10.10.0.2"


def test_fleet_service_aggregates_50_routers(app):
    with app.app_context():
        for i in range(50):
            _reserve(label=f"Branch {i + 1}")
        summary = RouterFleetProvisioningService().summary(tenant_id=1)

    assert summary["metrics"]["total_routers"] == 50
    assert summary["metrics"]["reserved"] == 50
    assert summary["allocation_usage"]["used"] == 50
    assert summary["allocation_usage"]["remaining"] == 203
    assert summary["allocation_usage"]["next_available"] == "10.10.0.52"


def test_failed_routers_appear_in_action_needed(app):
    with app.app_context():
        reservation = _reserve(label="Broken branch")
        with transaction() as conn:
            conn.execute(
                """
                UPDATE router_provisioning_registry
                SET status='failed', lifecycle_state='failed', failure_reason='vpn timeout'
                WHERE id=?
                """,
                (reservation["id"],),
            )
        summary = RouterFleetProvisioningService().summary(tenant_id=1)

    assert summary["metrics"]["failed"] == 1
    assert summary["action_needed"][0]["router_label"] == "Broken-branch"
    assert summary["action_needed"][0]["next_action"] == "open_recovery"


def test_retired_filter_excludes_when_requested(app):
    with app.app_context():
        reservation = _reserve(label="Old branch")
        with transaction() as conn:
            conn.execute(
                "UPDATE router_provisioning_registry SET status='retired', lifecycle_state='retired' WHERE id=?",
                (reservation["id"],),
            )
        included = RouterFleetProvisioningService().summary(tenant_id=1, include_retired=True)
        excluded = RouterFleetProvisioningService().summary(tenant_id=1, include_retired=False)

    assert included["metrics"]["total_routers"] == 1
    assert excluded["metrics"]["total_routers"] == 0


def test_fleet_route_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard/fleet")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "data-setup-wizard-fleet" in html
    assert "setup_wizard_fleet.css" in html
    assert "setup_wizard_fleet.js" in html
    assert "data-swfleet-action-needed" in html


def test_data_endpoint_masks_secret_refs(app):
    with app.app_context():
        _reserve(label="Masked branch")
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard/fleet/data")
        payload = json.dumps(res.get_json(), ensure_ascii=False)

    assert res.status_code == 200
    assert "radius-secret-ref" not in payload
    assert "api-password-ref" not in payload
    assert "wireguard_private_key_ref" not in payload


def test_router_detail_masks_public_and_private_material(app):
    with app.app_context():
        plan = _vpn_plan("Peer branch")
        registry_id = plan["router_provisioning"]["id"]
        RouterProvisioningOrchestrator().submit_router_public_key(
            tenant_id=1,
            registry_id=registry_id,
            public_key=VALID_KEY,
        )
    with app.test_client() as client:
        _auth_session(client)
        res = client.get(f"/admin/radius/setup-wizard/fleet/router/{registry_id}")
        payload = json.dumps(res.get_json(), ensure_ascii=False)

    assert res.status_code == 200
    assert VALID_KEY not in payload
    assert "private" not in payload.lower()
    assert "radius-secret-ref" not in payload


def test_resume_delegates_to_recovery_service(app):
    with app.app_context():
        plan = _vpn_plan("Resume branch")
        registry_id = plan["router_provisioning"]["id"]
    with app.test_client() as client:
        _auth_session(client)
        res = _post(client, f"/admin/radius/setup-wizard/fleet/router/{registry_id}/resume", {})
        body = res.get_json()

    assert res.status_code == 200
    assert body["status"] == "ready"
    assert body["next_safe_step"] == "router_public_key"
    assert body["analysis"]["recovery_state"] == "peer_key_missing"


def test_no_live_apply_surface_in_fleet_ui(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard/fleet").get_data(as_text=True).lower()

    assert "data-swfleet-apply" not in html
    assert "one click" not in html
