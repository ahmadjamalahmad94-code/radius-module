from __future__ import annotations

import os
import secrets

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.services.setup_wizard import (
    STEP_INTERNET_VERIFICATION,
    SetupWizardValidationError,
    get_setup_wizard_service,
)
from app.radius.services.setup_wizard_provisioning_orchestrator import (
    PreparedWireGuardPeerService,
    RouterProvisioningOrchestrator,
)
from app.radius.services.setup_wizard_router_provisioning import RouterProvisioningService


VALID_KEY_1 = "A" * 43 + "="
VALID_KEY_2 = "B" * 43 + "="


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-orch-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    # حارس دورة حياة الترخيص يقفل اللوحة على قاعدة جديدة بلا لقطة
    # ترخيص؛ تجاوزه في الاختبارات يحتاج العلمين معًا (راجع
    # license_lifecycle._test_bypass_active وتعليق tests/conftest.py).
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_VPN_POOL", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "187.77.70.18:51820")
    reset_for_tests(os.path.join(tmp_path, "test.db"))
    from app import create_app

    return create_app()


def _run_with_internet_verified() -> dict:
    svc = get_setup_wizard_service()
    run = svc.create_run(tenant_id=1, actor="qa")
    svc.mark_verified(
        tenant_id=1,
        run_id=run["id"],
        step_key=STEP_INTERNET_VERIFICATION,
    )
    return run


def _vpn_plan_for_run(run_id: int) -> dict:
    return get_setup_wizard_service().generate_vpn_radius_script(
        tenant_id=1,
        run_id=run_id,
        payload={"router_label": f"Branch {run_id}", "router_identity": f"branch-{run_id}"},
    )


def test_vpn_generation_prepares_lifecycle_and_peer(app):
    with app.app_context():
        run = _run_with_internet_verified()
        plan = _vpn_plan_for_run(run["id"])
        lifecycle = plan["provisioning_lifecycle"]
        peer = plan["prepared_wireguard_peer"]

    assert lifecycle["current_state"] == "waiting_router_key"
    assert peer["status"] == "waiting_router_key"
    assert plan["router_provisioning"]["router_vpn_ip"] == "10.10.0.2"
    assert 'address="10.10.0.2/24"' in plan["script_text"]
    assert peer["allowed_ips"] == "10.10.0.2/32"
    assert peer["allowed_ips"] == f'{plan["router_provisioning"]["router_vpn_ip"]}/32'
    assert "router_public_key" not in peer


def test_router_public_key_exchange_moves_to_peer_ready(app):
    with app.app_context():
        run = _run_with_internet_verified()
        plan = _vpn_plan_for_run(run["id"])
        registry_id = plan["router_provisioning"]["id"]
        result = RouterProvisioningOrchestrator().submit_router_public_key(
            tenant_id=1,
            registry_id=registry_id,
            public_key=VALID_KEY_1,
        )

    assert result["current_state"] == "peer_ready"
    assert result["prepared_wireguard_peer"]["status"] == "ready_to_apply"
    assert result["prepared_wireguard_peer"]["router_public_key_masked"] == "AAAAAA...AAAAA="


def test_duplicate_router_public_key_is_rejected(app):
    with app.app_context():
        run1 = _run_with_internet_verified()
        plan1 = _vpn_plan_for_run(run1["id"])
        run2 = _run_with_internet_verified()
        plan2 = _vpn_plan_for_run(run2["id"])
        orch = RouterProvisioningOrchestrator()
        orch.submit_router_public_key(
            tenant_id=1,
            registry_id=plan1["router_provisioning"]["id"],
            public_key=VALID_KEY_1,
        )
        with pytest.raises(SetupWizardValidationError, match="Public key is already assigned to another router"):
            orch.submit_router_public_key(
                tenant_id=1,
                registry_id=plan2["router_provisioning"]["id"],
                public_key=VALID_KEY_1,
            )


def test_peer_collision_is_prevented_by_unique_active_peer_name(app):
    with app.app_context():
        run1 = _run_with_internet_verified()
        plan1 = _vpn_plan_for_run(run1["id"])
        run2 = _run_with_internet_verified()
        plan2 = _vpn_plan_for_run(run2["id"])
        with pytest.raises(Exception):
            db().execute(
                """
                UPDATE prepared_wireguard_peers
                SET peer_name=?
                WHERE registry_id=?
                """,
                (
                    plan1["prepared_wireguard_peer"]["peer_name"],
                    plan2["router_provisioning"]["id"],
                ),
            )


@pytest.mark.parametrize(
    ("count", "last_ip", "last_peer"),
    [
        (5, "10.10.0.6", "hr-peer-0005"),
        (25, "10.10.0.26", "hr-peer-0025"),
        (50, "10.10.0.51", "hr-peer-0050"),
    ],
)
def test_multi_router_simulation_has_unique_allocations(app, count, last_ip, last_peer):
    with app.app_context():
        service = RouterProvisioningService()
        reservations = []
        for _ in range(count):
            run = get_setup_wizard_service().create_run(tenant_id=1, actor="qa")
            reservations.append(service.reserve_for_run(tenant_id=1, wizard_run_id=run["id"]))

    assert len({item["router_vpn_ip"] for item in reservations}) == count
    assert len({item["wireguard_peer_name"] for item in reservations}) == count
    assert reservations[-1]["router_vpn_ip"] == last_ip
    assert reservations[-1]["wireguard_peer_name"] == last_peer


def test_same_run_reuses_reservation_and_prepared_peer(app):
    with app.app_context():
        run = _run_with_internet_verified()
        first = _vpn_plan_for_run(run["id"])
        second_reservation = RouterProvisioningService().reserve_for_run(
            tenant_id=1,
            wizard_run_id=run["id"],
        )
        peer = PreparedWireGuardPeerService().latest_for_run(
            tenant_id=1,
            wizard_run_id=run["id"],
        )

    assert second_reservation["id"] == first["router_provisioning"]["id"]
    assert second_reservation["router_vpn_ip"] == first["router_provisioning"]["router_vpn_ip"]
    assert peer["id"] == first["prepared_wireguard_peer"]["id"]
    assert peer["allowed_ips"] == f'{second_reservation["router_vpn_ip"]}/32'


def test_prepared_peer_reconciles_to_reservation_router_vpn_ip_on_retry(app):
    with app.app_context():
        run = _run_with_internet_verified()
        first = _vpn_plan_for_run(run["id"])
        db().execute(
            """
            UPDATE prepared_wireguard_peers
            SET router_vpn_ip='10.10.0.99', allowed_ips='10.10.0.99/32'
            WHERE id=?
            """,
            (int(first["prepared_wireguard_peer"]["id"]),),
        )
        db().commit()
        retry = _vpn_plan_for_run(run["id"])

    reservation_ip = retry["router_provisioning"]["router_vpn_ip"]
    assert reservation_ip == first["router_provisioning"]["router_vpn_ip"]
    assert retry["prepared_wireguard_peer"]["router_vpn_ip"] == reservation_ip
    assert retry["prepared_wireguard_peer"]["allowed_ips"] == f"{reservation_ip}/32"


def test_reissue_status_is_safe_and_does_not_duplicate_peer(app):
    with app.app_context():
        run = _run_with_internet_verified()
        plan = _vpn_plan_for_run(run["id"])
        status = RouterProvisioningOrchestrator().reissue_router_script(
            tenant_id=1,
            registry_id=plan["router_provisioning"]["id"],
        )
        peer_count = db().execute(
            "SELECT COUNT(*) AS n FROM prepared_wireguard_peers"
        ).fetchone()["n"]

    assert status["current_state"] == "waiting_router_key"
    assert peer_count == 1


def test_no_secret_leak_in_provisioning_summary(app):
    with app.app_context():
        run = _run_with_internet_verified()
        _vpn_plan_for_run(run["id"])
        summary = get_setup_wizard_service().get_run_summary(tenant_id=1, run_id=run["id"])

    serialized = str(summary)
    assert "api-password-ref-" in serialized
    assert "radius-secret-ref-" in serialized
    assert "secret-pass" not in serialized
    assert VALID_KEY_1 not in serialized


def test_v2_provisioning_card_and_key_exchange_route_render(app):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["admin_id"] = 1
            sess["tenant_id"] = 1
            sess["_csrf_token"] = "test-csrf"
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert 'data-swv2-provisioning="lifecycle_state"' in html
    assert 'data-swv2-provisioning="peer_status"' in html
    assert "data-swv2-auto-public-key" in html
    assert "data-swv2-router-public-key" not in html


def test_public_key_endpoint_returns_masked_peer(app):
    with app.app_context():
        run = _run_with_internet_verified()
        _vpn_plan_for_run(run["id"])
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["admin_id"] = 1
            sess["tenant_id"] = 1
            sess["_csrf_token"] = "test-csrf"
        res = client.post(
            f"/admin/radius/setup-wizard/runs/{run['id']}/router-public-key",
            json={"public_key": VALID_KEY_2},
            headers={"X-CSRFToken": "test-csrf"},
        )
        body = res.get_json()

    assert res.status_code == 200
    assert body["provisioning"]["current_state"] == "peer_ready"
    assert body["provisioning"]["prepared_wireguard_peer"]["router_public_key_masked"] == "BBBBBB...BBBBB="
