from __future__ import annotations

import os
import secrets

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.services.setup_wizard_common import SetupWizardValidationError
from app.radius.services.setup_wizard_router_lifecycle import RouterLifecycleService
from app.radius.services.setup_wizard_router_provisioning import RouterProvisioningService
from app.radius.services.setup_wizard import get_setup_wizard_service


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-life-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_VPN_POOL", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP", "10.10.0.1")
    reset_for_tests(os.path.join(tmp_path, "test.db"))
    from app import create_app

    return create_app()


def _reservation() -> dict:
    run = get_setup_wizard_service().create_run(tenant_id=1, actor="qa")
    return RouterProvisioningService().reserve_for_run(
        tenant_id=1,
        wizard_run_id=run["id"],
        router_label="Branch",
    )


def test_router_lifecycle_valid_transitions_and_history(app):
    with app.app_context():
        reservation = _reservation()
        lifecycle = RouterLifecycleService()
        lifecycle.transition(tenant_id=1, registry_id=reservation["id"], to_state="script_generated")
        lifecycle.transition(tenant_id=1, registry_id=reservation["id"], to_state="waiting_router_key")
        history = lifecycle.history(tenant_id=1, registry_id=reservation["id"])

    assert [event["to_state"] for event in history] == ["script_generated", "waiting_router_key"]


def test_router_lifecycle_invalid_transition_is_blocked(app):
    with app.app_context():
        reservation = _reservation()
        lifecycle = RouterLifecycleService()
        with pytest.raises(SetupWizardValidationError):
            lifecycle.transition(tenant_id=1, registry_id=reservation["id"], to_state="vpn_verified")


def test_router_lifecycle_retry_from_failed(app):
    with app.app_context():
        reservation = _reservation()
        lifecycle = RouterLifecycleService()
        lifecycle.transition(tenant_id=1, registry_id=reservation["id"], to_state="failed", reason="lab failure")
        retry = lifecycle.retry(tenant_id=1, registry_id=reservation["id"])

    assert retry["lifecycle_state"] == "script_generated"
    assert retry["status"] == "generated"


def test_router_lifecycle_retire_is_terminal(app):
    with app.app_context():
        reservation = _reservation()
        lifecycle = RouterLifecycleService()
        retired = lifecycle.retire(tenant_id=1, registry_id=reservation["id"])
        with pytest.raises(SetupWizardValidationError):
            lifecycle.transition(tenant_id=1, registry_id=reservation["id"], to_state="script_generated")

    assert retired["lifecycle_state"] == "retired"
    assert retired["status"] == "retired"


def test_router_lifecycle_persists_across_service_instances(app):
    with app.app_context():
        reservation = _reservation()
        RouterLifecycleService().transition(
            tenant_id=1,
            registry_id=reservation["id"],
            to_state="script_generated",
            actor="test",
        )
        state = RouterLifecycleService().current_state(
            tenant_id=1,
            registry_id=reservation["id"],
        )

    assert state == "script_generated"
