from __future__ import annotations

import os
import secrets
import sqlite3

import pytest

from app.radius.db.connection import db, reset_for_tests, transaction
from app.radius.services.setup_wizard import (
    STEP_INTERNET_VERIFICATION,
    SetupWizardValidationError,
    get_setup_wizard_service,
)
from app.radius.services.setup_wizard_router_provisioning import RouterProvisioningService


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-prov-" + secrets.token_hex(8)
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

    created = create_app()
    created.config["TEST_API_TOKEN"] = token
    return created


def _new_run() -> dict:
    return get_setup_wizard_service().create_run(tenant_id=1, actor="qa")


def test_first_router_gets_first_usable_vpn_ip(app):
    with app.app_context():
        run = _new_run()
        reservation = RouterProvisioningService().reserve_for_run(
            tenant_id=1,
            wizard_run_id=run["id"],
            router_label="Branch 1",
        )

    assert reservation["router_vpn_ip"] == "10.10.0.2"
    assert reservation["server_vpn_ip"] == "10.10.0.1"
    assert reservation["wireguard_peer_name"] == "hr-peer-0001"
    assert reservation["api_username"] == "hr-api-0001"


def test_second_router_gets_next_vpn_ip(app):
    with app.app_context():
        svc = RouterProvisioningService()
        first = svc.reserve_for_run(tenant_id=1, wizard_run_id=_new_run()["id"])
        second = svc.reserve_for_run(tenant_id=1, wizard_run_id=_new_run()["id"])

    assert first["router_vpn_ip"] == "10.10.0.2"
    assert second["router_vpn_ip"] == "10.10.0.3"
    assert second["wireguard_peer_name"] == "hr-peer-0002"
    assert second["api_username"] == "hr-api-0002"


def test_server_ip_is_never_allocated_to_router(app):
    with app.app_context():
        reservations = [
            RouterProvisioningService().reserve_for_run(
                tenant_id=1,
                wizard_run_id=_new_run()["id"],
            )
            for _ in range(4)
        ]

    assert "10.10.0.1" not in {item["router_vpn_ip"] for item in reservations}


def test_duplicate_active_ip_allocation_is_prevented(app):
    with app.app_context():
        reservation = RouterProvisioningService().reserve_for_run(
            tenant_id=1,
            wizard_run_id=_new_run()["id"],
        )
        with pytest.raises(sqlite3.IntegrityError):
            db().execute(
                """
                INSERT INTO router_ip_allocations (
                  registry_id, tenant_id, pool_name, ip_address,
                  allocation_type, status, created_at
                ) VALUES (?, 1, '10.10.0.0/24', '10.10.0.2',
                  'router_vpn', 'reserved', 'now')
                """,
                (reservation["id"],),
            )


def test_same_wizard_run_reuses_existing_reservation(app):
    with app.app_context():
        svc = RouterProvisioningService()
        run = _new_run()
        first = svc.reserve_for_run(tenant_id=1, wizard_run_id=run["id"])
        second = svc.reserve_for_run(tenant_id=1, wizard_run_id=run["id"])

    assert first["id"] == second["id"]
    assert first["router_vpn_ip"] == second["router_vpn_ip"]


def test_failed_reservation_can_be_released_and_retried(app):
    with app.app_context():
        svc = RouterProvisioningService()
        run = _new_run()
        first = svc.reserve_for_run(tenant_id=1, wizard_run_id=run["id"])
        with transaction() as conn:
            conn.execute(
                "UPDATE router_provisioning_registry SET status='failed' WHERE id=?",
                (first["id"],),
            )
        released = svc.release_reservation(
            tenant_id=1,
            registry_id=first["id"],
            reason="lab retry",
        )
        retry = svc.reserve_for_run(tenant_id=1, wizard_run_id=run["id"])

    assert released["status"] == "retired"
    assert retry["router_vpn_ip"] == "10.10.0.2"
    assert retry["id"] != first["id"]


def test_vpn_radius_script_uses_allocated_values_not_placeholders(app):
    with app.app_context():
        svc = get_setup_wizard_service()
        run = _new_run()
        svc.mark_verified(
            tenant_id=1,
            run_id=run["id"],
            step_key=STEP_INTERNET_VERIFICATION,
        )
        plan = svc.generate_vpn_radius_script(
            tenant_id=1,
            run_id=run["id"],
            payload={"router_label": "Branch 1"},
        )
        script = plan["script_text"]

    assert "10.10.0.2/24" in script
    assert "10.10.0.1/32" in script
    assert "hr-peer-0001" in script
    assert "hr-api-0001" in script
    assert "HOBERADIUS_ROUTER:" in script
    assert "<run_id>" not in script
    assert "10.10.0.3" not in script


def test_run_summary_masks_provisioning_secrets(app):
    with app.app_context():
        svc = get_setup_wizard_service()
        run = _new_run()
        svc.mark_verified(
            tenant_id=1,
            run_id=run["id"],
            step_key=STEP_INTERNET_VERIFICATION,
        )
        svc.generate_vpn_radius_script(
            tenant_id=1,
            run_id=run["id"],
            payload={"router_label": "Branch 1"},
        )
        summary = svc.get_run_summary(tenant_id=1, run_id=run["id"])

    provisioning = summary["router_provisioning"]
    assert provisioning["masked_sensitive_values"]["radius_secret"] == "***"
    assert provisioning["masked_sensitive_values"]["api_password"] == "***"
    assert provisioning["api_password_ref"].startswith("api-password-ref-")
    assert "Secret!123" not in str(summary)


def test_provisioning_does_not_introduce_live_apply(app):
    with app.app_context():
        svc = get_setup_wizard_service()
        run = _new_run()
        svc.mark_verified(
            tenant_id=1,
            run_id=run["id"],
            step_key=STEP_INTERNET_VERIFICATION,
        )
        plan = svc.generate_vpn_radius_script(
            tenant_id=1,
            run_id=run["id"],
            payload={"router_label": "Branch 1"},
        )

    assert "script_text" in plan
    assert "applied_at" not in plan
    assert "mikrotik_execution_result" not in plan


def test_blocked_vpn_generation_does_not_reserve_router(app):
    with app.app_context():
        svc = get_setup_wizard_service()
        run = _new_run()
        with pytest.raises(SetupWizardValidationError):
            svc.generate_vpn_radius_script(
                tenant_id=1,
                run_id=run["id"],
                payload={"router_label": "Blocked"},
            )
        reservation_count = db().execute(
            "SELECT COUNT(*) AS n FROM router_provisioning_registry"
        ).fetchone()["n"]

    assert reservation_count == 0
