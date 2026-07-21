from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-lab-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", raising=False)
    monkeypatch.delenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", raising=False)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    # حارس دورة حياة الترخيص يقفل اللوحة على قاعدة جديدة بلا لقطة
    # ترخيص؛ تجاوزه في الاختبارات يحتاج العلمين معًا (راجع
    # license_lifecycle._test_bypass_active وتعليق tests/conftest.py).
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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


def _ok_ping() -> str:
    return "sent=5 received=5 packet-loss=0%"


def _inventory() -> str:
    return """
    /interface print detail
    0 name=ether1 type=ether running=yes
    1 name=ether2 type=ether running=yes
    /ip address print detail
    0 address=192.168.88.2/24 interface=ether1
    /ip route print detail
    0 dst-address=0.0.0.0/0 gateway=ether1
    """


def _ready_run(client) -> int:
    run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
    assert _post(
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
    ).status_code == 200
    assert _post(
        client,
        f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet",
        {"mode": "pasted_output", "output": _ok_ping()},
    ).status_code == 200
    assert _post(
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
                "radius_secret": "SECRET",
                "api_username": "hr_api_setup",
            }
        },
    ).status_code == 200
    assert _post(
        client,
        f"/admin/radius/setup-wizard/runs/{run_id}/inventory",
        {"output": _inventory()},
    ).status_code == 200
    assert _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/dry-run/internet", {}).status_code == 200
    return run_id


def _stale_snapshot(run_id: int) -> None:
    from app.radius.db.connection import transaction

    old = (datetime.utcnow() - timedelta(hours=3)).isoformat() + "Z"
    with transaction() as c:
        c.execute(
            "UPDATE setup_wizard_router_snapshots SET created_at=? WHERE wizard_run_id=?",
            (old, int(run_id)),
        )


def test_both_feature_flags_required(app, monkeypatch):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/apply/internet",
            {"confirmation": f"APPLY SETUP WIZARD {run_id} internet"},
        )
        assert res.status_code == 409
        reasons = {item["code"] for item in res.get_json()["policy"]["blocking_reasons"]}
        assert {"feature_flag_disabled", "lab_mode_disabled"} <= reasons

        monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", "true")
        res2 = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/apply/internet",
            {"confirmation": f"APPLY SETUP WIZARD {run_id} internet"},
        )
        reasons2 = {item["code"] for item in res2.get_json()["policy"]["blocking_reasons"]}
        assert "lab_mode_disabled" in reasons2


def test_one_step_apply_only(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/apply/internet,vpn",
            {"confirmation": f"APPLY SETUP WIZARD {run_id} internet,vpn"},
        )
        assert res.status_code == 409


def test_stale_snapshot_blocked(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        _stale_snapshot(run_id)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/apply/internet",
            {"confirmation": f"APPLY SETUP WIZARD {run_id} internet"},
        )
        reasons = {item["code"] for item in res.get_json()["policy"]["blocking_reasons"]}
        assert "stale_snapshot" in reasons


def test_missing_rollback_preview_blocks_lab_apply(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        from app.radius.db.connection import transaction

        with transaction() as c:
            c.execute(
                "UPDATE setup_wizard_operations SET rollback_command='' WHERE wizard_run_id=?",
                (run_id,),
            )
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/apply/internet",
            {"confirmation": f"APPLY SETUP WIZARD {run_id} internet"},
        )
        reasons = {item["code"] for item in res.get_json()["policy"]["blocking_reasons"]}
        assert "rollback_missing" in reasons


def test_failure_stops_execution_and_preserves_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    # حارس دورة حياة الترخيص يقفل اللوحة على قاعدة جديدة بلا لقطة
    # ترخيص؛ تجاوزه في الاختبارات يحتاج العلمين معًا (راجع
    # license_lifecycle._test_bypass_active وتعليق tests/conftest.py).
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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
            (700, 1, 'internet', 'add', 1, 'dry_run_ready',
             '/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:700:internet"',
             '/ip firewall nat remove [find where comment="HOBERADIUS_SETUP:700:internet"]', 'now'),
            (700, 1, 'internet', 'add', 2, 'dry_run_ready',
             '/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:700:internet" MOCK_FAIL',
             '/ip firewall nat remove [find where comment="HOBERADIUS_SETUP:700:internet"]', 'now')
            """
        )
    result = SetupWizardApplyService(adapter=MockMikroTikWriteAdapter(), repo=repo).apply(
        tenant_id=1,
        run_id=700,
        step_key="internet",
        confirmation="APPLY SETUP WIZARD 700 internet",
    )
    assert result["status"] == "failed"
    ops = repo.list_for_run(tenant_id=1, run_id=700, step_key="internet")
    assert ops[0]["status"] == "applied"
    assert ops[1]["status"] == "failed"
    assert "mock adapter failure" in str(ops[1]["error_json"])


def test_rollback_drill_only_applied_tagged_objects(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    # حارس دورة حياة الترخيص يقفل اللوحة على قاعدة جديدة بلا لقطة
    # ترخيص؛ تجاوزه في الاختبارات يحتاج العلمين معًا (راجع
    # license_lifecycle._test_bypass_active وتعليق tests/conftest.py).
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    from app import create_app
    from app.radius.db.connection import transaction
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.services.setup_wizard_operations import (
        MockMikroTikWriteAdapter,
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
            (701, 1, 'internet', 'add', 1, 'applied',
             '/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:701:internet"',
             '/ip firewall nat remove [find where comment="HOBERADIUS_SETUP:701:internet"]', 'now'),
            (701, 1, 'internet', 'add', 2, 'dry_run_ready',
             '/ip firewall nat add chain=srcnat comment="HOBERADIUS_SETUP:701:internet"',
             '/ip firewall nat remove [find where comment="HOBERADIUS_SETUP:701:internet"]', 'now')
            """
        )
    result = SetupWizardRollbackService(adapter=MockMikroTikWriteAdapter(), repo=repo).rollback(
        tenant_id=1,
        run_id=701,
        step_key="internet",
        confirmation="ROLLBACK SETUP WIZARD 701 internet",
    )
    assert result["status"] == "rolled_back"
    assert len(result["rolled_back"]) == 1
    ops = repo.list_for_run(tenant_id=1, run_id=701, step_key="internet")
    assert [op["status"] for op in ops] == ["rolled_back", "dry_run_ready"]


def test_verification_required_after_successful_apply(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        from app.radius.services.setup_wizard import get_setup_wizard_service
        from app.radius.services.setup_wizard_operations import MockMikroTikWriteAdapter, SetupWizardApplyService

        svc = get_setup_wizard_service()
        svc._apply_service = SetupWizardApplyService(  # test-only injection
            adapter=MockMikroTikWriteAdapter(),
            repo=svc._operation_repo,
        )
        result = svc.apply_step(
            tenant_id=1,
            run_id=run_id,
            step_key="internet",
            confirmation=f"APPLY SETUP WIZARD {run_id} internet",
        )
        assert result["status"] == "applied"
        assert result["verification_required"] is True


def test_ui_timeline_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard")
        html = res.get_data(as_text=True)
        assert res.status_code == 200
        assert "خط سير تنفيذ المختبر" in html
        assert "data-sw-action=\"lab-timeline\"" in html


def test_vpn_handshake_missing_diagnostic_still_preserved(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _ready_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-vpn-radius",
            {"mode": "pasted_output", "output": "no handshake\nradius unreachable\napi failed"},
        )
        text = res.get_data(as_text=True)
        assert res.status_code == 200
        assert "vpn_not_handshaking" in text or "failed" in text
