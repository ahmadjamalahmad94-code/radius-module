from __future__ import annotations

import os
import secrets

import pytest

from app.radius.services.setup_wizard import (
    STEP_ADDED_SERVICES_CHOICE,
    STEP_BROADBAND_CHOICE,
    STEP_HOTSPOT_CHOICE,
    STEP_INTERNET_SCRIPT_PREVIEW,
    STEP_INTERNET_VERIFICATION,
    STEP_VPN_RADIUS_SCRIPT_PREVIEW,
    STEP_VPN_RADIUS_VERIFICATION,
    STEP_STATUS_APPLIED_BY_CUSTOMER,
    STEP_STATUS_GENERATED,
    STEP_STATUS_SKIPPED,
    STEP_STATUS_VERIFIED,
    SetupWizardValidationError,
    get_setup_wizard_service,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-test-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    created = create_app()
    created.config["TEST_API_TOKEN"] = token
    return created


def _svc():
    return get_setup_wizard_service()


def test_create_wizard_run_persists_minimal_state(app):
    with app.app_context():
        run = _svc().create_run(tenant_id=1, actor="qa")
        assert run["id"] > 0
        assert run["status"] == "active"
        assert run["current_step"] == "welcome"
        assert run["verification_status_json"] == {}
        assert run["created_by"] == "qa"


def test_create_step_and_valid_script_transition_path(app):
    with app.app_context():
        svc = _svc()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]

        generated = svc.mark_script_generated(
            tenant_id=1,
            run_id=run_id,
            step_key=STEP_INTERNET_SCRIPT_PREVIEW,
            generated_script="# safe preview only",
            validation_commands=["/tool ping 8.8.8.8 count=3"],
        )
        assert generated["status"] == STEP_STATUS_GENERATED

        applied = svc.mark_applied_by_customer(
            tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_SCRIPT_PREVIEW
        )
        assert applied["status"] == STEP_STATUS_APPLIED_BY_CUSTOMER

        verified = svc.mark_verified(
            tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION
        )
        assert verified["status"] == STEP_STATUS_VERIFIED


def test_invalid_transition_is_blocked(app):
    with app.app_context():
        svc = _svc()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]

        with pytest.raises(SetupWizardValidationError):
            svc.mark_script_generated(
                tenant_id=1,
                run_id=run_id,
                step_key=STEP_INTERNET_VERIFICATION,
                generated_script="not allowed",
            )


def test_cannot_proceed_to_vpn_before_internet_verified(app):
    with app.app_context():
        svc = _svc()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]
        with pytest.raises(SetupWizardValidationError):
            svc.advance_to_step(
                tenant_id=1,
                run_id=run_id,
                step_key=STEP_VPN_RADIUS_SCRIPT_PREVIEW,
            )


def test_cannot_proceed_to_hotspot_or_broadband_before_vpn_verified(app):
    with app.app_context():
        svc = _svc()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]

        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION)

        with pytest.raises(SetupWizardValidationError):
            svc.advance_to_step(tenant_id=1, run_id=run_id, step_key=STEP_HOTSPOT_CHOICE)
        with pytest.raises(SetupWizardValidationError):
            svc.advance_to_step(tenant_id=1, run_id=run_id, step_key=STEP_BROADBAND_CHOICE)


def test_optional_skip_allowed_only_after_vpn_verified(app):
    with app.app_context():
        svc = _svc()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]

        with pytest.raises(SetupWizardValidationError):
            svc.skip_optional_step(
                tenant_id=1,
                run_id=run_id,
                step_key=STEP_HOTSPOT_CHOICE,
                reason="not needed",
            )

        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION)
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION)

        skipped_hotspot = svc.skip_optional_step(
            tenant_id=1, run_id=run_id, step_key=STEP_HOTSPOT_CHOICE, reason="N/A"
        )
        skipped_broadband = svc.skip_optional_step(
            tenant_id=1, run_id=run_id, step_key=STEP_BROADBAND_CHOICE, reason="N/A"
        )
        skipped_added = svc.skip_optional_step(
            tenant_id=1, run_id=run_id, step_key=STEP_ADDED_SERVICES_CHOICE, reason="N/A"
        )
        assert skipped_hotspot["status"] == STEP_STATUS_SKIPPED
        assert skipped_broadband["status"] == STEP_STATUS_SKIPPED
        assert skipped_added["status"] == STEP_STATUS_SKIPPED


def test_set_internet_source_validation_and_run_update(app):
    with app.app_context():
        svc = _svc()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]

        updated = svc.set_internet_source(
            tenant_id=1,
            run_id=run_id,
            source_type="pppoe",
            selected_wan_interface="ether1",
            input_json={"username": "isp-user"},
        )
        assert updated["internet_source_type"] == "pppoe"
        assert updated["selected_wan_interface"] == "ether1"

        with pytest.raises(SetupWizardValidationError):
            svc.set_internet_source(
                tenant_id=1,
                run_id=run_id,
                source_type="lte-unknown",
            )


def test_no_destructive_router_execution_is_introduced(app):
    with app.app_context():
        svc = _svc()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]

        step = svc.mark_script_generated(
            tenant_id=1,
            run_id=run_id,
            step_key=STEP_INTERNET_SCRIPT_PREVIEW,
            generated_script="# planning only\n/tool ping 8.8.8.8 count=3",
        )
        script = step["generated_script"]
        forbidden = ("remove [find]", "/interface disable", "/ip route remove")
        assert not any(token in script for token in forbidden)
