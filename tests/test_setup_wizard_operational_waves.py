from __future__ import annotations

import os
import secrets

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-ops-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", raising=False)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
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


def _run_with_internet_script(client) -> int:
    run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
    res = _post(
        client,
        f"/admin/radius/setup-wizard/runs/{run_id}/generate-internet-script",
        {
            "source_type": "dhcp",
            "selected_wan_interface": "ether1",
            "payload": {
                "interface": "ether1",
                "add_default_route": True,
                "use_peer_dns": True,
                "nat_enabled": True,
            },
        },
    )
    assert res.status_code == 200
    return run_id


def test_feature_flag_off_blocks_apply_and_rollback(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _run_with_internet_script(client)
        dry = _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/dry-run/internet", {})
        assert dry.status_code == 200
        assert dry.get_json()["status"] == "dry_run_ready"

        apply = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/apply/internet",
            {"confirmation": f"APPLY SETUP WIZARD {run_id} internet"},
        )
        assert apply.status_code == 409
        assert apply.get_json()["blocked_reason"] == "feature_flag_disabled"

        rollback = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/rollback/internet",
            {"confirmation": f"ROLLBACK SETUP WIZARD {run_id} internet"},
        )
        assert rollback.status_code == 409
        assert rollback.get_json()["blocked_reason"] == "feature_flag_disabled"


def test_dry_run_persists_operations(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _run_with_internet_script(client)
        dry = _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/dry-run/internet", {})
        body = dry.get_json()
        assert body["operations"]
        assert body["confirmation_phrase"] == f"APPLY SETUP WIZARD {run_id} internet"

        ops = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/operations").get_json()
        assert any(op["status"] == "dry_run_ready" for op in ops["operations"])


def test_dangerous_command_rejected():
    from app.radius.services.setup_wizard_operations import OperationSafetyValidator
    from app.radius.services.setup_wizard_common import SetupWizardValidationError

    validator = OperationSafetyValidator()
    with pytest.raises(SetupWizardValidationError):
        validator.validate_preview_command(
            command="/ip route remove [find]",
            run_id=1,
            step_key="internet",
        )
    with pytest.raises(SetupWizardValidationError):
        validator.validate_preview_command(
            command="/ip firewall nat set [find] action=masquerade",
            run_id=1,
            step_key="internet",
        )
    with pytest.raises(SetupWizardValidationError):
        validator.validate_preview_command(
            command='/ip firewall nat add chain=srcnat action=masquerade comment="OTHER_TAG"',
            run_id=1,
            step_key="internet",
        )


def test_mock_apply_stops_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    from app.radius.db.connection import transaction
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.services.setup_wizard_operations import (
        MockMikroTikWriteAdapter,
        SetupWizardApplyService,
        SetupWizardOperationRepo,
    )

    create_app()
    run_pending_migrations()
    repo = SetupWizardOperationRepo()
    with transaction() as c:
        c.execute(
            """
            INSERT INTO setup_wizard_operations (
              wizard_run_id, tenant_id, step_key, operation_type, operation_order,
              status, command_preview, rollback_command, created_at
            ) VALUES
            (999, 1, 'internet', 'add', 1, 'dry_run_ready',
             '/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:999:internet"', '', 'now'),
            (999, 1, 'internet', 'add', 2, 'dry_run_ready',
             '/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:999:internet" MOCK_FAIL', '', 'now')
            """
        )
    service = SetupWizardApplyService(adapter=MockMikroTikWriteAdapter(), repo=repo)
    result = service.apply(
        tenant_id=1,
        run_id=999,
        step_key="internet",
        confirmation="APPLY SETUP WIZARD 999 internet",
    )
    assert result["status"] == "failed"
    ops = repo.list_for_run(tenant_id=1, run_id=999, step_key="internet")
    assert ops[0]["status"] == "applied"
    assert ops[1]["status"] == "failed"


def test_rollback_validator_requires_tag():
    from app.radius.services.setup_wizard_operations import OperationSafetyValidator
    from app.radius.services.setup_wizard_common import SetupWizardValidationError

    validator = OperationSafetyValidator()
    with pytest.raises(SetupWizardValidationError):
        validator.validate_rollback_command(
            command="/ip firewall nat remove [find]",
            run_id=9,
            step_key="hotspot",
        )
    validator.validate_rollback_command(
        command='/ip firewall nat remove [find where comment="HOBERADIUS_SETUP:9:hotspot"]',
        run_id=9,
        step_key="hotspot",
    )


def test_inventory_parser_sanitizes_and_detects_risks(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
        output = """
        /interface print detail
        0 name=ether1 type=ether
        1 name=hr-wg type=wireguard private-key="SECRET"
        /ip address print detail
        0 address=10.20.30.1/24 interface=ether2
        /ip route print detail
        0 dst-address=0.0.0.0/0 gateway=ether1
        /ip pool print detail
        0 name=old-pool ranges=10.88.44.10-10.88.44.200
        /radius print detail
        0 address=10.10.0.1 secret="RADIUS_SECRET"
        """
        res = _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/inventory", {"output": output})
        assert res.status_code == 200
        snapshot = res.get_json()["snapshot"]
        dumped = str(snapshot)
        assert "SECRET" not in dumped
        assert "RADIUS_SECRET" not in dumped
        assert snapshot["risk_report"]["wan_interface"] == "ether1"
        assert "hr-wg" in snapshot["risk_report"]["excluded_interfaces"]
        assert "10.20.30.0/24" in snapshot["risk_report"]["existing_subnets"]


def test_partial_inventory_and_overlap_risk_do_not_crash():
    from app.radius.services.setup_wizard_inventory import RouterInventoryParser, RouterRiskAnalyzer

    parsed = RouterInventoryParser().parse("/ip address print detail\n0 address=10.77.50.1/24 interface=bridge1")
    risk = RouterRiskAnalyzer().analyze(
        snapshot=parsed,
        candidate_cidrs=["10.77.50.0/24", "10.88.90.0/24"],
    )
    assert risk["subnet_overlaps"] == [{"candidate": "10.77.50.0/24", "existing": "10.77.50.0/24"}]
    assert any(w["code"] == "subnet_overlap" for w in risk["warnings"])


def test_orchestrator_blocks_before_vpn_verified(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/orchestrate/hotspot",
            {"mode": "smart", "payload": {"selected_interfaces": ["ether3"]}, "manual_override": True},
        )
        assert res.status_code == 400
        assert "internet verification is required first" in res.get_json()["error"]


def test_added_services_catalog_and_unsupported_response(app):
    with app.test_client() as client:
        _auth_session(client)
        cat = client.get("/admin/radius/setup-wizard/added-services/catalog")
        assert cat.status_code == 200
        keys = {svc["key"] for svc in cat.get_json()["services"]}
        assert {"walled_garden", "web_block", "site_exit", "anti_sharing"} <= keys

        run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
        # Unlock required gates through manual contract to test added services routing only.
        from app.radius.services.setup_wizard import get_setup_wizard_service

        svc = get_setup_wizard_service()
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key="internet_verification", verification_result={"ok": True})
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key="vpn_radius_verification", verification_result={"ok": True})
        plan = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/added-services/plan",
            {"service_key": "anti_sharing", "inputs": {}},
        )
        assert plan.status_code == 200
        assert plan.get_json()["plan"]["plan_status"] == "not_supported_yet"


def test_support_bundle_masks_secrets_and_health_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/inventory",
            {"output": '/radius print detail secret="TOPSECRET"\n/interface print detail name=ether1'},
        )
        bundle = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/support-bundle")
        assert bundle.status_code == 200
        assert "TOPSECRET" not in bundle.get_data(as_text=True)
        health = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/health")
        assert health.status_code == 200
        assert health.get_json()["health"]["run_id"] == run_id


def test_setup_wizard_page_renders_operational_controls(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard")
        html = res.get_data(as_text=True)
        assert res.status_code == 200
        assert "data-sw-action=\"dry-run\"" in html
        assert "data-sw-action=\"apply-step\"" in html
        assert "data-sw-action=\"save-inventory\"" in html
        assert "HOBERADIUS_SETUP_WIZARD_LIVE_APPLY" in html
