from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "setup_wizard_inventory"


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-pilot-" + secrets.token_hex(8)
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
        # «الإعداد الهندسي» super_admin فقط (مخفي مؤقتاً بطلب المالك)
        sess["is_super_admin"] = True


def _post(client, url: str, payload: dict):
    return client.post(url, json=payload, headers={"X-CSRFToken": "test-csrf"})


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _ok_ping_output() -> str:
    return "sent=5 received=5 packet-loss=0%"


def _ready_run(client, *, inventory: str = "wan_dhcp.txt", dry_step: str = "internet") -> int:
    run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
    internet = _post(
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
    assert internet.status_code == 200
    verify = _post(
        client,
        f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet",
        {"mode": "pasted_output", "output": _ok_ping_output()},
    )
    assert verify.status_code == 200
    vpn = _post(
        client,
        f"/admin/radius/setup-wizard/runs/{run_id}/generate-vpn-radius-script",
        {
            "payload": {
                "wg_interface_name": "hr-wg",
                "peer_name": "vps-peer",
                "router_vpn_ip": "10.10.0.3",
                "vps_vpn_ip": "10.10.0.1",
                "allowed_address": "10.10.0.1/32",
                "vps_public_endpoint": "187.77.70.18",
                "radius_server_ip": "10.10.0.1",
                "radius_secret": "XyZ123!",
                "api_username": "hr_api_setup",
            }
        },
    )
    assert vpn.status_code == 200
    inv = _post(
        client,
        f"/admin/radius/setup-wizard/runs/{run_id}/inventory",
        {"output": _fixture(inventory)},
    )
    assert inv.status_code == 200
    dry = _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/dry-run/{dry_step}", {})
    assert dry.status_code == 200
    return run_id


def _insert_script_step_and_operation(run_id: int, step: str, input_json: str) -> None:
    from app.radius.db.connection import transaction

    script_step = f"{step}_script_preview"
    if step == "vpn":
        script_step = "vpn_radius_script_preview"
    with transaction() as c:
        c.execute(
            """
            INSERT INTO setup_wizard_steps (
              wizard_run_id, tenant_id, step_key, status, input_json,
              generated_script, validation_commands_json, verification_result_json,
              created_at, updated_at
            ) VALUES (?, 1, ?, 'generated', ?, ?, '["/tool ping 8.8.8.8 count=5"]', '{}', 'now', 'now')
            """,
            (
                run_id,
                script_step,
                input_json,
                f'/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:{run_id}:{step}"',
            ),
        )
        c.execute(
            """
            INSERT INTO setup_wizard_operations (
              wizard_run_id, tenant_id, step_key, operation_type, operation_order,
              status, command_preview, rollback_command, created_at
            ) VALUES (?, 1, ?, 'add', 1, 'dry_run_ready', ?, ?, 'now')
            """,
            (
                run_id,
                step,
                f'/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:{run_id}:{step}"',
                f'/ip firewall nat remove [find where comment="HOBERADIUS_SETUP:{run_id}:{step}"]',
            ),
        )


def test_eligible_run_returns_pilot_checklist(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        res = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/pilot-drill?step=internet")
        body = res.get_json()["pilot_drill"]
        assert res.status_code == 200
        assert body["eligible"] is True
        assert body["expected_operation_count"] > 0
        assert body["checklist"]
        assert "تم أخذ نسخة احتياطية وتصدير للراوتر" in body["required_manual_confirmations"]


def test_missing_inventory_blocks_pilot_drill(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-internet-script",
            {"source_type": "dhcp", "selected_wan_interface": "ether1", "payload": {"interface": "ether1"}},
        )
        res = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/pilot-drill?step=internet")
        codes = {item["code"] for item in res.get_json()["pilot_drill"]["blocking_reasons"]}
        assert "inventory_missing" in codes


def test_missing_dry_run_blocks_pilot_drill(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        # Broadband has no dry-run in this fixture.
        res = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/pilot-drill?step=broadband")
        codes = {item["code"] for item in res.get_json()["pilot_drill"]["blocking_reasons"]}
        assert "dry_run_missing" in codes


def test_wan_selected_as_hotspot_blocks_drill(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        _insert_script_step_and_operation(
            run_id,
            "hotspot",
            '{"mode":"manual","selected_interfaces":["ether1"],"network_cidr":"10.66.0.0/24"}',
        )
        res = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/pilot-drill?step=hotspot")
        codes = {item["code"] for item in res.get_json()["pilot_drill"]["blocking_reasons"]}
        assert "blocked_interface_selected" in codes


def test_subnet_conflict_blocks_or_warns(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client, inventory="subnet_conflict.txt")
        _insert_script_step_and_operation(
            run_id,
            "hotspot",
            '{"mode":"manual","selected_interfaces":["ether3"],"network_cidr":"10.77.50.0/24"}',
        )
        res = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/pilot-drill?step=hotspot")
        body = res.get_json()["pilot_drill"]
        assert body["eligible"] is False
        assert any(item["code"] == "subnet_overlap" for item in body["blocking_reasons"])


def test_partial_inventory_does_not_crash(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client, inventory="partial_broken.txt")
        res = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/pilot-drill?step=internet")
        assert res.status_code == 200
        assert "pilot_drill" in res.get_json()


def test_duplicate_nat_is_reported_as_risk(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client, inventory="duplicate_nat.txt")
        res = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/pilot-drill?step=internet")
        risks = res.get_json()["pilot_drill"]["risks"]
        assert any(item.get("code") == "existing_nat_detected" for item in risks)


def test_support_bundle_still_masks_secrets(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client, inventory="wireguard_hr_wg.txt")
        bundle = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/support-bundle")
        text = bundle.get_data(as_text=True)
        assert bundle.status_code == 200
        assert "DO_NOT_LEAK" not in text
        assert "XyZ123" not in text


def test_feature_flag_off_still_blocks_apply(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/apply/internet",
            {"confirmation": f"APPLY SETUP WIZARD {run_id} internet"},
        )
        assert res.status_code == 409
        assert res.get_json()["blocked_reason"] == "feature_flag_disabled"


def test_ui_route_renders_pilot_panel(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard")
        html = res.get_data(as_text=True)
        assert res.status_code == 200
        assert "data-sw-action=\"pilot-drill\"" in html
        assert "data-sw-pilot-panel" in html


def test_apply_failure_keeps_rollback_scoped_to_applied_tagged_ops(monkeypatch, tmp_path):
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
        SetupWizardRollbackService,
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
            (500, 1, 'internet', 'add', 1, 'dry_run_ready',
             '/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:500:internet"',
             '/ip firewall nat remove [find where comment="HOBERADIUS_SETUP:500:internet"]', 'now'),
            (500, 1, 'internet', 'add', 2, 'dry_run_ready',
             '/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:500:internet" MOCK_FAIL',
             '/ip firewall nat remove [find where comment="HOBERADIUS_SETUP:500:internet"]', 'now')
            """
        )
    apply_service = SetupWizardApplyService(adapter=MockMikroTikWriteAdapter(), repo=repo)
    result = apply_service.apply(
        tenant_id=1,
        run_id=500,
        step_key="internet",
        confirmation="APPLY SETUP WIZARD 500 internet",
    )
    assert result["status"] == "failed"
    preview = SetupWizardRollbackService(repo=repo).preview(tenant_id=1, run_id=500, step_key="internet")
    assert all("HOBERADIUS_SETUP:500:internet" in op["rollback_command"] for op in preview["operations"])
