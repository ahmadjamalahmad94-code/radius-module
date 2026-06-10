from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


class MockTransport:
    def __init__(self, response=None):
        self.response = response or {"ok": True, "status": "ok", "items": []}
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "public_ip_change.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app
    reset_for_tests(None)


def _service(response):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    from app.radius.services.license_admin_service_activation import ServiceActivationService

    config = AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        shared_secret="",
        timeout_seconds=1,
        retry_count=0,
    )
    transport = MockTransport(response=response)
    return ServiceActivationService(
        config=config,
        admin_client=AdminPanelClient(config=config, transport=transport),
    )


def _job(reference="pubip-1", payload=None, service_key="network"):
    return {
        "reference": reference,
        "service_key": service_key,
        "action_key": "network.public_ip_change",
        "payload": (
            payload
            if payload is not None
            else {
            "router_id": 7,
            "router_label": "Lab Router",
            "router_type": "mikrotik",
            "requested_public_ip": "8.8.4.4",
            "wan_interface": "ether1",
            }
        ),
    }


def test_missing_required_payload_rejected(app_db):
    service = _service({"ok": True, "status": "ok", "items": [_job(payload={})]})

    execution = service.poll_once(tenant_id=1)["recorded"][0]

    assert execution["status"] == "failed"
    assert execution["result_json"]["error"]["code"] == "invalid_public_ip_change_payload"
    assert "router_id" in execution["result_json"]["error"]["fields"]


def test_invalid_target_rejected(app_db):
    job = _job(payload={"router_id": 7, "requested_public_ip": "8.8.4.4", "router_type": "linux"})
    service = _service({"ok": True, "status": "ok", "items": [job]})

    execution = service.poll_once(tenant_id=1)["recorded"][0]

    assert execution["status"] == "failed"
    assert "router_type" in execution["result_json"]["error"]["fields"]


def test_dry_run_generates_expected_public_ip_plan(app_db):
    service = _service({"ok": True, "status": "ok", "items": [_job()]})

    execution = service.poll_once(tenant_id=1, dry_run=True)["recorded"][0]

    assert execution["status"] == "dry_run_completed"
    plan = execution["result_json"]
    assert plan["operation"]["requested_public_ip"] == "8.8.4.4"
    assert plan["target"]["router_id"] == "7"
    commands = [item["command"] for item in plan["planned_commands"]]
    assert any("/ip firewall nat add" in command for command in commands)
    assert any("HOBERADIUS_ADMIN_BRIDGE:public-ip-change:pubip-1" in command for command in commands)


def test_service_key_alias_public_ip_change_supported(app_db):
    service = _service(
        {
            "ok": True,
            "status": "ok",
            "items": [_job(reference="pubip-alias", service_key="public_ip_change")],
        }
    )

    execution = service.poll_once(tenant_id=1, dry_run=True)["recorded"][0]

    assert execution["status"] == "dry_run_completed"
    assert execution["adapter_key"] == "public_ip_change:network.public_ip_change"


def test_duplicate_reference_idempotent(app_db):
    service = _service({"ok": True, "status": "ok", "items": [_job()]})

    first = service.poll_once(tenant_id=1)["recorded"][0]
    second = service.poll_once(tenant_id=1)["recorded"][0]

    assert first["id"] == second["id"]


def test_live_apply_disabled_by_default(app_db):
    service = _service({"ok": True, "status": "ok", "items": [_job()]})

    execution = service.poll_once(tenant_id=1, dry_run=False)["recorded"][0]

    assert execution["status"] == "failed"
    assert execution["result_json"]["error"]["code"] == "public_ip_change_live_apply_not_enabled"
    assert "بانتظار تفعيلك" in execution["result_json"]["error"]["message"]


def _seed_router(app_db, *, nas_id=7):
    from datetime import datetime

    from app.radius.db.connection import transaction
    now = datetime.utcnow().isoformat() + "Z"
    with transaction() as c:
        c.execute(
            """INSERT INTO nas_devices
                (id, tenant_id, name, address, secret, vendor, nas_type,
                 enabled, created_at, connection_mode, api_user, api_password)
               VALUES (?, 1, 'lab', '203.0.113.7', 's', 'mikrotik', 'hotspot',
                       1, ?, 'direct', 'admin', 'x')""",
            (nas_id, now),
        )


def test_live_apply_runs_real_nat_add_when_enabled(app_db, monkeypatch):
    from app.radius.services import mikrotik_admin_client as mac

    _seed_router(app_db, nas_id=7)
    monkeypatch.setenv("HOBERADIUS_PUBLIC_IP_CHANGE_LIVE_APPLY_ENABLED", "1")
    calls = {}

    def fake_nat_add(nas, **kw):
        calls.update(kw)
        calls["host"] = nas.get("address")
        return mac.MtResult(ok=True)

    monkeypatch.setattr(mac, "firewall_nat_add", fake_nat_add)
    service = _service({"ok": True, "status": "ok", "items": [_job()]})

    execution = service.poll_once(tenant_id=1, dry_run=False)["recorded"][0]

    assert execution["status"] == "completed"
    assert calls["action"] == "src-nat"
    assert calls["to_addresses"] == "8.8.4.4"
    assert calls["out_interface"] == "ether1"
    assert "HOBERADIUS_ADMIN_BRIDGE:public-ip-change:pubip-1" in calls["comment"]


def test_live_apply_router_not_found(app_db, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_PUBLIC_IP_CHANGE_LIVE_APPLY_ENABLED", "1")
    service = _service({"ok": True, "status": "ok", "items": [_job()]})

    execution = service.poll_once(tenant_id=1, dry_run=False)["recorded"][0]

    assert execution["status"] == "failed"
    assert execution["result_json"]["error"]["code"] == "router_not_found"
